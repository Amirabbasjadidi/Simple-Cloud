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
| Optional daily database backup | `simplecloud-backup.timer` |

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
| `--backup-timer` | Back up the **database** every night at 03:00 (not the uploaded files, see section 5). |
| `--backup-keep N` | How many nightly database backups to keep (default 14). Remembered for later runs. |
| `--backup-dir PATH` | Where the database backups go, for example another disk or a mounted NAS share (default `/var/lib/simplecloud/backups`). Remembered for later runs. |
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

There are two kinds of backup. Both are single `.tar.gz` files and both use SQLite's online backup
API, so the database copy is consistent and the app keeps running.

| | Database backup | Full backup |
|---|---|---|
| File name | `simplecloud-db-YYYYmmdd-HHMMSS.tar.gz` | `simplecloud-YYYYmmdd-HHMMSS.tar.gz` |
| Contains | the database: users, password hashes, 2FA settings, folders, file list and owners, share links, quotas | the database **and every uploaded file** |
| Size | small (usually a few MB) | as large as all uploads |
| How | nightly timer, or by hand with `--db-only` | **only by hand**; the timer never runs it |
| Default location | `DB_BACKUP_FOLDER` (set by `--backup-dir`) | `BACKUP_FOLDER` (`/var/lib/simplecloud/backups`) |

**The nightly backup does not contain the uploaded files** (they are too large to copy every
night). Protect `/var/lib/simplecloud/uploads` separately, for example with an rsync or snapshot of
that disk to another machine: uploads are stored under random names and never changed in place, so
an incremental copy is cheap.

```bash
rsync -a /var/lib/simplecloud/uploads/ /mnt/nas/simplecloud-uploads/      # run as root, e.g. from cron
```

