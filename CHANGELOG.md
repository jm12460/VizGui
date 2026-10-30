# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Connected desktops on the server computer can add local and SMB sources or
  remove sources through a same-user Linux Unix socket without restarting playback.
  Phone tokens cannot administer sources, saved SMB credentials are not returned,
  and stale or externally changed configurations are rejected.
- Playback request IDs and explicit accepted/starting/playing/failed feedback in
  the API, desktop, and phone remote. Playing requires fresh receiver confirmation;
  launch errors persist and confirmation timeouts never retry commands.
- A real-hardware recovery checklist for direct Cast, remux, transcode, and paused
  receivers before v0.5.0.
- Connected desktops offer explicit recovery of known interrupted Cast playback
  after server outages, with persisted checkpoints and stale-request protection
  across multiple clients. Dismissing the non-modal prompt never starts playback.
- A service-file renderer validates Python/module paths for source-checkout and
  installed-package setups without installing or controlling a service.
- Server setup/recovery instructions and isolated restart-recovery tests for
  direct Cast, remux, and transcode, including pause, failure, and shutdown safety.
- Isolated multi-client integration tests for two desktops and the phone API
  sharing one headless playback session, including concurrent commands,
  disconnect/reconnect behavior, and client shutdown safety.

### Fixed
- Configuration saves create private, uniquely named temporary files before
  atomically replacing the configuration, protecting SMB credentials during saves.
- Superseded launch callbacks, queued launches after cancellation, and stale
  status responses cannot overwrite the latest playback request result.
- Interrupted recovery checkpoints are retained until playback is confirmed.
- Failed Cast status requests no longer count as confirmed idle playback.
- Failed startup reattachment preserves a checkpoint for explicit manual recovery.
- Direct Cast resumes now include the saved position in the initial load
  request, avoiding a follow-up seek that could fail before media initialization
  and silently start playback at the beginning.
- Connected desktops clear stale browsing/search selections when the server's
  source list changes, including removal of the last source. Late responses
  from the previous source cannot restore obsolete results.
- Resuming an episode in VLC no longer applies its saved timestamp to later
  playlist entries. Queued episodes start at zero, including when reusing a VLC
  instance launched with the old global resume option.
- Disabled the bottom-right Qt resize grip across all shared popup dialogs,
  including after showing them, matching the Appearance window fix.
- Setup, pairing, resume, confirmation, input, and error dialogs now use
  non-modal callbacks to avoid XWayland cursor trapping. Track/chapter menus
  no longer start nested event loops. Cancelling Resume leaves playback alone,
  and closing Setup cancels its pending prompts without applying their results.
- Continue Watching opens non-modally in connected and standalone mode to avoid
  X11/XWayland cursor trapping, matching the Appearance window behavior.
- Connected desktops verify server installation identity and detect process
  restarts from fresh status responses. Updated servers reject commands intended
  for a previous process; a different installation is not trusted automatically.
- Connected desktops retain the last playback display during temporary server
  outages and retry with a delay capped at 30 seconds.
- Remote commands are blocked while disconnected and queued commands are
  discarded after connection changes. Late callbacks from a closed controller
  cannot update a reopened connection; commands are never automatically retried.

## [0.4.1] - 2026-10-29

### Added
- **Desktop appearance preferences** — persistent interface scaling from
  100–200% or system default, with automatic, Wayland, and XWayland backends.
- **Connected-mode Appearance settings** — change local GUI preferences
  without modifying server-owned media settings.
- **Application icon** — new TV/play/casting artwork for Qt windows,
  application menus, and Linux desktop launchers.
- Appearance and dialog regression tests.

### Fixed
- Saved XWayland preferences now take precedence over inherited Qt platform
  lists such as `wayland;xcb`.
- Replaced the modal restart notification with an inline warning.
- Made connected-mode Appearance non-modal to avoid cursor trapping.
- Removed the unwanted Qt resize grip from the Appearance dialog.

### Compatibility
- On some Qt/Wayland setups, application scaling can misplace dropdown
  menus. XWayland is available as a compatibility option.
- Headless server and playback behavior remain unchanged.

## [0.4.0] - 2026-10-29

### Added
- **Safe standalone observer mode** — displays server-owned Chromecast
  playback without taking ownership or interrupting playback when closing.
- Regression tests for observer-mode playback ownership and shutdown safety.
- **Headless server mode** — run VizGui's playback engine without launching
  the desktop GUI, including as a persistent systemd user service.
- **Connected desktop mode** — the full desktop GUI can connect to an existing
  VizGui server and control playback remotely.
- **Server-backed media browsing** — navigate configured media sources through
  the connected GUI, including nested folders, search, and Continue Watching.
- **Startup mode selection** — when a server is detected, choose Connect to
  Server, Run Standalone, or Quit.
- Automated regression tests covering startup modes and safe handling of
  invalid configuration in both desktop and headless-server startup.

### Changed
- **Playback architecture** — extracted a Qt-independent engine shared by
  standalone desktop operation and headless server operation.
- Polished the desktop file browser with themed folder/video icons, uniform
  selection, roomier rows, hover feedback, and single-click folder expansion.
- **Configuration loading** — existing configuration values and unknown fields
  are preserved while missing supported defaults are filled in.

### Fixed
- **Configuration safety** — invalid JSON, unreadable configurations, and
  invalid configuration structures now stop startup without replacing the
  original configuration.
- Desktop startup displays a configuration error dialog instead of silently
  resetting settings.
- Headless-server startup reports configuration errors clearly without a
  Python traceback.

### Compatibility
- Standalone desktop mode remains supported; running a headless server is
  optional.
- Existing configurations do not require a manual migration for this release.
- Multiple independent simultaneous playback sessions are not yet supported.

## [0.3.0] - 2026-10-28

### Added
- **Media search** — a search box in both the desktop browser and the phone
  Browse tab finds videos anywhere under a source, no clicking through folders.
  Words match independently (so "family guy pilot" narrows), and a matching
  folder is returned as one entry you open rather than listing every file inside
  it.

### Fixed
- VLC now applies the chosen audio/subtitle track when reusing an already-open
  VLC, not only on a fresh launch.
- Sidecar-subtitle matching no longer grabs a similarly-named file (e.g.
  `Aliens.srt` was picked up for `Alien.mkv`).
- A failed subtitle conversion no longer leaves a stale subtitle to be burned in.
- **Security:** the phone remote can only cast files inside a configured source;
  a paired device can no longer request arbitrary local paths.
- A corrupted/unreadable `config.json` is backed up (to `config.json.corrupt`)
  instead of being silently overwritten — preserving SMB passwords and phone
  pairings. Only a genuinely missing config starts the first-run wizard.
- The phone status snapshot no longer reads a Qt widget off the HTTP thread.
- Continue-watching now records against the right file when VLC advances to the
  next episode (via the app's Next or VLC's own controls); the app reads the
  current item's path from VLC's playlist instead of only its filename.

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

[Unreleased]: https://github.com/jm12460/VizGui/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/jm12460/VizGui/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/jm12460/VizGui/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/jm12460/VizGui/releases/tag/v0.3.0
[0.2.0]: https://github.com/jm12460/VizGui/releases/tag/v0.2.0
[0.1.0]: https://github.com/jm12460/VizGui/releases/tag/v0.1.0
