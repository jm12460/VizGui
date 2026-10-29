"""Tests for the desktop-to-headless API client."""
import json
from unittest import mock

import pytest

from vizgui.server_client import ServerClient, ServerConnectionError


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, *args):
        return b'{"ok": true}'


def test_status_is_authenticated():
    client = ServerClient(port=8950, token="secret")

    with mock.patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ) as urlopen:
        assert client.status() == {"ok": True}

    request = urlopen.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:8950/api/sessions/default/status"
    assert request.get_header("X-viz-token") == "secret"


def test_command_sends_json():
    client = ServerClient(port=8950, token="secret")

    with mock.patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ) as urlopen:
        assert client.command("transport", action="playpause")["ok"]

    request = urlopen.call_args.args[0]
    assert json.loads(request.data) == {
        "kind": "transport",
        "action": "playpause",
    }


def test_unavailable_server():
    client = ServerClient(port=8950, token="secret")

    with mock.patch(
        "urllib.request.urlopen",
        side_effect=ConnectionRefusedError("offline"),
    ):
        assert client.available() is False

        with pytest.raises(ServerConnectionError):
            client.status()



def test_remote_server_and_session():
    client = ServerClient(
        host="192.168.1.101",
        port=9000,
        token="remote-secret",
        session_id="living-room",
    )

    with mock.patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ) as urlopen:
        client.status()

    request = urlopen.call_args.args[0]
    assert request.full_url == (
        "http://192.168.1.101:9000"
        "/api/sessions/living-room/status"
    )
    assert request.get_header("X-viz-token") == "remote-secret"


def test_server_metadata_endpoint():
    client = ServerClient(token="secret")

    with mock.patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ) as urlopen:
        client.server_info()

    assert urlopen.call_args.args[0].full_url.endswith("/api/server")


def test_reject_invalid_session_id():
    with pytest.raises(ValueError):
        ServerClient(session_id="../other")
