#!/usr/bin/env python3
"""
IntNetAdmin - Internal Network Administration Dashboard
Manage your internal network with ease

Copyright (c) 2026 Daniel Nylander
Licensed under GPL-3.0
"""

import os
import re
import json
import subprocess
import threading
import time
import ipaddress
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_babel import Babel, gettext as _

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'intnetadmin-default-secret-change-in-production')
app.config['BABEL_DEFAULT_LOCALE'] = 'en'
app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'sv']

babel = Babel(app)

# ============================================================================
# Configuration - Override via environment variables
# ============================================================================

DEFAULT_CONFIG = {
    'dhcp_conf': os.environ.get('DHCP_CONF', '/etc/dhcp/dhcpd.conf'),
    'bind_dir': os.environ.get('BIND_DIR', '/etc/bind'),
    'network': os.environ.get('NETWORK_CIDR', '10.0.0.0/24'),
    'scan_interval': int(os.environ.get('SCAN_INTERVAL', 7200)),  # 2 hours in seconds
    'ping_timeout': int(os.environ.get('PING_TIMEOUT', 1)),
    'ping_count': int(os.environ.get('PING_COUNT', 1)),
    'max_threads': int(os.environ.get('MAX_THREADS', 100)),
}

# Runtime config (can be changed via settings)
CONFIG = dict(DEFAULT_CONFIG)

# ============================================================================
# Global State
# ============================================================================

ping_results = {}
scan_in_progress = False
last_scan_time = None
scan_history = []  # List of {timestamp, online_count, offline_count}
scanner_thread = None
scanner_running = True

# Pending changes (staged before writing to disk)
pending_changes = {
    'dhcp': [],  # List of {action: 'add'|'edit'|'delete', hostname, data, timestamp}
    'dns': [],   # List of {action: 'add'|'edit'|'delete', zone, record, timestamp}
}
pending_changes_lock = threading.Lock()

# ============================================================================
# Internationalization
# ============================================================================

def get_locale():
    """Get user's preferred locale"""
    if 'lang' in session:
        return session['lang']
    return request.accept_languages.best_match(['en', 'sv'])

babel.init_app(app, locale_selector=get_locale)

# ============================================================================
# PAM Authentication
# ============================================================================

