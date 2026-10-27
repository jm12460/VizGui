# vizgui

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
- **SmartCast remote:** power, input, volume, D-pad, menu/back/home + media
  transport, all in one panel. Pairs with a Vizio-style SmartCast TV.
- **Phone remote:** open a page on your phone's browser to browse/cast/control —
  token-gated, paired by QR scan or a PIN (per-device, revocable).
- **Media sources:** local folders, mounted drives, or SMB shares mounted on
  demand (no root, no manual mounting).
- **Auto-play-next** through a folder, with Prev/Next skip.
- **Setup wizard:** add sources, scan for Cast devices, pair a SmartCast TV,
  configure the phone remote — no config files to edit by hand.

## Install (CachyOS / Arch)

One command installs the dependencies, the go-chromecast binary, the app, a
`viz-gui` launcher, and an app-menu entry:

```
bash <(curl -fsSL https://raw.githubusercontent.com/jm12460/VizGui/main/install.sh)
```

Or clone and run the script:

```
git clone https://github.com/jm12460/VizGui.git ~/Projects/vizgui
bash ~/Projects/vizgui/install.sh
```

Then launch **vizgui** from your app menu (or run `viz-gui`). First run opens the
setup wizard — add a media source and scan for your TV.

### Dependencies (other distros)
`python`, `PySide6`, `ffmpeg`, `vlc`, `playerctl`, `gvfs`/`gvfs-smb` (for SMB),
`qrencode` (optional, for the pairing QR), plus the
[`go-chromecast`](https://github.com/vishen/go-chromecast) binary on your `PATH`.
Then `python -m vizgui` from the source tree. Missing tools are reported in-app.

## Platform support
Linux only for now. OS-specific behavior (data paths, SMB mounting, process
launching, subnet/firewall, hardware encoder) lives behind `vizgui/platforms/`,
so macOS/Windows support is a matter of adding `macos.py` / `windows.py` that
implement the same contract — the rest of the app is OS-agnostic.

## Module layout
```
vizgui/
  app.py            entry point
  config.py         load/save/migrate config + shared active-cast state
  paths.py          dirs, tool paths, logging
  health.py         startup "which required tools are missing" check
  platforms/        OS integration (base.py contract + linux.py)
  smartcast.py      SmartCast TV keys, status, pairing
  chromecast.py     go-chromecast wrapper + device scan
  vlc.py            VLC control — HTTP (launched) + MPRIS (manual) backends
  transcode.py      live-transcode/remux wrapper (encoder from the platform layer)
  sources.py        local folders + on-demand SMB mounts
  webremote.py      phone web server + JSON API (+ remote.html)
  net.py            LAN IP / subnet detection
  helpers.py        time formatting, status parsing, cast planning
  qtasync.py        run work off the UI thread, deliver back on the main thread
  ui/               Qt GUI: theme, dpad, remote, dialogs, mainwindow
```
Settings live in `~/.config/vizgui/config.json` (chmod 600); logs in `~/.cache/viz-gui/`.

## Tests
```
pytest            # requires pytest (e.g. `sudo pacman -S python-pytest`)
```
Pure-logic unit tests in `tests/` (no GUI/network/hardware): status & cast
planning, config, VLC/MPRIS/Chromecast parsing, the transcode wrapper, web
browsing, subnet detection, the platform layer, and the health check.

## Roadmap
Headed toward easy, out-of-the-box install on Linux (AUR/Flatpak) and, later,
Windows and macOS. See [ROADMAP.md](ROADMAP.md).

## License
MIT — see [LICENSE](LICENSE).
