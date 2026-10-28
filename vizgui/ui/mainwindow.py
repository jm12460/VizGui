"""The main window: ties media browsing, casting, transport, polling, the phone
remote, and status together."""
import os
import secrets
import time
from PySide6.QtCore import Qt, QTimer, QDir, QModelIndex, QSize
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QTreeView, QSplitter, QSizePolicy, QFileSystemModel, QProgressBar, QComboBox,
    QDialog, QDialogButtonBox, QMessageBox, QCheckBox, QListWidget, QListWidgetItem, QMenu,
    QLineEdit, QStackedWidget,
)

from .. import config
from .. import platforms
from .. import health
from .. import history
from .. import probe
from ..config import save_config, load_config
from ..paths import VIDEO_GLOBS, log
from ..smartcast import sc_status
from ..sources import source_path
from ..net import local_ip, lan_cidr
from ..helpers import fmt_time
from ..qtasync import run_async
from ..webremote import WebBridge, start_web_server, web_path_ok, web_search
from ..playback import CastTarget, VlcTarget
from ..playback import media
from .remote import RemotePanel
from .icons import ThemedIconProvider
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
        self._recovered_cast_session = False
        self._startup_recast_attempted = False
        # last-known playback state, advanced locally once a second between polls
        self._np_pos = 0; self._np_dur = 0; self._np_state = ""; self._np_playing = False
        self._resume_base = 0       # seconds a live stream was resumed from (pos is relative)
        self._last_save = 0.0       # throttle history writes during playback
        self._media = {"audio": [], "subs": [], "chapters": []}   # ffprobe of current file
        self._audio = None          # selected audio track (0-based), None = file default
        self._sub = None            # selected subtitle (probe descriptor), None = off

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
        self.b_continue = QPushButton("▶ Continue watching")
        self.b_continue.setToolTip("Resume something you were watching")
        self.b_continue.clicked.connect(self.show_continue)
        self.b_setup = QPushButton("⚙ Setup"); self.b_setup.clicked.connect(self.open_setup)
        hdr.addWidget(t); hdr.addStretch(1)
        hdr.addWidget(QLabel("Source:")); hdr.addWidget(self.source_box)
        hdr.addWidget(self.b_continue)
        hdr.addWidget(self.b_setup)
        bl.addLayout(hdr)
        # search bar: recursive video-name search within the current source
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search videos in this source…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._on_search_text)
        self.search_box.returnPressed.connect(self._run_search)
        bl.addWidget(self.search_box)
        self._search_timer = QTimer(self)      # debounce typing
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._run_search)
        self._search_seq = 0                   # drop out-of-order async results

        self.model = QFileSystemModel()
        self.model.setNameFilters(VIDEO_GLOBS)
        self.model.setNameFilterDisables(False)
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self._icon_provider = ThemedIconProvider()   # flat folder/video glyphs
        self.model.setIconProvider(self._icon_provider)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)              # smooth expand/collapse
        self.tree.setUniformRowHeights(True)     # consistent rows + faster
        self.tree.setExpandsOnDoubleClick(False)  # folders toggle on a single click
        self.tree.clicked.connect(self._on_tree_click)
        self.tree.doubleClicked.connect(self.on_double)
        # flat search-results list, shown in place of the tree while searching
        self.results = QListWidget()
        self.results.setIconSize(QSize(18, 18))
        self.results.setSpacing(1)
        self.results.itemActivated.connect(self._result_activated)
        self.results.itemDoubleClicked.connect(self._result_activated)
        self.browse_stack = QStackedWidget()
        self.browse_stack.addWidget(self.tree)        # index 0: folder tree
        self.browse_stack.addWidget(self.results)     # index 1: search results
        bl.addWidget(self.browse_stack)
        self._source_changed(self.source_box.currentIndex())  # root at first source
        row = QHBoxLayout()
        self.b_cast = QPushButton("Cast selected"); self.b_cast.setProperty("class", "accent")
        self.b_cast.clicked.connect(self.cast_selected)
        self.target_box = QComboBox()
        self._reload_targets()
        # Plain mirror of the selected target index, so the HTTP thread's web_status
        # never touches the QComboBox off the GUI thread. Kept current in _target_changed.
        self._web_target_index = self.target_box.currentIndex()
        self.target_box.currentIndexChanged.connect(self._target_changed)
        # sync the active target to the configured selection, so a VLC-only setup
        # doesn't leave 'cast' active and poll go-chromecast with no device
        t0 = self.target_box.currentData()
        if t0:
            self.target = t0.get("type", "cast")
            self.active = self.targets.get(self.target, self.targets["cast"])
            if self.target == "cast":
                config.set_active_cast(t0)
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
        self.b_subs = QPushButton("Subtitles ▾"); self.b_subs.hide()
        self.b_subs.clicked.connect(self._subs_menu)
        self.b_audio = QPushButton("Audio ▾"); self.b_audio.hide()
        self.b_audio.clicked.connect(self._audio_menu)
        self.b_chapters = QPushButton("Chapters ▾"); self.b_chapters.hide()
        self.b_chapters.clicked.connect(self._chapters_menu)
        self.autonext_cb = QCheckBox("Auto-play next")
        self.autonext_cb.setChecked(self.auto_next)
        self.autonext_cb.toggled.connect(self._set_autonext)
        nhdr.addWidget(nt); nhdr.addStretch(1); nhdr.addWidget(self.np_badge)
        nhdr.addWidget(self.b_subs)
        nhdr.addWidget(self.b_audio)
        nhdr.addWidget(self.b_chapters)
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

    def _on_tree_click(self, idx):
        """Single click toggles a folder open/closed (files just select)."""
        if idx.isValid() and self.model.isDir(idx):
            self.tree.setExpanded(idx, not self.tree.isExpanded(idx))

    def on_double(self, idx):
        p = self.model.filePath(idx)
        if os.path.isfile(p):
            self._play(p)

    def _play(self, path):
        """Play a browsed file, offering to resume if we remember a position."""
        r = history.resume_pos(path)
        resume = 0
        if r > 0:
            box = QMessageBox(self)
            box.setWindowTitle("Resume")
            box.setText(f"Resume “{os.path.basename(path)}” from {fmt_time(r)}?")
            rb = box.addButton("Resume", QMessageBox.AcceptRole)
            box.addButton("Start over", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is rb:
                resume = r
        self._cast(path, resume)

    def _remember(self):
        """Record where the current file stopped, for continue-watching. For a live
        stream the reported pos is relative to the resume point, so add the base."""
        if self._now_path and (self._np_dur > 0 or self.now_live):
            base = self._resume_base if self.now_live else 0
            history.record(self._now_path, self._np_pos + base, self._np_dur,
                           self.now_title or "")
            self._last_save = time.time()

    def _recover_cast_item(self, st):
        """After an app restart, reconnect the UI to the active Cast item. The
        direct-vs-transcode matching rules live in history.recover_match."""
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
        # a direct cast identified from history needs its source saved for next time
        if media_file != "pipe_output" and (history.current() or {}).get("path") != m["path"]:
            history.set_current(m["path"], m["title"], 0)
        self._saw_playing = st.get("state") in ("PLAYING", "PAUSED")
        log.info("recovered active Cast item: %s", self.now_title)

    def cast_selected(self):
        p = self._sel_path()
        if p:
            self._play(p)
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
        self._web_target_index = idx   # GUI-thread cache the web status reads
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
        if getattr(self, "search_box", None) and self.search_box.text():
            self.search_box.clear()   # results belong to the old source; back to tree
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

    # ---- recursive search within the current source ----
    def _on_search_text(self, text):
        if text.strip():
            self._search_timer.start()        # debounce; fires _run_search
        else:
            self._search_timer.stop()
            self._search_seq += 1             # invalidate any in-flight search
            self.browse_stack.setCurrentWidget(self.tree)

    def _run_search(self):
        q = self.search_box.text().strip()
        if not q:
            self.browse_stack.setCurrentWidget(self.tree)
            return
        src_idx = self.source_box.currentIndex()
        self._search_seq += 1
        seq = self._search_seq
        self.browse_stack.setCurrentWidget(self.results)
        self.results.clear()
        self.results.addItem(QListWidgetItem("Searching…"))
        # web_search is filesystem-only (and may mount an SMB share), so run it off
        # the GUI thread; ignore it if a newer keystroke already started another.
        run_async(lambda: web_search(src_idx, q),
                  lambda res, s=seq, term=q: self._fill_results(res, term, s))

    def _fill_results(self, res, q, seq):
        if seq != self._search_seq:
            return                            # superseded by a later search
        self.results.clear()
        if not isinstance(res, dict) or not res.get("ok"):
            err = res.get("error", "search failed") if isinstance(res, dict) else "search failed"
            self.results.addItem(QListWidgetItem(f"⚠ {err}"))
            return
        entries = res.get("entries", [])
        if not entries:
            self.results.addItem(QListWidgetItem(f"No videos matching “{q}”."))
            return
        for e in entries:
            icon = "📁" if e.get("dir") else "🎬"
            it = QListWidgetItem(f"{icon}  {e['name']}\n      {e.get('folder') or '/'}")
            it.setData(Qt.UserRole, e["path"])
            it.setData(Qt.UserRole + 1, bool(e.get("dir")))
            self.results.addItem(it)
        if res.get("truncated"):
            self.results.addItem(QListWidgetItem("… more matches not shown — refine your search."))

    def _result_activated(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return
        if item.data(Qt.UserRole + 1):          # a matched folder -> reveal it in the tree
            self.search_box.clear()             # back to the tree view (same source root)
            idx = self.model.index(path)
            if idx.isValid():
                self.tree.setCurrentIndex(idx)
                self.tree.expand(idx)
                self.tree.scrollTo(idx)
        elif os.path.isfile(path):
            self._play(path)

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

    def show_continue(self):
        """Continue-watching list: resume on the current target, forget, or clear."""
        items = [it for it in history.recent() if it.get("path") != self._now_path]
        dlg = QDialog(self); dlg.setWindowTitle("Continue watching"); dlg.resize(460, 420)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for it in items:
            dur = it.get("dur", 0)
            when = f"     {fmt_time(it.get('pos', 0))} / {fmt_time(dur)}" if dur else ""
            w = QListWidgetItem(f"{it.get('title', '?')}{when}")
            w.setData(Qt.UserRole, it)
            lst.addItem(w)
        if items:
            lay.addWidget(lst, 1)
        else:
            lay.addWidget(QLabel("Nothing to continue yet — play something and it'll appear here."))

        def resume():
            w = lst.currentItem()
            if w:
                d = w.data(Qt.UserRole)
                dlg.accept()
                self._cast(d["path"], int(d.get("pos", 0)))

        def forget():
            w = lst.currentItem()
            if w:
                history.forget(w.data(Qt.UserRole)["path"])
                lst.takeItem(lst.row(w))

        def clear():
            if QMessageBox.question(dlg, "Clear history",
                    "Forget everything in Continue watching?") == QMessageBox.Yes:
                history.clear()
                dlg.accept()

        lst.itemDoubleClicked.connect(lambda _w: resume())
        row = QHBoxLayout()
        if items:
            b_resume = QPushButton("Resume"); b_resume.setProperty("class", "accent")
            b_resume.clicked.connect(resume)
            b_forget = QPushButton("Forget"); b_forget.clicked.connect(forget)
            row.addWidget(b_resume); row.addWidget(b_forget)
        b_clear = QPushButton("Clear all"); b_clear.clicked.connect(clear)
        row.addStretch(1); row.addWidget(b_clear)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Close); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

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

    def _cast(self, path, resume=0, audio=None, sub=None):
        self._remember()                 # save position of the file we're leaving
        self._now_path = path
        self._resume_base = resume
        self._audio = audio
        self._sub = sub
        self._last_save = time.time()
        self._saw_playing = False
        self._user_stopped = False
        ok, msg = self.active.available()
        if not ok:
            self.np_title.setText(msg)
            self._status(msg)
            return
        self.now_title = os.path.basename(path.rstrip(os.sep)) or path
        if self.target == "cast":
            history.set_current(path, self.now_title, resume)
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
                if self.target == "cast":
                    history.clear_current()
                self.np_title.setText(str(res))
                self._status(str(res))
            self.poll_cast()
        run_async(lambda: self.active.start(path, items, self.auto_next, resume, audio, sub), done)
        self._media = {"audio": [], "subs": [], "chapters": []}
        self.b_chapters.hide(); self.b_audio.hide(); self.b_subs.hide()
        run_async(lambda: probe.probe(path), lambda r: self._probe_done(path, r))

    def _probe_done(self, path, res):
        if path != self._now_path:   # cast failed or moved on -> don't show stale controls
            return
        self._media = res if isinstance(res, dict) and "chapters" in res else \
            {"audio": [], "subs": [], "chapters": []}
        self.b_chapters.setVisible(bool(self._media.get("chapters")))
        self.b_audio.setVisible(len(self._media.get("audio", [])) > 1)
        self.b_subs.setVisible(bool(self._media.get("subs")))

    def _subs_menu(self):
        subs = self._media.get("subs", [])
        if not subs:
            return
        menu = QMenu(self)
        off = menu.addAction(("●  " if self._sub is None else "     ") + "Off")
        off.triggered.connect(lambda: self._select_sub(None))
        menu.addSeparator()
        for s in subs:
            active = self._sub is not None and self._sub.get("i") == s["i"]
            extra = ("  (image)" if s.get("kind") == "image" else "") + \
                    ("  [file]" if s.get("external") else "")
            act = menu.addAction(("●  " if active else "     ") + s["label"] + extra)
            act.triggered.connect(lambda _=False, sub=s: self._select_sub(sub))
        menu.exec(self.b_subs.mapToGlobal(self.b_subs.rect().bottomLeft()))

    def _select_sub(self, sub):
        """Turn subtitles on/off by re-casting at the current position (keeps audio)."""
        if not self._now_path:
            return
        cur = self._sub.get("i") if self._sub else None
        new = sub.get("i") if sub else None
        if cur == new:
            return
        pos = self._np_pos + (self._resume_base if self.now_live else 0)
        self._cast(self._now_path, pos, audio=self._audio, sub=sub)

    def _audio_menu(self):
        tracks = self._media.get("audio", [])
        if len(tracks) < 2:
            return
        cur = self._audio if self._audio is not None else \
            next((t["i"] for t in tracks if t.get("default")), 0)
        menu = QMenu(self)
        for t in tracks:
            mark = "●  " if t["i"] == cur else "     "
            act = menu.addAction(mark + t["label"])
            act.triggered.connect(lambda _=False, i=t["i"]: self._select_audio(i))
        menu.exec(self.b_audio.mapToGlobal(self.b_audio.rect().bottomLeft()))

    def _select_audio(self, i):
        """Switch audio track by re-casting the current file at its current position."""
        if not self._now_path or i == self._audio:
            return
        pos = self._np_pos + (self._resume_base if self.now_live else 0)
        self._cast(self._now_path, pos, audio=i, sub=self._sub)

    def _chapters_menu(self):
        chaps = self._media.get("chapters", [])
        if not chaps:
            return
        menu = QMenu(self)
        for c in chaps:
            act = menu.addAction(f"{fmt_time(c['start'])}    {c['title']}")
            act.triggered.connect(lambda _=False, s=c["start"]: self._jump_to(s))
        menu.exec(self.b_chapters.mapToGlobal(self.b_chapters.rect().bottomLeft()))

    def _jump_to(self, secs):
        """Jump to an absolute time (chapter). A live stream can't seek, so restart
        it at the offset; direct cast and VLC seek in place."""
        secs = int(secs)
        if self.now_live and self._now_path:
            self._cast(self._now_path, secs, audio=self._audio, sub=self._sub)
        else:
            run_async(lambda: self.active.seek_to(secs), lambda _: self.poll_cast())

    def _t_rewind(self):
        return self.active.seek(-30)

    def _t_forward(self):
        return self.active.seek(30)

    def _t_playpause(self):
        return self.active.pause()

    def _t_stop(self):
        self._user_stopped = True
        if self.target == "cast":
            history.clear_current()
        self._remember()
        if self.target == "vlc":
            self.now_title = None
        return self.active.stop()

    def _on_ended(self, allow_auto_next=True, finished=True):
        """Current item finished. Auto-advance if the target needs us to, else clear."""
        old_path = self._now_path
        unknown_duration_live_end = finished and self.now_live and self._np_dur <= 0
        if not unknown_duration_live_end:
            self._remember()             # finished -> dropped from the continue list
        nxt = (media.next_file(self._now_path)
               if allow_auto_next and self.auto_next and not self._user_stopped
               and self._now_path and self.active.needs_advance_on_end() else None)
        if nxt:
            log.info("auto-next -> %s", os.path.basename(nxt))
            self._cast(nxt)
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
        self.b_chapters.hide(); self.b_audio.hide(); self.b_subs.hide()
        self.np_badge.hide()
        self.np_title.setText("Nothing playing.")
        self.np_bar.setRange(0, 1000)
        self.np_bar.setValue(0)
        self.np_time.setText("")

    def closeEvent(self, event):
        self._remember()
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

    def web_history(self):
        """Continue-watching items for the phone, minus whatever's playing now
        (so the current file isn't duplicated under the now-playing bar)."""
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
            "target_index": self._web_target_index,
            "targets": targets,
            "sources": sources,
            "auto_next": self.auto_next,
            "tv": self._tv_text if config.HOST else "",
            "now": {"title": self.now_title or "", "pos": self._np_pos,
                    "dur": self._np_dur, "state": self._np_state, "live": self.now_live},
            "chapters": self._media.get("chapters", []),
            "audio": {"tracks": self._media.get("audio", []), "current": self._audio},
            "subs": {"tracks": self._media.get("subs", []),
                     "current": self._sub.get("i") if self._sub else None},
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
                if (path.lower().endswith(tuple(g[1:] for g in VIDEO_GLOBS))
                        and web_path_ok(path)):
                    self._cast(path, int(p.get("resume", 0) or 0))
                else:
                    log.warning("web cast refused (outside sources): %s", path)
            elif kind == "forget":
                history.forget(p.get("path", ""))
            elif kind == "clear":
                history.clear()
            elif kind == "jump":
                self._jump_to(int(p.get("secs", 0) or 0))
            elif kind == "audio":
                self._select_audio(int(p.get("i", 0) or 0))
            elif kind == "sub":
                i = p.get("i")
                s = None if i is None else next(
                    (x for x in self._media.get("subs", []) if x.get("i") == int(i)), None)
                self._select_sub(s)
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
                # Cast receivers can drop a paused session and report Idle. That
                # is not evidence the episode finished, so don't jump to the
                # next file if our last confirmed playback state was PAUSED.
                paused = self._np_state == "PAUSED"
                if paused:
                    log.info("playback went idle while paused; suppressing auto-next")
                self._on_ended(allow_auto_next=not paused, finished=not paused)
                return
            self.now_title = None
            self.now_live = False
            self._np_playing = False
            self._now_path = None
            self._media = {"audio": [], "subs": [], "chapters": []}
            self._audio = None
            self._sub = None
            if self.target == "cast":
                history.clear_current()
            self.np_badge.hide()
            self.b_chapters.hide(); self.b_audio.hide(); self.b_subs.hide()
            self.np_title.setText("Nothing playing.")
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0)
            self.np_time.setText("")
            return
        if self.target == "cast" and not self._now_path:
            self._recover_cast_item(st)
        self.now_live = st.get("live", False)
        if st.get("path"):          # MPRIS gives the real path -> Next/Prev + title
            self._now_path = st["path"]
        if self.target == "vlc":
            title = st.get("file") or self.now_title or "VLC"
        else:
            status_title = st.get("file") or "Playing"
            if status_title == "pipe_output":
                status_title = "Live cast"
            title = self.now_title or status_title
        self.np_title.setText(title)
        self._np_pos = st.get("pos", 0)
        self._np_dur = st.get("dur", 0)
        self._np_state = st.get("state", "")
        self._np_playing = self._np_state in ("PLAYING", "")
        if self._np_state == "PLAYING":
            self._saw_playing = True
        if (self._recovered_cast_session and not self._startup_recast_attempted
                and self._np_state == "PLAYING" and self._now_path):
            # The previous app owned the local HTTP stream. Re-open it from the
            # receiver's current position before its remaining buffer runs out.
            resume = self._np_pos + (self._resume_base if self.now_live else 0)
            self._startup_recast_attempted = True
            self._recovered_cast_session = False
            log.info("reattaching to Cast stream at %ss: %s", resume, self.now_title)
            self._cast(self._now_path, resume)
            return
        if self._np_playing and time.time() - self._last_save > 12:
            self._remember()         # periodic save so a crash loses little
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
