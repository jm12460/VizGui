# VizGui — ChatGPT to Codex development handoff

**Snapshot: 2026-10-29.** Check the actual repository and user instructions first; some noted local changes may have since been committed. This is a curated technical handoff, not an import of the full ChatGPT transcript.

## Project and environment

- GitHub `jm12460/VizGui`: https://github.com/jm12460/VizGui
- Local project checkout normally at `~/Projects/vizgui` on CachyOS/Arch, Hyprland Wayland. A second tester (user's wife) runs KDE and installed with `install.sh` at the same relative path.
- Python + PySide6 desktop caster, Chromecast/SmartCast control, VLC, local/SMB folders, audio/subtitle/chapter support, remux/transcode, continue-watching, mobile phone remote.
- Independent persistent headless server (`python -m vizgui.server`) owns playback; desktop GUI can connect to it. Standalone/observer mode must not seize or stop server-owned Chromecast playback when opened/closed. No support yet for multiple independent simultaneous playback sessions.
- Architecture: `vizgui/app.py`, `vizgui/appearance.py`, `vizgui/ui/`, `vizgui/playback/` engine and targets, `vizgui/platforms/` OS backends, `vizgui/server_client.py`, `vizgui/remote_controller.py`, `vizgui/config.py`, `install.sh`, `packaging/`, tests. Prefer modular changes and preserve playback ownership.

## Published versions and core decisions

- v0.4.0 released: headless engine/server, connected GUI, safe standalone observer, server-backed media browser, config safety.
- v0.4.1 released and tagged on GitHub, with `vizgui.__version__ = '0.4.1'`. Includes Qt appearance settings (system, 100%, 125%, 150%, 175%, 200%; auto/Wayland/XCB) and app launcher/icon branding. v0.4.1 had 226 passing automated tests.
- Qt 6.12 on Hyprland Wayland mispositions/makes combobox popups transparent at increased `QT_SCALE_FACTOR`; running XCB/XWayland avoids the problem. Apply backend/scale before `QApplication`. Saved backend must override inherited `QT_QPA_PLATFORM=wayland;xcb`; optional `VIZGUI_QPA_PLATFORM` debugging override is supported. Appearance settings exist in connected mode, remain local, do not change server config. Inline restart notice avoids KDE/Hyprland modal cursor trapping. Qt resize-grip artifact removed.
- Branded PNGs in `packaging/vizgui-64.png`, `packaging/vizgui-256.png`, and `vizgui/icon.png` (512). Desktop launcher `vizgui.desktop` uses `Icon=vizgui` / `StartupWMClass=vizgui`; Qt app sets desktop file name, app name, display name, and icon. Python wheel package-data includes PNG. KDE taskbar and app-menu integration worked after manual repair.
- `README.md`, `CHANGELOG.md`, `ROADMAP.md` on GitHub were updated for v0.4.1; roadmap release-tag checklist and packaging notes were additionally being improved in a local edit.
- CI on `main` / PR uses Python 3.10/3.12, and version tags trigger GitHub Release sdist/wheel. Stable `packaging/vizgui/PKGBUILD` targets v0.4.1; rolling `vizgui-git` recipe exists. AUR publication pending; stable PKGBUILD checksum still `SKIP` until manually pinned.

## Current/very recent bug: upgrading existing installs

Wife's KDE installation had correct updated source but old app-menu and taskbar icon. Root cause: `git pull` alone updates source but does not reinstall the desktop entry/icons under `~/.local/share`. Manual installing `~/.local/share/applications/vizgui.desktop` plus hicolor PNGs and KDE cache refresh (`kbuildsycoca6 --noincremental`) fixed it.

A lightweight `bash install.sh --update` was added locally to run `git pull --ff-only`, refresh `~/.local/bin/viz-gui`, desktop entry and all PNG icon sizes, update desktop database, and refresh KDE cache if available, **without sudo, reinstalling OS dependencies, or restarting playback**. README updated to distinguish update command from git pull.

A new test `tests/test_installer_update.py` uses temp HOME and fake git/sudo/KDE tools. First run showed `226 passed, 1 failed`: update path lacked `mkdir -p "$BIN"`, so creating the launcher failed with a clean HOME. A subsequent proposed patch added `mkdir -p "$BIN"` directly before `cat > "$BIN/viz-gui" <<LAUNCH`. User did not post rerun results yet; **verify current source and rerun tests**. Expected total after fix is 227, not yet confirmed.

Most recent observed local Git status (may have changed): modified `README.md`, `ROADMAP.md`, `install.sh`, untracked `tests/test_installer_update.py`, and three old scratch artifacts `vizgui-phase-b.patch`, `vizgui_phase1_observer.py`, `vizgui_phase_b_patchgen.py`. **Do not overwrite or stage unrelated work**. User has been given potential separate commits for installer and roadmap changes, but has not confirmed they were pushed.

## Testing and release discipline

- Never restart/stop the headless server, disrupt active Cast playback, or modify real config/SMB/phone pairing/credentials to test or inspect.
- Never commit, push, merge, tag, or publish without explicit user approval.
- For installer test: isolate HOME / checkout and stub git, sudo, KDE tools; test clean directory case, idempotence, failure handling, updated desktop metadata, icon sizes, no sudo. Do not invoke the real installer during automated tests.
- `python -m pytest -q`; `bash -n install.sh`; `git diff --check`; review diff and git status.
- Do not move or retag already released v0.4.1 for follow-on documentation/installer changes. Add such fixes to a later release when approved.

## Likely next work

First inspect existing local updater changes, verify failure-path safety and tests, then report results without pushing. v0.5.0 is only a proposed direction: server reconnect/recovery, multi-client coordination around **one** server-owned session, ownership/lifecycle robustness, stronger integration tests and KDE/Hyprland validation. Scope to be decided with user.
