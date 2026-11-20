#!/usr/bin/env bash
# Simple Cloud installer for Debian/Ubuntu. Safe to re-run (idempotent): it updates the code,
# keeps existing secrets/config/data, runs database migrations (with backup) and restarts.
#
#   sudo ./install.sh [options]
#
# Options:
#   --dir PATH            code location (default /opt/simplecloud)
#   --data-dir PATH       database, uploads, backups (default /var/lib/simplecloud)
#   --repo URL            git repository (default https://github.com/Amirabbasjadidi/Simple-Cloud.git)
#   --branch NAME         git branch (default main)
#   --domain NAME         server_name for Nginx (default: catch-all)
#   --certbot EMAIL       get a Let's Encrypt certificate for --domain (needs public DNS)
#   --upgrade             also run `apt upgrade`
#   --import-from PATH    copy database.db and uploads/ from an old install (only into an empty data dir)
#   --workers N           gunicorn workers (default 4)
#   -h, --help
set -euo pipefail

INSTALL_DIR=/opt/simplecloud
DATA_DIR=/var/lib/simplecloud
REPO=https://github.com/Amirabbasjadidi/Simple-Cloud.git
BRANCH=main
DOMAIN=""
CERTBOT_EMAIL=""
APT_UPGRADE=0
IMPORT_FROM=""
WORKERS=4
APP_USER=simplecloud
ENV_FILE=/etc/simplecloud.env
SERVICE_FILE=/etc/systemd/system/simplecloud.service
NGINX_SITE=/etc/nginx/sites-available/simplecloud

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --data-dir) DATA_DIR="$2"; shift 2 ;;
        --repo) REPO="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --certbot) CERTBOT_EMAIL="$2"; shift 2 ;;
        --upgrade) APT_UPGRADE=1; shift ;;
        --import-from) IMPORT_FROM="$2"; shift 2 ;;
        --workers) WORKERS="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root: sudo $0 $*"
[ -z "$CERTBOT_EMAIL" ] || [ -n "$DOMAIN" ] || die "--certbot needs --domain"
[[ "$WORKERS" =~ ^[0-9]+$ ]] || die "--workers must be a number"

# ---------------------------------------------------------------- packages
log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
if [ "$APT_UPGRADE" -eq 1 ]; then apt-get upgrade -y; fi
PKGS=(python3 python3-venv python3-pip git nginx openssl)
[ -z "$CERTBOT_EMAIL" ] || PKGS+=(certbot python3-certbot-nginx)
apt-get install -y "${PKGS[@]}"

# ---------------------------------------------------------------- system user
if ! id "$APP_USER" >/dev/null 2>&1; then
    log "Creating system user $APP_USER"
    useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# ---------------------------------------------------------------- code
# Code lives in $INSTALL_DIR, owned by root and read-only for the service user.
if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating code in $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
    git -C "$INSTALL_DIR" checkout --quiet "$BRANCH"
    git -C "$INSTALL_DIR" pull --quiet --ff-only origin "$BRANCH"
elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    die "$INSTALL_DIR exists, is not empty and is not a git checkout. Choose another --dir."
else
    log "Cloning $REPO ($BRANCH) into $INSTALL_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO" "$INSTALL_DIR"
fi

log "Python virtual environment"
[ -x "$INSTALL_DIR/venv/bin/python" ] || python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

# ---------------------------------------------------------------- data
install -d -m 750 -o "$APP_USER" -g "$APP_USER" "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/backups"
if [ -n "$IMPORT_FROM" ]; then
    [ -f "$IMPORT_FROM/database.db" ] || die "$IMPORT_FROM/database.db not found"
    if [ -e "$DATA_DIR/database.db" ]; then
        echo "Data dir already has a database.db; skipping import (nothing overwritten)."
    else
        log "Copying data from $IMPORT_FROM (the old copy is left in place)"
        cp -a "$IMPORT_FROM/database.db" "$DATA_DIR/"
        for f in database.db-wal database.db-shm; do
            [ ! -e "$IMPORT_FROM/$f" ] || cp -a "$IMPORT_FROM/$f" "$DATA_DIR/"
        done
        [ ! -d "$IMPORT_FROM/uploads" ] || cp -a "$IMPORT_FROM/uploads/." "$DATA_DIR/uploads/"
        chown -R "$APP_USER:$APP_USER" "$DATA_DIR"
    fi
