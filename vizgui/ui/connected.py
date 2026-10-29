"""Desktop controller for an existing VizGui server; owns no Engine."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox,
)

from ..qtasync import run_async
from ..server_client import ServerClient


class ConnectedWindow(QMainWindow):
    def __init__(self, client: ServerClient):
        super().__init__()
        self.client = client
        self._busy = False
        self.setWindowTitle("VizGui — Connected")
        self.resize(560, 300)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.server_label = QLabel(
            f"Server: {client.host}:{client.port}  "
            f"·  Session: {client.session_id}"
        )
        self.title_label = QLabel("Connecting…")
        self.state_label = QLabel("")
        layout.addWidget(self.server_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.state_label)

        controls = QHBoxLayout()
        for label, action in (
            ("Previous", "prev"),
            ("Play / Pause", "playpause"),
            ("Next", "next"),
            ("Stop", "stop"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda checked=False, a=action: self.send(a)
            )
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addStretch()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        run_async(self.client.status, self._status_received)

    def _status_received(self, result):
        self._busy = False
        if not isinstance(result, dict) or not result.get("ok"):
            self.state_label.setText("Connection lost or server unavailable")
            return

        now = result.get("now") or {}
        self.title_label.setText(
            now.get("title") or "Nothing playing"
        )
        self.state_label.setText(
            f"State: {now.get('state') or 'Idle'}  "
            f"·  Position: {int(now.get('pos') or 0)}s"
        )

    def send(self, action):
        def completed(result):
            if not isinstance(result, dict) or not result.get("ok"):
                QMessageBox.warning(
                    self, "VizGui",
                    "Could not send command to the server."
                )
            self.refresh()

        run_async(
            lambda: self.client.command(
                "transport", action=action
            ),
            completed,
        )

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
