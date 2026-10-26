"""Filesystem paths, external tool locations, and logging setup."""
import logging
import os

GC = os.path.expanduser("~/.local/bin/go-chromecast")
CACHE = os.path.expanduser("~/.cache/cast")  # safe-named symlinks to media live here
VIDEO_GLOBS = ["*.mp4", "*.mkv", "*.m4v", "*.webm", "*.mov", "*.avi", "*.m2ts", "*.ts"]

CONFIG_DIR = os.path.expanduser("~/.config/vizgui")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

LOG_DIR = os.path.expanduser("~/.cache/viz-gui")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "viz-gui.log")
CASTER_LOG = os.path.join(LOG_DIR, "caster.log")   # go-chromecast / VLC's own output
WRAPPER = os.path.join(LOG_DIR, "transcode.sh")    # live-transcode helper (space-free path)

logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("viz-gui")
