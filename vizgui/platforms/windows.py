"""Windows OS integration: %APPDATA% dirs, SMB via UNC paths / ``net use``,
``ipconfig`` subnet detection, Windows Firewall (netsh), and process launch with
creation flags (no console window, kill the whole tree via taskkill).

No MPRIS on Windows, so a hand-opened VLC isn't controllable — only our own
HTTP-interface VLC is (``supports_mpris`` stays False from the base contract).
Imports cleanly on any OS (constants are literals, not Windows-only attributes)
so the pure logic can be unit-tested anywhere.
"""
import ipaddress
import os
import re
import subprocess

from .base import CasterHandle, find_tool, has_tool

# Popen creation flags (literals, so this module imports on non-Windows too).
_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

_VLC = (r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe")


def _appdata():
    return os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")


def _localappdata():
    return os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")


def config_dir():
    return os.path.join(_appdata(), "vizgui")


def cache_dir():
    return os.path.join(_localappdata(), "vizgui", "cast")


def log_dir():
    return os.path.join(_localappdata(), "vizgui", "logs")


def vlc_exe():
    return find_tool("vlc", _VLC)


def has_vlc():
    return has_tool("vlc", _VLC)


# ---- SMB: Windows reaches shares by UNC path; `net use` carries credentials ----
def _unc(server, share):
    return rf"\\{server}\{share}"


def find_mount(server, share):
    unc = _unc(server, share)
    return unc if os.path.isdir(unc) else None


def mount_share(server, share, user="", domain="", password="", guest=False, timeout=30):
    unc = _unc(server, share)
    if os.path.isdir(unc):
        return unc
    cmd = ["net", "use", unc]
    if guest:
        cmd += ['""', "/user:guest"]
    else:
        if password:
            cmd.append(password)
        if user:
            cmd.append("/user:" + (f"{domain}\\{user}" if domain else user))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not os.path.isdir(unc):
        raise RuntimeError((p.stderr + p.stdout).strip() or "net use failed")
    return unc


def lan_cidr(ip):
    try:
        out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5).stdout
        cur_ip = None
        for line in out.splitlines():
            m = re.search(r"IPv4 Address.*?:\s*([\d.]+)", line)
            if m:
                cur_ip = m.group(1)
                continue
            m = re.search(r"Subnet Mask.*?:\s*([\d.]+)", line)
            if m and cur_ip == ip:
                prefix = ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}", strict=False).prefixlen
                return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
    except Exception:
        pass
    return ".".join(ip.split(".")[:3]) + ".0/24"


def firewall_allow_cmd(cidr, port):
    # Run from an *elevated* (Administrator) command prompt.
    return ('netsh advfirewall firewall add rule name="vizgui phone remote" '
            f"dir=in action=allow protocol=TCP localport={port} remoteip={cidr}")


class _WinCasterHandle(CasterHandle):
    """Kill the whole tree (go-chromecast + any ffmpeg child) via taskkill /T."""
    def terminate(self):
        p = self._p
        if p.poll() is None:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, timeout=5,
                               creationflags=_CREATE_NO_WINDOW)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


def launch_caster(args, env=None, log_path=None):
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out, env=env,
                         creationflags=_CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP)
    if log_path:
        out.close()
    return _WinCasterHandle(p)


def launch_detached(args, log_path=None):
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP)
    if log_path:
        out.close()
    return p


_HW_UNSET = object()
_HW_CACHE = _HW_UNSET


def hw_encode_args():
    """First available hardware H.264 encoder: NVIDIA, then Intel QSV, then AMD."""
    global _HW_CACHE
    if _HW_CACHE is _HW_UNSET:
        _HW_CACHE = None
        try:
            enc = subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=10).stdout
            if "h264_nvenc" in enc:
                _HW_CACHE = ("-hwaccel cuda",
                             "-c:v h264_nvenc -preset p4 -rc vbr -cq 23 -b:v 0 "
                             "-maxrate 12M -bufsize 24M -g 48 -bf 0")
            elif "h264_qsv" in enc:
                _HW_CACHE = ("-hwaccel qsv",
                             "-c:v h264_qsv -global_quality 23 -maxrate 12M "
                             "-bufsize 24M -g 48")
            elif "h264_amf" in enc:
                _HW_CACHE = ("",
                             "-c:v h264_amf -quality balanced -rc vbr_latency "
                             "-b:v 8M -maxrate 12M -g 48")
        except Exception:
            _HW_CACHE = None
    return _HW_CACHE
