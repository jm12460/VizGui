"""Phone remote: a token-gated LAN web server serving the mobile page + a small
JSON API. Reads (status/browse) run on the HTTP thread; writes are marshaled to
the Qt main thread via ``MainWindow.web_bridge`` (see ui.py).
"""
import json
import os
import secrets
import threading
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from PySide6.QtCore import QObject, Signal

from . import config
from .paths import VIDEO_GLOBS
from .sources import source_path
from .chromecast import scan_cast_devices

with open(os.path.join(os.path.dirname(__file__), "remote.html"), encoding="utf-8") as _f:
    MOBILE_HTML = _f.read()


class _WebHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    @property
    def win(self):
        return self.server.win

    def _authed(self):
        # header only - never accept the token in the URL (keeps it out of
        # logs/bookmarks); the page sends X-Viz-Token on every request.
        tok = self.headers.get("X-Viz-Token", "")
        want = config.CFG.get("web", {}).get("token", "")
        return bool(tok) and bool(want) and secrets.compare_digest(tok, want)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, MOBILE_HTML, "text/html; charset=utf-8")
        if not self._authed():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        if u.path == "/api/status":
            return self._json(self.win.web_status())
        if u.path == "/api/browse":
            q = urllib.parse.parse_qs(u.query)
            return self._json(web_browse(int(q.get("src", ["0"])[0]), q.get("sub", [""])[0]))
        if u.path == "/api/search":
            q = urllib.parse.parse_qs(u.query)
            return self._json(web_search(int(q.get("src", ["0"])[0]), q.get("q", [""])[0]))
        if u.path == "/api/scan":
            return self._json({"ok": True, "devices": scan_cast_devices()})
        if u.path == "/api/history":
            return self._json({"ok": True, "items": self.win.web_history()})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/pair":
            try:
                pin = str(self._body().get("pin", ""))
            except Exception:
                return self._json({"ok": False}, 400)
            return self._json(self.win.web_pair(pin))
        if u.path != "/api/cmd":
            return self._send(404, "not found", "text/plain")
        if not self._authed():
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            body = self._body()
        except Exception:
            return self._json({"ok": False, "error": "bad body"}, 400)
        self.win.web_bridge.do.emit(body.get("kind", ""), body)
        return self._json({"ok": True})


def web_browse(src_idx, sub):
    """List a source's subfolder (confined within the source root). Runs on the
    HTTP thread - filesystem only, no Qt."""
    srcs = config.CFG.get("sources", [])
    if not (0 <= src_idx < len(srcs)):
        return {"ok": False, "error": "no such source"}
    try:
        base = source_path(srcs[src_idx])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    real_base = os.path.realpath(base)
    tgt = os.path.realpath(os.path.join(real_base, sub))
    if not (tgt == real_base or tgt.startswith(real_base + os.sep)):
        return {"ok": False, "error": "path outside source"}
    exts = tuple(g[1:] for g in VIDEO_GLOBS)  # (".mp4", ...)
    try:
        names = sorted(os.listdir(tgt), key=str.lower)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    entries = []
    for nm in names:
        if nm.startswith("."):
            continue
        full = os.path.join(tgt, nm)
        rel = os.path.relpath(full, real_base)
        if os.path.isdir(full):
            entries.append({"name": nm, "dir": True, "rel": rel, "path": full})
        elif nm.lower().endswith(exts):
            entries.append({"name": nm, "dir": False, "rel": rel, "path": full})
    up = None if tgt == real_base else os.path.relpath(os.path.dirname(tgt), real_base)
    if up == ".":
        up = ""
    return {"ok": True, "src": src_idx, "sub": sub, "up": up, "entries": entries}


def web_search(src_idx, query, limit=300):
    """Recursive video-file name search within one source root (confined to it).
    Case-insensitive substring match on the filename. Returns a flat list so the
    phone/desktop can show matches without clicking through folders. Filesystem
    only (no Qt), so it's safe on the HTTP thread or a worker thread."""
    srcs = config.CFG.get("sources", [])
    if not (0 <= src_idx < len(srcs)):
        return {"ok": False, "error": "no such source"}
    q = (query or "").strip().lower()
    if not q:
        return {"ok": True, "src": src_idx, "query": "", "entries": []}
    try:
        base = source_path(srcs[src_idx])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    real_base = os.path.realpath(base)
    exts = tuple(g[1:] for g in VIDEO_GLOBS)
    out = []
    for root, dirs, files in os.walk(real_base):   # followlinks=False -> no loops
        dirs[:] = sorted((d for d in dirs if not d.startswith(".")), key=str.lower)
        for nm in sorted(files, key=str.lower):
            if nm.startswith(".") or not nm.lower().endswith(exts):
                continue
            if q in nm.lower():
                full = os.path.join(root, nm)
                rel = os.path.relpath(full, real_base)
                out.append({"name": nm, "rel": rel, "path": full,
                            "folder": os.path.dirname(rel)})
                if len(out) >= limit:
                    return {"ok": True, "src": src_idx, "query": query,
                            "entries": out, "truncated": True}
    return {"ok": True, "src": src_idx, "query": query, "entries": out}


def web_path_ok(path):
    """True if `path` is a real file inside one of the configured source roots.
    A paired phone can POST any path to the cast action, so confine it the same
    way web_browse confines listing - a client must not cast arbitrary local files.
    SMB roots that can't be resolved (unmounted/offline) simply don't match."""
    if not path:
        return False
    tgt = os.path.realpath(path)
    if not os.path.isfile(tgt):
        return False
    for s in config.CFG.get("sources", []):
        try:
            root = os.path.realpath(source_path(s))
        except Exception:
            continue
        if root and (tgt == root or tgt.startswith(root + os.sep)):
            return True
    return False


class WebBridge(QObject):
    do = Signal(str, object)


def start_web_server(win, port):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), _WebHandler)
    httpd.win = win
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
