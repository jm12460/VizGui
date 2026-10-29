"""Verify corrupt configuration produces a GUI error without data loss."""

import os
import subprocess
import sys


def test_corrupt_config_shows_dialog_and_preserves_file(tmp_path):
    config_path = tmp_path / "config.json"
    original = "{deliberately invalid JSON"
    config_path.write_text(original)

    script = """
from unittest.mock import patch
from vizgui import paths

with patch.object(paths, "CONFIG_PATH", CONFIG_FILE):
    from vizgui import app as app_module
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QMessageBox

    original_message = app_module.message

    def show_error(parent, title, message, icon, finished):
        assert title == "VizGui Configuration Error"
        assert "invalid JSON" in message
        assert "left untouched" in message
        assert chr(10) * 2 in message
        assert chr(92) + "n" not in message
        assert icon == QMessageBox.Critical
        dialog = original_message(parent, title, message, icon, finished)
        assert not dialog.isModal()
        QTimer.singleShot(0, dialog.accept)
        return dialog

    with patch.object(app_module, "message", show_error):
        assert app_module.main() == 1
"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "CONFIG_FILE = " + repr(str(config_path)) + "\n" + script,
        ],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
             "XDG_CONFIG_HOME": str(tmp_path / "config"),
             "XDG_CACHE_HOME": str(tmp_path / "cache")},
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert config_path.read_text() == original
