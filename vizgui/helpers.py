"""Small pure helpers: time formatting and go-chromecast status parsing."""
import os
import re


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
