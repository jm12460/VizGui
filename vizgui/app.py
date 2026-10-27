"""Application entry point: build the QApplication and show the main window."""
from PySide6.QtWidgets import QApplication

from . import config
from .ui import QSS, MainWindow


def main():
    app = QApplication([])
    app.setStyleSheet(QSS)
    w = MainWindow()
    w.show()
    if config.FRESH_INSTALL:
        w.open_setup()   # genuine first run: guide setup (the TV auto-appears)
    app.exec()


if __name__ == "__main__":
    main()
