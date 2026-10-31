"""HDR sources: detection, tone mapping to SDR, and filter ordering."""
import json
import logging
import os
import shutil
import subprocess
import types
from unittest import mock

import pytest

from vizgui import transcode
from vizgui.playback import media, targets

FIT = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2"
VAAPI = ("-vaapi_device /dev/dri/renderD128", "-c:v h264_vaapi -b:v 8M",
         "format=nv12,hwupload")
SDR_TAGS = "-color_primaries bt709 -color_trc bt709 -colorspace bt709"


def _wrapper(tmp_path, hw=None, tonemap_ok=True, **kw):
    wp = tmp_path / "transcode.sh"
    with mock.patch.object(transcode, "WRAPPER", str(wp)), \
         mock.patch.object(transcode.platforms, "hw_encode_args", return_value=hw), \
         mock.patch.object(transcode, "can_tonemap", return_value=tonemap_ok):
        transcode.write_wrapper("/m/Movie (2160p).mkv", **kw)
    return wp.read_text()


# ---- detection ----

def _ffprobe(stream):
    out = json.dumps({"streams": [stream]})
    return mock.patch.object(media.subprocess, "run",
                             return_value=types.SimpleNamespace(stdout=out, returncode=0))


@pytest.mark.parametrize("transfer, hdr", [("smpte2084", "smpte2084"),
                                           ("arib-std-b67", "arib-std-b67"),
                                           ("bt709", None), (None, None)])
def test_video_info_reports_hdr_transfer(transfer, hdr):
    stream = {"width": 3840, "height": 2160}
    if transfer:
        stream["color_transfer"] = transfer
    with _ffprobe(stream):
        assert media.video_info("/m/x.mkv") == {"size": (3840, 2160), "hdr": hdr}


def test_video_info_keeps_hdr_when_size_is_missing():
    with _ffprobe({"color_transfer": "smpte2084"}):
        assert media.video_info("/m/x.mkv") == {"size": None, "hdr": "smpte2084"}


def test_video_info_none_when_ffprobe_fails():
    with mock.patch.object(media.subprocess, "run",
                           side_effect=subprocess.CalledProcessError(1, "ffprobe")):
        assert media.video_info("/m/x.mkv") is None
        assert media.video_size("/m/x.mkv") is None


def test_ffmpeg_filter_list_is_parsed_and_failures_are_not_cached(monkeypatch):
    monkeypatch.setattr(transcode, "_FILTERS", None)
    monkeypatch.setattr(transcode.subprocess, "run", mock.Mock(side_effect=OSError))
    assert transcode.can_tonemap() is False
    out = ("Filters:\n  T.. = Timeline support\n ------\n"
           " .SC zscale            V->V       Apply resizing\n"
           " .S. tonemap           V->V       Conversion to/from different dynamic ranges.\n")
    monkeypatch.setattr(transcode.subprocess, "run",
                        mock.Mock(return_value=types.SimpleNamespace(stdout=out)))
    assert transcode.can_tonemap() is True


def test_can_tonemap_needs_zscale(monkeypatch):
    monkeypatch.setattr(transcode, "_FILTERS", frozenset({"tonemap", "scale"}))
    assert transcode.can_tonemap() is False


# ---- filter chains ----

def test_hdr_transcode_tone_maps_and_tags_sdr(tmp_path):
    t = _wrapper(tmp_path, hdr="smpte2084")
    assert "-vf zscale=tin=smpte2084:pin=bt2020:min=bt2020nc:t=linear:npl=100," in t
    assert "tonemap=tonemap=hable" in t and "zscale=t=bt709:m=bt709:r=tv,format=yuv420p" in t
    assert SDR_TAGS in t


def test_hlg_source_uses_hlg_input_transfer(tmp_path):
    assert "zscale=tin=arib-std-b67:" in _wrapper(tmp_path, hdr="arib-std-b67")


def test_sdr_transcode_is_unchanged(tmp_path):
    t = _wrapper(tmp_path)
    assert "zscale" not in t and "-color_trc" not in t and "-vf" not in t


def test_hdr_remux_is_left_alone(tmp_path):
    t = _wrapper(tmp_path, copy_video=True, hdr="smpte2084")
    assert "-c:v copy" in t and "zscale" not in t and "-color_trc" not in t


def test_hdr_without_zscale_falls_back_and_warns(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="viz-gui"):
        t = _wrapper(tmp_path, tonemap_ok=False, hdr="smpte2084")
    assert "zscale" not in t and "-color_trc" not in t
    assert "washed out" in caplog.text


def test_hdr_1080p_scales_before_tone_mapping(tmp_path):
    t = _wrapper(tmp_path, hw=VAAPI, hdr="smpte2084", max_height=1080)
    vf = t.split("-vf ", 1)[1].split(" ", 1)[0]
    assert vf.startswith(FIT + ",zscale=tin=smpte2084")
    assert vf.endswith("format=yuv420p,format=nv12,hwupload")


def test_hdr_text_subtitles_burn_after_tone_mapping(tmp_path):
    t = _wrapper(tmp_path, hw=VAAPI, hdr="smpte2084", max_height=1080, audio=1,
                 sub={"kind": "text", "file": "/c/vizsub.ass"})
    assert "-map 0:v:0 -map 0:a:1 -vf " + FIT + ",zscale=" in t
    assert "format=yuv420p,subtitles=/c/vizsub.ass,format=nv12,hwupload" in t
    assert t.count("-vf") == 1


