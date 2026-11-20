"""SSRF-safe URL fetching.

Each hop: resolve the hostname, reject if ANY resolved address is non-public, then connect to the
checked IP directly (pinning, so DNS rebinding can't swap the address between check and connect).
For HTTPS the TLS handshake still uses the original hostname: urllib3 gets
server_hostname=<host> (SNI) and assert_hostname=<host> (certificate verification), while the
TCP connection goes to the pinned IP. Redirects are never followed automatically; each Location is
re-validated through the same path.
"""
import ipaddress
import os
import socket
import time
from email.message import Message
from urllib.parse import unquote, urljoin, urlsplit

import certifi
import urllib3

USER_AGENT = "SimpleCloud-Fetch/1.0"
REDIRECT_CODES = {301, 302, 303, 307, 308}


class FetchError(Exception):
    pass


def is_public_ip(ip):
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if addr.version == 6:
        embedded = addr.ipv4_mapped or addr.sixtofour or (addr.teredo[1] if addr.teredo else None)
        if embedded is not None and not is_public_ip(str(embedded)):
            return False
    return not (
        not addr.is_global or addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def _getaddrinfo(host, port):  # separate so tests can stub DNS
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def resolve_public(host, port):
    try:
        infos = _getaddrinfo(host, port)
    except (socket.gaierror, UnicodeError):
        raise FetchError("Could not resolve host")
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise FetchError("Could not resolve host")
    for ip in ips:
        if not is_public_ip(ip):
            raise FetchError("Destination address is not allowed")
    return ips[0]


def _filename(resp, url):
    cd = resp.headers.get("Content-Disposition")
    if cd:
        msg = Message()
        msg["content-disposition"] = cd
        name = msg.get_filename()  # handles filename*= (RFC 2231/5987)
        if name:
            return os.path.basename(name.replace("\\", "/"))
    path = urlsplit(url).path
    return "" if path.endswith("/") else unquote(path.split("/")[-1])  # "" -> caller picks a default


def _open(url, cfg):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are not allowed")
    host = parts.hostname
    if not host:
        raise FetchError("Invalid URL")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FetchError("Invalid port")

    ip = resolve_public(host, port)
    timeout = urllib3.Timeout(connect=cfg["FETCH_CONNECT_TIMEOUT"], read=cfg["FETCH_READ_TIMEOUT"])
    if parts.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            ip, port, timeout=timeout, retries=False,
            cert_reqs="CERT_REQUIRED", ca_certs=cfg.get("FETCH_CA_BUNDLE") or certifi.where(),
            server_hostname=host, assert_hostname=host,
        )
    else:
        pool = urllib3.HTTPConnectionPool(ip, port, timeout=timeout, retries=False)

    host_header = f"[{host}]" if ":" in host else host
    if parts.port:
        host_header += f":{parts.port}"
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        return pool.urlopen("GET", path, headers={"Host": host_header, "User-Agent": USER_AGENT},
                            redirect=False, preload_content=False, assert_same_host=False, retries=False)
    except urllib3.exceptions.SSLError as e:
        raise FetchError(f"TLS error: {e}")
    except urllib3.exceptions.HTTPError as e:
        raise FetchError(f"Connection failed: {e.__class__.__name__}")


def fetch_to_file(url, dest_path, cfg, progress=None):
    """Download url into dest_path. Returns (filename_hint, size, content_type).

    Deletes dest_path on any failure. `progress(done, total_or_None)` is called while streaming.
    """
    max_bytes = cfg["FETCH_MAX_BYTES"]
    deadline = time.monotonic() + cfg["FETCH_TOTAL_TIMEOUT"]
    for _ in range(cfg["FETCH_MAX_REDIRECTS"] + 1):
        resp = _open(url, cfg)
        if resp.status in REDIRECT_CODES and resp.headers.get("Location"):
            resp.drain_conn()
            resp.release_conn()
            url = urljoin(url, resp.headers["Location"])
            continue
        break
    else:
        raise FetchError("Too many redirects")

    try:
        if resp.status != 200:
            raise FetchError(f"HTTP {resp.status}")
        total = resp.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else None
        if total is not None and total > max_bytes:
            raise FetchError("File is larger than the allowed maximum")
        done = 0
        try:
            with open(dest_path, "wb") as f:
                for chunk in resp.stream(64 * 1024, decode_content=True):
                    done += len(chunk)
                    if done > max_bytes:
                        raise FetchError("File is larger than the allowed maximum")
                    if time.monotonic() > deadline:
                        raise FetchError("Download took too long")
                    f.write(chunk)
                    if progress:
                        progress(done, total)
        except (urllib3.exceptions.HTTPError, OSError) as e:
            raise FetchError(f"Download failed: {e.__class__.__name__}")
        return _filename(resp, url), done, resp.headers.get("Content-Type", "")
    except BaseException:
        try:
            os.remove(dest_path)
        except FileNotFoundError:
            pass
        raise
    finally:
        resp.release_conn()
