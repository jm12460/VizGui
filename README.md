# vizgui

Cast local media to a Vizio SmartCast TV / Chromecast or to VLC, with a Vizio
remote, a phone web-remote (QR/PIN paired), HEVC GPU transcoding, SMB sources,
auto-play-next, and a setup wizard.

## Run
```
python -m vizgui
```
A launcher is installed at `~/.local/bin/viz-gui` (adds this dir to the path and
runs `vizgui.app:main`).

## Module layout
- `paths.py`      dirs, tool paths, logging
- `config.py`     load/save/migrate config + shared active-cast state (HOST/TOK/TV_IP/CAST_PORT/CFG)
- `smartcast.py`  Vizio SmartCast keys, status, pairing
- `chromecast.py` go-chromecast wrapper + device scan
- `vlc.py`        VLC control — HTTP (launched) + MPRIS (manual) backends
- `transcode.py`  NVENC/x264 detection + live-transcode wrapper
- `sources.py`    local folders + on-demand SMB (gio/gvfs) mounts
- `webremote.py`  phone web server + JSON API (+ `remote.html`)
- `net.py`        LAN IP / subnet detection
- `helpers.py`    time formatting, status parsing
- `qtasync.py`    run work off the UI thread, deliver back on the main thread
- `ui.py`         DPad, RemotePanel, SetupDialog/SmbDialog, MainWindow, QSS theme
- `app.py`        entry point

Data lives in `~/.config/vizgui/config.json` (chmod 600) and logs in `~/.cache/viz-gui/`.

## License
MIT — see [LICENSE](LICENSE).
