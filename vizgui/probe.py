"""Probe a media file's audio tracks, subtitle tracks and chapters via ffprobe.

Pure parsing (ffprobe is shelled out, the rest is testable with its JSON). Used to
offer chapter jumps and, later, audio/subtitle selection.
"""
import json
import os
import subprocess

from . import platforms
from .paths import log

SUB_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sub")
# image-based subtitle codecs can't be burned with the text `subtitles` filter;
# they're composited with `overlay` instead.
IMAGE_SUB_CODECS = ("hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
                    "dvb_subtitle", "xsub")


def _ffprobe_json(path):
    try:
        r = subprocess.run(
            [platforms.find_tool("ffprobe"), "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_chapters", path],
            capture_output=True, text=True, timeout=20)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout else {}
    except Exception as e:
        log.warning("ffprobe %s failed: %s", os.path.basename(path or ""), e)
        return {}


def _label(tags, fallback):
    tags = {k.lower(): v for k, v in (tags or {}).items()}
    lang = tags.get("language", "")
    title = tags.get("title", "")
    parts = [p for p in (lang, title) if p and p.lower() != "und"]
    return "  ·  ".join(parts) if parts else fallback


def parse(data, path="", listdir=os.listdir):
    """Turn ffprobe JSON into {audio, subs, chapters}. Pure; `listdir` injectable
    so external-subtitle discovery is testable."""
    audio, subs, chapters = [], [], []
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        tags, disp = s.get("tags", {}) or {}, s.get("disposition", {}) or {}
        if kind == "audio":
            ch = s.get("channels")
            suffix = {1: " (mono)", 2: " (stereo)", 6: " (5.1)", 8: " (7.1)"}.get(ch, "")
            audio.append({"i": len(audio), "stream": s.get("index"),
                          "label": _label(tags, f"Audio {len(audio) + 1}") + suffix,
                          "codec": s.get("codec_name", ""), "default": bool(disp.get("default"))})
        elif kind == "subtitle":
            codec = s.get("codec_name", "")
            subs.append({"i": len(subs), "si": len(subs), "stream": s.get("index"),
                         "label": _label(tags, f"Subtitle {len(subs) + 1}"),
                         "codec": codec, "external": None,
                         "kind": "image" if codec in IMAGE_SUB_CODECS else "text",
                         "default": bool(disp.get("default")), "forced": bool(disp.get("forced"))})
    for c in data.get("chapters", []):
        try:
            start = int(float(c.get("start_time", 0)))
        except (TypeError, ValueError):
            start = 0
        title = (c.get("tags", {}) or {}).get("title") or f"Chapter {len(chapters) + 1}"
        chapters.append({"start": start, "title": title})
    subs.extend(_external_subs(path, len(subs), listdir))
    return {"audio": audio, "subs": subs, "chapters": chapters}


def _external_subs(path, start_i, listdir):
    """Sidecar subtitle files whose name starts with the video's (e.g. Show.en.srt)."""
    if not path:
        return []
    out = []
    folder = os.path.dirname(path)
    stem = os.path.basename(os.path.splitext(path)[0]).lower()
    try:
        names = listdir(folder)
    except OSError:
        return []
    for nm in sorted(names):
        if os.path.splitext(nm)[1].lower() in SUB_EXTS and nm.lower().startswith(stem):
            out.append({"i": start_i + len(out), "si": None, "stream": None, "label": nm,
                        "codec": os.path.splitext(nm)[1].lstrip("."), "kind": "text",
                        "external": os.path.join(folder, nm), "default": False, "forced": False})
    return out


def probe(path):
    """-> {audio, subs, chapters} for a media file (empty lists on failure)."""
    return parse(_ffprobe_json(path), path)
