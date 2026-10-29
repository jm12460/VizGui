"""Deterministic reconnect tests: workers and responses are delivered manually."""
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from vizgui import remote_controller as module


@pytest.fixture
def controller(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(module, "run_async", lambda fn, done: pending.append((fn, done)))
    client = Mock()
    remote = module.RemoteController(client)
    remote.start()
    yield remote, client, pending
    remote.close()
    assert app is not None


SERVER = {"ok": True, "id": "installation-a", "instance_id": "process-a"}


def deliver(pending, result):
    if result.get("ok") is True:
        result = {"server": SERVER, **result}
    _, done = pending.pop(0)
    done(result)


def test_disconnect_preserves_snapshot_and_reconnects(controller):
    remote, client, pending = controller
    states, snapshots = [], []
    remote.connection_changed.connect(states.append)
    remote.status_changed.connect(snapshots.append)
    first = {"ok": True, "now": {"title": "Movie", "pos": 10}}
    deliver(pending, first)
    remote.refresh()
    deliver(pending, {"error": "offline"})
    assert not remote.connected
    assert remote.snapshot == {"server": SERVER, **first}
    assert remote.timer.interval() == 4000
    for _ in range(6):
        remote.refresh()
        deliver(pending, {"error": "offline"})
    assert remote.timer.interval() == remote.MAX_RETRY_INTERVAL
    fresh = {"ok": True, "now": {"title": "Movie", "pos": 80}}
    remote.refresh()
    deliver(pending, fresh)
    assert remote.connected
    assert remote.snapshot == {"server": SERVER, **fresh}
    assert remote.timer.interval() == remote.POLL_INTERVAL
    assert states == [True, False, True]
    assert snapshots == [{"server": SERVER, **s} for s in (first, fresh)]
    client.command_for_server.assert_not_called()


def test_commands_are_not_queued_while_disconnected(controller):
    remote, client, pending = controller
    remote.transport("stop")
    assert len(pending) == 1  # only initial status request
    deliver(pending, {"ok": True})
    assert pending == []
    client.command_for_server.assert_not_called()


@pytest.mark.parametrize("transition", ["disconnect", "close"])
def test_queued_command_is_discarded_after_connection_changes(controller, transition):
    remote, client, pending = controller
    deliver(pending, {"ok": True})
    remote.transport("playpause")
    send, done = pending.pop()
    if transition == "close":
        remote.close()
        remote.start()
    else:
        remote.refresh()
        deliver(pending, {"error": "offline"})
        remote.refresh()
    deliver(pending, {"ok": True})
    done(send())
    client.command_for_server.assert_not_called()
    assert pending == []


def test_close_and_restart_ignore_old_status_callback(controller):
    remote, client, pending = controller
    _, old_done = pending.pop()
    remote.close()
    remote.start()
    old_done({"ok": True, "now": {"title": "stale"}})
    assert remote.snapshot == {}
    assert not remote.connected
    assert remote._polling
    remote.refresh()
    assert len(pending) == 1
    deliver(pending, {"ok": True, "now": {"title": "fresh"}})
    assert remote.snapshot["now"]["title"] == "fresh"
    client.command_for_server.assert_not_called()


def test_uncertain_command_is_not_retried(controller):
    remote, client, pending = controller
    deliver(pending, {"ok": True})
    client.command_for_server.return_value = {"error": "response lost"}
    remote.transport("playpause")
    send, done = pending.pop()
    done(send())
    client.command_for_server.assert_called_once_with(SERVER, "transport", action="playpause")
    assert len(pending) == 1  # refresh status, never resend the toggle
    deliver(pending, {"ok": True})
    client.command_for_server.assert_called_once()


def test_closed_controller_ignores_pending_callbacks(controller):
    remote, client, pending = controller
    remote.close()
    deliver(pending, {"ok": True})
    remote.transport("stop")
    remote.refresh()
    assert not remote.connected
    assert remote.snapshot == {}
    assert pending == []
    client.command_for_server.assert_not_called()


def test_restart_without_observed_outage_discards_queued_command(controller):
    remote, client, pending = controller
    restarts = []
    remote.server_restarted.connect(lambda: restarts.append(True))
    deliver(pending, {"ok": True})
    remote.transport("stop")
    send, done = pending.pop()
    remote.refresh()
    replacement = {**SERVER, "instance_id": "process-b"}
    deliver(pending, {"ok": True, "server": replacement, "now": {"pos": 50}})
    done(send())
    assert restarts == [True]
    assert remote.connected
    assert remote.snapshot["now"]["pos"] == 50
    client.command_for_server.assert_not_called()
    remote.transport("playpause")
    send, done = pending.pop()
    client.command_for_server.return_value = {"ok": True}
    done(send())
    client.command_for_server.assert_called_once_with(
        replacement, "transport", action="playpause")


def test_different_installation_is_not_automatically_trusted(controller):
    remote, client, pending = controller
    errors = []
    remote.error.connect(errors.append)
    deliver(pending, {"ok": True, "now": {"title": "original"}})
    remote.refresh()
    deliver(pending, {"ok": True, "server": {**SERVER, "id": "other"}})
    assert not remote.connected
    assert remote.snapshot["now"]["title"] == "original"
    assert "different VizGui server" in errors[-1]
    remote.transport("stop")
    client.command_for_server.assert_not_called()
    remote.refresh()
    deliver(pending, {"ok": True})
    assert remote.connected


@pytest.mark.parametrize("identity", [None, {}, {"ok": True, "id": 42},
                                     {**SERVER, "instance_id": ""}])
def test_invalid_identity_never_enables_commands(controller, identity):
    remote, client, pending = controller
    deliver(pending, {"ok": True, "server": identity})
    remote.transport("stop")
    assert not remote.connected
    client.command_for_server.assert_not_called()


def test_older_server_identity_without_instance_is_supported(controller):
    remote, _, pending = controller
    legacy = {"ok": True, "id": "installation-a"}
    deliver(pending, {"ok": True, "server": legacy})
    assert remote.connected
    remote.refresh()
    deliver(pending, {"ok": True, "server": legacy})
    assert remote.connected
