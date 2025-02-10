# Setup Guide for Simple Cloud Service

🚀 **Quick Installation:** Instead of following the steps manually, you can simply download and run the `install.sh` script, which automates the entire setup process.

### **How to Use install.sh**
```bash
wget https://raw.githubusercontent.com/Amirabbasjadidi/Simple-Cloud/main/install.sh
chmod +x install.sh
./install.sh
```

This script will automatically set up everything for you, including dependencies, virtual environment, and the systemd service.

---

## 1. Update System and Install Dependencies

```
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip python3-venv -y
```

## 2. Clone the Project and Create Virtual Environment

```
cd /path/to/your/project
git clone https://github.com/Amirabbasjadidi/Simple-Cloud.git
cd Simple-Cloud
python3 -m venv venv
source venv/bin/activate
```

## 3. Install Required Packages

```
pip install -r requirements.txt
```

## 4. Run Flask Application Temporarily

```
source venv/bin/activate
python app.py
```

## 5. Setup Flask as a Systemd Service

### Create the Service File

```
sudo nano /etc/systemd/system/simplecloud.service
```

### Add the Following Content

```
[Unit]
Description=Simple Cloud Service
After=network.target

[Service]
User=your_user  # Replace with your actual username
WorkingDirectory=/path/to/your/project  # Replace with the actual project path
EPATH=/path/to/your/project/venv/bin"  # Replace with the actual Environment (PATH)
ExecStart=/path/to/your/project/venv/bin/gunicorn -w 4 -b 0.0.0.0:80 app:app
Restart=always

[Install]
WantedBy=multi-user.target
```

**Note:** Only edit `User`, `WorkingDirectory`, `Environment (PATH)`, and ensure the correct `ExecStart` path. Do not modify other sections unless necessary.

## 6. Allow Python to Bind to Port 80

### Find the Real Python Path

```
readlink -f /path/to/your/project/venv/bin/python3
```

#### Example Output:

```
/usr/bin/python3.12
```

### Grant Permission to Use Port 80

```
sudo setcap 'cap_net_bind_service=+ep' /usr/bin/python3.12
```

**Note:** Running Flask directly on port 80 is suitable for local network applications. However, for larger deployments, it is recommended to use Nginx or a similar reverse proxy for better security and performance.

## 7. Enable and Start the Service

```
sudo systemctl daemon-reload
sudo systemctl enable simplecloud
sudo systemctl start simplecloud
```

## 8. Check Service Status

```
sudo systemctl status simplecloud
```

### Access the Flask Application

```
http://your_server_ip
```

## ⭐ Support the Project

If you find this project useful, consider giving it a star on GitHub! 🌟
[GitHub Repository](https://github.com/Amirabbasjadidi/Simple-Cloud)
