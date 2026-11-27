# Simple Cloud

A small self-hosted file cloud for a team or a home network: Flask + SQLite, plain HTML/CSS/JS,
one installer script for Ubuntu/Debian. The interface is in **Persian (default, right-to-left)
and English**.

## Features
- **Files:** folders, search across all folders, sort, grid and list views, preview of images,
  video, audio, PDF and text, thumbnails for PDF, video and music covers, rename, move, select
  several items to download as one ZIP or delete.
- **Trash:** deleted files wait 30 days (`TRASH_DAYS`) before they are removed, and can be restored.
  They keep counting toward the quota until they leave the trash.
- **Upload:** drag and drop, many files at once, into a folder you choose, chunked and resumable
  (a dropped connection or re-selecting the same file continues where it stopped).
- **Upload links:** others upload into one of your folders, with optional expiry, password, file
  limit and required sign-in (then each file shows who uploaded it).
- **Fetch from URL:** the server downloads files in the background with progress; private and
  internal addresses are refused (SSRF protection, with correct TLS checks for HTTPS sources).
- **Share links** for files and folders: optional expiry, password, download limit and required
  sign-in; online viewing that counts as a download; a folder can be downloaded as a ZIP; revocable.
- **Accounts:** per-user storage quota, optional two-factor sign-in (TOTP authenticator apps),
  change-password page, an admin panel for users, and a command-line tool.
- **Admin dashboard:** disk usage (cloud files, other data, free), storage per user, a warning when
  the quotas add up to more than the disk can hold or a user is unlimited, the largest files,
  database backups with a "Back up now" button, an activity log filterable by user, action,
  dates and text.
- **Security:** every user sees only their own files; math captcha and login lockout shared by all
  workers; CSRF protection; strict Content-Security-Policy; uploads stored under random names.
- **Interface:** mobile-first, light and dark mode, keyboard and screen-reader friendly (WCAG AA
  contrast), fonts bundled locally (Vazirmatn, Fira Sans), so it works on a network without internet.
- **Operations:** a nightly database backup (optional systemd timer, can write to a NAS), a
  manual full backup with all uploads, safe resumable database migrations, structured logs.

It is designed to run on a LAN over **plain HTTP** (for example `http://cloud.local`). Nothing in
the app requires HTTPS.

## Install
On a fresh Ubuntu Server 24.04:

```bash
git clone https://github.com/Amirabbasjadidi/Simple-Cloud.git
cd Simple-Cloud
sudo ./install.sh --domain cloud.local --backup-timer
```

The full guide (DNS record on a MikroTik router, the first admin password, backup, restore and
upgrading) is in [How to install.md](./How%20to%20install.md).

## Managing users
In the web interface: **Admin** (visible to admins). From the server's shell: see
[User management cli.md](./User%20management%20cli.md).

## Configuration
Settings are read from `/etc/simplecloud.env` (created by the installer), and environment
variables override them. The most useful ones:

| Setting | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | (generated) | Signs sessions. Keep it secret; changing it signs everyone out. |
| `DATABASE`, `UPLOAD_FOLDER`, `BACKUP_FOLDER` | `/var/lib/simplecloud/...` | Data locations. |
| `DB_BACKUP_FOLDER` | `BACKUP_FOLDER` | Where database backups go (installer: `--backup-dir`). |
| `DEFAULT_LANG` | `fa` | Interface language until a user picks one (`fa` or `en`). |
| `MAX_CONTENT_LENGTH` | 2 GiB | Largest single request (Nginx is sized from it). |
| `MAX_FILE_SIZE` | 10 GiB | Largest stored file (chunked uploads and URL fetches). |
| `DEFAULT_QUOTA_BYTES` | 0 | Quota for new users; 0 = unlimited. |
| `MIN_PASSWORD_LENGTH` | 8 | |
| `BACKUP_KEEP` | 14 | Database backups kept (installer: `--backup-keep`). |
| `BACKUP_MAX_AGE_HOURS`, `LOW_DISK_PERCENT` | 26, 10 | Dashboard warnings: newest database backup older than this; less free disk than this. |
| `STATS_CACHE_SECONDS` | 60 | How long the dashboard's storage figures are cached. |
| `TRASH_DAYS` | 30 | Days a deleted file stays in the trash before it is removed for good. |
| `EVENTS_KEEP_DAYS` | 180 | Days the admin activity log keeps an event. |
| `SHARE_GRANT_SECONDS` | 43200 (12 h) | After a share-link download is counted, that browser or download manager may resume, retry and fetch in parallel segments for this long without using up the download limit. |
| `LOGIN_MAX_FAILURES_USER` / `_IP`, `LOGIN_LOCKOUT_SECONDS` | 5 / 20, 900 | Login lockout. |
| `COOKIE_SECURE` | 0 | Keep 0 on plain HTTP. Set 1 only if the site is served over HTTPS. |
| `PROXY_COUNT` | 1 (installer) | Number of reverse proxies in front of the app. |

After changing the file: `sudo systemctl restart simplecloud`.

## Development
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
SECRET_KEY=dev DATABASE=dev-data/database.db UPLOAD_FOLDER=dev-data/uploads flask --app app run
pytest
```
Translations: English strings are the keys; Persian lives in `simplecloud/translations/fa.json`.
A test fails if a string in the templates or scripts has no Persian translation.

## License
GNU General Public License v3.0 (GPLv3), see [LICENSE](./LICENSE). Bundled third-party files:
Vazirmatn and Fira Sans (SIL Open Font License 1.1, `static/fonts/*/OFL.txt`), qrcode-generator
(MIT, `static/vendor/qrcode-generator.LICENSE`).

## Contact
Open a GitHub issue, or see [amirabbasjadidi.ir](https://amirabbasjadidi.ir/).
