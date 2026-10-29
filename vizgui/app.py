"""Application entry point: build the QApplication and show the main window."""
from PySide6.QtWidgets import QApplication, QMessageBox

from . import config
from .ui import QSS, MainWindow
from .server_client import ServerClient


def main():
    app = QApplication([])
    app.setStyleSheet(QSS)
    client = ServerClient()
    connected = client.available()

    if connected:
        choice = QMessageBox()
        choice.setWindowTitle("VizGui Server Detected")
        choice.setText(
            "An existing VizGui server is running.\\n\\n"
            "Connect to its playback session?"
        )
        connect_button = choice.addButton(
            "Connect to Server", QMessageBox.AcceptRole
        )
        choice.addButton("Cancel", QMessageBox.RejectRole)
        choice.exec()

        if choice.clickedButton() is not connect_button:
            return

        from .remote_controller import RemoteController
        controller = RemoteController(client)
        w = MainWindow(remote_controller=controller)
    else:
        w = MainWindow()

    w.show()
    if config.FRESH_INSTALL and not connected:
        w.open_setup()
    app.exec()


if __name__ == "__main__":
    main()
