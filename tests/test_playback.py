import os
import tempfile
from unittest import mock

from vizgui.playback import media, targets


# ---- media helpers ----
def test_folder_queue_from_middle():
    with tempfile.TemporaryDirectory() as d:
        for n in ["e01.mkv", "e02.mkv", "e03.mkv", "notes.txt"]:
            open(os.path.join(d, n), "w").close()
        q = media.folder_queue(os.path.join(d, "e02.mkv"))
    assert [os.path.basename(x) for x in q] == ["e02.mkv", "e03.mkv"]


def test_next_and_prev_file():
    with tempfile.TemporaryDirectory() as d:
        for n in ["a.mp4", "b.mp4", "c.mp4"]:
            open(os.path.join(d, n), "w").close()
        b = os.path.join(d, "b.mp4")
        assert os.path.basename(media.next_file(b)) == "c.mp4"
        assert os.path.basename(media.prev_file(b)) == "a.mp4"
        assert media.next_file(os.path.join(d, "c.mp4")) is None
        assert media.prev_file(os.path.join(d, "a.mp4")) is None


def test_symlink_serve_sanitizes_name():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "Show +1 (x).mkv")
        open(src, "w").close()
        with mock.patch.object(media, "CACHE", d):
            link = media.symlink_serve(src)
            assert os.path.basename(link) == "Show__1__x_.mkv"
            assert os.path.realpath(link) == os.path.realpath(src)


# ---- CastTarget ----
def test_cast_status_idle():
    t = targets.CastTarget()
    with mock.patch.object(targets, "gc", return_value=(True, "Idle (Backdrop), volume=0.1")):
        assert t.status()["idle"] is True


def test_cast_status_playing_carries_live():
    t = targets.CastTarget(); t.live = True
    line = ("Default Media Receiver (PLAYING), [http://x?media_file=/p/x.mp4&], "
            "time remaining=10s/100s")
    with mock.patch.object(targets, "gc", return_value=(True, line)):
        st = t.status()
    assert st["idle"] is False and st["state"] == "PLAYING" and st["pos"] == 10
    assert st["live"] is True


def test_cast_seek_maps_to_rewind_and_seek():
    t = targets.CastTarget()
    with mock.patch.object(targets, "gc") as g:
        t.seek(-30)
        t.seek(30)
    assert g.call_args_list[0].args == ("rewind", "30")
    assert g.call_args_list[1].args == ("seek", "30")


def test_cast_contract_flags():
    t = targets.CastTarget()
    assert t.needs_advance_on_end() is True
    assert t.native_skip() is False


# ---- VlcTarget ----
def test_vlc_status_stopped_is_idle():
    t = targets.VlcTarget()
    with mock.patch.object(targets, "vlc_any_status",
                           return_value={"state": "STOPPED", "_mode": "http"}):
        assert t.status()["idle"] is True


def test_vlc_status_playing_sets_mode():
    t = targets.VlcTarget()
    with mock.patch.object(targets, "vlc_any_status",
                           return_value={"state": "PLAYING", "pos": 3, "dur": 50,
                                         "file": "f.mkv", "_mode": "http"}):
        st = t.status()
    assert st["idle"] is False and st["file"] == "f.mkv" and t.mode == "http"


def test_vlc_seek_http_vs_mpris():
    t = targets.VlcTarget()
    t.mode = "http"
    with mock.patch.object(targets, "vlc_cmd") as vc:
        t.seek(30); t.seek(-30)
    assert vc.call_args_list[0].args[0] == "seek&val=+30"
    assert vc.call_args_list[1].args[0] == "seek&val=-30"
    t.mode = "mpris"
    with mock.patch.object(targets, "mpris_cmd") as mc:
        t.seek(30); t.seek(-30)
    assert mc.call_args_list[0].args == ("position", "30+")
    assert mc.call_args_list[1].args == ("position", "30-")


def test_vlc_native_skip_and_advance_depend_on_mode():
    t = targets.VlcTarget()
    t.mode = "http"
    assert t.native_skip() is True and t.needs_advance_on_end() is False
    t.mode = "mpris"
    assert t.native_skip() is False and t.needs_advance_on_end() is True


