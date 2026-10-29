"""Regression tests for connected-mode remote media browsing."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from vizgui.ui import remote_tree as module


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Client:
    def __init__(self):
        self.calls = []

    def browse(self, source, sub):
        self.calls.append((source, sub))
        return {"ok": True, "entries": []}


def test_lazy_folder_expansion(monkeypatch, app):
    pending = []
    monkeypatch.setattr(
        module, "run_async",
        lambda fn, done: pending.append((fn, done)),
    )

    tree = module.RemoteMediaTree(Client())
    tree.set_source(0)

    assert len(pending) == 1
    pending.pop(0)[1]({
        "ok": True,
        "entries": [
            {"name": "Movies", "dir": True, "rel": "Movies"},
            {"name": "film.mkv", "dir": False,
             "rel": "film.mkv", "path": "/media/film.mkv"},
        ],
    })

    folder = tree.topLevelItem(0)
    assert folder.childCount() == 1
    assert folder.data(0, Qt.UserRole + 1) == "unloaded"

    folder.setExpanded(True)
    assert len(pending) == 1
    assert folder.data(0, Qt.UserRole + 1) == "loading"

    pending.pop(0)[1]({
        "ok": True,
        "entries": [
            {"name": "nested.mkv", "dir": False,
             "rel": "Movies/nested.mkv",
             "path": "/media/Movies/nested.mkv"},
        ],
    })

    assert folder.childCount() == 1
    assert folder.data(0, Qt.UserRole + 1) == "loaded"

    folder.setExpanded(False)
    folder.setExpanded(True)
    assert not pending


def test_source_switch_discards_stale_response(monkeypatch, app):
    pending = []
    monkeypatch.setattr(
        module, "run_async",
        lambda fn, done: pending.append((fn, done)),
    )

    tree = module.RemoteMediaTree(Client())
    tree.set_source(0)
    old = pending.pop(0)

    tree.set_source(1)
    new = pending.pop(0)

    old[1]({"ok": True, "entries": [
        {"name": "Old", "dir": True, "rel": "Old"}
    ]})
    assert tree.topLevelItem(0).text(0) == "Loading media…"

    new[1]({"ok": True, "entries": [
        {"name": "New", "dir": True, "rel": "New"}
    ]})
    assert tree.topLevelItem(0).text(0) == "📁  New"


def test_reveal_nested_folder(monkeypatch, app):
    pending = []
    monkeypatch.setattr(
        module, "run_async",
        lambda fn, done: pending.append((fn, done)),
    )

    tree = module.RemoteMediaTree(Client())
    tree.set_source(0)
    tree.reveal_folder("Shows/Season 1")

    pending.pop(0)[1]({"ok": True, "entries": [
        {"name": "Shows", "dir": True, "rel": "Shows"}
    ]})

    assert len(pending) == 1
    pending.pop(0)[1]({"ok": True, "entries": [
        {"name": "Season 1", "dir": True,
         "rel": "Shows/Season 1"}
    ]})

    assert len(pending) == 1
    pending.pop(0)[1]({"ok": True, "entries": [
        {"name": "Episode.mkv", "dir": False,
         "rel": "Shows/Season 1/Episode.mkv",
         "path": "/media/Shows/Season 1/Episode.mkv"}
    ]})

    selected = tree.currentItem()
    assert selected is not None
    assert selected.data(0, Qt.UserRole)["rel"] == "Shows/Season 1"
    assert selected.isExpanded()
    assert tree.topLevelItem(0).isExpanded()
