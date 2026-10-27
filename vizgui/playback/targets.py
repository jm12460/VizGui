"""The two playback targets: CastTarget (go-chromecast) and VlcTarget (local VLC).

Each owns its own device state (a caster handle / a VLC process + control mode)
and speaks only device I/O - no UI, no threading. The controller runs these
methods off the UI thread and renders their normalized status.
"""
import glob
import os
import time
import urllib.parse

from .. import config
from .. import platforms
from .. import health
from ..paths import GC, CACHE, CASTER_LOG, log
from ..chromecast import gc
from ..transcode import write_wrapper, HAS_HW
from ..helpers import cast_plan, parse_status
from ..vlc import (vlc_cmd, vlc_status, vlc_any_status, mpris_cmd, mpris_running,
                   path_to_uri, VLC_PORT, VLC_PW)
from . import media
from .base import PlaybackTarget


class CastTarget(PlaybackTarget):
    name = "Cast"

    def __init__(self):
        self.handle = None
        self.live = False

    def available(self):
        if not platforms.has_tool("go-chromecast", health.GC_FALLBACKS):
            return False, "go-chromecast isn't installed — can't cast to the TV."
        return True, ""

    def _kill(self):
        h = self.handle
        self.handle = None
        if h:
            h.terminate()

    def start(self, path, items, auto_next):
        plan = cast_plan(media.vcodec(path), os.path.splitext(path)[1])
        self.live = plan != "direct"
        if self.live and not platforms.has_tool("ffmpeg"):
            return "ffmpeg isn't installed — can't transcode/remux this file."
        self._kill()
        gc("stop")                 # end the receiver session cleanly
        time.sleep(1)
        base = [GC, "--addr", config.TV_IP, "--port", config.CAST_PORT]
        if plan == "direct":
            self.handle = platforms.launch_caster(
                base + ["load", media.symlink_serve(path)], log_path=CASTER_LOG)
            what = "direct"
        else:
            wrapper = write_wrapper(path, copy_video=(plan == "remux"))
            self.handle = platforms.launch_caster(
                base + ["transcode", "--content-type", "video/mp4", "--command", wrapper],
                log_path=CASTER_LOG)
            what = "live remux" if plan == "remux" else f"live transcode ({'hw' if HAS_HW else 'cpu'})"
        time.sleep(5)              # let it establish + the TV start
        alive = self.handle.poll() is None
        log.info("caster pid=%s alive=%s (%s)", self.handle.pid, alive, what)
        return "playing" if alive else f"go-chromecast exited (rc={self.handle.returncode})"

    def stop(self):
        self._kill()
        gc("stop")
        for f in glob.glob(os.path.join(CACHE, "*")):
            try:
                os.remove(f)       # our symlinks/temps, not their targets
            except OSError:
                pass
        return True, ""

    def shutdown(self):
        self._kill()

    def pause(self):
        return gc("togglepause")

    def seek(self, secs):
        return gc("rewind", str(-secs)) if secs < 0 else gc("seek", str(secs))

    def needs_advance_on_end(self):
        return True

    def status(self):
        ok, out = gc("status", timeout=8)
        line = out.splitlines()[0] if out else ""
        if not line or "Idle" in line or "Backdrop" in line:
            return {"idle": True, "busy": bool(self.handle and self.handle.poll() is None)}
        d = parse_status(line)
        d["idle"] = False
        d["live"] = self.live
        return d


class VlcTarget(PlaybackTarget):
    name = "VLC"

    def __init__(self):
        self.proc = None
        self.mode = None           # "http" (we launched it) or "mpris" (manual VLC)

    def available(self):
        if not platforms.has_vlc():
            return False, "VLC isn't installed — can't use the VLC target."
        return True, ""

    def start(self, path, items, auto_next):
        # 1) Our HTTP VLC is running -> send the whole folder as a native playlist.
        if "error" not in vlc_status():
            self.mode = "http"
            vlc_cmd("pl_empty")
            vlc_cmd("in_play&input=" + urllib.parse.quote(items[0]))
            for it in items[1:]:
                vlc_cmd("in_enqueue&input=" + urllib.parse.quote(it))
            log.info("VLC(http) reuse: %d item(s)", len(items))
            return "playing"
        # 2) A VLC opened by hand (no HTTP) -> control over MPRIS; detect-and-load.
        if platforms.supports_mpris() and mpris_running():
            self.mode = "mpris"
            mpris_cmd("open", path_to_uri(items[0]))
            if auto_next:
                mpris_cmd("loop", "None")   # so a finished item STOPS -> we load next
            log.info("VLC(mpris) open: %s", items[0])
            return "playing"
        # 3) No VLC -> launch our own (HTTP), stays open, native playlist.
        self.mode = "http"
        self.proc = platforms.launch_detached(
            [platforms.vlc_exe(), "--extraintf", "http", "--http-host", "127.0.0.1",
             "--http-port", str(VLC_PORT), "--http-password", VLC_PW,
             "--no-random", "--no-loop", "--no-repeat", *items],
            log_path=CASTER_LOG)
        for _ in range(20):
            time.sleep(0.5)
            if self.proc.poll() is not None:
                break
            if "error" not in vlc_status():
                log.info("vlc pid=%s up", self.proc.pid)
                return "playing"
        return "VLC failed to start (see caster.log)"

    def stop(self):               # stop playback but leave VLC open
        return mpris_cmd("stop") if self.mode == "mpris" else vlc_cmd("pl_stop")

    def shutdown(self):
        pass                      # leave VLC running on its own

    def pause(self):
        return mpris_cmd("play-pause") if self.mode == "mpris" else vlc_cmd("pl_pause")

    def seek(self, secs):
        if self.mode == "mpris":
            return mpris_cmd("position", f"{abs(secs)}{'-' if secs < 0 else '+'}")
        return vlc_cmd(f"seek&val={'+' if secs >= 0 else '-'}{abs(secs)}")

    def native_skip(self):
        return self.mode == "http"

    def skip(self, direction):
        return vlc_cmd("pl_next" if direction > 0 else "pl_previous")

    def needs_advance_on_end(self):
        return self.mode == "mpris"

    def status(self):
        s = vlc_any_status()
        if not isinstance(s, dict) or "error" in s:
            return {"idle": True, "busy": bool(self.proc and self.proc.poll() is None)}
        self.mode = s.get("_mode", self.mode)
        if s.get("state") == "STOPPED":
            return {"idle": True, "busy": False}
        return {"idle": False, "state": s.get("state", ""), "pos": s.get("pos", 0),
                "dur": s.get("dur", 0), "file": s.get("file", ""),
                "path": s.get("path", ""), "live": False}
