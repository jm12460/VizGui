from vizgui.helpers import fmt_time, parse_status, cast_plan, discovery_plan


def test_fmt_time_minutes():
    assert fmt_time(0) == "0:00"
    assert fmt_time(5) == "0:05"
    assert fmt_time(65) == "1:05"
    assert fmt_time(600) == "10:00"


def test_fmt_time_hours():
    assert fmt_time(3600) == "1:00:00"
    assert fmt_time(3661) == "1:01:01"


def test_cast_plan_direct():
    assert cast_plan("h264", "mp4") == "direct"
    assert cast_plan("h264", ".MOV") == "direct"
    assert cast_plan("h264", "m4v") == "direct"


def test_cast_plan_remux():
    assert cast_plan("h264", "mkv") == "remux"
    assert cast_plan("h264", "avi") == "remux"
    assert cast_plan("h264", "ts") == "remux"


def test_cast_plan_transcode():
    assert cast_plan("hevc", "mkv") == "transcode"
    assert cast_plan("vp9", "webm") == "transcode"
    assert cast_plan("", "mp4") == "transcode"        # unknown codec
    assert cast_plan(None, "mp4") == "transcode"


def test_parse_status_playing():
    line = ("Default Media Receiver (PAUSED), "
            "[http://x?media_file=/p/Show.S01E02.mp4&live_streaming=false], "
            "time remaining=31s/1298s, volume=0.23, muted=false")
    d = parse_status(line)
    assert d["state"] == "PAUSED"
    assert d["pos"] == 31 and d["dur"] == 1298
    assert d["file"] == "Show.S01E02.mp4"


def test_parse_status_idle():
    d = parse_status("Idle (Backdrop), volume=0.17 muted=false")
    assert d["state"] == "IDLE"
    assert "pos" not in d


# --- discovery_plan (Cast auto-discovery decision) ---
_TV = {"name": "Living Room", "addr": "10.0.0.105", "cast_port": "8009"}


def test_discovery_fresh_autoadds_everything():
    to_add, msg = discovery_plan([_TV], [])
    assert to_add == [_TV]
    assert "Found and added" in msg


def test_discovery_does_not_autoadd_when_a_cast_target_exists():
    targets = [{"type": "cast", "addr": "10.0.0.50"}]
    to_add, msg = discovery_plan([_TV], targets)
    assert to_add == []
    assert "Also on your network" in msg


def test_discovery_all_known_is_silent():
    targets = [{"type": "cast", "addr": "10.0.0.105"}]
    to_add, msg = discovery_plan([_TV], targets)
    assert to_add == [] and msg == ""


def test_discovery_none_found_hint():
    to_add, msg = discovery_plan([], [])
    assert to_add == [] and "No Cast devices found" in msg


def test_discovery_handles_error_dict():
    to_add, msg = discovery_plan({"error": "boom"}, [])
    assert to_add == [] and "No Cast devices found" in msg


def test_discovery_vlc_target_does_not_count_as_cast():
    # a VLC-only setup is still "fresh" for Cast, so a found TV auto-adds
    to_add, _ = discovery_plan([_TV], [{"type": "vlc"}])
    assert to_add == [_TV]
