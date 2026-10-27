"""The live-transcode wrapper script go-chromecast runs. The video encoder is
chosen by the platform layer (hardware if available, else software libx264)."""
import os
import shlex

from . import platforms
from .paths import WRAPPER

# True if this platform/GPU offers a hardware H.264 encoder (for UI/logging).
HAS_HW = platforms.hw_encode_args() is not None


def write_wrapper(path, copy_video=False):
    """Write the helper go-chromecast runs for live streaming. The source path is
    embedded (shell-quoted, so spaces/+/() are safe) and the helper streams the
    video + stereo AAC as fragmented mp4 to stdout. Returns the script path
    (space-free, under the cache dir, since go-chromecast splits --command on spaces).

    copy_video=True: the video is already H.264, so just repackage it (``-c:v copy``)
    - a near-free live REMUX (no re-encode) for H.264 in a container go-chromecast
    can't serve directly (e.g. mkv). Otherwise re-encode to H.264 using the
    platform's hardware encoder, or software libx264 as a fallback."""
    q = shlex.quote(os.path.abspath(path))
    if copy_video:
        vin, venc = f"-i {q}", "-c:v copy"
    else:
        hw = platforms.hw_encode_args()
        if hw:
            vin, venc = f"{hw[0]} -i {q}", hw[1]
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
