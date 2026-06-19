# Distribution &amp; packaging

## It's a localhost app

ani-gui delegates playback to `ani-cli`, which launches a **local** video player
(mpv / IINA / VLC) on your machine. It's meant to run on your own computer &mdash;
not as a shared website. Each person runs their own instance.

## What's in the box

One Python file (`ani_gui/server.py`) + one HTML file (`ani_gui/index.html`).
Zero third-party dependencies. The server talks to the AllAnime API for search
and episode data, proxies cover art through the AllAnime CDN (with a Wikipedia
fallback when covers are missing), and hands playback off to the installed
`ani-cli` binary.

## How users get it

### pipx &mdash; recommended (any OS)

`pyproject.toml` already declares an `ani-gui` console entry point:

```sh
pipx install ani-gui
ani-gui                  # serves http://127.0.0.1:17390, opens browser
```

Works on macOS, Linux, and Windows. Isolated, always on your PATH.

### Homebrew &mdash; macOS / Linux

The formula lives in `Formula/ani-gui.rb`. To publish it, copy that file into a
**separate tap repo** called `homebrew-tap` under your GitHub account
(Homebrew expects the tap to be its own repo, not a subdirectory of this one):

```sh
# 1. Create a new repo: rickwiththeportalgun/homebrew-tap
# 2. Drop in the formula:
cp Formula/ani-gui.rb ../homebrew-tap/
# 3. Push it, then users run:
brew install rickwiththeportalgun/tap/ani-gui
ani-gui
```

The formula declares `ani-cli` and `mpv` as dependencies, so a fresh install
gets a working setup in one command.

### From source &mdash; no install

```sh
git clone https://github.com/rickwiththeportalgun/ani-gui && cd ani-gui
./ani-gui
```

### macOS `.app` &mdash; double-click, no terminal

`packaging/macos/make-app.sh` builds a minimal `.app` bundle that wraps the
`ani-gui` command. Users who never touch a terminal can double-click to start
the server and open their browser:

```sh
sh packaging/macos/make-app.sh       # builds dist/ani-gui.app
sh packaging/macos/make-app.sh /Applications  # installs system-wide
```

The `.app` is a thin shell-script wrapper &mdash; no `py2app` or bundling needed.

## All channels ship the same thing

pipx, Homebrew, source, and the `.app` all deliver the **same** `server.py` +
`index.html`. Packaging only changes how it's launched, not how it works.

## Why not a hosted website?

If you host the server in the cloud, the video player opens on the **server** &mdash;
not on the user's screen. To make it play in the browser you'd need to
re-implement ani-cli's stream extraction, proxy every stream through the server,
and add an in-browser player (hls.js for `.m3u8`). That's a different project
altogether &mdash; possible as a self-hosted instance behind auth, but not the
goal of ani-gui.
