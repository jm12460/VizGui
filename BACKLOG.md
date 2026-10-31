# VizGui backlog

A lightweight place to capture bugs, friction, and ideas from real use. The
[roadmap](ROADMAP.md) tracks larger milestones. Add a short note when something
feels wrong; details can be filled in later. Priorities and reproduction details
are optional. Move items to Resolved after a fix has been verified, or mark them
deferred if they are not worth pursuing.

## Bugs

- **Continue Watching entry appeared late** — Investigate. An episode with a
  saved resume position was missing from a freshly opened desktop list, then
  appeared later. Reproduction and cause are not established. Next: capture
  whether the list changes while open, the current playback state, and timing.
- **Direct 4K Cast showed a black screen** — Partially mitigated. HDR tone mapping
  now plays successfully on the tested Vizio 4K receiver, and its 1080p fallback
  works. Direct 4K H.264 and HEVC samples previously failed to show picture.
  Next: investigate receiver codec/profile limits for direct playback; do not
  assume every 4K TV accepts the same formats.

## Friction / UX

- **First 4K compatibility fallback is slow** — Observed. The receiver can wait
  through the confirmation timeout before retrying at 1080p. Later playback of
  the same unchanged file on the same target can use the remembered choice.
  Next: investigate safe capability detection and ways to shorten the first wait.
- **Live transcode seeks reload** — Observed. A 30-second jump took about five
  seconds on the latest test and reloaded the video. A temporary HLS prototype
  sought into a previously
  ungenerated section and felt faster; it is not a VizGui playback path. Next:
  test backward/repeated seeks, audio sync, subtitles, and interruption handling
  before considering integration.
- **Prepared-copy startup and storage** — Investigate. Seekable copies improve
  compatible H.264 seeking, but preparation can delay playback and consume disk.
  One roughly 330 MB episode prepared in about 1.25 seconds; larger and slower
  sources have not been measured. Next: measure time to first picture and disk
  use across representative files and decide whether a user choice is needed.
- **Remembered compatibility has no reset control** — Improvement. A learned
  choice is per target and file; older targets without UUIDs use address and port.
  An address change may lose the choice, and a receiver update may make it stale.
  Next: design a simple way to inspect or clear learned compatibility.

## Technical Debt

- **Verify encoder choices on real hardware** — Pending. NVENC worked on the
  test machine; AMD/Intel VAAPI and real subtitle-burn paths remain unverified.
  Next: test HEVC and subtitle casting on an AMD/Intel machine, including the
  software fallback. A successful small probe cannot guarantee every video works.
- **Confirm the requested Cast session** — Partially addressed. Verifiably
  unrelated sender URLs no longer confirm a launch, and 1080p fallback requires
  positive ownership. Some platforms or receiver responses cannot be verified,
  so ordinary launches can still lack definitive identity. Next: test missing
  identifiers and multi-client takeovers without interrupting another session.
- **Document release checks** — Ongoing. The roadmap's test snapshot and the
  hardware checklist can drift from the current checkout. Next: refresh verified
  results and the changelog when preparing a release.

## Ideas

- **Investigate device capabilities before retrying** — Idea. A TV's screen
  resolution alone does not establish which Cast codecs and profiles it accepts.
  Next: research a safe device-capability check; keep the existing guarded retry
  when capabilities are uncertain.
- **Separate playback state from command failures** — Investigate. A failed
  request may compete with the now-playing display even if another item remains
  active. Next: reproduce with a fake receiver, then consider a separate result
  notification if both facts can occur together.

## Resolved

- **Shared recovery prompts** — Verified on desktop and phone: Resume or Not now
  cleared the other prompt; Not now left the TV idle.
- **Receiver outage clock** — Verified on the test TV: the desktop indicated an
  unreachable receiver, froze its clock, and updated after reconnection.
- **Slow jumps on a compatible H.264 MKV** — Verified on the test TV: a prepared
  seekable copy reduced the observed jump from roughly 11–13 seconds to about 2.
