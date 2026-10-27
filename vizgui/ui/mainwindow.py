"""The main window: ties media browsing, casting, transport, polling, the phone
remote, and status together."""
import os
import secrets
from PySide6.QtCore import Qt, QTimer, QDir, QModelIndex
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QTreeView, QSplitter, QSizePolicy, QFileSystemModel, QProgressBar, QComboBox,
    QDialog, QDialogButtonBox, QMessageBox, QCheckBox,
)

from .. import config
from .. import platforms
from .. import health
from ..config import save_config, load_config
from ..paths import VIDEO_GLOBS, log
from ..smartcast import sc_status
from ..sources import source_path
from ..net import local_ip, lan_cidr
from ..helpers import fmt_time
from ..qtasync import run_async
from ..webremote import WebBridge, start_web_server
from ..playback import CastTarget, VlcTarget
from ..playback import media
from .remote import RemotePanel
from .dialogs import SetupDialog, SmbDialog


def qr_pixmap(data, box=8, border=3):
    """Render ``data`` as a QR QPixmap via the pure-Python ``qrcode`` lib (no
    external binary, no PIL — the module matrix is painted with QPainter).
    Returns None if qrcode isn't importable, so the UI can fall back to the PIN."""
    try:
        import qrcode
    except Exception:
        return None
    qr = qrcode.QRCode(border=border, box_size=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()                 # list[list[bool]], includes the border
    size = len(matrix) * box
    img = QImage(size, size, QImage.Format_RGB32)
    img.fill(QColor("white"))
    painter = QPainter(img)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("black"))
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                painter.drawRect(x * box, y * box, box, box)
    painter.end()
    return QPixmap.fromImage(img)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Basement Display")
        self.resize(980, 640)
        self.target = "cast"    # "cast" (TV via go-chromecast) or "vlc" (this PC)
        self.targets = {"cast": CastTarget(), "vlc": VlcTarget()}   # playback adapters
        self.active = self.targets[self.target]
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
        self.statusBar().showMessage("Ready", 3000)
        self._health_check()
        self.poll_tv(); self.poll_cast()

    # ---- status surface + startup health ----
    def _status(self, msg, timeout=6000):
        self.statusBar().showMessage(msg, timeout)

    def _health_check(self):
        miss = health.missing(config.CFG)
        if miss:
            names = ", ".join(m[0] for m in miss)
            self._status(f"⚠ Missing: {names} — hover for details", 0)
            detail = "Some tools aren't installed:\n" + \
                     "\n".join(f"  • {t} — {why}" for t, why in miss)
            self.statusBar().setToolTip(detail)
            log.warning("health: missing tools: %s", miss)
        else:
            self.statusBar().setToolTip("")

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
        self.active = self.targets.get(self.target, self.targets["cast"])
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
            self._status(f"Connecting to {name}…", 0)
            def done(res):
                if isinstance(res, str) and self._root_at(res):
                    self._status(f"Connected to {name}", 4000)
                    return
                self._status(f"Couldn't open '{name}': {res}")
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

    def go_next(self):
        if self.active.native_skip():
            run_async(lambda: self.active.skip(1), lambda _: self.poll_cast())
        elif self._now_path:
            nxt = media.next_file(self._now_path)
            if nxt:
                self._cast(nxt)

    def go_prev(self):
        if self.active.native_skip():
            run_async(lambda: self.active.skip(-1), lambda _: self.poll_cast())
        elif self._now_path:
            prv = media.prev_file(self._now_path)
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
        ok, msg = self.active.available()
        if not ok:
            self.np_title.setText(msg)
            self._status(msg)
            return
        self.now_title = os.path.basename(path.rstrip(os.sep)) or path
        self._casting = True
        self._np_pos = 0
        self._np_playing = False
        self.np_title.setText(f"Preparing: {self.now_title}\u2026")
        self.np_bar.setRange(0, 0)          # indeterminate until the first status
        self.np_time.setText("")
        self.np_badge.hide()
        self.b_cast.setEnabled(False)
        items = media.folder_queue(path) if (self.auto_next and os.sep in path) else [path]
        log.info("CAST requested: %s -> %s", path, self.active.name)

        def done(res):
            self._casting = False
            self.b_cast.setEnabled(True)
            if res != "playing":
                self.np_title.setText(str(res))
                self._status(str(res))
            self.poll_cast()
        run_async(lambda: self.active.start(path, items, self.auto_next), done)

    def _t_rewind(self):
        return self.active.seek(-30)

    def _t_forward(self):
        return self.active.seek(30)

    def _t_playpause(self):
        return self.active.pause()

    def _t_stop(self):
        self._user_stopped = True
        if self.target == "vlc":
            self.now_title = None
        return self.active.stop()

    def _on_ended(self):
        """Current item finished. Auto-advance if the target needs us to, else clear."""
        if (self.auto_next and not self._user_stopped and self._now_path
                and self.active.needs_advance_on_end()):
            nxt = media.next_file(self._now_path)
            if nxt:
                log.info("auto-next -> %s", os.path.basename(nxt))
                self._cast(nxt)
                return
        self.active.shutdown()
        self.now_title = None
        self.now_live = False
        self._saw_playing = False
        self._now_path = None
        self._np_playing = False
        self.np_badge.hide()
        self.np_title.setText("Nothing playing.")
        self.np_bar.setRange(0, 1000)
        self.np_bar.setValue(0)
        self.np_time.setText("")

    def closeEvent(self, event):
        for t in self.targets.values():
            t.shutdown()           # cast: kill the server; vlc: leave it running
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
        pm = qr_pixmap(url)
        dlg = QDialog(self); dlg.setWindowTitle("Pair a phone")
        lay = QVBoxLayout(dlg)
        head = QLabel("Scan with your phone's camera — it pairs instantly.")
        head.setWordWrap(True); lay.addWidget(head)
        if pm is not None:
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

    def web_add_cast(self, dev):
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
            self._reload_targets()
        for i in range(self.target_box.count()):
            t = self.target_box.itemData(i)
            if t and t.get("type") == "cast" and t.get("addr") == addr:
                self.target_box.setCurrentIndex(i)
                break
        self._status(f"Added {dev.get('name') or addr}")

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
            elif kind == "addcast":
                self.web_add_cast(p)
            elif kind == "autonext":
                self.autonext_cb.setChecked(bool(p.get("on")))
            elif kind == "cast":
                path = p.get("path", "")
                if os.path.isfile(path) and path.lower().endswith(tuple(g[1:] for g in VIDEO_GLOBS)):
                    self._cast(path)
        except Exception as e:
            log.warning("web action %s failed: %s", kind, e)

    def poll_cast(self):
        run_async(self.active.status, self._poll_done)

    def _poll_done(self, st):
        if not isinstance(st, dict):
            return
        if st.get("idle"):
            # our process still starting, or mid-launch -> transient, keep state
            if self._casting or st.get("busy"):
                return
            if self._saw_playing:
                self._on_ended()
                return
            self.now_title = None
            self.now_live = False
            self._np_playing = False
            self.np_badge.hide()
            self.np_title.setText("Nothing playing.")
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
            self.np_time.setText("")
            return
        self.now_live = st.get("live", False)
        if st.get("path"):          # MPRIS gives the real path -> Next/Prev + title
            self._now_path = st["path"]
        if self.target == "vlc":
            title = st.get("file") or self.now_title or "VLC"
        else:
            title = self.now_title or st.get("file") or "Playing"
        self.np_title.setText(title)
        self._np_pos = st.get("pos", 0)
        self._np_dur = st.get("dur", 0)
        self._np_state = st.get("state", "")
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
