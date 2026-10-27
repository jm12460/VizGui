"""The remote panel: SmartCast TV keys + media transport, wired to the window."""
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QLabel, QPushButton, QVBoxLayout,
                               QHBoxLayout, QGridLayout)

from ..smartcast import sc_key, KEYS
from ..paths import log
from ..qtasync import run_async
from .dpad import DPad


class RemotePanel(QFrame):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("remote")
        self.setFixedWidth(208)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 18)
        v.setSpacing(12)
        self._tv = []   # TV-only widgets, disabled when no SmartCast TV is paired

        def rkey(label, cmd):
            b = QPushButton(label); b.setProperty("class", "rkey")
            b.clicked.connect(partial(self.send, cmd))
            self._tv.append(b)
            return b

        # power + input
        top = QHBoxLayout()
        self.b_power = QPushButton("⏻"); self.b_power.setObjectName("power")
        self.b_power.setFixedSize(46, 46); self.b_power.setToolTip("Power")
        self.b_power.clicked.connect(partial(self.send, "power"))
        self._tv.append(self.b_power)
        top.addWidget(self.b_power); top.addStretch(1); top.addWidget(rkey("Input", "input"))
        v.addLayout(top)

        # circular clickpad
        self.dpad = DPad(146)
        self.dpad.pressed.connect(self.send)
        self._tv.append(self.dpad)
        drow = QHBoxLayout(); drow.addStretch(1); drow.addWidget(self.dpad); drow.addStretch(1)
        v.addLayout(drow)

        # back / home / menu / exit
        grid = QGridLayout(); grid.setSpacing(10)
        for i, (lab, cmd) in enumerate([("Back", "back"), ("Home", "home"),
                                        ("Menu", "menu"), ("Exit", "exit")]):
            grid.addWidget(rkey(lab, cmd), i // 2, i % 2)
        v.addLayout(grid)

        # volume rocker + mute/info
        volr = QHBoxLayout(); volr.setSpacing(10)
        volr.addWidget(rkey("Vol −", "voldown")); volr.addWidget(rkey("Vol +", "volup"))
        v.addLayout(volr)
        mi = QHBoxLayout(); mi.setSpacing(10)
        mi.addWidget(rkey("Mute", "mute")); mi.addWidget(rkey("Info", "info"))
        v.addLayout(mi)

        # ---- media transport (always enabled - drives the cast session) ----
        div = QFrame(); div.setObjectName("divider"); div.setFixedHeight(1)
        v.addWidget(div)
        self.tgt_lbl = QLabel(""); self.tgt_lbl.setObjectName("dim")
        self.tgt_lbl.setAlignment(Qt.AlignCenter); self.tgt_lbl.setWordWrap(True)
        v.addWidget(self.tgt_lbl)

        def mbtn(glyph, fn, tip, cls=None):
            b = QPushButton(glyph); b.setToolTip(tip)
            if cls:
                b.setProperty("class", cls)
            b.clicked.connect(fn)
            return b

        skip = QHBoxLayout(); skip.setSpacing(10)
        skip.addWidget(mbtn("⏮", win.go_prev, "Previous"))
        skip.addWidget(mbtn("⏯", lambda: win._transport(win._t_playpause), "Play / Pause", "accent"))
        skip.addWidget(mbtn("⏭", win.go_next, "Next"))
        v.addLayout(skip)
        tr = QHBoxLayout(); tr.setSpacing(10)
        tr.addWidget(mbtn("⏪", lambda: win._transport(win._t_rewind), "Back 30s"))
        tr.addWidget(mbtn("⏹", lambda: win._transport(win._t_stop), "Stop"))
        tr.addWidget(mbtn("⏩", lambda: win._transport(win._t_forward), "Forward 30s"))
        v.addLayout(tr)

    def set_tv_enabled(self, on):
        for w in self._tv:
            w.setEnabled(on)

    def set_target_label(self, name):
        self.tgt_lbl.setText(f"▶ playback → {name}")

    def send(self, cmd):
        def fire():
            try:
                r = sc_key(*KEYS[cmd])
                res = r.get("STATUS", {}).get("RESULT", r) if isinstance(r, dict) else r
                log.info("key %s %s -> %s", cmd, KEYS[cmd], res)
                return r
            except Exception as e:
                log.warning("key %s %s FAILED: %s", cmd, KEYS[cmd], e)
                return {"error": str(e)}
        run_async(fire, self._sent)

    def _sent(self, res):
        # a key that didn't reach the TV should say so, not fail silently
        if isinstance(res, dict) and "error" in res and self.win:
            self.win._status("TV didn't respond — is it on and on the same Wi-Fi?")
