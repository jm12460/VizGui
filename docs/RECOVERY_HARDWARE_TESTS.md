# Recovery hardware checks before v0.5.0

Automated tests use fake playback devices. These checks cover receiver timing,
media delivery, and desktop/phone rendering on real hardware. The matrix is not
complete; observations below record individual checks rather than a full pass.

Choose test media and a time when playback may be interrupted. Stopping the
server, disconnecting its network, or restarting it can stop Cast playback.
Run each action deliberately; do not automate service or network changes as a
side effect of installing/updating VizGui. Do not change pairing credentials.

Record the server/GUI commit, receiver model, desktop environment, media type,
initial saved position, observed request states, recovered position, and any
failure message for each case. Keep private media paths and tokens out of
shared reports.

Observations from October 30–31, 2026: a Linux headless server with NVIDIA
encoding, connected desktop and phone, and a Vizio 4K TV with built-in Cast.
The exact TV model and desktop environment were not recorded. Tests span
`038d03c` and subsequent local changes, so they are not all verification of one
identical build. Only one desktop and one phone participated in these checks.

| Scenario | H.264 MP4 direct | H.264 MKV remux | HEVC transcode |
| --- | --- | --- | --- |
| Resume an interrupted episode after the receiver buffer expires | Observed | Observed | Observed |
| Brief server restart while the receiver still reports playing | Observed | Observed | Observed |
| Server restart while receiver is paused | Observed | Observed | Observed |
| TV network interruption | Observed | Observed | Observed |
| Server network interruption | Not tested | Not tested | Observed |
| Launch failure or unavailable media | Not tested | Not tested | 4K black screen observed; 1080p fallback later played |

In the expired-buffer tests, the resume point was where the server stopped, not
where the receiver finished its remaining buffer. During later TV network-loss
checks, a guarded stream restart resumed at the receiver-reported position.
The desktop and phone both showed recovery prompts; Resume and Not now each
cleared the other prompt in a separate check. The 4K-to-1080p choice was tested
again through Continue Watching and after a server restart. These observations
do not establish results for different receivers, VAAPI, or every failure mode.

A separate temporary HLS prototype played on the same TV and sought into a
previously ungenerated section; it is not integrated into VizGui. Continue the
remaining checks with the actual application playback paths.

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
