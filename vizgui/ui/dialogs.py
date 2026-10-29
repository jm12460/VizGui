"""Setup wizard and the Add-SMB-share dialog."""
import os
from copy import deepcopy

from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QLineEdit, QCheckBox, QFormLayout, QListWidget,
                               QMessageBox, QDialogButtonBox, QTabWidget, QWidget, QComboBox)

from ..qtpopups import (show_dialog, message, confirm, text_input, item_input,
                        directory_input, dismiss_dialogs)

from .. import config
from ..config import save_config
from ..sources import smb_mount
from ..chromecast import scan_cast_devices
from ..helpers import discovery_plan
from ..smartcast import sc_pair_start, sc_pair_finish
from ..net import local_ip, lan_cidr
from ..qtasync import run_async
from .. import platforms
from ..appearance import load as load_appearance, save as save_appearance


class SmbDialog(QDialog):
    """Collect SMB share details; result() returns a source config entry."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add SMB share")
        form = QFormLayout(self)
        self.server = QLineEdit(); self.server.setPlaceholderText("nas.local or 192.168.1.50")
        self.share = QLineEdit(); self.share.setPlaceholderText("media")
        self.name = QLineEdit(); self.name.setPlaceholderText("(defaults to the share name)")
        self.user = QLineEdit()
        self.domain = QLineEdit(); self.domain.setPlaceholderText("(usually blank)")
        self.password = QLineEdit(); self.password.setEchoMode(QLineEdit.Password)
        self.guest = QCheckBox("Connect as guest (no login)")
        self.guest.toggled.connect(lambda on: [w.setEnabled(not on) for w in
                                               (self.user, self.domain, self.password)])
        form.addRow("Server:", self.server)
        form.addRow("Share:", self.share)
        form.addRow("Name:", self.name)
        form.addRow("Username:", self.user)
        form.addRow("Domain:", self.domain)
        form.addRow("Password:", self.password)
        form.addRow("", self.guest)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_source(self):
        server = self.server.text().strip().strip("/\\")
        share = self.share.text().strip().strip("/\\")
        # Normalize dashed IPs (e.g. from Synology QuickConnect URLs: 192-168-0-58 -> 192.168.0.58)
        import re
        m = re.search(r"(\d{1,3})-(\d{1,3})-(\d{1,3})-(\d{1,3})", server)
        if m:
            server = ".".join(m.groups())
        if not server or not share:
            return None
        smb = {"server": server, "share": share, "guest": self.guest.isChecked(),
               "user": self.user.text().strip(), "domain": self.domain.text().strip(),
               "password": self.password.text()}
        return {"name": self.name.text().strip() or share, "kind": "smb", "smb": smb}


class SetupDialog(QDialog):
    """Configure media sources and cast/playback targets, saved to config.json."""
    def __init__(self, parent=None, *, appearance_only=False):
        super().__init__(parent)
        self.finished.connect(lambda _: dismiss_dialogs(self))
        self.appearance_only = appearance_only
        self.win = parent
        self.setWindowTitle("viz-gui Appearance" if appearance_only else "viz-gui Setup")
        self.resize(560, 470)
        self.sources = deepcopy(config.CFG.get("sources", []))
        self.targets = deepcopy(config.CFG.get("targets", []))
        self.web = dict(config.CFG.get("web", {"enabled": True, "port": 8950}))
        root = QVBoxLayout(self)
        self.setSizeGripEnabled(False)
        if self.appearance_only:
            # Leave room around the bottom-right XWayland window corner.
            root.setContentsMargins(12, 12, 30, 16)
        tabs = QTabWidget(); root.addWidget(tabs, 1)

        if not self.appearance_only:
            self._add_media_tabs(tabs)
        self._add_appearance_tab(tabs)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        root.addWidget(bb)
        if not self.appearance_only:
            self._refresh()
            self._autoscan()

    def _add_media_tabs(self, tabs):
        """Build full Setup controls, never appearance-only controls."""
        # ---- Sources tab ----
        srcw = QWidget(); sv = QVBoxLayout(srcw)
        sv.addWidget(QLabel("Media sources (folders / NAS mounts)"))
        self.src_list = QListWidget(); sv.addWidget(self.src_list, 1)
        sb = QHBoxLayout()
        b_add = QPushButton("Add folder…"); b_add.clicked.connect(self._add_source)
        b_smb = QPushButton("Add NAS/SMB share…"); b_smb.clicked.connect(self._add_smb)
        b_rms = QPushButton("Remove"); b_rms.clicked.connect(self._rm_source)
        sb.addWidget(b_add); sb.addWidget(b_smb); sb.addWidget(b_rms); sb.addStretch(1)
        sv.addLayout(sb)
        hint = QLabel("A folder is any local path or already-mounted drive. "
                      "Add NAS/SMB share connects to a NAS over the network (no mounting needed).")
        hint.setObjectName("status"); hint.setWordWrap(True); sv.addWidget(hint)
        tabs.addTab(srcw, "Sources")

        # ---- Cast & VLC tab ----
        tgtw = QWidget(); tv = QVBoxLayout(tgtw)
        tv.addWidget(QLabel("Cast / playback targets"))
        self.tgt_list = QListWidget(); tv.addWidget(self.tgt_list, 1)
        tb = QHBoxLayout()
        self.b_scan = QPushButton("Scan for Cast devices"); self.b_scan.clicked.connect(self._scan)
        b_pair = QPushButton("Pair SmartCast…"); b_pair.clicked.connect(self._pair)
        b_vlc = QPushButton("Add local VLC"); b_vlc.clicked.connect(self._add_vlc)
        b_rmt = QPushButton("Remove"); b_rmt.clicked.connect(self._rm_target)
        for b in (self.b_scan, b_pair, b_vlc, b_rmt):
            tb.addWidget(b)
        tv.addLayout(tb)
        self.tgt_status = QLabel(); self.tgt_status.setObjectName("status")
        self.tgt_status.setWordWrap(True); tv.addWidget(self.tgt_status)
        tabs.addTab(tgtw, "Cast && VLC")

        # ---- Phone Remote tab ----
        webw = QWidget(); wv = QVBoxLayout(webw)
        self.web_enabled = QCheckBox("Enable — serves a remote page any phone on your Wi-Fi can open")
        self.web_enabled.setChecked(bool(self.web.get("enabled", True)))
        wv.addWidget(self.web_enabled)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Port:"))
        self.web_port = QLineEdit(str(self.web.get("port", 8950))); self.web_port.setMaximumWidth(90)
        pr.addWidget(self.web_port)
        self.web_url = QLabel(); self.web_url.setObjectName("dim")
        pr.addWidget(self.web_url, 1)
        wv.addLayout(pr)
        self.fwhint = QLabel()
        self.fwhint.setObjectName("status"); self.fwhint.setWordWrap(True); wv.addWidget(self.fwhint)
        self.web_cmd = QLineEdit(); self.web_cmd.setReadOnly(True); wv.addWidget(self.web_cmd)
        self.web_port.textChanged.connect(self._upd_web)
        self._upd_web()

        # pairing controls (operate on live config immediately)
        pairrow = QHBoxLayout()
        self.pin_lbl = QLabel()
        # fix the width (to the revealed text) so the eye button doesn't shift
        # when the PIN is shown/hidden mid-hold
        self.pin_lbl.setFixedWidth(self.pin_lbl.fontMetrics().horizontalAdvance("Pairing PIN:  000000") + 12)
        self.b_eye = QPushButton("👁"); self.b_eye.setFixedWidth(40)
        self.b_eye.setToolTip("Hold to reveal the PIN")
        self.b_eye.pressed.connect(lambda: self._refresh_pin(reveal=True))
        self.b_eye.released.connect(lambda: self._refresh_pin(reveal=False))
        b_qr = QPushButton("Pair phone (QR)…"); b_qr.clicked.connect(self._pair_qr)
        b_new = QPushButton("New PIN & QR"); b_new.clicked.connect(self._new_pairing)
        b_new.setToolTip("Fresh PIN and QR — un-pairs all currently paired phones")
        pairrow.addWidget(self.pin_lbl); pairrow.addWidget(self.b_eye); pairrow.addStretch(1)
        pairrow.addWidget(b_qr); pairrow.addWidget(b_new)
        wv.addLayout(pairrow)
        self._refresh_pin()
        wv.addStretch(1)
        tabs.addTab(webw, "Phone Remote")


    def _add_appearance_tab(self, tabs):
        """Build desktop-only appearance controls."""
        # ---- Appearance tab: desktop-only preferences ----
        appearance_widget = QWidget()
        appearance_form = QFormLayout(appearance_widget)

        scale, backend = load_appearance()
        self._initial_appearance = (scale, backend)

        self.appearance_scale = QComboBox()
        for label, value in (
            ("System default", "system"),
            ("100% — Normal", "1"),
            ("125% — Comfortable", "1.25"),
            ("150% — Large", "1.5"),
            ("175% — Extra large", "1.75"),
            ("200% — Maximum", "2"),
        ):
            self.appearance_scale.addItem(label, value)
        self.appearance_scale.setCurrentIndex(
            self.appearance_scale.findData(scale)
        )

        self.appearance_backend = QComboBox()
        for label, value in (
            ("Automatic", "auto"),
            ("Native Wayland", "wayland"),
            ("X11 / XWayland — compatibility", "xcb"),
        ):
            self.appearance_backend.addItem(label, value)
        self.appearance_backend.setCurrentIndex(
            self.appearance_backend.findData(backend)
        )

        appearance_form.addRow("Interface size:", self.appearance_scale)
        appearance_form.addRow("Display backend:", self.appearance_backend)

        note = QLabel(
            "Changes take effect the next time you open the desktop GUI. "
            "On some Wayland systems, scaled popup menus may be misplaced. "
            "Try X11 / XWayland if that happens. "
            "Your headless server does not need to restart."
        )
        note.setWordWrap(True)
        appearance_form.addRow(note)

        self.appearance_restart_warning = QLabel(
            "⚠  Restart required — save your changes, then close and "
            "reopen the desktop GUI. Your headless server can stay running."
        )
        self.appearance_restart_warning.setWordWrap(True)
        self.appearance_restart_warning.setStyleSheet(
            "color: #e8bd65; font-weight: 600;"
        )
        appearance_form.addRow(self.appearance_restart_warning)

        self.appearance_scale.currentIndexChanged.connect(
            self._update_appearance_restart_warning
        )
        self.appearance_backend.currentIndexChanged.connect(
            self._update_appearance_restart_warning
        )
        self._update_appearance_restart_warning()

        tabs.addTab(appearance_widget, "Appearance")

    def showEvent(self, event):
        super().showEvent(event)

        # Some Qt styles enable a resize grip when the dialog is shown.
        # Setup and Appearance don't need this corner widget.
        self.setSizeGripEnabled(False)

    def _upd_web(self):
        port = (self.web_port.text().strip() or "8950")
        self.web_url.setText(f"→  http://{local_ip()}:{port}")
        cmd = platforms.firewall_allow_cmd(lan_cidr(), port)
        if cmd:
            self.fwhint.setText("Your firewall blocks this by default. Run this once in a "
                                "terminal to allow only your home network (changes apply when "
                                "you restart viz-gui):")
            self.web_cmd.setText(cmd); self.web_cmd.show()
        else:
            self.fwhint.setText("No active firewall detected — your phone should reach this "
                                f"already. If you set one up later, allow TCP port {port} "
                                "from your home network.")
            self.web_cmd.clear(); self.web_cmd.hide()

    def _refresh_pin(self, reveal=False):
        pin = config.CFG.get("web", {}).get("pin", "------") if reveal else "••••••"
        self.pin_lbl.setText(f"Pairing PIN:  {pin}")

    def _new_pairing(self):
        if not self.win:
            return
        def regenerate():
            self.win.new_pairing()
            self._refresh_pin()

        confirm(self, "New PIN & QR",
                "Generate a fresh PIN and QR?\n\nThis un-pairs every phone that's "
                "currently connected — they'll each need to scan or enter the new code "
                "again.", regenerate)

    def _pair_qr(self):
        if self.win:
            self.win.show_pair_qr()

    def _refresh(self):
        self.src_list.clear()
        for s in self.sources:
            if s.get("kind") == "smb":
                sm = s.get("smb", {})
                self.src_list.addItem(f"{s.get('name','?')}   —   smb://{sm.get('server','')}/{sm.get('share','')}")
            else:
                self.src_list.addItem(f"{s.get('name','?')}   —   {s.get('path','')}")
        self.tgt_list.clear()
        for t in self.targets:
            if t.get("type") == "cast":
                extra = "   [paired]" if t.get("auth_token") else ""
                self.tgt_list.addItem(f"Cast:  {t.get('name','?')}  ({t.get('addr','')}){extra}")
            else:
                self.tgt_list.addItem(f"VLC:  {t.get('name','this PC')}")

    def _add_source(self):
        def folder_selected(folder):
            default = os.path.basename(folder) or folder

            def named(name):
                self.sources.append({"name": name or default, "path": folder})
                self._refresh()

            text_input(self, "Source name", "Name:", named, text=default)

        directory_input(self, folder_selected)

    def _add_smb(self):
        dlg = SmbDialog(self)

        def accepted(result):
            if result != QDialog.Accepted:
                return
            src = dlg.result_source()
            if not src:
                message(self, "SMB", "Server and share are required.")
                return

            def save_share():
                self.sources.append(src)
                self._refresh()

            try:
                smb_mount(**{k: src["smb"].get(k, "") for k in
                             ("server", "share", "user", "domain", "password")},
                          guest=src["smb"].get("guest", False))
            except Exception as e:
                confirm(self, "SMB", f"Couldn't connect now:\n{e}\n\nSave the share anyway?",
                        save_share)
                return
            save_share()

        show_dialog(dlg, accepted)

    def _rm_source(self):
        i = self.src_list.currentRow()
        if 0 <= i < len(self.sources):
            del self.sources[i]; self._refresh()

    def _add_device(self, d):
        """Add a discovered Cast device if not already a target. -> added?"""
        if any(t.get("type") == "cast" and t.get("addr") == d["addr"] for t in self.targets):
            return False
        self.targets.append({"type": "cast", "name": d["name"], "addr": d["addr"],
                             "cast_port": d.get("cast_port", "8009")})
        return True

    def _autoscan(self):
        """Background discovery on open, so a first-run user sees their TV with no clicks."""
        self.tgt_status.setText("🔍 Looking for Cast devices on your network…")
        run_async(scan_cast_devices, self._autoscan_done)

    def _autoscan_done(self, devs):
        if getattr(self, "_popup_closed", False):
            return
        try:
            to_add, msg = discovery_plan(devs, self.targets)
            for d in to_add:
                self._add_device(d)
            if to_add:
                self._refresh()
            self.tgt_status.setText(msg)
        except RuntimeError:
            pass   # dialog closed mid-scan

    def _scan(self):
        self.b_scan.setEnabled(False)
        self.tgt_status.setText("🔍 Scanning…")
        run_async(scan_cast_devices, self._scan_done)

    def _scan_done(self, devs):
        if getattr(self, "_popup_closed", False):
            return
        try:
            self.b_scan.setEnabled(True)
            if not isinstance(devs, list):
                devs = []
            if not devs:
                self.tgt_status.setText("No Cast devices found. Is the TV on and on the same Wi-Fi?")
                return
            names = [f"{d['name']}  ({d['addr']})" for d in devs]
            def selected(choice):
                d = devs[names.index(choice)]
                added = self._add_device(d)
                self._refresh()
                self.tgt_status.setText(f"Added {d['name']}." if added
                                       else f"{d['name']} is already in your targets.")

            item_input(self, "Cast devices", "Add which device?", names, selected)
        except RuntimeError:
            pass   # dialog closed mid-scan

    def _pair(self):
        i = self.tgt_list.currentRow()
        if not (0 <= i < len(self.targets)) or self.targets[i].get("type") != "cast":
            message(self, "Pair", "Select a Cast target first (a SmartCast TV).")
            return
        target = self.targets[i]

        def port_selected(port):
            if not any(t is target for t in self.targets):
                return
            try:
                req_token, challenge = sc_pair_start(target["addr"], port)
            except Exception as e:
                message(self, "Pair", "Could not start pairing (is it a SmartCast TV?):\n"
                        + str(e), QMessageBox.Warning)
                return

            def code_selected(code):
                if not code or not any(t is target for t in self.targets):
                    return
                try:
                    token = sc_pair_finish(target["addr"], port, req_token, code, challenge)
                except Exception as e:
                    message(self, "Pair", "Pairing failed:\n" + str(e), QMessageBox.Warning)
                    return
                target["smartcast_host"] = f"{target['addr']}:{port}"
                target["auth_token"] = token
                message(self, "Pair", "Paired — remote control enabled for this TV.")
                self._refresh()

            text_input(self, "Pairing code", "Enter the code shown on the TV:", code_selected)

        text_input(self, "SmartCast port", "SmartCast API port:", port_selected, text="7345")

    def _add_vlc(self):
        if not any(x.get("type") == "vlc" for x in self.targets):
            self.targets.append({"type": "vlc", "name": "VLC (this PC)"})
        self._refresh()

    def _rm_target(self):
        i = self.tgt_list.currentRow()
        if 0 <= i < len(self.targets):
            del self.targets[i]; self._refresh()

    def _save(self):
        if self.appearance_only:
            self._save_appearance()
            return

        try:
            port = int(self.web_port.text())
        except ValueError:
            port = 8950
        # Mutate the live config so the web token/PIN, auto_next and anything else
        # survive — this dialog only owns sources, targets, and web enable/port.
        config.CFG["sources"] = self.sources
        config.CFG["targets"] = self.targets
        web = config.CFG.setdefault("web", {"enabled": True, "port": 8950})
        web["enabled"] = self.web_enabled.isChecked()
        web["port"] = port
        try:
            save_config(config.CFG)
        except Exception as e:
            message(self, "Save", "Could not save config:\n" + str(e), QMessageBox.Warning)
            return
        self._save_appearance()

    def _update_appearance_restart_warning(self, *_):
        current = (
            self.appearance_scale.currentData(),
            self.appearance_backend.currentData(),
        )
        self.appearance_restart_warning.setVisible(
            current != self._initial_appearance
        )

    def _save_appearance(self):
        new_appearance = (
            self.appearance_scale.currentData(),
            self.appearance_backend.currentData(),
        )
        try:
            save_appearance(*new_appearance)
        except Exception as e:
            message(
                self, "Appearance",
                "Could not save appearance settings:\n" + str(e), QMessageBox.Warning
            )
            return

        self.accept()
