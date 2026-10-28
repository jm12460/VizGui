from vizgui.playback.targets import VlcTarget


def test_item_opts_audio_only():
    assert VlcTarget._item_opts(1, None) == [":audio-track=1"]


def test_item_opts_embedded_sub():
    assert VlcTarget._item_opts(None, {"si": 2}) == [":sub-track=2"]


def test_item_opts_external_sub():
    assert VlcTarget._item_opts(0, {"external": "/m/Show.en.srt"}) == \
        [":audio-track=0", ":sub-file=/m/Show.en.srt"]


def test_item_opts_none():
    assert VlcTarget._item_opts(None, None) == []


def test_item_opts_external_takes_priority_over_si():
    # an external sidecar sub has a path but no stream index to burn
    assert VlcTarget._item_opts(None, {"external": "/m/a.srt", "si": None}) == \
        [":sub-file=/m/a.srt"]
