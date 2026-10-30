"""The playback engine: all cast/VLC control + state, with no Qt and no widgets.

A view (the desktop ``MainWindow``) or a headless server owns an ``Engine``,
drives its ``poll_cast``/``poll_tv``/``tick`` on a timer, forwards user actions
to its methods, and renders its state. The engine never touches a widget:

* it offloads blocking device I/O through the injected ``run_async`` (Qt's thread
  pool in the GUI, a worker thread headless), whose callbacks run back where the
  caller put them;
* after any state change it calls ``on_change()`` so the view can re-render;
* one-off messages (an error, a launch result) go through ``on_status(msg)``.

The web-remote API (``web_status``/``web_history``/``web_pair``/``web_do``) lives
here too, so the phone works identically with or without the desktop window.
"""
import os
import secrets
import time

from . import config
from . import history
from . import probe
from .config import save_config
from .paths import VIDEO_GLOBS, log
from .smartcast import sc_key, sc_status, KEYS
from .webremote import web_path_ok
from .playback import CastTarget, VlcTarget
from .playback import media


def run_sync(fn, on_done=None):
    """Default runner: execute inline. Fine for tests and simple drivers; the GUI
    injects Qt's thread pool so device I/O doesn't block the UI."""
    try:
        res = fn()
    except Exception as e:
        res = {"error": str(e)}
    if on_done is not None:
        on_done(res)


