"""Explicit recovery checkpoints: temporary files, no playback device I/O."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from vizgui import engine, history


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"test media")
    monkeypatch.setattr(history, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(history, "CURRENT_PATH", str(tmp_path / "current.json"))
    monkeypatch.setattr(engine.config, "CFG", {"sources": [{"path": str(tmp_path)}],
                                             "auto_next": False})
    history.record(str(media), 420, 1200, "Episode")
    history.set_current(str(media), "Episode", 300)
    pending = []
    e = engine.Engine(run_async=lambda fn, done=None: pending.append((fn, done)))
    e.target_index = 0
    e.active = Mock()
    e.active.available.return_value = (True, "")
    e.active.start.return_value = "playing"
    e.active.status.return_value = {"idle": True, "busy": False}
    monkeypatch.setattr(engine.probe, "probe", lambda _: {"chapters": []})
    return e, media, pending


def idle(e):
    e._poll_done({"idle": True, "busy": False})
    return e.web_status()["interrupted"]


def test_idle_startup_preserves_exact_checkpoint_without_starting(recovery):
    e, media, pending = recovery
    entry = idle(e)
    assert entry["path"] == str(media)
    assert entry["pos"] == 420  # recorded absolute time, not the old resume base
    assert history.current() is None
    assert history.interrupted() == entry
    assert pending == []
    e.active.start.assert_not_called()
    assert idle(e) == entry


def test_checkpoint_survives_multiple_restarts(recovery):
    e, _, _ = recovery
    first = idle(e)
    for _ in range(2):
        fresh = engine.Engine(run_async=Mock())
        fresh.target_index = 0
        assert idle(fresh) == first


def test_history_alone_never_creates_recovery_offer(recovery):
    e, _, pending = recovery
    history.clear_current()
    assert idle(e) is None
    assert pending == []


def test_missing_file_does_not_create_recovery_offer(recovery):
    e, media, _ = recovery
    media.unlink()
    assert idle(e) is None


def test_busy_receiver_does_not_capture_or_offer_checkpoint(recovery):
    e, _, pending = recovery
    e._poll_done({"idle": True, "busy": True})
    assert e.web_status()["interrupted"] is None
    assert history.interrupted() is None
    assert history.current() is not None
    assert pending == []


def test_resume_requires_current_checkpoint_and_target(recovery):
    e, media, pending = recovery
    entry = idle(e)
    for checkpoint, target in (("stale", 0), (entry["id"], 1), (entry["id"], True)):
        assert e.resume_interrupted(checkpoint, target)["ok"] is False
    assert pending == []
    accepted = e.resume_interrupted(entry["id"], 0)
    assert accepted["ok"] is True
    assert accepted["state"] == "accepted"
    assert accepted["request_id"] == e.web_status()["playback_request"]["id"]
    assert e._resume_base == 420
    assert e._now_path == str(media)
    assert e.web_status()["interrupted"] is None
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert len(pending) == 2  # one launch and one media probe
    fn, done = pending.pop(0)
    done(fn())
    e.active.start.assert_called_once_with(str(media), [str(media)], False, 420, None, None)
    assert history.interrupted() == entry  # keep until receiver confirms playback
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 420, "dur": 1200})
    assert history.interrupted() is None


@pytest.mark.parametrize("change", ["missing", "outside_sources"])
def test_resume_revalidates_media_before_start(recovery, monkeypatch, change):
    e, media, pending = recovery
    entry = idle(e)
    if change == "missing":
        media.unlink()
    else:
        monkeypatch.setattr(engine.config, "CFG", {"sources": []})
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert pending == []
    e.active.start.assert_not_called()


def test_another_client_starting_playback_invalidates_offer(recovery):
    e, media, pending = recovery
    entry = idle(e)
    e.cast(str(media), 100)
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert len(pending) == 2


def test_dismiss_is_shared_and_keeps_continue_watching(recovery):
    e, media, pending = recovery
    entry = idle(e)
    assert not e.dismiss_interrupted("stale", 0)["ok"]
    assert not e.dismiss_interrupted(entry["id"], 1)["ok"]
    assert e.web_status()["interrupted"] == entry
    assert e.web_do("dismiss_interrupted", {"id": entry["id"], "target_index": 0})["ok"]
    assert e.web_status()["interrupted"] is None
    assert history.interrupted() is None
    assert str(media) in history.load()
    assert not e.resume_interrupted(entry["id"], 0)["ok"]
    assert not e.dismiss_interrupted(entry["id"], 0)["ok"]
    assert pending == []
    e.active.start.assert_not_called()
    fresh = engine.Engine(run_async=Mock())
    fresh.target_index = 0
    assert idle(fresh) is None


def test_failed_resume_preserves_checkpoint_and_can_be_retried(recovery):
    e, _, pending = recovery
    entry = idle(e)
    e.active.start.return_value = "test launch failure"
    assert e.resume_interrupted(entry["id"], 0)["ok"] is True
    fn, done = pending.pop(0)
    done(fn())
    assert history.interrupted() == entry
    assert idle(e) == entry
    pending.clear()
    assert e.resume_interrupted(entry["id"], 0)["ok"] is True
    assert len(pending) == 2


def test_unavailable_device_preserves_checkpoint(recovery):
    e, _, pending = recovery
    entry = idle(e)
    e.active.available.return_value = (False, "Device unavailable")
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert history.interrupted() == entry
    assert pending == []


@pytest.mark.parametrize("action", ["clear", "forget", "stop"])
def test_explicit_stop_or_history_removal_clears_checkpoint(recovery, action):
    e, media, _ = recovery
    entry = idle(e)
    if action == "stop":
        e.stop()
    else:
        e.web_do(action, {"path": str(media)})
    assert e.web_status()["interrupted"] is None
    assert history.interrupted() is None
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False


def test_observer_cannot_capture_or_resume_interruption(recovery):
    e, _, pending = recovery
    entry = idle(e)
    e.playback_role = "observer"
    assert e.web_status()["interrupted"] is None
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert pending == []


def test_malformed_checkpoint_is_ignored(recovery):
    _, _, _ = recovery
    checkpoint = Path(history.CURRENT_PATH).with_name("interrupted_playback.json")
    checkpoint.write_text('{"id":"test", "path":"/test", "pos":"invalid"}')
    assert history.interrupted() is None


def test_stopping_observer_local_vlc_does_not_clear_server_checkpoint(recovery):
    e, _, _ = recovery
    entry = idle(e)
    e.playback_role = "observer"
    e.target = "vlc"
    e.stop()
    assert history.interrupted() == entry


def test_malformed_current_source_cannot_create_offer(recovery):
    e, _, _ = recovery
    Path(history.CURRENT_PATH).write_text('{"path": ["invalid"]}')
    assert idle(e) is None


def test_failed_automatic_reattachment_keeps_manual_recovery_offer(recovery):
    from vizgui.playback.media import safe_name
    e, media, pending = recovery
    e.active.start.return_value = "test launch failure"
    e._poll_done({"idle": False, "state": "PLAYING", "file": safe_name(str(media)),
                  "pos": 480, "dur": 1200, "live": False})
    fn, done = pending.pop(0)
    done(fn())
    entry = idle(e)
    assert entry["path"] == str(media)
    assert entry["pos"] == 480
    assert history.interrupted() == entry


def test_status_failure_hides_offer_until_receiver_idle_is_confirmed(recovery):
    e, _, _ = recovery
    entry = idle(e)
    e._poll_done({"idle": True, "busy": True, "error": "temporary failure"})
    assert e.web_status()["interrupted"] is None
    assert e.resume_interrupted(entry["id"], 0)["ok"] is False
    assert idle(e) == entry


def test_receiver_outage_freezes_clock_without_issuing_playback_commands(recovery):
    e, _, pending = recovery
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 80, "dur": 600})
    e._poll_done({"idle": True, "busy": True, "error": "unreachable"})
    assert e.web_status()["receiver_unreachable"] is True
    for _ in range(20):
        e.tick()
    assert e.web_status()["now"]["pos"] == 80
    assert pending == []
    e.active.start.assert_not_called()
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 82, "dur": 600})
    assert e.web_status()["receiver_unreachable"] is False
    e.tick()
    assert e.web_status()["now"]["pos"] == 83


def test_interruption_preserves_known_position_when_duration_is_unknown(recovery):
    e, media, _ = recovery
    # A receiver can report a position before it reports the media duration.
    e._now_path = str(media)
    e.now_title = "Episode"
    e.now_live = False
    e._np_pos = 30
    e._np_dur = 0
    e.shutdown()
    fresh = engine.Engine(run_async=Mock())
    fresh.target_index = 0
    entry = idle(fresh)
    assert entry is not None
    assert entry["path"] == str(media)
    assert entry["pos"] == 30


def test_resumed_transcode_does_not_use_stream_duration_to_discard_history(recovery):
    e, media, pending = recovery
    entry = idle(e)
    assert e.resume_interrupted(entry["id"], 0)["ok"]
    fn, done = pending.pop(0)
    done(fn())
    e._poll_done({"idle": False, "state": "PLAYING", "pos": 60,
                  "dur": 100, "live": True})
    e.shutdown()
    assert history.resume_pos(str(media)) == 480
    fresh = engine.Engine(run_async=Mock())
    fresh.target_index = 0
    assert idle(fresh)["pos"] == 480


def test_new_launch_does_not_save_stale_duration_with_zero_position(recovery):
    e, media, _ = recovery
    entry = idle(e)
    e._np_dur = 1200
    assert e.resume_interrupted(entry["id"], 0)["ok"]
    e.shutdown()
    assert history.resume_pos(str(media)) == 420


@pytest.mark.parametrize("action,base,position,expected", [("forward", 420, 12, 462),
                                                            ("rewind", 420, 12, 402),
                                                            ("rewind", 10, 2, 0)])
def test_live_relative_transport_recasts_at_absolute_position(recovery, action, base, position, expected):
    e, media, pending = recovery
    e._now_path = str(media)
    e._resume_base = base
    e._np_pos = position
    e.now_live = True
    e._audio = 2
    e._sub = {"i": 3}
    e.cast = Mock()
    e.transport(getattr(e, action))
    e.cast.assert_called_once_with(str(media), expected, audio=2, sub={"i": 3})
    e.active.seek.assert_not_called()
    assert pending == []


def test_live_status_reports_absolute_episode_position(recovery):
    e, _, _ = recovery
    e._resume_base = 420
    e._np_pos = 12
    e.now_live = True
    assert e.playback_position() == 432
    assert e.web_status()["now"]["pos"] == 432
    e.now_live = False
    assert e.web_status()["now"]["pos"] == 12
    e.playback_role = "observer"
    e.now_live = True
    assert e.playback_position() == 12
