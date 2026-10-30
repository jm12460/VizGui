"""Non-modal dialogs with explicit lifetimes and callback-based results."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox,
)


def dismiss_dialogs(owner):
    """Cancel pending UI flows when their owner closes."""
    owner._popup_closed = True
    for dialog in list(getattr(owner, "_popup_dialogs", [])):
        dialog.reject()


def show_dialog(dialog, finished=None):
    owner = dialog.parentWidget() or QApplication.instance()
    if getattr(owner, "_popup_closed", False):
        dialog.deleteLater()
        return dialog
    if not hasattr(owner, "_popup_dialogs"):
        owner._popup_dialogs = []
        if isinstance(owner, QDialog):
            owner.finished.connect(lambda _: dismiss_dialogs(owner))
    owner._popup_dialogs.append(dialog)
    dialog.setModal(False)
    dialog.setWindowModality(Qt.NonModal)
    dialog.setSizeGripEnabled(False)
    dialog.setAttribute(Qt.WA_DeleteOnClose)

    def completed(result):
        if dialog not in owner._popup_dialogs:
            return
        owner._popup_dialogs.remove(dialog)
        if finished is not None and not getattr(owner, "_popup_closed", False):
            finished(result)

    dialog.finished.connect(completed)
    dialog.show()
    # Some Qt styles recreate the corner grip when a dialog is shown.
    # Apply this after show as well, matching the Appearance window fix.
    dialog.setSizeGripEnabled(False)
    return dialog


def message(parent, title, text, icon=QMessageBox.Information, finished=None):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setTextFormat(Qt.PlainText)
    dialog.setText(text)
    dialog.setIcon(icon)
    dialog.setStandardButtons(QMessageBox.Ok)
    return show_dialog(dialog, finished)


def confirm(parent, title, text, accepted):
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setTextFormat(Qt.PlainText)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Question)
    dialog.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    dialog.setDefaultButton(QMessageBox.No)
    return show_dialog(dialog, lambda _: accepted()
                       if dialog.clickedButton() == dialog.button(QMessageBox.Yes) else None)


def text_input(parent, title, label, accepted, text=""):
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    return show_dialog(dialog, lambda result: accepted(dialog.textValue())
                       if result == QDialog.Accepted else None)


def item_input(parent, title, label, items, accepted):
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setComboBoxItems(items)
    dialog.setComboBoxEditable(False)
    return show_dialog(dialog, lambda result: accepted(dialog.textValue())
                       if result == QDialog.Accepted else None)


def directory_input(parent, accepted):
    dialog = QFileDialog(parent, "Choose a media folder")
    dialog.setDirectory(os.path.expanduser("~"))
    # Keep control of modality instead of delegating to a native modal picker.
    dialog.setOption(QFileDialog.DontUseNativeDialog)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly)
    return show_dialog(dialog, lambda result: accepted(dialog.selectedFiles()[0])
                       if result == QDialog.Accepted and dialog.selectedFiles() else None)


def popup_menu(menu, position):
    # QObject parent ownership retains the menu until it hides.
    menu.aboutToHide.connect(menu.deleteLater)
    menu.popup(position)
