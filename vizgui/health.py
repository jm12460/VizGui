"""Startup health checks: given the config, which external tools are needed and
which are missing. Pure logic (the tool check is injectable) so it's testable."""
from . import platforms

GC_FALLBACKS = ["~/.local/bin/go-chromecast"]


def missing(cfg, has_tool=None, vlc_present=None):
    """Return a list of (tool, consequence) for required-but-missing tools, based
    on the configured targets. ``has_tool`` and ``vlc_present`` are injectable for
    testing; VLC gets its own check because it's off-PATH on Windows/macOS."""
    has_tool = has_tool or platforms.has_tool
    vlc_present = vlc_present if vlc_present is not None else platforms.has_vlc
    targets = cfg.get("targets", [])
    needs_cast = any(t.get("type") == "cast" for t in targets)
    needs_vlc = any(t.get("type") == "vlc" for t in targets)
    out = []
    if needs_cast and not has_tool("go-chromecast", GC_FALLBACKS):
        out.append(("go-chromecast", "casting to a TV / Chromecast won't work"))
    if needs_cast and not has_tool("ffmpeg"):
        out.append(("ffmpeg", "transcoding HEVC or remuxing mkv to the TV won't work"))
    if needs_vlc and not vlc_present():
        out.append(("vlc", "the VLC target won't work"))
    return out
