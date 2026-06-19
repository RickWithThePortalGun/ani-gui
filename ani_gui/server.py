#!/usr/bin/env python3
"""
ani-gui — a small local web UI for ani-cli.

It talks to the same AllAnime API that ani-cli uses (for search + episode
lists, so the UI can show a proper grid), and hands playback off to the
installed `ani-cli` binary so all the stream-extraction and player logic
stays in one place.

Zero third-party dependencies — standard library only.

    python3 server.py            # serves on http://127.0.0.1:17390
    python3 server.py --port 9000
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.2.3"
ANI_CLI_RAW = "https://raw.githubusercontent.com/pystardust/ani-cli/master/ani-cli"

# --- AllAnime API (mirrors the constants inside the ani-cli script) ----------
AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
         "Gecko/20100101 Firefox/150.0")
REFERER = "https://youtu-chan.com"
API = "https://api.allanime.day/api"
COVER_CDN = "https://wp.youtube-anime.com/aln.youtube-anime.com"
ANILIST_API = "https://graphql.anilist.co"

# Wikipedia cover cache (title -> image URL or "")
_wiki_cover_cache = {}
_wiki_cover_lock = threading.Lock()

# AniList recommendation cache (show title -> [(title, cover_url), …])
_anilist_recs_cache = {}
_anilist_recs_lock = threading.Lock()

SEARCH_GQL = ("query( $search: SearchInput $limit: Int $page: Int "
              "$translationType: VaildTranslationTypeEnumType "
              "$countryOrigin: VaildCountryOriginEnumType ) { shows( "
              "search: $search limit: $limit page: $page "
              "translationType: $translationType countryOrigin: "
              "$countryOrigin ) { edges { _id name thumbnail "
              "availableEpisodes __typename }}}")

EPISODES_GQL = ("query ($showId: String!) { show( _id: $showId ) "
                "{ _id name thumbnail availableEpisodesDetail }}")

# ani-cli's history file (same default location the script uses).
HISTFILE = os.path.join(
    os.environ.get("ANI_CLI_HIST_DIR")
    or os.path.join(os.environ.get("XDG_STATE_HOME")
                    or os.path.expanduser("~/.local/state"), "ani-cli"),
    "ani-hsts")

# Downloads log — records what the user asked to download.
DLFILE = os.path.join(
    os.environ.get("ANI_CLI_DOWNLOAD_DIR")
    or os.path.join(os.environ.get("XDG_STATE_HOME")
                    or os.path.expanduser("~/.local/state"), "ani-cli"),
    "ani-downloads.json")

_downloads_lock = threading.Lock()


def _api_post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": AGENT, "Referer": REFERER})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _resolve_thumbnail(raw, title=""):
    """Turn an API thumbnail into a browser-usable URL.

    * Full URLs (anilist, myanimelist) → passed through as-is.
    * Relative ``mcovers/…`` paths → rewritten to ``/api/cover?path=…`` so the
      server proxies them with the CDN's required referer.
    * Empty → ``/api/cover?title=…`` so the server can try Wikipedia as a
      fallback (lazy — only fetched when the browser requests it)."""
    if raw:
        if raw.startswith("http"):
            return raw
        return f"/api/cover?path={urllib.parse.quote(raw, safe='')}"
    if title:
        return f"/api/cover?title={urllib.parse.quote(clean_title(title))}"
    return ""


def _wiki_cover(title):
    """Return a Wikipedia cover image URL for *title*, or '' on failure."""
    with _wiki_cover_lock:
        if title in _wiki_cover_cache:
            return _wiki_cover_cache[title]

    url = ""
    try:
        # 1) Search for the best page.
        q = f"{title} anime"
        req = urllib.request.Request(
            "https://en.wikipedia.org/w/api.php?action=query"
            f"&list=search&srsearch={urllib.parse.quote(q)}"
            "&format=json&srlimit=1",
            headers={"User-Agent": f"ani-gui/{VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            results = json.loads(r.read()).get("query", {}).get("search", [])
        page = results[0]["title"] if results else title

        # 2) Fetch summary + thumbnail.
        req = urllib.request.Request(
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            f"{urllib.parse.quote(page.replace(' ', '_'))}",
            headers={"User-Agent": f"ani-gui/{VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            summary = json.loads(r.read())
        url = summary.get("thumbnail", {}).get("source") or ""
    except Exception:
        url = ""

    with _wiki_cover_lock:
        _wiki_cover_cache[title] = url
    return url


def search_anime(query, mode):
    payload = {
        "variables": {
            "search": {"allowAdult": False, "allowUnknown": False,
                       "query": query},
            "limit": 40, "page": 1,
            "translationType": mode, "countryOrigin": "ALL"},
        "query": SEARCH_GQL}
    edges = _api_post(payload).get("data", {}).get("shows", {}).get("edges", [])
    out = []
    # Keep only shows that actually have episodes in this mode, preserving
    # order — this matches how `ani-cli -S <n>` numbers its results, so the
    # 1-based `nth` we hand back lines up with ani-cli's selection.
    nth = 0
    for e in edges:
        avail = e.get("availableEpisodes") or {}
        count = avail.get(mode) or 0
        if count < 1:
            continue
        nth += 1
        out.append({
            "id": e.get("_id"),
            "name": e.get("name", "").replace('\\"', '"'),
            "thumbnail": _resolve_thumbnail(
                e.get("thumbnail") or "", e.get("name", "")),
            "sub": avail.get("sub") or 0,
            "dub": avail.get("dub") or 0,
            "count": count,
            "nth": nth,
        })
    return out


def _ep_key(x):
    try:
        return float(x)
    except ValueError:
        return float("inf")


def _show(show_id):
    payload = {"variables": {"showId": show_id}, "query": EPISODES_GQL}
    return _api_post(payload).get("data", {}).get("show", {}) or {}


def episodes_list(show_id, mode):
    detail = _show(show_id).get("availableEpisodesDetail", {}) or {}
    return sorted(detail.get(mode, []) or [], key=_ep_key)


def find_nth(query, mode, show_id):
    """Return the 1-based position of show_id in a search for `query`,
    matching how `ani-cli -S <n>` numbers results. None if not found."""
    for r in search_anime(query, mode):
        if r["id"] == show_id:
            return r["nth"]
    return None


def clean_title(title):
    """Strip ani-cli's trailing " (N episodes)" annotation."""
    return re.sub(r"\s*\(\d+ episodes\)\s*$", "", title).strip()


