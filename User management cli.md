# Simple Cloud: command-line tool

Most user management is also available in the web interface (**Admin**, for admins). The
command line is for the server's shell: first setup, recovery, scripting.

## Running a command

On a server installed with `install.sh`, run commands as the service user, from the code
directory, pointing at the config file:

```bash
cd /opt/simplecloud
sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app <command> [options]
```

To save typing, define a shell alias once:

```bash
alias sc='cd /opt/simplecloud && sudo -u simplecloud SIMPLECLOUD_ENV_FILE=/etc/simplecloud.env venv/bin/flask --app app'
```

Then: `sc listusers`. With Docker: `docker compose exec app flask --app app <command>`.

Every command prints its options with `--help`. Anything not given as an option is asked for.
**Passwords** are asked for twice with hidden input; for scripts, pass them in the
`SIMPLECLOUD_PASSWORD` environment variable instead (avoid `--password` on the command line,
which ends up in the shell history).

## Commands

| Command | What it does |
|---|---|
| `adduser --username NAME [--admin] [--quota MB]` | Create a user. |
| `changepassword --username NAME [--force-change]` | Set a new password. Signs the user out everywhere. `--force-change` makes them choose a new one at next sign-in. |
| `setadmin --username NAME [--no-admin]` | Give (or with `--no-admin` remove) the admin role. The last admin cannot be removed. |
| `disable2fa --username NAME` | Turn off two-factor sign-in for someone who lost their phone. |
| `changeusername --old NAME --new NAME` | Rename a user. Their files stay theirs. |
| `deleteuser --username NAME [--files transfer --to OTHER \| --files delete] [--yes]` | Delete a user. A user who owns files needs `--files`: transfer them to another user, or delete them permanently. Asks for confirmation unless `--yes`. |
| `listusers` | List users with id, admin and disabled flags, file count and bytes used. |
| `backup --db-only [--output-dir DIR] [--keep N]` | Write `simplecloud-db-YYYYmmdd-HHMMSS.tar.gz` with only a database snapshot, into `DB_BACKUP_FOLDER`. This is what the nightly timer runs. |
| `backup [--output-dir DIR] [--keep N]` | Full backup: `simplecloud-YYYYmmdd-HHMMSS.tar.gz` with the database snapshot and all uploads, into `BACKUP_FOLDER`. Manual only. |
| `migrate [--dry-run]` | Bring the database up to date after an upgrade. Takes a backup first, is resumable, and `--dry-run` only prints the plan. The installer runs it for you. |

For both kinds of backup, `--keep N` deletes all but the newest N archives **of that kind** in
the folder; other files there are never touched. Both are safe while the app runs.

## Examples

```bash
# a new colleague with 20 GB of space
sc adduser --username sara --quota 20480

# an admin forgot their password and is locked out
sc changepassword --username admin --force-change

# someone lost the phone with their authenticator app
sc disable2fa --username sara

# someone leaves: hand their files to a colleague
sc deleteuser --username sara --files transfer --to reza

# scripted user creation
SIMPLECLOUD_PASSWORD='temporary-pass-123' sc adduser --username guest
```

## First admin

On the very first start an `admin` account is created with a random password, printed once
in the installer output (look for `Initial admin`). It must be changed at first sign-in. If you
missed it, set a new one with `changepassword --username admin --force-change`.

All commands write a line to the log (`journalctl -u simplecloud` for web actions; the
terminal for CLI runs), for example `event=cli_adduser user=sara admin=False`.
