"""GPU/CPU transcode: NVENC detection and the live-transcode wrapper script."""
import os
import shlex
import subprocess

from .paths import WRAPPER


def _has_nvenc():
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=10)
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


HAS_NVENC = _has_nvenc()   # use the GPU when available, else fall back to libx264


def write_wrapper(path):
    """Write the transcode helper go-chromecast runs for live streaming. The
    source path is embedded (shell-quoted, so spaces/+/() are safe) and the helper
    streams H.264 + stereo AAC as fragmented mp4 to stdout. NVENC if available,
    else libx264. Returns the script path. go-chromecast splits --command on
    spaces, so the script path must contain none (it lives under ~/.cache)."""
    q = shlex.quote(os.path.abspath(path))
    if HAS_NVENC:
        vin = f"-hwaccel cuda -i {q}"
        venc = ("-c:v h264_nvenc -preset p4 -rc vbr -cq 23 -b:v 0 "
                "-maxrate 12M -bufsize 24M -g 48 -bf 0")
    else:
        vin = f"-i {q}"
        venc = ("-c:v libx264 -preset veryfast -tune zerolatency -crf 23 "
                "-maxrate 10M -bufsize 20M -g 48 -bf 0")
    text = ("#!/usr/bin/env bash\n"
            "exec ffmpeg " + vin + " " + venc +
            " -c:a aac -ac 2 -b:a 192k"
            " -movflags frag_keyframe+empty_moov+default_base_moof"
            " -f mp4 -loglevel error pipe:1\n")
    with open(WRAPPER, "w") as f:
        f.write(text)
    os.chmod(WRAPPER, 0o755)
    return WRAPPER
