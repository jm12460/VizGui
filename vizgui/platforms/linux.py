"""Linux OS integration: XDG dirs, gvfs (userspace) SMB mounts, `ip addr` subnet
detection, ufw firewall, and D-Bus/MPRIS availability."""
import ipaddress
import os
import pty
import re
import signal
import subprocess
import threading

from .base import CasterHandle


def _xdg(var, default):
    return os.environ.get(var) or os.path.expanduser(default)


def config_dir():
    return os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "vizgui")


def cache_dir():
    return os.path.join(_xdg("XDG_CACHE_HOME", "~/.cache"), "cast")


def log_dir():
    return os.path.join(_xdg("XDG_CACHE_HOME", "~/.cache"), "viz-gui")


def _gvfs_dir():
    return f"/run/user/{os.getuid()}/gvfs"


def find_mount(server, share):
    try:
        entries = os.listdir(_gvfs_dir())
    except OSError:
        return None
    sv, sh = server.lower(), share.lower()
    for e in entries:
        el = e.lower()
        if el.startswith("smb-share:") and f"server={sv}" in el and f"share={sh}" in el:
            full = os.path.join(_gvfs_dir(), e)
            if os.path.isdir(full):
                return full
    return None


def mount_share(server, share, user="", domain="", password="", guest=False, timeout=30):
    """Mount smb://server/share via gvfs (userspace, no root). Credentials go to
    `gio mount` on stdin (user\\ndomain\\npassword\\n)."""
    existing = find_mount(server, share)
    if existing:
        return existing
    url = f"smb://{server}/{share}"
    if guest:
        p = subprocess.run(["gio", "mount", "-a", url],
                           capture_output=True, text=True, timeout=timeout)
    else:
        p = subprocess.run(["gio", "mount", url],
                           input=f"{user}\n{domain}\n{password}\n",
                           capture_output=True, text=True, timeout=timeout)
    msg = (p.stderr + p.stdout).strip()
    if p.returncode != 0 and "already mounted" not in msg.lower():
        raise RuntimeError(msg or "mount failed")
    path = find_mount(server, share)
    if not path:
        raise RuntimeError("mounted but gvfs path not found")
    return path


def lan_cidr(ip):
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            mt = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
            if mt and mt.group(1) == ip:
                return str(ipaddress.ip_network(f"{ip}/{mt.group(2)}", strict=False))
    except Exception:
        pass
    return ".".join(ip.split(".")[:3]) + ".0/24"


def _svc_active(name):
    """Is a systemd service active? (root-free, so safe to call from the UI.)"""
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", name],
                              timeout=3).returncode == 0
    except Exception:
        return False


def firewall_allow_cmd(cidr, port):
    """The command to open the phone-remote port for the LAN, matched to whichever
    firewall is actually running. None when no known firewall is active (the port
    is then already reachable)."""
    if _svc_active("ufw"):
        return f"sudo ufw allow from {cidr} to any port {port} proto tcp"
    if _svc_active("firewalld"):
        return ("sudo firewall-cmd --permanent --add-rich-rule="
                f"'rule family=\"ipv4\" source address=\"{cidr}\" "
                f"port port=\"{port}\" protocol=\"tcp\" accept' "
                "&& sudo firewall-cmd --reload")
    return None


def supports_mpris():
    return True


class _LinuxCasterHandle(CasterHandle):
    """Terminates the whole process group (go-chromecast + any ffmpeg child)."""
    def terminate(self):
        p = self._p
        if p.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(os.getpgid(p.pid), sig)
                    p.wait(timeout=3)
                    break
                except ProcessLookupError:
                    break
                except Exception:
                    pass


def launch_caster(args, env=None, log_path=None):
    """go-chromecast keeps its in-process HTTP server alive only while it has a
    controlling terminal, so launch it on a PTY (its own session), draining the
    PTY to a log so the buffer never stalls it."""
    master, slave = pty.openpty()
    p = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                         start_new_session=True, close_fds=True, env=env)
    os.close(slave)

    def _drain():
        try:
            with (open(log_path, "wb") if log_path else open(os.devnull, "wb")) as f:
                while True:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    f.write(data)
                    f.flush()
        except OSError:
            pass
        finally:
            try:
                os.close(master)
            except OSError:
                pass

    threading.Thread(target=_drain, daemon=True).start()
    return _LinuxCasterHandle(p)


def launch_detached(args, log_path=None):
    out = open(log_path, "wb") if log_path else subprocess.DEVNULL
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                         start_new_session=True)
    if log_path:
        out.close()
    return p


_HW_UNSET = object()
_HW_CACHE = _HW_UNSET


def hw_encode_args():
    """NVENC (NVIDIA) if ffmpeg has it, else None (software libx264). Cached."""
    global _HW_CACHE
    if _HW_CACHE is _HW_UNSET:
        _HW_CACHE = None
        try:
            r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                               capture_output=True, text=True, timeout=10)
            if "h264_nvenc" in r.stdout:
                _HW_CACHE = ("-hwaccel cuda",
                             "-c:v h264_nvenc -preset p4 -rc vbr -cq 23 -b:v 0 "
                             "-maxrate 12M -bufsize 24M -g 48 -bf 0")
        except Exception:
            _HW_CACHE = None
    return _HW_CACHE
