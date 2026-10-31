"""The live-transcode wrapper script go-chromecast runs. The video encoder is
chosen by the platform layer (hardware if available, else software libx264)."""
import os
import shlex
import subprocess

from . import platforms
from .paths import WRAPPER, log


def encoder_name():
    """Video encoder a live transcode will use, e.g. 'h264_vaapi' or 'cpu' (for
    status/logging). Probed lazily on first use, not at import."""
    hw = platforms.hw_encode_args()
    if not hw:
        return "cpu"
    args = hw[1].split()
    return args[args.index("-c:v") + 1] if "-c:v" in args[:-1] else "hw"


_SUB_FILE = os.path.join(os.path.dirname(WRAPPER), "vizsub.ass")   # safe burn path

_FIT_1080 = "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2"
_SDR_TAGS = " -color_primaries bt709 -color_trc bt709 -colorspace bt709"
_FILTERS = None


def _ffmpeg_filters():
    """Names of the filters this ffmpeg build has (cached once it has answered)."""
    global _FILTERS
    if _FILTERS is None:
        try:
            out = subprocess.run([platforms.find_tool("ffmpeg"), "-hide_banner", "-filters"],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return frozenset()
        _FILTERS = frozenset(parts[1] for parts in (line.split() for line in out.splitlines())
                             if len(parts) >= 3)
    return _FILTERS


def can_tonemap():
    """True if ffmpeg can convert HDR to SDR (zscale needs ffmpeg built with zimg)."""
    filters = _ffmpeg_filters()
    return "zscale" in filters and "tonemap" in filters


def _tonemap_filter(transfer):
    """HDR (PQ or HLG, BT.2020) -> 8-bit SDR BT.709 via linear light. Without this,
    HDR re-encoded to H.264 keeps PQ/HLG values and looks washed out on the TV."""
    return (f"zscale=tin={transfer}:pin=bt2020:min=bt2020nc:t=linear:npl=100,"
            "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def prepare_subtitle(media_path, sub):
    """Make a subtitle ready to burn. Image subs (PGS/VOBSUB) are composited by
    stream index and need no prep. Text subs are extracted (embedded) or converted
    (external) to a safe-named .ass so the burn filter never has to escape a path.
    -> descriptor for write_wrapper ({kind:'image',si} / {kind:'text',file}), or None."""
    if not sub:
        return None
    if sub.get("kind") == "image" and sub.get("si") is not None:
        return {"kind": "image", "si": int(sub["si"])}
    try:
        if os.path.exists(_SUB_FILE):
            os.remove(_SUB_FILE)          # never reuse a stale sub if this fails
    except OSError:
        pass
    try:
        ff = platforms.find_tool("ffmpeg")
        if sub.get("external"):
            cmd = [ff, "-y", "-i", sub["external"], _SUB_FILE]
        elif sub.get("si") is not None:
            cmd = [ff, "-y", "-i", media_path, "-map", f"0:s:{int(sub['si'])}", _SUB_FILE]
        else:
            return None
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode == 0 and os.path.isfile(_SUB_FILE) and os.path.getsize(_SUB_FILE) > 0:
            return {"kind": "text", "file": _SUB_FILE}
        log.warning("subtitle prep failed (rc=%s): %s", r.returncode,
                    r.stderr.decode("utf-8", "replace")[-200:] if r.stderr else "")
    except Exception as e:
        log.warning("subtitle prep failed: %s", e)
    return None


def _mid_args(audio, sub, upload="", max_height=None, tonemap=None):
    """ffmpeg maps + video filters for a chosen audio track and/or subtitle.
    ``upload`` (e.g. VAAPI's format=nv12,hwupload) must run last, after any
    burn-in, so it ends whichever filter chain is used.

    SDR: burn-in, then the 1080p scale. HDR (``tonemap`` = PQ/HLG transfer): scale
    first, then tone-map, then burn in, so the costly linear-light conversion
    works on a quarter of the pixels and subtitles are drawn on SDR frames.
    [..] are quoted because bash would glob them."""
    fit = _FIT_1080 if max_height == 1080 else ""
    if tonemap:
        pre = [f for f in (fit, _tonemap_filter(tonemap)) if f]
        post = [upload] if upload else []
    else:
        pre = []
        post = [f for f in (fit, upload) if f]
    tail = "," + ",".join(post) if post else ""
    if sub and sub.get("kind") == "image":
        si = int(sub["si"])
        a = int(audio) if audio is not None else 0
        if pre:
            graph = f"[0:v:0]{','.join(pre)}[base];"
            if fit:   # scale the subtitle canvas with the video so positions line up
                graph += f"[0:s:{si}]{fit}[subs];[base][subs]"
            else:
                graph += f"[base][0:s:{si}]"
            graph += f"overlay{tail}[vo]"
        else:
            graph = f"[0:v:0][0:s:{si}]overlay{tail}[vo]"
        return f" -filter_complex {shlex.quote(graph)} -map {shlex.quote('[vo]')} -map 0:a:{a}"
    if sub:                                   # text subtitle burned via -vf
        chain = pre + ["subtitles=" + sub["file"]] + post
        vf = f" -vf {shlex.quote(','.join(chain))}"
        return (f" -map 0:v:0 -map 0:a:{int(audio)}{vf}") if audio is not None else vf
    chain = pre + post
    vf = f" -vf {shlex.quote(','.join(chain))}" if chain else ""
    if audio is not None:
        return f" -map 0:v:0 -map 0:a:{int(audio)}{vf}"
    return vf


def write_wrapper(path, copy_video=False, start=0, audio=None, sub=None, max_height=None,
                  hdr=None):
    """Write the helper go-chromecast runs for live streaming. The source path is
    embedded (shell-quoted, so spaces/+/() are safe) and the helper streams the
    video + stereo AAC as fragmented mp4 to stdout. Returns the script path
    (space-free, under the cache dir, since go-chromecast splits --command on spaces).

    copy_video=True: the video is already H.264, so just repackage it (``-c:v copy``)
    - a near-free live REMUX (no re-encode) for H.264 in a container go-chromecast
    can't serve directly (e.g. mkv). Otherwise re-encode to H.264 using the
    platform's hardware encoder, or software libx264 as a fallback.

    hdr: the source's PQ/HLG transfer (see media.video_info); a re-encode then
    tone-maps to SDR BT.709 when ffmpeg can, since Cast H.264 is SDR."""
    if sub:
        copy_video = False          # burning a subtitle requires re-encoding the video
    q = shlex.quote(os.path.abspath(path))
    ss = f"-ss {int(start)} " if start else ""   # input seek, for resume
    upload = ""
    if copy_video:
        vin, venc = f"{ss}-i {q}", "-c:v copy"
    else:
        hw = platforms.hw_encode_args()
        if hw:
            vin = f"{hw[0]} {ss}-i {q}" if hw[0] else f"{ss}-i {q}"
            venc = hw[1]
            upload = hw[2] if len(hw) > 2 else ""
        else:
            # -pix_fmt yuv420p: libx264 otherwise keeps a 10-bit source's depth
            # and emits High 10 H.264, which Cast receivers can't decode.
            vin = f"{ss}-i {q}"
            venc = ("-c:v libx264 -pix_fmt yuv420p -preset veryfast -tune zerolatency "
                    "-crf 23 -maxrate 10M -bufsize 20M -g 48 -bf 0")
    tonemap = None
    if hdr and not copy_video:
        if can_tonemap():
            tonemap = hdr
            venc += _SDR_TAGS
            log.info("tone-mapping %s HDR to SDR BT.709", hdr)
        else:
            log.warning("HDR (%s) source, but this ffmpeg lacks the zscale filter; "
                        "colors will look washed out", hdr)
    mid = _mid_args(audio, sub, upload, max_height, tonemap)
    text = ("#!/usr/bin/env bash\n"
            "exec ffmpeg " + vin + mid + " " + venc +
            " -c:a aac -ac 2 -b:a 192k"
            " -movflags frag_keyframe+empty_moov+default_base_moof"
            " -f mp4 -loglevel error pipe:1\n")
    with open(WRAPPER, "w") as f:
        f.write(text)
    os.chmod(WRAPPER, 0o755)
    return WRAPPER
