"""Local 'continue watching' history: remember where playback stopped, per file.

Stored in ``history.json`` beside the config (never leaves the machine). Keyed by
absolute path — not by device — so resuming uses whatever target is selected now,
not the one it was last played on. Pure logic (path-injectable) so it's testable.
"""
import json
import os
import time

from .paths import CONFIG_DIR

HISTORY_PATH = os.path.join(CONFIG_DIR, "history.json")

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
