# Headless server setup and recovery

The headless server owns one Cast playback session. Configure a Cast target and
turn on the phone remote in the desktop Setup window before starting it.

## Generate and check the service file

Use `packaging/render_server_service.py` from a checkout. It checks that the
selected Python can resolve `vizgui.server`, then prints a systemd user unit.
It does not import the server, read pairing credentials, install anything, call
systemd, or start playback. It checks module resolution, not device connectivity
or the validity of your playback configuration.

For an `install.sh` or source-checkout installation:

```bash
cd ~/Projects/vizgui
python packaging/render_server_service.py --checkout "$PWD" > /tmp/vizgui-server.service
```

For a package installed into a virtual environment, select that environment's
Python and omit `--checkout`:

```bash
python packaging/render_server_service.py --python /absolute/path/to/venv/bin/python > /tmp/vizgui-server.service
```

The default interpreter is the Python running the renderer. You can also use
`--python` with `--checkout` when running the source with a virtual environment.
A source unit includes its absolute `WorkingDirectory`; an installed-package
unit uses the selected Python's installed modules. This avoids relying on your
terminal's directory or `PYTHONPATH`. Spaces and literal `%` signs in paths are
escaped for systemd. Keep the source checkout at that location after installing
the service.

If generation reports an error, fix the path or Python environment before
continuing. Review and validate the generated file:

```bash
cat /tmp/vizgui-server.service
systemd-analyze --user verify /tmp/vizgui-server.service
```

The hand-editable `packaging/vizgui-server.service` template is intended for an
installed Python package. Copying it unchanged for a source-only installation
can produce `ModuleNotFoundError: No module named 'vizgui'`.

## Install or replace the service deliberately

These are manual steps, separate from the app installer/updater. Installing a
unit changes future service startup; restarting the server can interrupt Cast
playback. Wait until playback can be interrupted before applying a replacement.
Existing service customizations should be reviewed and retained as appropriate.

To install a unit for the first time:

```bash
install -Dm644 /tmp/vizgui-server.service ~/.config/systemd/user/vizgui-server.service
systemctl --user daemon-reload
systemctl --user start vizgui-server.service
systemctl --user status vizgui-server.service --no-pager
```

To start it automatically with your user session, run
`systemctl --user enable vizgui-server.service` separately. This does not enable
boot-time operation without a login; configuring user lingering is separate.

For an existing service, inspect its unit and drop-ins first with
`systemctl --user cat vizgui-server.service`. Back up customizations before
replacing the unit. Existing drop-ins may override the generated working
directory or command. After deliberately installing the replacement and
reloading systemd, restart it only when you are ready to interrupt playback:

```bash
systemctl --user restart vizgui-server.service
systemctl --user status vizgui-server.service --no-pager
```

A successful `start`/`restart` command alone does not prove that Python finished
starting the server. Check its status and, if needed, recent logs:

```bash
journalctl --user -u vizgui-server.service -n 30 --no-pager
```

Logs can contain media paths and device details; inspect them locally before
sharing. `--no-print-pin` suppresses the phone pairing PIN in startup output.
Neither `install.sh` nor `install.sh --update` installs, enables, reloads, or
restarts the headless service. Updating source only affects the server the next
time you deliberately start or restart it.

## Managing server sources

A connected desktop running on the server computer as the same Linux user can
open **Sources** to add an absolute server folder or SMB share, or remove a
source. Changes apply to the running server and appear in connected clients
without a restart. Removing a source does not delete its files, unmount a share,
or stop current playback. A folder path is interpreted and checked on the
server; it is not a path on a separate desktop.

Source administration uses a private Unix socket in the user's runtime
directory, verifies the connecting OS user and current server instance, and
rejects stale source-list revisions. The phone playback token cannot authorize
these changes, and stored SMB credentials and unknown configuration fields are
not returned in source lists. New SMB credentials enter over the local socket;
the share is mounted on demand when browsing, not when it is added. Source
management is currently unavailable from a remote machine or a different OS
user; playback controls and browsing remain available as before. Use a local
same-user desktop to manage sources.

