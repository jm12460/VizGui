"""Local 'continue watching' history: remember where playback stopped, per file.

Stored in ``history.json`` beside the config (never leaves the machine). Keyed by
absolute path — not by device — so resuming uses whatever target is selected now,
not the one it was last played on. Pure logic (path-injectable) so it's testable.
"""
import json
import os
import time
import uuid

from .paths import CONFIG_DIR

HISTORY_PATH = os.path.join(CONFIG_DIR, "history.json")
CURRENT_PATH = os.path.join(CONFIG_DIR, "current_playback.json")

_FINISHED = 0.92   # watched >= 92% -> treat as finished (drop from the list)
_MIN_POS = 20      # ignore the first few seconds (not worth resuming)


def _path():
    return HISTORY_PATH


def load():
    try:
        with open(_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=0)
        os.replace(tmp, _path())
    except Exception:
        pass


def record(path, pos, dur, title="", now=None):
    """Remember `path` at `pos`/`dur` seconds. Drops the entry when finished
    (>= 92%) or barely started (< 20s), so the continue list stays meaningful."""
    if not path:
        return
    key = os.path.abspath(path)
    pos, dur = int(pos or 0), int(dur or 0)
    d = load()
    finished = dur > 0 and pos >= dur * _FINISHED
    if pos < _MIN_POS or finished:
        d.pop(key, None)
    else:
        d[key] = {"pos": pos, "dur": dur,
                  "title": title or os.path.basename(key),
                  "ts": time.time() if now is None else now}
    _save(d)


def resume_pos(path):
    """Seconds to resume `path` from, or 0 if nothing worth resuming."""
    e = load().get(os.path.abspath(path or ""))
    return int(e.get("pos", 0)) if e else 0


def recent(limit=24, exists=os.path.isfile):
    """Most-recent-first list of {path,pos,dur,title,ts} for files that still
    exist. `exists` is injectable for testing."""
    items = [dict(path=k, **v) for k, v in load().items()]
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return [x for x in items if exists(x["path"])][:limit]


def forget(path):
    d = load()
    if d.pop(os.path.abspath(path or ""), None) is not None:
        _save(d)


def clear():
    _save({})


def set_current(path, title="", resume_base=0):
    """Persist the source identity for reconnecting the UI to an active Cast."""
    if not path:
        return
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CURRENT_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"path": os.path.abspath(path), "title": title,
                       "resume_base": int(resume_base or 0)}, f)
        os.replace(tmp, CURRENT_PATH)
    except OSError:
        pass


def current():
    """Return the last Cast source identity, if it was persisted."""
    try:
        with open(CURRENT_PATH) as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("path"):
            return d
    except Exception:
        pass
    return None


def clear_current():
    try:
        os.remove(CURRENT_PATH)
    except OSError:
        pass


def _interrupted_path():
    return os.path.join(os.path.dirname(CURRENT_PATH), "interrupted_playback.json")


def interrupted():
    """Read an explicit interrupted-session checkpoint, never guess from history."""
    try:
        with open(_interrupted_path(), encoding="utf-8") as f:
            entry = json.load(f)
        if (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry["id"] and isinstance(entry.get("path"), str)
                and entry["path"] and type(entry.get("pos")) is int
                and entry["pos"] > 0):
            return entry
    except (OSError, ValueError):
        pass
    return None


def capture_interrupted(*, replace=False):
    """Preserve the known current file at its last recorded position."""
    previous = interrupted()
    if previous is not None and not replace:
        return previous
    saved = current()
    if not saved:
        return None
    path = saved["path"]
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    record = load().get(path)
    if (not isinstance(record, dict) or type(record.get("pos")) is not int
            or record["pos"] < _MIN_POS or not os.path.isfile(path)):
        return None
    entry = {"id": str(uuid.uuid4()), "path": path,
             "title": record.get("title") or saved.get("title") or os.path.basename(path),
             "pos": record["pos"]}
    try:
        os.makedirs(os.path.dirname(_interrupted_path()), exist_ok=True)
        temporary = _interrupted_path() + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            os.chmod(temporary, 0o600)
            json.dump(entry, f)
        os.replace(temporary, _interrupted_path())
    except OSError:
        pass
    return entry


def clear_interrupted():
    try:
        os.remove(_interrupted_path())
    except OSError:
        pass


def recover_match(media_file, saved, recent, safe=None, exists=os.path.isfile):
    """Identify which local file a Cast receiver is playing after an app restart,
    and whether we can reattach (re-cast) to it.

    `media_file` is the receiver's reported name: the sanitized filename for a
    direct cast, or ``pipe_output`` for a live transcode/remux. A transcode
    carries no filename, so it can ONLY be recovered from our own ``saved``
    current cast — never guessed from history (that would launch the wrong
    episode). ``safe`` / ``exists`` are injectable for testing.

    -> {path, title, resume_base, reattach} or None.
    """
    if safe is None:
        from .playback.media import safe_name as safe
    is_pipe = media_file == "pipe_output"
    if saved and exists(saved.get("path", "")):
        sp = saved["path"]
        if is_pipe or media_file in (os.path.basename(sp), safe(sp)):
            return {"path": sp, "title": saved.get("title") or os.path.basename(sp),
                    "resume_base": int(saved.get("resume_base", 0)), "reattach": True}
    if not is_pipe:                      # a direct cast we can name from history
        for e in recent:
            p = e.get("path", "")
            if media_file in (os.path.basename(p), safe(p)) or e.get("title") == media_file:
                return {"path": p, "title": e.get("title") or os.path.basename(p),
                        "resume_base": 0, "reattach": True}
    return None                          # live stream we didn't save -> don't guess
