"""Application entry point: build the QApplication and show the main window."""
from PySide6.QtWidgets import QApplication

from . import config
from .ui import QSS, MainWindow


def main():
    app = QApplication([])
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    if not config.CFG.get("sources"):
        w.open_setup()   # first run with nothing configured
    app.exec()


if __name__ == "__main__":
    main()
