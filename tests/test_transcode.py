import os
import tempfile
from unittest import mock

from vizgui import transcode


def _wrapper_text(copy_video, hw):
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "transcode.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=hw):
            path = transcode.write_wrapper("/m/Show +1 (x).mkv", copy_video=copy_video)
            return open(path).read()


def test_wrapper_remux_copies_video():
    text = _wrapper_text(copy_video=True, hw=("-hwaccel cuda", "-c:v h264_nvenc -preset p4"))
    assert "-c:v copy" in text
    assert "nvenc" not in text and "libx264" not in text
    assert "'/m/Show +1 (x).mkv'" in text   # path is shell-quoted


def test_wrapper_uses_hw_encoder():
    text = _wrapper_text(copy_video=False, hw=("-hwaccel cuda", "-c:v h264_nvenc -preset p4"))
    assert "h264_nvenc" in text and "-hwaccel cuda" in text


def test_wrapper_cpu_fallback_without_hw():
    text = _wrapper_text(copy_video=False, hw=None)
    assert "libx264" in text and "nvenc" not in text


def test_wrapper_always_fragmented_mp4_stereo_aac():
    text = _wrapper_text(copy_video=True, hw=None)
    assert "frag_keyframe" in text and "-f mp4" in text
    assert "-c:a aac" in text and "-ac 2" in text
    assert text.rstrip().endswith("pipe:1")


def test_wrapper_resume_adds_input_seek():
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "transcode.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
            assert "-ss 600 -i" in open(transcode.write_wrapper("/m/x.mkv", False, start=600)).read()
            assert "-ss 90 -i" in open(transcode.write_wrapper("/m/x.mkv", True, start=90)).read()
            assert "-ss" not in open(transcode.write_wrapper("/m/x.mkv", True, start=0)).read()


def test_wrapper_audio_map():
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "t.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
            assert "-map 0:v:0 -map 0:a:2" in open(transcode.write_wrapper("/m/x.mkv", True, audio=2)).read()
            assert "-map 0:a" not in open(transcode.write_wrapper("/m/x.mkv", True)).read()
