import os
import tempfile
from unittest import mock

from vizgui import history


def _tmp():
    d = tempfile.mkdtemp()
    return os.path.join(d, "history.json")


def test_record_and_resume_roundtrip():
    with mock.patch.object(history, "HISTORY_PATH", _tmp()):
        history.record("/m/show.mkv", 600, 3600, "Show")
        assert history.resume_pos("/m/show.mkv") == 600
        assert history.resume_pos("/m/unknown.mkv") == 0


def test_finished_is_dropped():
    with mock.patch.object(history, "HISTORY_PATH", _tmp()):
        history.record("/m/a.mkv", 300, 3600, "A")       # kept
        history.record("/m/a.mkv", 3500, 3600, "A")      # >92% -> finished, drop
        assert history.resume_pos("/m/a.mkv") == 0
        assert history.recent(exists=lambda p: True) == []


def test_barely_started_is_ignored():
    with mock.patch.object(history, "HISTORY_PATH", _tmp()):
        history.record("/m/a.mkv", 5, 3600, "A")
        assert history.resume_pos("/m/a.mkv") == 0


def test_recent_sorted_and_filters_missing():
    with mock.patch.object(history, "HISTORY_PATH", _tmp()):
        history.record("/m/old.mkv", 100, 3600, "Old", now=1000)
        history.record("/m/new.mkv", 200, 3600, "New", now=2000)
        history.record("/m/gone.mkv", 300, 3600, "Gone", now=3000)
        got = history.recent(exists=lambda p: p != "/m/gone.mkv")
        assert [x["title"] for x in got] == ["New", "Old"]   # newest first, gone filtered
        assert got[0]["pos"] == 200


def test_forget_and_clear():
    with mock.patch.object(history, "HISTORY_PATH", _tmp()):
        history.record("/m/a.mkv", 100, 3600, "A")
        history.record("/m/b.mkv", 100, 3600, "B")
        history.forget("/m/a.mkv")
        assert history.resume_pos("/m/a.mkv") == 0 and history.resume_pos("/m/b.mkv") == 100
        history.clear()
        assert history.recent(exists=lambda p: True) == []


def test_load_missing_file_is_empty():
    with mock.patch.object(history, "HISTORY_PATH", "/no/such/history.json"):
        assert history.load() == {}
        assert history.resume_pos("/m/x.mkv") == 0


from vizgui.playback.media import safe_name   # noqa: E402

_EXISTS = lambda p: True


def test_current_roundtrip():
    import os
    d = tempfile.mkdtemp()
    with mock.patch.object(history, "CURRENT_PATH", os.path.join(d, "cur.json")), \
         mock.patch.object(history, "CONFIG_DIR", d):
        assert history.current() is None
        history.set_current("/m/Show S01E01.mkv", "Show", 300)
        c = history.current()
        assert c["path"].endswith("Show S01E01.mkv") and c["resume_base"] == 300 and c["title"] == "Show"
        history.clear_current()
        assert history.current() is None


def test_recover_direct_via_saved_sanitized_name():
    saved = {"path": "/m/Show +1.mkv", "title": "Show", "resume_base": 0}
    m = history.recover_match("Show__1.mkv", saved, [], safe=safe_name, exists=_EXISTS)
    assert m["path"] == "/m/Show +1.mkv" and m["reattach"] is True


def test_recover_pipe_via_saved_uses_resume_base():
    saved = {"path": "/m/Movie.mkv", "title": "Movie", "resume_base": 600}
    m = history.recover_match("pipe_output", saved, [], safe=safe_name, exists=_EXISTS)
    assert m["path"] == "/m/Movie.mkv" and m["resume_base"] == 600 and m["reattach"] is True


def test_recover_pipe_without_saved_refuses_to_guess():
    recent = [{"path": "/m/Ep.mkv", "title": "Ep"}]
    assert history.recover_match("pipe_output", None, recent, safe=safe_name, exists=_EXISTS) is None


def test_recover_pipe_saved_missing_file_refuses():
    saved = {"path": "/m/Gone.mkv", "title": "Gone", "resume_base": 0}
    assert history.recover_match("pipe_output", saved, [], safe=safe_name, exists=lambda p: False) is None


def test_recover_direct_via_history_when_no_saved():
    recent = [{"path": "/m/Family Guy S22E01.mp4", "title": "FG"}]
    m = history.recover_match("Family_Guy_S22E01.mp4", None, recent, safe=safe_name, exists=_EXISTS)
    assert m["path"] == "/m/Family Guy S22E01.mp4" and m["resume_base"] == 0 and m["reattach"] is True


def test_recover_no_match_returns_none():
    recent = [{"path": "/m/other.mp4", "title": "o"}]
    assert history.recover_match("Unknown.mp4", None, recent, safe=safe_name, exists=_EXISTS) is None
