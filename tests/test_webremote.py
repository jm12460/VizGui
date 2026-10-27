import os
import tempfile
from unittest import mock

from vizgui import webremote, config


def test_web_browse_lists_videos_and_dirs():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "Season 1"))
        open(os.path.join(d, "ep1.mp4"), "w").close()
        open(os.path.join(d, "notes.txt"), "w").close()     # non-video -> excluded
        open(os.path.join(d, ".hidden.mkv"), "w").close()   # dotfile -> excluded
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_browse(0, "")
    assert r["ok"] is True
    names = {e["name"] for e in r["entries"]}
    assert "ep1.mp4" in names and "Season 1" in names
    assert "notes.txt" not in names and ".hidden.mkv" not in names
    assert r["up"] is None   # at the root


def test_web_browse_descends_and_has_up():
    with tempfile.TemporaryDirectory() as d:
        sub = os.path.join(d, "S1")
        os.makedirs(sub)
        open(os.path.join(sub, "a.mkv"), "w").close()
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_browse(0, "S1")
    assert r["ok"] is True
    assert {e["name"] for e in r["entries"]} == {"a.mkv"}
    assert r["up"] == ""   # parent is the source root


def test_web_browse_traversal_blocked():
    with tempfile.TemporaryDirectory() as d:
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_browse(0, "../../../etc")
    assert r["ok"] is False


def test_web_browse_bad_source_index():
    with mock.patch.object(config, "CFG", {"sources": []}):
        assert webremote.web_browse(0, "")["ok"] is False
