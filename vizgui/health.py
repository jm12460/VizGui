"""Startup health checks: given the config, which external tools are needed and
which are missing. Pure logic (the tool check is injectable) so it's testable."""
from . import platforms

GC_FALLBACKS = ["~/.local/bin/go-chromecast"]


def missing(cfg, has_tool=None):
    """Return a list of (tool, consequence) for required-but-missing tools, based
    on the configured targets. ``has_tool`` is injectable for testing."""
    has_tool = has_tool or platforms.has_tool
    targets = cfg.get("targets", [])
    has_cast = any(t.get("type") == "cast" for t in targets)
    has_vlc = any(t.get("type") == "vlc" for t in targets)
    out = []
    if has_cast and not has_tool("go-chromecast", GC_FALLBACKS):
        out.append(("go-chromecast", "casting to a TV / Chromecast won't work"))
    if has_cast and not has_tool("ffmpeg"):
        out.append(("ffmpeg", "transcoding HEVC or remuxing mkv to the TV won't work"))
    if has_vlc and not has_tool("vlc"):
        out.append(("vlc", "the VLC target won't work"))
    return out
