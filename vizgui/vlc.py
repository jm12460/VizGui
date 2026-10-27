"""VLC control, two backends:

* HTTP  - a VLC we launched with ``--extraintf http`` (rich: load playlist, seek).
* MPRIS - any running VLC incl. one opened by hand, via playerctl/D-Bus (Linux).

``vlc_any_status`` prefers HTTP and falls back to MPRIS.
"""
import base64
import os
import re
import subprocess
import urllib.parse
import urllib.request

from . import platforms

VLC_PORT = 9099
VLC_PW = "vizgui"
VLC_URL = f"http://127.0.0.1:{VLC_PORT}/requests/status.xml"
_VLC_AUTH = "Basic " + base64.b64encode(f":{VLC_PW}".encode()).decode()


# ---- HTTP interface (a VLC we launched) ----
def vlc_req(params=""):
    url = VLC_URL + (("?" + params) if params else "")
    r = urllib.request.Request(url, headers={"Authorization": _VLC_AUTH})
    with urllib.request.urlopen(r, timeout=4) as resp:
        return resp.read().decode("utf-8", "replace")


def vlc_cmd(command):
    try:
        vlc_req("command=" + command)
        return True, ""
    except Exception as e:
        return False, str(e)


def vlc_status():
    """-> {state, pos, dur, file} or {'error': ...}."""
    try:
        xml = vlc_req()
    except Exception as e:
        return {"error": str(e)}
    def tag(t):
        m = re.search(rf"<{t}>([^<]*)</{t}>", xml)
        return m.group(1) if m else ""
    d = {"state": tag("state").upper()}
    try:
        d["pos"] = int(float(tag("time") or 0))
        d["dur"] = int(float(tag("length") or 0))
    except ValueError:
        d["pos"] = d["dur"] = 0
    m = re.search(r"<info name=['\"]filename['\"]>([^<]*)", xml)
    if m:
        d["file"] = m.group(1)
    return d


# ---- MPRIS (D-Bus) fallback: control ANY running VLC, incl. one opened by hand.
#      On by default in VLC (the `dbus` module), session-bus only (not networked).
def _pctl(*args, timeout=4):
    return subprocess.run(["playerctl", "-p", "vlc", *args],
                          capture_output=True, text=True, timeout=timeout)


def uri_to_path(u):
    if u.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(u).path)
    return u


def path_to_uri(p):
    return "file://" + urllib.parse.quote(os.path.abspath(p))


def mpris_running():
    try:
        return _pctl("status", timeout=3).returncode == 0
    except Exception:
        return False


def mpris_cmd(*args):
    try:
        _pctl(*args)
        return True, ""
    except Exception as e:
        return False, str(e)


def mpris_status():
    """-> {state, pos, dur, file, path} or {'error': ...}."""
    try:
        st = _pctl("status", timeout=3)
        if st.returncode != 0:
            return {"error": "no player"}
        state = st.stdout.strip().upper()
        pos = _pctl("position", timeout=3)
        try:
            posn = int(float(pos.stdout.strip() or 0))
        except ValueError:
            posn = 0
        meta = _pctl("metadata", "--format",
                     "{{mpris:length}}\t{{title}}\t{{xesam:url}}", timeout=3)
        length, title, url = (meta.stdout.strip().split("\t") + ["", "", ""])[:3]
        try:
            dur = int(int(length) / 1_000_000)
        except ValueError:
            dur = 0
        path = uri_to_path(url) if url else ""
        return {"state": state, "pos": posn, "dur": dur,
                "file": title or (os.path.basename(path) if path else ""), "path": path}
    except Exception as e:
        return {"error": str(e)}


def vlc_any_status():
    """Prefer our HTTP VLC; fall back to MPRIS (a manually-opened VLC) where the
    platform supports it."""
    s = vlc_status()
    if "error" not in s:
        s["_mode"] = "http"
        return s
    if platforms.supports_mpris():
        m = mpris_status()
        if "error" not in m:
            m["_mode"] = "mpris"
            return m
    return {"error": "no vlc"}