def read_history():
    """Parse ani-cli's history file into [{ep, id, title}] (newest last)."""
    entries = []
    try:
        with open(HISTFILE) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[1]:
                    entries.append({"ep": parts[0], "id": parts[1],
                                    "title": parts[2]})
    except FileNotFoundError:
        pass
    return entries


def _read_downloads():
    """Return the current download log (newest first)."""
    try:
        with open(DLFILE) as f:
            return json.loads(f.read() or "[]")
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_downloads(items):
    with _downloads_lock:
        with open(DLFILE, "w") as f:
            json.dump(items, f, indent=2)


def _record_download(title, ep, quality, mode):
    """Add a download entry and persist immediately."""
    items = _read_downloads()
    # Remove any older entry for the same title+ep (duplicate).
    items = [d for d in items
             if not (d.get("title") == title and d.get("ep") == ep)]
    items.insert(0, {
        "title": title,
        "ep": ep,
        "quality": quality,
        "mode": mode,
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "dir": os.environ.get("ANI_CLI_DOWNLOAD_DIR")
               or os.getcwd(),
    })
    # Keep at most 50 entries.
    _write_downloads(items[:50])


def _anilist_post(query, variables):
    """Tiny GraphQL helper for the AniList API (public, no auth)."""
    payload = {"query": query, "variables": variables}
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        ANILIST_API, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _anilist_recommendations(title):
    """Query AniList for user-submitted recommendations for *title*.

    Returns a list of ``{name, thumbnail}`` dicts (english/romaji title and
    AniList cover URL).  Cached per *title* so repeated calls are cheap."""
    with _anilist_recs_lock:
        if title in _anilist_recs_cache:
            return _anilist_recs_cache[title]

    recs = []
    try:
        # 1) Find the AniList media ID for this title.
        search = _anilist_post(
            "query ($s: String) { Media(search: $s, type: ANIME) { id } }",
            {"s": title})
        media = (search.get("data", {}).get("Media") or {})
        ani_id = media.get("id")
        if not ani_id:
            raise ValueError("not found on AniList")

        # 2) Fetch recommendations (highest-rated first).
        result = _anilist_post(
            """query ($id: Int) { Media(id: $id, type: ANIME) {
              recommendations(sort: RATING_DESC) {
                nodes {
                  mediaRecommendation {
                    title { romaji english }
                    coverImage { medium }
                    format
                  }
                }
              }
            }}""",
            {"id": ani_id})

        nodes = (result.get("data", {})
                 .get("Media", {})
                 .get("recommendations", {})
                 .get("nodes", []) or [])
        for n in nodes:
            mr = n.get("mediaRecommendation") or {}
            t = mr.get("title") or {}
            name = (t.get("english") or t.get("romaji") or "").strip()
            cover = (mr.get("coverImage") or {}).get("medium") or ""
            if name:
                recs.append({"name": name, "thumbnail": cover or ""})
    except Exception:
        recs = []

    with _anilist_recs_lock:
        _anilist_recs_cache[title] = recs
    return recs


