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


def discovery_plan(devs, targets):
    """Decide what a Cast scan should do, given its results and the current targets.
    Pure (no UI): returns (to_add, status_message).

    Only the unambiguous case auto-adds — a fresh setup where exactly one device
    is on the network. With several devices we never presume; we list them and
    let the user pick (via Scan), so nobody with many TVs gets them all dumped in.
    Adding a target only lists it as a possible destination; it never connects to
    or commands the device.
    """
    if not isinstance(devs, list):
        devs = []   # run_async wraps a failed scan as an error dict
    cast_addrs = {t.get("addr") for t in targets if t.get("type") == "cast"}
    new = [d for d in devs if d.get("addr") not in cast_addrs]
    if not new:
        if devs:
            return [], ""   # everything found is already a target
        return [], ("No Cast devices found yet. Make sure the TV is on and on the same "
                    "Wi-Fi, then click “Scan for Cast devices”.")
    if cast_addrs:
        names = ", ".join(f"{d['name']} ({d['addr']})" for d in new)
        return [], f"Also on your network: {names} — use “Scan for Cast devices” to add."
    if len(new) == 1:
        d = new[0]
        return [d], (f"✓ Found your TV: {d['name']}. Select it and “Pair SmartCast…” "
                     "to enable the remote.")
    names = ", ".join(d["name"] for d in new)
    return [], (f"Found {len(new)} Cast devices ({names}). Click “Scan for Cast devices” "
                "to add the one you want.")


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
