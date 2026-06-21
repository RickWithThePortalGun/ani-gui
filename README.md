 # ani-gui

<p align="center">
  <a href="https://github.com/rickwiththeportalgun/ani-gui/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue?logo=gnu" alt="license: GPL-3.0"></a>
  <a href="https://pypi.org/project/ani-gui/"><img src="https://img.shields.io/pypi/v/ani-gui?logo=pypi&label=pypi" alt="PyPI version"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.6%2B-blue?logo=python&logoColor=white" alt="python 3.6+"></a>
  <a href="#"><img src="https://img.shields.io/badge/dependencies-none-brightgreen" alt="zero dependencies"></a>
  <a href="https://github.com/rickwiththeportalgun/ani-gui/releases"><img src="https://img.shields.io/github/v/release/rickwiththeportalgun/ani-gui?include_prereleases&label=release&logo=github" alt="GitHub release"></a>
  <a href="https://github.com/rickwiththeportalgun/ani-gui"><img src="https://img.shields.io/github/stars/rickwiththeportalgun/ani-gui?style=flat&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/rickwiththeportalgun/ani-gui"><img src="https://img.shields.io/github/last-commit/rickwiththeportalgun/ani-gui?logo=github" alt="last commit"></a>
</p>

A small local web UI for [ani-cli](https://github.com/pystardust/ani-cli).

It searches the same AllAnime API that ani-cli uses (so it can show a proper
list of series and an episode grid), and hands playback off to your installed
`ani-cli` binary — so all the stream-extraction and player logic stays in one
place and keeps working as ani-cli updates.

## Features

- Search with cover art (AniList / MyAnimeList / Wikipedia fallback), sub/dub toggle, episode grid
- Play or download any episode in your chosen player (mpv / IINA / VLC)
- **Continue Watching** — resumes the next episode from ani-cli's history
- Quality selection (best / 1080 / 720 / 480 / worst)
- Update & health check — flags ani-cli and ani-gui updates, missing binaries, or no player
- Zero third-party dependencies; single Python file + single HTML file

## Requirements

- `ani-cli` (it handles playback) — the [one-line installer](#install--run),
  the Homebrew formula, and `ani-gui --install-ani-cli` will set it up for you
  if it's missing; or install it yourself
- `python3` (standard library only — no pip installs)
- A player ani-cli knows about (mpv / IINA / VLC) for watching

On Linux, install the dependencies from your package manager first:

```sh
# Debian/Ubuntu
sudo apt install mpv python3 pipx
# Fedora
sudo dnf install mpv python3 pipx
# Arch
sudo pacman -S mpv python python-pipx
```

`ani-cli` isn't packaged on most distros — install it from the
[upstream repo](https://github.com/pystardust/ani-cli) (Arch users: `yay -S ani-cli`):

```sh
sudo curl -sL https://raw.githubusercontent.com/pystardust/ani-cli/master/ani-cli -o /usr/local/bin/ani-cli
sudo chmod +x /usr/local/bin/ani-cli
```

## Install & run

**One-line installer** — installs ani-cli, a player, and ani-gui (skips
whatever's already there):

```sh
curl -sL https://raw.githubusercontent.com/rickwiththeportalgun/ani-gui/main/install.sh | sh
```

**pipx** — recommended. Isolated environment, handles PATH automatically:

```sh
# Install pipx first (one time):
#   Debian/Ubuntu:  sudo apt install pipx
#   Fedora:         sudo dnf install pipx
#   Arch:           sudo pacman -S python-pipx
#   macOS:          brew install pipx
#   any OS:         python3 -m pip install --user pipx
pipx ensurepath          # adds ~/.local/bin to PATH — restart your shell after

pipx install ani-gui
ani-gui
```

**Homebrew** (macOS / Linux) — see [`Formula/ani-gui.rb`](Formula/ani-gui.rb):

```sh
brew install rickwiththeportalgun/tap/ani-gui
ani-gui
```

**pip** — works but may need a PATH tweak. Scripts land in a user bin dir that
isn't always on `PATH`: `~/.local/bin` on Linux, `~/Library/Python/3.x/bin` on
macOS. Add it to your shell config if `ani-gui` isn't found:

```sh
pip3 install --user ani-gui
# Linux — if "command not found":
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # or ~/.zshrc
# macOS — if "command not found":
export PATH="$HOME/Library/Python/$(python3 -c 'import sys;print(sys.version_info.minor)')/bin:$PATH"
ani-gui
```

**From source** (no install):

```sh
git clone https://github.com/rickwiththeportalgun/ani-gui && cd ani-gui
./ani-gui
```

**macOS .app** (double-click, no terminal):

```sh
sh packaging/macos/make-app.sh    # builds dist/ani-gui.app
```

Running `ani-gui` starts a local server and opens `http://127.0.0.1:17390` in
your browser. Options: `--port`, `--host`, `--no-browser` (or `ANI_GUI_PORT`).

> ani-gui is a **localhost** tool — it opens a player on the machine running it,
> so it isn't a shared website. See [DISTRIBUTION.md](DISTRIBUTION.md).

## Uninstall / teardown

Remove ani-gui the same way you installed it:

```sh
pipx uninstall ani-gui          # installed with pipx
pip3 uninstall ani-gui          # installed with pip
brew uninstall ani-gui          # installed via the Homebrew tap
# from source: just delete the cloned folder
```

ani-gui keeps a little state next to ani-cli's, under
`~/.local/state/ani-cli/` (or `$XDG_STATE_HOME/ani-cli`). Remove the files
**ani-gui** created — this leaves ani-cli's own watch history untouched:

```sh
rm -f ~/.local/state/ani-cli/ani-gui-settings.json   # your saved download dir
rm -f ~/.local/state/ani-cli/ani-downloads.json      # ani-gui's download log
```

Downloaded videos stay wherever you saved them (your chosen download directory,
or the folder you launched from) — delete those yourself if you want.

To also remove ani-cli and the player (full teardown):

```sh
rm -rf ~/.local/state/ani-cli         # ani-cli watch history + ani-gui state
sudo rm -f /usr/local/bin/ani-cli     # if installed via the curl snippet above
# Debian/Ubuntu:  sudo apt remove mpv
# Fedora:         sudo dnf remove mpv
# Arch:           sudo pacman -R mpv
```

## Use

**Search tab**

1. Type a search and hit **Search** (toggle **Sub/Dub** as needed).
2. Click a series to load its episodes (cover art shown for each result).
3. Click an episode, pick a quality, then **Play** (or **Download**).

**Continue Watching tab**

Reads ani-cli's watch history and shows each series with its cover and the
next unwatched episode. **Resume** plays that next episode in one click.
Series you're caught up on are shown but disabled.

Playback opens in your usual ani-cli player. Downloads go to ani-cli's
download directory (`ANI_CLI_DOWNLOAD_DIR`, defaults to the current dir).

## How it works

- `GET /api/search` and `GET /api/episodes` call the AllAnime GraphQL API
  directly. Covers are proxied through `GET /api/cover` (with a Wikipedia
  fallback when the API provides no artwork). Results are filtered/ordered
  to match ani-cli's own `-S` numbering.
- `GET /api/continue` parses ani-cli's history file
  (`$ANI_CLI_HIST_DIR` or `~/.local/state/ani-cli/ani-hsts`) and resolves
  each show's cover + next episode.
- `POST /api/play` runs `ani-cli -S <n> -e <ep> -q <quality> [--dub] [-v] "<query>"`
  with stdin closed, so ani-cli resolves the stream, launches the player
  detached, then exits cleanly. Pass `player: "vlc"` for VLC.
- `POST /api/resume` looks up the show's search position by id (so the `-S`
  number is correct) and plays the next episode.
- `GET /api/version` reports ani-gui / ani-cli versions, whether an update is
  available, and which players are installed.
- `GET /docs` serves the project landing page with install instructions and
  links back to the app.

## Troubleshooting

- **"ani-cli isn't installed"** — install it (`brew install ani-cli` or see the
  upstream repo) and make sure it's on your `PATH`, then reload.
- **"No video player found"** — install `mpv` or `iina`.
- **Play does nothing / black screen** — a specific source may be down. Try a
  different quality, or pick another search result. (Same behavior as ani-cli.)
- **Continue tab is slow** — it makes one API call per history entry; large
  histories take a moment.

## FAQ

**Can I host this so people use it from a browser, like a website?**
Not as-is. ani-gui launches a local player (mpv / iina) on the machine running
the server, so if you host it remotely the video opens on the *server*, not the
visitor's screen. It's designed as a localhost tool — each person runs it on
their own computer. See `DISTRIBUTION.md` for packaging options and what a
truly hosted, in-browser-playback version would require.

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Files

- `ani_gui/server.py` — zero-dependency Python backend
- `ani_gui/index.html` — single-file frontend
- `ani-gui` — source launcher (`python3 -m ani_gui`)
- `pyproject.toml` — packaging / console entry point
- `Formula/ani-gui.rb` — Homebrew formula template
- `packaging/macos/make-app.sh` — builds a double-clickable `.app`
- `docs/index.html` — project homepage (GitHub Pages)

## License

[GPL-3.0](LICENSE), matching upstream ani-cli (ani-gui replicates ani-cli's
public AllAnime API constants and mirrors its result ordering).

ani-gui is an independent front-end and is not affiliated with the ani-cli
project. It streams nothing itself — playback is delegated to ani-cli.