def recommendations(mode):
    """Generate personalised recommendations from the user's watch history.

    For the 5 most-recently-watched shows we ask AniList for similar anime,
    then deduplicate, drop anything the user has already watched, and
    cross-reference with AllAnime so we only return titles that actually have
    episodes in *mode*."""
    hist = read_history()
    if not hist:
        return []

    already = {h["id"] for h in hist}
    seen_names = set()
    out = []

    # Most-recent first — the newest 5 shows drive recommendations.
    for h in reversed(hist[-5:]):
        title = clean_title(h["title"])
        for rec in _anilist_recommendations(title):
            rname = rec["name"]
            if rname in seen_names:
                continue
            seen_names.add(rname)

            # Search AllAnime to see if it's available and get the ID.
            results = search_anime(rname, mode)
            if not results:
                continue
            best = results[0]

            # Skip shows the user already has in history.
            if best["id"] in already:
                continue

            # Use the AllAnime thumbnail if available, else the AniList one
            # (which is always a full URL and needs no proxying).
            thumb = best["thumbnail"]
            if not thumb or "/api/cover?title=" in thumb:
                thumb = rec["thumbnail"] or ""

            out.append({
                "id": best["id"],
                "name": best["name"],
                "thumbnail": _resolve_thumbnail(thumb, best["name"]),
                "nth": best["nth"],
                "sub": best["sub"],
                "dub": best["dub"],
                "mode": mode,
                "because": title,
            })

        # Don't overwhelm — 12 recommendations is plenty.
        if len(out) >= 12:
            break

    return out[:12]


