"""Pure media helpers for casting: codec probe, safe-named serving symlink, and
folder queue / next-prev navigation."""
import os
import json
import re
import subprocess

from .. import platforms
from ..paths import CACHE, VIDEO_GLOBS

_EXTS = tuple(g[1:] for g in VIDEO_GLOBS)   # (".mp4", ".mkv", ...)


# Transfer characteristics of HDR video: PQ (HDR10, HDR10+, Dolby Vision 8.1's
# base layer) and HLG. Dolby Vision profile 5 has no HDR10 base and isn't covered.
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")


def video_info(path):
    """First video stream as ``{"size": (w, h) or None, "hdr": transfer or None}``,
    from one ffprobe call; None when probing fails. ``hdr`` names the PQ/HLG
    transfer of HDR video and is None for SDR."""
    try:
        result = subprocess.run(
            [platforms.find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,color_transfer", "-of", "json", path],
            capture_output=True, text=True, timeout=10, check=True)
        stream = json.loads(result.stdout)["streams"][0]
        if not isinstance(stream, dict):
            return None
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError):
        return None
    try:
        width, height = int(stream["width"]), int(stream["height"])
        size = (width, height) if width > 0 and height > 0 else None
    except (KeyError, TypeError, ValueError):
        size = None
    transfer = stream.get("color_transfer")
    return {"size": size, "hdr": transfer if transfer in HDR_TRANSFERS else None}


def video_size(path):
    """Return first-video dimensions, or None when probing is inconclusive."""
    info = video_info(path)
    return info["size"] if info else None


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


def safe_name(path):
    """The sanitized basename go-chromecast serves and reports for a file (so the
    receiver's media filename can be matched back to its source)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path)) or "video"


def symlink_serve(path):
    """Serve a file via a safe-named symlink in CACHE. go-chromecast embeds the
    path in its URL query where '+' etc. misbehave, so the served name must be
    sanitized; a symlink avoids copying."""
    os.makedirs(CACHE, exist_ok=True)
    safe = safe_name(path)
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
