#!/usr/bin/env bash
# vizgui installer for CachyOS / Arch-based systems.
# Installs dependencies, the go-chromecast binary (not in the repos), the app,
# a `viz-gui` launcher, and an app-menu entry. Safe to re-run (idempotent).
#
#   bash install.sh
#
# Override the repo URL or install location with env vars if needed:
#   VIZGUI_REPO=https://github.com/jm12460/VizGui.git VIZGUI_DEST=~/Projects/vizgui bash install.sh
set -euo pipefail

REPO_URL="${VIZGUI_REPO:-https://github.com/jm12460/VizGui.git}"
DEST="${VIZGUI_DEST:-$HOME/Projects/vizgui}"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }

if ! command -v pacman >/dev/null 2>&1; then
    echo "This installer is for Arch-based systems (pacman not found)." >&2
    exit 1
fi

say "Installing dependencies (you'll be asked for your password)..."
sudo pacman -S --needed --noconfirm \
    python pyside6 ffmpeg vlc playerctl gvfs-smb qrencode git curl

say "Setting up go-chromecast (casting to the TV)..."
mkdir -p "$BIN"
if command -v go-chromecast >/dev/null 2>&1 || [ -x "$BIN/go-chromecast" ]; then
    echo "    already installed."
else
    url="$(curl -fsSL https://api.github.com/repos/vishen/go-chromecast/releases/latest \
           | grep -oE '"browser_download_url": *"[^"]*Linux_x86_64[^"]*"' \
           | head -1 | cut -d'"' -f4 || true)"
    [ -n "$url" ] || url="https://github.com/vishen/go-chromecast/releases/download/v0.3.4/go-chromecast_Linux_x86_64.tar.gz"
    tmp="$(mktemp -d)"
    curl -fsSL "$url" -o "$tmp/gc.tar.gz"
    tar -xzf "$tmp/gc.tar.gz" -C "$tmp"
    install -Dm755 "$tmp/go-chromecast" "$BIN/go-chromecast"
    rm -rf "$tmp"
    echo "    installed to $BIN/go-chromecast"
fi

say "Getting the app..."
if [ -d "$DEST/.git" ]; then
    git -C "$DEST" pull --ff-only || echo "    (couldn't update - using the existing copy)"
else
    mkdir -p "$(dirname "$DEST")"
    git clone "$REPO_URL" "$DEST"
fi

say "Installing the launcher..."
cat > "$BIN/viz-gui" <<LAUNCH
#!/usr/bin/env python3
"""Launcher for the vizgui package."""
import os
import sys

sys.path.insert(0, os.path.expanduser("${DEST/#$HOME/~}"))
from vizgui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
LAUNCH
chmod +x "$BIN/viz-gui"

say "Adding it to your app menu..."
mkdir -p "$APPS"
cat > "$APPS/vizgui.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=vizgui
Comment=Cast local media to a TV / Chromecast or VLC
Exec=$BIN/viz-gui
Terminal=false
Categories=AudioVideo;Player;
DESK
update-desktop-database "$APPS" 2>/dev/null || true

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) printf '\n\033[1;33mNote:\033[0m add %s to your PATH to run `viz-gui` from a terminal.\n' "$BIN" ;;
esac

say "Done! Launch 'vizgui' from your app menu, or run: viz-gui"
echo "    First run opens a setup wizard - add your media source and scan for your TV."
