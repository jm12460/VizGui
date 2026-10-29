"""Run installer updates in a disposable HOME with an allowlisted PATH."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installation(tmp_path):
    home = tmp_path / "home"
    repo = home / "Projects/vizgui"
    commands = tmp_path / "bin"
    log = tmp_path / "commands.log"
    commands.mkdir()
    (repo / ".git").mkdir(parents=True)
    icons = {
        "64x64": ("packaging/vizgui-64.png", b"icon-64"),
        "256x256": ("packaging/vizgui-256.png", b"icon-256"),
        "512x512": ("vizgui/icon.png", b"icon-512"),
    }
    for filename, data in icons.values():
        path = repo / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    # Only filesystem utilities may run for real. No inherited PATH, service
    # tools, Python launcher, network clients, or playback tools are available.
    bash = shutil.which("bash")
    for name in ("mkdir", "dirname", "cat", "chmod", "install"):
        (commands / name).symlink_to(shutil.which(name))

    def fake(name, status=0):
        path = commands / name
        path.write_text(
            '#!/bin/sh\nprintf "%s\\n" "' + name + ' $*" >> "$TEST_LOG"\n'
            + f"exit {status}\n"
        )
        path.chmod(0o755)

    fake("git")
    fake("kbuildsycoca6")
    fake("update-desktop-database")
    for name in ("sudo", "systemctl", "service", "kill", "pkill", "killall",
                 "go-chromecast", "curl", "wget", "python", "python3",
                 "pacman", "apt-get", "dnf", "viz-gui"):
        fake(name, 99)

    env = os.environ.copy()
    env.update(HOME=str(home), VIZGUI_DEST=str(repo), TEST_LOG=str(log),
               PATH=str(commands), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"),
               XDG_CACHE_HOME=str(home / ".cache"))
    protected = {}
    for filename in (".config/vizgui/config.json", ".config/vizgui/active-cast.json",
                     ".local/share/vizgui/session.json"):
        path = home / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test credentials and session sentinel\n")
        protected[path] = path.read_bytes()

    def run():
        result = subprocess.run(
            [bash, str(ROOT / "install.sh"), "--update"], env=env,
            capture_output=True, text=True, timeout=15,
        )
        for path, original in protected.items():
            assert path.read_bytes() == original
        calls = log.read_text().splitlines() if log.exists() else []
        assert all(line.split()[0] in {
            "git", "kbuildsycoca6", "kbuildsycoca5", "update-desktop-database",
        } for line in calls), calls
        return result

    return home, repo, icons, commands, log, fake, run


def test_update_refreshes_desktop_integration_without_sudo(installation):
    home, repo, icons, _, log, _, run = installation
    for _ in range(2):
        result = run()
        assert result.returncode == 0, result.stdout + result.stderr
        desktop = home / ".local/share/applications/vizgui.desktop"
        content = desktop.read_text()
        assert "Icon=vizgui" in content
        assert "StartupWMClass=vizgui" in content
        assert f"Exec={home}/.local/bin/viz-gui" in content
        launcher = home / ".local/bin/viz-gui"
        assert os.access(launcher, os.X_OK)
        assert f'os.path.expanduser("{repo}")' in launcher.read_text()
        for size, (_, expected) in icons.items():
            installed = home / ".local/share/icons/hicolor" / size / "apps/vizgui.png"
            assert installed.read_bytes() == expected
            # Prove the next update replaces stale installed assets.
            installed.write_bytes(b"stale icon")
        desktop.write_text("stale desktop entry")
        launcher.write_text("stale launcher")
        assert "VizGui updated" in result.stdout
    calls = log.read_text().splitlines()
    assert calls.count(f"git -C {repo} pull --ff-only") == 2
    assert calls.count("kbuildsycoca6 --noincremental") == 2


def test_failed_pull_leaves_desktop_integration_unchanged(installation):
    home, _, _, _, log, fake, run = installation
    assert run().returncode == 0
    local = home / ".local"
    before = {p: p.read_bytes() for p in local.rglob("*") if p.is_file()}
    log.write_text("")
    fake("git", 1)
    result = run()
    assert result.returncode != 0
    assert "Update failed; desktop integration unchanged" in result.stderr
    assert before == {p: p.read_bytes() for p in local.rglob("*") if p.is_file()}
    assert len(log.read_text().splitlines()) == 1


def test_update_requires_existing_checkout(installation):
    home, repo, _, _, log, _, run = installation
    (repo / ".git").rmdir()
    result = run()
    assert result.returncode != 0
    assert "No existing installation" in result.stderr
    assert not (home / ".local/bin/viz-gui").exists()
    assert not log.exists()


@pytest.mark.parametrize("cache", ["kde5", "absent", "failing"])
def test_optional_cache_refresh(installation, cache):
    _, _, _, commands, log, fake, run = installation
    (commands / "kbuildsycoca6").unlink()
    if cache == "kde5":
        fake("kbuildsycoca5")
    elif cache == "failing":
        fake("kbuildsycoca6", 1)
        fake("update-desktop-database", 1)
    else:
        (commands / "update-desktop-database").unlink()
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    if cache == "kde5":
        assert "kbuildsycoca5 --noincremental" in log.read_text()
