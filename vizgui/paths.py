"""Filesystem paths, external tool locations, and logging setup.

OS-specific locations come from the platform layer, so this stays portable.
"""
import logging
import os

from . import platforms

GC = platforms.find_tool("go-chromecast", ["~/.local/bin/go-chromecast"])
VIDEO_GLOBS = ["*.mp4", "*.mkv", "*.m4v", "*.webm", "*.mov", "*.avi", "*.m2ts", "*.ts"]

CONFIG_DIR = platforms.config_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

CACHE = platforms.cache_dir()          # served media (symlinks / remuxes)
LOG_DIR = platforms.log_dir()
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "viz-gui.log")
CASTER_LOG = os.path.join(LOG_DIR, "caster.log")   # go-chromecast / VLC's own output
WRAPPER = os.path.join(LOG_DIR, "transcode.sh")    # live-transcode helper (space-free path)

logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("viz-gui")
