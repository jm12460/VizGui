# Recovery hardware checks before v0.5.0

Automated tests use fake playback devices. These checks cover receiver timing,
media delivery, and desktop/phone rendering on real hardware. They have not yet
been verified as a complete matrix. Earlier informal checks confirmed basic
multi-client control, GUI reconnection, and saved-position resumption.

Choose test media and a time when playback may be interrupted. Stopping the
server, disconnecting its network, or restarting it can stop Cast playback.
Run each action deliberately; do not automate service or network changes as a
side effect of installing/updating VizGui. Do not change pairing credentials.

Record the server/GUI commit, receiver model, desktop environment, media type,
initial saved position, observed request states, recovered position, and any
failure message for each case. Keep private media paths and tokens out of
shared reports.

| Scenario | H.264 MP4 direct | H.264 MKV remux | HEVC transcode |
| --- | --- | --- | --- |
| Resume an interrupted episode after the receiver buffer expires | Pending | Pending | Pending |
| Brief server restart while the receiver still reports playing | Pending | Pending | Pending |
| Server restart while receiver is paused | Pending | Pending | Pending |
| Temporary server/TV network interruption | Pending | Pending | Pending |
| Launch failure or unavailable media | Pending | Pending | Pending |

For each playable case:

1. Keep two connected desktops and the phone remote available. Start the media
   and let a meaningful saved position accumulate.
2. Perform the chosen outage/restart action, then restore connectivity or start
   the server deliberately. Confirm it is actually running before judging GUI
   reconnection; see [server setup](SERVER_SETUP.md).
3. If the receiver remains playing, check the known-source reattachment path.
   If it is idle, check that an interrupted-playback prompt appears and nothing
   starts until Resume is clicked. Not now and closing the prompt must do nothing.
4. Verify the request is acknowledged as accepted, is shown as starting while
   preparing, and becomes playing only after playback confirmation. Very fast
   transitions may occur between UI polls; use the matching request ID in API
   status if examining timings. All clients should converge on the same result.
5. Confirm the saved timestamp refers to the original episode timeline. Remux
   and transcode must add the input offset once, including after a second restart.
6. Verify a paused receiver is not automatically unpaused. When playback is
   explicitly resumed, check that it recovers the correct source and position.
7. Try Resume from two clients. Only one recovery launch should be accepted.
   An older prompt must not replace playback already started by another client.
8. For failures, verify the error remains visible, the command is not replayed,
   and a checkpoint remains available for a deliberate retry. A confirmation
   timeout must not launch another player or interrupt another session.

Repeat the desktop-facing checks on both KDE and Hyprland/XWayland. Recovery
prompts should stay non-modal, without a bottom-right Qt resize grip.
Uninterrupted playback across server outages is outside the current release
behavior; buffering may run out while media serving is unavailable.
