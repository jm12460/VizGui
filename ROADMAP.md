# Roadmap

Where vizgui is headed: from a working single-user Linux app to something
**anyone can download and use, cross-platform, with ease-of-use and
functionality out of the box.**

Current status: **v0.1.0** · Linux only · 69 passing tests · installs via
`install.sh` (clone + fetch go-chromecast binary).

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
- [x] Test suite (69 tests, pure-logic, no GUI/network/hardware)
- [x] Reliability pass (status bar, startup health check, graceful
      tool-missing handling)
- [x] MIT licensed, generic branding, public repo

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
- [ ] Publish to the AUR.

**Risk:** low. Fastest win; covers the Arch/CachyOS crowd (most early users).

## Phase 1.5 — Release & CI plumbing (cross-cutting)

Needed before AUR/Flatpak can pull versioned tarballs, and before multi-platform
makes manual testing untenable.

- [x] **GitHub Actions**: run the test suite on push/PR
      (`.github/workflows/ci.yml`, Python 3.10 + 3.12).
- [x] A **release process**: push a `v*` tag → `release.yml` builds sdist/wheel
      and cuts a GitHub release (AUR & Flatpak can reference these tags).
- [x] **`CHANGELOG.md`** (keep-a-changelog style).
- [x] Cut the first **`v0.1.0`** tag (release workflow builds sdist/wheel + the
      GitHub release).
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
- [ ] Config **upgrade safety** — never corrupt an older config across versions.
      *(basic: load backfills missing keys via setdefault)*

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
- [ ] Replace the `qrencode` CLI with the **`qrcode` Python lib** so QR pairing
      works on Win/Mac (currently degrades to PIN there).
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

- [ ] **README screenshots** / a short demo gif — sells a download-and-use app.
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
