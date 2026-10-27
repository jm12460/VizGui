"""Media sources: local folders and on-demand network (SMB) shares.

OS-specific mounting is delegated to the platform layer.
"""
from . import platforms


def smb_mount(server, share, user="", domain="", password="", guest=False, timeout=30):
    """Mount an SMB share and return a local path (platform-specific)."""
    return platforms.mount_share(server, share, user, domain, password, guest, timeout)


def source_path(source):
    """Resolve a source config entry to a local filesystem path (mounting an SMB
    share on demand). Raises on failure."""
    if source.get("kind") == "smb":
        s = source.get("smb", {})
        return smb_mount(s.get("server", ""), s.get("share", ""), s.get("user", ""),
                         s.get("domain", ""), s.get("password", ""), s.get("guest", False))
    return source.get("path")
