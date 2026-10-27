"""Config load/save/migration and the shared, mutable cast-target state.

Other modules read the active cast target as ``config.HOST`` / ``config.TOK`` /
``config.TV_IP`` / ``config.CAST_PORT`` and the loaded config as ``config.CFG``
(always via the module, never ``from config import CFG``, so updates propagate).
"""
import json
import os
import secrets

from .paths import CONFIG_DIR, CONFIG_PATH, log
from . import platforms


def _migrate_default():
    """Build a clean first-run config so the app is useful immediately: the user's
    standard media folders (those that exist) as sources, plus a local VLC target.
    Cast devices and SmartCast TVs are discovered/added through the setup wizard."""
    sources = [{"name": name, "path": path}
               for name, path in platforms.default_media_dirs()]
    return {
        "sources": sources,
        "targets": [{"type": "vlc", "name": "VLC (this PC)"}],
        "web": {"enabled": True, "port": 8950},
        "auto_next": True,
    }


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


# True when this launch created the config from scratch (no file existed) —
# the app uses it to open the setup wizard on a genuine first run.
FRESH_INSTALL = False


def load_config():
    global FRESH_INSTALL
    FRESH_INSTALL = False
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
        FRESH_INSTALL = True
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
HOST = None            # SmartCast host:port (or None)
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
