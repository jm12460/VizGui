"""Pure media helpers for casting: codec probe, safe-named serving symlink, and
folder queue / next-prev navigation."""
import os
import re
import subprocess

from ..paths import CACHE, VIDEO_GLOBS

_EXTS = tuple(g[1:] for g in VIDEO_GLOBS)   # (".mp4", ".mkv", ...)


def vcodec(path):
    """Video codec of the first video stream (e.g. 'h264', 'hevc'), or ''."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name",
                            "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception:
        return ""


def symlink_serve(path):
    """Serve a file via a safe-named symlink in CACHE. go-chromecast embeds the
    path in its URL query where '+' etc. misbehave, so the served name must be
    sanitized; a symlink avoids copying."""
    os.makedirs(CACHE, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path)) or "video"
    link = os.path.join(CACHE, safe)
    try:
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(os.path.abspath(path), link)
        return link
    except OSError:
        return path


def _sorted_videos(folder):
    try:
        return sorted((os.path.join(folder, f) for f in os.listdir(folder)
                       if f.lower().endswith(_EXTS) and os.path.isfile(os.path.join(folder, f))),
                      key=str.lower)
    except OSError:
        return []


def folder_queue(path):
    """Sorted playable files in path's folder, from `path` to the end."""
    files = _sorted_videos(os.path.dirname(path))
    try:
        return files[files.index(path):]
    except ValueError:
        return [path]


def next_file(path):
    q = folder_queue(path)
    return q[1] if len(q) > 1 else None


def prev_file(path):
    files = _sorted_videos(os.path.dirname(path))
    try:
        i = files.index(path)
        return files[i - 1] if i > 0 else None
    except ValueError:
        return None
