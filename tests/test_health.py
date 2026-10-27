from vizgui import health


def _fake_has(present):
    return lambda name, fallbacks=(): name in present


def test_nothing_missing_when_all_present():
    cfg = {"targets": [{"type": "cast"}, {"type": "vlc"}]}
    assert health.missing(cfg, _fake_has({"go-chromecast", "ffmpeg", "vlc"})) == []


def test_cast_needs_gochromecast_and_ffmpeg():
    cfg = {"targets": [{"type": "cast"}]}
    tools = [t for t, _ in health.missing(cfg, _fake_has(set()))]
    assert "go-chromecast" in tools and "ffmpeg" in tools
    assert "vlc" not in tools


def test_vlc_target_needs_vlc_only():
    cfg = {"targets": [{"type": "vlc"}]}
    assert [t for t, _ in health.missing(cfg, _fake_has(set()))] == ["vlc"]


def test_empty_targets_needs_nothing():
    assert health.missing({"targets": []}, _fake_has(set())) == []


def test_each_entry_has_a_consequence_string():
    cfg = {"targets": [{"type": "cast"}, {"type": "vlc"}]}
    for tool, why in health.missing(cfg, _fake_has(set())):
        assert isinstance(why, str) and why
