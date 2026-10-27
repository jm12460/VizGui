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