def test_cast_direct_resume_seeks_to():
    t = targets.CastTarget()
    calls = []
    with mock.patch.object(targets, "gc", side_effect=lambda *a, **k: calls.append(a) or (True, "")), \
         mock.patch.object(targets.media, "vcodec", return_value="h264"), \
         mock.patch.object(targets.media, "symlink_serve", return_value="/cache/x.mp4"), \
         mock.patch.object(targets.platforms, "launch_caster",
                           return_value=mock.Mock(poll=lambda: None, pid=5, returncode=0)), \
         mock.patch.object(targets.time, "sleep", lambda *a: None):
        res = t.start("/m/x.mp4", ["/m/x.mp4"], False, resume=600)
    assert res == "playing"
    assert ("seek-to", "600") in calls


def test_vlc_launch_includes_start_time_on_resume():
    t = targets.VlcTarget()
    captured = {}

    def fake_launch(args, log_path=None):
        captured["args"] = args
        return mock.Mock(poll=lambda: None, pid=1)

    with mock.patch.object(targets, "vlc_status",
                           side_effect=[{"error": "x"}, {"state": "PLAYING"}]), \
         mock.patch.object(targets.platforms, "supports_mpris", return_value=False), \
         mock.patch.object(targets.platforms, "launch_detached", fake_launch), \
         mock.patch.object(targets.platforms, "vlc_exe", return_value="vlc"), \
         mock.patch.object(targets.time, "sleep", lambda *a: None):
        res = t.start("/m/x.mkv", ["/m/x.mkv"], False, resume=300)
    assert res == "playing"
    assert "--start-time=300" in captured["args"]


def test_cast_audio_forces_remux_on_direct():
    t = targets.CastTarget()
    ww = {}

    def fake_ww(path, copy_video=False, start=0, audio=None, sub=None):
        ww.update(copy_video=copy_video, audio=audio, sub=sub)
        return "/tmp/w.sh"

    with mock.patch.object(targets, "gc", return_value=(True, "")), \
         mock.patch.object(targets.media, "vcodec", return_value="h264"), \
         mock.patch.object(targets, "write_wrapper", fake_ww), \
         mock.patch.object(targets.platforms, "has_tool", return_value=True), \
         mock.patch.object(targets.platforms, "launch_caster",
                           return_value=mock.Mock(poll=lambda: None, pid=1, returncode=0)), \
         mock.patch.object(targets.time, "sleep", lambda *a: None):
        res = t.start("/m/x.mp4", ["/m/x.mp4"], False, resume=0, audio=1)
    assert res == "playing" and t.live is True
    assert ww.get("copy_video") is True and ww.get("audio") == 1


def test_cast_sub_forces_transcode():
    t = targets.CastTarget()
    ww = {}

    def fake_ww(path, copy_video=False, start=0, audio=None, sub=None):
        ww.update(copy_video=copy_video, sub=sub)
        return "/tmp/w.sh"

    with mock.patch.object(targets, "gc", return_value=(True, "")), \
         mock.patch.object(targets.media, "vcodec", return_value="h264"), \
         mock.patch.object(targets, "write_wrapper", fake_ww), \
         mock.patch.object(targets, "prepare_subtitle", return_value={"kind": "text", "file": "/c/s.ass"}), \
         mock.patch.object(targets.platforms, "has_tool", return_value=True), \
         mock.patch.object(targets.platforms, "launch_caster",
                           return_value=mock.Mock(poll=lambda: None, pid=1, returncode=0)), \
         mock.patch.object(targets.time, "sleep", lambda *a: None):
        res = t.start("/m/x.mp4", ["/m/x.mp4"], False, sub={"i": 0, "kind": "text", "si": 0})
    assert res == "playing" and t.live is True
    assert ww.get("copy_video") is False and ww.get("sub") == {"kind": "text", "file": "/c/s.ass"}


def test_vlc_own_vlc_stays_http_never_mpris():
    t = targets.VlcTarget()
    t.proc = mock.Mock(poll=lambda: None)    # we launched it, still alive
    t.mode = "mpris"                         # a prior poll had flipped it
    with mock.patch.object(targets, "vlc_status",
                           return_value={"state": "PLAYING", "pos": 5, "dur": 50, "file": "f.mkv"}), \
         mock.patch.object(targets, "vlc_any_status",
                           side_effect=AssertionError("must not consult MPRIS for our own VLC")):
        st = t.status()
    assert st["idle"] is False and t.mode == "http"


def test_vlc_own_http_hiccup_is_busy_not_idle():
    t = targets.VlcTarget()
    t.proc = mock.Mock(poll=lambda: None)
    with mock.patch.object(targets, "vlc_status", return_value={"error": "timeout"}):
        st = t.status()
    assert st["idle"] is True and st["busy"] is True   # transient, not ended
