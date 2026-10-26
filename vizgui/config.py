"""Config load/save/migration and the shared, mutable cast-target state.

Other modules read the active cast target as ``config.HOST`` / ``config.TOK`` /
``config.TV_IP`` / ``config.CAST_PORT`` and the loaded config as ``config.CFG``
(always via the module, never ``from config import CFG``, so updates propagate).
"""
import json
import os
import secrets

from .paths import CONFIG_DIR, CONFIG_PATH, log


def _migrate_default():
    """Build a first-run config from whatever this machine already has: the old
    Vizio pairing (~/.config/vizio/auth.json) becomes a cast target, /tank/media
    a source, and local VLC is always offered."""
    cfg = {"sources": [], "targets": []}
    if os.path.isdir("/tank/media"):
        cfg["sources"].append({"name": "Media", "path": "/tank/media"})
    old = os.path.expanduser("~/.config/vizio/auth.json")
    if os.path.isfile(old):
        try:
            with open(old) as f:
                a = json.load(f)
            host = a.get("host", "")
            cfg["targets"].append({
                "type": "cast", "name": "Basement TV",
                "addr": host.split(":")[0], "cast_port": "8009",
                "smartcast_host": host, "auth_token": a.get("auth_token")})
        except Exception:
            pass
    cfg["targets"].append({"type": "vlc", "name": "VLC (this PC)"})
    cfg["web"] = {"enabled": True, "port": 8950}
    cfg["auto_next"] = True
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)   # may hold a share password
    except OSError:
        pass


def ensure_web_secrets(cfg):
    """Make sure the phone remote has an auth token and a pairing PIN."""
    w = cfg.setdefault("web", {"enabled": True, "port": 8950})
    changed = False
    if not w.get("token"):
        w["token"] = secrets.token_urlsafe(24); changed = True
    if not w.get("pin"):
        w["pin"] = f"{secrets.randbelow(1000000):06d}"; changed = True
    return changed


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg.setdefault("sources", [])
        cfg.setdefault("targets", [])
        cfg.setdefault("web", {"enabled": True, "port": 8950})
        cfg.setdefault("auto_next", True)
        if ensure_web_secrets(cfg):
            try:
                save_config(cfg)
            except Exception:
                pass
        return cfg
    except Exception:
        cfg = _migrate_default()
        ensure_web_secrets(cfg)
        try:
            save_config(cfg)
        except Exception:
            pass
        return cfg


CFG = load_config()


def active_cast(cfg=None):
    for t in (cfg or CFG).get("targets", []):
        if t.get("type") == "cast":
            return t
    return None


# ---- shared mutable state: the currently-active cast target ----
TV_IP = None           # Cast device IP (go-chromecast)
CAST_PORT = "8009"
HOST = None            # Vizio SmartCast host:port (or None)
TOK = None             # SmartCast pairing token (or None)


def set_active_cast(t):
    """Point the shared cast/SmartCast state at target dict ``t``."""
    global TV_IP, CAST_PORT, HOST, TOK
    TV_IP = t.get("addr")
    CAST_PORT = t.get("cast_port") or "8009"
    HOST = t.get("smartcast_host")
    TOK = t.get("auth_token")


def apply_active_cast(cfg=None):
    t = active_cast(cfg)
    set_active_cast(t or {})


apply_active_cast()
log.info("=== viz-gui started; host=%s tv_ip=%s ===", HOST, TV_IP)
