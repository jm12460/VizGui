from vizgui import probe

SAMPLE = {
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "index": 0},
        {"codec_type": "audio", "index": 1, "codec_name": "aac", "channels": 6,
         "tags": {"language": "eng", "title": "Surround"}, "disposition": {"default": 1}},
        {"codec_type": "audio", "index": 2, "codec_name": "ac3", "channels": 2,
         "tags": {"language": "spa"}, "disposition": {"default": 0}},
        {"codec_type": "subtitle", "index": 3, "codec_name": "subrip",
         "tags": {"language": "eng"}, "disposition": {"default": 0, "forced": 1}},
    ],
    "chapters": [
        {"start_time": "0.000000", "tags": {"title": "Intro"}},
        {"start_time": "125.5", "tags": {"title": "Act 1"}},
        {"start_time": "600"},
    ],
}


def test_parse_audio_tracks():
    a = probe.parse(SAMPLE)["audio"]
    assert len(a) == 2
    assert a[0]["label"].startswith("eng") and "Surround" in a[0]["label"] and "5.1" in a[0]["label"]
    assert a[0]["default"] is True
    assert a[1]["label"].startswith("spa") and "stereo" in a[1]["label"]


def test_parse_subtitles():
    s = probe.parse(SAMPLE)["subs"]
    assert len(s) == 1
    assert s[0]["forced"] is True and s[0]["external"] is None and s[0]["label"].startswith("eng")


def test_parse_chapters():
    c = probe.parse(SAMPLE)["chapters"]
    assert [x["start"] for x in c] == [0, 125, 600]
    assert c[0]["title"] == "Intro" and c[2]["title"] == "Chapter 3"


def test_external_subs_discovered():
    files = ["Show.mkv", "Show.en.srt", "Show.es.srt", "Other.srt", "Show.txt"]
    s = probe.parse({"streams": []}, path="/m/Show.mkv", listdir=lambda d: files)["subs"]
    assert [x["label"] for x in s] == ["Show.en.srt", "Show.es.srt"]
    assert all(x["external"] for x in s)


def test_parse_empty_on_no_data():
    assert probe.parse({}) == {"audio": [], "subs": [], "chapters": []}


def test_parse_subtitle_kind_and_si():
    data = {"streams": [
        {"codec_type": "subtitle", "index": 2, "codec_name": "subrip",
         "tags": {"language": "eng"}, "disposition": {}},
        {"codec_type": "subtitle", "index": 3, "codec_name": "hdmv_pgs_subtitle",
         "tags": {"language": "eng"}, "disposition": {}},
    ]}
    s = probe.parse(data)["subs"]
    assert s[0]["kind"] == "text" and s[0]["si"] == 0
    assert s[1]["kind"] == "image" and s[1]["si"] == 1
