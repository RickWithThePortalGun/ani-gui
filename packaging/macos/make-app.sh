#!/bin/sh
# Build a double-clickable ani-gui.app for macOS.
#
#   ./packaging/macos/make-app.sh            # builds ./dist/ani-gui.app
#   ./packaging/macos/make-app.sh /Applications
#
# The app just launches the `ani-gui` command (installed via pipx/brew/pip)
# and opens the browser. Install ani-gui first, e.g.:
#   pipx install ani-gui     OR     brew install rickwiththeportalgun/tap/ani-gui
set -eu

DEST="${1:-$(cd "$(dirname "$0")/../.." && pwd)/dist}"
APP="$DEST/ani-gui.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"

rm -rf "$APP"
mkdir -p "$MACOS" "$RES"

cat >"$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>ani-gui</string>
  <key>CFBundleDisplayName</key>     <string>ani-gui</string>
  <key>CFBundleIdentifier</key>      <string>com.anigui.app</string>
  <key>CFBundleVersion</key>         <string>0.2.0</string>
  <key>CFBundleShortVersionString</key> <string>0.2.0</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleExecutable</key>      <string>ani-gui</string>
  <key>LSMinimumSystemVersion</key>  <string>10.13</string>
  <key>LSUIElement</key>             <true/>
</dict>
</plist>
PLIST

cat >"$MACOS/ani-gui" <<'LAUNCH'
#!/bin/sh
# Finder launches apps with a minimal PATH, so widen it before searching.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
BIN="$(command -v ani-gui || true)"
if [ -z "$BIN" ]; then
  osascript -e 'display alert "ani-gui not installed" message "Install it first:\n\n  pipx install ani-gui\n\nor\n\n  brew install rickwiththeportalgun/tap/ani-gui" as critical'
  exit 1
fi
exec "$BIN"
LAUNCH

chmod +x "$MACOS/ani-gui"
echo "Built $APP"
echo "Move it to /Applications or double-click to run."
