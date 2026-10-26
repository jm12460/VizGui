"""Media sources: local folders and on-demand SMB mounts (gio/gvfs, no root)."""
import os
import subprocess


def _gvfs_dir():
    return f"/run/user/{os.getuid()}/gvfs"


def find_smb_mount(server, share):
    """Return the local gvfs path for an already-mounted smb://server/share, or None."""
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


def smb_mount(server, share, user="", domain="", password="", guest=False, timeout=30):
    """Mount smb://server/share via gvfs (userspace, no root) and return its local
    path. Credentials go to `gio mount` on stdin (user\\ndomain\\npassword\\n)."""
    existing = find_smb_mount(server, share)
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
    path = find_smb_mount(server, share)
    if not path:
        raise RuntimeError("mounted but gvfs path not found")
    return path


def source_path(source):
    """Resolve a source config entry to a local filesystem path (mounting an SMB
    share on demand). Raises on failure."""
    if source.get("kind") == "smb":
        s = source.get("smb", {})
        return smb_mount(s.get("server", ""), s.get("share", ""), s.get("user", ""),
                         s.get("domain", ""), s.get("password", ""), s.get("guest", False))
    return source.get("path")
