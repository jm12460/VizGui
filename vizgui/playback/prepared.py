"""Prepare bounded, private seekable copies for compatible Cast media."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from .. import platforms

# Avoid unexpectedly copying large films or filling a user's cache partition.
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_CACHE_BYTES = 2 * MAX_SOURCE_BYTES
MIN_FREE_BYTES = 256 * 1024 * 1024
PREPARE_TIMEOUT = 45


def _cache_used(cache_dir):
    """Count only our private prepared-file directories, without following links."""
    total = 0
    with os.scandir(cache_dir) as entries:
        for entry in entries:
            if not entry.name.startswith("vizgui-seekable-") or not entry.is_dir(follow_symlinks=False):
                continue
            with os.scandir(entry.path) as children:
                for child in children:
                    if child.is_file(follow_symlinks=False):
                        total += child.stat(follow_symlinks=False).st_size
    return total


def prepared_name(path):
    digest = hashlib.sha256(os.fsencode(os.path.abspath(path))).hexdigest()[:24]
    return f"vizgui-prepared-{digest}.mp4"


def prepare(path, cache_dir):
    """Return (temporary-directory owner, MP4 path), or None for streaming fallback.

    Only H.264 with one AAC audio track is copied; track selection and subtitle
    handling remain on the existing streaming path. Nothing is written to the
    source directory and each output belongs to one playback session.
    """
    directory = None
    try:
        before = os.stat(path)
        if not os.path.isfile(path) or before.st_size <= 0 or before.st_size > MAX_SOURCE_BYTES:
            return None
        probe = subprocess.run(
            [platforms.find_tool("ffprobe"), "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,profile,channels", "-of", "json", path],
            capture_output=True, text=True, timeout=10, check=True)
        streams = json.loads(probe.stdout).get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        if (len(video) != 1 or video[0].get("codec_name") != "h264"
                or len(audio) != 1 or audio[0].get("codec_name") != "aac"
                or audio[0].get("profile") != "LC"
                or audio[0].get("channels") not in (1, 2)):
            return None
        os.makedirs(cache_dir, mode=0o700, exist_ok=True)
        if (_cache_used(cache_dir) + before.st_size + MIN_FREE_BYTES > MAX_CACHE_BYTES
                or shutil.disk_usage(cache_dir).free < before.st_size * 2 + MIN_FREE_BYTES):
            return None
        directory = tempfile.TemporaryDirectory(prefix="vizgui-seekable-", dir=cache_dir)
        output = os.path.join(directory.name, prepared_name(path))
        subprocess.run(
            [platforms.find_tool("ffmpeg"), "-nostdin", "-v", "error", "-i", path,
             "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart",
             "-fs", str(MAX_SOURCE_BYTES + MIN_FREE_BYTES), output],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PREPARE_TIMEOUT, check=True)
        after = os.stat(path)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or not 0 < os.path.getsize(output) < MAX_SOURCE_BYTES + MIN_FREE_BYTES):
            directory.cleanup()
            return None
        return directory, output
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        if directory is not None:
            directory.cleanup()
        return None
