"""macOS OS integration: ~/Library dirs, SMB via Finder (``osascript mount
volume`` → /Volumes), ``ifconfig`` subnet detection, and VideoToolbox hardware
encoding. POSIX process launching (PTY caster) is shared with Linux in ``_posix``.

macOS has no MPRIS, so a hand-opened VLC isn't controllable — only our own
HTTP-interface VLC is (``supports_mpris`` stays False from the base contract).
The macOS application firewall is per-app (it prompts on first bind) rather than
per-port, so there's no allow-command to show (``firewall_allow_cmd`` stays None).
Imports cleanly on any OS so the pure logic can be unit-tested anywhere.
"""
import ipaddress
import os
import re
import subprocess
from urllib.parse import quote

from .base import find_tool, has_tool
from ._posix import launch_caster, launch_detached  # noqa: F401 - re-exported

_VLC = ("/Applications/VLC.app/Contents/MacOS/VLC",
        "~/Applications/VLC.app/Contents/MacOS/VLC")


def _home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def config_dir():
    return _home("Library", "Application Support", "vizgui")


def cache_dir():
    return _home("Library", "Caches", "vizgui", "cast")


def log_dir():
    return _home("Library", "Logs", "vizgui")


def default_media_dirs():
    # macOS uses ~/Movies (not ~/Videos).
    out = []
    for name, folder in (("Movies", "Movies"), ("Music", "Music"), ("Downloads", "Downloads")):
        p = _home(folder)
        if os.path.isdir(p):
            out.append((name, p))
    return out


def vlc_exe():
    return find_tool("vlc", _VLC)


def has_vlc():
    return has_tool("vlc", _VLC)


# ---- SMB: let Finder mount it (no sudo); shares land under /Volumes/<share> ----
def find_mount(server, share):
    """Return the mountpoint of smb://server/share, matched by BOTH server and
    share from ``mount`` output (so a same-named share from another server isn't
    mistaken for this one)."""
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    sv, sh = server.lower(), share.lower()
    for line in out.splitlines():
        # //[user@]server/share on /Volumes/<name> (smbfs, ...)
        m = re.match(r"//(?:[^@/]+@)?([^/]+)/(\S+) on (.+?) \(", line)
        if m and m.group(1).lower() == sv and m.group(2).lower() == sh:
            path = m.group(3)
            if os.path.isdir(path):
                return path
    return None


def mount_share(server, share, user="", domain="", password="", guest=False, timeout=30):
    existing = find_mount(server, share)
    if existing:
        return existing
    if guest or not (user or password):
        url = f"smb://{server}/{share}"
    else:
        cred = ""
        if domain:
            cred += quote(domain, safe="") + ";"
        cred += quote(user, safe="")
        if password:
            cred += ":" + quote(password, safe="")
        url = f"smb://{cred}@{server}/{share}"
    p = subprocess.run(["osascript", "-e", f'mount volume "{url}"'],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr + p.stdout).strip() or "mount failed")
    path = find_mount(server, share)
    if not path:
        raise RuntimeError("mounted but /Volumes path not found")
    return path


def lan_cidr(ip):
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            m = re.search(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]+)", line)
            if m and m.group(1) == ip:
                prefix = bin(int(m.group(2), 16)).count("1")
                return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
    except Exception:
        pass
    return ".".join(ip.split(".")[:3]) + ".0/24"


_HW_UNSET = object()
_HW_CACHE = _HW_UNSET


def hw_encode_args():
    """Apple VideoToolbox H.264 encoder if ffmpeg has it, else software libx264."""
    global _HW_CACHE
    if _HW_CACHE is _HW_UNSET:
        _HW_CACHE = None
        try:
            enc = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=10).stdout
            if "h264_videotoolbox" in enc:
                _HW_CACHE = ("", "-c:v h264_videotoolbox -b:v 8M -maxrate 12M "
                                 "-bufsize 24M -g 48")
        except Exception:
            _HW_CACHE = None
    return _HW_CACHE
