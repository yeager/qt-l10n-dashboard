# IntNetAdmin

<p align="center">
  <img src="https://raw.githubusercontent.com/yeager/IntNetAdmin/main/static/logo.svg" alt="IntNetAdmin Logo" width="120">
</p>

**Manage your internal network with ease**

[![GitHub](https://img.shields.io/badge/GitHub-yeager/IntNetAdmin-181717?logo=github)](https://github.com/yeager/IntNetAdmin)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Flask](https://img.shields.io/badge/Flask-2.0+-green.svg)](https://flask.palletsprojects.com/)

IntNetAdmin is a web-based dashboard for managing DHCP and DNS services on your internal network. It provides a modern, clean interface for viewing and configuring network services, with automatic network scanning and real-time status monitoring.

![Screenshot](https://via.placeholder.com/800x500?text=IntNetAdmin+Dashboard)

## Features

### Core Functionality
- **DHCP Management** - View and add static host reservations (MAC → IP mapping)
- **DNS Zone Management** - Browse DNS records with status indicators, create new zones
- **IP Scanner** - Automatic network scanning every 2 hours with manual trigger option
- **DHCP Leases** - View active and expired DHCP leases with status
- **Network Overview** - Subnet configuration display

### Dashboard
- Real-time statistics (static hosts, dynamic hosts, online count, DNS zones)
- Network status history charts (powered by Chart.js)
- Host distribution visualization
- Service status monitoring (ISC DHCP, BIND)

### User Experience
- 🌙 Dark/Light theme toggle
- 🇬🇧/🇸🇪 i18n support (English & Swedish)
- Green/red status indicators for online/offline hosts
- Search and filter functionality
- Responsive design for mobile devices

### Security
- PAM authentication (system users)
- Session-based sudo password (never stored on disk)
- No external API dependencies

## Requirements

- Python 3.8+
- ISC DHCP Server (isc-dhcp-server)
- BIND DNS Server (bind9)
- Linux system with PAM

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/yourusername/intnetadmin.git
cd intnetadmin
```

### 2. Create virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

Edit `app.py` to match your network configuration:

```python
DEFAULT_CONFIG = {
    'dhcp_conf': '/etc/dhcp/dhcpd.conf',
    'bind_dir': '/etc/bind',
    'network': '192.168.2.0/23',  # Your network CIDR
    'scan_interval': 7200,        # 2 hours
    'ping_timeout': 1,
    'ping_count': 1,
    'max_threads': 100,
}
```

### 5. Run with Gunicorn (Production)

```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 6. Run in Development

```bash
python app.py
```

## Systemd Service

Create `/etc/systemd/system/intnetadmin.service`:

```ini
[Unit]
Description=IntNetAdmin - Network Administration Dashboard
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=/opt/intnetadmin
Environment="PATH=/opt/intnetadmin/venv/bin"
ExecStart=/opt/intnetadmin/venv/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable intnetadmin
sudo systemctl start intnetadmin
```

## Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name netadmin.example.local;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Configuration Files

IntNetAdmin reads the following system files:

| File | Description |
|------|-------------|
| `/etc/dhcp/dhcpd.conf` | DHCP server configuration |
| `/etc/bind/db.*` | BIND DNS zone files |
| `/var/lib/dhcp/dhcpd.leases` | DHCP lease database |

**Note:** Write operations (adding hosts/zones) require sudo privileges, which are requested through the UI and stored only in session memory.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/user` | GET | Current user info |
| `/api/stats` | GET | Dashboard statistics |
| `/api/dhcp` | GET | DHCP configuration |
| `/api/dhcp/host` | POST | Add static DHCP host |
| `/api/dns` | GET | DNS zones and records |
| `/api/dns/zone` | POST | Create new DNS zone |
| `/api/leases` | GET | DHCP leases |
| `/api/networks` | GET | Network configuration |
| `/api/scan` | GET | IP scan results |
| `/api/scan/start` | POST | Trigger manual scan |
| `/api/scan/history` | GET | Scan history for charts |
| `/api/services` | GET | Service status |
| `/api/config` | GET/POST | App configuration |
| `/api/sudo` | POST | Set sudo password |

## Screenshots

### Dashboard
The main dashboard shows network statistics, charts, and service status.

### DHCP View
View and manage static host reservations with online/offline indicators.

### DNS View
Browse DNS zones and records with search functionality.

### IP Scanner
Visual grid of online hosts with hostname mapping.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Author

**Daniel Nylander** - © 2026

## Acknowledgments

- [Flask](https://flask.palletsprojects.com/) - Web framework
- [Chart.js](https://www.chartjs.org/) - Charts library
- [ISC DHCP](https://www.isc.org/dhcp/) - DHCP server
- [BIND](https://www.isc.org/bind/) - DNS server