Archives are readable only by the `simplecloud` user: they contain password hashes (and, for full
backups, every user's files).

### The nightly database backup

Turn it on (or change its settings) by running the installer again; the location and retention
are remembered in `/etc/simplecloud.env` (`DB_BACKUP_FOLDER`, `BACKUP_KEEP`):

```bash
sudo ./install.sh --domain cloud.local --backup-timer                                   # on, defaults
sudo ./install.sh --domain cloud.local --backup-timer --backup-keep 30                  # keep 30
sudo ./install.sh --domain cloud.local --backup-timer --backup-dir /mnt/nas/simplecloud # on a NAS
```

It runs at 03:00 (within 15 minutes; a run missed while the server was off happens at the next
boot) and keeps the newest 14 database backups. Retention only ever deletes files named
`simplecloud-db-*.tar.gz` in that folder: full backups and anything else there are never touched.

**On a NAS or another disk:** mount it first and make it permanent in `/etc/fstab`; the backup
waits for the mount (`RequiresMountsFor`). The `simplecloud` user must be able to write there; the
installer checks this and stops with a message if not. For a CIFS/SMB share, mount it with that
user as owner, for example:

```
//nas.local/backups  /mnt/nas  cifs  credentials=/root/.nas-cred,uid=simplecloud,gid=simplecloud,file_mode=0600,dir_mode=0700,_netdev  0  0
```

Check and control the timer:

```bash
systemctl list-timers simplecloud-backup                  # next run
journalctl -u simplecloud-backup -n 20                    # last results
sudo systemctl start simplecloud-backup                   # run one now
sudo systemctl disable --now simplecloud-backup.timer     # turn the nightly backup off
sudo systemctl enable --now simplecloud-backup.timer      # turn it back on
```

Re-running the installer without `--backup-timer` keeps a disabled timer disabled.

### From the admin dashboard

**Admin → System and storage** shows the database backups (newest first, with date and size),
the retention setting, the time of the last backup with a warning when it is older than 26 hours
(`BACKUP_MAX_AGE_HOURS`) or missing, and whether the nightly timer is enabled. **Back up now**
runs the same database-only backup with the same retention, in the background (one at a time).

The app itself writes these backups, so its service must be allowed to write the backup folder.
The installer grants that (`ReadWritePaths`) for `DB_BACKUP_FOLDER`; if the folder is not writable
(for example a NAS share mounted with the wrong owner), the dashboard says so and the button is
disabled.

### By hand

```bash
cd /opt/simplecloud
# database only, into DB_BACKUP_FOLDER
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app backup --db-only
# full backup with all uploads (needs free space for a copy of all files), into BACKUP_FOLDER
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app backup
```

Both accept `--output-dir PATH` and `--keep N` (the latter only deletes older archives of the same
kind in that folder).

## 6. Restore

Both procedures keep the current data next to it (renamed), so nothing is lost if you picked the
wrong archive. Set these first:

```bash
D=/var/lib/simplecloud
NOW=$(date +%Y%m%d-%H%M%S)
sudo systemctl stop simplecloud simplecloud-backup.timer
```

### Restore the database (from a `simplecloud-db-*` archive)

Use this when the database is damaged or someone made changes you need to undo (deleted users,
folders or share links). The uploaded files stay where they are.

```bash
ARCHIVE=/var/lib/simplecloud/backups/simplecloud-db-20261120-030000.tar.gz   # the one to restore

sudo mkdir "$D/before-restore-$NOW"
sudo mv "$D"/database.db* "$D/before-restore-$NOW/"          # current database, set aside
sudo tar -xzf "$ARCHIVE" -C "$D" database.db
sudo chown simplecloud:simplecloud "$D/database.db"
```

What to expect: the database goes back to the night of the backup.
- Files **uploaded after** the backup are still on disk but no longer listed (the database does not
  know them). Users upload them again, or restore a newer backup.
- Files **deleted after** the backup are listed again but cannot be downloaded (their data is gone),
  unless you also restore them from your copy of the uploads folder. Users can delete those entries.
- Passwords, users, share links and quotas are as they were that night.

### Restore everything (from a full `simplecloud-*` archive)

```bash
ARCHIVE=/var/lib/simplecloud/backups/simplecloud-20261120-120000.tar.gz   # a full backup

sudo mkdir "$D/before-restore-$NOW" "$D/restore-$NOW"
sudo mv "$D/uploads" "$D"/database.db* "$D/before-restore-$NOW/"
sudo tar -xzf "$ARCHIVE" -C "$D/restore-$NOW"
sudo mv "$D/restore-$NOW/database.db" "$D/restore-$NOW/uploads" "$D/"
sudo rm -r "$D/restore-$NOW"                                   # only the leftover manifest.json
sudo chown -R simplecloud:simplecloud "$D"
```

### Then, in both cases

```bash
cd /opt/simplecloud
# brings an older backup up to the installed version (does nothing if it is current)
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app migrate
sudo systemctl start simplecloud
sudo systemctl start simplecloud-backup.timer     # only if you use the nightly backup
```

Check that you can sign in and open some files. When you are sure, remove
`/var/lib/simplecloud/before-restore-*` yourself.

On a **new server**, run the installer first, then follow the same steps (for a database-only
restore, copy the uploads folder over from your copy first). Copy the old `SECRET_KEY` line from the
old `/etc/simplecloud.env` if you want existing "remember me" sign-ins to stay valid (otherwise
everyone just signs in again).

## 7. Upgrading

```bash
cd ~/Simple-Cloud && git pull          # the copy you installed from
sudo ./install.sh --domain cloud.local --backup-timer
```

The installer pulls the newest `main` into `/opt/simplecloud`, updates the Python packages,
upgrades the database and restarts the service. Without `--data-dir` or `--domain` it keeps the data
directory (from `DATABASE=` in `/etc/simplecloud.env`) and the domain (from the Nginx site) that the
install already uses, so `sudo ./install.sh` alone is enough for an upgrade. Before changing the database schema it writes a
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
docker compose exec app flask --app app backup --db-only   # archives land in the data volume, /data/backups
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
