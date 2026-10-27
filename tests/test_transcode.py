import os
import tempfile
from unittest import mock

from vizgui import transcode


def _wrapper_text(copy_video, has_nvenc):
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "transcode.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode, "HAS_NVENC", has_nvenc):
            path = transcode.write_wrapper("/m/Show +1 (x).mkv", copy_video=copy_video)
            return open(path).read()


def test_wrapper_remux_copies_video():
    text = _wrapper_text(copy_video=True, has_nvenc=True)
    assert "-c:v copy" in text
    assert "nvenc" not in text and "libx264" not in text
    assert "'/m/Show +1 (x).mkv'" in text   # path is shell-quoted


def test_wrapper_uses_nvenc_when_available():
    text = _wrapper_text(copy_video=False, has_nvenc=True)
    assert "h264_nvenc" in text and "-hwaccel cuda" in text


def test_wrapper_cpu_fallback_without_nvenc():
    text = _wrapper_text(copy_video=False, has_nvenc=False)
    assert "libx264" in text and "nvenc" not in text


def test_wrapper_always_fragmented_mp4_stereo_aac():
    text = _wrapper_text(copy_video=True, has_nvenc=False)
    assert "frag_keyframe" in text and "-f mp4" in text
    assert "-c:a aac" in text and "-ac 2" in text
    assert text.rstrip().endswith("pipe:1")
