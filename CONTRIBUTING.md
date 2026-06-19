# Contributing to ani-gui

Thanks for your interest! ani-gui is a small, dependency-free web UI for
[ani-cli](https://github.com/pystardust/ani-cli). Contributions of all sizes
are welcome — bug reports, docs, and code.

## Ground rules

- **No third-party Python dependencies.** The backend is standard-library only,
  on purpose — it should run with a bare `python3`. Please keep it that way.
- **Single-file frontend.** `index.html` holds its own CSS and JS. No build step.
- **ani-cli does the playback.** Stream extraction, players, and history live in
  ani-cli. ani-gui should call it rather than re-implement it.

## Getting started

```sh
git clone <your-fork>
cd ani-gui
python3 server.py        # http://127.0.0.1:17390
```

You need a working `ani-cli` and a player (`mpv` or `iina`) to test playback.

## Before opening a PR

- Run the checks CI runs:
  ```sh
  python3 -m py_compile server.py
  ruff check .            # if you have ruff installed
  ```
- Keep changes focused; describe what and why in the PR.
- Update `CHANGELOG.md` under `## [Unreleased]`.
- If you touch behavior, note it in the `README.md`.

## Reporting bugs

Use the issue templates. Include your OS, `python3 --version`, `ani-cli -V`,
and the server log (the terminal output of `python3 server.py`).

## Code style

- Python: PEP 8, 4-space indent. Small, readable functions.
- JS: vanilla, no frameworks. Match the existing style in `index.html`.

By contributing you agree your work is licensed under the project's
[GPL-3.0](LICENSE).