def test_hdr_image_subtitles_scale_with_video_before_overlay(tmp_path):
    t = _wrapper(tmp_path, hw=VAAPI, hdr="smpte2084", max_height=1080,
                 sub={"kind": "image", "si": 1})
    graph = t.split("-filter_complex '", 1)[1].split("'", 1)[0]
    video, subs, overlay = graph.split(";")
    assert video.startswith("[0:v:0]" + FIT + ",zscale=") and video.endswith("[base]")
    assert subs == f"[0:s:1]{FIT}[subs]"
    assert overlay == "[base][subs]overlay,format=nv12,hwupload[vo]"
    assert "-map '[vo]' -map 0:a:0" in t


def test_hdr_image_subtitles_at_source_size(tmp_path):
    t = _wrapper(tmp_path, hdr="smpte2084", sub={"kind": "image", "si": 0})
    graph = t.split("-filter_complex '", 1)[1].split("'", 1)[0]
    assert graph.startswith("[0:v:0]zscale=tin=smpte2084")
    assert graph.endswith("[base];[base][0:s:0]overlay[vo]")


# ---- Cast target wiring ----

def _start(path, vcodec, info, **kw):
    t = targets.CastTarget()
    seen = {}

    def fake_ww(path, copy_video=False, start=0, audio=None, sub=None, max_height=None,
                hdr=None):
        seen.update(copy_video=copy_video, hdr=hdr)
        return "/tmp/w.sh"

    probe = mock.Mock(return_value=info)
    with mock.patch.object(targets, "gc", return_value=(True, "")), \
         mock.patch.object(targets.media, "vcodec", return_value=vcodec), \
         mock.patch.object(targets.media, "video_info", probe), \
         mock.patch.object(targets.prepared, "prepare", return_value=None), \
         mock.patch.object(targets, "write_wrapper", fake_ww), \
         mock.patch.object(targets, "can_tonemap", return_value=True), \
         mock.patch.object(targets.platforms, "has_tool", return_value=True), \
         mock.patch.object(targets.platforms, "launch_caster",
                           return_value=mock.Mock(poll=lambda: None, pid=1, returncode=0)), \
         mock.patch.object(targets.time, "sleep", lambda *a: None), \
         mock.patch.object(targets.log, "info") as log_info:
        assert t.start(path, [path], False, **kw) == "playing"
    return seen, probe, log_info


def test_cast_passes_hdr_to_transcode_and_logs_it():
    seen, probe, log_info = _start("/m/x.mkv", "hevc",
                                   {"size": (3840, 2160), "hdr": "smpte2084"})
    probe.assert_called_once_with("/m/x.mkv")
    assert seen == {"copy_video": False, "hdr": "smpte2084"}
    assert any("HDR to SDR" in str(c) for c in log_info.call_args_list)


def test_cast_sdr_transcode_passes_no_hdr():
    seen, _, _ = _start("/m/x.mkv", "hevc", {"size": (1920, 1080), "hdr": None})
    assert seen["hdr"] is None


def test_cast_remux_does_not_probe_color():
    seen, probe, _ = _start("/m/x.mkv", "h264", {"size": (3840, 2160), "hdr": "smpte2084"},
                            audio=1)
    probe.assert_not_called()
    assert seen == {"copy_video": True, "hdr": None}


# ---- real ffmpeg ----

def _ffmpeg_ready():
    if not all(shutil.which(t) for t in ("ffmpeg", "ffprobe", "bash")):
        return False
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    flt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                         capture_output=True, text=True).stdout
    return "libx264" in enc and "libx265" in enc and " zscale " in flt


def _saturation(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-f", "lavfi",
                          f"movie={path},signalstats", "-show_entries",
                          "frame_tags=lavfi.signalstats.SATAVG", "-of", "csv=p=0"],
                         capture_output=True, text=True, check=True).stdout.split()
    values = [float(v.strip(",")) for v in out if v.strip(",")]
    return sum(values) / len(values)


@pytest.mark.skipif(not _ffmpeg_ready(), reason="needs ffmpeg with libx264, libx265 and zscale")
def test_real_hdr10_transcode_restores_sdr_color(tmp_path):
    # An SDR colour-bar pattern converted to genuine PQ/BT.2020, then 10-bit HEVC.
    src = tmp_path / "hdr10.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
         "-t", "0.5", "-vf",
         "format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:"
         "range=tv,zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt2020,"
         "zscale=t=smpte2084:m=bt2020nc:r=tv:npl=203,format=yuv420p10le",
         "-c:v", "libx265", "-x265-params", "log-level=error", "-color_primaries", "bt2020",
         "-color_trc", "smpte2084", "-colorspace", "bt2020nc", str(src)],
        check=True, timeout=120)
    assert media.video_info(str(src))["hdr"] == "smpte2084"

    def transcode_to(name, **kw):
        wp, out = tmp_path / f"{name}.sh", tmp_path / f"{name}.mp4"
        with mock.patch.object(transcode, "WRAPPER", str(wp)), \
             mock.patch.object(transcode.platforms, "hw_encode_args", return_value=None):
            transcode.write_wrapper(str(src), **kw)
        with open(out, "wb") as f:
            subprocess.run(["bash", str(wp)], stdout=f, check=True, timeout=120)
        return out

    mapped = transcode_to("mapped", hdr="smpte2084")
    naive = transcode_to("naive")
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=pix_fmt,color_primaries,color_transfer,color_space",
                           "-of", "csv=p=0", str(mapped)],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert tags == "yuv420p,bt709,bt709,bt709"
    # Untone-mapped PQ shown as SDR is grey and flat; tone mapping restores colour.
    assert _saturation(mapped) > 1.5 * _saturation(naive)
