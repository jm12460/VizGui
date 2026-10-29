"""Application entry point: build the QApplication and show the main window."""
from pathlib import Path
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .qtpopups import show_dialog, message


def main():
    from .appearance import apply_startup
    apply_startup()

    app = QApplication([])

    # Identity shared by KDE taskbar, desktop launcher, and Qt windows.
    app.setApplicationName("vizgui")
    app.setApplicationDisplayName("VizGui")
    app.setDesktopFileName("vizgui")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))

    try:
        from . import config
        from .ui import QSS, MainWindow
        from .server_client import ServerClient
    except RuntimeError as exc:
        message(
            None,
            "VizGui Configuration Error",
            chr(10).join([
                "VizGui could not load its configuration.",
                "",
                str(exc),
                "",
                "Your existing configuration has not been replaced. "
                "Check the file and its permissions before trying again.",
            ]), QMessageBox.Critical,
            finished=lambda _: app.quit(),
        )
        app.exec()
        return 1

    app.setStyleSheet(QSS)
    client = ServerClient()
    connected = client.available()

    if connected:
        choice = QMessageBox()
        choice.setWindowTitle("VizGui Server Detected")
        choice.setText(
            "An existing VizGui server is running.\n\n"
            "Connect to its playback session?"
        )
        connect_button = choice.addButton(
            "Connect to Server", QMessageBox.AcceptRole
        )
        standalone_button = choice.addButton(
            "Run Standalone", QMessageBox.ActionRole
        )
        choice.addButton("Quit", QMessageBox.RejectRole)

        def selected(_):
            button = choice.clickedButton()
            if button is connect_button:
                from .remote_controller import RemoteController
                controller = RemoteController(client)
                window = MainWindow(remote_controller=controller)
            elif button is standalone_button:
                window = MainWindow(playback_role="observer", observer_client=client)
            else:
                app.quit()
                return
            app._main_window = window
            window.show()

        show_dialog(choice, selected)
    else:
        app._main_window = MainWindow()
        app._main_window.show()
        if config.FRESH_INSTALL:
            app._main_window.open_setup()
    app.exec()


if __name__ == "__main__":
    main()
