"""Application entry point: build the QApplication and show the main window."""
from PySide6.QtWidgets import QApplication, QMessageBox

def main():
    app = QApplication([])

    try:
        from . import config
        from .ui import QSS, MainWindow
        from .server_client import ServerClient
    except RuntimeError as exc:
        QMessageBox.critical(
            None,
            "VizGui Configuration Error",
            chr(10).join([
                "VizGui could not load its configuration.",
                "",
                str(exc),
                "",
                "Your existing configuration has not been replaced. "
                "Check the file and its permissions before trying again.",
            ]),
        )
        return 1

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
        standalone_button = choice.addButton(
            "Run Standalone", QMessageBox.ActionRole
        )
        choice.addButton("Quit", QMessageBox.RejectRole)
        choice.exec()

        selected = choice.clickedButton()
        if selected is connect_button:
            from .remote_controller import RemoteController
            controller = RemoteController(client)
            w = MainWindow(remote_controller=controller)
        elif selected is standalone_button:
            w = MainWindow()
        else:
            return
    else:
        w = MainWindow()

    w.show()
    if config.FRESH_INSTALL and not connected:
        w.open_setup()
    app.exec()


if __name__ == "__main__":
    main()