def pam_authenticate(username, password):
    """Authenticate user via PAM"""
    try:
        import pam
        p = pam.pam()
        return p.authenticate(username, password)
    except ImportError:
        # Fallback: use /etc/shadow via subprocess
        try:
            result = subprocess.run(
                ['su', '-c', 'echo ok', username],
                input=password.encode() + b'\n',
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            return False

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            if request.is_json:
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def sudo_required(f):
    """Decorator to require sudo password in session"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'sudo_password' not in session:
            return jsonify({'error': 'Sudo password required', 'need_sudo': True}), 403
        return f(*args, **kwargs)
    return decorated_function

def run_with_sudo(command, password=None):
    """Run command with sudo using session password"""
    if password is None:
        password = session.get('sudo_password', '')
    
    try:
        proc = subprocess.Popen(
            ['sudo', '-S'] + command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(input=(password + '\n').encode(), timeout=30)
        return proc.returncode == 0, stdout.decode(), stderr.decode()
    except Exception as e:
        return False, '', str(e)

# ============================================================================
# DHCP Parser & Writer
# ============================================================================

def parse_dhcp_config():
    """Parse DHCP configuration file"""
    hosts = []
    global_options = {}
    subnets = []
    
    try:
        with open(CONFIG['dhcp_conf'], 'r') as f:
            content = f.read()
        
        # Parse global options
        for match in re.finditer(r'^option\s+([\w-]+)\s+(.+?);', content, re.MULTILINE):
            global_options[match.group(1)] = match.group(2)
        
        # Parse host declarations
        host_pattern = r'host\s+(\S+)\s*\{([^}]+)\}'
        for match in re.finditer(host_pattern, content):
            hostname = match.group(1)
            block = match.group(2)
            
            mac_match = re.search(r'hardware\s+ethernet\s+([\w:]+)', block)
            ip_match = re.search(r'fixed-address\s+(\S+)', block)
            
            if mac_match:
                host = {
                    'name': hostname,
                    'mac': mac_match.group(1).lower(),
                    'address': ip_match.group(1).rstrip(';') if ip_match else None,
                    'type': 'static'
                }
                hosts.append(host)
        
        # Parse subnet declarations
        subnet_pattern = r'subnet\s+([\d.]+)\s+netmask\s+([\d.]+)\s*\{([^}]+)\}'
        for match in re.finditer(subnet_pattern, content):
            subnet = {
                'network': match.group(1),
                'netmask': match.group(2),
                'options': {}
            }
            block = match.group(3)
            
            range_match = re.search(r'range\s+([\d.]+)\s+([\d.]+)', block)
            if range_match:
                subnet['range_start'] = range_match.group(1)
                subnet['range_end'] = range_match.group(2)
            
            # Parse subnet-specific options
            for opt_match in re.finditer(r'option\s+([\w-]+)\s+(.+?);', block):
                subnet['options'][opt_match.group(1)] = opt_match.group(2)
            
            subnets.append(subnet)
        
        return {
            'hosts': sorted(hosts, key=lambda x: x['name'].lower()),
            'options': global_options,
            'subnets': subnets,
            'raw': content
        }
    except Exception as e:
        return {'error': str(e), 'hosts': [], 'options': {}, 'subnets': []}

def add_dhcp_host(hostname, mac, ip, stage_only=True):
    """Add a new static DHCP host - stages change by default"""
    with pending_changes_lock:
        pending_changes['dhcp'].append({
            'action': 'add',
            'hostname': hostname,
            'data': {'mac': mac, 'ip': ip},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def edit_dhcp_host(hostname, mac=None, ip=None):
    """Edit an existing DHCP host - stages change"""
    with pending_changes_lock:
        # Remove any existing pending changes for this host
        pending_changes['dhcp'] = [c for c in pending_changes['dhcp'] if c.get('hostname') != hostname]
        pending_changes['dhcp'].append({
            'action': 'edit',
            'hostname': hostname,
            'data': {'mac': mac, 'ip': ip},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def delete_dhcp_host(hostname):
    """Delete a DHCP host - stages change"""
    with pending_changes_lock:
        # Remove any existing pending changes for this host
        pending_changes['dhcp'] = [c for c in pending_changes['dhcp'] if c.get('hostname') != hostname]
        pending_changes['dhcp'].append({
            'action': 'delete',
            'hostname': hostname,
            'data': {},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def apply_dhcp_changes():
    """Apply all pending DHCP changes to disk"""
    try:
        with open(CONFIG['dhcp_conf'], 'r') as f:
            content = f.read()
        
        original_content = content
        
        with pending_changes_lock:
            changes = pending_changes['dhcp'][:]
        
        for change in changes:
            hostname = change['hostname']
            action = change['action']
            data = change.get('data', {})
            
            if action == 'delete':
                # Remove host block
                pattern = rf'host\s+{re.escape(hostname)}\s*\{{[^}}]+\}}\s*'
                content = re.sub(pattern, '', content)
            
            elif action == 'edit':
                # Find and replace host block
                pattern = rf'(host\s+{re.escape(hostname)}\s*\{{)([^}}]+)(\}})'
                match = re.search(pattern, content)
                if match:
                    new_block = match.group(1)
                    if data.get('mac'):
                        new_block += f"\n    hardware ethernet {data['mac']};"
                    if data.get('ip'):
                        new_block += f"\n    fixed-address {data['ip']};"
                    new_block += "\n" + match.group(3)
                    content = re.sub(pattern, new_block, content)
            
            elif action == 'add':
                entry = f'''
host {hostname} {{
    hardware ethernet {data['mac']};
    fixed-address {data['ip']};
}}
'''
                content = content.rstrip() + '\n' + entry
        
        # Write via sudo
        temp_file = f'/tmp/dhcpd.conf.{os.getpid()}'
        with open(temp_file, 'w') as f:
            f.write(content)
        
        success, stdout, stderr = run_with_sudo(['cp', temp_file, CONFIG['dhcp_conf']])
        os.remove(temp_file)
        
        if success:
            # Reload DHCP service
            run_with_sudo(['systemctl', 'reload', 'isc-dhcp-server'])
            # Clear pending changes
            with pending_changes_lock:
                pending_changes['dhcp'] = []
        
        return success, stderr if not success else 'OK', original_content, content
    except Exception as e:
        return False, str(e), '', ''

# ============================================================================
# BIND Parser & Writer
# ============================================================================

def parse_bind_zones():
    """Parse BIND zone files"""
    zones = []
    
    try:
        bind_dir = CONFIG['bind_dir']
        
        for filename in os.listdir(bind_dir):
            if filename.startswith('db.') and not filename.endswith('.jnl'):
                filepath = os.path.join(bind_dir, filename)
                
                # Skip standard zone files
                if filename in ['db.0', 'db.127', 'db.255', 'db.empty', 'db.local', 'db.root']:
                    continue
                
                try:
                    with open(filepath, 'r') as f:
                        content = f.read()
                    
                    zone_name = filename[3:]  # Remove 'db.' prefix
                    records = parse_zone_records(content)
                    
                    # Determine if reverse zone
                    is_reverse = 'in-addr.arpa' in zone_name or zone_name.replace('.', '').isdigit()
                    
                    zones.append({
                        'name': zone_name,
                        'file': filepath,
                        'records': records,
                        'is_reverse': is_reverse,
                        'record_count': len(records)
                    })
                except PermissionError:
                    zones.append({
                        'name': filename[3:],
                        'file': filepath,
                        'error': 'Permission denied',
                        'records': []
                    })
        
        return sorted(zones, key=lambda x: x['name'])
    except Exception as e:
        return [{'error': str(e)}]

def parse_zone_records(content):
    """Parse DNS zone file records"""
    records = []
    
    # A records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?A\s+([\d.]+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'A',
            'value': match.group(2)
        })
    
    # AAAA records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?AAAA\s+(\S+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'AAAA',
            'value': match.group(2)
        })
    
    # CNAME records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?CNAME\s+(\S+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'CNAME',
            'value': match.group(2)
        })
    
    # PTR records (reverse DNS)
    for match in re.finditer(r'^(\d+)\s+(?:\d+\s+)?(?:IN\s+)?PTR\s+(\S+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'PTR',
            'value': match.group(2)
        })
    
    # MX records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?MX\s+(\d+)\s+(\S+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'MX',
            'priority': match.group(2),
            'value': match.group(3)
        })
    
    # TXT records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?TXT\s+"([^"]+)"', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'TXT',
            'value': match.group(2)
        })
    
    # NS records
    for match in re.finditer(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?NS\s+(\S+)', content, re.MULTILINE):
        records.append({
            'name': match.group(1),
            'type': 'NS',
            'value': match.group(2)
        })
    
    return records

def create_zone_file(zone_name, soa_ns, soa_email, ttl=86400):
    """Create a new DNS zone file"""
    serial = datetime.now().strftime('%Y%m%d01')
    content = f'''$TTL {ttl}
@   IN  SOA {soa_ns}. {soa_email.replace('@', '.')}. (
            {serial}    ; Serial
            3600        ; Refresh
            1800        ; Retry
            604800      ; Expire
            86400       ; Minimum TTL
        )
    IN  NS  {soa_ns}.
'''
    
    filepath = os.path.join(CONFIG['bind_dir'], f'db.{zone_name}')
    
    try:
        temp_file = f'/tmp/db.{zone_name}.{os.getpid()}'
        with open(temp_file, 'w') as f:
            f.write(content)
        
        success, stdout, stderr = run_with_sudo(['cp', temp_file, filepath])
        os.remove(temp_file)
        
        if success:
            # Set proper ownership
            run_with_sudo(['chown', 'bind:bind', filepath])
        
        return success, stderr if not success else 'OK'
    except Exception as e:
        return False, str(e)

def edit_dns_record(zone_name, record_name, record_type, new_value):
    """Edit a DNS record - stages change"""
    with pending_changes_lock:
        pending_changes['dns'].append({
            'action': 'edit',
            'zone': zone_name,
            'record': {'name': record_name, 'type': record_type, 'value': new_value},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def delete_dns_record(zone_name, record_name, record_type):
    """Delete a DNS record - stages change"""
    with pending_changes_lock:
        pending_changes['dns'].append({
            'action': 'delete',
            'zone': zone_name,
            'record': {'name': record_name, 'type': record_type},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def add_dns_record(zone_name, record_name, record_type, value):
    """Add a DNS record - stages change"""
    with pending_changes_lock:
        pending_changes['dns'].append({
            'action': 'add',
            'zone': zone_name,
            'record': {'name': record_name, 'type': record_type, 'value': value},
            'timestamp': datetime.now().isoformat()
        })
    return True, 'Staged for activation'

def apply_dns_changes():
    """Apply all pending DNS changes to zone files"""
    results = []
    
    with pending_changes_lock:
        changes = pending_changes['dns'][:]
    
    # Group changes by zone
    zones_to_update = defaultdict(list)
    for change in changes:
        zones_to_update[change['zone']].append(change)
    
    for zone_name, zone_changes in zones_to_update.items():
        filepath = os.path.join(CONFIG['bind_dir'], f'db.{zone_name}')
        
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            
            original_content = content
            
            for change in zone_changes:
                action = change['action']
                record = change['record']
                
                if action == 'delete':
                    # Remove the record line
                    pattern = rf'^{re.escape(record["name"])}\s+.*{record["type"]}\s+.*$'
                    content = re.sub(pattern, '', content, flags=re.MULTILINE)
                
                elif action == 'edit':
                    # Replace the record
                    pattern = rf'^({re.escape(record["name"])}\s+(?:\d+\s+)?(?:IN\s+)?{record["type"]}\s+).*$'
                    replacement = rf'\g<1>{record["value"]}'
                    content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
                
                elif action == 'add':
                    # Add new record at end
                    new_record = f'{record["name"]}\tIN\t{record["type"]}\t{record["value"]}\n'
                    content = content.rstrip() + '\n' + new_record
            
            # Update serial number
            serial = datetime.now().strftime('%Y%m%d%H')
            content = re.sub(r'(\d{10})\s*;\s*Serial', f'{serial}  ; Serial', content)
            
            # Write via sudo
            temp_file = f'/tmp/db.{zone_name}.{os.getpid()}'
            with open(temp_file, 'w') as f:
                f.write(content)
            
            success, stdout, stderr = run_with_sudo(['cp', temp_file, filepath])
            os.remove(temp_file)
            
            if success:
                run_with_sudo(['chown', 'bind:bind', filepath])
                results.append({'zone': zone_name, 'status': 'ok'})
            else:
                results.append({'zone': zone_name, 'status': 'failed', 'error': stderr})
        
        except Exception as e:
            results.append({'zone': zone_name, 'status': 'failed', 'error': str(e)})
    
    # Reload BIND if any changes succeeded
    if any(r['status'] == 'ok' for r in results):
        run_with_sudo(['systemctl', 'reload', 'bind9'])
        with pending_changes_lock:
            pending_changes['dns'] = []
    
    return results

def get_lease_history():
    """Get lease request history from dhcpd.leases for network status charts"""
    lease_file = '/var/lib/dhcp/dhcpd.leases'
    history = defaultdict(lambda: {'requests': 0, 'unique_hosts': set()})
    
    try:
        with open(lease_file, 'r') as f:
            content = f.read()
        
        # Parse lease timestamps
        for match in re.finditer(r'starts\s+\d+\s+(\d{4}/\d{2}/\d{2}\s+\d{2}):\d{2}:\d{2}', content):
            hour_key = match.group(1).replace('/', '-').replace(' ', 'T') + ':00:00'
            history[hour_key]['requests'] += 1
        
        # Parse MAC addresses per hour
        lease_pattern = r'lease\s+[\d.]+\s*\{([^}]+)\}'
        for match in re.finditer(lease_pattern, content):
            block = match.group(1)
            mac_match = re.search(r'hardware\s+ethernet\s+([\w:]+)', block)
            start_match = re.search(r'starts\s+\d+\s+(\d{4}/\d{2}/\d{2}\s+\d{2}):\d{2}:\d{2}', block)
            if mac_match and start_match:
                hour_key = start_match.group(1).replace('/', '-').replace(' ', 'T') + ':00:00'
                history[hour_key]['unique_hosts'].add(mac_match.group(1))
        
        # Convert to list format for charts
        result = []
        for timestamp, data in sorted(history.items())[-48:]:  # Last 48 hours
            result.append({
                'timestamp': timestamp,
                'requests': data['requests'],
                'unique_hosts': len(data['unique_hosts'])
            })
        
        return result
    except Exception as e:
        return []

# ============================================================================
# IP Scanner
# ============================================================================

def ping_host(ip):
    """Ping a single host"""
    try:
        result = subprocess.run(
            ['ping', '-c', str(CONFIG['ping_count']), '-W', str(CONFIG['ping_timeout']), str(ip)],
            capture_output=True,
            timeout=CONFIG['ping_timeout'] + 2
        )
        return result.returncode == 0
    except:
        return False

def scan_network():
    """Scan the network for active hosts"""
    global ping_results, scan_in_progress, last_scan_time, scan_history
    
    if scan_in_progress:
        return
    
    scan_in_progress = True
    network = ipaddress.ip_network(CONFIG['network'], strict=False)
    
    results = {}
    results_lock = threading.Lock()
    
    def ping_worker(ip):
        online = ping_host(ip)
        with results_lock:
            results[str(ip)] = {
                'online': online,
                'checked': datetime.now().isoformat()
            }
    
    threads = []
    for ip in network.hosts():
        t = threading.Thread(target=ping_worker, args=(ip,))
        threads.append(t)
        t.start()
        
        # Limit concurrent threads
        if len(threads) >= CONFIG['max_threads']:
            for t in threads:
                t.join()
            threads = []
    
    # Wait for remaining threads
    for t in threads:
        t.join()
    
    ping_results = results
    last_scan_time = datetime.now()
    
    # Record history for charts
    online_count = sum(1 for r in results.values() if r.get('online'))
    offline_count = len(results) - online_count
    
    scan_history.append({
        'timestamp': last_scan_time.isoformat(),
        'online': online_count,
        'offline': offline_count
    })
    
    # Keep only last 168 entries (1 week at 1 scan per hour)
    if len(scan_history) > 168:
        scan_history = scan_history[-168:]
    
    scan_in_progress = False

def background_scanner():
    """Background thread that scans periodically"""
    global scanner_running
    
    # Initial scan on startup
    scan_network()
    
    while scanner_running:
        # Sleep in small increments to allow quick shutdown
        sleep_time = CONFIG['scan_interval']
        for _ in range(sleep_time):
            if not scanner_running:
                return
            time.sleep(1)
        
        if scanner_running:
            scan_network()

def start_background_scanner():
    """Start the background scanner thread"""
    global scanner_thread
    if scanner_thread is None or not scanner_thread.is_alive():
        scanner_thread = threading.Thread(target=background_scanner, daemon=True)
        scanner_thread.start()

# ============================================================================
# DHCP Leases
# ============================================================================

def get_dhcp_leases():
    """Read DHCP leases file"""
    leases = []
    lease_file = '/var/lib/dhcp/dhcpd.leases'
    
    try:
        with open(lease_file, 'r') as f:
            content = f.read()
        
        lease_pattern = r'lease\s+([\d.]+)\s*\{([^}]+)\}'
        for match in re.finditer(lease_pattern, content):
            ip = match.group(1)
            block = match.group(2)
            
            lease = {'ip': ip, 'type': 'dynamic'}
            
            mac_match = re.search(r'hardware\s+ethernet\s+([\w:]+)', block)
            if mac_match:
                lease['mac'] = mac_match.group(1)
            
            hostname_match = re.search(r'client-hostname\s+"([^"]+)"', block)
            if hostname_match:
                lease['hostname'] = hostname_match.group(1)
            
            start_match = re.search(r'starts\s+\d+\s+([\d/:\s]+)', block)
            if start_match:
                lease['starts'] = start_match.group(1).strip()
            
            end_match = re.search(r'ends\s+\d+\s+([\d/:\s]+)', block)
            if end_match:
                lease['ends'] = end_match.group(1).strip()
            
            binding_match = re.search(r'binding\s+state\s+(\w+)', block)
            if binding_match:
                lease['state'] = binding_match.group(1)
            
            leases.append(lease)
        
        return leases
    except Exception as e:
        return []

# ============================================================================
# Network Management
# ============================================================================

def get_networks():
    """Get configured network ranges"""
    networks = []
    dhcp_data = parse_dhcp_config()
    
    for subnet in dhcp_data.get('subnets', []):
        networks.append({
            'network': subnet['network'],
            'netmask': subnet['netmask'],
            'range_start': subnet.get('range_start'),
            'range_end': subnet.get('range_end'),
            'options': subnet.get('options', {})
        })
    
    return networks

# ============================================================================
# Routes - Pages
# ============================================================================

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        
        if pam_authenticate(username, password):
            session['user'] = username
            session['login_time'] = datetime.now().isoformat()
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error=_('Invalid credentials'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ============================================================================
# Routes - API
# ============================================================================

@app.route('/api/user')
@login_required
def api_user():
    return jsonify({
        'username': session.get('user'),
        'login_time': session.get('login_time'),
        'has_sudo': 'sudo_password' in session
    })

@app.route('/api/sudo', methods=['POST'])
@login_required
def api_sudo():
    """Set sudo password for session (never saved to disk)"""
    password = request.json.get('password', '')
    
    # Verify the password works
    success, _, _ = run_with_sudo(['echo', 'ok'], password)
    
    if success:
        session['sudo_password'] = password
        return jsonify({'status': 'ok'})
    else:
        return jsonify({'error': 'Invalid sudo password'}), 401

@app.route('/api/sudo/clear', methods=['POST'])
@login_required
def api_sudo_clear():
    """Clear sudo password from session"""
    session.pop('sudo_password', None)
    return jsonify({'status': 'ok'})

@app.route('/api/dhcp')
@login_required
def api_dhcp():
    data = parse_dhcp_config()
    
    # Add ping status to each host
    for host in data.get('hosts', []):
        if host.get('address'):
            ip_status = ping_results.get(host['address'], {})
            host['online'] = ip_status.get('online')
            host['last_check'] = ip_status.get('checked')
    
    return jsonify(data)

@app.route('/api/dhcp/host', methods=['POST'])
@login_required
def api_add_dhcp_host():
    """Add a new DHCP static mapping (staged)"""
    data = request.json
    hostname = data.get('hostname', '').strip()
    mac = data.get('mac', '').strip().lower()
    ip = data.get('ip', '').strip()
    
    # Validation
    if not hostname or not mac or not ip:
        return jsonify({'error': 'Missing required fields'}), 400
    
    # Validate MAC format
    if not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac):
        return jsonify({'error': 'Invalid MAC address format'}), 400
    
    # Validate IP
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return jsonify({'error': 'Invalid IP address'}), 400
    
    success, msg = add_dhcp_host(hostname, mac, ip)
    
    if success:
        return jsonify({'status': 'ok', 'message': msg, 'pending': True})
    else:
        return jsonify({'error': msg}), 500

@app.route('/api/dhcp/host/<hostname>', methods=['PUT'])
@login_required
def api_edit_dhcp_host(hostname):
    """Edit an existing DHCP host (staged)"""
    data = request.json
    mac = data.get('mac', '').strip().lower() if data.get('mac') else None
    ip = data.get('ip', '').strip() if data.get('ip') else None
    
    if mac and not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', mac):
        return jsonify({'error': 'Invalid MAC address format'}), 400
    
    if ip:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return jsonify({'error': 'Invalid IP address'}), 400
    
    success, msg = edit_dhcp_host(hostname, mac, ip)
    return jsonify({'status': 'ok', 'message': msg, 'pending': True})

@app.route('/api/dhcp/host/<hostname>', methods=['DELETE'])
@login_required
def api_delete_dhcp_host(hostname):
    """Delete a DHCP host (staged)"""
    success, msg = delete_dhcp_host(hostname)
    return jsonify({'status': 'ok', 'message': msg, 'pending': True})

@app.route('/api/dns')
@login_required
def api_dns():
    zones = parse_bind_zones()
    
    # Add ping status to A records
    for zone in zones:
        for record in zone.get('records', []):
            if record['type'] == 'A':
                ip_status = ping_results.get(record['value'], {})
                record['online'] = ip_status.get('online')
                record['last_check'] = ip_status.get('checked')
    
    return jsonify({'zones': zones})

@app.route('/api/dns/zone', methods=['POST'])
@login_required
@sudo_required
def api_create_zone():
    """Create a new DNS zone"""
    data = request.json
    zone_name = data.get('name', '').strip()
    soa_ns = data.get('soa_ns', '').strip()
    soa_email = data.get('soa_email', '').strip()
    ttl = int(data.get('ttl', 86400))
    
    if not zone_name or not soa_ns or not soa_email:
        return jsonify({'error': 'Missing required fields'}), 400
    
    success, msg = create_zone_file(zone_name, soa_ns, soa_email, ttl)
    
    if success:
        return jsonify({'status': 'ok', 'message': 'Zone created'})
    else:
        return jsonify({'error': msg}), 500

@app.route('/api/dns/zone/<zone>/record', methods=['POST'])
@login_required
def api_add_dns_record(zone):
    """Add a DNS record (staged)"""
    data = request.json
    name = data.get('name', '').strip()
    rtype = data.get('type', '').strip().upper()
    value = data.get('value', '').strip()
    
    if not name or not rtype or not value:
        return jsonify({'error': 'Missing required fields'}), 400
    
    if rtype not in ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'PTR']:
        return jsonify({'error': 'Invalid record type'}), 400
    
    success, msg = add_dns_record(zone, name, rtype, value)
    return jsonify({'status': 'ok', 'message': msg, 'pending': True})

@app.route('/api/dns/zone/<zone>/record', methods=['PUT'])
@login_required
def api_edit_dns_record(zone):
    """Edit a DNS record (staged)"""
    data = request.json
    name = data.get('name', '').strip()
    rtype = data.get('type', '').strip().upper()
    value = data.get('value', '').strip()
    
    if not name or not rtype or not value:
        return jsonify({'error': 'Missing required fields'}), 400
    
    success, msg = edit_dns_record(zone, name, rtype, value)
    return jsonify({'status': 'ok', 'message': msg, 'pending': True})

@app.route('/api/dns/zone/<zone>/record/<name>/<rtype>', methods=['DELETE'])
@login_required
def api_delete_dns_record(zone, name, rtype):
    """Delete a DNS record (staged)"""
    success, msg = delete_dns_record(zone, name, rtype.upper())
    return jsonify({'status': 'ok', 'message': msg, 'pending': True})

@app.route('/api/leases')
@login_required
def api_leases():
    leases = get_dhcp_leases()
    
    # Add ping status
    for lease in leases:
        ip_status = ping_results.get(lease['ip'], {})
        lease['online'] = ip_status.get('online')
    
    return jsonify({'leases': leases})

@app.route('/api/networks')
@login_required
def api_networks():
    return jsonify({'networks': get_networks()})

@app.route('/api/scan')
@login_required
def api_scan():
    return jsonify({
        'results': ping_results,
        'last_scan': last_scan_time.isoformat() if last_scan_time else None,
        'in_progress': scan_in_progress,
        'online_count': sum(1 for r in ping_results.values() if r.get('online')),
        'total_count': len(ping_results)
    })

@app.route('/api/scan/start', methods=['POST'])
@login_required
def api_start_scan():
    if not scan_in_progress:
        thread = threading.Thread(target=scan_network)
        thread.start()
        return jsonify({'status': 'started'})
    return jsonify({'status': 'already_running'})

@app.route('/api/scan/history')
@login_required
def api_scan_history():
    """Return scan history for charts"""
    return jsonify({'history': scan_history})

@app.route('/api/lease/history')
@login_required
def api_lease_history():
    """Return DHCP lease request history for charts"""
    return jsonify({'history': get_lease_history()})

@app.route('/api/changes')
@login_required
def api_get_changes():
    """Get pending changes"""
    with pending_changes_lock:
        return jsonify({
            'dhcp': pending_changes['dhcp'],
            'dns': pending_changes['dns'],
            'total': len(pending_changes['dhcp']) + len(pending_changes['dns'])
        })

@app.route('/api/changes/apply', methods=['POST'])
@login_required
@sudo_required
def api_apply_changes():
    """Apply all pending changes to disk"""
    results = {'dhcp': None, 'dns': None}
    
    with pending_changes_lock:
        has_dhcp = len(pending_changes['dhcp']) > 0
        has_dns = len(pending_changes['dns']) > 0
    
    if has_dhcp:
        success, msg, old_content, new_content = apply_dhcp_changes()
        results['dhcp'] = {
            'status': 'ok' if success else 'failed',
            'message': msg
        }
    
    if has_dns:
        dns_results = apply_dns_changes()
        results['dns'] = dns_results
    
    return jsonify(results)

@app.route('/api/changes/discard', methods=['POST'])
@login_required
def api_discard_changes():
    """Discard all pending changes"""
    with pending_changes_lock:
        pending_changes['dhcp'] = []
        pending_changes['dns'] = []
    return jsonify({'status': 'ok', 'message': 'Changes discarded'})

@app.route('/api/changes/preview')
@login_required
def api_preview_changes():
    """Preview what changes will be made"""
    preview = {'dhcp': [], 'dns': []}
    
    with pending_changes_lock:
        for change in pending_changes['dhcp']:
            action = change['action']
            hostname = change['hostname']
            data = change.get('data', {})
            
            if action == 'add':
                preview['dhcp'].append(f"+ Add host '{hostname}': MAC={data.get('mac')}, IP={data.get('ip')}")
            elif action == 'edit':
                preview['dhcp'].append(f"~ Edit host '{hostname}': MAC={data.get('mac')}, IP={data.get('ip')}")
            elif action == 'delete':
                preview['dhcp'].append(f"- Delete host '{hostname}'")
        
        for change in pending_changes['dns']:
            action = change['action']
            zone = change['zone']
            record = change.get('record', {})
            
            if action == 'add':
                preview['dns'].append(f"+ Add {record.get('type')} record '{record.get('name')}' = {record.get('value')} in zone '{zone}'")
            elif action == 'edit':
                preview['dns'].append(f"~ Edit {record.get('type')} record '{record.get('name')}' = {record.get('value')} in zone '{zone}'")
            elif action == 'delete':
                preview['dns'].append(f"- Delete {record.get('type')} record '{record.get('name')}' from zone '{zone}'")
    
    return jsonify(preview)

@app.route('/api/ping/<ip>')
@login_required
def api_ping(ip):
    try:
        ipaddress.ip_address(ip)
        online = ping_host(ip)
        return jsonify({'ip': ip, 'online': online})
    except ValueError:
        return jsonify({'error': 'Invalid IP'}), 400

@app.route('/api/config')
@login_required
def api_config():
    return jsonify({
        'network': CONFIG['network'],
        'dhcp_conf': CONFIG['dhcp_conf'],
        'bind_dir': CONFIG['bind_dir'],
        'scan_interval': CONFIG['scan_interval'],
        'ping_timeout': CONFIG['ping_timeout'],
        'max_threads': CONFIG['max_threads']
    })

@app.route('/api/config', methods=['POST'])
@login_required
def api_update_config():
    data = request.json
    
    if 'scan_interval' in data:
        CONFIG['scan_interval'] = max(60, int(data['scan_interval']))  # Minimum 1 minute
    if 'ping_timeout' in data:
        CONFIG['ping_timeout'] = max(1, min(10, int(data['ping_timeout'])))  # 1-10 seconds
    if 'network' in data:
        try:
            ipaddress.ip_network(data['network'], strict=False)
            CONFIG['network'] = data['network']
        except ValueError:
            pass
    if 'max_threads' in data:
        CONFIG['max_threads'] = max(10, min(500, int(data['max_threads'])))
    
    return jsonify({'status': 'updated', 'config': CONFIG})

@app.route('/api/services')
@login_required
def api_services():
    """Check status of DHCP and DNS services"""
    services = {}
    
    for service in ['isc-dhcp-server', 'named', 'bind9']:
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', service],
                capture_output=True,
                text=True
            )
            if result.stdout.strip() == 'active':
                services[service] = 'running'
            else:
                services[service] = 'stopped'
        except:
            services[service] = 'unknown'
    
    return jsonify(services)

@app.route('/api/services/<service>/<action>', methods=['POST'])
@login_required
@sudo_required
def api_service_action(service, action):
    """Control services (requires sudo)"""
    if action not in ['start', 'stop', 'restart', 'reload']:
        return jsonify({'error': 'Invalid action'}), 400
    
    if service not in ['isc-dhcp-server', 'named', 'bind9']:
        return jsonify({'error': 'Invalid service'}), 400
    
    success, stdout, stderr = run_with_sudo(['systemctl', action, service])
    
    return jsonify({
        'status': 'success' if success else 'failed',
        'output': stdout + stderr
    })

@app.route('/api/lang', methods=['POST'])
def api_set_lang():
    """Set language preference"""
    lang = request.json.get('lang', 'en')
    if lang in ['en', 'sv']:
        session['lang'] = lang
        return jsonify({'status': 'ok', 'lang': lang})
    return jsonify({'error': 'Invalid language'}), 400

@app.route('/api/stats')
@login_required
def api_stats():
    """Get dashboard statistics"""
    dhcp_data = parse_dhcp_config()
    zones = parse_bind_zones()
    leases = get_dhcp_leases()
    
    static_hosts = len(dhcp_data.get('hosts', []))
    dynamic_hosts = len([l for l in leases if l.get('state') == 'active'])
    online_count = sum(1 for r in ping_results.values() if r.get('online'))
    
    return jsonify({
        'static_hosts': static_hosts,
        'dynamic_hosts': dynamic_hosts,
        'dns_zones': len(zones),
        'online_hosts': online_count,
        'active_leases': dynamic_hosts,
        'last_scan': last_scan_time.isoformat() if last_scan_time else None,
        'scan_interval': CONFIG['scan_interval']
    })

# ============================================================================
# Main
# ============================================================================

if __name__ == '__main__':
    # Start background scanner
    start_background_scanner()
    
    app.run(host='0.0.0.0', port=5000, debug=False)
