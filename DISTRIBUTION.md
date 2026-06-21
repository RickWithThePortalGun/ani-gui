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

## Getting ani-cli automatically

ani-gui can't play anything without `ani-cli`, so every install path makes sure
it's there:

- **One-line installer** (`install.sh`) installs ani-cli (+ a player) before
  ani-gui.
- **Homebrew** declares `depends_on "ani-cli"` &mdash; `brew install` pulls it in.
- **pip / pipx**: run `ani-gui --install-ani-cli`, or click **Install ani-cli**
  in the app's banner. Both download the upstream script into a user-writable
  bin dir (no sudo), or use Homebrew if it's available.

## How users get it

### One-line installer &mdash; the simplest path

```sh
curl -sL https://raw.githubusercontent.com/rickwiththeportalgun/ani-gui/main/install.sh | sh
```

`install.sh` ensures ani-cli, a player (mpv if none is found), and pipx, then
installs ani-gui. Anything already present is skipped, so it's safe to re-run.

### pipx &mdash; recommended (any OS)

`pyproject.toml` already declares an `ani-gui` console entry point:

```sh
pipx install ani-gui
ani-gui                  # serves http://127.0.0.1:17390, opens browser
ani-gui --install-ani-cli   # if ani-cli isn't installed yet
```

Works on macOS, Linux, and Windows. Isolated, always on your PATH.

On Linux, get `pipx` and the runtime deps from the distro first:

```sh
# Debian/Ubuntu
sudo apt install pipx mpv && pipx ensurepath
# Fedora
sudo dnf install pipx mpv && pipx ensurepath
# Arch
sudo pacman -S python-pipx mpv && pipx ensurepath
```

`ani-cli` itself isn't packaged on most distros (Arch has it in the AUR:
`yay -S ani-cli`); otherwise drop the upstream script onto your PATH:

```sh
sudo curl -sL https://raw.githubusercontent.com/pystardust/ani-cli/master/ani-cli -o /usr/local/bin/ani-cli
sudo chmod +x /usr/local/bin/ani-cli
```

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

## Teardown / uninstall

Each install method removes cleanly. Pick the one you used:

```sh
pipx uninstall ani-gui          # pipx
pip3 uninstall ani-gui          # pip
brew uninstall ani-gui          # Homebrew tap
rm -rf dist/ani-gui.app         # macOS .app
# from source: delete the cloned repo
```

### State ani-gui leaves behind

ani-gui writes two small files into ani-cli's state directory
(`$XDG_STATE_HOME/ani-cli`, default `~/.local/state/ani-cli/`):

| File | Purpose | Safe to delete |
| --- | --- | --- |
| `ani-gui-settings.json` | saved download directory | yes |
| `ani-downloads.json` | ani-gui's download log (what you queued) | yes |

It only *reads* `ani-hsts` (ani-cli's watch history) — it never creates it, so
removing ani-gui won't touch your history. Downloaded video files live in the
configured download dir (`$ANI_CLI_DOWNLOAD_DIR`, or the launch directory) and
are left in place.

```sh
# Remove just ani-gui's own state:
rm -f ~/.local/state/ani-cli/ani-gui-settings.json \
      ~/.local/state/ani-cli/ani-downloads.json

# Full teardown — also drops ani-cli's history and the tools it used:
rm -rf ~/.local/state/ani-cli
sudo rm -f /usr/local/bin/ani-cli        # if installed via the curl snippet
sudo apt remove mpv   # or: dnf remove mpv / pacman -R mpv
```

The Homebrew formula's `ani-cli` and `mpv` dependencies are not auto-removed on
`brew uninstall ani-gui`; run `brew autoremove` to clear unused dependencies.

## Why not a hosted website?

If you host the server in the cloud, the video player opens on the **server** &mdash;
not on the user's screen. To make it play in the browser you'd need to
re-implement ani-cli's stream extraction, proxy every stream through the server,
and add an in-browser player (hls.js for `.m3u8`). That's a different project
altogether &mdash; possible as a self-hosted instance behind auth, but not the
goal of ani-gui.
