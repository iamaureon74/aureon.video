"""local web interface. binds to 127.0.0.1 only; every api call needs the per-launch
token, and the host header is checked so other websites cannot reach it (dns rebinding)."""
from __future__ import annotations

import json
import mimetypes
import secrets
import shutil
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import pipeline
from .overlay import FONT_DIR

WEB = Path(__file__).parent / "web"
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
MAX_UPLOAD = 20 * 1024 ** 3
STAGES = ["transcribing", "cleaning audio", "placing transcript", "rendering", "verifying"]


class Job:
    def __init__(self, root: Path, ext: str):
        self.id = uuid.uuid4().hex
        self.dir = root / self.id
        self.dir.mkdir(parents=True)
        self.input = self.dir / f"input{ext}"
        self.state = "uploaded"
        self.stage = None
        self.done_stages: list[str] = []
        self.scan = None
        self.report = None
        self.error = None
        self.output: Path | None = None

    def public(self):
        return {"id": self.id, "state": self.state, "stage": self.stage,
                "done_stages": self.done_stages, "stages": STAGES, "scan": self.scan,
                "report": self.report, "error": self.error}


class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.root = Path(tempfile.mkdtemp(prefix="aureon-app-"))
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.last_ping: float | None = None   # set by the open browser tab

    def busy(self) -> bool:
        return any(j.state in ("scanning", "processing") for j in self.jobs.values())

    def new_job(self, ext):
        with self.lock:
            for j in self.jobs.values():           # one video at a time; drop old data
                shutil.rmtree(j.dir, ignore_errors=True)
            self.jobs.clear()
            j = Job(self.root, ext)
            self.jobs[j.id] = j
            return j

    def run_scan(self, j: Job):
        try:
            j.state = "scanning"
            j.scan = pipeline.scan(j.input)
            j.state = "scanned"
        except Exception as e:  # surfaced to the ui, never swallowed
            j.state, j.error = "error", str(e)[-600:]

    def run_clean(self, j: Job, opts: dict):
        def log(stage):
            if j.stage and j.stage != "scanning":
                j.done_stages.append(j.stage)
            j.stage = stage

        try:
            j.state, j.done_stages = "processing", []
            out = j.dir / "out"
            r = pipeline.clean(
                j.input, out, audio_mode=opts.get("audio", "standard"),
                overlay_on=bool(opts.get("overlay", True)),
                text_color=opts.get("color", "golden-brown"),
                platform=opts.get("platform", "auto"), log=log,
            )
            if j.stage:
                j.done_stages.append(j.stage)
            j.stage = None
            j.report = r
            j.output = out / r["output"]
            j.state = "done"
        except Exception as e:
            j.state, j.error = "error", str(e)[-600:]

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def make_handler(app: App, port_ref: list[int]):
    class H(BaseHTTPRequestHandler):
        server_version = "aureon"
        sys_version = ""

        def log_message(self, *a):  # no request logs (they would contain file names)
            pass

        # ---------------------------------------------------------- guards
        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            return host in {f"127.0.0.1:{port_ref[0]}", f"localhost:{port_ref[0]}"}

        def _auth(self):
            return secrets.compare_digest(self.headers.get("X-Aureon-Token", ""), app.token)

        def _send(self, code, body=b"", ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode())

        def _job(self, jid):
            j = app.jobs.get(jid)
            if not j:
                self._json({"error": "no such job"}, 404)
            return j

        # ---------------------------------------------------------- routes
        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"")
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            if u.path in ("/", "/index.html"):
                html = (WEB / "index.html").read_text(encoding="utf-8")
                html = html.replace("__AUREON_TOKEN__", app.token)
                csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                       "media-src 'self' blob:; font-src 'self'; connect-src 'self'; "
                       "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                return self._send(200, html.encode(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"})
            if len(parts) == 2 and parts[0] == "static":
                f = WEB / parts[1]
                if parts[1] in {"icon.svg"} and f.exists():
                    return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0])
            if len(parts) == 2 and parts[0] == "fonts" and parts[1].endswith(".otf"):
                f = FONT_DIR / Path(parts[1]).name
                if f.exists():
                    return self._send(200, f.read_bytes(), "font/otf")
            if len(parts) >= 3 and parts[:2] == ["api", "job"]:
                # media elements cannot send headers, so downloads take the token as a query
                q = parse_qs(u.query)
                tok = q.get("t", [""])[0]
                if not (self._auth() or secrets.compare_digest(tok, app.token)):
                    return self._json({"error": "forbidden"}, 403)
                j = self._job(parts[2])
                if not j:
                    return
                if len(parts) == 3:
                    return self._json(j.public())
                if parts[3] == "video" and j.output and j.output.exists():
                    return self._file(j.output, q.get("dl", ["0"])[0] == "1")
                if parts[3] == "report" and j.report:
                    p = j.output.parent / (j.output.stem + ".report.md")
                    return self._send(200, p.read_bytes(), "text/markdown; charset=utf-8",
                                      {"Content-Disposition":
                                       f'attachment; filename="{p.name}"'})
            return self._send(404, b"")

        def _file(self, path: Path, download: bool):
            size = path.stat().st_size
            rng = self.headers.get("Range")
            start, end = 0, size - 1
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                end = min(end, size - 1)
            length = end - start + 1
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_POST(self):
            if not self._host_ok():
                return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"")
            if not self._auth():
                return self._json({"error": "forbidden"}, 403)
            u = urlparse(self.path)
            parts = [p for p in u.path.split("/") if p]
            if parts == ["api", "ping"]:
                import time as _t
                app.last_ping = _t.monotonic()
                return self._json({"ok": True})
            if parts == ["api", "upload"]:
                name = parse_qs(u.query).get("name", ["video.mp4"])[0]
                ext = Path(name).suffix.lower()
                if ext not in ALLOWED_EXT:
                    return self._json({"error": f"unsupported file type {ext or '(none)'}"}, 415)
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > MAX_UPLOAD:
                    return self._json({"error": "file is empty or too large"}, 413)
                j = app.new_job(ext)
                with j.input.open("wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            break
                        f.write(chunk)
                        left -= len(chunk)
                if left:
                    return self._json({"error": "upload interrupted"}, 400)
                threading.Thread(target=app.run_scan, args=(j,), daemon=True).start()
                return self._json({"id": j.id})
            if len(parts) == 4 and parts[:2] == ["api", "job"] and parts[3] == "clean":
                j = self._job(parts[2])
                if not j:
                    return
                if j.state != "scanned":
                    return self._json({"error": f"job is {j.state}"}, 409)
                n = int(self.headers.get("Content-Length") or 0)
                opts = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
                clean = {
                    "audio": opts.get("audio") if opts.get("audio") in ("standard", "warp", "voice") else "standard",
                    "overlay": bool(opts.get("overlay", True)),
                    "color": opts.get("color") if opts.get("color") in ("golden-brown", "auto") else "golden-brown",
                    "platform": opts.get("platform") if opts.get("platform") in
                    ("auto", "none", "youtube", "tiktok", "reels", "shorts") else "auto",
                }
                j.state = "processing"
                threading.Thread(target=app.run_clean, args=(j, clean), daemon=True).start()
                return self._json({"ok": True})
            return self._send(404, b"")

    return H


IDLE_QUIT_S = 150  # browsers may slow a background tab's timers to ~1/min


def serve(port: int = 0, open_browser: bool = True, idle_quit: bool = True):
    """run the local interface. with idle_quit, the app exits by itself a little while
    after its browser tab is closed (no ping) and no video is being processed, so the
    windows build needs no console window to quit from."""
    import time

    app = App()
    port_ref = [0]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port_ref[0] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}/"
    print(f"aureon is running at {url}  (ctrl+c to quit)")

    def watchdog():
        started = time.monotonic()
        while True:
            time.sleep(5)
            last = app.last_ping
            if app.busy():
                continue
            if last is None:
                # browser never connected (blocked or closed instantly): give it 5 min
                if time.monotonic() - started > 300:
                    break
            elif time.monotonic() - last > IDLE_QUIT_S:
                break
        httpd.shutdown()

    if idle_quit:
        threading.Thread(target=watchdog, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.close()
