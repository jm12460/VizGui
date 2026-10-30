"""Non-modal connected source management with a fake client."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit, QMessageBox

from vizgui.ui import server_sources as module


class Controller(QObject):
    status_changed = Signal(dict)
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.connected = True
        self.snapshot = {"server": {"id": "test", "instance_id": "one"}}
        self.client = Mock()
        self.refresh = Mock()


@pytest.fixture
def dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    monkeypatch.setattr(module, "run_async", lambda fn, done: pending.append((fn, done)))
    controller = Controller()
    window = module.ServerSourcesDialog(controller)
    window.show()
    yield window, controller, pending
    window.reject()
    window.deleteLater()
    app.processEvents()


def finish(pending, result):
    _, callback = pending.pop(0)
    callback(result)


def response(revision=0, entries=None):
    return {"ok": True, "revision": revision, "server": {"id": "test", "instance_id": "one"},
            "sources": entries or [{"name": "Media", "kind": "local", "path": "/server/media"}]}


def test_add_folder_uses_server_path_and_explicit_save(dialog):
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    assert not window.isModal()
    window._add_folder()
    child = window._popup_dialogs[-1]
    assert not child.isModal() and not child.isSizeGripEnabled()
    fields = child.findChildren(QLineEdit)
    fields[0].setText("Movies")
    fields[1].setText("/server/movies")
    assert pending == []
    child.accept()
    fn, done = pending.pop(0)
    controller.client.manage_sources.return_value = response(1)
    result = fn()
    controller.client.manage_sources.assert_called_once_with(
        controller.snapshot["server"], "add", revision=0,
        source={"kind": "local", "name": "Movies", "path": "/server/movies"})
    done(result)
    controller.refresh.assert_called_once()
    finish(pending, response(1))
    assert "Source updated" in window.status.text()


def test_remove_confirmation_uses_original_revision_after_refresh(dialog):
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    window.listing.setCurrentRow(0)
    window._remove()
    confirmation = window._popup_dialogs[-1]
    controller.status_changed.emit({"server": controller.snapshot["server"], "sources_revision": 1})
    finish(pending, response(1, [{"name": "Different", "kind": "local", "path": "/other"}]))
    confirmation.button(QMessageBox.Yes).click()
    fn, done = pending.pop(0)
    controller.client.manage_sources.return_value = {"ok": False, "error": "Sources changed; refresh and try again"}
    done(fn())
    assert controller.client.manage_sources.call_args.kwargs == {"revision": 0, "index": 0}
    finish(pending, response(1))
    assert "Change not applied" in window.status.text()


def test_disconnect_blocks_new_changes_and_close_discards_queued_work(dialog):
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    controller.connected = False
    controller.connection_changed.emit(False)
    assert not window.add_folder.isEnabled()
    assert not window.remove.isEnabled()
    controller.connected = True
    window.refresh()
    fn, _ = pending.pop(0)
    window.reject()
    assert "error" in fn()
    controller.client.manage_sources.assert_not_called()


def test_invalid_or_remote_server_response_disables_mutations(dialog):
    window, _, pending = dialog
    window.refresh()
    finish(pending, {"error": "Source management is available only on the server machine"})
    assert not window.add_folder.isEnabled()
    assert "server machine" in window.status.text()


def test_smb_secret_is_submitted_only_to_local_admin_and_never_rendered(dialog):
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    window._add_smb()
    child = window._popup_dialogs[-1]
    child.server.setText("test-nas")
    child.share.setText("media")
    child.password.setText("temporary-test-secret")
    assert child.password.echoMode() == QLineEdit.Password
    child.accept()
    fn, done = pending.pop(0)
    controller.client.manage_sources.return_value = response(1, [{"name": "media", "kind": "smb",
                                                                  "server": "test-nas", "share": "media"}])
    result = fn()
    assert controller.client.manage_sources.call_args.kwargs["source"]["smb"]["password"] == "temporary-test-secret"
    done(result)
    finish(pending, response(1, [{"name": "media", "kind": "smb", "server": "test-nas", "share": "media"}]))
    assert "temporary-test-secret" not in window.listing.item(0).text()
    assert "temporary-test-secret" not in window.status.text()


def test_lost_mutation_response_is_not_claimed_to_have_failed(dialog):
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    window._add_folder()
    child = window._popup_dialogs[-1]
    child.findChildren(QLineEdit)[1].setText("/server/media")
    child.accept()
    fn, done = pending.pop(0)
    controller.client.manage_sources.side_effect = OSError("connection lost")
    done(fn())
    finish(pending, {"error": "Still offline"})
    assert "Could not confirm change" in window.status.text()
    assert "Still offline" in window.status.text()
    assert not window.add_folder.isEnabled()


def test_folder_picker_selects_path_without_saving_until_confirmed(dialog, tmp_path):
    from PySide6.QtWidgets import QPushButton
    window, controller, pending = dialog
    window.refresh()
    finish(pending, response())
    window._add_folder()
    child = window._popup_dialogs[-1]
    assert child.width() >= 560
    fields = child.findChildren(QLineEdit)
    fields[1].setText("/manual/path")
    browse = next(button for button in child.findChildren(QPushButton) if button.text() == "Browse…")
    browse.click()
    picker = child._popup_dialogs[-1]
    assert not picker.isModal() and not picker.isSizeGripEnabled()
    picker.reject()
    assert fields[1].text() == "/manual/path"
    browse.click()
    picker = child._popup_dialogs[-1]
    folder = tmp_path / "server folder"
    folder.mkdir()
    picker.setDirectory(str(folder))
    picker.accept()
    assert fields[1].text() == str(folder)
    assert fields[0].text() == ""  # server supplies the default name
    assert pending == []
    child.accept()
    fn, _ = pending.pop(0)
    controller.client.manage_sources.return_value = response(1)
    fn()
    assert controller.client.manage_sources.call_args.kwargs["source"] == {
        "kind": "local", "name": "", "path": str(folder)}
