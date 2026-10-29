"""The remote panel delegates TV commands to its owning window."""

from unittest.mock import Mock

from vizgui.ui.remote import RemotePanel


def test_remote_delegates_tv_key_to_window():
    window = Mock()
    panel = RemotePanel.__new__(RemotePanel)

    panel.win = window
    panel.send("volup")

    window.send_tv_key.assert_called_once_with("volup")
