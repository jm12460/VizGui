"""Stable identity and capabilities for a VizGui installation."""

import json
import os
import socket
import uuid

from .paths import CONFIG_DIR

IDENTITY_PATH = os.path.join(CONFIG_DIR, "server-identity.json")
DEFAULT_SESSION = "default"
INSTANCE_ID = str(uuid.uuid4())


def server_identity():
    """Read or create a persistent installation identity."""
    try:
        with open(IDENTITY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        uuid.UUID(data["id"])
        return data["id"]
    except FileNotFoundError:
        pass

    os.makedirs(CONFIG_DIR, exist_ok=True)
    server_id = str(uuid.uuid4())

    # Exclusive creation prevents overwriting an identity from another process.
    try:
        with open(IDENTITY_PATH, "x", encoding="utf-8") as f:
            json.dump({"id": server_id}, f)
        os.chmod(IDENTITY_PATH, 0o600)
    except FileExistsError:
        with open(IDENTITY_PATH, encoding="utf-8") as f:
            server_id = json.load(f)["id"]

    return server_id


def server_info():
    return {
        "ok": True,
        "id": server_identity(),
        "instance_id": INSTANCE_ID,
        "name": socket.gethostname(),
        "api_version": 1,
        "capabilities": ["default-session", "casting", "history"],
    }


def sessions_info():
    return {
        "ok": True,
        "sessions": [
            {"id": DEFAULT_SESSION, "name": "Default", "active": True}
        ],
    }
