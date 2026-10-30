"""Validation and credential-safe projections for server-managed sources.

This module does not persist configuration or expose an API. Callers must
perform authorization and serialize any eventual configuration updates.
"""
import os


def _text(value, field, *, required=False):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {field}")
    if required and not value.strip():
        raise ValueError(f"{field} is required")
    return value


def validate_new_source(source):
    """Return a new source using only supported fields and server-local paths."""
    if not isinstance(source, dict):
        raise ValueError("Invalid source")
    kind = source.get("kind", "local")
    name = _text(source.get("name", ""), "name").strip()
    if kind == "local":
        path = os.path.expanduser(_text(source.get("path", ""), "path", required=True).strip())
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise ValueError("Folder must be an existing absolute server path")
        path = os.path.normpath(path)
        return {"name": name or os.path.basename(path) or path, "path": path}
    if kind != "smb" or not isinstance(source.get("smb"), dict):
        raise ValueError("Invalid source type")
    raw = source["smb"]
    server = _text(raw.get("server", ""), "server", required=True).strip().strip("/\\")
    share = _text(raw.get("share", ""), "share", required=True).strip().strip("/\\")
    if not server or not share or "/" in server or "\\" in server or "/" in share or "\\" in share:
        raise ValueError("Invalid SMB server or share")
    guest = raw.get("guest", False)
    if type(guest) is not bool:
        raise ValueError("Invalid guest setting")
    user = _text(raw.get("user", ""), "username").strip()
    domain = _text(raw.get("domain", ""), "domain").strip()
    password = _text(raw.get("password", ""), "password")
    smb = {"server": server, "share": share, "guest": guest,
           "user": "" if guest else user, "domain": "" if guest else domain,
           "password": "" if guest else password}
    return {"name": name or share, "kind": "smb", "smb": smb}


def public_sources(sources):
    """Expose only display information; never return SMB credentials or unknown fields."""
    result = []
    for index, source in enumerate(sources):
        item = {"index": index, "name": source.get("name", ""),
                "kind": "smb" if source.get("kind") == "smb" else "local"}
        if item["kind"] == "smb":
            smb = source.get("smb") or {}
            item.update(server=smb.get("server", ""), share=smb.get("share", ""))
        else:
            item["path"] = source.get("path", "")
        result.append(item)
    return result
