"""Callback-based popup behavior, tested without devices or real settings."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QInputDialog, QListWidget, QMessageBox,
)

from vizgui import qtpopups as popups
from vizgui.ui import dialogs as module


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def parent(app):
    dialog = QDialog()
    dialog.show()
    yield dialog
    popups.dismiss_dialogs(dialog)
    dialog.deleteLater()


def assert_nonmodal(dialog):
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.NonModal


def test_message_lifetime_and_nonmodal(parent, app):
    dialog = popups.message(parent, "Error", "A test error", QMessageBox.Warning)
    assert_nonmodal(dialog)
    assert dialog in parent._popup_dialogs
    dialog.button(QMessageBox.Ok).click()
    assert dialog not in parent._popup_dialogs


@pytest.mark.parametrize("answer", ["yes", "no", "close"])
def test_confirmation_requires_explicit_yes(parent, answer):
    accepted = Mock()
    dialog = popups.confirm(parent, "Clear", "Clear test history?", accepted)
    assert_nonmodal(dialog)
    if answer == "close":
        dialog.reject()
    else:
        dialog.button(QMessageBox.Yes if answer == "yes" else QMessageBox.No).click()
    assert accepted.call_count == (1 if answer == "yes" else 0)


@pytest.mark.parametrize("accept", [True, False])
def test_text_and_item_inputs(parent, accept):
    for create in (
        lambda cb: popups.text_input(parent, "Name", "Name:", cb, text="original"),
        lambda cb: popups.item_input(parent, "Device", "Select:", ["one", "two"], cb),
    ):
        callback = Mock()
        dialog = create(callback)
        assert_nonmodal(dialog)
        dialog.setTextValue("two")
        if accept:
            dialog.accept()
            callback.assert_called_once_with("two")
        else:
            dialog.reject()
            callback.assert_not_called()


def test_directory_picker_is_nonmodal_and_cancel_is_safe(parent):
    callback = Mock()
    dialog = popups.directory_input(parent, callback)
    assert_nonmodal(dialog)
    assert dialog.testOption(QFileDialog.DontUseNativeDialog)
    assert dialog.fileMode() == QFileDialog.Directory
    dialog.reject()
    callback.assert_not_called()


def test_closing_parent_cancels_pending_input(parent):
    callback = Mock()
    child = popups.text_input(parent, "Name", "Name:", callback)
    parent.reject()
    assert not child.isVisible()
    assert parent._popup_dialogs == []
    callback.assert_not_called()


@pytest.fixture
def setup(monkeypatch, app):
    cfg = {"sources": [], "targets": [{"type": "cast", "addr": "test-tv", "name": "TV"}],
           "web": {"enabled": False, "port": 8950, "token": "test-token"}}
    monkeypatch.setattr(module.config, "CFG", deepcopy(cfg))
    monkeypatch.setattr(module, "local_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(module, "lan_cidr", lambda: "127.0.0.0/8")
    monkeypatch.setattr(module.platforms, "firewall_allow_cmd", lambda *args: "")
    monkeypatch.setattr(module.SetupDialog, "_autoscan", lambda self: None)
    monkeypatch.setattr(module, "save_config", Mock())
    monkeypatch.setattr(module, "save_appearance", Mock())
    dialog = module.SetupDialog()
    popups.show_dialog(dialog)
    yield dialog
    dialog.reject()


def test_setup_cancel_does_not_mutate_nested_targets(setup):
    assert_nonmodal(setup)
    setup.targets[0]["auth_token"] = "new-test-token"
    setup.reject()
    assert "auth_token" not in module.config.CFG["targets"][0]
    module.save_config.assert_not_called()


def test_add_folder_requires_both_picker_and_name_acceptance(setup, tmp_path):
    setup._add_source()
    picker = setup._popup_dialogs[-1]
    picker.setDirectory(str(tmp_path))
    picker.accept()
    naming = setup._popup_dialogs[-1]
    assert isinstance(naming, QInputDialog)
    naming.reject()
    assert setup.sources == []
    setup._add_source()
    setup._popup_dialogs[-1].setDirectory(str(tmp_path))
    setup._popup_dialogs[-1].accept()
    setup._popup_dialogs[-1].setTextValue("Test media")
    setup._popup_dialogs[-1].accept()
    assert setup.sources == [{"name": "Test media", "path": str(tmp_path)}]


@pytest.mark.parametrize("save_anyway", [True, False])
def test_smb_failure_requires_confirmation(setup, monkeypatch, save_anyway):
    monkeypatch.setattr(module, "smb_mount", Mock(side_effect=RuntimeError("offline")))
    setup._add_smb()
    smb = setup._popup_dialogs[-1]
    assert_nonmodal(smb)
    smb.server.setText("test-nas")
    smb.share.setText("media")
    smb.accept()
    confirm = setup._popup_dialogs[-1]
    assert_nonmodal(confirm)
    confirm.button(QMessageBox.Yes if save_anyway else QMessageBox.No).click()
    assert len(setup.sources) == (1 if save_anyway else 0)
    module.save_config.assert_not_called()


def test_device_selection_only_adds_on_accept(setup):
    devices = [{"addr": "other-tv", "name": "Other TV"}]
    setup._scan_done(devices)
    picker = setup._popup_dialogs[-1]
    assert_nonmodal(picker)
    picker.reject()
    assert len(setup.targets) == 1
    setup._scan_done(devices)
    setup._popup_dialogs[-1].accept()
    assert setup.targets[-1]["addr"] == "other-tv"


def test_pairing_stages_wait_for_acceptance_and_remain_local_until_save(setup, monkeypatch):
    start = Mock(return_value=("request-token", "challenge"))
    finish = Mock(return_value="paired-test-token")
    monkeypatch.setattr(module, "sc_pair_start", start)
    monkeypatch.setattr(module, "sc_pair_finish", finish)
    setup.tgt_list.setCurrentRow(0)
    setup._pair()
    port = setup._popup_dialogs[-1]
    assert_nonmodal(port)
    start.assert_not_called()
    port.accept()
    start.assert_called_once_with("test-tv", "7345")
    code = setup._popup_dialogs[-1]
    assert_nonmodal(code)
    finish.assert_not_called()
    code.setTextValue("1234")
    code.accept()
    finish.assert_called_once_with("test-tv", "7345", "request-token", "1234", "challenge")
    assert setup.targets[0]["auth_token"] == "paired-test-token"
    assert "auth_token" not in module.config.CFG["targets"][0]
    module.save_config.assert_not_called()


def test_setup_close_cancels_pairing_without_network_calls(setup, monkeypatch):
    start = Mock()
    monkeypatch.setattr(module, "sc_pair_start", start)
    setup.tgt_list.setCurrentRow(0)
    setup._pair()
    setup.reject()
    assert setup._popup_dialogs == []
    start.assert_not_called()


def test_setup_saves_only_after_explicit_save(setup):
    setup.sources.append({"name": "Test", "path": "/test"})
    module.save_config.assert_not_called()
    setup._save()
    module.save_config.assert_called_once()
    assert module.config.CFG["sources"] == setup.sources
    assert module.config.CFG["web"]["token"] == "test-token"
    assert setup.result() == QDialog.Accepted


def test_popup_menu_does_not_start_nested_loop(parent):
    from PySide6.QtWidgets import QMenu
    menu = QMenu(parent)
    action = menu.addAction("Test action")
    selected = Mock()
    action.triggered.connect(selected)
    popups.popup_menu(menu, parent.pos())
    assert menu.isVisible()
    action.trigger()
    selected.assert_called_once()
    menu.hide()


def test_dialog_result_callback_runs_once(parent):
    callback = Mock()
    dialog = popups.show_dialog(QDialog(parent), callback)
    dialog.accept()
    dialog.reject()
    callback.assert_called_once_with(QDialog.Accepted)


def test_closed_setup_ignores_late_scan_results(setup):
    setup.reject()
    setup._scan_done([{"addr": "late-tv", "name": "Late TV"}])
    assert getattr(setup, "_popup_dialogs", []) == []
    assert len(setup.targets) == 1


@pytest.mark.parametrize("kind", ["dialog", "message", "text", "item", "directory", "smb"])
def test_all_popup_types_disable_corner_resize_grip(parent, app, kind):
    from PySide6.QtWidgets import QSizeGrip

    factories = {
        "dialog": lambda: popups.show_dialog(QDialog(parent)),
        "message": lambda: popups.message(parent, "Test", "Test message"),
        "text": lambda: popups.text_input(parent, "Name", "Name:", Mock()),
        "item": lambda: popups.item_input(parent, "Device", "Select:", ["Test"], Mock()),
        "directory": lambda: popups.directory_input(parent, Mock()),
        "smb": lambda: popups.show_dialog(module.SmbDialog(parent)),
    }
    dialog = factories[kind]()
    app.processEvents()
    assert not dialog.isSizeGripEnabled()
    assert not any(grip.isVisible() for grip in dialog.findChildren(QSizeGrip))
    dialog.reject()


def test_show_time_resize_grip_is_disabled_again(parent, app):
    from PySide6.QtWidgets import QSizeGrip

    class StyleEnabledGripDialog(QDialog):
        def showEvent(self, event):
            super().showEvent(event)
            self.setSizeGripEnabled(True)

    dialog = StyleEnabledGripDialog(parent)
    dialog.setSizeGripEnabled(True)
    popups.show_dialog(dialog)
    app.processEvents()
    assert not dialog.isSizeGripEnabled()
    assert not any(grip.isVisible() for grip in dialog.findChildren(QSizeGrip))
    dialog.reject()
