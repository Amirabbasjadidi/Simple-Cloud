# Simple Cloud

A small self-hosted file cloud for a team or a home network: Flask + SQLite, plain HTML/CSS/JS,
one installer script for Ubuntu/Debian. The interface is in **Persian (default, right-to-left)
and English**.

## Features
- **Files:** folders, search across all folders, sort, grid and list views, preview of images,
  video, audio, PDF and text, rename, move, delete.
- **Upload:** drag and drop, many files at once, chunked and resumable (a dropped connection or
  re-selecting the same file continues where it stopped).
- **Fetch from URL:** the server downloads files in the background with progress; private and
  internal addresses are refused (SSRF protection, with correct TLS checks for HTTPS sources).
- **Share links:** optional expiry, password and download limit; revocable.
- **Accounts:** per-user storage quota, optional two-factor sign-in (TOTP authenticator apps),
  change-password page, an admin panel for users, and a command-line tool.
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
