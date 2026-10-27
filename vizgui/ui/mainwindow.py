"""The main window: ties media browsing, casting, transport, polling, the phone
remote, and status together."""
import glob
import os
import pty
import re
import secrets
import signal
import subprocess
import threading
import time
import urllib.parse
from functools import partial

from PySide6.QtCore import Qt, QTimer, QDir, QModelIndex
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QTreeView, QSplitter, QSizePolicy, QFileSystemModel, QProgressBar, QComboBox,
    QDialog, QDialogButtonBox, QMessageBox, QCheckBox,
)

from .. import config
from ..config import save_config, load_config
from ..paths import GC, CACHE, VIDEO_GLOBS, CASTER_LOG, log
from ..transcode import HAS_NVENC, write_wrapper
from ..smartcast import sc_status
from ..chromecast import gc
from ..vlc import (vlc_cmd, vlc_status, vlc_any_status, mpris_cmd, mpris_running,
                   path_to_uri, VLC_PORT, VLC_PW)
from ..sources import source_path
from ..net import local_ip, lan_cidr
from ..helpers import fmt_time, parse_status
from ..qtasync import run_async
from ..webremote import WebBridge, start_web_server
from .remote import RemotePanel
from .dialogs import SetupDialog, SmbDialog


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Basement Display")
        self.resize(980, 640)
        self.target = "cast"    # "cast" (TV via go-chromecast) or "vlc" (this PC)
        self.vlc_proc = None    # the VLC process we launched (vlc target)
        self._vlc_mode = None   # "http" (we launched it) or "mpris" (manual VLC)
        self.caster = None      # the live go-chromecast process serving the TV
        self.now_title = None   # real filename of what we're casting (for display)
        self.now_live = False   # True while live-transcoding (no seek, no duration)
        self._casting = False   # True from click until the launch worker finishes
        self.auto_next = bool(config.CFG.get("auto_next", True))
        self._now_path = None       # the file currently cast (for "next in folder")
        self._saw_playing = False   # True once this cast actually reached PLAYING
        self._user_stopped = False  # True when the user hit Stop (suppresses auto-next)
        # last-known playback state, advanced locally once a second between polls
        self._np_pos = 0; self._np_dur = 0; self._np_state = ""; self._np_playing = False

        root = QWidget(); self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(14)

        # ---- left: remote + TV status ----
        left = QVBoxLayout()
        left.setSpacing(12)
        self.remote = RemotePanel(self)
        rrow = QHBoxLayout(); rrow.addStretch(1); rrow.addWidget(self.remote); rrow.addStretch(1)
        left.addLayout(rrow)
        tvrow = QHBoxLayout()
        self.tv_status = QLabel("TV: …"); self.tv_status.setObjectName("tvpill")
        self.tv_status.setAlignment(Qt.AlignCenter)
        tvrow.addStretch(1); tvrow.addWidget(self.tv_status); tvrow.addStretch(1)
        left.addLayout(tvrow)
        left.addStretch(1)
        lw = QWidget(); lw.setLayout(left); lw.setFixedWidth(252)
        outer.addWidget(lw)

        # ---- right: media browser (top) + now playing (bottom) ----
        split = QSplitter(Qt.Vertical)

        browser = QFrame(); browser.setObjectName("card")
        bl = QVBoxLayout(browser)
        hdr = QHBoxLayout()
        t = QLabel("Media"); t.setObjectName("title")
        self.source_box = QComboBox()
        for s in config.CFG.get("sources", []):
            tag = "  (SMB)" if s.get("kind") == "smb" else ""
            self.source_box.addItem((s.get("name") or s.get("path", "?")) + tag, s)
        self.source_box.currentIndexChanged.connect(self._source_changed)
        self.b_setup = QPushButton("⚙ Setup"); self.b_setup.clicked.connect(self.open_setup)
        hdr.addWidget(t); hdr.addStretch(1)
        hdr.addWidget(QLabel("Source:")); hdr.addWidget(self.source_box)
        hdr.addWidget(self.b_setup)
        bl.addLayout(hdr)
        self.model = QFileSystemModel()
        self.model.setNameFilters(VIDEO_GLOBS)
        self.model.setNameFilterDisables(False)
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.doubleClicked.connect(self.on_double)
        bl.addWidget(self.tree)
        self._source_changed(self.source_box.currentIndex())  # root at first source
        row = QHBoxLayout()
        self.b_cast = QPushButton("Cast selected"); self.b_cast.setProperty("class", "accent")
        self.b_cast.clicked.connect(self.cast_selected)
        self.target_box = QComboBox()
        self._reload_targets()
        self.target_box.currentIndexChanged.connect(self._target_changed)
        row.addWidget(self.b_cast)
        row.addWidget(QLabel("→"))
        row.addWidget(self.target_box)
        row.addStretch(1)
        bl.addLayout(row)
        split.addWidget(browser)

        now = QFrame(); now.setObjectName("card")
        nl = QVBoxLayout(now); nl.setSpacing(10)
        nhdr = QHBoxLayout()
        nt = QLabel("NOW PLAYING"); nt.setObjectName("title")
        self.np_badge = QLabel("● LIVE"); self.np_badge.setObjectName("badge"); self.np_badge.hide()
        self.autonext_cb = QCheckBox("Auto-play next")
        self.autonext_cb.setChecked(self.auto_next)
        self.autonext_cb.toggled.connect(self._set_autonext)
        nhdr.addWidget(nt); nhdr.addStretch(1); nhdr.addWidget(self.np_badge)
        nhdr.addWidget(self.autonext_cb)
        nl.addLayout(nhdr)
        self.np_title = QLabel("Nothing casting."); self.np_title.setObjectName("now")
        self.np_title.setWordWrap(True)
        nl.addWidget(self.np_title)
        prow = QHBoxLayout()
        self.np_bar = QProgressBar(); self.np_bar.setTextVisible(False)
        self.np_bar.setFixedHeight(8); self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
        self.np_time = QLabel(""); self.np_time.setObjectName("dim")
        prow.addWidget(self.np_bar, 1); prow.addWidget(self.np_time)
        nl.addLayout(prow)
        hint = QLabel("Playback controls are on the remote ◀")
        hint.setObjectName("dim")
        nl.addWidget(hint)
        self.web_label = QLabel(""); self.web_label.setObjectName("dim")
        self.web_label.setAlignment(Qt.AlignCenter)
        self.web_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bl.addWidget(self.web_label)

        split.addWidget(now)
        split.setSizes([460, 150])
        outer.addWidget(split, 1)

        # ---- phone remote (LAN web server) ----
        self._tv_text = ""
        self._pair_fails = 0
        self._pair_locked = False
        self.web_bridge = WebBridge()
        self.web_bridge.do.connect(self._web_do)   # runs on the main thread
        self.httpd = None
        web = config.CFG.get("web", {})
        if web.get("enabled", True):
            try:
                self.httpd = start_web_server(self, int(web.get("port", 8950)))
                self._update_web_label()
            except Exception as e:
                self.web_label.setText(f"phone remote off ({e})")

        # ---- pollers ----
        self._tv_timer = QTimer(self); self._tv_timer.timeout.connect(self.poll_tv)
        self._tv_timer.start(5000)
        self._cast_timer = QTimer(self); self._cast_timer.timeout.connect(self.poll_cast)
        self._cast_timer.start(3000)
        self._np_timer = QTimer(self); self._np_timer.timeout.connect(self._tick)
        self._np_timer.start(1000)   # smooth 1s clock between the 3s status polls
        self._refresh_remote_enabled()
        _ct = self.target_box.currentData()
        self.remote.set_target_label((_ct or {}).get("name") or self.target)
        self.poll_tv(); self.poll_cast()

    # ---- media actions ----
    def _sel_path(self):
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        p = self.model.filePath(idx)
        return p if os.path.isfile(p) else None

    def on_double(self, idx):
        p = self.model.filePath(idx)
        if os.path.isfile(p):
            self._cast(p)

    def cast_selected(self):
        p = self._sel_path()
        if p:
            self._cast(p)
        else:
            self.np_title.setText("Select a video file first.")

    def _reload_targets(self):
        self.target_box.blockSignals(True)
        self.target_box.clear()
        for t in config.CFG.get("targets", []):
            label = t.get("name") or t.get("type", "?")
            if t.get("type") == "cast":
                label += " (Cast)"
            self.target_box.addItem(label, t)
        self.target_box.blockSignals(False)

    def _target_changed(self, idx):
        t = self.target_box.currentData()
        if not t:
            return
        self.target = t.get("type", "cast")
        if self.target == "cast":
            config.set_active_cast(t)
        log.info("target -> %s (%s)", self.target, t.get("name"))
        self.remote.set_target_label(t.get("name") or self.target)
        self.now_title = None
        self._refresh_remote_enabled()
        self.poll_cast()

    def _root_at(self, path):
        if path and os.path.isdir(path):
            self.model.setRootPath(path)
            self.tree.setRootIndex(self.model.index(path))
            return True
        return False

    def _source_changed(self, idx):
        s = self.source_box.itemData(idx) if idx >= 0 else None
        if not s:
            return
        if s.get("kind") == "smb":
            self.tree.setRootIndex(QModelIndex())   # clear while connecting
            name = s.get("name") or s.get("smb", {}).get("share", "share")
            def done(res):
                if isinstance(res, str) and self._root_at(res):
                    return
                QMessageBox.warning(self, "SMB", f"Could not open '{name}':\n{res}")
            run_async(lambda: self._safe_source_path(s), done)
        else:
            self._root_at(s.get("path"))

    def _safe_source_path(self, s):
        try:
            return source_path(s)
        except Exception as e:
            return str(e)

    def _reload_sources(self):
        self.source_box.blockSignals(True)
        self.source_box.clear()
        for s in config.CFG.get("sources", []):
            tag = "  (SMB)" if s.get("kind") == "smb" else ""
            self.source_box.addItem((s.get("name") or s.get("path", "?")) + tag, s)
        self.source_box.blockSignals(False)
        self._source_changed(self.source_box.currentIndex())

    def _refresh_remote_enabled(self):
        on = bool(config.HOST)
        self.remote.set_tv_enabled(on)   # transport stays enabled; TV keys gated
        if not on:
            self.tv_status.setText("No SmartCast TV configured")

    def open_setup(self):
        dlg = SetupDialog(self)
        if dlg.exec():
            config.CFG = load_config()
            self._reload_sources()
            self._reload_targets()
            self._target_changed(self.target_box.currentIndex())

    def _vcodec(self, path):
        """Video codec name of the first video stream (e.g. 'h264', 'hevc')."""
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=codec_name",
                                "-of", "default=nw=1:nk=1", path],
                               capture_output=True, text=True, timeout=30)
            return r.stdout.strip()
        except Exception:
            return ""

    def _symlink_serve(self, path):
        """Serve an H.264 file via a safe-named symlink in CACHE. go-chromecast
        embeds the path in its URL query where '+' etc. misbehave, so the served
        name must be sanitized; a symlink avoids copying."""
        os.makedirs(CACHE, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(path)) or "video"
        link = os.path.join(CACHE, safe)
        try:
            if os.path.lexists(link):
                os.remove(link)
            os.symlink(os.path.abspath(path), link)
            return link
        except OSError:
            return path

    def _launch_caster(self, args, env=None):
        """Start go-chromecast on a PTY (so it keeps its HTTP server alive) and
        drain its output. Returns the Popen. Its process group is killed on stop,
        taking any ffmpeg transcode child with it."""
        master, slavefd = pty.openpty()
        p = subprocess.Popen(args, stdin=slavefd, stdout=slavefd, stderr=slavefd,
                             start_new_session=True, close_fds=True, env=env)
        os.close(slavefd)
        self.caster = p
        threading.Thread(target=self._drain_pty, args=(master,), daemon=True).start()
        return p

    def _kill_caster(self):
        """Terminate the go-chromecast process group we own (if any). Using the
        group takes down any ffmpeg child it spawned for transcoding too."""
        p = self.caster
        self.caster = None
        if p and p.poll() is None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(os.getpgid(p.pid), sig)
                    p.wait(timeout=3)
                    break
                except ProcessLookupError:
                    break
                except Exception:
                    pass

    def _drain_pty(self, master):
        """Read go-chromecast's PTY output to CASTER_LOG until it exits, so the
        terminal buffer never fills and stalls it (and we can see why it dies)."""
        try:
            with open(CASTER_LOG, "wb") as f:
                while True:
                    data = os.read(master, 4096)
                    if not data:
                        break
                    f.write(data)
                    f.flush()
        except OSError:
            pass
        finally:
            try:
                os.close(master)
            except OSError:
                pass

    def _kill_vlc(self):
        p = self.vlc_proc
        self.vlc_proc = None
        if p and p.poll() is None:
            try:
                p.terminate(); p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _cast_vlc(self, path):
        """Play a file/URL in VLC on this machine, with its HTTP control iface
        enabled so our transport + Now Playing drive it. VLC handles every codec
        and plays smb://, nfs://, http:// URLs directly - no transcode/serve."""
        self.now_title = os.path.basename(path.rstrip("/")) or path
        self.now_live = False
        self._casting = True
        self._np_pos = 0; self._np_playing = False
        self.np_title.setText(f"Opening in VLC: {self.now_title}…")
        self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
        self.np_time.setText("")
        self.b_cast.setEnabled(False)
        log.info("VLC play: %s", path)

        # auto-next: queue the rest of the folder (native, smooth playlist)
        items = self._folder_queue(path) if (self.auto_next and os.path.sep in path) else [path]

        def do():
            # 1) Our HTTP VLC is running -> send the whole folder as a native
            #    playlist (seamless auto-next).
            if "error" not in vlc_status():
                self._vlc_mode = "http"
                vlc_cmd("pl_empty")
                vlc_cmd("in_play&input=" + urllib.parse.quote(items[0]))
                for it in items[1:]:
                    vlc_cmd("in_enqueue&input=" + urllib.parse.quote(it))
                log.info("VLC(http) reuse: %d item(s)", len(items))
                return "playing"
            # 2) A VLC opened by hand is running (no HTTP) -> control it over
            #    MPRIS. Load this file; auto-next is detect-end-and-load.
            if mpris_running():
                self._vlc_mode = "mpris"
                mpris_cmd("open", path_to_uri(items[0]))
                if self.auto_next:
                    # turn off repeat so a finished episode STOPS -> we detect the
                    # end and load the next (manual VLC often has loop on, which
                    # would otherwise just restart the same episode).
                    mpris_cmd("loop", "None")
                log.info("VLC(mpris) open: %s (loop=%s)", items[0], "None" if self.auto_next else "kept")
                return "playing"
            # 3) No VLC at all -> launch our own (HTTP), STAYS OPEN, native
            #    playlist. Survives app close (own session).
            self._vlc_mode = "http"
            logf = open(CASTER_LOG, "wb")
            p = subprocess.Popen(
                ["vlc", "--extraintf", "http", "--http-host", "127.0.0.1",
                 "--http-port", str(VLC_PORT), "--http-password", VLC_PW,
                 "--no-random", "--no-loop", "--no-repeat", *items],
                stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                start_new_session=True)
            logf.close()
            self.vlc_proc = p
            ok = False
            for _ in range(20):
                time.sleep(0.5)
                if p.poll() is not None:
                    break
                if "error" not in vlc_status():
                    ok = True
                    break
            log.info("vlc pid=%s up=%s", p.pid, ok)
            return "playing" if ok else "VLC failed to start (see caster.log)"

        def done(res):
            self._casting = False
            self.b_cast.setEnabled(True)
            if res != "playing":
                self.np_title.setText(str(res))
            self.poll_cast()
        run_async(do, done)

    def _folder_queue(self, path):
        """Sorted playable files in path's folder, from `path` to the end."""
        folder = os.path.dirname(path)
        exts = tuple(g[1:] for g in VIDEO_GLOBS)
        try:
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                            if f.lower().endswith(exts) and os.path.isfile(os.path.join(folder, f))),
                           key=str.lower)
        except OSError:
            return [path]
        try:
            return files[files.index(path):]
        except ValueError:
            return [path]

    def _next_file(self, path):
        q = self._folder_queue(path)
        return q[1] if len(q) > 1 else None

    def _prev_file(self, path):
        folder = os.path.dirname(path)
        exts = tuple(g[1:] for g in VIDEO_GLOBS)
        try:
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                            if f.lower().endswith(exts) and os.path.isfile(os.path.join(folder, f))),
                           key=str.lower)
            i = files.index(path)
            return files[i - 1] if i > 0 else None
        except (OSError, ValueError):
            return None

    def go_next(self):
        if self.target == "vlc" and self._vlc_mode == "http":
            run_async(lambda: vlc_cmd("pl_next"), lambda _: self.poll_cast())
        elif self._now_path:   # cast, or vlc(mpris): load the next file ourselves
            nxt = self._next_file(self._now_path)
            if nxt:
                self._cast(nxt)

    def go_prev(self):
        if self.target == "vlc" and self._vlc_mode == "http":
            run_async(lambda: vlc_cmd("pl_previous"), lambda _: self.poll_cast())
        elif self._now_path:
            prv = self._prev_file(self._now_path)
            if prv:
                self._cast(prv)

    def _set_autonext(self, on):
        self.auto_next = bool(on)
        config.CFG["auto_next"] = self.auto_next
        try:
            save_config(config.CFG)
        except Exception:
            pass

    def _cast(self, path):
        self._now_path = path
        self._saw_playing = False
        self._user_stopped = False
        if self.target == "vlc":
            self._cast_vlc(path)
            return
        self.now_title = os.path.basename(path)   # real name, for the display
        vcodec = self._vcodec(path)               # quick probe to choose the path
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        # The TV plays H.264 in an mp4-family container directly (no ffmpeg at all).
        direct = (vcodec == "h264" and ext in ("mp4", "m4v", "mov"))
        # Otherwise stream live. H.264 just needs a container REMUX (-c:v copy,
        # near-free); anything else is a real GPU/CPU transcode to H.264. Either
        # way this avoids go-chromecast's own slow CPU transcode of mkv/etc.
        copy_video = (vcodec == "h264")
        live = not direct
        self.now_live = live
        self._casting = True                       # guard polls from clearing state
        self._np_pos = 0; self._np_playing = False
        verb = "Preparing" if direct else ("Remuxing (live)" if copy_video
                                           else "Streaming (transcode)")
        self.np_title.setText(f"{verb}: {self.now_title}…")
        if live:
            self.np_bar.setRange(0, 0)       # indeterminate; a live stream has no duration
        else:
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
        self.np_time.setText("")
        self.b_cast.setEnabled(False)
        log.info("CAST requested: %s (vcodec=%s, direct=%s, copy=%s)",
                 path, vcodec, direct, copy_video)

        def do():
            self._kill_caster()                     # drop any server we were running
            gc("stop")                              # end the receiver session cleanly
            time.sleep(1)
            base = [GC, "--addr", config.TV_IP, "--port", config.CAST_PORT]
            if direct:
                # H.264 mp4: serve the file directly via a symlink (no ffmpeg).
                p = self._launch_caster(base + ["load", self._symlink_serve(path)])
                what = "direct"
            else:
                # Live stream: go-chromecast runs our wrapper and streams its stdout
                # to the TV. Instant start, no buffering. copy_video = a free remux
                # (H.264 mkv), else NVENC/libx264 transcode (HEVC etc).
                wrapper = write_wrapper(path, copy_video=copy_video)
                p = self._launch_caster(base + ["transcode", "--content-type",
                                                "video/mp4", "--command", wrapper])
                what = ("live remux" if copy_video
                        else f"live transcode ({'nvenc' if HAS_NVENC else 'cpu'})")
            time.sleep(5)                           # let it establish + the TV start
            alive = p.poll() is None
            log.info("caster pid=%s alive=%s (%s)", p.pid, alive, what)
            return "playing" if alive else f"go-chromecast exited (rc={p.returncode})"

        def done(res):
            self._casting = False
            self.b_cast.setEnabled(True)
            if isinstance(res, dict) and "error" in res:
                self.np_title.setText("error: " + res["error"])
            elif res != "playing":
                self.np_title.setText(str(res))
            # else: the status poll fills in title/progress
            self.poll_cast()
        run_async(do, done)

    def _stop(self):
        self._kill_caster()
        out = gc("stop")
        for f in glob.glob(os.path.join(CACHE, "*")):
            try:
                os.remove(f)  # removes our temp mp4s and symlinks (not their targets)
            except OSError:
                pass
        return out

    # ---- transport (routed to the active target) ----
    def _t_rewind(self):
        if self.target != "vlc":
            return gc("rewind", "30")
        return mpris_cmd("position", "30-") if self._vlc_mode == "mpris" else vlc_cmd("seek&val=-30")

    def _t_forward(self):
        if self.target != "vlc":
            return gc("seek", "30")
        return mpris_cmd("position", "30+") if self._vlc_mode == "mpris" else vlc_cmd("seek&val=+30")

    def _t_playpause(self):
        if self.target != "vlc":
            return gc("togglepause")
        return mpris_cmd("play-pause") if self._vlc_mode == "mpris" else vlc_cmd("pl_pause")

    def _t_stop(self):
        self._user_stopped = True   # suppress auto-next for a deliberate stop
        if self.target == "vlc":
            self.now_title = None
            # stop playback but leave VLC open, on whichever backend is active
            return mpris_cmd("stop") if self._vlc_mode == "mpris" else vlc_cmd("pl_stop")
        return self._stop()

    def _on_ended(self):
        """Current item finished on its own. Auto-advance (Cast, or VLC in MPRIS
        mode where there's no native playlist) or clear. HTTP-mode VLC advances
        its own playlist, so we don't drive it here."""
        advance = self.auto_next and not self._user_stopped and self._now_path
        app_driven = (self.target == "cast") or (self.target == "vlc" and self._vlc_mode == "mpris")
        if advance and app_driven:
            nxt = self._next_file(self._now_path)
            if nxt:
                log.info("auto-next -> %s", os.path.basename(nxt))
                self._cast(nxt)
                return
        self._kill_caster()   # stop the Cast serving process; leave VLC open
        self.now_title = None; self.now_live = False
        self._saw_playing = False; self._now_path = None; self._np_playing = False
        self.np_badge.hide()
        self.np_title.setText("Nothing casting.")
        self.np_bar.setRange(0, 1000); self.np_bar.setValue(0); self.np_time.setText("")

    def closeEvent(self, event):
        self._kill_caster()   # leave VLC running on its own
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
        super().closeEvent(event)

    def _transport(self, fn):
        run_async(fn, lambda _: self.poll_cast())

    # ---- polling ----
    def poll_tv(self):
        if not config.HOST:
            return
        run_async(sc_status, self._tv_done)

    def _tv_done(self, s):
        if "error" in s:
            self._tv_text = "TV: unreachable"
        else:
            m = "  muted" if s["mute"] == "On" else ""
            self._tv_text = f"TV {s['power']}  ·  {s['input']}  ·  vol {s['vol']}{m}"
        self.tv_status.setText(self._tv_text)

    # ---- phone remote (web) ----
    def _update_web_label(self):
        web = config.CFG.get("web", {})
        self.web_label.setText(
            f"📱 Phone remote:  http://{local_ip()}:{web.get('port', 8950)}    ·    pair in ⚙ Setup")

    def show_pair_qr(self):
        """Pop a modal QR of the tokenized URL for scan-to-pair (shown only while
        open, so the token isn't left on screen). Falls back to the PIN."""
        web = config.CFG.get("web", {})
        ip, port = local_ip(), web.get("port", 8950)
        url = f"http://{ip}:{port}/#t={web.get('token', '')}"
        png = None
        try:
            r = subprocess.run(["qrencode", "-t", "PNG", "-s", "9", "-m", "2", "-o", "-", url],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                png = r.stdout
        except Exception:
            pass
        dlg = QDialog(self); dlg.setWindowTitle("Pair a phone")
        lay = QVBoxLayout(dlg)
        head = QLabel("Scan with your phone's camera — it pairs instantly.")
        head.setWordWrap(True); lay.addWidget(head)
        if png:
            pm = QPixmap(); pm.loadFromData(png, "PNG")
            img = QLabel(); img.setPixmap(pm); img.setAlignment(Qt.AlignCenter)
            lay.addWidget(img)
        sub = QLabel(f"Or open  http://{ip}:{port}  and enter PIN  {web.get('pin', '------')}")
        sub.setObjectName("dim"); sub.setWordWrap(True); lay.addWidget(sub)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject); bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

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
            "target_index": self.target_box.currentIndex(),
            "targets": targets,
            "sources": sources,
            "auto_next": self.auto_next,
            "tv": self._tv_text if config.HOST else "",
            "now": {"title": self.now_title or "", "pos": self._np_pos,
                    "dur": self._np_dur, "state": self._np_state, "live": self.now_live},
        }

    def _web_do(self, kind, p):
        """Apply a phone action on the main thread."""
        try:
            if kind == "key":
                self.remote.send(p["cmd"])
            elif kind == "transport":
                act = p.get("action")
                if act == "next":
                    self.go_next()
                elif act == "prev":
                    self.go_prev()
                else:
                    fn = {"rewind": self._t_rewind, "playpause": self._t_playpause,
                          "forward": self._t_forward, "stop": self._t_stop}.get(act)
                    if fn:
                        self._transport(fn)
            elif kind == "target":
                self.target_box.setCurrentIndex(int(p["index"]))
            elif kind == "autonext":
                self.autonext_cb.setChecked(bool(p.get("on")))
            elif kind == "cast":
                path = p.get("path", "")
                if os.path.isfile(path) and path.lower().endswith(tuple(g[1:] for g in VIDEO_GLOBS)):
                    self._cast(path)
        except Exception as e:
            log.warning("web action %s failed: %s", kind, e)

    def poll_cast(self):
        if self.target == "vlc":
            run_async(vlc_any_status, self._vlc_done)
        else:
            run_async(lambda: gc("status", timeout=8), self._cast_done)

    def _vlc_done(self, s):
        if not isinstance(s, dict) or "error" in s:
            # No VLC reachable on either backend
            if self._casting or (self.vlc_proc and self.vlc_proc.poll() is None):
                return
            if self._saw_playing:          # VLC finished the playlist (or was closed)
                self._on_ended()
                return
            self.now_title = None
            self._np_playing = False
            self.np_title.setText("Nothing playing in VLC.")
            self.np_badge.hide()
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
            self.np_time.setText("")
            return
        state = s.get("state", "")
        if state == "STOPPED" and not self._casting:
            if self._saw_playing:
                self._on_ended()
                return
            self.np_title.setText("Nothing playing in VLC.")
            self.np_badge.hide()
            self.np_bar.setValue(0); self.np_time.setText("")
            self._np_playing = False
            return
        self.now_live = False
        self._vlc_mode = s.get("_mode", self._vlc_mode)
        # Track what VLC is actually on (esp. MPRIS / a manually-opened file) so
        # Next/Prev know the folder, and the status filename is the display truth.
        if s.get("path"):
            self._now_path = s["path"]
        self.np_title.setText(s.get("file", "") or self.now_title or "VLC")
        self._np_pos = s.get("pos", 0)
        self._np_dur = s.get("dur", 0)
        self._np_state = state
        self._np_playing = (state == "PLAYING")
        if state == "PLAYING":
            self._saw_playing = True
        self._render_np()

    def _cast_done(self, res):
        ok, out = res if isinstance(res, tuple) else (False, str(res))
        line = out.splitlines()[0] if out else ""
        if not line or "Idle" in line or "Backdrop" in line:
            # If we've already seen this cast play, an Idle means it FINISHED ->
            # auto-advance (or stop). Otherwise it's the startup transient while
            # the receiver app launches - keep the title/live flag.
            if self._saw_playing and not self._casting:
                self._on_ended()
                return
            if self._casting or (self.caster and self.caster.poll() is None):
                return
            self.now_title = None
            self.now_live = False
            self._np_playing = False
            self.np_title.setText("Nothing casting.")
            self.np_badge.hide()
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
            self.np_time.setText("")
            return
        d = parse_status(line)
        self.np_title.setText(self.now_title or d.get("file", "") or "Playing")
        # resync the local clock to the TV's reported position, then let _tick
        # advance it smoothly between the 3s polls.
        self._np_pos = d.get("pos", 0)
        self._np_dur = d.get("dur", 0)
        self._np_state = d.get("state", "")
        self._np_playing = self._np_state in ("PLAYING", "")
        if self._np_state == "PLAYING":
            self._saw_playing = True
        self._render_np()

    def _render_np(self):
        pos, dur, state = self._np_pos, self._np_dur, self._np_state
        self.np_badge.setVisible(self.now_live)
        if self.now_live:
            self.np_bar.setRange(0, 0)   # live: no real duration/seek
            mark = state.lower() if state and state != "PLAYING" else "live"
            self.np_time.setText(f"{fmt_time(pos)}  ·  {mark}")
        elif dur > 0:
            self.np_bar.setRange(0, dur); self.np_bar.setValue(min(int(pos), dur))
            tail = f"  ·  {state.lower()}" if state and state != "PLAYING" else ""
            self.np_time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}{tail}")
        else:
            self.np_bar.setRange(0, 0)   # unknown duration (still buffering)
            self.np_time.setText(state.lower())

    def _tick(self):
        """Advance the displayed position one second between polls (smooth clock)."""
        if self._np_playing and (self.now_live or self._np_dur > 0):
            if not self.now_live and self._np_dur:
                self._np_pos = min(self._np_pos + 1, self._np_dur)
            else:
                self._np_pos += 1
            self._render_np()
