import os
import shutil
import subprocess
import tempfile
from unittest import mock

import pytest

from vizgui import transcode


def _wrapper_text(copy_video, hw, **kw):
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "transcode.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=hw):
            path = transcode.write_wrapper("/m/Show +1 (x).mkv", copy_video=copy_video, **kw)
            return open(path).read()


VAAPI = ("-vaapi_device /dev/dri/renderD128", "-c:v h264_vaapi -b:v 8M",
         "format=nv12,hwupload")


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


def test_wrapper_text_sub_burns_via_vf():
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "t.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
            t = open(transcode.write_wrapper("/m/x.mkv", copy_video=True,
                                             sub={"kind": "text", "file": "/c/vizsub.ass"})).read()
    assert "-vf subtitles=/c/vizsub.ass" in t
    assert "-c:v copy" not in t   # a subtitle forces a re-encode


def test_wrapper_image_sub_overlays():
    with tempfile.TemporaryDirectory() as d:
        wp = os.path.join(d, "t.sh")
        with mock.patch.object(transcode, "WRAPPER", wp), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
            t = open(transcode.write_wrapper("/m/x.mkv", sub={"kind": "image", "si": 2})).read()
    assert "overlay" in t and "0:s:2" in t and "-map '[vo]'" in t


def test_wrapper_cpu_fallback_forces_8bit():
    # A 10-bit source would otherwise become High 10 H.264, which Cast can't decode.
    assert "-c:v libx264 -pix_fmt yuv420p" in _wrapper_text(copy_video=False, hw=None)


def test_wrapper_vaapi_uploads_after_decode():
    t = _wrapper_text(copy_video=False, hw=VAAPI)
    assert "-vaapi_device /dev/dri/renderD128 -i '/m/Show +1 (x).mkv'" in t
    assert "-vf format=nv12,hwupload -c:v h264_vaapi" in t


def test_wrapper_vaapi_upload_follows_text_sub_burn():
    t = _wrapper_text(copy_video=False, hw=VAAPI, sub={"kind": "text", "file": "/c/vizsub.ass"})
    assert "-vf subtitles=/c/vizsub.ass,format=nv12,hwupload" in t
    assert t.count("-vf") == 1


def test_wrapper_vaapi_upload_follows_image_sub_overlay():
    t = _wrapper_text(copy_video=False, hw=VAAPI, sub={"kind": "image", "si": 1})
    assert "'[0:v:0][0:s:1]overlay,format=nv12,hwupload[vo]'" in t
    assert "-vf" not in t


def test_wrapper_vaapi_upload_with_audio_map():
    t = _wrapper_text(copy_video=False, hw=VAAPI, audio=1)
    assert "-map 0:v:0 -map 0:a:1 -vf format=nv12,hwupload" in t


def test_1080p_compatibility_scales_before_vaapi_upload_and_after_subtitles():
    scale = 'scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2'
    plain = _wrapper_text(copy_video=False, hw=VAAPI, max_height=1080)
    assert f'-vf {scale},format=nv12,hwupload' in plain
    image = _wrapper_text(copy_video=False, hw=VAAPI, max_height=1080,
                          sub={'kind': 'image', 'si': 1})
    assert f'overlay,{scale},format=nv12,hwupload[vo]' in image
    text = _wrapper_text(copy_video=False, hw=None, max_height=1080,
                         sub={'kind': 'text', 'file': '/c/vizsub.ass'})
    assert f'subtitles=/c/vizsub.ass,{scale}' in text


def test_1080p_compatibility_produces_playable_scaled_video(tmp_path):
    if not _has_encoders('libx264'):
        pytest.skip('needs ffmpeg with libx264')
    source = tmp_path / 'source.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=s=1920x1082:r=1', '-frames:v', '1', '-c:v', 'libx264',
                    '-threads', '2', str(source)], capture_output=True, check=True, timeout=30)
    wrapper = tmp_path / 'transcode.sh'
    with mock.patch.object(transcode, 'WRAPPER', str(wrapper)), \
         mock.patch.object(transcode.platforms, 'hw_encode_args', return_value=None):
        transcode.write_wrapper(str(source), max_height=1080)
    output = tmp_path / 'output.mp4'
    with output.open('wb') as f:
        subprocess.run(['bash', str(wrapper)], stdout=f, stderr=subprocess.PIPE,
                       check=True, timeout=30)
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                             str(output)], capture_output=True, text=True, check=True)
    width, height = map(int, result.stdout.strip().split(','))
    assert width <= 1920 and height <= 1080 and height % 2 == 0 and width % 2 == 0


def test_wrapper_vaapi_remux_needs_no_gpu():
    t = _wrapper_text(copy_video=True, hw=VAAPI)
    assert "-c:v copy" in t and "vaapi" not in t and "hwupload" not in t


def test_wrapper_accepts_two_part_hw_args():
    # macOS/Windows backends still return (input_prefix, codec_args).
    t = _wrapper_text(copy_video=False, hw=("", "-c:v h264_videotoolbox"))
    assert "h264_videotoolbox" in t and "-vf" not in t and "ffmpeg  " not in t


def test_encoder_name():
    with mock.patch.object(transcode.platforms, "hw_encode_args", return_value=VAAPI):
        assert transcode.encoder_name() == "h264_vaapi"
    with mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
        assert transcode.encoder_name() == "cpu"


def _has_encoders(*names):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("bash")):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    return all(n in out for n in names)


@pytest.mark.skipif(not _has_encoders("libx264", "libx265"),
                    reason="needs ffmpeg/ffprobe with libx264 and libx265")
def test_cpu_transcode_of_10bit_hevc_outputs_8bit_h264(tmp_path):
    src = tmp_path / "ten bit (x).mkv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24", "-t", "1",
                    "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                    "-x265-params", "log-level=error", str(src)], check=True, timeout=60)
    wp = tmp_path / "transcode.sh"
    with mock.patch.object(transcode, "WRAPPER", str(wp)), \
         mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
        transcode.write_wrapper(str(src), copy_video=False)
    out = tmp_path / "out.mp4"
    with open(out, "wb") as f:
        subprocess.run(["bash", str(wp)], stdout=f, check=True, timeout=60)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True, check=True).stdout
    assert probe.strip() == "h264,yuv420p"
