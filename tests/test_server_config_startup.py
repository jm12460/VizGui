"""Test safe headless-server startup with invalid configuration."""

import os
import subprocess
import sys


def test_server_rejects_corrupt_config_without_data_loss(tmp_path):
    config_path = tmp_path / "config.json"
    original = "{deliberately invalid JSON"
    config_path.write_text(original)

    script = """
from unittest.mock import patch
from vizgui import paths

with patch.object(paths, "CONFIG_PATH", CONFIG_FILE):
    import vizgui.server
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

    assert result.returncode != 0
    assert "VizGui server cannot start:" in result.stderr
    assert "invalid JSON" in result.stderr
    assert "Traceback" not in result.stderr
    assert config_path.read_text() == original