If the server configuration was changed externally while it was running, the
server refuses to overwrite those changes. Review the external changes and
restart the server deliberately when playback can be interrupted. Configuration
save failures leave the existing file and in-memory sources unchanged. An older
server must be deliberately restarted onto the updated code before source
management becomes available; installing or updating VizGui never does this.

## What recovery does today

- Closing a connected GUI leaves the server and playback running. A GUI network
  outage preserves its last view, blocks commands while disconnected, and
  retries automatically. This does not restart the server or media stream.
- Stopping the server also removes its media-serving process. The TV may keep
  playing buffered data briefly, then stop. Seamless playback through a server
  outage is not currently provided.
- On startup, if the receiver still reports a playing source that VizGui can
  identify from its saved state, the server attempts one reattachment. Direct
  Cast uses the receiver's absolute position; remux/transcode adds the saved
  input offset to the receiver's stream-relative position.
- A paused receiver is not automatically unpaused. Reattachment is deferred
  until it reports playing again.
- If the receiver has gone idle, the server preserves the known interrupted
  source and its last recorded position in `interrupted_playback.json` beside
  the other playback state. Connected desktops offer **Resume interrupted
  playback** once for that checkpoint. **Not now** or closing the prompt leaves
  playback alone; the normal Continue Watching list remains available.
- Clicking Resume sends a checkpoint ID and expected target, not a client-chosen
  file or timestamp. The server rechecks that the receiver is idle, the target
  is unchanged, and the file still belongs to a configured source. Two clients
  cannot launch the same recovery simultaneously. Failed requests are never
  replayed automatically. A failed launch retains the checkpoint for manual
  retry; confirmed Cast playback, Stop, or removing its server history clears it.
- A TV status failure is treated as unknown, not confirmed idle, so it cannot
  itself trigger the recovery prompt. A checkpoint is created only from the
  saved current-session identity and its unfinished history entry, never by
  choosing an arbitrary episode from history. The checkpoint survives repeated
  server restarts until resumed or explicitly cleared.
- Missing files and live streams without a saved source identity are not
  guessed. A failed reattachment reports an error rather than repeatedly
  launching playback. Saved history remains available for a manual retry.

Automated recovery tests use temporary history files, fake Cast processes, and
no TV or live service. Receiver timing, codecs, SMB availability, and recovery
across real server outages still require hardware validation.

## Playback request feedback

Playback commands and interrupted-session recovery return `ok`, `request_id`,
and `state: "accepted"` when queued successfully. Acceptance does not mean the
TV has started playing. The status API exposes the latest launch under
`playback_request`, with its ID, title, state, and any error:

| State | Meaning |
| --- | --- |
| `accepted` | The command reply acknowledges the request. |
| `starting` | Launch is queued/running, or awaiting playback confirmation. |
| `playing` | A fresh receiver/player status confirmed playback after launch. |
| `failed` | Availability/launch failed, a pending launch was cancelled, or playback was not confirmed in time. |

Desktop and phone interfaces display starting and failure details. Failure
information persists through later polls until another request replaces it.
The last request's result is separate from current transport state: pausing or
ending a successfully launched episode does not retroactively fail its launch.

After a launcher reports success, VizGui allows 45 seconds for playback
confirmation. A timeout reports `error_code: "confirmation_timeout"`; it never
retries the command or restarts playback automatically. If that same request is
subsequently confirmed playing, status is updated to reflect the actual result.
Recovery checkpoints are cleared only after playback is confirmed, not merely
because a launcher process is alive. Superseded launch callbacks and status
responses from before launch completion cannot overwrite the current result.

Older clients can continue checking `ok`; older servers without
`playback_request` remain supported by updated desktop and phone renderers.
See [the hardware recovery checklist](RECOVERY_HARDWARE_TESTS.md) for validation
that still needs a real TV and desktop/phone clients.
