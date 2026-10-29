"""The main window: ties media browsing, casting, transport, polling, the phone
remote, and status together."""
import os
from PySide6.QtCore import Qt, QTimer, QDir, QModelIndex, QSize
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QMainWindow, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QTreeView, QSplitter, QSizePolicy, QFileSystemModel, QProgressBar, QComboBox,
    QDialog, QDialogButtonBox, QMessageBox, QCheckBox, QListWidget, QListWidgetItem, QMenu,
    QLineEdit, QStackedWidget,
)

from .. import config
from .. import health
from .. import history
from ..config import load_config
from ..paths import VIDEO_GLOBS, log
from ..sources import source_path
from ..net import local_ip
from ..helpers import fmt_time
from ..qtasync import run_async
from ..webremote import start_web_server, web_search
from ..qtpopups import show_dialog, message, confirm, popup_menu, dismiss_dialogs
from .webbridge import WebBridge
from ..engine import Engine
from .remote import RemotePanel
from .remote_tree import RemoteMediaTree
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
    def __init__(self, remote_controller=None, playback_role="owner",
                 observer_client=None):
        super().__init__()
        self.remote_controller = remote_controller
        self.connected_mode = remote_controller is not None
        self.observer_controller = None
        self._observer_snapshot = None
        self._observer_online = False
        self.setWindowTitle("vizgui")
        self.resize(980, 640)
        # All playback control + state lives in the Qt-free Engine; this window is a
        # view that drives it (timers, button clicks) and renders its state via the
        # on_change hook, so a headless server can reuse the same engine.
        self.engine = None if self.connected_mode else Engine(
            run_async=run_async, on_change=self._render,
            on_status=self._status, playback_role=playback_role)

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
        self.remote_tree = None
        if self.connected_mode:
            self.remote_tree = RemoteMediaTree(
                self.remote_controller.client, self)
            self.remote_tree.play_requested.connect(self._play)
            self.remote_tree.error_occurred.connect(self._status)
            self.browse_stack.addWidget(self.remote_tree)
        bl.addWidget(self.browse_stack)
        if not self.connected_mode:
            self._source_changed(self.source_box.currentIndex())
        row = QHBoxLayout()
        self.b_cast = QPushButton("Cast selected"); self.b_cast.setProperty("class", "accent")
        self.b_cast.clicked.connect(self.cast_selected)
        self.target_box = QComboBox()
        if not self.connected_mode:
            self._reload_targets()
        # Point the engine at the configured selection (so a VLC-only setup doesn't
        # leave 'cast' active and poll go-chromecast with no device), then mirror it.
        if not self.connected_mode:
            self.engine.init_target()
            self.target_box.setCurrentIndex(self.engine.target_index)
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
        self.b_subs = QPushButton("Subtitles ▾"); self.b_subs.hide()
        self.b_subs.clicked.connect(self._subs_menu)
        self.b_audio = QPushButton("Audio ▾"); self.b_audio.hide()
        self.b_audio.clicked.connect(self._audio_menu)
        self.b_chapters = QPushButton("Chapters ▾"); self.b_chapters.hide()
        self.b_chapters.clicked.connect(self._chapters_menu)
        self.autonext_cb = QCheckBox("Auto-play next")
        self.autonext_cb.setChecked(
            False if self.connected_mode else self.engine.auto_next)
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
        self.web_bridge = WebBridge()
        self.web_bridge.do.connect(self._on_web_do)   # marshals the action to the GUI thread
        self.httpd = None
        web = config.CFG.get("web", {})
        if not self.connected_mode and web.get("enabled", True):
            try:
                self.httpd = start_web_server(self, int(web.get("port", 8950)))
                self._update_web_label()
            except Exception as e:
                self.web_label.setText(f"phone remote off ({e})")

        # ---- playback updates ----
        if self.connected_mode:
            self.setWindowTitle("vizgui — Connected")
            self.remote_controller.status_changed.connect(
                self._render_remote)
            self.remote_controller.connection_changed.connect(
                self._remote_connection_changed)
            self.remote_controller.error.connect(self._status)
            self.remote_controller.server_restarted.connect(
                lambda: self._status("VizGui server restarted — playback state refreshed"))

            # Local filesystem paths are not necessarily server paths.
            # Remote browsing will replace these controls in the next step.
            self._remote_browse_seq = 0
            self._remote_sources = []
            self.browse_stack.setCurrentWidget(self.remote_tree)
            self.b_continue.setEnabled(True)
            self.b_setup.setText("⚙ Appearance")
            self.web_label.setText("Browsing media on the connected server")
            self.remote_controller.start()
        else:
            self._tv_timer = QTimer(self)
            self._tv_timer.timeout.connect(self.engine.poll_tv)
            self._tv_timer.start(5000)
            self._cast_timer = QTimer(self)
            self._cast_timer.timeout.connect(self.engine.poll_cast)
            self._cast_timer.start(3000)
            self._np_timer = QTimer(self)
            self._np_timer.timeout.connect(self.engine.tick)
            self._np_timer.start(1000)
            self._render()
            self._health_check()
            self.engine.poll_tv()
            self.engine.poll_cast()
            if observer_client is not None:
                from ..remote_controller import RemoteController
                self.observer_controller = RemoteController(observer_client, self)
                self.observer_controller.status_changed.connect(self._observer_status)
                self.observer_controller.connection_changed.connect(
                    self._observer_connection_changed)
                self.observer_controller.start()
        self.statusBar().showMessage("Ready", 3000)

    def _remote_connection_changed(self, connected):
        if connected:
            self._status("Connected to VizGui server")
        else:
            self._status("Server connection lost — retrying automatically")
            self.remote.set_tv_enabled(False)

    def _render_remote(self, status):
        """Render server state using the original MainWindow widgets."""
        now = status.get("now") or {}
        title = now.get("title") or ""
        pos = int(now.get("pos") or 0)
        dur = int(now.get("dur") or 0)
        state = now.get("state") or ""
        live = bool(now.get("live"))

        self.np_title.setText(title or "Nothing playing.")
        self.np_badge.setVisible(live)

        if live:
            self.np_bar.setRange(0, 0)
            self.np_time.setText(f"{fmt_time(pos)} · live")
        elif dur > 0:
            self.np_bar.setRange(0, dur)
            self.np_bar.setValue(min(pos, dur))
            tail = f" · {state.lower()}" if state and state != "PLAYING" else ""
            self.np_time.setText(
                f"{fmt_time(pos)} / {fmt_time(dur)}{tail}")
        else:
            self.np_bar.setRange(0, 1000)
            self.np_bar.setValue(0)
            self.np_time.setText(state.lower())

        sources = status.get("sources") or []
        if sources != self._remote_sources:
            self._remote_sources = sources
            self.source_box.blockSignals(True)
            self.source_box.clear()
            for source in sources:
                self.source_box.addItem(
                    source.get("name", "?"), source)
            self.source_box.blockSignals(False)
            if sources:
                self.remote_tree.set_source(
                    self.source_box.currentIndex())

        targets = status.get("targets") or []
        names = [t.get("name", "?") for t in targets]
        current_names = [
            self.target_box.itemText(i)
            for i in range(self.target_box.count())
        ]
        if names != current_names:
            self.target_box.blockSignals(True)
            self.target_box.clear()
            for t in targets:
                self.target_box.addItem(t.get("name", "?"), t)
            self.target_box.blockSignals(False)

        index = int(status.get("target_index", -1))
        if self.target_box.currentIndex() != index:
            self.target_box.blockSignals(True)
            self.target_box.setCurrentIndex(index)
            self.target_box.blockSignals(False)

        name = names[index] if 0 <= index < len(names) else "No target"
        self.remote.set_target_label(name)

        checked = bool(status.get("auto_next"))
        if self.autonext_cb.isChecked() != checked:
            self.autonext_cb.blockSignals(True)
            self.autonext_cb.setChecked(checked)
            self.autonext_cb.blockSignals(False)

        self.tv_status.setText(status.get("tv") or "TV status unavailable")
        self.remote.set_tv_enabled(bool(status.get("tv")))

        audio = (status.get("audio") or {}).get("tracks") or []
        subs = (status.get("subs") or {}).get("tracks") or []
        chapters = status.get("chapters") or []
        self.b_audio.setVisible(len(audio) > 1)
        self.b_subs.setVisible(bool(subs))
        self.b_chapters.setVisible(bool(chapters))

    # ---- connected-mode playback history and track selection ----
    def _remote_play(self, path):
        """Offer the server's saved position before casting a remote file."""
        def done(result):
            if getattr(self, "_popup_closed", False):
                return
            if not isinstance(result, dict) or not result.get("ok"):
                self._status("Could not load server history")
                return

            resume = next(
                (int(item.get("pos", 0))
                 for item in result.get("items", [])
                 if item.get("path") == path),
                0,
            )

            if resume > 0:
                self._offer_resume(path, resume, self.remote_controller.cast)
            else:
                self.remote_controller.cast(path, 0)

        run_async(self.remote_controller.client.history, done)

    def _remote_continue(self):
        """Show Continue Watching from the server's playback history."""
        if getattr(self, "_continue_loading", False):
            return
        self._continue_loading = True

        def done(result):
            self._continue_loading = False
            if getattr(self, "_popup_closed", False):
                return
            if not isinstance(result, dict) or not result.get("ok"):
                self._status("Could not load server history")
                return

            items = result.get("items", [])
            dlg = QDialog(self)
            dlg.setWindowTitle("Continue watching — Server")
            dlg.resize(480, 420)
            layout = QVBoxLayout(dlg)
            lst = QListWidget()

            for entry in items:
                pos = int(entry.get("pos", 0))
                dur = int(entry.get("dur", 0))
                label = entry.get("title") or os.path.basename(
                    entry.get("path", ""))
                if dur:
                    label += f"     {fmt_time(pos)} / {fmt_time(dur)}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, entry)
                lst.addItem(item)

            if items:
                layout.addWidget(lst, 1)
            else:
                layout.addWidget(QLabel(
                    "Nothing to continue yet on this server."))

            def selected():
                item = lst.currentItem()
                return item.data(Qt.UserRole) if item else None

            def resume():
                entry = selected()
                if entry:
                    dlg.accept()
                    self.remote_controller.cast(
                        entry["path"], int(entry.get("pos", 0)))

            def forget():
                entry = selected()
                if entry:
                    self.remote_controller.forget(entry["path"])
                    lst.takeItem(lst.row(lst.currentItem()))

            def clear():
                def confirmed():
                    self.remote_controller.clear_history()
                    dlg.accept()

                confirm(dlg, "Clear server history",
                        "Forget all Continue Watching entries on the server?", confirmed)

            lst.itemDoubleClicked.connect(lambda _: resume())
            buttons = QHBoxLayout()

            if items:
                play_button = QPushButton("Resume")
                play_button.setProperty("class", "accent")
                play_button.clicked.connect(resume)
                forget_button = QPushButton("Forget")
                forget_button.clicked.connect(forget)
                buttons.addWidget(play_button)
                buttons.addWidget(forget_button)

            clear_button = QPushButton("Clear all")
            clear_button.clicked.connect(clear)
            buttons.addStretch(1)
            buttons.addWidget(clear_button)
            layout.addLayout(buttons)

            close = QDialogButtonBox(QDialogButtonBox.Close)
            close.rejected.connect(dlg.reject)
            layout.addWidget(close)
            self._show_continue_dialog(dlg)

        run_async(self.remote_controller.client.history, done)

    def _remote_audio_menu(self):
        audio = self.remote_controller.snapshot.get("audio") or {}
        tracks = audio.get("tracks") or []
        current = audio.get("current")
        menu = QMenu(self)

        for track in tracks:
            index = track.get("i")
            mark = "●  " if index == current else "     "
            action = menu.addAction(
                mark + track.get("label", f"Track {index}"))
            action.triggered.connect(
                lambda _=False, i=index:
                self.remote_controller.select_audio(i))

        if not menu.isEmpty():
            popup_menu(menu, self.b_audio.mapToGlobal(
                self.b_audio.rect().bottomLeft()))

    def _remote_subs_menu(self):
        subs = self.remote_controller.snapshot.get("subs") or {}
        tracks = subs.get("tracks") or []
        current = subs.get("current")
        menu = QMenu(self)

        off = menu.addAction(
            ("●  " if current is None else "     ") + "Off")
        off.triggered.connect(
            lambda: self.remote_controller.select_subtitle(None))
        menu.addSeparator()

        for track in tracks:
            index = track.get("i")
            mark = "●  " if index == current else "     "
            action = menu.addAction(
                mark + track.get("label", f"Subtitle {index}"))
            action.triggered.connect(
                lambda _=False, i=index:
                self.remote_controller.select_subtitle(i))

        popup_menu(menu, self.b_subs.mapToGlobal(
            self.b_subs.rect().bottomLeft()))

    def _remote_chapters_menu(self):
        chapters = self.remote_controller.snapshot.get("chapters") or []
        menu = QMenu(self)

        for chapter in chapters:
            start = int(chapter.get("start", 0))
            title = chapter.get("title", "Chapter")
            action = menu.addAction(
                f"{fmt_time(start)}    {title}")
            action.triggered.connect(
                lambda _=False, seconds=start:
                self.remote_controller.jump_to(seconds))

        if not menu.isEmpty():
            popup_menu(menu, self.b_chapters.mapToGlobal(
                self.b_chapters.rect().bottomLeft()))

    def _remote_reveal_folder(self, rel):
        """Reveal a search-result folder in the expandable tree."""
        self._remote_browse_seq += 1
        self._search_timer.stop()
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.browse_stack.setCurrentWidget(self.remote_tree)
        self.remote_tree.reveal_folder(rel)

    def _remote_search(self):
        query = self.search_box.text().strip()
        if not query:
            self.browse_stack.setCurrentWidget(self.remote_tree)
            return
        src = self.source_box.currentIndex()
        if src < 0:
            return
        self._remote_browse_seq += 1
        seq = self._remote_browse_seq
        self.browse_stack.setCurrentWidget(self.results)
        self.results.clear()
        self.results.addItem("Searching server…")

        def done(result):
            if seq != self._remote_browse_seq:
                return
            self._remote_fill(result, None)

        run_async(
            lambda: self.remote_controller.client.search(src, query),
            done,
        )

    def _remote_fill(self, result, sub=None):
        """Render server search results in the separate results list."""
        self.results.clear()

        if not isinstance(result, dict) or not result.get("ok"):
            message = (
                result.get("error", "Server unavailable")
                if isinstance(result, dict)
                else "Server unavailable"
            )
            self.results.addItem(f"⚠ {message}")
            return

        entries = result.get("entries") or []

        for entry in entries:
            icon = "📁" if entry.get("dir") else "🎬"
            folder = entry.get("folder") or ""
            label = f"{icon}  {entry.get('name', '?')}"
            if folder:
                label += f"\n      {folder}"

            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, entry)
            self.results.addItem(item)

        if not entries:
            self.results.addItem("No matching media found.")

        if result.get("truncated"):
            self.results.addItem(
                "More matches exist — narrow your search."
            )

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
        if self.connected_mode:
            self._remote_play(path)
            return
        resume = history.resume_pos(path)
        if resume > 0:
            self._offer_resume(path, resume, self.engine.cast)
        else:
            self.engine.cast(path, 0)

    def _offer_resume(self, path, position, cast):
        box = QMessageBox(self)
        box.setWindowTitle("Resume")
        box.setText(f"Resume “{os.path.basename(path)}” from {fmt_time(position)}?")
        resume = box.addButton("Resume", QMessageBox.AcceptRole)
        start = box.addButton("Start over", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)

        def selected(_):
            if box.clickedButton() is resume:
                cast(path, position)
            elif box.clickedButton() is start:
                cast(path, 0)

        show_dialog(box, selected)

    def cast_selected(self):
        if self.connected_mode:
            if self.browse_stack.currentWidget() is self.remote_tree:
                path = self.remote_tree.selected_video()
            else:
                item = self.results.currentItem()
                entry = item.data(Qt.UserRole) if item else None
                path = (
                    entry.get("path")
                    if isinstance(entry, dict) and not entry.get("dir")
                    else None
                )
            if path:
                self._play(path)
            else:
                self._status("Select a video file first")
            return
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
        if self.connected_mode:
            if idx >= 0:
                self.remote_controller.select_target(idx)
        else:
            self.engine.set_target_index(idx)

    def _root_at(self, path):
        if path and os.path.isdir(path):
            self.model.setRootPath(path)
            self.tree.setRootIndex(self.model.index(path))
            return True
        return False

    def _source_changed(self, idx):
        if self.connected_mode:
            if self.remote_tree is None:
                return
            self._remote_browse_seq += 1
            self.search_box.blockSignals(True)
            self.search_box.clear()
            self.search_box.blockSignals(False)
            self._search_timer.stop()
            self.results.clear()
            self.remote_tree.set_source(idx)
            self.browse_stack.setCurrentWidget(self.remote_tree)
            return
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
                message(self, "SMB", f"Could not open '{name}':\n{res}", QMessageBox.Warning)
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
        if self.connected_mode:
            self._remote_browse_seq += 1
            self._search_timer.stop()
            if text.strip():
                self.browse_stack.setCurrentWidget(self.results)
                self._search_timer.start()
            else:
                self.browse_stack.setCurrentWidget(self.remote_tree)
            return
        if text.strip():
            self._search_timer.start()        # debounce; fires _run_search
        else:
            self._search_timer.stop()
            self._search_seq += 1             # invalidate any in-flight search
            self.browse_stack.setCurrentWidget(self.tree)

    def _run_search(self):
        if self.connected_mode:
            self._remote_search()
            return
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
        if self.connected_mode:
            entry = item.data(Qt.UserRole)
            if not isinstance(entry, dict):
                return
            if entry.get("dir"):
                self._remote_reveal_folder(entry.get("rel", ""))
            elif entry.get("path"):
                self._play(entry["path"])
            return

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
        self._render()   # remote gating + TV pill are part of the full render

    def open_setup(self):
        attribute = "_appearance_dialog" if self.connected_mode else "_setup_dialog"
        dlg = getattr(self, attribute, None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = SetupDialog(self, appearance_only=self.connected_mode)
        setattr(self, attribute, dlg)

        def completed(result):
            setattr(self, attribute, None)
            if not self.connected_mode and result == QDialog.Accepted:
                config.CFG = load_config()
                self._reload_sources()
                self._reload_targets()
                self._target_changed(self.target_box.currentIndex())

        show_dialog(dlg, completed)

    def show_continue(self):
        """Continue-watching list: resume on the current target, forget, or clear."""
        dlg = getattr(self, "_continue_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        if self.connected_mode:
            self._remote_continue()
            return
        if self.engine._cast_observer():
            self._status("Continue Watching changes are managed by the VizGui server")
            return
        items = [it for it in history.recent() if it.get("path") != self.engine._now_path]
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
                self.engine.cast(d["path"], int(d.get("pos", 0)))

        def forget():
            w = lst.currentItem()
            if w:
                history.forget(w.data(Qt.UserRole)["path"])
                lst.takeItem(lst.row(w))

        def clear():
            def confirmed():
                history.clear()
                dlg.accept()

            confirm(dlg, "Clear history", "Forget everything in Continue watching?", confirmed)

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
        self._show_continue_dialog(dlg)

    def _show_continue_dialog(self, dlg):
        self._continue_dialog = dlg
        show_dialog(dlg, lambda _: setattr(self, "_continue_dialog", None))

    # Transport / navigation delegate to the engine (remote.py calls these by name).
    def go_next(self):
        if self.connected_mode:
            self.remote_controller.transport("next")
        else:
            self.engine.go_next()

    def go_prev(self):
        if self.connected_mode:
            self.remote_controller.transport("prev")
        else:
            self.engine.go_prev()

    def _set_autonext(self, on):
        if self.connected_mode:
            self.remote_controller.set_autonext(on)
        else:
            self.engine.set_autonext(on)

    def _subs_menu(self):
        if self.connected_mode:
            self._remote_subs_menu()
            return
        subs = self.engine._media.get("subs", [])
        if not subs:
            return
        menu = QMenu(self)
        off = menu.addAction(("●  " if self.engine._sub is None else "     ") + "Off")
        off.triggered.connect(lambda: self.engine.select_sub(None))
        menu.addSeparator()
        for s in subs:
            active = self.engine._sub is not None and self.engine._sub.get("i") == s["i"]
            extra = ("  (image)" if s.get("kind") == "image" else "") + \
                    ("  [file]" if s.get("external") else "")
            act = menu.addAction(("●  " if active else "     ") + s["label"] + extra)
            act.triggered.connect(lambda _=False, sub=s: self.engine.select_sub(sub))
        popup_menu(menu, self.b_subs.mapToGlobal(self.b_subs.rect().bottomLeft()))

    def _audio_menu(self):
        if self.connected_mode:
            self._remote_audio_menu()
            return
        tracks = self.engine._media.get("audio", [])
        if len(tracks) < 2:
            return
        cur = self.engine._audio if self.engine._audio is not None else \
            next((t["i"] for t in tracks if t.get("default")), 0)
        menu = QMenu(self)
        for t in tracks:
            mark = "●  " if t["i"] == cur else "     "
            act = menu.addAction(mark + t["label"])
            act.triggered.connect(lambda _=False, i=t["i"]: self.engine.select_audio(i))
        popup_menu(menu, self.b_audio.mapToGlobal(self.b_audio.rect().bottomLeft()))

    def _chapters_menu(self):
        if self.connected_mode:
            self._remote_chapters_menu()
            return
        chaps = self.engine._media.get("chapters", [])
        if not chaps:
            return
        menu = QMenu(self)
        for c in chaps:
            act = menu.addAction(f"{fmt_time(c['start'])}    {c['title']}")
            act.triggered.connect(lambda _=False, s=c["start"]: self.engine.jump_to(s))
        popup_menu(menu, self.b_chapters.mapToGlobal(self.b_chapters.rect().bottomLeft()))

    # Transport buttons (used by remote.py via win._transport(win._t_*)).
    def _t_rewind(self):
        return self.engine.rewind()

    def _t_forward(self):
        return self.engine.forward()

    def _t_playpause(self):
        return self.engine.playpause()

    def _t_stop(self):
        return self.engine.stop()

    def closeEvent(self, event):
        dismiss_dialogs(self)
        if self.observer_controller is not None:
            self.observer_controller.close()
        if self.connected_mode:
            self.remote_controller.close()
        else:
            self.engine.shutdown()
        if self.httpd:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
        super().closeEvent(event)

    def _transport(self, fn):
        if self.connected_mode:
            actions = {
                self._t_rewind: "rewind",
                self._t_forward: "forward",
                self._t_playpause: "playpause",
                self._t_stop: "stop",
            }
            action = actions.get(fn)
            if action:
                self.remote_controller.transport(action)
        else:
            self.engine.transport(fn)

    def send_tv_key(self, cmd):
        if self.connected_mode:
            self.remote_controller.send_tv_key(cmd)
        else:
            self.engine.send_key(cmd)

    # ---- polling ----
    # ---- phone remote (web) ----
    def _update_web_label(self):
        web = config.CFG.get("web", {})
        self.web_label.setText(
            f"📱 Phone remote:  http://{local_ip()}:{web.get('port', 8950)}    ·    pair in ⚙ Setup")

    def show_pair_qr(self):
        """Show a QR of the tokenized URL for scan-to-pair (shown only while
        open, so the token isn't left on screen). Falls back to the PIN."""
        existing = getattr(self, "_pair_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
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
        self._pair_dialog = dlg
        show_dialog(dlg, lambda _: setattr(self, "_pair_dialog", None))

    def new_pairing(self):
        dialog = getattr(self, "_pair_dialog", None)
        if dialog is not None:
            dialog.reject()
        self.engine.new_pairing()

    def web_pair(self, pin):
        return self.engine.web_pair(pin)

    def web_history(self):
        return self.engine.web_history()

    def web_status(self):
        return self.engine.web_status()

    def web_add_cast(self, dev):
        self.engine.web_add_cast(dev)

    def _on_web_do(self, kind, p):
        """A phone action, delivered on the GUI thread by web_bridge -> engine."""
        self.engine.web_do(kind, p)

    # ---- rendering: reflect the engine's state into the widgets ----
    def _observer_status(self, status):
        self._observer_snapshot = status
        self._observer_online = True
        if self.engine._cast_observer():
            self._render()

    def _observer_connection_changed(self, connected):
        self._observer_online = connected
        if not connected:
            self._observer_snapshot = None
        if self.engine._cast_observer():
            self._render()

    def _render_observer_now(self):
        """Display server playback without changing local Engine ownership."""
        if not self._observer_online or self._observer_snapshot is None:
            self.np_title.setText(
                "Server disconnected — Chromecast controls unavailable")
            self.np_badge.hide()
            self.np_bar.setRange(0, 1000)
            self.np_bar.setValue(0)
            self.np_time.setText("")
            return

        now = self._observer_snapshot.get("now") or {}
        title = now.get("title") or "Nothing playing."
        pos = int(now.get("pos") or 0)
        dur = int(now.get("dur") or 0)
        state = now.get("state") or ""
        live = bool(now.get("live"))

        self.np_title.setText(title)
        self.np_badge.setVisible(live)

        if live:
            self.np_bar.setRange(0, 0)
            self.np_time.setText(f"{fmt_time(pos)} · live")
        elif dur > 0:
            self.np_bar.setRange(0, dur)
            self.np_bar.setValue(min(pos, dur))
            tail = f" · {state.lower()}" if state and state != "PLAYING" else ""
            self.np_time.setText(
                f"{fmt_time(pos)} / {fmt_time(dur)}{tail}")
        else:
            self.np_bar.setRange(0, 1000)
            self.np_bar.setValue(0)
            self.np_time.setText(state.lower())

    def _render(self):
        """Called on every engine state change (poll result, tick, action)."""
        e = self.engine
        if self.observer_controller is not None and e._cast_observer():
            self._render_observer_now()
            self.b_chapters.hide()
            self.b_audio.hide()
            self.b_subs.hide()
            self.b_cast.setEnabled(False)
            self._sync_target_box()
            self.remote.set_target_label(
                e.active_target_name() + " (server-owned)")
            self.remote.set_tv_enabled(bool(config.HOST))
            self.tv_status.setText(
                (e._tv_text or "TV: …") if config.HOST
                else "No SmartCast TV configured")
            return
        if e._message:
            self.np_title.setText(e._message)
            self.np_badge.hide()
            self.np_bar.setRange(0, 0); self.np_time.setText("")
        elif e._casting:
            self.np_title.setText(f"Preparing: {e.now_title}…")
            self.np_badge.hide()
            self.np_bar.setRange(0, 0); self.np_time.setText("")
        elif not e._now_path:
            self.np_title.setText("Nothing playing.")
            self.np_badge.hide()
            self.np_bar.setRange(0, 1000); self.np_bar.setValue(0); self.np_time.setText("")
        else:
            self.np_title.setText(e._display_title)
            self._render_np()
        # track-selection buttons + cast button + auto-next mirror engine state
        self.b_chapters.setVisible(bool(e._media.get("chapters")))
        self.b_audio.setVisible(len(e._media.get("audio", [])) > 1)
        self.b_subs.setVisible(bool(e._media.get("subs")))
        self.b_cast.setEnabled(not e._casting)
        if self.autonext_cb.isChecked() != e.auto_next:
            self.autonext_cb.blockSignals(True)
            self.autonext_cb.setChecked(e.auto_next)
            self.autonext_cb.blockSignals(False)
        self._sync_target_box()
        self.remote.set_target_label(e.active_target_name())
        on = bool(config.HOST)
        self.remote.set_tv_enabled(on)          # transport stays enabled; TV keys gated
        self.tv_status.setText((e._tv_text or "TV: …") if on else "No SmartCast TV configured")

    def _render_np(self):
        e = self.engine
        pos, dur, state = e._np_pos, e._np_dur, e._np_state
        self.np_badge.setVisible(e.now_live)
        if e.now_live:
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

    def _sync_target_box(self):
        """Mirror the engine's target selection in the picker (rebuild only if the
        target list changed, e.g. a cast was added from the phone)."""
        targets = config.CFG.get("targets", [])
        if self.target_box.count() != len(targets):
            self._reload_targets()
        if self.target_box.currentIndex() != self.engine.target_index:
            self.target_box.blockSignals(True)
            self.target_box.setCurrentIndex(self.engine.target_index)
            self.target_box.blockSignals(False)
