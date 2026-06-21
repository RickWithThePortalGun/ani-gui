# Homebrew formula for ani-gui.
#
# This file lives in two repos:
#   rickwiththeportalgun/ani-gui           — source of truth, edited here
#   rickwiththeportalgun/homebrew-tap      — the published copy Brew reads
#
# After updating, copy to the tap repo and push both.
class AniGui < Formula
  include Language::Python::Virtualenv

  desc "Small local web UI for ani-cli"
  homepage "https://github.com/rickwiththeportalgun/ani-gui"
  url "https://github.com/rickwiththeportalgun/ani-gui/archive/refs/tags/v0.5.0.tar.gz"
  sha256 "728480e6cb2e45769141ee907b3356fc2783a87301f64f598b4ebda9819198b7"
  license "GPL-3.0-or-later"

  # ani-cli does the actual playback; mpv is its default player. Brew pulls
  # both in automatically so a fresh `brew install` gives a working setup.
  depends_on "ani-cli"
  depends_on "mpv"
  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      ani-gui is a localhost tool — run it and it opens http://127.0.0.1:17390
      in your browser:

        ani-gui

      ani-cli and mpv were installed as dependencies. If you'd rather use a
      different player (IINA / VLC), install it and ani-cli will pick it up.
    EOS
  end

  test do
    assert_match "ani-gui", shell_output("#{bin}/ani-gui --version")
  end
end
