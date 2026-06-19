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

## [Unreleased]

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

[Unreleased]: https://github.com/rickwiththeportalgun/ani-gui/compare/v0.4.0...main
[0.4.0]: https://github.com/rickwiththeportalgun/ani-gui/releases/tag/v0.4.0
[0.2.0]: https://github.com/rickwiththeportalgun/ani-gui
[0.1.0]: https://github.com/rickwiththeportalgun/ani-gui
