"""The painted circular D-pad clickpad widget."""
import math

from PySide6.QtCore import Qt, QPointF, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPolygonF, QFont
from PySide6.QtWidgets import QWidget


class DPad(QWidget):
    """A circular navigation clickpad: a ring with ▲▸▼◀ regions and OK in the
    center, like a streamer remote. Emits pressed(cmd) with up/down/left/right/ok."""
    pressed = Signal(str)

    def __init__(self, size=150, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._active = None

    def _region(self, pos):
        c = self._size / 2
        dx, dy = pos.x() - c, pos.y() - c
        r = math.hypot(dx, dy)
        if r <= self._size * 0.235:
            return "ok"
        if r > self._size * 0.5:
            return None
        ang = math.degrees(math.atan2(-dy, dx))   # 0°=right, 90°=up
        if -45 <= ang < 45:
            return "right"
        if 45 <= ang < 135:
            return "up"
        if ang >= 135 or ang < -135:
            return "left"
        return "down"

    def mousePressEvent(self, e):
        self._active = self._region(e.position())
        self.update()

    def mouseReleaseEvent(self, e):
        reg = self._region(e.position())
        if reg and reg == self._active:
            self.pressed.emit(reg)
        self._active = None
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        c = s / 2
        outer = s * 0.5 - 2
        inner = s * 0.235
        # ring
        p.setPen(QPen(QColor("#2d343f"), 1.5))
        p.setBrush(QBrush(QColor("#1b212b")))
        p.drawEllipse(QPointF(c, c), outer, outer)
        # pressed direction highlight (a 90° wedge)
        if self._active in ("up", "down", "left", "right"):
            start = {"right": -45, "up": 45, "left": 135, "down": 225}[self._active]
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor("#2b3340")))
            p.drawPie(QRectF(c - outer, c - outer, 2 * outer, 2 * outer),
                      int(start * 16), int(90 * 16))
        # direction arrows
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#c7ccd4"))
        mid = (inner + outer) / 2
        a = s * 0.045
        p.drawPolygon(QPolygonF([QPointF(c, c - mid - a), QPointF(c - a, c - mid + a), QPointF(c + a, c - mid + a)]))
        p.drawPolygon(QPolygonF([QPointF(c, c + mid + a), QPointF(c - a, c + mid - a), QPointF(c + a, c + mid - a)]))
        p.drawPolygon(QPolygonF([QPointF(c - mid - a, c), QPointF(c - mid + a, c - a), QPointF(c - mid + a, c + a)]))
        p.drawPolygon(QPolygonF([QPointF(c + mid + a, c), QPointF(c + mid - a, c - a), QPointF(c + mid - a, c + a)]))
        # center OK
        p.setPen(QPen(QColor("#5a8dff"), 1))
        p.setBrush(QBrush(QColor("#5a8dff" if self._active == "ok" else "#3f7bff")))
        p.drawEllipse(QPointF(c, c), inner, inner)
        p.setPen(QColor("#ffffff"))
        f = QFont(); f.setBold(True); f.setPointSize(11); p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "OK")
