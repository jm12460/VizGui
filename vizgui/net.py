"""Local network helpers: this machine's LAN IP and its real subnet CIDR."""
import ipaddress
import re
import socket
import subprocess


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def lan_cidr():
    """The real network CIDR of the interface this machine uses (e.g.
    10.0.0.0/24, 192.168.0.0/23), read from `ip addr`. Falls back to /24."""
    ip = local_ip()
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
