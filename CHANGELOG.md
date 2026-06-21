# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-06-19

### Added
- **Downloads tab** with real-time progress: downloads run attached to a
  pseudo-terminal so aria2c / yt-dlp / ffmpeg emit their actual progress, which
  the server parses into live percent / speed / ETA shown on a determinate bar.
- **For You** tab — AniList-powered recommendations from your watch history.
- Per-download status (`downloading` / `done` / `failed`) with cover art and a
  one-click "play this file" button for finished downloads.
- Packaging: installable via `pipx install ani-gui` / `pip` with an `ani-gui`
  console command (`pyproject.toml`, `ani_gui` package).
- Homebrew formula template (`Formula/ani-gui.rb`).
- macOS `.app` build script (`packaging/macos/make-app.sh`).
- Project homepage (`docs/index.html`) for GitHub Pages.
- `DISTRIBUTION.md` explaining hosting/packaging options.
- `ani-gui` opens the browser automatically; `--no-browser`, `--version` flags.

### Changed
- Source moved into the `ani_gui/` package; run from source with `./ani-gui`
  or `python3 -m ani_gui`.

## [0.5.5] — 2026-06-21

### Fixed
- **Playback no longer gets killed by ani-cli's post-play menu.** ani-cli drops
  into an interactive `fzf` menu after launching the player, which blocks
  forever under our non-tty stdin — ani-gui then timed out at 90s and killpg'd
  the whole group, taking the player with it. Now ani-gui (a) runs ani-cli with
  `ANI_CLI_EXTERNAL_MENU` disabled so it cleanly exits after launching the
  player, (b) returns as soon as the player launches (~40s, not 90s) instead of
  waiting, and (c) reaps the leftover ani-cli/fzf processes while **sparing the
  player** as a fallback. Also fixes the leaked-process pile this caused.
- Failed downloads that hit an expired CDN token now say "Source link expired
  (403) — hit Retry for a fresh one" (these tokens expire in seconds/minutes;
  Retry re-resolves a working link).
- The auto-opened browser is now launched detached (via `xdg-open`/`open` with
  output to /dev/null), so its own startup noise (Chromium/Ozone-Wayland/GCM
  warnings) no longer spills into ani-gui's terminal and looks like ani-gui
  errors.
- Diagnostics shows the full Wayland environment (`WAYLAND_DISPLAY`, `DISPLAY`,
  `XDG_RUNTIME_DIR`, session) and flags a missing `XDG_RUNTIME_DIR`.

## [0.5.4] — 2026-06-21

### Added
- **Force X11 (Wayland workaround)** toggle in Diagnostics — drops
  `WAYLAND_DISPLAY` (and sets `GDK_BACKEND=x11` / `QT_QPA_PLATFORM=xcb`) for the
  player, so mpv/VLC use XWayland when native Wayland output is broken (the
  player resolves the stream but never opens a window). Only shown on Wayland.
- **"Test stream reachability"** button in Diagnostics (`GET /api/test-stream`)
  — does a tiny real download to tell apart "the stream CDN is unreachable on
  your network" from "the player won't open a window," which look identical
  otherwise (both: nothing plays).

## [0.5.3] — 2026-06-21

### Fixed
- Failed downloads now show ani-cli's **real reason** (e.g. `Program "aria2c"
  not found`) instead of a generic "Download failed" — the reader thread keeps a
  tail of the downloader's output and surfaces the error on a non-zero exit.
- Diagnostics now flags missing **download** dependencies (aria2c / ffmpeg),
  which ani-cli requires for any download.

## [0.5.2] — 2026-06-21

### Added
- **Diagnostics panel** (footer link / `GET /api/diagnostics`) — reports ani-cli,
  players (with versions), tools, graphical display, and root status, and lists
  the likely reasons playback isn't opening (no display, running as root, VLC
  only, missing player).

### Fixed
- When playback resolves but there's no X11/Wayland display (SSH, sudo, headless
  service), ani-gui now says so instead of falsely claiming "Playing in mpv" —
  the player can't open a window without a display.
- The VLC success message warns that VLC may open-then-close on these streams
  and points to mpv.

## [0.5.1] — 2026-06-21

### Fixed
- Playback errors now surface ani-cli's **actual** reason (ANSI-stripped)
  instead of a generic "couldn't resolve a stream" — a missing dependency,
  dead provider, etc. Missing-dependency failures get an actionable message
  naming the program to install.
- "Default player" now falls back to VLC when only VLC is installed (no
  mpv/IINA) — previously ani-cli defaulted to mpv and died on Linux boxes that
  only had VLC.

## [0.5.0] — 2026-06-21

### Added
- **Auto-install ani-cli** — ani-gui sets up its own dependency now. The
  Homebrew formula `depends_on "ani-cli"`, a one-line `install.sh` bootstraps
  everything, and pip/pipx users get `ani-gui --install-ani-cli` plus an
  **Install ani-cli** button in the missing-binary banner (`POST
  /api/install-ani-cli`, no sudo — drops the script into a writable PATH dir or
  uses Homebrew).
- **Watch progress in the episode grid** — episodes you've already seen are
  dimmed with a ✓, and the next unwatched episode is auto-selected and scrolled
  into view (`GET /api/episodes` now returns `watched`).
- **Jump-to-episode filter** and a scrollable grid for long-running series, so
  shows with hundreds of episodes stay usable.
- **Download range** — pick an episode span (e.g. 5–12) instead of all-or-nothing.
- **Desktop notifications** when a background download finishes, with a watcher
  that keeps polling even after you leave the Downloads tab.
- **Remembered preferences** — sub/dub, player, quality, and last tab persist
  across reloads (localStorage).
- **Keyboard shortcuts** — `/` focuses search, `Esc` collapses the open result
  or clears the search box.
- **Retry failed downloads** — failed episodes get a one-click Retry, and a
  series accordion shows a "Retry failed (N)" button for the common case where
  a few episodes flake out during a bulk download. Download records now store
  the query / search position / show id so a retry re-issues the exact episode
  (`POST /api/retry-download`).

### Changed
- Recommendations are far faster: parallelized AniList + AllAnime lookups and a
  cached result (was 10-20s sequential, now a few seconds and instant when warm).

## [0.2.0]

### Added
- **Continue Watching** tab backed by ani-cli's history file — shows each
  series with its next unwatched episode and a one-click Resume.
- **Cover art** for search results, the episode panel, and Continue cards
  (from the AllAnime `thumbnail` field).
- **Update check & health**: footer shows ani-gui / ani-cli versions, a banner
  appears when an ani-cli update is available, when ani-cli is missing, or when
  no video player is found. New `GET /api/version` endpoint.
- New endpoints: `GET /api/continue`, `POST /api/resume`.

## [0.1.0]

### Added
- Initial release: search anime, browse episodes, and play in your ani-cli
  player (mpv / iina) from the browser.
- Sub/Dub toggle, quality selection, and download support.
- Endpoints: `GET /api/search`, `GET /api/episodes`, `POST /api/play`.

[Unreleased]: https://github.com/rickwiththeportalgun/ani-gui/compare/v0.5.5...main
[0.5.5]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.5
[0.5.4]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.4
[0.5.3]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.3
[0.5.2]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.2
[0.5.1]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.1
[0.5.0]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.5.0
[0.4.0]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.4.0
[0.2.0]: https://github.com/rickwiththeportalgun/ani-gui
[0.1.0]: https://github.com/rickwiththeportalgun/ani-gui
