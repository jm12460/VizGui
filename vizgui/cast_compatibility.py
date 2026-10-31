"""Private, bounded records of confirmed per-target media compatibility."""
import hashlib
import json
import os
import tempfile
import uuid

from .paths import CONFIG_DIR

COMPATIBILITY_PATH = os.path.join(CONFIG_DIR, 'cast_compatibility.json')
MAX_ENTRIES = 256


def media_key(target, path):
    """Hash a target identity and file fingerprint; never persist paths or addresses."""
    if not isinstance(target, dict) or target.get('type') != 'cast' or not path:
        return None
    raw_uuid = target.get('uuid')
    try:
        identity = ('uuid', str(uuid.UUID(raw_uuid))) if raw_uuid else None
    except (ValueError, TypeError, AttributeError):
        identity = None
    if identity is None:
        addr = target.get('addr')
        if not isinstance(addr, str) or not addr.strip():
            return None
        identity = ('address', addr.strip().lower(), str(target.get('cast_port') or '8009'))
    try:
        info = os.stat(path)
        if not os.path.isfile(path):
            return None
        payload = [identity, os.path.abspath(path), info.st_size, info.st_mtime_ns]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def _read():
    try:
        with open(COMPATIBILITY_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('entries'), list):
            return None
        entries = data['entries']
        if not all(isinstance(e, str) and len(e) == 64 and all(c in '0123456789abcdef' for c in e)
                   for e in entries):
            return None
        return entries[-MAX_ENTRIES:]
    except FileNotFoundError:
        return None if os.path.lexists(COMPATIBILITY_PATH) else []
    except (OSError, ValueError, TypeError):
        return None


def remembered(key):
    if not key:
        return False
    entries = _read()
    return entries is not None and key in entries


def remember(key):
    """Record confirmed 1080p playback; leave unreadable/corrupt files alone."""
    if not key:
        return False
    entries = _read()
    if entries is None:
        return False
    entries = [e for e in entries if e != key]
    entries.append(key)
    temp = None
    try:
        os.makedirs(os.path.dirname(COMPATIBILITY_PATH), mode=0o700, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.cast-compatibility-', dir=os.path.dirname(COMPATIBILITY_PATH))
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'version': 1, 'entries': entries[-MAX_ENTRIES:]}, f)
        os.replace(temp, COMPATIBILITY_PATH)
        return True
    except OSError:
        return False
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass
