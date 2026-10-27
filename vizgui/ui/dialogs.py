"""Setup wizard and the Add-SMB-share dialog."""
import os

from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QLineEdit, QCheckBox, QFormLayout, QListWidget, QFileDialog,
                               QMessageBox, QInputDialog, QDialogButtonBox, QTabWidget, QWidget)

from .. import config
from ..config import save_config
from ..sources import smb_mount
from ..chromecast import scan_cast_devices
from ..helpers import discovery_plan
from ..smartcast import sc_pair_start, sc_pair_finish
from ..net import local_ip, lan_cidr
from ..qtasync import run_async
from .. import platforms


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
        server = self.server.text().strip()
        share = self.share.text().strip()
        if not server or not share:
            return None
        smb = {"server": server, "share": share, "guest": self.guest.isChecked(),
               "user": self.user.text().strip(), "domain": self.domain.text().strip(),
               "password": self.password.text()}
        return {"name": self.name.text().strip() or share, "kind": "smb", "smb": smb}


class SetupDialog(QDialog):
    """Configure media sources and cast/playback targets, saved to config.json."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.win = parent
        self.setWindowTitle("viz-gui Setup")
        self.resize(560, 470)
        self.sources = [dict(s) for s in config.CFG.get("sources", [])]
        self.targets = [dict(t) for t in config.CFG.get("targets", [])]
        self.web = dict(config.CFG.get("web", {"enabled": True, "port": 8950}))
        root = QVBoxLayout(self)
        tabs = QTabWidget(); root.addWidget(tabs, 1)

        # ---- Sources tab ----
        srcw = QWidget(); sv = QVBoxLayout(srcw)
        sv.addWidget(QLabel("Media sources (folders / NAS mounts)"))
        self.src_list = QListWidget(); sv.addWidget(self.src_list, 1)
        sb = QHBoxLayout()
        b_add = QPushButton("Add folder…"); b_add.clicked.connect(self._add_source)
        b_smb = QPushButton("Add SMB share…"); b_smb.clicked.connect(self._add_smb)
        b_rms = QPushButton("Remove"); b_rms.clicked.connect(self._rm_source)
        sb.addWidget(b_add); sb.addWidget(b_smb); sb.addWidget(b_rms); sb.addStretch(1)
        sv.addLayout(sb)
        hint = QLabel("A folder is any local path or already-mounted drive. "
                      "Add SMB share connects to a NAS over the network (no mounting needed).")
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

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self._refresh()
        self._autoscan()   # look for the TV in the background so the user needn't type an IP

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
        if QMessageBox.question(self, "New PIN & QR",
                "Generate a fresh PIN and QR?\n\nThis un-pairs every phone that's "
                "currently connected — they'll each need to scan or enter the new code "
                "again.") == QMessageBox.Yes:
            self.win.new_pairing()
            self._refresh_pin()

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
        d = QFileDialog.getExistingDirectory(self, "Choose a media folder", os.path.expanduser("~"))
        if not d:
            return
        name, ok = QInputDialog.getText(self, "Source name", "Name:", text=os.path.basename(d) or d)
        self.sources.append({"name": name if (ok and name) else (os.path.basename(d) or d), "path": d})
        self._refresh()

    def _add_smb(self):
        dlg = SmbDialog(self)
        if dlg.exec():
            src = dlg.result_source()
            if not src:
                QMessageBox.information(self, "SMB", "Server and share are required.")
                return
            # try connecting now so the user gets immediate feedback
            try:
                smb_mount(**{k: src["smb"].get(k, "") for k in
                             ("server", "share", "user", "domain", "password")},
                          guest=src["smb"].get("guest", False))
            except Exception as e:
                if QMessageBox.question(self, "SMB",
                        f"Couldn't connect now:\n{e}\n\nSave the share anyway?") != QMessageBox.Yes:
                    return
            self.sources.append(src)
            self._refresh()

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
        try:
            self.b_scan.setEnabled(True)
            if not isinstance(devs, list):
                devs = []
            if not devs:
                self.tgt_status.setText("No Cast devices found. Is the TV on and on the same Wi-Fi?")
                return
            names = [f"{d['name']}  ({d['addr']})" for d in devs]
            choice, ok = QInputDialog.getItem(self, "Cast devices", "Add which device?", names, 0, False)
            if not (ok and choice):
                return
            d = devs[names.index(choice)]
            added = self._add_device(d)
            self._refresh()
            self.tgt_status.setText(f"Added {d['name']}." if added
                                    else f"{d['name']} is already in your targets.")
        except RuntimeError:
            pass   # dialog closed mid-scan

    def _pair(self):
        i = self.tgt_list.currentRow()
        if not (0 <= i < len(self.targets)) or self.targets[i].get("type") != "cast":
            QMessageBox.information(self, "Pair", "Select a Cast target first (a SmartCast TV).")
            return
        t = self.targets[i]
        port, ok = QInputDialog.getText(self, "SmartCast port", "SmartCast API port:", text="7345")
        if not ok:
            return
        try:
            req_token, challenge = sc_pair_start(t["addr"], port)
        except Exception as e:
            QMessageBox.warning(self, "Pair", "Could not start pairing (is it a SmartCast TV?):\n" + str(e))
            return
        code, ok = QInputDialog.getText(self, "Pairing code", "Enter the code shown on the TV:")
        if not (ok and code):
            return
        try:
            tok = sc_pair_finish(t["addr"], port, req_token, code, challenge)
        except Exception as e:
            QMessageBox.warning(self, "Pair", "Pairing failed:\n" + str(e))
            return
        t["smartcast_host"] = f"{t['addr']}:{port}"; t["auth_token"] = tok
        QMessageBox.information(self, "Pair", "Paired — remote control enabled for this TV.")
        self._refresh()

    def _add_vlc(self):
        if not any(x.get("type") == "vlc" for x in self.targets):
            self.targets.append({"type": "vlc", "name": "VLC (this PC)"})
        self._refresh()

    def _rm_target(self):
        i = self.tgt_list.currentRow()
        if 0 <= i < len(self.targets):
            del self.targets[i]; self._refresh()

    def _save(self):
        try:
            port = int(self.web_port.text())
        except ValueError:
            port = 8950
        try:
            save_config({"sources": self.sources, "targets": self.targets,
                         "web": {"enabled": self.web_enabled.isChecked(), "port": port}})
        except Exception as e:
            QMessageBox.warning(self, "Save", "Could not save config:\n" + str(e))
            return
        self.accept()
