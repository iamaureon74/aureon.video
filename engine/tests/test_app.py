"""local server: guards, and auto-quit after the browser tab goes away."""
import http.client
import re
import threading
import time

from aureon import app as A


def _start(monkeypatch, idle=1):
    monkeypatch.setattr(A, "IDLE_QUIT_S", idle)
    monkeypatch.setattr(A.webbrowser, "open", lambda url: None)
    out = {}
    real = A.ThreadingHTTPServer

    class Cap(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            out["port"] = self.server_address[1]

    monkeypatch.setattr(A, "ThreadingHTTPServer", Cap)
    t = threading.Thread(target=A.serve, kwargs={"open_browser": True}, daemon=True)
    t.start()
    for _ in range(50):
        if "port" in out:
            break
        time.sleep(0.05)
    return t, out["port"]


def _req(port, method, path, host=None, token=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        h["X-Aureon-Token"] = token
    c.request(method, path, body=b"" if method == "POST" else None, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def test_guards_and_idle_quit(monkeypatch):
    t, port = _start(monkeypatch)
    status, body = _req(port, "GET", "/")
    assert status == 200
    token = re.search(rb'const TOKEN = "([^"]+)"', body).group(1).decode()
    assert _req(port, "GET", "/", host="evil.example")[0] == 421       # dns rebinding
    assert _req(port, "POST", "/api/ping")[0] == 403                    # no token
    assert _req(port, "POST", "/api/ping", token=token)[0] == 200
    t.join(timeout=15)                                                  # tab "closed"
    assert not t.is_alive(), "server did not quit after the tab stopped pinging"
