# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-27

### Added
- **Continue watching** — resumes where you stopped, per file, locally; a
  header button (desktop) / overlay (phone) lists recent items with progress.
  Resume works across direct cast, remux, live transcode, and VLC.
- **Audio track selection** — pick a track (desktop menu / phone overlay);
  Cast re-maps it through the wrapper, VLC uses `--audio-track`.
- **Subtitles** — embedded and sidecar `.srt`/`.ass`; burned in on Cast (text
  via `-vf subtitles`, image/PGS via `overlay`), native on VLC.
- **Chapters** — jump to a chapter from a menu (desktop) / overlay (phone).
- **Cast-session restore** — reconnect to and reattach an active cast after the
  app restarts (persists the current source; never guesses a transcode episode).
- **Windows and macOS platform backends** (experimental, untested on hardware).
- `install.sh` now supports Debian/Ubuntu (apt) and Fedora (dnf), not just Arch.

### Changed
- Pairing QR uses the pure-Python `qrcode` lib (painted with Qt) instead of the
  `qrencode` binary — one fewer external dependency, works cross-platform.

### Fixed
- **Security:** the phone remote built UI from filenames/device names with
  `innerHTML`; a crafted name could run script in a paired browser. All dynamic
  UI is now built with DOM APIs (no HTML injection).
- Setup → Save no longer drops the phone-remote token/PIN or the auto-next
  setting (which unpaired phones and reset auto-next on reload).
- 10-bit HEVC now transcodes to H.264 for Chromecast (CPU-decode + 8-bit).
- Switching audio/subs on a live cast no longer races the old session
  ("context deadline exceeded"); waits for the receiver to go idle first.
- A paused Cast session going idle no longer triggers false auto-next.

## [0.1.0] - 2026-10-27

First tagged release.

### Added
- Cast local media to a SmartCast TV / Chromecast, or play it in VLC.
- Plays anything on the TV: H.264 mp4 served directly, H.264-in-mkv live-remuxed
  (no re-encode), HEVC live-transcoded to H.264 (hardware-accelerated where
  available, software fallback otherwise).
- On-screen SmartCast remote (power, input, volume rocker, D-pad, menu/back/home,
  media transport) with a custom-painted clickpad.
- Phone web-remote: a persistent now-playing/transport bar over Remote and Browse
  tabs, plus a settings sheet (auto-next, Cast rescan/switch, source picker);
  token-gated and paired by QR scan or PIN (per-device, revocable).
- Media sources: local folders, mounted drives, and on-demand SMB shares (no
  root, no manual mounting).
- Auto-play-next through a folder, with Prev/Next skip.
- Setup wizard (tabbed: Sources / Cast & VLC / Phone Remote) with background
  Cast auto-discovery — a single found TV is added automatically; several are
  listed to pick from.
- Zero-config first run: seeds the user's Videos/Music/Downloads as sources and
  opens the wizard automatically.
- Firewall-aware phone-remote setup: shows the allow command matching the running
  firewall (ufw/firewalld), or says none is needed.
- In-app status bar and startup health check for missing tools; network/IO paths
  time out and report clearly instead of hanging.
- Packaging: `pyproject.toml` (pip-installable, `viz-gui` entry point), an AUR
  `PKGBUILD` (`vizgui-git`), desktop entry, and app icon.
- Continuous integration (GitHub Actions) running the test suite on push/PR.

### Architecture
- Modular package with a platform-abstraction layer (`vizgui/platforms/`) and a
  playback-target contract (`vizgui/playback/`), so new OSes and playback targets
  are additive rather than rewrites.
- Pure-logic test suite (85 tests, no GUI/network/hardware).

[Unreleased]: https://github.com/jm12460/VizGui/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jm12460/VizGui/releases/tag/v0.2.0
[0.1.0]: https://github.com/jm12460/VizGui/releases/tag/v0.1.0
