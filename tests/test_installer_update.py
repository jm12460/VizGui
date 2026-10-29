"""Regression coverage for the install.sh --update path."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_update_refreshes_desktop_integration_without_sudo(tmp_path):
    home = tmp_path / "home"
    repo = home / "Projects" / "vizgui"
    commands = tmp_path / "bin"
    log = tmp_path / "commands.log"

    commands.mkdir()
    (repo / ".git").mkdir(parents=True)
    (repo / "packaging").mkdir()
    (repo / "vizgui").mkdir()

    icons = {
        "64x64": ("packaging/vizgui-64.png", b"icon-64"),
        "256x256": ("packaging/vizgui-256.png", b"icon-256"),
        "512x512": ("vizgui/icon.png", b"icon-512"),
    }

    for _, (filename, data) in icons.items():
        (repo / filename).write_bytes(data)

    def fake_command(name, body):
        path = commands / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    fake_command(
        "git",
        'echo "git $*" >> "$TEST_LOG"\nexit 0',
    )
    fake_command(
        "sudo",
        'echo "SUDO CALLED" >> "$TEST_LOG"\nexit 99',
    )
    fake_command(
        "kbuildsycoca6",
        'echo "kbuildsycoca6 $*" >> "$TEST_LOG"\nexit 0',
    )
    fake_command(
        "update-desktop-database",
        'echo "desktop database updated" >> "$TEST_LOG"\nexit 0',
    )

    env = os.environ.copy()
    env.update({
        "HOME": str(home),
        "VIZGUI_DEST": str(repo),
        "TEST_LOG": str(log),
        "PATH": str(commands) + os.pathsep + env["PATH"],
    })

    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--update"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    desktop = home / ".local/share/applications/vizgui.desktop"
    content = desktop.read_text()
    assert "Icon=vizgui" in content
    assert "StartupWMClass=vizgui" in content
    assert f"Exec={home}/.local/bin/viz-gui" in content

    for size, (_, expected) in icons.items():
        installed = (
            home / ".local/share/icons/hicolor" /
            size / "apps/vizgui.png"
        )
        assert installed.read_bytes() == expected

    commands_run = log.read_text()
    assert "pull --ff-only" in commands_run
    assert "kbuildsycoca6 --noincremental" in commands_run
    assert "SUDO CALLED" not in commands_run
    assert "VizGui updated" in result.stdout
