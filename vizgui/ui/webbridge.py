"""GUI-only bridge between HTTP request threads and the Qt main thread."""
from PySide6.QtCore import QObject, Signal


class WebBridge(QObject):
    do = Signal(str, object)
