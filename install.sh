#!/bin/bash

# Get project path and username
echo "Enter the project path (e.g., /home/user/Simple-Cloud):"
read PROJECT_PATH
echo "Enter the username (e.g., $(whoami)):"
read USERNAME

# Check if project path exists
if [ ! -d "$PROJECT_PATH" ]; then
    echo "Error: Project path does not exist!"
    exit 1
fi

# Update system and install dependencies
echo "Updating system and installing dependencies..."
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv -y

# Clone the project and set up virtual environment
echo "Cloning the project and setting up virtual environment..."
cd "$PROJECT_PATH" || exit 1
git clone https://github.com/Amirabbasjadidi/Simple-Cloud.git
cd Simple-Cloud || exit 1
python3 -m venv venv
source venv/bin/activate

# Install required packages
echo "Installing required packages..."
pip install -r requirements.txt

# Generate a persistent SECRET_KEY
SECRET_KEY_FILE="/etc/simplecloud.env"
if [ ! -f "$SECRET_KEY_FILE" ]; then
    echo "Generating a new SECRET_KEY..."
    SECRET_KEY=$(openssl rand -hex 32)
    echo "SECRET_KEY=$SECRET_KEY" | sudo tee "$SECRET_KEY_FILE"
    sudo chmod 600 "$SECRET_KEY_FILE"  # Secure the file
else
    echo "SECRET_KEY already exists. Skipping generation."
fi

# Create Systemd service
echo "Creating systemd service..."
SERVICE_FILE="/etc/systemd/system/simplecloud.service"
echo "[Unit]
Description=Simple Cloud Service
After=network.target

[Service]
User=$USERNAME
WorkingDirectory=$PROJECT_PATH/Simple-Cloud
Environment=\"PATH=$PROJECT_PATH/Simple-Cloud/venv/bin:$PATH\"
ExecStart=$PROJECT_PATH/Simple-Cloud/venv/bin/gunicorn -w 4 -b 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target" | sudo tee $SERVICE_FILE

echo "Granting permission to bind to port 80..."
PYTHON_PATH="$PROJECT_PATH/Simple-Cloud/venv/bin/python"

# Check if PYTHON_PATH is a symlink
if [ -L "$PYTHON_PATH" ]; then
    REAL_PYTHON_PATH=$(readlink -f "$PYTHON_PATH")
else
    REAL_PYTHON_PATH="$PYTHON_PATH"
fi

if [ -f "$REAL_PYTHON_PATH" ]; then
    if sudo setcap 'cap_net_bind_service=+ep' "$REAL_PYTHON_PATH"; then
        echo "Successfully granted permission to bind to port 80."
    else
        echo "Failed to set capabilities. You may need to run Gunicorn with sudo."
        echo "Try: sudo $REAL_PYTHON_PATH -m gunicorn -w 4 -b 0.0.0.0:80 app:app"
    fi
else
    echo "Error: Python not found in virtual environment!"
    exit 1
fi

# Enable and start the service
echo "Enabling and starting the service..."
sudo systemctl daemon-reload
sudo systemctl enable simplecloud

# Check if Gunicorn is already running
if pgrep -f "gunicorn" > /dev/null; then
    echo "Gunicorn is already running. Restarting..."
    sudo systemctl restart simplecloud
else
    echo "Starting service..."
    sudo systemctl start simplecloud
fi

# Check service status
echo "Checking service status..."
sudo systemctl status simplecloud --no-pager

# Fetch server IP and test with Curl
echo "Fetching server IP and testing the service..."
IP_ADDRESS=$(hostname -I | awk '{print $1}')
echo "Server is running at: http://$IP_ADDRESS"
curl -I "http://$IP_ADDRESS"
