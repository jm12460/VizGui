"""Themed file-browser icons.

The native system icons (a generic folder and a blue "film strip" for videos)
look out of place in the dark theme and get noisy down a long episode list, so
we paint our own flat, legible glyphs and hand them to the file model through a
QFileIconProvider. Painted with QPainter (no image assets to ship), matching the
qr_pixmap approach used elsewhere.
"""
from PySide6.QtCore import Qt, QRectF, QPointF, QFileInfo
from PySide6.QtGui import QPixmap, QPainter, QColor, QPolygonF, QIcon
from PySide6.QtWidgets import QFileIconProvider

from ..paths import VIDEO_GLOBS

_VIDEO_EXTS = tuple(g[1:].lower() for g in VIDEO_GLOBS)   # (".mp4", ".mkv", ...)
_FOLDER = "#5b86d6"       # soft blue folder
_FRAME = "#2f3a4d"        # muted video frame
_PLAY = "#9fb4e0"         # light play triangle


def _canvas(px):
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    return pm, p


def folder_pixmap(px=64):
    pm, p = _canvas(px)
    p.setBrush(QColor(_FOLDER))
    m = px * 0.15
    w = px - 2 * m
    r = px * 0.07
    # tab, then the body overlapping it, so it reads as one folder
    p.drawRoundedRect(QRectF(m, m + px * 0.07, w * 0.46, px * 0.20), r, r)
    p.drawRoundedRect(QRectF(m, m + px * 0.17, w, px * 0.52), r, r)
    p.end()
    return pm


def video_pixmap(px=64):
    pm, p = _canvas(px)
    m = px * 0.17
    p.setBrush(QColor(_FRAME))
    p.drawRoundedRect(QRectF(m, m, px - 2 * m, px - 2 * m), px * 0.13, px * 0.13)
    p.setBrush(QColor(_PLAY))
    cx, cy, s = px / 2, px / 2, px * 0.15
    p.drawPolygon(QPolygonF([QPointF(cx - s * 0.65, cy - s),
                             QPointF(cx - s * 0.65, cy + s),
                             QPointF(cx + s, cy)]))
    p.end()
    return pm


class ThemedIconProvider(QFileIconProvider):
    """Folder + video icons for the browser; everything else falls back to the
    platform icons. Icons are built once and reused."""

    def __init__(self):
        super().__init__()
        self._folder = QIcon(folder_pixmap())
        self._video = QIcon(video_pixmap())

    def icon(self, arg):
        if isinstance(arg, QFileInfo):
            if arg.isDir():
                return self._folder
            if ("." + arg.suffix().lower()) in _VIDEO_EXTS:
                return self._video
            return super().icon(arg)
        if arg == QFileIconProvider.Folder:      # generic-type overload
            return self._folder
        return super().icon(arg)
