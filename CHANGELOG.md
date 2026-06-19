# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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

[Unreleased]: https://github.com/pystardust/ani-cli/compare
[0.2.0]: https://github.com/pystardust/ani-cli
[0.1.0]: https://github.com/pystardust/ani-cli
