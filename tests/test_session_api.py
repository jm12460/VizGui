"""HTTP integration tests for session-aware API compatibility."""

import json
import urllib.error
import urllib.request
from unittest import mock

import pytest

from vizgui import config, server_identity, webremote


class FakeController:
    def __init__(self):
        self.commands = []

    def web_status(self):
        return {"ok": True, "now": {"title": "Test Movie"}}

    def web_history(self):
        return []

    def web_dispatch(self, kind, payload):
        self.commands.append((kind, payload))


@pytest.fixture
def api_server(tmp_path):
    controller = FakeController()

    with (
        mock.patch.object(config, "CFG", {
            "web": {"token": "test-secret"},
            "sources": [],
        }),
        mock.patch.object(
            server_identity,
            "IDENTITY_PATH",
            str(tmp_path / "identity.json"),
        ),
    ):
        httpd = webremote.start_web_server(controller, 0)
        base = f"http://127.0.0.1:{httpd.server_port}"

        try:
            yield base, controller
        finally:
            httpd.shutdown()
            httpd.server_close()


def request(base, path, token="test-secret", payload=None):
    headers = {}
    if token is not None:
        headers["X-Viz-Token"] = token

    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()

    req = urllib.request.Request(
        base + path, data=data, headers=headers
    )

    with urllib.request.urlopen(req, timeout=3) as response:
        return json.load(response)


def test_server_identity_endpoint(api_server):
    base, _ = api_server

    first = request(base, "/api/server")
    second = request(base, "/api/server")

    assert first["ok"] is True
    assert first["id"] == second["id"]
    assert first["api_version"] == 1


def test_sessions_endpoint(api_server):
    base, _ = api_server

    result = request(base, "/api/sessions")

    assert result["ok"] is True
    assert result["sessions"][0]["id"] == "default"


def test_legacy_and_session_status_match(api_server):
    base, _ = api_server

    legacy = request(base, "/api/status")
    session = request(base, "/api/sessions/default/status")

    assert legacy == session
    assert session["now"]["title"] == "Test Movie"


def test_session_command_dispatch(api_server):
    base, controller = api_server

    result = request(
        base,
        "/api/sessions/default/cmd",
        payload={"kind": "transport", "action": "playpause"},
    )

    assert result["ok"] is True
    assert controller.commands == [
        (
            "transport",
            {"kind": "transport", "action": "playpause"},
        )
    ]


def test_legacy_command_still_works(api_server):
    base, controller = api_server

    request(
        base,
        "/api/cmd",
        payload={"kind": "transport", "action": "stop"},
    )

    assert controller.commands[0][0] == "transport"


def test_authentication_required(api_server):
    base, _ = api_server

    for path in (
        "/api/server",
        "/api/sessions",
        "/api/sessions/default/status",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc:
            request(base, path, token=None)

        assert exc.value.code == 401


def test_unknown_session_returns_404(api_server):
    base, _ = api_server

    with pytest.raises(urllib.error.HTTPError) as exc:
        request(base, "/api/sessions/nonexistent/status")

    assert exc.value.code == 404
