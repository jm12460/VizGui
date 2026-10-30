"""Local same-user source administration; the phone HTTP API cannot access it."""
import copy
import errno
import json
import os
import socket
import socketserver
import stat
import struct
import threading
import time

try:
    import fcntl
except ImportError:  # Windows has no local Linux peer-credential channel.
    fcntl = None

from . import config, server_identity
from .source_management import public_sources, validate_new_source

MAX_REQUEST = 65536
SOCKET_NAME = "vizgui-source-admin.sock"


def socket_path():
    if fcntl is None or not hasattr(socket, "SO_PEERCRED") or not hasattr(os, "getuid"):
        raise RuntimeError("Local source administration requires Linux")
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    info = os.lstat(runtime)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise RuntimeError("User runtime directory is not private")
    return os.path.join(runtime, SOCKET_NAME)


class SourceManager:
    """All access is serialized on the headless controller's owner thread."""
    def __init__(self, controller=None):
        self.revision = 0
        self.controller = controller

    def handle(self, request, source=None, deadline=None):
        if not isinstance(request, dict):
            return {"ok": False, "error": "Invalid request"}
        if deadline is not None and time.monotonic() >= deadline:
            return {"ok": False, "error": "Source request expired"}
        identity = server_identity.server_info()
        if request.get("server_id") != identity["id"] or request.get("instance_id") != identity["instance_id"]:
            return {"ok": False, "error": "Server connection changed"}
        action = request.get("action")
        if action == "list":
            return {"ok": True, "sources": public_sources(config.CFG["sources"]),
                    "revision": self.revision, "server": identity}
        if type(request.get("revision")) is not int or request["revision"] != self.revision:
            return {"ok": False, "error": "Sources changed; refresh and try again"}
        try:
            with open(config.CONFIG_PATH, encoding="utf-8") as f:
                disk_config = json.load(f)
        except (OSError, ValueError):
            return {"ok": False, "error": "Server configuration cannot be verified"}
        if not isinstance(disk_config, dict):
            return {"ok": False, "error": "Server configuration cannot be verified"}
        # load_config backfills these fields in memory for older installations.
        disk_config.setdefault("sources", [])
        disk_config.setdefault("targets", [])
        disk_config.setdefault("web", {"enabled": True, "port": 8950})
        disk_config.setdefault("auto_next", True)
        if disk_config != config.CFG:
            return {"ok": False, "error": "Server configuration changed on disk; no changes were saved"}
        updated = copy.deepcopy(config.CFG)
        if action == "add" and source is not None:
            updated["sources"].append(source)
        elif action == "remove":
            index = request.get("index")
            if type(index) is not int or not 0 <= index < len(updated["sources"]):
                return {"ok": False, "error": "Invalid source selection"}
            del updated["sources"][index]
        else:
            return {"ok": False, "error": "Invalid action"}
        if deadline is not None and time.monotonic() >= deadline:
            return {"ok": False, "error": "Source request expired"}
        try:
            config.save_config(updated)
        except (OSError, ValueError, TypeError):
            return {"ok": False, "error": "Could not save server configuration"}
        config.CFG = updated
        self.revision += 1
        if self.controller is not None:
            self.controller.engine._sources_revision = self.revision
        return {"ok": True, "sources": public_sources(updated["sources"]),
                "revision": self.revision, "server": identity}


def _same_user(connection):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _, uid, _ = struct.unpack("3i", credentials)
    return uid == os.getuid()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.connection.settimeout(3)
            if not _same_user(self.connection):
                return
            line = self.rfile.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                return
            request = json.loads(line)
            if not isinstance(request, dict):
                return
            source = validate_new_source(request.get("source")) if request.get("action") == "add" else None
            deadline = time.monotonic() + 10
            response = self.server.controller.call(self.server.manager.handle, request, source,
                                                   deadline, timeout=12)
        except (ValueError, TypeError):
            response = {"ok": False, "error": "Invalid source or request"}
        except TimeoutError:
            response = {"ok": False, "outcome": "uncertain",
                        "error": "Result uncertain; refresh before retrying"}
        except Exception:
            response = {"ok": False, "error": "Source administration unavailable"}
        self.wfile.write(json.dumps(response).encode() + b"\n")


class _Server(socketserver.ThreadingMixIn,
              getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = True
    allow_reuse_address = False


def start(controller):
    path = socket_path()
    lock_fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("Unsafe source administration lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            old = os.lstat(path)
        except FileNotFoundError:
            old = None
        if old is not None:
            if not stat.S_ISSOCK(old.st_mode) or old.st_uid != os.getuid():
                raise RuntimeError("Unsafe existing source administration socket")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                result = probe.connect_ex(path)
            if result != errno.ECONNREFUSED:
                raise RuntimeError("Source administration socket is already in use")
            os.unlink(path)
        server = _Server(path, _Handler)
        server.controller = controller
        server.manager = SourceManager(controller)
        os.chmod(path, 0o600)
        identity = os.lstat(path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    except Exception:
        if "server" in locals():
            server.server_close()
        os.close(lock_fd)
        raise
    return server, path, (identity.st_dev, identity.st_ino), lock_fd


def close(handle):
    server, path, identity, lock_fd = handle
    server.shutdown()
    server.server_close()
    try:
        current = os.lstat(path)
        if (current.st_dev, current.st_ino) == identity and stat.S_ISSOCK(current.st_mode):
            os.unlink(path)
    except OSError:
        pass
    finally:
        os.close(lock_fd)


def request(host, expected_server, action, **payload):
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("Source management is available only on the server machine")
    if not expected_server or not expected_server.get("id") or not expected_server.get("instance_id"):
        raise RuntimeError("Connect to an updated server before managing sources")
    data = {"action": action, "server_id": expected_server["id"],
            "instance_id": expected_server["instance_id"], **payload}
    encoded = json.dumps(data).encode() + b"\n"
    if len(encoded) > MAX_REQUEST:
        raise ValueError("Source request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(15)
        conn.connect(socket_path())
        conn.sendall(encoded)
        with conn.makefile("rb") as stream:
            line = stream.readline(MAX_REQUEST + 1)
        if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
            raise RuntimeError("Invalid source administration response")
        result = json.loads(line)
    if not isinstance(result, dict):
        raise RuntimeError("Invalid source administration response")
    return result
