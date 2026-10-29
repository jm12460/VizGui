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

        # volume rocker (left) + mute/info (right), each a joined two-button column
        def rockered(top_btn, bot_btn):
            top_btn.setProperty("rocker", "top"); bot_btn.setProperty("rocker", "bottom")
            col = QVBoxLayout(); col.setSpacing(0)
            col.addWidget(top_btn); col.addWidget(bot_btn)
            return col
        volr = QHBoxLayout(); volr.setSpacing(10)
        volr.addLayout(rockered(rkey("Vol +", "volup"), rkey("Vol −", "voldown")), 1)
        volr.addLayout(rockered(rkey("Mute", "mute"), rkey("Info", "info")), 1)
        v.addLayout(volr)

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
        """Delegate TV commands to the owning window/controller."""
        self.win.send_tv_key(cmd)
