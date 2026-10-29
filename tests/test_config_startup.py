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
    from vizgui.app import main
    from PySide6.QtWidgets import QMessageBox

    def show_error(parent, title, message):
        assert title == "VizGui Configuration Error"
        assert "invalid JSON" in message
        assert "left untouched" in message
        assert chr(10) * 2 in message
        assert chr(92) + "n" not in message
        return QMessageBox.Ok

    with patch.object(QMessageBox, "critical", show_error):
        assert main() == 1
"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "CONFIG_FILE = " + repr(str(config_path)) + "\n" + script,
        ],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert config_path.read_text() == original
