import os
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from simplecloud import fetch
from simplecloud.fetch import FetchError, fetch_to_file, is_public_ip

HOSTS = {
    "public.test": ["127.0.0.1"],
    "pinned.test": ["127.0.0.1"],
    "wrongname.test": ["127.0.0.1"],
    "internal.test": ["10.0.0.5"],
    "mixed.test": ["93.184.216.34", "10.0.0.1"],
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        self.server.seen_hosts.append(self.headers.get("Host"))
        if p.startswith("/redir/"):
            self.send_response(302)
            self.send_header("Location", p[len("/redir/"):].replace("__", "://", 1))
            self.end_headers()
            return
        body = b"x" * 5000 if p == "/big" else b"hello"
        self.send_response(200)
        if p == "/cd":
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''%DA%AF%D8%B2%D8%A7%D8%B1%D8%B4.pdf")
        if p != "/nolength":
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(tls_ctx=None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.seen_hosts = []
    if tls_ctx:
        srv.socket = tls_ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def http_srv():
    srv = _serve()
    yield srv
    srv.shutdown()


@pytest.fixture
def https_srv(cert_dir):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(cert_dir, "test-cert.pem"), os.path.join(cert_dir, "test-key.pem"))
    snis = []
    ctx.sni_callback = lambda sock, name, c: snis.append(name)
    srv = _serve(ctx)
    srv.snis = snis
    yield srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    real = socket.getaddrinfo
    real_check = fetch.is_public_ip

    def stub(host, port):
        if host in HOSTS:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in HOSTS[host]]
        return real(host, port, type=socket.SOCK_STREAM)

    monkeypatch.setattr(fetch, "_getaddrinfo", stub)
    # Treat exactly 127.0.0.1 as "public" so the local test servers are reachable.
    monkeypatch.setattr(fetch, "is_public_ip", lambda ip: ip == "127.0.0.1" or real_check(ip))


@pytest.fixture
def cfg(cert_dir):
    return {
        "FETCH_MAX_BYTES": 1024 * 1024, "FETCH_CONNECT_TIMEOUT": 5, "FETCH_READ_TIMEOUT": 5,
        "FETCH_TOTAL_TIMEOUT": 30, "FETCH_MAX_REDIRECTS": 3,
        "FETCH_CA_BUNDLE": os.path.join(cert_dir, "test-cert.pem"),
    }


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                                "0.0.0.0", "224.0.0.1", "239.255.255.250", "240.0.0.1", "100.64.0.1",
                                "::1", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                                "2002:7f00:1::1"])
def test_non_public_ips_rejected(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ips_allowed(ip):
    assert is_public_ip(ip)


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "gopher://x", "http://u:p@public.test/"])
def test_bad_schemes_and_creds(url, cfg, tmp_path):
    with pytest.raises(FetchError):
        fetch_to_file(url, str(tmp_path / "f"), cfg)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://10.0.0.1/",
                                 "http://internal.test/", "http://mixed.test/", "http://[::1]/",
                                 "http://127.0.0.2/"])
def test_private_destinations_rejected(url, cfg, tmp_path):
    with pytest.raises(FetchError, match="not allowed"):
        fetch_to_file(url, str(tmp_path / "f"), cfg)
    assert not (tmp_path / "f").exists()


def test_http_fetch_with_pinning(http_srv, cfg, tmp_path):
    port = http_srv.server_address[1]
    dest = tmp_path / "f"
    name, size, _ = fetch_to_file(f"http://public.test:{port}/files/report.txt", str(dest), cfg)
    assert (name, size, dest.read_bytes()) == ("report.txt", 5, b"hello")
    assert http_srv.seen_hosts[-1] == f"public.test:{port}"  # Host header keeps the original name


@pytest.mark.parametrize("target", ["http__169.254.169.254/x", "http__internal.test/", "http__127.0.0.2/"])
def test_redirect_to_private_rejected(http_srv, cfg, tmp_path, target):
    port = http_srv.server_address[1]
    with pytest.raises(FetchError, match="not allowed"):
        fetch_to_file(f"http://public.test:{port}/redir/{target}", str(tmp_path / "f"), cfg)


def test_redirect_to_public_followed(http_srv, cfg, tmp_path):
    port = http_srv.server_address[1]
    name, size, _ = fetch_to_file(f"http://public.test:{port}/redir//ok.bin", str(tmp_path / "f"), cfg)
    assert (name, size) == ("ok.bin", 5)


def test_redirect_loop_limited(http_srv, cfg, tmp_path):
    port = http_srv.server_address[1]
    url = f"http://public.test:{port}/redir/" * 1 + f"http__public.test:{port}/redir/" * 5 + "x"
    with pytest.raises(FetchError, match="redirects"):
        fetch_to_file(url, str(tmp_path / "f"), cfg)


@pytest.mark.parametrize("path", ["/big", "/nolength"])
def test_max_size_aborts_and_deletes(http_srv, cfg, tmp_path, path):
    cfg["FETCH_MAX_BYTES"] = 4 if path == "/nolength" else 1000
    port = http_srv.server_address[1]
    dest = tmp_path / "f"
    with pytest.raises(FetchError, match="larger"):
        fetch_to_file(f"http://public.test:{port}{path}", str(dest), cfg)
    assert not dest.exists()


def test_content_disposition_unicode_and_empty_name(http_srv, cfg, tmp_path):
    port = http_srv.server_address[1]
    name, _, _ = fetch_to_file(f"http://public.test:{port}/cd", str(tmp_path / "a"), cfg)
    assert name == "گزارش.pdf"
    name, _, _ = fetch_to_file(f"http://public.test:{port}/dir/", str(tmp_path / "b"), cfg)
    assert name == ""


def test_https_pinned_uses_sni_and_verifies_hostname(https_srv, cfg, tmp_path):
    port = https_srv.server_address[1]
    name, size, _ = fetch_to_file(f"https://pinned.test:{port}/a.txt", str(tmp_path / "f"), cfg)
    assert (name, size) == ("a.txt", 5)
    assert https_srv.snis[-1] == "pinned.test"  # SNI is the hostname, not the pinned IP


def test_https_wrong_hostname_fails_verification(https_srv, cfg, tmp_path):
    port = https_srv.server_address[1]
    with pytest.raises(FetchError, match="TLS"):
        fetch_to_file(f"https://wrongname.test:{port}/a.txt", str(tmp_path / "f"), cfg)


def test_https_untrusted_ca_fails(https_srv, cfg, tmp_path):
    cfg["FETCH_CA_BUNDLE"] = None  # certifi bundle doesn't trust our self-signed cert
    port = https_srv.server_address[1]
    with pytest.raises(FetchError, match="TLS"):
        fetch_to_file(f"https://pinned.test:{port}/a.txt", str(tmp_path / "f"), cfg)


def test_fetch_job_rejects_metadata_ip(two_users):
    import time
    alice, _ = two_users
    job = alice.post("/api/fetch", json={"url": "http://169.254.169.254/latest/meta-data/"}).json["jobs"][0]
    for _ in range(100):
        job = alice.get(f"/api/fetch/{job['id']}").json["job"]
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert job["status"] == "failed" and "not allowed" in job["error"]
