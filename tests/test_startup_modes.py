"""Regression tests for standalone and connected startup modes."""

from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from vizgui import app


def _run_startup(monkeypatch, server_available, selected):
    """Exercise startup without opening a real GUI or starting services."""
    from vizgui import appearance
    monkeypatch.setattr(appearance, "apply_startup", lambda: None)
    monkeypatch.setattr(app, "QIcon", lambda path: object())
    application = MagicMock()
    window = MagicMock()
    client = MagicMock()
    client.available.return_value = server_available

    monkeypatch.setattr(app, "QApplication", lambda args: application)
    from vizgui import server_client
    from vizgui import ui

    monkeypatch.setattr(server_client, "ServerClient", lambda: client)
    monkeypatch.setattr(ui, "MainWindow", lambda **kwargs: window)
    from vizgui import config
    monkeypatch.setattr(config, "FRESH_INSTALL", False)

    buttons = {}
    pending = []
    monkeypatch.setattr(app, "show_dialog", lambda dialog, done: pending.append(done))

    def event_loop():
        if pending:
            window.show.assert_not_called()
            pending.pop()(0)

    application.exec.side_effect = event_loop

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
            raise AssertionError("Nested event loop")

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
    application.exec.assert_called_once()
    application.quit.assert_called_once()


def test_real_startup_chooser_keeps_event_loop_alive(tmp_path):
    """Closing the chooser must not quit the app before its main window opens."""
    import os
    import subprocess
    import sys

    script = '''
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow
from vizgui import app, ui

windows = []
def window(**kwargs):
    w = QMainWindow()
    windows.append(w)
    def verify():
        assert w.isVisible()
        QApplication.instance().exit(0)
    QTimer.singleShot(10, verify)
    return w

original_show = app.show_dialog
def choose(dialog, finished):
    original_show(dialog, finished)
    assert not dialog.isModal()
    QTimer.singleShot(0, lambda: dialog.buttons()[0].click())

with patch('vizgui.appearance.apply_startup'), \\
     patch('vizgui.server_client.ServerClient') as client, \\
     patch('vizgui.remote_controller.RemoteController'), \\
     patch.object(ui, 'MainWindow', window), \\
     patch.object(app, 'show_dialog', choose), \\
     patch.object(QDialog, 'exec', side_effect=AssertionError('nested event loop')):
    client.return_value.available.return_value = True
    app.main()
    assert len(windows) == 1
    assert windows[0].isVisible()
'''
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=15,
        env={**os.environ, "HOME": str(tmp_path), "QT_QPA_PLATFORM": "offscreen",
             "XDG_CONFIG_HOME": str(tmp_path / "config"),
             "XDG_CACHE_HOME": str(tmp_path / "cache")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
