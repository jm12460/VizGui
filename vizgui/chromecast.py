"""go-chromecast wrapper: run commands against the active Cast device, and scan."""
import re
import subprocess

from . import config
from .paths import GC, log


def gc(*args, timeout=15):
    """Run go-chromecast against the active Cast device, return (ok, output)."""
    if not config.TV_IP:
        return False, "no cast device configured"
    try:
        p = subprocess.run([GC, "--addr", config.TV_IP, "--port", config.CAST_PORT, *args],
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout + p.stderr).strip()
        if args and args[0] != "status":
            log.info("gc %s -> rc=%s %s", " ".join(args), p.returncode, out)
        return p.returncode == 0, out
    except Exception as e:
        log.warning("gc %s FAILED: %s", " ".join(args), e)
        return False, str(e)


def scan_cast_devices(timeout=8):
    """Discover Cast devices on the LAN via `go-chromecast ls`.
    -> [{name, addr, cast_port, uuid}]."""
    try:
        r = subprocess.run([GC, "ls"], capture_output=True, text=True, timeout=timeout)
        out = r.stdout
    except Exception:
        return []
    devices = []
    for line in out.splitlines():
        vals = dict(re.findall(r'(\w+)="([^"]*)"', line))
        addr = vals.get("address", "")
        if not addr:
            continue
        ip, _, port = addr.partition(":")
        devices.append({"name": vals.get("device_name") or vals.get("device") or ip,
                        "addr": ip, "cast_port": port or "8009",
                        "uuid": vals.get("uuid", "")})
    return devices
