# vizgui

[![CI](https://github.com/jm12460/VizGui/actions/workflows/ci.yml/badge.svg)](https://github.com/jm12460/VizGui/actions/workflows/ci.yml)

Cast local media to a SmartCast TV / Chromecast, or play it in VLC — with an
on-screen remote, a phone web-remote, SMB network sources, auto-play-next, and a
first-run setup wizard. A single-user desktop app (PySide6/Qt).

## Features
- **Two playback targets:** cast to a Chromecast / SmartCast TV, or play on this
  PC in VLC (reuses a running VLC; controlled over its HTTP interface, or MPRIS
  for a VLC you opened yourself).
- **Plays anything on the TV:** H.264 mp4 is served directly; H.264 in mkv/etc. is
  live-**remuxed** (no re-encode); HEVC/x265 is **live-transcoded** to H.264
  (hardware-accelerated where available, software fallback otherwise).
- **SmartCast remote:** power, input, a volume rocker, D-pad, menu/back/home +
  media transport, all in one panel. Pairs with a SmartCast TV.
- **Phone remote:** a mobile web page with a persistent now-playing/transport bar
  over Remote and Browse tabs, plus a settings sheet (auto-next, Cast rescan,
  source). Token-gated, paired by QR scan or a PIN (per-device, revocable).
- **Media sources:** local folders, mounted drives, or SMB shares mounted on
  demand (no root, no manual mounting).
- **Auto-play-next** through a folder, with Prev/Next skip.
- **Setup wizard:** tabbed (Sources / Cast & VLC / Phone Remote). Auto-discovers
  your TV, seeds your media folders on first run, and shows the firewall command
  matching your firewall — no config files to edit by hand.

## Install (Linux)

Works on **Arch, Debian/Ubuntu, and Fedora** — the installer detects
`pacman` / `apt` / `dnf`. One command installs the dependencies, the
go-chromecast binary, the app, a `viz-gui` launcher, and an app-menu entry:

```
bash <(curl -fsSL https://raw.githubusercontent.com/jm12460/VizGui/main/install.sh)
```

Or clone and run the script:

```
git clone https://github.com/jm12460/VizGui.git ~/Projects/vizgui
bash ~/Projects/vizgui/install.sh
```

Then launch **vizgui** from your app menu (or run `viz-gui`). First run seeds your
media folders and opens the setup wizard, which auto-discovers your TV.

On **Fedora**, VLC and the full ffmpeg build live in [RPM Fusion](https://rpmfusion.org/)
(the installer notes this); enable it and `sudo dnf install vlc ffmpeg` for the VLC
target and all codecs.

### Other distros / manual
Install `python3`, `PySide6`, `ffmpeg`, `vlc`, `playerctl`, `gvfs` (SMB backend),
`qrencode` (optional, for the pairing QR), plus the
[`go-chromecast`](https://github.com/vishen/go-chromecast) binary on your `PATH`.
Then `pipx install git+https://github.com/jm12460/VizGui.git`, or run
`python -m vizgui` from a clone. Missing tools are reported in-app.

## Platform support
**Linux** is fully supported. OS-specific behavior (data paths, SMB mounting,
process launching, subnet/firewall, hardware encoder, VLC location) lives behind
`vizgui/platforms/` behind a single contract, with `linux.py`, `windows.py`, and
`macos.py` backends. **Windows and macOS backends exist but are experimental and
untested on real hardware** — macOS should be close to complete; on Windows,
casting of H.264 mp4 works, but live remux/transcode (bash wrapper) and the
pairing QR (qrencode) still need Windows-native equivalents.

## Module layout
```
vizgui/
  app.py            entry point
  config.py         load/save/migrate config + shared active-cast state
  paths.py          dirs, tool paths, logging
  health.py         startup "which required tools are missing" check
  platforms/        OS integration (base.py contract + linux.py)
  playback/         playback-target contract + Cast / VLC adapters + media helpers
  smartcast.py      SmartCast TV keys, status, pairing
  chromecast.py     go-chromecast wrapper + device scan
  vlc.py            VLC control — HTTP (launched) + MPRIS (manual) backends
  transcode.py      live-transcode/remux wrapper (encoder from the platform layer)
  sources.py        local folders + on-demand SMB mounts
  webremote.py      phone web server + JSON API (+ remote.html)
  net.py            LAN IP / subnet detection
  helpers.py        time formatting, status parsing, cast planning, discovery
  qtasync.py        run work off the UI thread, deliver back on the main thread
  ui/               Qt GUI: theme, dpad, remote, dialogs, mainwindow
```
Settings live in `~/.config/vizgui/config.json` (chmod 600); logs in `~/.cache/viz-gui/`.

## Tests
```
pytest            # requires pytest (e.g. `sudo pacman -S python-pytest`)
```
Pure-logic unit tests in `tests/` (no GUI/network/hardware): status & cast
planning, Cast-device discovery, config, the playback targets & media helpers,
VLC/MPRIS/Chromecast parsing, the transcode wrapper, web browsing, subnet
detection, the platform layer, and the health check.

## Roadmap & releases
Headed toward easy, out-of-the-box install on Linux (AUR/Flatpak) and, later,
Windows and macOS. See [ROADMAP.md](ROADMAP.md) for the plan and
[CHANGELOG.md](CHANGELOG.md) for what's shipped.

## License
MIT — see [LICENSE](LICENSE).
