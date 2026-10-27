"""Small pure helpers: time formatting, status parsing, cast planning."""
import os
import re

# Containers a Cast receiver plays directly (no repackaging) when the video is H.264.
DIRECT_CONTAINERS = ("mp4", "m4v", "mov")


def cast_plan(vcodec, ext):
    """How to send a file to a Cast device, given its video codec and extension:
      'direct'    -> serve the file as-is (H.264 in an mp4-family container)
      'remux'     -> container copy only, no re-encode (H.264 in e.g. mkv)
      'transcode' -> re-encode the video to H.264 (HEVC and everything else)
    This keeps go-chromecast off its own slow CPU transcode path."""
    ext = (ext or "").lower().lstrip(".")
    if vcodec == "h264" and ext in DIRECT_CONTAINERS:
        return "direct"
    if vcodec == "h264":
        return "remux"
    return "transcode"


def fmt_time(s):
    s = int(s)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def parse_status(line):
    """Parse a go-chromecast status line into {state, pos, dur, file}.
    Example: 'Default Media Receiver (PAUSED), [http://...media_file=/p/x.mp4&...],
              time remaining=31s/1298s, volume=0.23, muted=false'
    The first time value is the current position, the second the duration."""
    d = {}
    m = re.search(r"\((PLAYING|PAUSED|BUFFERING|LOADING|IDLE)\)", line)
    d["state"] = m.group(1) if m else ("IDLE" if ("Idle" in line or "Backdrop" in line) else "")
    m = re.search(r"=(\d+)s/(\d+)s", line)
    if m:
        d["pos"], d["dur"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"media_file=([^&\]]+)", line)
    if m:
        d["file"] = os.path.basename(m.group(1))
    return d
