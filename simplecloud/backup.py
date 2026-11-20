"""`flask backup`: one timestamped .tar.gz with a consistent SQLite snapshot and the uploads.

Layout of the archive:
    database.db      snapshot taken with the SQLite online backup API (safe while the app runs)
    uploads/...      stored files (in-progress *.part uploads are skipped)
    manifest.json    version info and counts

The database is snapshotted first, then the files are added. A file deleted in the seconds
between the two is reported as missing; files uploaded meanwhile are included but unreferenced
(harmless). For a perfectly still copy, stop the service while the backup runs.
"""
import datetime
import glob
import io
import json
import os
import sqlite3
import tarfile
import tempfile

from .db import schema_version

PREFIX = "simplecloud-"
SUFFIX = ".tar.gz"


def _snapshot(db_path, dest):
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        return schema_version(dst)
    finally:
        dst.close()
        src.close()


def create(cfg, out_dir=None, keep=None, echo=print):
    """Write a backup archive and return its path. keep=N removes all but the N newest archives."""
    out_dir = out_dir or cfg["BACKUP_FOLDER"]
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    final = os.path.join(out_dir, f"{PREFIX}{stamp}{SUFFIX}")
    n = 1
    while os.path.exists(final):
        final = os.path.join(out_dir, f"{PREFIX}{stamp}-{n}{SUFFIX}")
        n += 1
    partial = final + ".part"  # renamed only when complete, so a crash never leaves a half archive
    upload_root = cfg["UPLOAD_FOLDER"]
    files = missing = size = 0
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        snap = os.path.join(tmp, "database.db")
        version = _snapshot(cfg["DATABASE"], snap)
        try:
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(snap, arcname="database.db")
                for dirpath, dirnames, names in os.walk(upload_root):
                    dirnames.sort()
                    for name in sorted(names):
                        if name.endswith(".part"):
                            continue
                        path = os.path.join(dirpath, name)
                        rel = os.path.relpath(path, upload_root).replace(os.sep, "/")
                        try:
                            st = os.stat(path)
                            tar.add(path, arcname=f"uploads/{rel}", recursive=False)
                        except FileNotFoundError:
                            missing += 1
                            continue
                        files += 1
                        size += st.st_size
                manifest = json.dumps({"created": stamp, "schema_version": version, "files": files,
                                       "bytes": size, "missing": missing}, indent=2).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size = len(manifest)
                info.mtime = int(datetime.datetime.now().timestamp())
                tar.addfile(info, io.BytesIO(manifest))
        except BaseException:
            if os.path.exists(partial):
                os.remove(partial)
            raise
    os.replace(partial, final)
    os.chmod(final, 0o600)  # contains password hashes and every user's files
    echo(f"Backup written: {final} ({files} files, {size / 1048576:.1f} MB"
         + (f", {missing} deleted during the backup" if missing else "") + ")")
    if keep:
        for old in sorted(glob.glob(os.path.join(out_dir, f"{PREFIX}*{SUFFIX}")), key=os.path.getmtime)[:-keep]:
            os.remove(old)
            echo(f"Removed old backup: {old}")
    return final
