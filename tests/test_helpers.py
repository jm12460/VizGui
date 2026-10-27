from vizgui.helpers import fmt_time, parse_status, cast_plan


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
