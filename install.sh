#!/bin/sh
# ani-gui bootstrap installer.
#
#   curl -sL https://raw.githubusercontent.com/rickwiththeportalgun/ani-gui/main/install.sh | sh
#
# Installs ani-cli (if missing), a video player (mpv, if no player is found),
# and ani-gui itself via pipx. Re-running is safe — anything already present is
# left alone. No sudo unless your package manager needs it.
set -eu

say()  { printf '\033[36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[33m warning:\033[0m %s\n' "$1" >&2; }

have() { command -v "$1" >/dev/null 2>&1; }

# Detect a system package manager (used for mpv / pipx, not for ani-cli).
pkg_install() {
  if   have brew;    then brew install "$@"
  elif have apt-get; then sudo apt-get update -qq && sudo apt-get install -y "$@"
  elif have dnf;     then sudo dnf install -y "$@"
  elif have pacman;  then sudo pacman -S --noconfirm "$@"
  else return 1
  fi
}

# 1. ani-cli — the thing that actually plays anime.
if have ani-cli; then
  say "ani-cli already installed ($(command -v ani-cli))"
elif have brew; then
  say "installing ani-cli via Homebrew"
  brew install ani-cli
else
  # No-sudo install: drop the upstream script into a bin dir on PATH.
  bindir="$HOME/.local/bin"
  mkdir -p "$bindir"
  say "installing ani-cli to $bindir"
  curl -sL https://raw.githubusercontent.com/pystardust/ani-cli/master/ani-cli -o "$bindir/ani-cli"
  chmod +x "$bindir/ani-cli"
  case ":$PATH:" in
    *":$bindir:"*) : ;;
    *) warn "add $bindir to your PATH, then restart your shell" ;;
  esac
fi

# 2. A video player — only install one if none is present.
if have mpv || have iina || have vlc; then
  say "video player already present"
else
  say "installing mpv (video player)"
  pkg_install mpv || warn "couldn't auto-install a player — install mpv, IINA, or VLC yourself"
fi

# 3. pipx, then ani-gui.
if ! have pipx; then
  say "installing pipx"
  pkg_install pipx || python3 -m pip install --user pipx
  python3 -m pipx ensurepath >/dev/null 2>&1 || pipx ensurepath >/dev/null 2>&1 || true
fi

say "installing ani-gui"
pipx install ani-gui || pipx upgrade ani-gui

say "done — run 'ani-gui' to start (opens http://127.0.0.1:17390)"
