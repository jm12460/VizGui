"""Asynchronous controller for a remote VizGui playback session.

Does not own an Engine, Chromecast connection, or HTTP listener.
All network I/O runs outside the Qt GUI thread.
"""
from PySide6.QtCore import QObject, Signal, QTimer

from .qtasync import run_async


class RemoteController(QObject):
    POLL_INTERVAL = 2000
    MAX_RETRY_INTERVAL = 30000
    status_changed = Signal(dict)
    connection_changed = Signal(bool)
    error = Signal(str)
    server_restarted = Signal()

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.snapshot = {}
        self.connected = False
        self._polling = False
        self._closed = False
        self._generation = 0
        self._connection_epoch = 0
        self._server = None

        self.timer = QTimer(self)
        self.timer.setInterval(self.POLL_INTERVAL)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        if self.timer.isActive() and not self._closed:
            return
        self._generation += 1
        self._closed = False
        self._polling = False
        self.connected = False
        self.timer.setInterval(self.POLL_INTERVAL)
        self.timer.start()
        self.refresh()

    def close(self):
        """Disconnect the GUI only. Never stop server playback."""
        self._closed = True
        self._generation += 1
        self._connection_epoch += 1
        self._polling = False
        self.connected = False
        self.timer.stop()

    def refresh(self):
        if self._closed or self._polling:
            return
        self._polling = True
        generation = self._generation
        run_async(self.client.connection_status,
                  lambda result: self._received(result, generation))

    def _received(self, result, generation):
        if self._closed or generation != self._generation:
            return
        self._polling = False

        ok = isinstance(result, dict) and result.get("ok") is True
        if ok:
            server = result.get("server")
            if (not isinstance(server, dict) or server.get("ok") is not True
                    or not isinstance(server.get("id"), str) or not server["id"]
                    or ("instance_id" in server and (
                        not isinstance(server["instance_id"], str)
                        or not server["instance_id"]))):
                result = {"error": "Server returned invalid connection identity"}
                ok = False
            elif self._server is not None and server["id"] != self._server["id"]:
                result = {"error": "A different VizGui server is at this address; "
                          "close and reopen the desktop to connect to it"}
                ok = False
            else:
                restarted = (self._server is not None
                             and server.get("instance_id")
                             != self._server.get("instance_id"))
                self._server = dict(server)
                if restarted:
                    self._connection_epoch += 1
                    self.server_restarted.emit()

        if ok:
            self.timer.setInterval(self.POLL_INTERVAL)
            self.snapshot = result
            self.status_changed.emit(result)
        else:
            self._connection_epoch += 1
            self.timer.setInterval(min(
                self.timer.interval() * 2, self.MAX_RETRY_INTERVAL))

        if ok != self.connected:
            self.connected = ok
            self.connection_changed.emit(ok)
        if not ok:
            self.error.emit(
                str(result.get("error", "Server unavailable"))
                if isinstance(result, dict)
                else "Server unavailable"
            )

    def command(self, kind, **payload):
        if self._closed:
            return
        if not self.connected:
            self.error.emit("Server unavailable — command not sent")
            return

        generation = self._generation
        epoch = self._connection_epoch
        server = dict(self._server)

        def send():
            # A queued worker may start after a disconnect or window close.
            # Never retain commands for replay when connectivity returns.
            if (self._closed or not self.connected
                    or generation != self._generation
                    or epoch != self._connection_epoch):
                return {"error": "Connection changed — command not sent"}
            return self.client.command_for_server(server, kind, **payload)

        def completed(result):
            if (self._closed or generation != self._generation
                    or epoch != self._connection_epoch):
                return
            if not isinstance(result, dict) or not result.get("ok"):
                message = (
                    str(result.get("error", "Command failed"))
                    if isinstance(result, dict)
                    else "Command failed"
                )
                self.error.emit(message)
            # Read fresh state even when delivery was uncertain. Retrying a
            # transport toggle could undo a command the server already applied.
            self.refresh()

        run_async(
            send,
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
