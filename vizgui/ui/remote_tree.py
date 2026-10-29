"""Lazy-loading media tree backed by the VizGui server."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from ..qtasync import run_async


class RemoteMediaTree(QTreeWidget):
    play_requested = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self.client = client
        self.source_index = -1
        self.generation = 0
        self._navigation = 0
        self._root_loading = False
        self._root_waiters = []
        self.setHeaderHidden(True)
        self.setIndentation(16)
        self.setAnimated(True)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(False)

        self.itemExpanded.connect(self._expanded)
        self.itemClicked.connect(self._clicked)
        self.itemDoubleClicked.connect(self._double_clicked)

    def set_source(self, index):
        """Reset to a new server source, invalidating pending requests."""
        self.generation += 1
        self._navigation += 1
        self._root_loading = False
        self._root_waiters.clear()
        self.source_index = index
        self.clear()

        if index < 0:
            return

        root = QTreeWidgetItem(["Loading media…"])
        root.setDisabled(True)
        self.addTopLevelItem(root)
        self._root_loading = True
        self._load(None, "")

    def reveal_folder(self, rel):
        """Expand and select a server-relative folder without resetting the tree."""
        parts = [p for p in rel.replace("\\", "/").split("/")
                 if p and p != "."]
        if ".." in parts:
            self.error_occurred.emit("Invalid folder path")
            return

        self._navigation += 1
        navigation = self._navigation
        generation = self.generation

        def children_of(parent):
            if parent is None:
                return [self.topLevelItem(i)
                        for i in range(self.topLevelItemCount())]
            return [parent.child(i) for i in range(parent.childCount())]

        def walk(parent, depth):
            if (navigation != self._navigation
                    or generation != self.generation):
                return

            if depth == len(parts):
                if parent is not None:
                    self.setCurrentItem(parent)
                    self.scrollToItem(parent)
                return

            def ready(success):
                if (navigation != self._navigation
                        or generation != self.generation):
                    return
                if not success:
                    return

                expected = "/".join(parts[:depth + 1])
                item = next(
                    (child for child in children_of(parent)
                     if isinstance(child.data(0, Qt.UserRole), dict)
                     and child.data(0, Qt.UserRole).get("dir")
                     and child.data(0, Qt.UserRole).get("rel") == expected),
                    None,
                )
                if item is None:
                    self.error_occurred.emit(
                        f"Folder not found on server: {expected}")
                    return

                item.setExpanded(True)
                walk(item, depth + 1)

            self._ensure_loaded(parent, ready)

        walk(None, 0)

    def _ensure_loaded(self, parent, callback):
        if parent is None:
            if self._root_loading:
                self._root_waiters.append(callback)
            else:
                callback(True)
            return

        state = parent.data(0, Qt.UserRole + 1)
        if state == "loaded":
            callback(True)
        elif state == "loading":
            waiters = parent.data(0, Qt.UserRole + 2) or []
            waiters.append(callback)
            parent.setData(0, Qt.UserRole + 2, waiters)
        else:
            parent.setData(0, Qt.UserRole + 2, [callback])
            entry = parent.data(0, Qt.UserRole)
            self._load(parent, entry.get("rel", ""))

    def selected_video(self):
        item = self.currentItem()
        if item is None:
            return None
        entry = item.data(0, Qt.UserRole)
        if isinstance(entry, dict) and not entry.get("dir"):
            return entry.get("path")
        return None

    def _placeholder(self, parent):
        child = QTreeWidgetItem(["Loading…"])
        child.setDisabled(True)
        parent.addChild(child)

    def _load(self, parent, sub):
        generation = self.generation
        source = self.source_index

        if parent is not None:
            parent.setData(0, Qt.UserRole + 1, "loading")

        def completed(result):
            if generation != self.generation:
                return

            if parent is not None and (
                parent.treeWidget() is not self
                or parent.data(0, Qt.UserRole + 1) != "loading"
            ):
                return

            if parent is None:
                self.clear()
            else:
                while parent.childCount():
                    parent.takeChild(0)

            success = isinstance(result, dict) and bool(result.get("ok"))

            if not success:
                message = (
                    result.get("error", "Browse failed")
                    if isinstance(result, dict)
                    else "Browse failed"
                )
                if parent is not None:
                    parent.setData(0, Qt.UserRole + 1, "error")
                    error_item = QTreeWidgetItem([f"⚠ {message}"])
                    error_item.setDisabled(True)
                    parent.addChild(error_item)
                else:
                    item = QTreeWidgetItem([f"⚠ {message}"])
                    item.setDisabled(True)
                    self.addTopLevelItem(item)
                self.error_occurred.emit(str(message))
                self._finish_load(parent, False)
                return

            for entry in result.get("entries", []):
                is_dir = bool(entry.get("dir"))
                icon = "📁" if is_dir else "🎬"
                item = QTreeWidgetItem([
                    f"{icon}  {entry.get('name', '?')}"
                ])
                item.setData(0, Qt.UserRole, entry)

                if is_dir:
                    item.setData(0, Qt.UserRole + 1, "unloaded")
                    self._placeholder(item)

                if parent is None:
                    self.addTopLevelItem(item)
                else:
                    parent.addChild(item)

            if parent is not None:
                parent.setData(0, Qt.UserRole + 1, "loaded")

            if parent is None and self.topLevelItemCount() == 0:
                empty = QTreeWidgetItem(["No media found."])
                empty.setDisabled(True)
                self.addTopLevelItem(empty)

            self._finish_load(parent, True)

        run_async(
            lambda: self.client.browse(source, sub),
            completed,
        )

    def _finish_load(self, parent, success):
        if parent is None:
            self._root_loading = False
            waiters = self._root_waiters[:]
            self._root_waiters.clear()
        else:
            waiters = parent.data(0, Qt.UserRole + 2) or []
            parent.setData(0, Qt.UserRole + 2, [])

        for callback in waiters:
            callback(success)

    def _expanded(self, item):
        entry = item.data(0, Qt.UserRole)
        if not isinstance(entry, dict) or not entry.get("dir"):
            return
        if item.data(0, Qt.UserRole + 1) == "unloaded":
            self._ensure_loaded(item, lambda success: None)

    def _clicked(self, item, column):
        entry = item.data(0, Qt.UserRole)
        if isinstance(entry, dict) and entry.get("dir"):
            item.setExpanded(not item.isExpanded())

    def _double_clicked(self, item, column):
        entry = item.data(0, Qt.UserRole)
        if isinstance(entry, dict) and not entry.get("dir"):
            path = entry.get("path")
            if path:
                self.play_requested.emit(path)
