"""Linux OS integration: XDG dirs, gvfs (userspace) SMB mounts, `ip addr` subnet
detection, ufw/firewalld firewall, and D-Bus/MPRIS availability. POSIX process
launching is shared with macOS in ``_posix``."""
import glob
import ipaddress
import logging
import os
import re
import shlex
import subprocess
import threading
import urllib.parse

from ._posix import launch_caster, launch_detached  # noqa: F401 - re-exported


def _xdg(var, default):
    return os.environ.get(var) or os.path.expanduser(default)


def _xdg_user_dir(key, fallback):
    """Resolve an XDG user dir (e.g. 'VIDEOS') from ~/.config/user-dirs.dirs,
    falling back to ~/<fallback>."""
    cfg = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "user-dirs.dirs")
    try:
        with open(cfg) as f:
            for line in f:
                if line.startswith(f"XDG_{key}_DIR"):
                    val = line.split("=", 1)[1].strip().strip('"')
                    return val.replace("$HOME", os.path.expanduser("~"))
    except Exception:
        pass
    return os.path.expanduser(f"~/{fallback}")


def default_media_dirs():
    out = []
    for name, key in (("Videos", "VIDEOS"), ("Music", "MUSIC"), ("Downloads", "DOWNLOAD")):
        p = _xdg_user_dir(key, name)
        if os.path.isdir(p):
            out.append((name, p))
    return out


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
        el = urllib.parse.unquote(e).lower()
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


def process_listens_on_port(pid, port, *, proc_root="/proc"):
    """Check that this process owns the listening socket, without opening it."""
    if type(pid) is not int or pid <= 0 or type(port) is not int or not 0 < port < 65536:
        return False
    try:
        directory = os.path.join(proc_root, str(pid), "fd")
        inodes = set()
        inaccessible = False
        for fd in os.listdir(directory):
            try:
                link = os.readlink(os.path.join(directory, fd))
            except OSError:
                inaccessible = True
                continue
            if link.startswith("socket:[") and link.endswith("]"):
                inodes.add(link[8:-1])
        for table in ("tcp", "tcp6"):
            try:
                with open(os.path.join(proc_root, "net", table)) as f:
                    next(f, None)
                    for line in f:
                        fields = line.split()
                        if (len(fields) > 9 and fields[3] == "0A"
                                and int(fields[1].rsplit(":", 1)[1], 16) == port
                                and fields[9] in inodes):
                            return True
            except (OSError, ValueError, IndexError):
                inaccessible = True
                continue
    except OSError:
        return None
    return None if inaccessible else False


_HW_UNSET = object()
_HW_CACHE = _HW_UNSET
_HW_LOCK = threading.Lock()
_HW_ENV = "VIZGUI_HW_ENCODER"            # auto (default) | nvenc | vaapi | none
_PROBE_TIMEOUT = 10
_log = logging.getLogger("viz-gui")

# Decode on the CPU and convert to 8-bit 4:2:0 before H.264 encoding. CUDA
# decoding can leave 10-bit HEVC frames in a format that H.264 NVENC cannot
# consume, even though the encoder itself is available.
_NVENC = ("", "-c:v h264_nvenc -pix_fmt yuv420p -preset p4 -rc vbr -cq 23 -b:v 0 "
              "-maxrate 12M -bufsize 24M -g 48 -bf 0")
# VAAPI (AMD/Intel): CPU decode, then convert to 8-bit NV12 and upload to the GPU
# as the last step of the filter chain (after any subtitle burn-in). No -rc_mode,
# so ffmpeg picks a rate-control mode the driver actually supports.
_VAAPI_CODEC = "-c:v h264_vaapi -b:v 8M -maxrate 12M -bufsize 24M -g 48 -bf 0"
_VAAPI_UPLOAD = "format=nv12,hwupload"


def _render_nodes():
    return sorted(glob.glob("/dev/dri/renderD*"))


def _encoder_works(input_prefix, codec_args, vfilter=""):
    """Encode a few frames of a synthetic clip with exactly these arguments.

    ``ffmpeg -encoders`` only lists what ffmpeg was *built* with: distro builds
    (Arch/CachyOS, Debian/Ubuntu) list h264_nvenc on every machine, and it then
    fails at runtime without an NVIDIA GPU ("Cannot load libcuda.so.1"). A real
    encode is the only reliable check."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    cmd += shlex.split(input_prefix)
    cmd += ["-f", "lavfi", "-i", "color=c=black:s=320x240:r=24", "-frames:v", "3"]
    if vfilter:
        cmd += ["-vf", vfilter]
    cmd += shlex.split(codec_args) + ["-an", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        _log.info("hw encoder probe failed to run (%s): %s", codec_args.split()[1], e)
        return False
    if r.returncode != 0:
        first = (r.stderr or "").strip().splitlines()[:1] or [""]
        _log.info("hw encoder probe rejected %s: %s", codec_args.split()[1], first[0])
        return False
    return True


def _hw_candidates(choice):
    """(label, (input_prefix, codec_args, upload_filter)) in preference order."""
    if choice in ("auto", "nvenc"):
        yield "nvenc", _NVENC + ("",)
    if choice in ("auto", "vaapi"):
        for node in _render_nodes():
            yield f"vaapi {node}", (f"-vaapi_device {shlex.quote(node)}",
                                     _VAAPI_CODEC, _VAAPI_UPLOAD)


def hw_encode_args():
    """First hardware H.264 encoder that actually works here: NVIDIA NVENC, then
    VAAPI (AMD/Intel) on each GPU render node; None means software libx264.

    Returns ``(input_prefix, codec_args, upload_filter)``. Each candidate is
    verified with a tiny test encode once per process. Set VIZGUI_HW_ENCODER to
    nvenc, vaapi or none to force a choice (e.g. if the TV shows artifacts)."""
    global _HW_CACHE
    if _HW_CACHE is not _HW_UNSET:
        return _HW_CACHE
    # Do not publish a fallback until probing is complete: callers in other
    # threads must see the same result rather than treating an in-flight probe
    # as evidence that hardware is unavailable.
    with _HW_LOCK:
        if _HW_CACHE is _HW_UNSET:
            _HW_CACHE = _probe_hw_encoder()
        return _HW_CACHE


def _probe_hw_encoder():
    choice = os.environ.get(_HW_ENV, "auto").strip().lower() or "auto"
    if choice in ("none", "off", "cpu", "software"):
        _log.info("hw encoder disabled by %s; using libx264", _HW_ENV)
        return None
    if choice not in ("auto", "nvenc", "vaapi"):
        _log.warning("ignoring unknown %s=%r; probing automatically", _HW_ENV, choice)
        choice = "auto"
    for label, args in _hw_candidates(choice):
        if _encoder_works(args[0], args[1], args[2]):
            _log.info("hw encoder: %s", label)
            return args
    _log.info("no working hw encoder; using libx264")
    return None
