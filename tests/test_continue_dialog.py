"""Continue Watching must remain non-modal in standalone and connected mode."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QListWidget, QPushButton

from vizgui.ui import mainwindow as module


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    # Construct only the window shell: no Engine, discovery, or HTTP listener.
    win = module.MainWindow.__new__(module.MainWindow)
    QMainWindow.__init__(win)
    win.engine = Mock()
    win.engine._cast_observer.return_value = False
    win.engine._now_path = ""
    win.remote_controller = Mock()
    win._status = Mock()
    entries = [{"path": "/media/movie.mp4", "title": "Movie", "pos": 42, "dur": 100}]
    monkeypatch.setattr(module.history, "recent", lambda: entries)
    monkeypatch.setattr(module.history, "forget", Mock())
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("modal dialog opened"))
    pending = []
    monkeypatch.setattr(module, "run_async", lambda fn, done: pending.append(done))
    yield win, app, entries, pending
    win.deleteLater()
    app.processEvents()


def button(dialog, text):
    return next(b for b in dialog.findChildren(QPushButton) if b.text() == text)


@pytest.mark.parametrize("connected", [False, True])
def test_continue_is_nonmodal_reusable_and_resumes(window, connected):
    win, app, entries, pending = window
    win.connected_mode = connected
    win.show_continue()
    if connected:
        win.show_continue()  # repeat click while loading must not duplicate requests
        assert len(pending) == 1
        pending.pop()({"ok": True, "items": entries})
    app.processEvents()
    dialog = win._continue_dialog
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.NonModal
    win.show_continue()
    assert win._continue_dialog is dialog
    assert pending == []
    dialog.findChild(QListWidget).setCurrentRow(0)
    button(dialog, "Resume").click()
    assert win._continue_dialog is None
    owner = win.remote_controller if connected else win.engine
    owner.cast.assert_called_once_with("/media/movie.mp4", 42)
    other = win.engine if connected else win.remote_controller
    other.cast.assert_not_called()
    win.show_continue()
    if connected:
        pending.pop()({"ok": True, "items": entries})
    assert win._continue_dialog is not dialog
    win._continue_dialog.reject()
    assert win._continue_dialog is None


@pytest.mark.parametrize("connected", [False, True])
def test_forget_uses_correct_history_and_close_does_not_stop_playback(window, connected):
    win, app, entries, pending = window
    win.connected_mode = connected
    win.show_continue()
    if connected:
        pending.pop()({"ok": True, "items": entries})
    dialog = win._continue_dialog
    listing = dialog.findChild(QListWidget)
    listing.setCurrentRow(0)
    button(dialog, "Forget").click()
    assert listing.count() == 0
    if connected:
        win.remote_controller.forget.assert_called_once_with("/media/movie.mp4")
        module.history.forget.assert_not_called()
    else:
        module.history.forget.assert_called_once_with("/media/movie.mp4")
        win.remote_controller.forget.assert_not_called()
    dialog.reject()
    assert win._continue_dialog is None
    win.engine.stop.assert_not_called()
    win.remote_controller.transport.assert_not_called()


def test_failed_remote_history_can_be_retried(window):
    win, _, entries, pending = window
    win.connected_mode = True
    win.show_continue()
    pending.pop()({"error": "offline"})
    assert not win._continue_loading
    assert getattr(win, "_continue_dialog", None) is None
    win.show_continue()
    pending.pop()({"ok": True, "items": entries})
    assert win._continue_dialog.isVisible()
    win._continue_dialog.reject()


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("answer", ["Resume", "Start over", "Cancel", "close"])
def test_resume_prompt_waits_for_explicit_choice(window, monkeypatch, connected, answer):
    from PySide6.QtWidgets import QMessageBox
    win, _, entries, pending = window
    win.connected_mode = connected
    monkeypatch.setattr(module.history, "resume_pos", lambda path: 42)
    win._play("/media/movie.mp4")
    if connected:
        pending.pop()({"ok": True, "items": entries})
    dialog = win._popup_dialogs[-1]
    assert not dialog.isModal()
    owner = win.remote_controller if connected else win.engine
    owner.cast.assert_not_called()
    if answer == "close":
        dialog.reject()
    elif answer == "Cancel":
        dialog.button(QMessageBox.Cancel).click()
    else:
        next(b for b in dialog.buttons() if b.text() == answer).click()
    if answer in ("Cancel", "close"):
        owner.cast.assert_not_called()
    else:
        owner.cast.assert_called_once_with("/media/movie.mp4", 42 if answer == "Resume" else 0)


@pytest.mark.parametrize("connected", [False, True])
@pytest.mark.parametrize("answer", ["yes", "no", "close"])
def test_clear_history_requires_confirmation(window, monkeypatch, connected, answer):
    from PySide6.QtWidgets import QMessageBox
    win, _, entries, pending = window
    win.connected_mode = connected
    local_clear = Mock()
    monkeypatch.setattr(module.history, "clear", local_clear)
    win.show_continue()
    if connected:
        pending.pop()({"ok": True, "items": entries})
    dialog = win._continue_dialog
    button(dialog, "Clear all").click()
    question = dialog._popup_dialogs[-1]
    assert not question.isModal()
    if answer == "close":
        question.reject()
    else:
        question.button(QMessageBox.Yes if answer == "yes" else QMessageBox.No).click()
    target = win.remote_controller.clear_history if connected else local_clear
    assert target.call_count == (1 if answer == "yes" else 0)
    if answer != "yes":
        assert dialog.isVisible()
        dialog.reject()


def test_qr_window_is_nonmodal_reused_and_dismissed_on_pin_change(window, monkeypatch):
    win, _, _, _ = window
    monkeypatch.setattr(module.config, "CFG", {"web": {"token": "test-token", "pin": "123456"}})
    monkeypatch.setattr(module, "local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(module, "qr_pixmap", lambda data: None)
    win.show_pair_qr()
    dialog = win._pair_dialog
    assert not dialog.isModal()
    assert dialog.isVisible()
    win.show_pair_qr()
    assert win._pair_dialog is dialog
    win.new_pairing()
    assert win._pair_dialog is None
    win.engine.new_pairing.assert_called_once()


@pytest.mark.parametrize("connected", [False, True])
def test_setup_is_nonmodal_reused_and_reloads_only_after_save(window, monkeypatch, connected):
    win, _, _, _ = window
    win.connected_mode = connected
    created = []

    def setup(parent, appearance_only=False):
        dialog = QDialog(parent)
        created.append((dialog, appearance_only))
        return dialog

    monkeypatch.setattr(module, "SetupDialog", setup)
    monkeypatch.setattr(module, "load_config", Mock(return_value={}))
    monkeypatch.setattr(module.config, "CFG", {})
    win._reload_sources = Mock()
    win._reload_targets = Mock()
    win._target_changed = Mock()
    win.target_box = Mock()
    win.open_setup()
    dialog = created[0][0]
    assert created[0][1] == connected
    assert not dialog.isModal()
    win.open_setup()
    assert len(created) == 1
    dialog.reject()
    win._reload_sources.assert_not_called()
    win.open_setup()
    created[-1][0].accept()
    assert win._reload_sources.call_count == (0 if connected else 1)
    assert win._reload_targets.call_count == (0 if connected else 1)
