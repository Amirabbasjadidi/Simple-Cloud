#!/bin/bash

# Get project path and username
echo "Enter the project path (e.g., /home/user/Simple-Cloud):"
read PROJECT_PATH
echo "Enter the username (e.g., $(whoami)):"
read USERNAME

# Update system and install dependencies
echo "Updating system and installing dependencies..."
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv -y

# Clone the project and set up virtual environment
echo "Cloning the project and setting up virtual environment..."
cd "$PROJECT_PATH"
git clone https://github.com/Amirabbasjadidi/Simple-Cloud.git
cd Simple-Cloud
python3 -m venv venv
source venv/bin/activate

# Install required packages
echo "Installing required packages..."
pip install -r requirements.txt

# Create Systemd service
echo "Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/simplecloud.service"
echo "[Unit]
Description=Simple Cloud Service
After=network.target

[Service]
User=$USERNAME
WorkingDirectory=$PROJECT_PATH/Simple-Cloud
Environment=$PROJECT_PATH/Simple-Cloud/venv/bin
ExecStart=$PROJECT_PATH/Simple-Cloud/venv/bin/gunicorn -w 4 -b 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target" | sudo tee $SERVICE_FILE

# Grant permission to bind to port 80
echo "Granting permission to bind to port 80..."
PYTHON_PATH=$(readlink -f "$PROJECT_PATH/Simple-Cloud/venv/bin/python3")
sudo setcap 'cap_net_bind_service=+ep' "$PYTHON_PATH"

# Enable and start the service
echo "Enabling and starting the service..."
sudo systemctl daemon-reload
sudo systemctl enable simplecloud
sudo systemctl start simplecloud

# Check service status
echo "Checking service status..."
sudo systemctl status simplecloud --no-pager

# Fetch server IP and test with Curl
echo "Fetching server IP and testing the service..."
IP_ADDRESS=$(hostname -I | awk '{print $1}')
echo "Server is running at: http://$IP_ADDRESS"
curl -I "http://$IP_ADDRESS"
