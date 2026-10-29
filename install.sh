#!/usr/bin/env bash
# vizgui installer for Linux (Arch, Debian/Ubuntu, Fedora).
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

UPDATE_ONLY=0
case "${1:-}" in
    "") ;;
    --update) UPDATE_ONLY=1 ;;
    *) echo "Usage: bash install.sh [--update]" >&2; exit 2 ;;
esac

say() { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }

if [ "$UPDATE_ONLY" -eq 0 ]; then
say "Installing dependencies (you'll be asked for your password)..."
if command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm \
        python pyside6 python-qrcode ffmpeg vlc playerctl gvfs-smb git curl
elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y \
        python3 python3-pyside6 python3-qrcode ffmpeg vlc playerctl gvfs-backends git curl
elif command -v dnf >/dev/null 2>&1; then
    # vlc and the full ffmpeg build live in RPM Fusion on Fedora; install what's in
    # the default repos and let the user add the rest if they want the VLC target.
    sudo dnf install -y \
        python3 python3-pyside6 python3-qrcode ffmpeg-free playerctl gvfs-smb git curl || true
    if ! command -v vlc >/dev/null 2>&1; then
        printf '\n\033[1;33mNote:\033[0m VLC (and the full ffmpeg build for some codecs) are in RPM Fusion.\n'
        printf '      To use the VLC target / all codecs: enable https://rpmfusion.org then\n'
        printf '      sudo dnf install vlc ffmpeg\n'
    fi
else
    echo "No supported package manager found (need pacman, apt, or dnf)." >&2
    echo "Install these manually, then re-run or use 'python -m vizgui':" >&2
    echo "  python3, PySide6, python-qrcode, ffmpeg, vlc, playerctl, gvfs (smb backend), git, curl" >&2
    exit 1
fi

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

fi

say "Getting the app..."
if [ -d "$DEST/.git" ]; then
    git -C "$DEST" pull --ff-only || {
    if [ "$UPDATE_ONLY" -eq 1 ]; then
        echo "Update failed; desktop integration unchanged." >&2
        exit 1
    fi
    echo "    (couldn't update - using the existing copy)"
}
else
    mkdir -p "$(dirname "$DEST")"
    if [ "$UPDATE_ONLY" -eq 1 ]; then
        echo "No existing installation at $DEST" >&2
        exit 1
    fi
    git clone "$REPO_URL" "$DEST"
fi

say "Installing the launcher..."
mkdir -p "$BIN"
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
Name=VizGui
Comment=Cast local media to a TV / Chromecast or VLC
Exec=$BIN/viz-gui
Icon=vizgui
Terminal=false
Categories=AudioVideo;Player;
StartupNotify=true
StartupWMClass=vizgui
DESK
say "Installing application icons..."
ICONS="$HOME/.local/share/icons/hicolor"
install -Dm644 "$DEST/packaging/vizgui-64.png" "$ICONS/64x64/apps/vizgui.png"
install -Dm644 "$DEST/packaging/vizgui-256.png" "$ICONS/256x256/apps/vizgui.png"
install -Dm644 "$DEST/vizgui/icon.png" "$ICONS/512x512/apps/vizgui.png"

update-desktop-database "$APPS" 2>/dev/null || true

if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

case ":$PATH:" in
    *":$BIN:"*) ;;
    *) printf '\n\033[1;33mNote:\033[0m add %s to your PATH to run `viz-gui` from a terminal.\n' "$BIN" ;;
esac

if [ "$UPDATE_ONLY" -eq 1 ]; then
    say "VizGui updated. Desktop launcher and icons refreshed."
else
    say "Done! Launch 'vizgui' from your app menu, or run: viz-gui"
    echo "    First run seeds your media folders and opens a wizard that finds your TV."
fi
