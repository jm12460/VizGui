"""Launch acknowledgements and receiver-confirmed state, without device I/O."""
from unittest.mock import Mock

import pytest

from vizgui import engine, history


@pytest.fixture
def launch(tmp_path, monkeypatch):
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"test media")
    monkeypatch.setattr(history, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(history, "CURRENT_PATH", str(tmp_path / "current.json"))
    monkeypatch.setattr(engine.config, "CFG", {"auto_next": False,
                                             "sources": [{"path": str(tmp_path)}]})
    monkeypatch.setattr(engine.probe, "probe", lambda _: {"chapters": []})
    pending, messages = [], []
    e = engine.Engine(run_async=lambda fn, done=None: pending.append((fn, done)),
                      on_status=messages.append)
    e.active = Mock()
    e.targets = {"cast": e.active}
    e.active.available.return_value = (True, "")
    e.active.start.return_value = "playing"
    e.active.status.return_value = {"idle": True, "busy": True}
    e.target_index = 0
    return e, str(path), pending, messages


def playing(e):
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 60, "dur": 600})


def test_accepted_command_does_not_claim_playback_has_started(launch):
    e, path, pending, _ = launch
    reply = e.web_do("cast", {"path": path, "resume": 60})
    request = e.web_status()["playback_request"]
    assert reply == {"ok": True, "request_id": request["id"], "state": "accepted"}
    assert request["state"] == "starting"
    e.active.start.assert_not_called()
    fn, done = pending.pop(0)
    done(fn())
    assert e.web_status()["playback_request"]["state"] == "starting"
    e._poll_done({"idle": True, "busy": True})
    assert e.web_status()["playback_request"]["state"] == "starting"
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"


@pytest.mark.parametrize("failure", ["device launch failed", {"error": "worker raised an error"}])
def test_launch_error_is_visible_and_not_erased_by_later_polls(launch, failure):
    e, path, pending, messages = launch
    e.cast(path)
    pending.pop(0)[1](failure)
    expected = failure["error"] if isinstance(failure, dict) else failure
    request = e.web_status()["playback_request"]
    assert request["state"] == "failed"
    assert request["error"] == expected
    assert messages[-1] == expected
    e._poll_done({"idle": True, "busy": False})
    playing(e)  # old or external playback must not turn a failed launch into success
    assert e.web_status()["playback_request"] == request


def test_unavailable_device_reports_immediate_failure_without_queuing(launch):
    e, path, pending, _ = launch
    e.active.available.return_value = (False, "Device unavailable")
    reply = e.web_do("cast", {"path": path})
    assert reply["ok"] is False
    assert e.web_status()["playback_request"]["state"] == "failed"
    assert e.web_status()["playback_request"]["error"] == "Device unavailable"
    assert pending == []


def test_superseded_queued_launch_and_callbacks_cannot_overwrite_new_request(launch):
    e, path, pending, _ = launch
    e.cast(path, 10)
    old_start, old_done = pending.pop(0)
    _, old_probe = pending.pop(0)
    first_id = e.web_status()["playback_request"]["id"]
    e.cast(path, 60)
    second_id = e.web_status()["playback_request"]["id"]
    assert second_id != first_id
    old_done(old_start())
    old_probe({"chapters": [{"title": "stale"}]})
    e.active.start.assert_not_called()
    assert e._casting
    assert e._media["chapters"] == []
    fn, done = pending.pop(0)
    done(fn())
    e.active.start.assert_called_once_with(path, [path], False, 60, None, None)
    assert e.web_status()["playback_request"]["id"] == second_id


def test_poll_scheduled_during_launch_cannot_falsely_confirm_new_playback(launch):
    e, path, pending, _ = launch
    e.cast(path)
    _, done = pending.pop(0)
    e.poll_cast()
    _, old_poll = pending.pop()
    done("playing")
    old_poll({"idle": False, "state": "PLAYING", "pos": 10, "dur": 600})
    assert e.web_status()["playback_request"]["state"] == "starting"
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"


def test_confirmation_timeout_reports_failure_without_retrying_and_accepts_late_confirmation(launch, monkeypatch):
    e, path, pending, messages = launch
    clock = [100.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])
    e.cast(path)
    fn, done = pending.pop(0)
    done(fn())
    clock[0] += e.PLAYBACK_CONFIRM_TIMEOUT + 1
    e.tick()
    request = e.web_status()["playback_request"]
    assert request["state"] == "failed"
    assert request["error_code"] == "confirmation_timeout"
    assert "not confirmed" in messages[-1]
    e.tick()
    e.active.start.assert_called_once()
    playing(e)
    assert e.web_status()["playback_request"]["state"] == "playing"
    assert e.web_status()["playback_request"]["error"] is None


@pytest.mark.parametrize("action", ["stop", "shutdown", "target"])
def test_cancelled_pending_launch_cannot_run_after_controller_change(launch, action, monkeypatch):
    e, path, pending, _ = launch
    e.cast(path)
    fn, done = pending.pop(0)
    if action == "target":
        e.targets["vlc"] = Mock()
        monkeypatch.setitem(engine.config.CFG, "targets", [{"type": "cast"}, {"type": "vlc"}])
        e.set_target_index(1)
    else:
        getattr(e, action)()
    done(fn())
    assert e.web_status()["playback_request"]["state"] == "failed"
    e.targets["cast"].start.assert_not_called()


def test_snapshots_are_independent_of_later_request_transitions(launch):
    e, path, pending, _ = launch
    e.cast(path)
    before = e.web_status()["playback_request"]
    pending.pop(0)[1]("Launch failed")
    assert before["state"] == "starting"
    assert e.web_status()["playback_request"]["state"] == "failed"
