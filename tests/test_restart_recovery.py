"""Restart recovery against temporary persisted state and fake Cast processes."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from vizgui import engine, history, transcode
from vizgui.playback import media, targets


@pytest.fixture(params=["direct", "remux", "transcode"])
def playback(tmp_path, monkeypatch, request):
    plan = request.param
    path = tmp_path / ("Episode + 1.mp4" if plan == "direct" else "Episode + 1.mkv")
    path.write_bytes(b"test media placeholder")
    monkeypatch.setattr(history, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(history, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(history, "CURRENT_PATH", str(tmp_path / "current.json"))
    monkeypatch.setattr(transcode, "WRAPPER", str(tmp_path / "transcode.sh"))
    monkeypatch.setattr(engine.config, "CFG", {"auto_next": False})
    monkeypatch.setattr(targets.media, "vcodec", lambda _: "hevc" if plan == "transcode" else "h264")
    monkeypatch.setattr(targets.media, "symlink_serve", lambda p: str(tmp_path / media.safe_name(p)))
    monkeypatch.setattr(targets.platforms, "has_tool", lambda *args: True)
    monkeypatch.setattr(targets.platforms, "hw_encode_args", lambda: None)
    monkeypatch.setattr(targets, "gc", Mock(return_value=(True, "")))
    monkeypatch.setattr(targets.time, "sleep", lambda _: None)
    launch = Mock(side_effect=lambda *args, **kwargs:
                  Mock(poll=lambda: None, pid=1, returncode=0))
    monkeypatch.setattr(targets.platforms, "launch_caster", launch)
    monkeypatch.setattr(engine.probe, "probe", lambda _: {"audio": [], "subs": [], "chapters": []})

    def make(role="owner"):
        pending, messages = [], []
        e = engine.Engine(run_async=lambda fn, done=None: pending.append((fn, done)),
                          on_status=messages.append, playback_role=role)
        return SimpleNamespace(engine=e, pending=pending, messages=messages)

    def receiver(position=120, state="PLAYING"):
        return {"idle": False, "file": media.safe_name(str(path)) if plan == "direct" else "pipe_output",
                "pos": position, "dur": 1800 if plan == "direct" else 0,
                "live": plan != "direct", "state": state}

    def checkpoint():
        old = make()
        old.engine.cast(str(path), resume=600)
        fn, done = old.pending.pop(0)
        assert fn() == "playing"
        done("playing")
        # Direct Cast reports absolute time; a live stream reports time since -ss.
        old.engine._poll_done(receiver(720 if plan == "direct" else 120))
        old.engine.shutdown()
        assert history.resume_pos(str(path)) == 720
        assert history.current()["path"] == str(path)
        launch.reset_mock()
        return old

    return SimpleNamespace(plan=plan, path=str(path), make=make, receiver=receiver,
                           checkpoint=checkpoint, launch=launch)


def run_start(restarted):
    fn, done = restarted.pending.pop(0)
    result = fn()
    done(result)
    return result


def test_restart_recovers_source_and_absolute_position_once(playback):
    playback.checkpoint()
    fresh = playback.make()
    state = playback.receiver(720 if playback.plan == "direct" else 120)
    fresh.engine._poll_done(state)
    assert fresh.engine._now_path == playback.path
    assert fresh.engine._resume_base == 720
    assert fresh.engine._casting
    assert run_start(fresh) == "playing"
    argv = playback.launch.call_args.args[0]
    if playback.plan == "direct":
        assert argv[argv.index("--start-time") + 1] == "720"
    else:
        text = Path(argv[-1]).read_text()
        assert "-ss 720 -i" in text
        assert ("-c:v copy" in text) == (playback.plan == "remux")
    # Repeated receiver polls must not enqueue another reattachment.
    fresh.engine._poll_done(state)
    assert playback.launch.call_count == 1
    assert len(fresh.pending) == 2  # media probe and post-launch status only
    assert history.current()["resume_base"] == 720


def test_second_restart_does_not_double_count_live_resume_base(playback):
    playback.checkpoint()
    fresh = playback.make()
    fresh.engine._poll_done(playback.receiver(720 if playback.plan == "direct" else 120))
    run_start(fresh)
    fresh.engine._poll_done(playback.receiver(750 if playback.plan == "direct" else 30))
    fresh.engine.shutdown()
    assert history.resume_pos(playback.path) == 750
    again = playback.make()
    again.engine._poll_done(playback.receiver(750 if playback.plan == "direct" else 30))
    assert again.engine._resume_base == 750


def test_restart_does_not_unpause_receiver_automatically(playback):
    playback.checkpoint()
    fresh = playback.make()
    position = 720 if playback.plan == "direct" else 120
    fresh.engine._poll_done(playback.receiver(position, "PAUSED"))
    assert fresh.pending == []
    playback.launch.assert_not_called()
    assert history.resume_pos(playback.path) == 720
    # Once the receiver is playing again, recover at that same saved timeline.
    fresh.engine._poll_done(playback.receiver(position, "PLAYING"))
    assert fresh.engine._resume_base == 720
    assert run_start(fresh) == "playing"
    playback.launch.assert_called_once()


def test_expired_receiver_buffer_keeps_history_without_auto_start(playback):
    playback.checkpoint()
    fresh = playback.make()
    fresh.engine._poll_done({"idle": True, "busy": False})
    assert fresh.pending == []
    playback.launch.assert_not_called()
    assert fresh.engine._now_path is None
    assert history.resume_pos(playback.path) == 720  # available for explicit Continue Watching


def test_recovery_failure_reports_error_without_relaunch_loop(playback):
    playback.checkpoint()
    playback.launch.side_effect = lambda *args, **kwargs: Mock(poll=lambda: 9, pid=2, returncode=9)
    fresh = playback.make()
    fresh.engine._poll_done(playback.receiver(720 if playback.plan == "direct" else 120))
    result = run_start(fresh)
    assert "exited" in result
    assert any("exited" in message for message in fresh.messages)
    fresh.engine._poll_done(playback.receiver(720 if playback.plan == "direct" else 120))
    assert playback.launch.call_count == 1
    assert history.resume_pos(playback.path) == 720


def test_missing_source_is_not_recovered(playback):
    playback.checkpoint()
    Path(playback.path).unlink()
    fresh = playback.make()
    fresh.engine._poll_done(playback.receiver())
    assert fresh.pending == []
    assert fresh.engine._now_path is None
    playback.launch.assert_not_called()


def test_observer_restart_does_not_take_ownership(playback):
    playback.checkpoint()
    before = history.current()
    observer = playback.make("observer")
    observer.engine._poll_done(playback.receiver())
    observer.engine.shutdown()
    assert observer.pending == []
    assert history.current() == before
    assert history.resume_pos(playback.path) == 720
    playback.launch.assert_not_called()


def test_unidentified_live_stream_is_not_guessed_from_history(playback):
    playback.checkpoint()
    history.clear_current()
    fresh = playback.make()
    fresh.engine._poll_done({"idle": False, "file": "pipe_output", "live": True,
                             "pos": 120, "dur": 0, "state": "PLAYING"})
    assert fresh.pending == []
    assert fresh.engine._now_path is None
    playback.launch.assert_not_called()
