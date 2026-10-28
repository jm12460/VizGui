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


def test_web_path_ok_confines_to_sources():
    with tempfile.TemporaryDirectory() as d:
        inside = os.path.join(d, "ep1.mp4")
        open(inside, "w").close()
        with tempfile.TemporaryDirectory() as other:
            outside = os.path.join(other, "secret.mp4")
            open(outside, "w").close()
            with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
                assert webremote.web_path_ok(inside) is True
                assert webremote.web_path_ok(outside) is False      # outside any source
                assert webremote.web_path_ok(os.path.join(d, "nope.mp4")) is False  # not a file
                assert webremote.web_path_ok("") is False


def test_web_path_ok_blocks_traversal_out_of_source():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "ep.mp4"), "w").close()
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            # a crafted ../ path that resolves outside the root is rejected
            assert webremote.web_path_ok(os.path.join(d, "..", "etc", "passwd")) is False


def test_web_search_recursive_substring_match():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "Season 1"))
        os.makedirs(os.path.join(d, "Season 2"))
        open(os.path.join(d, "Season 1", "Alien.mkv"), "w").close()
        open(os.path.join(d, "Season 2", "aliens.mp4"), "w").close()  # case-insensitive
        open(os.path.join(d, "Season 2", "Predator.mp4"), "w").close()
        open(os.path.join(d, "Season 1", "notes.txt"), "w").close()   # not a video
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_search(0, "alien")
    assert r["ok"] is True
    names = sorted(e["name"] for e in r["entries"])
    assert names == ["Alien.mkv", "aliens.mp4"]
    # folder is relative to the source root, path is absolute within it
    by_name = {e["name"]: e for e in r["entries"]}
    assert by_name["Alien.mkv"]["folder"] == "Season 1"
    assert by_name["Alien.mkv"]["path"].endswith("Season 1/Alien.mkv")


def test_web_search_empty_query_returns_nothing():
    with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": "/x"}]}):
        r = webremote.web_search(0, "   ")
    assert r["ok"] is True and r["entries"] == []


def test_web_search_bad_source_index():
    with mock.patch.object(config, "CFG", {"sources": []}):
        assert webremote.web_search(0, "x")["ok"] is False


def test_web_search_truncates_at_limit():
    with tempfile.TemporaryDirectory() as d:
        for i in range(5):
            open(os.path.join(d, f"movie{i}.mp4"), "w").close()
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_search(0, "movie", limit=3)
    assert r["ok"] is True and len(r["entries"]) == 3 and r.get("truncated") is True


def test_web_search_results_pass_path_confinement():
    # every search hit must be castable (web_path_ok must accept it)
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "sub"))
        open(os.path.join(d, "sub", "clip.mp4"), "w").close()
        with mock.patch.object(config, "CFG", {"sources": [{"name": "T", "path": d}]}):
            r = webremote.web_search(0, "clip")
            assert all(webremote.web_path_ok(e["path"]) for e in r["entries"])
