# How to install Simple Cloud

This guide installs Simple Cloud on a fresh **Ubuntu Server 24.04** and serves it on the LAN
over **plain HTTP** at `http://cloud.local`. Debian 12 works the same way.

What the installer sets up:

| Part | Where |
|---|---|
| Code (owned by root, read-only for the app) | `/opt/simplecloud` |
| Database, uploads, backups | `/var/lib/simplecloud` |
| Settings, including the generated `SECRET_KEY` | `/etc/simplecloud.env` |
| App service (gunicorn on 127.0.0.1:8000, user `simplecloud`) | `simplecloud.service` |
| Web server on port 80 | Nginx site `/etc/nginx/sites-available/simplecloud` |
| Optional daily backup | `simplecloud-backup.timer` |

## 1. Install

```bash
sudo apt update && sudo apt install -y git
git clone https://github.com/Amirabbasjadidi/Simple-Cloud.git
cd Simple-Cloud
sudo ./install.sh --domain cloud.local --backup-timer
```

The installer clones the `main` branch into `/opt/simplecloud` (the clone you ran it from is only
used to start it). Options:

| Option | Meaning |
|---|---|
| `--domain NAME` | Name the site answers to (Nginx `server_name`). Without it, it answers on any name/IP. |
| `--pip-index URL` | Install Python packages from a PyPI mirror, for example `--pip-index http://pypi.mirror.local/simple` (an `http://` mirror is marked trusted automatically). |
| `--backup-timer` | Back up every night at 03:00. |
| `--backup-keep N` | How many nightly archives to keep (default 14). |
| `--workers N` | gunicorn worker processes (default 4). |
| `--branch NAME`, `--repo URL` | Install another branch or a fork. |
| `--dir PATH`, `--data-dir PATH` | Other locations for the code and the data. |
| `--import-from PATH` | Copy `database.db` and `uploads/` from an old install (see section 7). |
| `--upgrade` | Also run `apt upgrade`. |

The installer is safe to run again: it keeps your settings and data.

## 2. The first admin password

On a fresh install the end of the output contains:

```
============================================================
Initial admin created. Username: admin  Password: Xy3...
You must change this password at first login.
============================================================
```

Sign in as `admin` with that password; you will be asked to choose a new one. It is shown only
once. If you missed it, set a new one:

```bash
cd /opt/simplecloud
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app changepassword --username admin --force-change
```

Then create accounts for your colleagues under **Admin** in the web interface, or with the
command line ([User management cli.md](./User%20management%20cli.md)).

## 3. A name on the network: DNS on the MikroTik router

Give the server a fixed address first (a DHCP lease reservation or a static IP), then add a
static DNS record on the MikroTik router so every computer on the LAN can open `http://cloud.local`:

```
/ip dns static add name=cloud.local address=<server-ip>
```

For example `/ip dns static add name=cloud.local address=192.168.88.20`. The clients must use the
router as their DNS server (the default with MikroTik DHCP), and the router must answer
queries (`/ip dns set allow-remote-requests=yes`; keep port 53 closed on the WAN side in the
firewall). Check from a client:

```bash
nslookup cloud.local
```

Then open **http://cloud.local** in a browser. (`.local` names can also be claimed by mDNS on
some clients; if a Mac or Linux PC does not resolve it, use a name such as `cloud.lan`, both in the
MikroTik record and in `--domain`.)

The site uses plain HTTP: no certificate is needed and nothing in the app redirects to HTTPS.
Passwords cross the LAN unencrypted, so use it only on a network you trust. If you add HTTPS
later (your own reverse proxy or certificate), also set `COOKIE_SECURE=1` in
`/etc/simplecloud.env`; the installer will not overwrite an Nginx site you edited.

## 4. Settings

