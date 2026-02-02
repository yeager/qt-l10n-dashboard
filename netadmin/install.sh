#!/bin/bash
# NetAdmin Installation Script

set -e

APP_DIR="/opt/netadmin"
APP_USER="www-data"

echo "=== Installing NetAdmin ==="

# Install dependencies
echo "Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv python3-pam

# Create app directory
echo "Creating application directory..."
sudo mkdir -p $APP_DIR
sudo cp -r ./* $APP_DIR/
sudo chown -R $APP_USER:$APP_USER $APP_DIR

# Create virtual environment
echo "Setting up Python virtual environment..."
cd $APP_DIR
sudo -u $APP_USER python3 -m venv venv
sudo -u $APP_USER ./venv/bin/pip install --upgrade pip
sudo -u $APP_USER ./venv/bin/pip install flask python-pam gunicorn

# Create systemd service
echo "Creating systemd service..."
sudo tee /etc/systemd/system/netadmin.service > /dev/null <<EOF
[Unit]
Description=NetAdmin DHCP/DNS Management Dashboard
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
ExecStart=$APP_DIR/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Set permissions for PAM and config file access
echo "Setting up permissions..."
sudo chmod +r /etc/dhcp/dhcpd.conf 2>/dev/null || true
sudo chmod +r /etc/bind/db.* 2>/dev/null || true
sudo chmod +r /var/lib/dhcp/dhcpd.leases 2>/dev/null || true

# Enable and start service
echo "Starting NetAdmin service..."
sudo systemctl daemon-reload
sudo systemctl enable netadmin
sudo systemctl restart netadmin

# Show status
echo ""
echo "=== Installation Complete ==="
echo "NetAdmin is running at: http://$(hostname -I | awk '{print $1}'):5000"
echo ""
sudo systemctl status netadmin --no-pager
