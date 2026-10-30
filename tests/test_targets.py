import pytest

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


# Resume must belong to one input, never become a default for the whole folder.
# VLC's per-input ':option' arguments follow the input they belong to:
# https://github.com/videolan/vlc/blob/3.0.x/src/libvlc.c (GetFilenames).

@pytest.mark.parametrize("resume", [0, 420])
def test_fresh_vlc_resume_does_not_apply_to_next_episode(monkeypatch, resume):
    from unittest.mock import Mock
    from vizgui.playback import targets

    episodes = ["/media/Show/episode 1.mkv", "/media/Show/episode 2.mkv"]
    monkeypatch.setattr(targets, "vlc_status", Mock(side_effect=[{"error": "offline"}, {}]))
    monkeypatch.setattr(targets.platforms, "supports_mpris", lambda: False)
    monkeypatch.setattr(targets.platforms, "vlc_exe", lambda: "test-vlc")
    monkeypatch.setattr(targets.time, "sleep", lambda _: None)
    process = Mock()
    process.poll.return_value = None
    launch = Mock(return_value=process)
    monkeypatch.setattr(targets.platforms, "launch_detached", launch)
    target = VlcTarget()
    assert target.start(episodes[0], episodes, True, resume=resume) == "playing"
    argv = launch.call_args.args[0]
    # Parse only the input section using VLC's preceding-input option rule.
    global_start = next((int(arg.split("=", 1)[1]) for arg in argv
                         if arg.startswith("--start-time=")), 0)
    starts = {}
    current = None
    for arg in argv[argv.index(episodes[0]):]:
        if arg.startswith(":start-time="):
            starts[current] = int(arg.split("=", 1)[1])
        elif not arg.startswith(":"):
            current = arg
            starts[current] = global_start
    assert starts == {episodes[0]: resume, episodes[1]: 0}


@pytest.mark.parametrize("resume", [0, 420])
def test_reused_vlc_overrides_old_global_start_time_for_every_episode(monkeypatch, resume):
    from unittest.mock import Mock
    import urllib.parse
    from vizgui.playback import targets

    episodes = ["/media/Show/episode 1.mkv", "/media/Show/episode 2.mkv",
                "/media/Show/episode 3.mkv"]
    monkeypatch.setattr(targets, "vlc_status", lambda: {})
    command = Mock(return_value=(True, ""))
    monkeypatch.setattr(targets, "vlc_cmd", command)
    monkeypatch.setattr(targets.time, "sleep", lambda _: None)
    launch = Mock(side_effect=AssertionError("must reuse existing VLC"))
    monkeypatch.setattr(targets.platforms, "launch_detached", launch)
    target = VlcTarget()
    assert target.start(episodes[0], episodes, True, resume=resume) == "playing"
    commands = [call.args[0] for call in command.call_args_list]
    queued = [urllib.parse.parse_qs(text) for text in commands
              if text.startswith(("in_play&", "in_enqueue&"))]
    assert [item["input"][0] for item in queued] == episodes
    assert all(":start-time=0" in item.get("option", []) for item in queued)
    assert [c for c in commands if c.startswith("seek&")] == (
        [f"seek&val={resume}"] if resume else [])
