"""Connected Appearance must not construct or save server-owned settings."""
import os
import subprocess
import sys


def test_connected_appearance_is_local_only(tmp_path):
    script = '''
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QDialog, QSizeGrip, QTabWidget
from vizgui.ui.dialogs import SetupDialog

app = QApplication([])

with patch.object(SetupDialog, "_add_media_tabs",
                  side_effect=AssertionError("media UI created")), \\
     patch("vizgui.ui.dialogs.run_async",
           side_effect=AssertionError("scan started")), \\
     patch("vizgui.ui.dialogs.save_config",
           side_effect=AssertionError("shared config saved")), \\
     patch("vizgui.ui.dialogs.save_appearance") as save_appearance:

    dlg = SetupDialog(appearance_only=True)
    tabs = dlg.findChild(QTabWidget)

    assert tabs.count() == 1
    assert tabs.tabText(0) == "Appearance"
    assert not hasattr(dlg, "web_port")

    dlg.show()
    app.processEvents()

    assert not dlg.isSizeGripEnabled()
    assert not any(g.isVisible() for g in dlg.findChildren(QSizeGrip))

    dlg._save()

    save_appearance.assert_called_once()
    assert dlg.result() == QDialog.Accepted
'''

    env = os.environ.copy()
    env.update({
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    })

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