def continue_watching(mode):
    """For each history entry, resolve cover + next unwatched episode."""
    entries = read_history()

    def enrich(h):
        try:
            show = _show(h["id"])
            detail = show.get("availableEpisodesDetail", {}) or {}
            eps = sorted(detail.get(mode, []) or [], key=_ep_key)
            next_ep = None
            if h["ep"] in eps:
                i = eps.index(h["ep"])
                if i + 1 < len(eps):
                    next_ep = eps[i + 1]
            return {
                "id": h["id"],
                "title": clean_title(show.get("name") or h["title"]),
                "thumbnail": _resolve_thumbnail(
                    show.get("thumbnail") or "", show.get("name") or h["title"]),
                "watched": h["ep"],
                "next_ep": next_ep,
                "total": eps[-1] if eps else None,
                "mode": mode,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = [r for r in ex.map(enrich, reversed(entries)) if r]
    return out


def ani_cli_path():
    return (shutil.which("ani-cli")
            or ("/opt/homebrew/bin/ani-cli"
                if os.path.exists("/opt/homebrew/bin/ani-cli") else None))


PLAYER_LABELS = {"default": "your player", "vlc": "VLC"}


def _player_label(player):
    if player == "vlc":
        return "VLC"
    # Best-effort name of the default player ani-cli will pick.
    if shutil.which("iina"):
        return "IINA"
    if shutil.which("mpv"):
        return "mpv"
    return "your player"


def play(query, nth, ep, quality, mode, download=False, player="default"):
    """Run ani-cli for one episode.

    For playback we capture ani-cli's output and wait briefly so we can report
    what actually happened (source found, quality fallback, or no source).
    For downloads we return immediately (they run for minutes in the
    background)."""
    binp = ani_cli_path()
    if not binp:
        return {"ok": False, "stage": "missing",
                "error": "ani-cli not found on PATH."}

    cmd = [binp, "-S", str(nth), "-e", str(ep), "-q", quality or "best"]
    if mode == "dub":
        cmd.append("--dub")
    if player == "vlc":
        cmd.append("-v")
    if download:
        cmd.append("-d")
    cmd.append(query)

    env = dict(os.environ)
    # Make sure ani-cli can find the players/curl even if the server was
    # started from a minimal environment.
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")

    plabel = "the downloader" if download else _player_label(player)

    if download:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env, start_new_session=True)
        _record_download(query, ep, quality, mode)
        return {"ok": True, "stage": "download",
                "message": f"Downloading episode {ep} in the background "
                           "(saved to ani-cli's download dir)."}

    # Playback: ani-cli launches the player detached, then exits — so we can
    # capture its output to learn whether a source was found.
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=env,
                           start_new_session=True, timeout=90)
        out = (r.stdout or "") + "\n" + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return {"ok": True, "stage": "slow",
                "message": "Still resolving the stream — this source is slow. "
                           f"{plabel} should open shortly."}

    low = out.lower()
    if "episode not released" in low:
        return {"ok": False, "stage": "not_released",
                "error": "That episode isn't released yet."}
    if "no valid sources" in low or "episode is released, but no" in low:
        return {"ok": False, "stage": "no_source",
                "error": "No working source for this episode. "
                         "Try a different quality, language, or result."}

    fell_back = "specified quality not found" in low
    fetched = "links fetched" in low
    if not fetched and r.returncode != 0:
        return {"ok": False, "stage": "failed",
                "error": "Couldn't resolve a stream. "
                         "Try another quality or result."}

    msg = f"Playing episode {ep} in {plabel}."
    if fell_back:
        msg = (f"Requested quality wasn't available — playing the best source "
               f"in {plabel}.")
    return {"ok": True, "stage": "playing", "message": msg}


# --- version / health --------------------------------------------------------
_anicli_latest_cache = {"value": None, "ts": 0}
_latest_lock = threading.Lock()

PYPI_URL = "https://pypi.org/pypi/ani-gui/json"


