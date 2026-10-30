# VizGui — Codex project instructions

Read `docs/CODEX_HANDOFF.md`, `README.md`, `ROADMAP.md`, and `CHANGELOG.md` for context. Treat current source, Git state and tests as authoritative if snapshot notes differ.

VizGui is a Python/PySide6 desktop media caster for Chromecast/SmartCast and VLC, with an independent headless playback server, connected GUI, SMB/local media browsing, and phone web remote.

## Safety boundaries

- Never stop, restart, kill, enable/disable or modify the running headless server without **explicit user approval**. It may be serving playback.
- Never seize, terminate, or interrupt a server-owned Chromecast session just to inspect, connect, test, or close a client. Standalone observer mode is read-only for server-owned Cast.
- Never modify, delete, reset, expose or commit real user configuration, SMB credentials, remote pairing tokens/PINs, or live session state; use temporary test directories and mocks.
- Never commit, merge, tag, push, create releases, or modify remote branches without explicit user approval. Preserve unrelated uncommitted work.
- Never use destructive broad cleanup (`git clean`, `git reset --hard`, deleting checkout) without approval.

## Coding and workflow

- Favor modular architecture, clear interfaces, minimal changes, and backwards compatibility; don't introduce unnecessary frameworks or spaghetti.
- Be direct about bugs, risks, tradeoffs, and incorrect assumptions.
- Inspect `git status --short` and branch before changes. Work directly in the repo; avoid asking the user to paste large generated patch scripts.
- Run targeted tests, full `python -m pytest -q`, and `git diff --check`; for installer changes also `bash -n install.sh`.
- Test installer updates using an isolated HOME and fake commands; do not run real services or touch real user state as a side effect.
- Respect both Hyprland/Wayland and KDE desktops; prefer idempotent user-mode desktop integration.
- State what is verified versus what requires real-hardware tests. Request approval before publishing.

Scratch local files to avoid staging accidentally: `vizgui-phase-b.patch`, `vizgui_phase1_observer.py`, `vizgui_phase_b_patchgen.py`.
