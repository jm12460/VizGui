"""Asynchronous controller for a remote VizGui playback session.

Does not own an Engine, Chromecast connection, or HTTP listener.
All network I/O runs outside the Qt GUI thread.
"""
from PySide6.QtCore import QObject, Signal, QTimer

from .qtasync import run_async


class RemoteController(QObject):
    status_changed = Signal(dict)
    connection_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.snapshot = {}
        self.connected = False
        self._polling = False
        self._closed = False

        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        self._closed = False
        self.timer.start()
        self.refresh()

    def close(self):
        """Disconnect the GUI only. Never stop server playback."""
        self._closed = True
        self.timer.stop()

    def refresh(self):
        if self._closed or self._polling:
            return
        self._polling = True
        run_async(self.client.status, self._received)

    def _received(self, result):
        self._polling = False
        if self._closed:
            return

        ok = isinstance(result, dict) and result.get("ok") is True

        if ok:
            self.snapshot = result
            self.status_changed.emit(result)
        else:
            self.error.emit(
                str(result.get("error", "Server unavailable"))
                if isinstance(result, dict)
                else "Server unavailable"
            )

        if ok != self.connected:
            self.connected = ok
            self.connection_changed.emit(ok)

    def command(self, kind, **payload):
        if self._closed:
            return

        def completed(result):
            if self._closed:
                return
            if not isinstance(result, dict) or not result.get("ok"):
                message = (
                    str(result.get("error", "Command failed"))
                    if isinstance(result, dict)
                    else "Command failed"
                )
                self.error.emit(message)
            else:
                self.refresh()

        run_async(
            lambda: self.client.command(kind, **payload),
            completed,
        )

    def transport(self, action):
        self.command("transport", action=action)

    def send_tv_key(self, key):
        self.command("key", cmd=key)

    def select_target(self, index):
        self.command("target", index=index)

    def cast(self, path, resume=0):
        self.command("cast", path=path, resume=resume)

    def set_autonext(self, enabled):
        self.command("autonext", on=bool(enabled))

    def jump_to(self, seconds):
        self.command("jump", secs=int(seconds))

    def select_audio(self, index):
        self.command("audio", i=index)

    def forget(self, path):
        self.command("forget", path=path)

    def clear_history(self):
        self.command("clear")

    def select_subtitle(self, index):
        self.command("sub", i=index)
