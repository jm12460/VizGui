"""Regression tests for standalone and connected startup modes."""

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from vizgui import app


def _run_startup(monkeypatch, server_available, selected):
    """Exercise startup without opening a real GUI or starting services."""
    application = MagicMock()
    window = MagicMock()
    client = MagicMock()
    client.available.return_value = server_available

    monkeypatch.setattr(app, "QApplication", lambda args: application)
    monkeypatch.setattr(app, "ServerClient", lambda: client)
    monkeypatch.setattr(app, "MainWindow", lambda **kwargs: window)
    monkeypatch.setattr(app.config, "FRESH_INSTALL", False)

    buttons = {}

    class FakeMessageBox:
        AcceptRole = QMessageBox.AcceptRole
        ActionRole = QMessageBox.ActionRole
        RejectRole = QMessageBox.RejectRole

        def setWindowTitle(self, title):
            pass

        def setText(self, text):
            pass

        def addButton(self, text, role):
            button = object()
            buttons[text] = button
            return button

        def exec(self):
            pass

        def clickedButton(self):
            return buttons.get(selected)

    monkeypatch.setattr(app, "QMessageBox", FakeMessageBox)

    if server_available and selected == "Connect to Server":
        with patch("vizgui.remote_controller.RemoteController") as controller:
            app.main()
            controller.assert_called_once_with(client)
    else:
        app.main()

    return window, application


def test_no_server_launches_standalone(monkeypatch):
    window, application = _run_startup(
        monkeypatch, False, None)
    window.show.assert_called_once()
    application.exec.assert_called_once()


def test_detected_server_can_launch_standalone(monkeypatch):
    window, application = _run_startup(
        monkeypatch, True, "Run Standalone")
    window.show.assert_called_once()
    application.exec.assert_called_once()


def test_detected_server_can_connect(monkeypatch):
    window, application = _run_startup(
        monkeypatch, True, "Connect to Server")
    window.show.assert_called_once()
    application.exec.assert_called_once()


def test_detected_server_can_quit(monkeypatch):
    window, application = _run_startup(
        monkeypatch, True, "Quit")
    window.show.assert_not_called()
    application.exec.assert_not_called()
