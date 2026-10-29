# Roadmap

Where vizgui is headed: from a working single-user Linux app to something
**anyone can download and use, cross-platform, with ease-of-use and
functionality out of the box.**

Current status: **v0.4.1** · Linux daily-driven, with an optional headless
server, connected GUI, and desktop appearance scaling · 226 passing tests.
Windows/macOS backends are still untested on hardware. `install.sh` supports
pacman/apt/dnf; AUR recipes are ready, but publication remains blocked.

Phases ship in order; each one is usable on its own. Linux first (that's what
works today), then Windows and macOS.

---

## Already done (the foundation)

These are the architectural pieces the rest of the roadmap builds on — worth
noting because they make the cross-platform work *additive* instead of a rewrite:

- [x] Modular package (`config` / backends / `sources` / `webremote` / `ui`)
- [x] **Platform abstraction** (`vizgui/platforms/` — a contract + `linux.py`);
      new OSes are a new backend file, not surgery on the app
- [x] **Playback-engine extraction** (`vizgui/playback/` — `PlaybackTarget`
      contract + `CastTarget`/`VlcTarget` adapters); a new target (e.g. DLNA) is
      a self-contained file
- [x] Automated test suite (226 tests, including offscreen Qt checks;
      real hardware testing is still needed)
- [x] Reliability pass (status bar, startup health check, graceful
      tool-missing handling)
- [x] MIT licensed, generic branding, public repo

---

## Shipped since v0.1.0 (v0.2.0 – v0.4.1)

Feature and hardening work beyond the original roadmap — captured here so the
plan reflects reality:

- [x] **Continue watching** — per-file resume across cast/remux/transcode/VLC
      (desktop button + phone overlay).
- [x] **Audio-track / subtitle / chapter selection** — embedded + sidecar subs;
      burned in on Cast (text + image/PGS), native on VLC; chapter jumps.
- [x] **Cast-session restore** — reattach to an active cast after an app restart.
- [x] **Media search** — recursive, per-source, in both the desktop tree and the
      phone Browse tab; multi-word AND matching; a matching folder collapses to
      one entry instead of dumping every episode.
- [x] **Desktop browser polish** — themed folder/video icons, clean row
      selection, single-click folder expand.
- [x] **Security + reliability fixes** — XSS-safe phone UI (DOM, no innerHTML);
      web-cast paths confined to source roots; corrupted config backed up, not
      wiped; VLC launch/reuse reliability; VLC Continue-watching tracks the real
      file after a native skip.

- [x] **Headless server + connected GUI (v0.4.0)** — shared playback engine,
      server-owned playback, and safe standalone observer mode.
- [x] **Appearance and desktop integration (v0.4.1)** — persistent scaling,
      XWayland compatibility, and a proper application icon.

---

## Phase 1 — Linux, properly installable (AUR)

Turn "clone + shim + curl a binary" into a real, managed package.

- [x] Add **`pyproject.toml`** so vizgui is a proper installable package
      (entry point, package data for `remote.html`). *Load-bearing: this
      unblocks AUR, Flatpak, and PyInstaller later.*
- [x] Write a **PKGBUILD** (`packaging/`) with real dependencies:
      `python pyside6 ffmpeg vlc playerctl gvfs-smb qrencode` +
      `go-chromecast-git` (the one AUR dep — builds from Go source; there's no
      stable/binary go-chromecast package, but depending on it still drops the
      curl-a-binary step). First package is `vizgui-git` (builds from main);
      a stable `vizgui` follows once releases exist.
- [x] Ship `.desktop` entry + SVG icon via the package (`packaging/`).
- [ ] **Test in a clean Arch container/VM** with nothing pre-installed — the
      whole point is proving zero-setup install (`makepkg` + install).
      *(Next unchecked gate for Phase 1; homelab VMs available.)*
- [ ] Publish to the AUR. *(Blocked externally: AUR account registration
      freeze. PKGBUILDs are ready in `packaging/`.)*

**Risk:** low. Fastest win; covers the Arch/CachyOS crowd (most early users).

## Phase 1.5 — Release & CI plumbing (cross-cutting)

Needed before AUR/Flatpak can pull versioned tarballs, and before multi-platform
makes manual testing untenable.

- [x] **GitHub Actions**: run the test suite on push/PR
      (`.github/workflows/ci.yml`, Python 3.10 + 3.12).
- [x] A **release process**: push a `v*` tag → `release.yml` builds sdist/wheel
      and cuts a GitHub release (AUR & Flatpak can reference these tags).
- [x] **`CHANGELOG.md`** (keep-a-changelog style).
- [x] Cut tags **`v0.1.0`**, **`v0.2.0`**, **`v0.3.0`** (release workflow builds
      sdist/wheel + the GitHub release on each `v*` tag).
- [x] Add a stable `vizgui` PKGBUILD built from the v0.1.0 release tarball
      (`packaging/vizgui/`), alongside `vizgui-git` (`packaging/vizgui-git/`).