fi

# ---------------------------------------------------------------- config (existing values are kept)
set_default() {  # add KEY=VALUE to the env file only if KEY is not there yet
    grep -q "^$1=" "$ENV_FILE" 2>/dev/null || echo "$1=$2" >> "$ENV_FILE"
}
log "Configuration in $ENV_FILE"
touch "$ENV_FILE"
set_default SECRET_KEY "$(openssl rand -hex 32)"
set_default DATABASE "$DATA_DIR/database.db"
set_default UPLOAD_FOLDER "$DATA_DIR/uploads"
set_default BACKUP_FOLDER "$DATA_DIR/backups"
set_default PROXY_COUNT 1
set_default COOKIE_SECURE 0
set_default MAX_CONTENT_LENGTH 2147483648
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

# ---------------------------------------------------------------- database (creates or migrates, backs up first)
log "Database setup / migration"
cd "$INSTALL_DIR"
runuser -u "$APP_USER" -- env SIMPLECLOUD_ENV_FILE="$ENV_FILE" "$INSTALL_DIR/venv/bin/flask" --app app migrate

# ---------------------------------------------------------------- systemd
log "systemd service"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Simple Cloud
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$INSTALL_DIR/venv/bin/gunicorn --workers $WORKERS --bind 127.0.0.1:8000 --timeout 600 app:app
Restart=always
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet simplecloud
systemctl restart simplecloud

# ---------------------------------------------------------------- nginx
log "Nginx reverse proxy"
MAX_BODY=$(grep '^MAX_CONTENT_LENGTH=' "$ENV_FILE" | cut -d= -f2)
if [ -n "$DOMAIN" ]; then LISTEN="listen 80;"; NAME="$DOMAIN"; else LISTEN="listen 80 default_server;"; NAME="_"; fi
if grep -q "managed by Certbot" "$NGINX_SITE" 2>/dev/null; then
    # Re-running must not drop the TLS config certbot added. Delete the file to regenerate it.
    echo "$NGINX_SITE was modified by certbot; leaving it unchanged."
else
cat > "$NGINX_SITE" <<EOF
server {
    $LISTEN
    server_name $NAME;

    client_max_body_size $(( MAX_BODY / 1048576 + 16 ))m;
    proxy_request_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
    }
}
EOF
fi
ln -sf "$NGINX_SITE" /etc/nginx/sites-enabled/simplecloud
if [ -z "$DOMAIN" ] && [ -L /etc/nginx/sites-enabled/default ]; then
    # Both would claim default_server. Only the symlink is removed; sites-available/default stays.
    echo "Disabling the stock 'default' Nginx site (re-enable: ln -s ../sites-available/default /etc/nginx/sites-enabled/)"
    rm /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl reload nginx

# ---------------------------------------------------------------- TLS
if [ -n "$CERTBOT_EMAIL" ]; then
    log "Let's Encrypt certificate for $DOMAIN"
    certbot --nginx -d "$DOMAIN" -m "$CERTBOT_EMAIL" --agree-tos --redirect --non-interactive
    sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' "$ENV_FILE"
    systemctl restart simplecloud
fi

# ---------------------------------------------------------------- done
log "Status"
systemctl --no-pager --lines=0 status simplecloud || true
sleep 2
URL="http://${DOMAIN:-$(hostname -I | awk '{print $1}')}"
[ -z "$CERTBOT_EMAIL" ] || URL="https://$DOMAIN"
if curl -fsS -o /dev/null "http://127.0.0.1:8000/login"; then
    echo "Simple Cloud is running at $URL"
else
    echo "The service did not answer yet. Check: journalctl -u simplecloud -n 50"
fi
echo "If this was a fresh install, the initial admin password was printed above (search for 'Initial admin')."
