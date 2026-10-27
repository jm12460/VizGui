"""Local network helpers: this machine's LAN IP and its subnet CIDR."""
import socket

from . import platforms


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
    """The network CIDR this machine is on (platform-specific detection)."""
    return platforms.lan_cidr(local_ip())