**Risk:** low.

## Phase 2 — "Just works" out of the box

The ease-of-use half. Mostly hardening what already exists.

- [x] **Auto-discover the TV** via mDNS in the wizard (background scan on open;
      fresh setups auto-add found TVs; manual Scan no longer blocks the UI).
- [x] **Every failure path graceful** — audited all network/IO calls; added
      timeouts where missing, surfaced silent remote-key failures, matched the
      firewall command to the running firewall (ufw/firewalld/none). SMB and
      cast paths were already off-thread with clear messages.
- [x] **Zero-config defaults** — fresh config seeds the user's Videos/Music/
      Downloads (those that exist) via `platforms.default_media_dirs()`, so the
      app opens with media to browse. Wizard auto-opens on a genuine first run
      (tracked by `config.FRESH_INSTALL`), with the TV auto-appearing in it.
- [ ] A **"test connection"** affordance in the wizard (TV reachable? share
      mountable?). *(optional polish)*
- [x] Config **upgrade safety** — load backfills missing keys via setdefault,
      and a present-but-unreadable config is backed up (`.corrupt`) rather than
      silently overwritten, so SMB passwords / phone pairings survive (v0.3.0).

**Risk:** low, high payoff. This is what makes a stranger keep the app.

## Phase 3 — Flatpak (any-distro reach)

- [ ] Flatpak manifest bundling ffmpeg/vlc; installs on any distro.
- [ ] **The hard part — the sandbox:** punch holes for go-chromecast's mDNS
      discovery, gvfs SMB mounting, casting (`--share=network`, filesystem /
      portal access). Needs real testing.
- [ ] Flathub submission once solid.

**Risk:** medium — the sandbox is the first genuinely tricky bit.

## Phase 4 — Windows & macOS backends

`windows.py` / `macos.py` fill the existing platform contract (shared POSIX
process launch factored into `_posix.py`). Written here; **untested on real
Windows/macOS** — pure logic is unit-tested, hardware behavior is not.

- [x] **Windows:** `%APPDATA%`/`%LOCALAPPDATA%` paths; VLC in Program Files; SMB
      via UNC/`net use`; firewall via `netsh`; no MPRIS; `CREATE_NO_WINDOW` +
      taskkill tree; hw encode nvenc/qsv/amf.
- [x] **macOS:** `~/Library` paths; VLC at `/Applications/VLC.app/...`; SMB via
      Finder (`osascript mount volume` → /Volumes); `ifconfig` subnet; no MPRIS;
      hw encode videotoolbox; PTY caster shared with Linux.
- [x] Add `vlc_exe()`/`has_vlc()` to the contract so VLC is found off-PATH.
- [x] Platform unit tests for both backends (`test_platforms.py`).
- [x] Replace the `qrencode` CLI with the **`qrcode` Python lib** so QR pairing
      works on Win/Mac (painted with Qt, no external binary) — done in v0.2.0.
- [ ] **Windows transcode/remux:** `write_wrapper` emits a bash script, so only
      direct-cast works on Windows — needs a `.bat`/host-native wrapper (macOS
      has bash, so it's fine there).
- [ ] Verify go-chromecast stays alive on Windows without a PTY (unknown).

**Risk:** medium — volume of per-OS detail, but the contract says exactly what
to fill. Real-hardware testing is the remaining unknown.

## Phase 5 — Windows & macOS packaging

- [ ] **PyInstaller** → Windows installer/.exe and macOS .app/.dmg; bundle
      ffmpeg, keep VLC as a declared external install.
- [ ] **macOS notarization** (Apple dev account, ~$99/yr) to avoid Gatekeeper
      blocks.
- [ ] **Windows signing** / document the SmartScreen warning for unsigned
      builds.

**Risk:** medium — signing/notarization is bureaucracy more than code.

---

## Polish & adoption (ongoing)

- [x] **README screenshots** — desktop + phone remote (Remote/Browse) in the
      README. *(A short demo gif is still a nice-to-have.)*
- [ ] **`CONTRIBUTING.md`** + dev-setup notes (it's a public repo now).
- [ ] Surface logs easily for supporting non-technical users.

## Deferred / maybe

Considered and intentionally **not** doing yet — revisit only if the project's
shape changes:

- **VAAPI encoder** for AMD GPUs (currently HEVC→TV falls back to CPU libx264).
  Wire up when an AMD user needs it.
- **DLNA target** — designed (SSDP + AVTransport + HTTP server); Chromecast
  covers most TVs for now. Drops in as a new `PlaybackTarget` when needed.
- **Event bus / DI container / config-service-with-listeners / deeper package
  split** — team-of-many patterns; over-engineering for the current scale. The
  Qt signal we already use (`WebBridge`) covers the one place decoupling pays
  off. Revisit only with multiple contributors.
- **Rust rewrite** — separate learning side-project, not a replacement.
