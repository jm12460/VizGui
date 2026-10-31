"""The two playback targets: CastTarget (go-chromecast) and VlcTarget (local VLC).

Each owns its own device state (a caster handle / a VLC process + control mode)
and speaks only device I/O - no UI, no threading. The controller runs these
methods off the UI thread and renders their normalized status.
"""
import glob
import os
import socket
import time
import urllib.parse

from .. import config
from .. import platforms
from .. import health
from ..paths import GC, CACHE, CASTER_LOG, log
from ..chromecast import gc
from ..transcode import write_wrapper, prepare_subtitle, encoder_name
from ..helpers import cast_plan, parse_status
from ..vlc import (vlc_cmd, vlc_status, vlc_any_status, vlc_playlist_path,
                   mpris_cmd, mpris_running, path_to_uri, VLC_PORT, VLC_PW)
from . import media, prepared
from .base import PlaybackTarget


class CastTarget(PlaybackTarget):
    name = "Cast"

    def __init__(self):
        self.handle = None
        self.live = False
        self._prepared = None
        self._can_fallback_1080 = False
        self._fallback_host = None

    def available(self):
        if not platforms.has_tool("go-chromecast", health.GC_FALLBACKS):
            return False, "go-chromecast isn't installed — can't cast to the TV."
        return True, ""

    def _kill(self):
        h = self.handle
        self.handle = None
        if h:
            h.terminate()
        if self._prepared is not None:
            self._prepared.cleanup()
            self._prepared = None

    def _wait_idle(self, budget=4.0):
        """Wait (time-budgeted) for the receiver to report idle, so a replacement
        session starts from a clean slate instead of racing the old one."""
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            time.sleep(0.4)
            ok, out = gc("status", timeout=3)
            line = out.splitlines()[0] if out else ""
            if not line or "Idle" in line or "Backdrop" in line:
                return

    def start_guarded(self, path, items, auto_next, resume=0, audio=None, sub=None, *, current,
                      max_height=None):
        return self.start(path, items, auto_next, resume, audio, sub, current=current,
                          max_height=max_height)

    def start(self, path, items, auto_next, resume=0, audio=None, sub=None, *, current=None,
              max_height=None):
        started = time.monotonic()
        plan = cast_plan(media.vcodec(path), os.path.splitext(path)[1])
        if sub is not None:
            plan = "transcode"     # burning a subtitle requires re-encoding the video
        elif plan == "direct" and audio is not None:
            plan = "remux"         # selecting a non-default audio needs a re-map
        if max_height == 1080:
            plan = "transcode"     # a remembered compatibility choice must actually scale
        dimensions = media.video_size(path) if plan == "transcode" and max_height is None else None
        can_fallback = bool(dimensions and (dimensions[0] > 1920 or dimensions[1] > 1080))
        host = self._fallback_sender_host()
        ready = None
        if plan == "remux" and audio is None and sub is None:
            ready = prepared.prepare(path, CACHE)
            if ready is not None:
                plan = "direct"
        if current is not None and not current():
            if ready is not None:
                ready[0].cleanup()
            return {"error": "Playback request superseded"}
        self.live = plan != "direct"
        self._can_fallback_1080 = bool(host and can_fallback)
        self._fallback_host = host
        if self.live and not platforms.has_tool("ffmpeg"):
            return "ffmpeg isn't installed — can't transcode/remux this file."
        self._kill()
        self._prepared = ready[0] if ready is not None else None
        gc("stop")                 # end the receiver session cleanly
        stopped = time.monotonic()
        self._wait_idle()          # let the receiver fully reset before a new
                                   # session, or go-chromecast's app-switch handshake
                                   # times out ("context deadline exceeded") on re-cast
        idle = time.monotonic()
        if current is not None and not current():
            # A newer request may already own the target; never stop its caster.
            if ready is not None and self._prepared is ready[0]:
                self._prepared = None
                ready[0].cleanup()
            return {"error": "Playback request superseded"}
        base = [GC, "--addr", config.TV_IP, "--port", config.CAST_PORT]
        if plan == "direct":
            # Include the resume offset in LOAD. A separate seek after a fixed
            # delay can race receiver initialization and silently start at zero.
            self.handle = platforms.launch_caster(
                base + ["load", "--start-time", str(int(resume)),
                        ready[1] if ready is not None else media.symlink_serve(path)],
                log_path=CASTER_LOG)
            what = "direct"
        else:
            # a live stream resumes by seeking the input (-ss); it begins at `resume`
            sub_desc = prepare_subtitle(path, sub) if sub else None
            wrapper_args = {"copy_video": plan == "remux", "start": resume,
                            "audio": audio, "sub": sub_desc}
            if max_height is not None:
                wrapper_args["max_height"] = max_height
            wrapper = write_wrapper(path, **wrapper_args)
            self.handle = platforms.launch_caster(
                base + ["transcode", "--content-type", "video/mp4", "--command", wrapper],
                log_path=CASTER_LOG)
            what = "live remux" if plan == "remux" else f"live transcode ({encoder_name()})"
        launched = time.monotonic()
        log.info("Cast launch timing: prepare_stop=%.2fs wait_idle=%.2fs launch=%.2fs",
                 stopped - started, idle - stopped, launched - idle)
        time.sleep(5)              # let it establish + the TV start
        alive = self.handle.poll() is None
        log.info("caster pid=%s alive=%s (%s) resume=%s", self.handle.pid, alive, what, resume)
        return "playing" if alive else f"go-chromecast exited (rc={self.handle.returncode})"

    @staticmethod
    def _fallback_sender_host():
        """Find the local address used to reach the receiver, without sending data."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((config.TV_IP, int(config.CAST_PORT)))
                return route.getsockname()[0]
        except (OSError, ValueError, TypeError):
            return None

    def sender_matches_content(self, content_id):
        """Return True/False for a verifiable sender URL, otherwise None."""
        if not self._fallback_host or not self.handle or self.handle.poll() is not None:
            return None
        try:
            url = urllib.parse.urlsplit(content_id)
            if url.scheme not in ("http", "https") or not url.hostname or not url.port:
                return None
            if url.hostname != self._fallback_host:
                return False
            return platforms.process_listens_on_port(self.handle.pid, url.port)
        except (ValueError, TypeError):
            return None

    def owns_buffering_content(self, content_id):
        """Require positive sender ownership for automatic retry and memory."""
        return self.sender_matches_content(content_id) is True

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

    def seek_to(self, secs):
        return gc("seek-to", str(int(secs)))

    def needs_advance_on_end(self):
        return True

    def status(self):
        ok, out = gc("status", timeout=8)
        if not ok:
            # A failed status request is not evidence that playback is idle.
            return {"idle": True, "busy": True, "error": out}
        line = out.splitlines()[0] if out else ""
        if not line or "Idle" in line or "Backdrop" in line:
            return {"idle": True, "busy": bool(self.handle and self.handle.poll() is None)}
        d = parse_status(line)
        d["idle"] = False
        # A new app process doesn't know its previous CastTarget.live flag.
        # go-chromecast names the transcoded stream pipe_output, so recover that
        # fact from the receiver's current media status.
        d["live"] = self.live or d.get("file") == "pipe_output"
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

    def _own(self):
        """True if we launched this VLC and it's still alive (so we control it
        over HTTP and must not flip to MPRIS on a transient HTTP hiccup)."""
        return self.proc is not None and self.proc.poll() is None

    @staticmethod
    def _item_opts(audio, sub):
        """Per-item VLC MRL options (the `:opt=val` form) for a chosen audio/sub
        track. Same option names as the launch flags, so a reused VLC applies the
        selection exactly as a freshly-launched one does."""
        opts = []
        if audio is not None:
            opts.append(f":audio-track={int(audio)}")
        if sub:
            if sub.get("external"):
                opts.append(f":sub-file={sub['external']}")
            elif sub.get("si") is not None:
                opts.append(f":sub-track={int(sub['si'])}")
        return opts

    def start(self, path, items, auto_next, resume=0, audio=None, sub=None):
        # 1) Reuse our own HTTP VLC (preferred) -> send the folder as a native playlist.
        if self._own() or "error" not in vlc_status():
            self.mode = "http"
            vlc_cmd("pl_empty")
            # audio/sub ride along as per-item options on the first item, so reuse
            # honors the selection instead of ignoring it (the launch path applies
            # the same options via --audio-track/--sub-file/--sub-track).
            play = "in_play&input=" + urllib.parse.quote(items[0])
            # Override a global start-time left by older VizGui launches (or
            # VLC preferences). Seek only the current item to its saved offset.
            for opt in [":start-time=0", *self._item_opts(audio, sub)]:
                play += "&option=" + urllib.parse.quote(opt)
            vlc_cmd(play)
            for it in items[1:]:
                vlc_cmd("in_enqueue&input=" + urllib.parse.quote(it)
                        + "&option=" + urllib.parse.quote(":start-time=0"))
            if resume:
                time.sleep(1)                       # let the item load before seeking
                vlc_cmd(f"seek&val={int(resume)}")
            log.info("VLC(http) reuse: %d item(s)", len(items))
            return "playing"
        # 2) A VLC opened by hand (we didn't launch one) -> control over MPRIS.
        if platforms.supports_mpris() and mpris_running():
            self.mode = "mpris"
            mpris_cmd("open", path_to_uri(items[0]))
            if auto_next:
                mpris_cmd("loop", "None")   # so a finished item STOPS -> we load next
            if resume:
                time.sleep(1)
                mpris_cmd("position", str(int(resume)))
            log.info("VLC(mpris) open: %s", items[0])
            return "playing"
        # 3) No VLC -> launch our own (HTTP), stays open, native playlist.
        self.mode = "http"
        start_arg = ["--start-time=0"]
        # A global nonzero --start-time is inherited by every playlist entry.
        # VLC's :option syntax applies only to the immediately preceding input.
        playlist = [items[0]]
        if resume:
            playlist.append(f":start-time={int(resume)}")
        playlist.extend(items[1:])
        if audio is not None:
            start_arg.append(f"--audio-track={int(audio)}")
        if sub:
            if sub.get("external"):
                start_arg.append(f"--sub-file={sub['external']}")
            elif sub.get("si") is not None:
                start_arg.append(f"--sub-track={int(sub['si'])}")
        self.proc = platforms.launch_detached(
            [platforms.vlc_exe(), "--extraintf", "http", "--http-host", "127.0.0.1",
             "--http-port", str(VLC_PORT), "--http-password", VLC_PW,
             # --no-one-instance: VLC's single-instance mode (on by default) would
             # otherwise forward our files to a lingering instance and exit without
             # opening a window — so casts silently stop working after a while.
             "--no-one-instance", "--no-random", "--no-loop", "--no-repeat",
             *start_arg, *playlist],
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

    def seek_to(self, secs):
        if self.mode == "mpris":
            return mpris_cmd("position", str(int(secs)))     # absolute seconds
        return vlc_cmd(f"seek&val={int(secs)}")              # absolute seconds

    def native_skip(self):
        return self.mode == "http"

    def skip(self, direction):
        return vlc_cmd("pl_next" if direction > 0 else "pl_previous")

    def needs_advance_on_end(self):
        return self.mode == "mpris"

    def status(self):
        if self._own():
            s = vlc_status()                 # our VLC -> trust HTTP; don't flip to MPRIS
            if "error" in s:
                return {"idle": True, "busy": True}   # transient hiccup, not ended
            self.mode = "http"
            # map the current playlist item back to its real path so the app tracks
            # which file is playing after a native Next/Prev (for Continue-watching)
            s["path"] = vlc_playlist_path() or s.get("path", "")
        else:
            s = vlc_any_status()
            if not isinstance(s, dict) or "error" in s:
                return {"idle": True, "busy": False}
            self.mode = s.get("_mode", self.mode)
        if s.get("state") == "STOPPED":
            return {"idle": True, "busy": False}
        return {"idle": False, "state": s.get("state", ""), "pos": s.get("pos", 0),
                "dur": s.get("dur", 0), "file": s.get("file", ""),
                "path": s.get("path", ""), "live": False}