Edit `/etc/simplecloud.env`, then `sudo systemctl restart simplecloud`. Common settings:
`DEFAULT_LANG` (`fa` or `en`), `DEFAULT_QUOTA_BYTES`, `MAX_FILE_SIZE`, `MAX_CONTENT_LENGTH`
(re-run the installer after changing it, so Nginx accepts the new size). The full list is in the
[README](./README.md#configuration).

Useful commands:

```bash
systemctl status simplecloud              # is it running?
journalctl -u simplecloud -n 100          # recent log (sign-ins, uploads, errors)
journalctl -u simplecloud | grep event=login_failed
```

## 5. Backups

A backup is one file, `simplecloud-YYYYmmdd-HHMMSS.tar.gz`, containing a consistent snapshot of
the database (taken with SQLite's online backup API, so the app keeps running) and all uploaded
files. Archives are readable only by the `simplecloud` user: they contain every user's files.

**Daily, automatic:** install with `--backup-timer` (or re-run the installer with it). Archives
go to `/var/lib/simplecloud/backups`, the newest 14 are kept (`--backup-keep N`).

```bash
systemctl list-timers simplecloud-backup     # next run
journalctl -u simplecloud-backup             # results
```

**By hand, any time:**

```bash
cd /opt/simplecloud
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app backup
```

A backup on the same disk does not protect against a dead disk: copy the archives to another
machine, for example from that machine every night:

```bash
rsync -a admin@cloud.local:/var/lib/simplecloud/backups/ /srv/backup/simplecloud/
```

(Make the archives readable for that account, or run rsync as root on the server side.)

## 6. Restore

Restoring replaces the current database and files with those in the archive. The steps below keep
the current data next to it (renamed), so nothing is lost if you picked the wrong archive.

```bash
ARCHIVE=/var/lib/simplecloud/backups/simplecloud-20261120-030000.tar.gz   # the one to restore
D=/var/lib/simplecloud
NOW=$(date +%Y%m%d-%H%M%S)

sudo systemctl stop simplecloud simplecloud-backup.timer

# 1. move the current data aside (not deleted)
sudo mkdir "$D/before-restore-$NOW"
sudo mv "$D/uploads" "$D"/database.db* "$D/before-restore-$NOW/"

# 2. unpack the archive
sudo mkdir "$D/restore-$NOW"
sudo tar -xzf "$ARCHIVE" -C "$D/restore-$NOW"
sudo mv "$D/restore-$NOW/database.db" "$D/restore-$NOW/uploads" "$D/"
sudo rmdir --ignore-fail-on-non-empty "$D/restore-$NOW"
sudo chown -R simplecloud:simplecloud "$D"

# 3. bring an older backup up to the installed version (no-op otherwise), then start
cd /opt/simplecloud
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app migrate
sudo systemctl start simplecloud simplecloud-backup.timer
```

Check that you can sign in and open some files. When you are sure, remove
`/var/lib/simplecloud/before-restore-*` yourself.

On a **new server**, run the installer first, then follow the same steps. Copy the old
`SECRET_KEY` line from the old `/etc/simplecloud.env` if you want existing "remember me" sign-ins
to stay valid (otherwise everyone just signs in again).

## 7. Upgrading

```bash
cd ~/Simple-Cloud && git pull          # the copy you installed from
sudo ./install.sh --domain cloud.local --backup-timer
```

The installer pulls the newest `main` into `/opt/simplecloud`, updates the Python packages,
upgrades the database and restarts the service. Before changing the database schema it writes a
backup to `/var/lib/simplecloud/backups/<timestamp>/` automatically, and the migration can be
resumed if it is interrupted. To see what an upgrade would change without changing anything:

```bash
cd /opt/simplecloud
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app migrate --dry-run
```

**From the original Simple Cloud (before this version):** run the installer with
`--import-from /path/to/old/Simple-Cloud`. It copies (never moves) the old `database.db` and
`uploads/` into the new data directory and migrates them: files get random storage names and are
linked to their owners by user id. The old `root` account becomes an admin and, if it still has
the password `1234`, must choose a new password at the next sign-in. Stop the old service first
(`sudo systemctl disable --now simplecloud` if it used that name) so it does not hold port 80.

## 8. Docker (alternative)

```bash
cp .env.example .env        # then set SECRET_KEY (openssl rand -hex 32)
docker compose up -d
docker compose logs app | grep -A2 "Initial admin"
docker compose exec app flask --app app backup     # archives land in the data volume, /data/backups
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `502 Bad Gateway` | `journalctl -u simplecloud -n 50`; the app is not running or failed at start. |
| "Database upgrade required" | Run `flask migrate` as shown in section 7. |
| Large uploads fail | `MAX_CONTENT_LENGTH`/`MAX_FILE_SIZE` in `/etc/simplecloud.env`, then re-run the installer. |
| "Too many failed attempts" | Wait 15 minutes, or reset the password with the CLI. |
| `cloud.local` does not resolve | Section 3: the MikroTik record, and that the client uses the router for DNS. |
| Sign-in loops back to the login page | `COOKIE_SECURE` must be `0` on plain HTTP. |
