# Homebrew formula for ani-gui.
#
# This is a template for a Homebrew *tap*. To ship it:
#   1. Push ani-gui to a GitHub repo and cut a release tag (e.g. v0.2.0).
#   2. Set `url` to that release tarball and fill in `sha256`
#      (run: brew fetch --build-from-source ./Formula/ani-gui.rb, or
#       `shasum -a 256 <tarball>`).
#   3. Put this file in a tap repo named `homebrew-tap`, then users run:
#        brew install rickwiththeportalgun/tap/ani-gui
#
class AniGui < Formula
  include Language::Python::Virtualenv

  desc "Small local web UI for ani-cli"
  homepage "https://github.com/rickwiththeportalgun/ani-gui"
  url "https://github.com/rickwiththeportalgun/ani-gui/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "REPLACE_WITH_TARBALL_SHA256"
  license "GPL-3.0-or-later"

  depends_on "python@3.12"
  depends_on "ani-cli"
  depends_on "mpv"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "ani-gui", shell_output("#{bin}/ani-gui --version")
  end
end
