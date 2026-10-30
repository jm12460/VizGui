"""Non-modal management of sources on the local headless server."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout)

from ..qtasync import run_async
from ..qtpopups import confirm, show_dialog
from .dialogs import SmbDialog


class ServerSourcesDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._closed = False
        self._busy = False
        self._revision = None
        self._server = None
        self._entries = []
        self.setWindowTitle("Server sources")
        self.resize(540, 380)
        layout = QVBoxLayout(self)
        note = QLabel("Folders are on the server computer. Removing a source does not delete files or stop playback.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.listing = QListWidget()
        self.listing.currentRowChanged.connect(self._buttons)
        layout.addWidget(self.listing)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.add_folder = QPushButton("Add folder…")
        self.add_folder.clicked.connect(self._add_folder)
        self.add_smb = QPushButton("Add SMB share…")
        self.add_smb.clicked.connect(self._add_smb)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        self.reload = QPushButton("Refresh")
        self.reload.clicked.connect(self.refresh)
        for button in (self.add_folder, self.add_smb, self.remove, self.reload):
            row.addWidget(button)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.finished.connect(self._finish)
        self.controller.status_changed.connect(self._server_status)
        self.controller.connection_changed.connect(self._connection_changed)
        self._buttons()

    def _finish(self, _):
        if self._closed:
            return
        self._closed = True
        try:
            self.controller.status_changed.disconnect(self._server_status)
            self.controller.connection_changed.disconnect(self._connection_changed)
        except (RuntimeError, TypeError):
            pass

    def _buttons(self, *_):
        ready = not self._busy and self._revision is not None and self.controller.connected
        self.add_folder.setEnabled(ready)
        self.add_smb.setEnabled(ready)
        self.remove.setEnabled(ready and self.listing.currentRow() >= 0)
        self.reload.setEnabled(not self._busy and self.controller.connected)

    def _server_status(self, status):
        if self._closed or self._busy:
            return
        identity = status.get("server") or {}
        revision = status.get("sources_revision")
        if (self._server is not None and
                (identity.get("instance_id") != self._server.get("instance_id")
                 or revision != self._revision)):
            self.refresh()

    def _connection_changed(self, connected):
        if self._closed:
            return
        if not connected:
            self.status.setText("Server disconnected; source changes are unavailable.")
        elif not self._busy:
            self.refresh()
        self._buttons()

    def _run(self, server, action, callback, **payload):
        if not self.controller.connected:
            self.status.setText("Server disconnected; source changes are unavailable.")
            self._buttons()
            return
        self._busy = True
        self._buttons()

        def work():
            if self._closed or not self.controller.connected:
                return {"error": "Server disconnected or window closed"}
            try:
                return self.controller.client.manage_sources(server, action, **payload)
            except Exception:
                if action == "list":
                    return {"error": "Local source administration is unavailable"}
                return {"outcome": "uncertain", "error": "Connection failed; refresh before retrying"}

        def done(result):
            if self._closed:
                return
            self._busy = False
            callback(result)
            self._buttons()

        run_async(work, done)

    def refresh(self, message=""):
        if self._busy or self._closed:
            return
        server = self.controller.snapshot.get("server") or {}
        self.status.setText(message or "Loading server sources…")

        def received(result):
            if not isinstance(result, dict) or result.get("ok") is not True:
                self._revision = None
                error = (str(result.get("error", "Could not load sources"))
                         if isinstance(result, dict) else "Could not load sources")
                self.status.setText((message + "\n" if message else "") + error)
                return
            entries, revision = result.get("sources"), result.get("revision")
            identity = result.get("server")
            if not isinstance(entries, list) or type(revision) is not int or not isinstance(identity, dict):
                self._revision = None
                self.status.setText("Invalid server response")
                return
            self._entries, self._revision, self._server = entries, revision, identity
            self.listing.clear()
            for entry in entries:
                detail = (f"SMB: {entry.get('server', '')}/{entry.get('share', '')}"
                          if entry.get("kind") == "smb" else entry.get("path", ""))
                self.listing.addItem(f"{entry.get('name', '')} — {detail}")
            self.status.setText(message or "Changes apply to the server immediately.")

        self._run(server, "list", received)

    def _context(self):
        return dict(self._server), self._revision

    def _submit(self, context, action, **payload):
        server, revision = context
        self.status.setText("Saving…")

        def completed(result):
            if isinstance(result, dict) and result.get("ok") is True:
                self.controller.refresh()
                self.refresh("Source updated.")
            else:
                error = str(result.get("error", "Could not update sources")) if isinstance(result, dict) else "Could not update sources"
                prefix = ("Could not confirm change" if isinstance(result, dict)
                          and result.get("outcome") == "uncertain" else "Change not applied")
                self.refresh(f"{prefix}: {error}")

        self._run(server, action, completed, revision=revision, **payload)

    def _add_folder(self):
        context = self._context()
        dialog = QDialog(self)
        dialog.setWindowTitle("Add server folder")
        form = QFormLayout(dialog)
        name, path = QLineEdit(), QLineEdit()
        path.setPlaceholderText("/absolute/path/on/server")
        form.addRow("Name (optional):", name)
        form.addRow("Server folder:", path)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        def finished(result):
            if result == QDialog.Accepted:
                self._submit(context, "add", source={"kind": "local", "name": name.text(), "path": path.text()})

        show_dialog(dialog, finished)

    def _add_smb(self):
        context = self._context()
        dialog = SmbDialog(self)

        def finished(result):
            if result == QDialog.Accepted:
                source = dialog.result_source()
                if source is None:
                    self.status.setText("SMB server and share are required.")
                else:
                    self._submit(context, "add", source=source)

        show_dialog(dialog, finished)

    def _remove(self):
        index = self.listing.currentRow()
        if not 0 <= index < len(self._entries):
            return
        context = self._context()
        name = self._entries[index].get("name", "source")
        confirm(self, "Remove source", f"Remove {name} from server sources? Files will not be deleted.",
                lambda: self._submit(context, "remove", index=index))