def anicli_installed_version():
    binp = ani_cli_path()
    if not binp:
        return None
    try:
        out = subprocess.run([binp, "-V"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return out.splitlines()[0].strip() if out else None
    except Exception:
        return None


def anicli_latest_version():
    """Latest ani-cli version from the master branch, same source ``ani-cli -U``
    uses.  Cached for an hour so the UI can poll cheaply."""
    with _latest_lock:
        if _anicli_latest_cache["value"] and \
           time.time() - _anicli_latest_cache["ts"] < 3600:
            return _anicli_latest_cache["value"]
    try:
        req = urllib.request.Request(ANI_CLI_RAW, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=12) as r:
            text = r.read().decode("utf-8", "replace")
        m = re.search(r'^version_number="([^"]+)"', text, re.M)
        latest = m.group(1) if m else None
    except Exception:
        latest = None
    if latest:
        with _latest_lock:
            _anicli_latest_cache.update(value=latest, ts=time.time())
    return latest


def _anigui_latest_version():
    """Latest ani-gui version from PyPI.  Cached for an hour."""
    with _latest_lock:
        if _anicli_latest_cache.get("_anigui_value") and \
           time.time() - _anicli_latest_cache.get("_anigui_ts", 0) < 3600:
            return _anicli_latest_cache["_anigui_value"]
    try:
        req = urllib.request.Request(
            PYPI_URL, headers={"User-Agent": f"ani-gui/{VERSION}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            latest = json.loads(r.read()).get("info", {}).get("version", "")
    except Exception:
        latest = ""
    if latest:
        with _latest_lock:
            _anicli_latest_cache["_anigui_value"] = latest
            _anicli_latest_cache["_anigui_ts"] = time.time()
    return latest


def version_info():
    acli_installed = anicli_installed_version()
    acli_latest = anicli_latest_version()
    agui_latest = _anigui_latest_version()
    deps = {name: bool(shutil.which(name))
            for name in ("ani-cli", "mpv", "iina", "vlc", "curl",
                         "yt-dlp", "ffmpeg")}
    has_player = deps["mpv"] or deps["iina"] or deps["vlc"]
    return {
        "ani_gui": VERSION,
        "ani_gui_update": bool(agui_latest and agui_latest != VERSION),
        "ani_gui_latest": agui_latest or None,
        "ani_cli": {
            "installed": acli_installed,
            "latest": acli_latest,
            "path": ani_cli_path(),
            "update_available": bool(acli_installed and acli_latest
                                     and acli_installed != acli_latest),
        },
        "deps": deps,
        "has_player": has_player,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if u.path == "/docs":
                # Try the in-package copy first (works for pip installs),
                # then fall back to the source-layout location.
                candidates = [
                    os.path.join(HERE, "docs.html"),
                    os.path.join(os.path.dirname(HERE), "docs", "index.html"),
                ]
                for path in candidates:
                    if os.path.isfile(path):
                        with open(path, "rb") as f:
                            return self._send(200, f.read(),
                                              "text/html; charset=utf-8")
                return self._send(404, {"error": "docs not found"})
            if u.path == "/api/search":
                query = (q.get("q", [""])[0]).strip()
                mode = q.get("mode", ["sub"])[0]
                if not query:
                    return self._send(200, {"results": []})
                return self._send(200, {"results": search_anime(query, mode)})
            if u.path == "/api/episodes":
                sid = q.get("id", [""])[0]
                mode = q.get("mode", ["sub"])[0]
                return self._send(200, {"episodes": episodes_list(sid, mode)})
            if u.path == "/api/continue":
                mode = q.get("mode", ["sub"])[0]
                return self._send(200, {"items": continue_watching(mode)})
            if u.path == "/api/recommendations":
                mode = q.get("mode", ["sub"])[0]
                return self._send(200, {"items": recommendations(mode)})
            if u.path == "/api/downloads":
                return self._send(200, {"items": _read_downloads()})
            if u.path == "/api/version":
                return self._send(200, version_info())
            if u.path == "/api/cover":
                path = (q.get("path", [""])[0])
                title = (q.get("title", [""])[0])
                if path:
                    # Proxy from the AllAnime CDN (requires specific referer).
                    cdn = f"{COVER_CDN}/{path}"
                    req = urllib.request.Request(
                        cdn, headers={"User-Agent": AGENT, "Referer": REFERER})
                    try:
                        with urllib.request.urlopen(req, timeout=12) as r:
                            data = r.read()
                            ctype = r.headers.get("Content-Type", "image/webp")
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    except Exception:
                        return self._send(404, {"error": "cover not reachable"})
                if title:
                    url = _wiki_cover(title)
                    if url:
                        # Redirect to the full Wikipedia image URL.
                        self.send_response(302)
                        self.send_header("Location", url)
                        self.send_header("Cache-Control", "public, max-age=86400")
                        self.end_headers()
                        return
                    return self._send(404, {"error": "no Wikipedia cover"})
                return self._send(400, {"error": "need ?path= or ?title="})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/play":
                res = play(
                    query=body["query"], nth=body["nth"], ep=body["ep"],
                    quality=body.get("quality", "best"),
                    mode=body.get("mode", "sub"),
                    player=body.get("player", "default"),
                    download=bool(body.get("download")))
                return self._send(200 if res.get("ok") else 502, res)
            if u.path == "/api/resume":
                # Resolve the show's search position by id, then play.
                mode = body.get("mode", "sub")
                title = clean_title(body["title"])
                nth = find_nth(title, mode, body["id"])
                if nth is None:
                    return self._send(404, {
                        "ok": False,
                        "error": "Couldn't locate this title in search — "
                                 "open it from the Search tab.",
                        "query": title})
                res = play(query=title, nth=nth, ep=body["ep"],
                           quality=body.get("quality", "best"), mode=mode,
                           player=body.get("player", "default"),
                           download=bool(body.get("download")))
                return self._send(200 if res.get("ok") else 502, res)
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ani-gui",
                                 description="Local web UI for ani-cli.")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("ANI_GUI_PORT", 17390)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true",
                    help="don't open the browser automatically")
    ap.add_argument("-V", "--version", action="version",
                    version=f"ani-gui {VERSION}")
    args = ap.parse_args(argv)
    if not ani_cli_path():
        print("warning: ani-cli not found on PATH — playback will fail",
              file=sys.stderr)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"ani-gui {VERSION} running at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
