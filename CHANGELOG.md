# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

This will become **0.1.0**, the first tagged release. To cut it, move this
section to a dated `[0.1.0]` heading and push a `v0.1.0` tag.

### Added
- Cast local media to a SmartCast TV / Chromecast, or play it in VLC.
- Plays anything on the TV: H.264 mp4 served directly, H.264-in-mkv live-remuxed
  (no re-encode), HEVC live-transcoded to H.264 (hardware-accelerated where
  available, software fallback otherwise).
- On-screen SmartCast remote (power, input, volume, D-pad, menu/back/home, media
  transport) with a custom-painted clickpad.
- Phone web-remote: browse/cast/control from a phone browser, token-gated and
  paired by QR scan or PIN (per-device, revocable).
- Media sources: local folders, mounted drives, and on-demand SMB shares (no
  root, no manual mounting).
- Auto-play-next through a folder, with Prev/Next skip.
- First-run setup wizard; in-app status bar and startup health check for missing
  tools.
- Packaging: `pyproject.toml` (pip-installable, `viz-gui` entry point), an AUR
  `PKGBUILD` (`vizgui-git`), desktop entry, and app icon.
- Continuous integration (GitHub Actions) running the test suite on push/PR.

### Architecture
- Modular package with a platform-abstraction layer (`vizgui/platforms/`) and a
  playback-target contract (`vizgui/playback/`), so new OSes and playback targets
  are additive rather than rewrites.
- Pure-logic test suite (69 tests, no GUI/network/hardware).

[Unreleased]: https://github.com/jm12460/VizGui/commits/main
