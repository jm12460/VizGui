"""Observer-mode Cast safety: never modify a server-owned playback session."""
from unittest import mock
import pytest
from vizgui import engine as E


def observer():
    return E.Engine(playback_role="observer")


def test_invalid_role():
    with pytest.raises(ValueError):
        E.Engine(playback_role="something-else")


def test_observer_poll_never_recovers_or_writes_history():
    e = observer()
    with mock.patch.object(e, "recover_cast_item") as recover, \
         mock.patch.object(e, "on_ended") as ended, \
         mock.patch.object(E.history, "record") as record, \
         mock.patch.object(E.history, "clear_current") as clear, \
         mock.patch.object(E.history, "set_current") as current:
        e._poll_done({"idle": False, "state": "PLAYING", "pos": 30,
                      "dur": 100, "file": "pipe_output", "live": True})
        assert e._display_title == "Live cast"
        assert e._np_pos == 30
        e._poll_done({"idle": True})
        e.remember()
        recover.assert_not_called()
        ended.assert_not_called()
        record.assert_not_called()
        clear.assert_not_called()
        current.assert_not_called()


def test_observer_blocks_all_cast_actions():
    e = observer()
    e._now_path = "/media/example.mp4"
    with mock.patch.object(e.active, "start") as start, \
         mock.patch.object(e.active, "stop") as stop, \
         mock.patch.object(e.active, "pause") as pause, \
         mock.patch.object(e.active, "seek") as seek, \
         mock.patch.object(e.active, "seek_to") as seek_to, \
         mock.patch.object(e.active, "shutdown") as shutdown, \
         mock.patch.object(E.history, "set_current") as current, \
         mock.patch.object(E.history, "clear_current") as clear:
        e.cast("/media/other.mp4")
        e.stop()
        e.playpause()
        e.rewind()
        e.forward()
        e.jump_to(10)
        e.go_next()
        e.go_prev()
        e.select_audio(1)
        e.select_sub({"i": 0})
        e.on_ended()
        e.transport(lambda: (_ for _ in ()).throw(AssertionError("ran")))
        for fn in (start, stop, pause, seek, seek_to, shutdown, current, clear):
            fn.assert_not_called()


def test_observer_does_not_change_autonext_or_history():
    e = observer()
    old = e.auto_next
    with mock.patch.object(E, "save_config") as save, \
         mock.patch.object(E.history, "forget") as forget, \
         mock.patch.object(E.history, "clear") as clear:
        e.set_autonext(not old)
        e.web_do("forget", {"path": "/media/a.mp4"})
        e.web_do("clear", {})
        assert e.auto_next == old
        save.assert_not_called()
        forget.assert_not_called()
        clear.assert_not_called()


def test_observer_vlc_remains_independent():
    e = observer()
    e.target = "vlc"
    e.active = e.targets["vlc"]
    with mock.patch.object(e.active, "pause", return_value=(True, "")) as pause:
        assert e.playpause() == (True, "")
        pause.assert_called_once_with()


def test_owner_default_unchanged():
    assert E.Engine().playback_role == "owner"


def test_observer_shutdown_does_not_change_cast_playback():
    e = observer()

    with mock.patch.object(E.history, "record") as record, \
         mock.patch.object(E.history, "clear_current") as clear, \
         mock.patch.object(E.history, "set_current") as current, \
         mock.patch.object(e.targets["cast"], "shutdown") as cast_shutdown:
        e.shutdown()

    record.assert_not_called()
    clear.assert_not_called()
    current.assert_not_called()
    cast_shutdown.assert_called_once()
