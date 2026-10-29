"""Server identity and initial session API contract."""

import json
import uuid
from unittest import mock

from vizgui import server_identity


def test_identity_persists(tmp_path):
    path = tmp_path / "identity.json"
    with mock.patch.object(server_identity, "IDENTITY_PATH", str(path)):
        first = server_identity.server_identity()
        second = server_identity.server_identity()

    assert first == second
    assert str(uuid.UUID(first)) == first
    assert json.loads(path.read_text())["id"] == first


def test_server_info(tmp_path):
    path = tmp_path / "identity.json"
    with mock.patch.object(server_identity, "IDENTITY_PATH", str(path)):
        info = server_identity.server_info()

    assert info["ok"] is True
    assert info["api_version"] == 1
    assert "default-session" in info["capabilities"]


def test_initial_sessions():
    result = server_identity.sessions_info()

    assert result["ok"] is True
    assert [s["id"] for s in result["sessions"]] == ["default"]
