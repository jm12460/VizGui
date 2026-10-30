"""Connected UI synchronization without constructing an Engine or listener."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QLineEdit, QListWidget, QMainWindow,
    QStackedWidget,
)

from vizgui.ui import mainwindow as module
from vizgui.ui import remote_tree as tree_module


@pytest.fixture
def window(monkeypatch):
    app = QApplication.instance() or QApplication([])
    pending = []
    schedule = lambda fn, done: pending.append((fn, done))
    monkeypatch.setattr(module, "run_async", schedule)
    monkeypatch.setattr(tree_module, "run_async", schedule)
    win = module.MainWindow.__new__(module.MainWindow)
    QMainWindow.__init__(win)
    win.connected_mode = True
    win.remote_controller = Mock()
    win._remote_sources = []
    win._remote_browse_seq = 0
    win.source_box = QComboBox(win)
    win.target_box = QComboBox(win)
    win.autonext_cb = QCheckBox(win)
    win.search_box = QLineEdit(win)
    win._search_timer = QTimer(win)
    win.results = QListWidget(win)
    win.remote_tree = tree_module.RemoteMediaTree(win.remote_controller.client, win)
    win.browse_stack = QStackedWidget(win)
    win.browse_stack.addWidget(win.results)
    win.browse_stack.addWidget(win.remote_tree)
    for name in ("np_title", "np_badge", "np_bar", "np_time", "remote", "tv_status",
                 "b_audio", "b_subs", "b_chapters"):
        setattr(win, name, Mock())
    win.source_box.currentIndexChanged.connect(win._source_changed)
    win.target_box.currentIndexChanged.connect(win._target_changed)
    win.autonext_cb.toggled.connect(win.remote_controller.set_autonext)
    yield win, pending
    win.deleteLater()
    app.processEvents()


def status(sources):
    return {"ok": True, "sources": sources, "targets": [{"name": "TV"}],
            "target_index": 0, "auto_next": False,
            "now": {"title": "Movie", "pos": 30, "dur": 600, "state": "PLAYING"}}


def video(name):
    return {"name": name, "path": "/test/" + name, "dir": False}


def load(pending, entries):
    _, done = pending.pop(0)
    done({"ok": True, "entries": entries})


def test_removing_last_source_clears_old_playable_paths(window):
    win, pending = window
    win._render_remote(status([{"i": 0, "name": "Media"}]))
    load(pending, [video("old.mkv")])
    win.remote_tree.setCurrentItem(win.remote_tree.topLevelItem(0))
    assert win.remote_tree.selected_video() == "/test/old.mkv"
    win._render_remote(status([]))
    assert win.source_box.currentIndex() == -1
    assert win.remote_tree.source_index == -1
    assert win.remote_tree.selected_video() is None
    assert win.remote_tree.topLevelItemCount() == 0


def test_removing_source_discards_pending_browse_response(window):
    win, pending = window
    win._render_remote(status([{"i": 0, "name": "Media"}]))
    win._render_remote(status([]))
    load(pending, [video("stale.mkv")])
    assert win.remote_tree.topLevelItemCount() == 0


def test_source_update_discards_pending_search_and_returns_to_tree(window):
    win, pending = window
    win._render_remote(status([{"i": 0, "name": "Old library"}]))
    load(pending, [video("old.mkv")])
    win.search_box.setText("episode")
    win._remote_search()
    _, old_search = pending.pop(0)
    assert win.browse_stack.currentWidget() is win.results
    win._render_remote(status([{"i": 0, "name": "New library"}]))
    old_search({"ok": True, "entries": [video("stale-search.mkv")]})
    assert win.results.count() == 0
    assert win.search_box.text() == ""
    assert win.browse_stack.currentWidget() is win.remote_tree
    load(pending, [video("new.mkv")])
    assert win.remote_tree.topLevelItem(0).data(0, Qt.UserRole)["name"] == "new.mkv"


def test_unchanged_status_preserves_browsing_and_selection(window):
    win, pending = window
    state = status([{"i": 0, "name": "Media"}])
    win._render_remote(state)
    load(pending, [video("movie.mkv")])
    item = win.remote_tree.topLevelItem(0)
    win.remote_tree.setCurrentItem(item)
    generation = win.remote_tree.generation
    win._render_remote({**state, "now": {**state["now"], "pos": 45}})
    assert win.remote_tree.currentItem() is item
    assert win.remote_tree.generation == generation
    assert pending == []
    win.remote_controller.select_target.assert_not_called()
    win.remote_controller.set_autonext.assert_not_called()


def test_another_client_target_and_autonext_changes_render_without_echo(window):
    win, pending = window
    state = {**status([]), "targets": [{"name": "TV A"}, {"name": "TV B"}]}
    win._render_remote(state)
    win._render_remote({**state, "target_index": 1, "auto_next": True})
    assert win.target_box.currentIndex() == 1
    assert win.autonext_cb.isChecked()
    win.remote_controller.select_target.assert_not_called()
    win.remote_controller.set_autonext.assert_not_called()
    assert pending == []


def interrupted_status():
    return {**status([]), "server": {"id": "test-server", "instance_id": "test-process"},
            "interrupted": {"id": "checkpoint-a", "path": "/test/episode.mp4",
                            "title": "Episode", "pos": 420}}


@pytest.mark.parametrize("choice", ["Resume", "Not now", "close"])
def test_interrupted_playback_is_nonmodal_and_needs_explicit_resume(window, choice):
    win, _ = window
    state = interrupted_status()
    win._offer_interrupted_playback(state)
    dialog = win._interrupted_dialog
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert not dialog.isSizeGripEnabled()
    assert dialog.testAttribute(Qt.WA_ShowWithoutActivating)
    assert dialog.defaultButton().text() == "Not now"
    assert "7:00" in dialog.text()
    win.remote_controller.resume_interrupted.assert_not_called()
    win._offer_interrupted_playback(state)
    assert win._interrupted_dialog is dialog
    if choice == "close":
        dialog.reject()
    else:
        next(b for b in dialog.buttons() if b.text() == choice).click()
    if choice == "Resume":
        win.remote_controller.resume_interrupted.assert_called_once_with("checkpoint-a", 0)
        win.remote_controller.dismiss_interrupted.assert_not_called()
    else:
        win.remote_controller.resume_interrupted.assert_not_called()
        win.remote_controller.dismiss_interrupted.assert_called_once_with("checkpoint-a", 0)
    win._offer_interrupted_playback(state)
    assert win._interrupted_dialog is None  # no repeated prompts on each poll


@pytest.mark.parametrize("change", ["other_client_playing", "target_changed", "process_changed"])
def test_stale_interruption_prompt_closes_without_starting_playback(window, change):
    win, _ = window
    state = interrupted_status()
    win._offer_interrupted_playback(state)
    dialog = win._interrupted_dialog
    if change == "other_client_playing":
        new = {**state, "interrupted": None}
    elif change == "target_changed":
        new = {**state, "target_index": 1}
    else:
        new = {**state, "server": {**state["server"], "instance_id": "new-process"}}
    win._offer_interrupted_playback(new)
    assert not dialog.isVisible()
    if change == "other_client_playing":
        assert win._interrupted_dialog is None
    else:
        assert win._interrupted_dialog is not dialog
        win._interrupted_dialog.reject()
    win.remote_controller.resume_interrupted.assert_not_called()


def test_disconnect_closes_interruption_prompt_without_command(window):
    win, _ = window
    win._offer_interrupted_playback(interrupted_status())
    dialog = win._interrupted_dialog
    win._remote_connection_changed(False)
    assert not dialog.isVisible()
    assert win._interrupted_dialog is None
    win.remote_controller.resume_interrupted.assert_not_called()
    win.remote_controller.dismiss_interrupted.assert_not_called()


def test_disconnect_does_not_count_as_user_dismissing_checkpoint(window):
    win, _ = window
    state = interrupted_status()
    win._offer_interrupted_playback(state)
    first = win._interrupted_dialog
    win._remote_connection_changed(False)
    win._offer_interrupted_playback(state)
    assert win._interrupted_dialog is not None
    assert win._interrupted_dialog is not first
    win._interrupted_dialog.reject()
    win.remote_controller.resume_interrupted.assert_not_called()


@pytest.mark.parametrize("state", ["starting", "failed", "playing"])
def test_remote_window_renders_playback_request_result(window, state):
    win, _ = window
    request = {"id": "request-a", "state": state, "title": "Episode",
               "error": "Test launch error" if state == "failed" else None}
    win._render_remote({**status([]), "playback_request": request})
    if state == "failed":
        win.np_title.setText.assert_called_with("Playback failed: Episode")
        win.np_time.setText.assert_called_with("Test launch error")
    elif state == "starting":
        win.np_title.setText.assert_called_with("Starting: Episode…")
        win.np_time.setText.assert_called_with("Waiting for playback confirmation")
    else:
        win.np_title.setText.assert_called_with("Movie")