class Engine:
    PLAYBACK_CONFIRM_TIMEOUT = 45
    def __init__(self, run_async=run_sync, on_change=None, on_status=None,
                 playback_role="owner"):
        if playback_role not in ("owner", "observer"):
            raise ValueError(f"Invalid playback role: {playback_role}")
        self.playback_role = playback_role
        self._run = run_async
        self._on_change = on_change or (lambda: None)
        self._on_status = on_status or (lambda msg: None)

        self.targets = {"cast": CastTarget(), "vlc": VlcTarget()}
        self.target = "cast"             # "cast" (TV via go-chromecast) or "vlc"
        self.active = self.targets[self.target]
        self.target_index = -1           # index into config targets (web_status)
        self.now_title = None            # real filename of what we're casting
        self.now_live = False            # True while live-transcoding (no seek/dur)
        self._casting = False            # True from request until the launch finishes
        self._playback_request = None
        self._poll_generation = 0
        self._confirmation_deadline = None
        self.auto_next = bool(config.CFG.get("auto_next", True))
        self._now_path = None            # the file currently cast ("next in folder")
        self._saw_playing = False        # True once this cast reached PLAYING
        self._user_stopped = False       # True when the user hit Stop (no auto-next)
        self._recovered_cast_session = False
        self._startup_recast_attempted = False
        self._interruption_checked = False
        self._interrupted_playback = None
        self._receiver_idle = False
        # last-known playback, advanced locally once a second between polls
        self._np_pos = 0; self._np_dur = 0; self._np_state = ""; self._np_playing = False
        self._resume_base = 0            # secs a live stream was resumed from
        self._last_save = 0.0            # throttle history writes during playback
        self._media = {"audio": [], "subs": [], "chapters": []}   # ffprobe of current
        self._audio = None               # selected audio track (0-based), None=default
        self._sub = None                 # selected subtitle descriptor, None=off
        self._pair_fails = 0
        self._pair_locked = False
        self._tv_text = ""               # SmartCast status line for the pill
        self._message = None             # transient now-playing message (error/result)
        self._display_title = ""         # now-playing title while a file is playing

    def _cast_observer(self):
        """Never operate on a Cast receiver owned by another engine."""
        return self.playback_role == "observer" and self.target == "cast"

    def _observer_blocked(self):
        if self._cast_observer():
            self._status("Chromecast is managed by the VizGui server. Connect to control it.")
            return True
        return False

    # ---- view hooks ----
    def _changed(self):
        self._on_change()

    def _status(self, msg):
        self._on_status(msg)

    # ---- target selection ----
    def init_target(self):
        """Point the engine at the first configured target (so a VLC-only setup
        doesn't leave 'cast' active and poll go-chromecast with no device)."""
        targets = config.CFG.get("targets", [])
        self.target_index = 0 if targets else -1
        if targets:
            self._apply_target(targets[0])

    def _apply_target(self, t):
        self.target = t.get("type", "cast")
        self.active = self.targets.get(self.target, self.targets["cast"])
        if self.target == "cast":
            config.set_active_cast(t)

    def set_target_index(self, i):
        targets = config.CFG.get("targets", [])
        if not (0 <= i < len(targets)):
            return
        if i != self.target_index:
            self._cancel_playback_request("Playback start cancelled: target changed")
        self.target_index = i
        self._apply_target(targets[i])
        log.info("target -> %s (%s)", self.target, targets[i].get("name"))
        self.now_title = None
        self._changed()
        self.poll_cast()

    def active_target_name(self):
        targets = config.CFG.get("targets", [])
        if 0 <= self.target_index < len(targets):
            return targets[self.target_index].get("name") or self.target
        return self.target

    # ---- continue-watching + cast recovery ----
    def remember(self):
        """Record where the current file stopped. For a live stream the reported
        pos is relative to the resume point, so add the base."""
        if self._cast_observer():
            return
        if self._now_path and (self._np_dur > 0 or self.now_live):
            base = self._resume_base if self.now_live else 0
            history.record(self._now_path, self._np_pos + base, self._np_dur,
                           self.now_title or "")
            self._last_save = time.time()

    def recover_cast_item(self, st):
        """After an app restart, reconnect to the active Cast item. The
        direct-vs-transcode matching rules live in history.recover_match."""
        if self._cast_observer():
            return
        media_file = st.get("file", "")
        m = history.recover_match(media_file, history.current(), history.recent())
        if not m:
            if media_file == "pipe_output":
                log.warning("active transcode source unknown; refusing to guess from history")
            return
        self._now_path = m["path"]
        self.now_title = m["title"]
        self._resume_base = m["resume_base"]
        self._recovered_cast_session = m["reattach"]
        if media_file != "pipe_output" and (history.current() or {}).get("path") != m["path"]:
            history.set_current(m["path"], m["title"], 0)
        self._saw_playing = st.get("state") in ("PLAYING", "PAUSED")
        log.info("recovered active Cast item: %s", self.now_title)

    # ---- casting ----
    def set_autonext(self, on):
        if self._observer_blocked():
            return
        self.auto_next = bool(on)
        config.CFG["auto_next"] = self.auto_next
        try:
            save_config(config.CFG)
        except Exception:
            pass
        self._changed()

    def cast(self, path, resume=0, audio=None, sub=None):
        if self._observer_blocked():
            return False
        request_id = secrets.token_hex(8)
        title = os.path.basename(path.rstrip(os.sep)) or path
        self._playback_request = {"id": request_id, "state": "accepted",
                                  "title": title, "error": None, "error_code": None}
        self._confirmation_deadline = None
        self._poll_generation += 1
        self._casting = False
        self.remember()                  # save position of the file we're leaving
        self._now_path = path
        self._resume_base = resume
        self._audio = audio
        self._sub = sub
        self._last_save = time.time()
        self._saw_playing = False
        self._user_stopped = False
        ok, msg = self.active.available()
        if not ok:
            self._playback_request.update(state="failed", error=str(msg))
            self._message = msg
            self._changed()
            self._status(msg)
            return False
        self._message = None
        self.now_title = os.path.basename(path.rstrip(os.sep)) or path
        if self.target == "cast":
            history.set_current(path, self.now_title, resume)
        self._casting = True
        self._playback_request["state"] = "starting"
        self._receiver_idle = False
        self._np_pos = 0
        self._np_playing = False
        self._media = {"audio": [], "subs": [], "chapters": []}
        self._changed()                  # render "Preparing…", disable cast, hide tracks
        items = media.folder_queue(path) if (self.auto_next and os.sep in path) else [path]
        log.info("CAST requested: %s -> %s", path, self.active.name)
        active = self.active
        target_index = self.target_index

        def current():
            return (self._playback_request["id"] == request_id
                    and self._playback_request["state"] == "starting"
                    and self.active is active and self.target_index == target_index)

        def start():
            if not current():
                return {"error": "Playback request superseded"}
            return active.start(path, items, self.auto_next, resume, audio, sub)

        def done(res):
            if not current():
                return
            self._casting = False
            self._poll_generation += 1
            if res != "playing":
                if self.target == "cast":
                    history.clear_current()
                message = str(res.get("error", res)) if isinstance(res, dict) else str(res)
                self._playback_request.update(state="failed", error=message)
                self._message = message
                self._status(message)
            else:
                self._confirmation_deadline = time.monotonic() + self.PLAYBACK_CONFIRM_TIMEOUT
            self._changed()
            self.poll_cast()
        self._run(start, done)
        self._run(lambda: probe.probe(path),
                  lambda r: self._probe_done(path, r)
                  if self._playback_request["id"] == request_id else None)
        return True

    def _cancel_playback_request(self, message):
        self._poll_generation += 1
        self._confirmation_deadline = None
        if self._playback_request and self._playback_request["state"] in ("accepted", "starting"):
            self._playback_request.update(state="failed", error=message)
            self._casting = False

    def _playback_accepted(self):
        request = self._playback_request
        if request is None:
            return {"ok": False, "error": "No playback request accepted"}
        if request and request["state"] == "failed":
            return {"ok": False, "request_id": request["id"], "state": "failed",
                    "error": request["error"]}
        return {"ok": True, "request_id": request["id"], "state": "accepted"}

    def _probe_done(self, path, res):
        if path != self._now_path:       # cast failed or moved on -> no stale controls
            return
        self._media = res if isinstance(res, dict) and "chapters" in res else \
            {"audio": [], "subs": [], "chapters": []}
        self._changed()

    def select_sub(self, sub):
        if self._observer_blocked():
            return
        """Turn subtitles on/off by re-casting at the current position (keeps audio)."""
        if not self._now_path:
            return
        cur = self._sub.get("i") if self._sub else None
        new = sub.get("i") if sub else None
        if cur == new:
            return
        pos = self._np_pos + (self._resume_base if self.now_live else 0)
        self.cast(self._now_path, pos, audio=self._audio, sub=sub)

    def select_audio(self, i):
        if self._observer_blocked():
            return
        """Switch audio track by re-casting the current file at its position."""
        if not self._now_path or i == self._audio:
            return
        pos = self._np_pos + (self._resume_base if self.now_live else 0)
        self.cast(self._now_path, pos, audio=i, sub=self._sub)

    def jump_to(self, secs):
        if self._observer_blocked():
            return
        """Jump to an absolute time (chapter). A live stream can't seek, so restart
        it at the offset; direct cast and VLC seek in place."""
        secs = int(secs)
        if self.now_live and self._now_path:
            self.cast(self._now_path, secs, audio=self._audio, sub=self._sub)
        else:
            self._run(lambda: self.active.seek_to(secs), lambda _: self.poll_cast())

    def go_next(self):
        if self._observer_blocked():
            return
        if self.active.native_skip():
            self._run(lambda: self.active.skip(1), lambda _: self.poll_cast())
        elif self._now_path:
            nxt = media.next_file(self._now_path)
            if nxt:
                self.cast(nxt)

    def go_prev(self):
        if self._observer_blocked():
            return
        if self.active.native_skip():
            self._run(lambda: self.active.skip(-1), lambda _: self.poll_cast())
        elif self._now_path:
            prv = media.prev_file(self._now_path)
            if prv:
                self.cast(prv)

    # ---- transport ----
    def rewind(self):
        if self._observer_blocked():
            return
        return self.active.seek(-30)

    def forward(self):
        if self._observer_blocked():
            return
        return self.active.seek(30)

    def playpause(self):
        if self._observer_blocked():
            return
        return self.active.pause()

    def stop(self):
        if self._observer_blocked():
            return
        self._cancel_playback_request("Playback start cancelled by Stop")
        if self.target == "cast":
            self._interrupted_playback = None
            history.clear_interrupted()
        self._user_stopped = True
        if self.target == "cast":
            history.clear_current()
        self.remember()
        if self.target == "vlc":
            self.now_title = None
        return self.active.stop()

    def transport(self, fn):
        if self._observer_blocked():
            return
        """Run a transport action off-thread, then poll to refresh state."""
        self._run(fn, lambda _: self.poll_cast())

    def send_key(self, cmd):
        """Send a SmartCast remote key to the TV (off-thread)."""
        def fire():
            try:
                r = sc_key(*KEYS[cmd])
                res = r.get("STATUS", {}).get("RESULT", r) if isinstance(r, dict) else r
                log.info("key %s %s -> %s", cmd, KEYS[cmd], res)
                return r
            except Exception as e:
                log.warning("key %s %s FAILED: %s", cmd, KEYS[cmd], e)
                return {"error": str(e)}
        self._run(fire, self._key_sent)

    def _key_sent(self, res):
        if isinstance(res, dict) and "error" in res:
            self._status("TV didn't respond — is it on and on the same Wi-Fi?")

    def on_ended(self, allow_auto_next=True, finished=True):
        if self._cast_observer():
            return
        """Current item finished. Auto-advance if the target needs us to, else clear."""
        old_path = self._now_path
        unknown_duration_live_end = finished and self.now_live and self._np_dur <= 0
        if not unknown_duration_live_end:
            self.remember()              # finished -> dropped from the continue list
        nxt = (media.next_file(self._now_path)
               if allow_auto_next and self.auto_next and not self._user_stopped
               and self._now_path and self.active.needs_advance_on_end() else None)
        if nxt:
            log.info("auto-next -> %s", os.path.basename(nxt))
            self.cast(nxt)
            if unknown_duration_live_end:
                history.forget(old_path)
            return
        history.clear_current()
        if unknown_duration_live_end:
            history.forget(old_path)
        self.active.shutdown()
        self.now_title = None
        self.now_live = False
        self._saw_playing = False
        self._now_path = None
        self._np_playing = False
        self._media = {"audio": [], "subs": [], "chapters": []}
        self._audio = None
        self._sub = None
        self._message = None
        self._changed()

    def shutdown(self):
        self._cancel_playback_request("Playback start cancelled: controller closed")
        self.remember()
        for t in self.targets.values():
            t.shutdown()                 # cast: kill the server; vlc: leave it running

    # ---- polling ----
    def poll_cast(self):
        generation = self._poll_generation
        self._run(self.active.status,
                  lambda result: self._poll_done(result)
                  if generation == self._poll_generation else None)

    def _poll_done(self, st):
        if not isinstance(st, dict):
            return
        if self._cast_observer():
            idle = bool(st.get("idle"))
            self._now_path = None
            self.now_title = None
            self._recovered_cast_session = False
            self._saw_playing = False
            self._np_pos = st.get("pos", 0) if not idle else 0
            self._np_dur = st.get("dur", 0) if not idle else 0
            self._np_state = st.get("state", "") if not idle else ""
            self._np_playing = not idle and self._np_state in ("PLAYING", "")
            self.now_live = not idle and bool(st.get("live", False))
            filename = st.get("file") or "Playing"
            self._display_title = ("Nothing casting" if idle else
                                   "Live cast" if filename == "pipe_output" else filename)
            self._changed()
            return
        if st.get("idle"):
            # our process still starting, or mid-launch -> transient, keep state
            if self._casting or st.get("busy"):
                self._receiver_idle = False
                return
            self._receiver_idle = True
            if self._saw_playing:
                # Cast receivers can drop a paused session and report Idle. That is
                # not evidence the episode finished, so don't auto-next if our last
                # confirmed playback state was PAUSED.
                paused = self._np_state == "PAUSED"
                if paused:
                    log.info("playback went idle while paused; suppressing auto-next")
                self.on_ended(allow_auto_next=not paused, finished=not paused)
                return
            self.now_title = None
            self.now_live = False
            self._np_playing = False
            self._now_path = None
            self._media = {"audio": [], "subs": [], "chapters": []}
            self._audio = None
            self._sub = None
            self._message = None
            if self.target == "cast":
                if not self._interruption_checked:
                    self._interruption_checked = True
                    self._interrupted_playback = history.capture_interrupted()
                history.clear_current()
            self._changed()
            return
        self._receiver_idle = False
        self._message = None
        if self.target == "cast" and not self._now_path:
            self.recover_cast_item(st)
        self.now_live = st.get("live", False)
        if st.get("path"):               # MPRIS/VLC give the real path -> Next/Prev + title
            self._now_path = st["path"]
        if self.target == "vlc":
            title = st.get("file") or self.now_title or "VLC"
        else:
            status_title = st.get("file") or "Playing"
            if status_title == "pipe_output":
                status_title = "Live cast"
            title = self.now_title or status_title
        self._display_title = title
        self._np_pos = st.get("pos", 0)
        self._np_dur = st.get("dur", 0)
        self._np_state = st.get("state", "")
        self._np_playing = self._np_state in ("PLAYING", "")
        if self._np_state == "PLAYING":
            self._saw_playing = True
            if (not self._casting and self._playback_request
                    and (self._playback_request["state"] == "starting"
                         or self._playback_request.get("error_code") == "confirmation_timeout")):
                self._playback_request.update(state="playing", error=None, error_code=None)
                self._confirmation_deadline = None
                if self.target == "cast":
                    self._interrupted_playback = None
                    history.clear_interrupted()
        if (self._recovered_cast_session and not self._startup_recast_attempted
                and self._np_state == "PLAYING" and self._now_path):
            # The previous app owned the local HTTP stream. Re-open it from the
            # receiver's current position before its remaining buffer runs out.
            resume = self._np_pos + (self._resume_base if self.now_live else 0)
            self._startup_recast_attempted = True
            self._recovered_cast_session = False
            log.info("reattaching to Cast stream at %ss: %s", resume, self.now_title)
            # Preserve a manual recovery option if this automatic reattachment
            # fails after the receiver's remaining buffer has expired.
            self.remember()
            self._interrupted_playback = history.capture_interrupted(replace=True)
            self._interruption_checked = True
            self.cast(self._now_path, resume)
            return
        if self._np_playing and time.time() - self._last_save > 12:
            self.remember()              # periodic save so a crash loses little
        self._changed()

    def tick(self):
        """Advance the displayed position one second between polls (smooth clock)."""
        if (self._confirmation_deadline is not None
                and time.monotonic() >= self._confirmation_deadline):
            self._confirmation_deadline = None
            message = "Playback was not confirmed by the receiver in time"
            self._playback_request.update(state="failed", error=message,
                                          error_code="confirmation_timeout")
            self._status(message)
            self._changed()
        if self._np_playing and (self.now_live or self._np_dur > 0):
            if not self.now_live and self._np_dur:
                self._np_pos = min(self._np_pos + 1, self._np_dur)
            else:
                self._np_pos += 1
            self._changed()

    def poll_tv(self):
        if not config.HOST:
            return
        self._run(sc_status, self._tv_done)

    def _tv_done(self, s):
        if "error" in s:
            self._tv_text = "TV: unreachable"
        else:
            m = "  muted" if s["mute"] == "On" else ""
            self._tv_text = f"TV {s['power']}  ·  {s['input']}  ·  vol {s['vol']}{m}"
        self._changed()

    # ---- phone remote / web API ----
    def new_pairing(self):
        """Rotate BOTH the auth token (= the QR) and the PIN. Every paired phone
        is un-paired and must scan/enter the new credentials."""
        w = config.CFG.setdefault("web", {})
        w["token"] = secrets.token_urlsafe(24)
        w["pin"] = f"{secrets.randbelow(1000000):06d}"
        self._pair_fails = 0
        self._pair_locked = False
        try:
            save_config(config.CFG)
        except Exception:
            pass

    def web_pair(self, pin):
        """Validate a pairing PIN (HTTP thread) and hand back the auth token."""
        if self._pair_locked:
            return {"ok": False, "locked": True}
        want = str(config.CFG.get("web", {}).get("pin", ""))
        if pin and want and secrets.compare_digest(str(pin), want):
            self._pair_fails = 0
            return {"ok": True, "token": config.CFG["web"]["token"]}
        self._pair_fails += 1
        if self._pair_fails >= 5:
            self._pair_locked = True
        return {"ok": False, "locked": self._pair_locked}

    def web_history(self):
        """Continue-watching items for the phone, minus whatever's playing now."""
        cur = self._now_path
        return [it for it in history.recent() if it.get("path") != cur]

    def web_status(self):
        """Snapshot for the phone UI - reads cached state only (HTTP-thread safe)."""
        def tname(t):
            base = t.get("name") or t.get("type", "?")
            return base + (" (Cast)" if t.get("type") == "cast" else "")
        targets = [{"i": i, "name": tname(t)} for i, t in enumerate(config.CFG.get("targets", []))]
        sources = [{"i": i, "name": s.get("name") or s.get("path", "?")}
                   for i, s in enumerate(config.CFG.get("sources", []))]
        return {
            "ok": True,
            "target_index": self.target_index,
            "targets": targets,
            "sources": sources,
            "auto_next": self.auto_next,
            "playback_request": dict(self._playback_request) if self._playback_request else None,
            "interrupted": (self._interrupted_playback
                            if self.target == "cast" and self._receiver_idle
                            and not self._casting and not self._cast_observer() else None),
            "tv": self._tv_text if config.HOST else "",
            "now": {"title": self.now_title or "", "pos": self._np_pos,
                    "dur": self._np_dur, "state": self._np_state, "live": self.now_live},
            "chapters": self._media.get("chapters", []),
            "audio": {"tracks": self._media.get("audio", []), "current": self._audio},
            "subs": {"tracks": self._media.get("subs", []),
                     "current": self._sub.get("i") if self._sub else None},
        }

    def web_add_cast(self, dev):
        if self.playback_role == "observer":
            return
        """Add a Cast device discovered from the phone, then switch to it."""
        addr = dev.get("addr")
        if not addr:
            return
        targets = config.CFG.setdefault("targets", [])
        if not any(t.get("type") == "cast" and t.get("addr") == addr for t in targets):
            targets.append({"type": "cast", "name": dev.get("name") or addr,
                            "addr": addr, "cast_port": dev.get("cast_port") or "8009"})
            try:
                save_config(config.CFG)
            except Exception as e:
                log.warning("save after web add-cast failed: %s", e)
        for i, t in enumerate(config.CFG.get("targets", [])):
            if t.get("type") == "cast" and t.get("addr") == addr:
                self.set_target_index(i)
                break
        self._status(f"Added {dev.get('name') or addr}")
        self._changed()                  # target list grew -> view rebuilds the picker

    def web_do(self, kind, p):
        """Apply a remote action on the GUI thread or headless owner queue."""
        if kind == "resume_interrupted":
            return self.resume_interrupted(p.get("id"), p.get("target_index"))
        if self.playback_role == "observer" and kind in ("forget", "clear"):
            return
        try:
            if kind == "key":
                self.send_key(p["cmd"])
            elif kind == "transport":
                act = p.get("action")
                if act == "next":
                    self.go_next()
                elif act == "prev":
                    self.go_prev()
                else:
                    fn = {"rewind": self.rewind, "playpause": self.playpause,
                          "forward": self.forward, "stop": self.stop}.get(act)
                    if fn:
                        self.transport(fn)
            elif kind == "target":
                self.set_target_index(int(p["index"]))
            elif kind == "addcast":
                self.web_add_cast(p)
            elif kind == "autonext":
                self.set_autonext(bool(p.get("on")))
            elif kind == "cast":
                path = p.get("path", "")
                if (path.lower().endswith(tuple(g[1:] for g in VIDEO_GLOBS))
                        and web_path_ok(path)):
                    if not self.cast(path, int(p.get("resume", 0) or 0)):
                        return {"ok": False, "error": self._message or "Playback not allowed"}
                    return self._playback_accepted()
                else:
                    log.warning("web cast refused (outside sources): %s", path)
                    return {"ok": False, "error": "Media is unavailable or outside configured sources"}
            elif kind == "forget":
                history.forget(p.get("path", ""))
                if (self._interrupted_playback
                        and p.get("path") == self._interrupted_playback["path"]):
                    self._interrupted_playback = None
                    history.clear_interrupted()
            elif kind == "clear":
                history.clear()
                self._interrupted_playback = None
                history.clear_interrupted()
            elif kind == "jump":
                self.jump_to(int(p.get("secs", 0) or 0))
            elif kind == "audio":
                self.select_audio(int(p.get("i", 0) or 0))
            elif kind == "sub":
                i = p.get("i")
                s = None if i is None else next(
                    (x for x in self._media.get("subs", []) if x.get("i") == int(i)), None)
                self.select_sub(s)
        except Exception as e:
            log.warning("web action %s failed: %s", kind, e)

    def resume_interrupted(self, checkpoint_id, target_index):
        """Validate a user-confirmed checkpoint on the playback owner thread."""
        entry = self._interrupted_playback
        if (self.playback_role != "owner" or self.target != "cast"
                or not self._receiver_idle or self._casting
                or not entry or checkpoint_id != entry["id"]
                or type(target_index) is not int or target_index != self.target_index):
            return {"ok": False, "error": "Interrupted playback is no longer available"}
        if not web_path_ok(entry["path"]):
            return {"ok": False, "error": "Interrupted media is missing or outside configured sources"}
        if not self.cast(entry["path"], entry["pos"]):
            return {"ok": False, "error": self._message or "Could not resume playback"}
        return self._playback_accepted()
