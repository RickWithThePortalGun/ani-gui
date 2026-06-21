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
import signal
import subprocess
import sys
import threading
import time
import uuid
import urllib.request
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.5.2"
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

# Final "For You" results cache, keyed by (mode, recent-history signature).
_recs_cache = {"key": None, "value": None, "ts": 0}
_recs_cache_lock = threading.Lock()

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

# User settings — a tiny JSON file next to the other ani-cli state.
SETTINGS_FILE = os.path.join(
    os.environ.get("ANI_CLI_HIST_DIR")
    or os.path.join(os.environ.get("XDG_STATE_HOME")
                    or os.path.expanduser("~/.local/state"), "ani-cli"),
    "ani-gui-settings.json")

_downloads_lock = threading.Lock()
_settings_lock = threading.Lock()

# Live progress for downloads started in *this* server session, keyed by the
# download id we hand back to the browser. Populated by a reader thread that
# parses the downloader's terminal output (see _watch_download). Lost on
# restart — that's fine, the pid/file heuristic in _downloads_with_status()
# takes over for anything we don't have live state for.
_active_downloads = {}
_active_lock = threading.Lock()

# If a download's percentage hasn't advanced in this many seconds while the
# process is still alive, we treat it as stalled (e.g. the streaming source
# dropped the connection or its auth token expired) rather than leaving the
# progress bar spinning forever.
STALL_SECS = 60


def _set_progress(dl_id, data):
    with _active_lock:
        cur = _active_downloads.setdefault(dl_id, {})
        # Track forward progress so a stall (percent stuck) can be detected.
        pct = data.get("percent")
        if isinstance(pct, (int, float)) and pct > cur.get("_max_pct", -1):
            cur["_max_pct"] = pct
            cur["_advance_ts"] = time.time()
        cur.update(data)


def _finish_progress(dl_id, returncode):
    with _active_lock:
        cur = _active_downloads.setdefault(dl_id, {})
        cur["downloading"] = False
        cur["returncode"] = returncode
        cur["done_ts"] = time.time()
        # Keep the registry from growing without bound over a long session.
        if len(_active_downloads) > 80:
            finished = sorted(
                ((k, v.get("done_ts", 0)) for k, v in _active_downloads.items()
                 if not v.get("downloading", True)),
                key=lambda kv: kv[1])
            for k, _ in finished[:len(_active_downloads) - 80]:
                _active_downloads.pop(k, None)


def cancel_download(dl_id):
    """Stop an in-flight download by killing its process group, keeping any
    partial file already on disk. Used for stalled downloads where the source
    connection died near the end but the part fetched is still watchable."""
    rec = next((d for d in _read_downloads() if d.get("id") == dl_id), None)
    if not rec:
        return {"ok": False, "error": "download not found"}
    pid = rec.get("pid")
    if pid and os.name == "posix":
        try:
            # start_new_session put ani-cli + its downloader in one group;
            # killing the group stops aria2c/yt-dlp too.
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    with _active_lock:
        cur = _active_downloads.setdefault(dl_id, {})
        cur["cancelled"] = True
    _finish_progress(dl_id, -1)
    return {"ok": True}


def retry_download(dl_id):
    """Re-issue a failed/cancelled download from its stored record.

    Re-resolves the show's search position from the saved id where possible
    (search ordering can drift over time), falling back to the saved nth, then
    to the top result for the query."""
    rec = next((d for d in _read_downloads() if d.get("id") == dl_id), None)
    if not rec:
        return {"ok": False, "error": "download not found"}

    mode = rec.get("mode", "sub")
    title = rec.get("title", "")
    query = rec.get("query") or clean_title(title)
    show_id = rec.get("show_id", "")
    ep = rec.get("ep")

    nth = find_nth(query, mode, show_id) if show_id else None
    if nth is None:
        nth = rec.get("nth")
    if nth is None:
        results = search_anime(query, mode)
        nth = results[0]["nth"] if results else None
    if nth is None:
        return {"ok": False,
                "error": "Couldn't locate this show to retry — try from Search."}

    return play(query=query, nth=nth, ep=ep, quality=rec.get("quality", "best"),
                mode=mode, download=True, title=title,
                thumbnail=rec.get("thumbnail", ""), show_id=show_id)


def _parse_progress_line(line):
    """Pull percent / speed / ETA out of one downloader output line.

    Handles the three downloaders ani-cli uses — yt-dlp and aria2c report a
    real percentage; ffmpeg (HLS remux) only reports bytes, so we surface that
    and leave the bar indeterminate. Returns None for non-progress lines."""
    line = line.strip()
    if not line:
        return None

    # yt-dlp: "[download]  22.5% of ~123.45MiB at  2.34MiB/s ETA 00:42"
    m = re.search(r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\s*[KMGT]i?B)", line)
    if m:
        out = {"percent": float(m.group(1)), "total": m.group(2).replace(" ", "")}
        s = re.search(r"at\s+([\d.]+\s*[KMGT]i?B/s)", line)
        if s:
            out["speed"] = s.group(1).replace(" ", "")
        e = re.search(r"ETA\s+([\d:]+)", line)
        if e:
            out["eta"] = e.group(1)
        return out

    # aria2c: "[#abcd 1.2MiB/5.4MiB(22%) CN:16 DL:2.1MiB ETA:2s]"
    m = re.search(r"\((\d+)%\)", line)
    if m and ("DL:" in line or "ETA:" in line):
        out = {"percent": float(m.group(1))}
        b = re.search(r"([\d.]+[KMGT]i?B)/([\d.]+[KMGT]i?B)", line)
        if b:
            out["downloaded"], out["total"] = b.group(1), b.group(2)
        s = re.search(r"DL:([\d.]+[KMGT]i?B)", line)
        if s:
            out["speed"] = s.group(1) + "/s"
        e = re.search(r"ETA:(\w+)", line)
        if e:
            out["eta"] = e.group(1)
        return out

    # ffmpeg: "... size=   10240kB time=00:01:23.00 ... speed=1.2x"
    m = re.search(r"size=\s*([\d.]+\s*[kKMGT]?B)\s", line)
    if m and "time=" in line:
        return {"downloaded": m.group(1).replace(" ", "")}

    return None


def _watch_download(dl_id, master_fd, proc):
    """Read the download's terminal output, parse progress, mark completion."""
    _set_progress(dl_id, {"downloading": True, "percent": None,
                          "_advance_ts": time.time()})
    buf = b""
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break  # slave closed (EIO on macOS) → download finished
            if not chunk:
                break
            buf += chunk
            # Progress is updated in place with carriage returns; treat \r and
            # \n alike and parse every complete line, keeping the partial tail.
            text = buf.replace(b"\r", b"\n").decode("utf-8", "replace")
            parts = text.split("\n")
            buf = parts[-1].encode("utf-8", "replace")
            for ln in parts[:-1]:
                p = _parse_progress_line(ln)
                if p:
                    _set_progress(dl_id, p)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        _finish_progress(dl_id, proc.wait())


def _read_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.loads(f.read() or "{}")
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_settings(s):
    with _settings_lock:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)


def _download_dir():
    """Effective download directory: env var > settings > cwd."""
    env = os.environ.get("ANI_CLI_DOWNLOAD_DIR")
    if env:
        return env
    s = _read_settings().get("download_dir", "")
    return s or os.getcwd()


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


def watched_episode(show_id):
    """The last episode watched for *show_id* per ani-cli's history, or None."""
    for h in read_history():
        if h["id"] == show_id:
            return h["ep"]
    return None


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


def _record_download(title, ep, quality, mode, pid=None, thumbnail="", dl_id=None,
                     query="", nth=None, show_id=""):
    """Add a download entry and persist immediately.

    Stores enough to re-issue the exact same download later (query / search
    position / show id), so a failed episode can be retried in one click."""
    items = _read_downloads()
    # Remove any older entry for the same title+ep (duplicate).
    items = [d for d in items
             if not (d.get("title") == title and d.get("ep") == ep)]
    items.insert(0, {
        "id": dl_id,
        "title": title,
        "ep": ep,
        "quality": quality,
        "mode": mode,
        "thumbnail": thumbnail,
        "time": time.strftime("%Y-%m-%d %H:%M"),
        "dir": _download_dir(),
        "pid": pid,
        "query": query,
        "nth": nth,
        "show_id": show_id,
    })
    # Keep at most 50 entries.
    _write_downloads(items[:50])


def _norm_name(s):
    """Normalise a title the way ani-cli does when it builds a filename.

    ani-cli does ``cut -d'(' -f1 | tr -d '[:punct:]'`` on the show's canonical
    AllAnime name, so the file is ``<name without punctuation> Episode <N>.mp4``.
    We mirror that — drop everything from the first ``(``, strip punctuation,
    collapse whitespace, lowercase — so a record's title lines up with the
    real filename regardless of colons, dashes, etc."""
    s = s.split("(")[0]
    s = re.sub(r"[^\w\s]|_", "", s)      # tr -d '[:punct:]' (underscore included)
    return re.sub(r"\s+", " ", s).strip().lower()


def _match_download_files(entry, files):
    """Match a download record to files already on disk.

    ani-cli names downloads "<canonical title> Episode <N>.<ext>", so we
    require an exact "Episode <N>" token plus the normalised title appearing
    in the filename. The title must be the *canonical AllAnime name* (what
    ani-cli names the file after), not the user's search term — e.g. a search
    for "super cube" downloads "Chao Neng Lifang Chaofan Pian Episode 8.mp4"."""
    ep = str(entry.get("ep", ""))
    ep_re = re.compile(rf"episode\s*0*{re.escape(ep)}(?!\d)", re.I)
    title_norm = _norm_name(entry.get("title", ""))
    out = []
    for f in files:
        if not ep_re.search(f["name"].lower()):
            continue
        if title_norm and title_norm not in _norm_name(f["name"]):
            continue
        out.append(f)
    return out


def _scan_download_files(ddir=None):
    """List video files in *ddir* (defaults to the configured download dir)."""
    ddir = ddir or _download_dir()
    exts = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v"}
    files = []
    try:
        for name in os.listdir(ddir):
            if os.path.splitext(name)[1].lower() in exts:
                full = os.path.join(ddir, name)
                try:
                    st = os.stat(full)
                    files.append({
                        "name": name,
                        "path": full,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    # Newest first.
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def _downloads_with_status():
    """Return the download log with live status, progress, and matched files.

    Two layers of truth, in order of preference:
    1. Live progress from this session's reader thread (real percent/speed/ETA
       and the downloader's own exit code) — accurate to the second.
    2. A pid + file-presence fallback for entries we have no live state for
       (e.g. started by a previous server run). ani-cli's downloader runs in
       the foreground, so its recorded pid stays alive for the whole download;
       once it's gone and no file showed up, the download genuinely failed.
    """
    items = _read_downloads()
    now = time.time()
    files_by_dir = {}  # cache directory listings — several entries share a dir

    for d in items:
        # Match against files in the directory this download actually used —
        # not whatever directory happens to be configured right now.
        ddir = d.get("dir") or _download_dir()
        if ddir not in files_by_dir:
            files_by_dir[ddir] = _scan_download_files(ddir)
        matched = _match_download_files(d, files_by_dir[ddir])
        d["files"] = [f["path"] for f in matched]

        with _active_lock:
            live = dict(_active_downloads.get(d.get("id") or "", {}))

        if live.get("downloading"):
            # Detect a stall: process still alive but no forward progress for
            # a while (dead source connection / expired stream token).
            adv = live.get("_advance_ts", 0)
            stalled = bool(adv and now - adv > STALL_SECS)
            d["status"] = "stalled" if stalled else "downloading"
            if live.get("percent") is not None:
                d["percent"] = live["percent"]
            for k in ("speed", "eta", "downloaded", "total"):
                if live.get(k):
                    d[k] = live[k]
            d["progress_bytes"] = sum(f["size"] for f in matched) if matched else 0
            continue

        if "returncode" in live:
            # The downloader finished and told us how it went — trust it.
            if live.get("cancelled"):
                # User stopped a stalled download; the partial file (if any)
                # is usually still watchable, so treat it as a usable result.
                d["status"] = "done" if matched else "failed"
                d["partial"] = bool(matched)
            else:
                d["status"] = "done" if live["returncode"] == 0 else "failed"
            continue

        # No live state — fall back to pid liveness + file presence.
        # NB: signal 0 is a liveness probe only on POSIX. On Windows
        # os.kill() *terminates* the target for any signal, so we skip the
        # probe there and lean on the age/file heuristic instead.
        pid = d.get("pid")
        alive = False
        if pid is not None and os.name == "posix":
            try:
                os.kill(pid, 0)
                alive = True
            except (OSError, ProcessLookupError):
                pass
        try:
            t = time.mktime(time.strptime(d.get("time", ""), "%Y-%m-%d %H:%M"))
            age = now - t
        except (ValueError, OverflowError):
            age = 999

        if alive:
            # Without live progress (e.g. after a server restart) we infer a
            # stall from the file: if it exists but hasn't grown in a while,
            # the downloader is stuck even though its process is still up.
            newest = max((f["mtime"] for f in matched), default=0)
            if matched and now - newest > STALL_SECS:
                d["status"] = "stalled"
            else:
                d["status"] = "downloading"
            d["progress_bytes"] = sum(f["size"] for f in matched) if matched else 0
        elif age < 20:
            # Just queued / just exited — don't flash "failed" prematurely.
            d["status"] = "downloading"
        elif matched:
            d["status"] = "done"
        else:
            d["status"] = "failed"

    return items


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
        # Search the title and pull its recommendations in a single query —
        # AniList lets us nest recommendations under a Media(search:) lookup,
        # so we avoid a second round-trip for the id.
        result = _anilist_post(
            """query ($s: String) { Media(search: $s, type: ANIME) {
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
            {"s": title})

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
    deduplicate, drop anything already watched, and cross-reference with
    AllAnime so we only return titles that actually have episodes in *mode*.

    Both network-heavy phases run in parallel (AniList lookups, then AllAnime
    availability checks) and the final list is cached per history signature —
    a sequential version made 20-30 round-trips and took 10-20s."""
    hist = read_history()
    if not hist:
        return []

    recent = list(reversed(hist[-5:]))
    sig = (mode, tuple(h["id"] for h in recent))
    with _recs_cache_lock:
        c = _recs_cache
        if c["key"] == sig and time.time() - c["ts"] < 1800:
            return c["value"]

    already = {h["id"] for h in hist}
    titles = [clean_title(h["title"]) for h in recent]

    # Phase 1: AniList recommendations for each recent show, in parallel.
    with ThreadPoolExecutor(max_workers=5) as ex:
        rec_lists = list(ex.map(_anilist_recommendations, titles))

    # Build a deduped candidate list, a few top picks per show (AniList already
    # sorts by rating), capped so the availability phase stays bounded.
    seen_names = set()
    candidates = []  # (rname, because, anilist_thumb)
    for because, recs in zip(titles, rec_lists):
        taken = 0
        for rec in recs:
            rname = rec["name"]
            if rname in seen_names:
                continue
            seen_names.add(rname)
            candidates.append((rname, because, rec.get("thumbnail", "")))
            taken += 1
            if taken >= 6:
                break
    candidates = candidates[:24]

    # Phase 2: check AllAnime availability for every candidate in parallel.
    def _check(cand):
        rname, because, anilist_thumb = cand
        try:
            results = search_anime(rname, mode)
        except Exception:
            return None
        if not results:
            return None
        best = results[0]
        if best["id"] in already:
            return None
        thumb = best["thumbnail"]
        if not thumb or "/api/cover?title=" in thumb:
            thumb = anilist_thumb or ""
        return {
            "id": best["id"],
            "name": best["name"],
            "thumbnail": _resolve_thumbnail(thumb, best["name"]),
            "nth": best["nth"],
            "sub": best["sub"],
            "dub": best["dub"],
            "mode": mode,
            "because": because,
        }

    with ThreadPoolExecutor(max_workers=10) as ex:
        checked = list(ex.map(_check, candidates))

    out, seen_ids = [], set()
    for r in checked:
        if r and r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            out.append(r)
        if len(out) >= 12:
            break

    with _recs_cache_lock:
        _recs_cache.update(key=sig, value=out, ts=time.time())
    return out


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
    found = shutil.which("ani-cli")
    if found:
        return found
    # Common locations that may not be on the server's PATH (incl. the dir we
    # install ani-cli into ourselves, ~/.local/bin).
    for cand in ("/opt/homebrew/bin/ani-cli", "/usr/local/bin/ani-cli",
                 os.path.expanduser("~/.local/bin/ani-cli")):
        if os.path.exists(cand):
            return cand
    return None


def install_ani_cli():
    """Install ani-cli if it's missing, without sudo.

    Prefers Homebrew when available (so it stays brew-updatable); otherwise
    downloads the upstream script into a user-writable bin directory and marks
    it executable — the same thing ani-cli's own README tells you to do."""
    existing = ani_cli_path()
    if existing:
        return {"ok": True, "already": True, "path": existing,
                "message": "ani-cli is already installed."}

    # Homebrew route — clean and updatable.
    brew = shutil.which("brew")
    if brew:
        try:
            subprocess.run([brew, "install", "ani-cli"], check=True,
                           capture_output=True, text=True, timeout=900)
            p = ani_cli_path() or shutil.which("ani-cli")
            return {"ok": True, "path": p,
                    "message": "Installed ani-cli with Homebrew."}
        except Exception:
            pass  # fall through to the script download

    # No-sudo script install into a user-writable bin dir (prefer ~/.local/bin,
    # which pipx already puts on PATH).
    local_bin = os.path.expanduser("~/.local/bin")
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if os.path.isdir(local_bin) and os.access(local_bin, os.W_OK):
        target_dir = local_bin
    else:
        writable = [d for d in path_dirs
                    if d and os.path.isdir(d) and os.access(d, os.W_OK)]
        target_dir = writable[0] if writable else local_bin
    try:
        os.makedirs(target_dir, exist_ok=True)
        req = urllib.request.Request(ANI_CLI_RAW, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        target = os.path.join(target_dir, "ani-cli")
        with open(target, "wb") as f:
            f.write(data)
        os.chmod(target, 0o755)
    except Exception as e:
        return {"ok": False, "error": f"Couldn't install ani-cli: {e}"}

    on_path = target_dir in path_dirs
    msg = f"Installed ani-cli to {target}."
    if not on_path:
        msg += (f" Add {target_dir} to your PATH "
                "(e.g. `pipx ensurepath`) and restart ani-gui.")
    return {"ok": True, "path": target, "on_path": on_path, "message": msg}


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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


def _ani_cli_error_detail(out, limit=240):
    """Extract the most useful line from ani-cli's output for the UI.

    ani-cli prints its real failure reason (missing dependency, dead provider,
    bad range, …) to stderr wrapped in ANSI colour codes. We strip those and
    surface the most relevant line so the user sees the actual cause instead of
    a generic 'couldn't resolve a stream'."""
    lines = [ln.strip() for ln in _strip_ansi(out).splitlines() if ln.strip()]
    keywords = ("not found", "please install", "no valid", "not released",
                "connection error", "invalid", "error", "failed", "no such")
    pick = ""
    for ln in reversed(lines):
        low = ln.lower()
        if any(k in low for k in keywords) and "links fetched" not in low:
            pick = ln
            break
    if not pick:
        # Fall back to the last non-progress line.
        pick = next((ln for ln in reversed(lines)
                     if "links fetched" not in ln.lower()), "")
    return pick[:limit]


def play(query, nth, ep, quality, mode, download=False, player="default",
         thumbnail="", title="", show_id=""):
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
    # ani-cli defaults to mpv. If the user picked VLC, or "default" but only VLC
    # is installed (common on Linux), tell ani-cli to use VLC (-v) so playback
    # doesn't die with "Program mpv not found".
    use_vlc = player == "vlc" or (
        player == "default" and not shutil.which("mpv")
        and not shutil.which("iina") and shutil.which("vlc"))
    if use_vlc:
        cmd.append("-v")
    if download:
        cmd.append("-d")
    cmd.append(query)

    env = dict(os.environ)
    # Make sure ani-cli can find the players/curl even if the server was
    # started from a minimal environment. These are the common POSIX brew
    # locations; on Windows ani-cli runs under a shell that has its own PATH,
    # so we leave it untouched there.
    if os.name == "posix":
        extra = os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin",
                                 os.path.expanduser("~/.local/bin")])
        env["PATH"] = extra + os.pathsep + env.get("PATH", "")
    # Use the configured download directory for ani-cli.
    ddir = _download_dir()
    if ddir:
        env["ANI_CLI_DOWNLOAD_DIR"] = ddir

    plabel = "the downloader" if download else ("VLC" if use_vlc else _player_label(player))

    # The download is recorded under the show's *canonical* name (what ani-cli
    # names the file after), falling back to the search query if we weren't
    # given one — so the file-matcher can find it on disk later.
    rec_title = title or query

    if download:
        dl_id = uuid.uuid4().hex
        # Attach the download to a pseudo-terminal so aria2c / yt-dlp / ffmpeg
        # think they're on a TTY and emit their real progress output, which a
        # reader thread parses for live percent/speed/ETA. Falls back to a
        # plain detached process where pty isn't available (e.g. Windows).
        if hasattr(os, "openpty") and os.name == "posix":
            master, slave = os.openpty()
            try:
                import fcntl
                import struct
                import termios
                # Give the pty a sane size; a 0×0 terminal makes some tools
                # suppress or mangle their progress line.
                fcntl.ioctl(slave, termios.TIOCSWINSZ,
                            struct.pack("HHHH", 24, 100, 0, 0))
            except Exception:
                pass
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=slave, stderr=slave,
                                    env=env, start_new_session=True)
            os.close(slave)
            _record_download(rec_title, ep, quality, mode, pid=proc.pid,
                             thumbnail=thumbnail, dl_id=dl_id,
                             query=query, nth=nth, show_id=show_id)
            threading.Thread(target=_watch_download,
                             args=(dl_id, master, proc), daemon=True).start()
        else:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            _record_download(rec_title, ep, quality, mode, pid=proc.pid,
                             thumbnail=thumbnail, dl_id=dl_id,
                             query=query, nth=nth, show_id=show_id)
        return {"ok": True, "stage": "download", "id": dl_id,
                "message": f"Downloading episode {ep} in the background "
                           "(saved to ani-cli's download dir)."}

    # Playback: ani-cli launches the player detached, then exits — so we can
    # capture its output to learn whether a source was found. We run it in its
    # own session so that if it hangs (a dead provider with no curl timeout),
    # we can kill the *whole group* — otherwise its background link-resolution
    # subshells leak as orphaned processes.
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    try:
        out_s, err_s = proc.communicate(timeout=90)
        out = (out_s or "") + "\n" + (err_s or "")
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        proc.wait()
        return {"ok": False, "stage": "slow",
                "error": "This source is taking too long to resolve. "
                         "Try another quality or result."}

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
    if not fetched and proc.returncode != 0:
        detail = _ani_cli_error_detail(out)
        # Most common real cause on a fresh box: ani-cli is missing one of its
        # own dependencies (a player, fzf, openssl, …). Make that actionable.
        m = re.search(r'program "(.+?)" not found', detail, re.I)
        if m:
            return {"ok": False, "stage": "missing_dep",
                    "error": f'ani-cli needs "{m.group(1)}" but it isn\'t '
                             "installed. Install it with your package manager, "
                             "then try again.",
                    "detail": detail}
        return {"ok": False, "stage": "failed",
                "error": ("ani-cli couldn't resolve a stream — " + detail)
                         if detail else
                         "ani-cli couldn't resolve a stream. "
                         "Try another quality or result.",
                "detail": detail}

    # On Linux a missing X11/Wayland display means the player can't open a
    # window even though the stream resolved fine — flag it instead of silently
    # claiming success.
    if _display_problem() == "no_display":
        return {"ok": True, "stage": "no_display",
                "message": f"Stream resolved, but no display is set "
                           "($DISPLAY/$WAYLAND_DISPLAY are empty) so the player "
                           "can't open a window. Start ani-gui from your desktop "
                           "session (not SSH/sudo). See Diagnostics in the footer."}

    msg = f"Playing episode {ep} in {plabel}."
    if fell_back:
        msg = (f"Requested quality wasn't available — playing the best source "
               f"in {plabel}.")
    if use_vlc:
        msg += (" If VLC opens then closes, the stream didn't load in VLC — "
                "install mpv for reliable playback.")
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


def _install_method():
    """Figure out how ani-gui was installed so we can give the right
    upgrade command.  Checks the package's location on disk."""
    import ani_gui as _pkg
    loc = getattr(_pkg, "__file__", "") or ""

    if "/pipx/venvs/" in loc:
        return "pipx"
    if "/Cellar/ani-gui/" in loc or "/homebrew/" in loc:
        return "brew"
    if "site-packages" in loc:
        return "pip"
    if "/ani-gui/ani_gui/" in loc:
        return "source"
    return "unknown"


def _version_newer(a, b):
    """True if version string *b* is strictly newer than *a*."""
    try:
        aa = [int(x) for x in a.split(".")]
        bb = [int(x) for x in b.split(".")]
        # Pad to same length.
        while len(aa) < len(bb):
            aa.append(0)
        while len(bb) < len(aa):
            bb.append(0)
        return bb > aa
    except (ValueError, AttributeError):
        return a != b  # fall back to string compare


def version_info():
    acli_installed = anicli_installed_version()
    acli_latest = anicli_latest_version()
    agui_latest = _anigui_latest_version()
    method = _install_method()
    deps = {name: bool(shutil.which(name))
            for name in ("ani-cli", "mpv", "iina", "vlc", "curl",
                         "yt-dlp", "ffmpeg")}
    has_player = deps["mpv"] or deps["iina"] or deps["vlc"]
    return {
        "ani_gui": VERSION,
        "ani_gui_update": bool(agui_latest and _version_newer(VERSION, agui_latest)),
        "ani_gui_latest": agui_latest or None,
        "install_method": method,
        "ani_cli": {
            "installed": acli_installed,
            "latest": acli_latest,
            "path": ani_cli_path(),
            "update_available": bool(acli_installed and acli_latest
                                     and _version_newer(acli_installed, acli_latest)),
        },
        "deps": deps,
        "has_player": has_player,
    }


def _display_problem():
    """On Linux, GUI players need an X11/Wayland display. If neither is in the
    environment (SSH, sudo, a bare systemd unit), the player can't open a
    window. macOS doesn't use DISPLAY, so this only applies to Linux."""
    if sys.platform.startswith("linux"):
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return "no_display"
    return None


def _tool_report(name):
    p = shutil.which(name)
    if not p:
        return {"found": False}
    ver = ""
    try:
        out = subprocess.run([name, "--version"], capture_output=True,
                             text=True, timeout=8)
        text = (out.stdout or out.stderr or "").strip()
        ver = _strip_ansi(text.splitlines()[0]) if text else ""
    except Exception:
        pass
    return {"found": True, "path": p, "version": ver[:120]}


def diagnostics():
    """A support report: tools, players, display, and likely problems.

    Built to answer 'I clicked play and nothing happened' — it surfaces the
    environment differences (no display, running as root, missing player) that
    silently break ani-cli's detached player launch."""
    import platform
    problems = []

    players = {n: _tool_report(n) for n in ("mpv", "iina", "vlc")}
    has_good = players["mpv"]["found"] or players["iina"]["found"]
    has_any = has_good or players["vlc"]["found"]

    disp = {
        "DISPLAY": os.environ.get("DISPLAY", ""),
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", ""),
        "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE", ""),
    }
    as_root = (os.geteuid() == 0) if hasattr(os, "geteuid") else False

    if not ani_cli_path():
        problems.append("ani-cli isn't installed or isn't on PATH.")
    if not has_any:
        problems.append("No video player found — install mpv.")
    elif not has_good:
        problems.append("Only VLC is installed. ani-cli plays most reliably "
                        "with mpv (VLC often opens then closes). Install mpv.")
    if _display_problem() == "no_display":
        problems.append("No graphical display ($DISPLAY/$WAYLAND_DISPLAY are "
                        "empty) — the player can't open a window. Launch ani-gui "
                        "from your desktop session, not over SSH.")
    if as_root:
        problems.append("ani-gui is running as root — the player may not reach "
                        "your display. Run it as your normal user.")

    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "ani_cli": _tool_report("ani-cli"),
        "players": players,
        "tools": {n: bool(shutil.which(n))
                  for n in ("curl", "ffmpeg", "aria2c", "yt-dlp", "fzf",
                            "openssl")},
        "display": disp,
        "running_as_root": as_root,
        "problems": problems,
        "ok": not problems,
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
                return self._send(200, {"episodes": episodes_list(sid, mode),
                                        "watched": watched_episode(sid)})
            if u.path == "/api/continue":
                mode = q.get("mode", ["sub"])[0]
                return self._send(200, {"items": continue_watching(mode)})
            if u.path == "/api/recommendations":
                mode = q.get("mode", ["sub"])[0]
                return self._send(200, {"items": recommendations(mode)})
            if u.path == "/api/downloads":
                return self._send(200, {"items": _downloads_with_status()})
            if u.path == "/api/settings":
                return self._send(200, _read_settings())
            if u.path == "/api/browse-dir":
                # Open a native folder picker and return the chosen path.
                try:
                    import platform
                    system = platform.system()
                    path = ""
                    if system == "Darwin":
                        # choose folder is a StandardAdditions command —
                        # no System Events permission needed.
                        script = (
                            'set f to choose folder with prompt '
                            '"Choose download directory for ani-gui"\n'
                            'POSIX path of f')
                        out = subprocess.run(
                            ["osascript", "-e", script],
                            capture_output=True, text=True, timeout=300)
                        path = out.stdout.strip() if out.returncode == 0 else ""
                    elif system == "Windows":
                        import tempfile
                        ps = os.path.join(tempfile.gettempdir(),
                                          "ani_gui_picker.ps1")
                        with open(ps, "w") as f:
                            f.write(
                                'Add-Type -AssemblyName System.Windows.Forms\n'
                                '$f = New-Object System.Windows.Forms.FolderBrowserDialog\n'
                                '$f.Description = "Choose download directory for ani-gui"\n'
                                'if ($f.ShowDialog() -eq "OK") { $f.SelectedPath }\n')
                        out = subprocess.run(
                            ["powershell", "-ExecutionPolicy", "Bypass",
                             "-File", ps],
                            capture_output=True, text=True, timeout=60)
                        path = out.stdout.strip()
                        try:
                            os.unlink(ps)
                        except OSError:
                            pass
                    else:
                        # Linux: try zenity, kdialog, then fall back.
                        for cmd in (["zenity", "--file-selection",
                                     "--directory",
                                     "--title=Choose download directory for ani-gui"],
                                    ["kdialog", "--getexistingdirectory"]):
                            if shutil.which(cmd[0]):
                                out = subprocess.run(
                                    cmd, capture_output=True, text=True,
                                    timeout=60)
                                path = out.stdout.strip()
                                if path:
                                    break
                    return self._send(200, {"path": path})
                except Exception as e:
                    return self._send(500, {"error": str(e)})
            if u.path == "/api/version":
                return self._send(200, version_info())
            if u.path == "/api/diagnostics":
                return self._send(200, diagnostics())
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
                    download=bool(body.get("download")),
                    thumbnail=body.get("thumbnail", ""),
                    title=body.get("title", ""),
                    show_id=body.get("id", ""))
                return self._send(200 if res.get("ok") else 502, res)
            if u.path == "/api/cancel-download":
                res = cancel_download(body.get("id", ""))
                return self._send(200 if res.get("ok") else 404, res)
            if u.path == "/api/retry-download":
                res = retry_download(body.get("id", ""))
                return self._send(200 if res.get("ok") else 502, res)
            if u.path == "/api/install-ani-cli":
                res = install_ani_cli()
                return self._send(200 if res.get("ok") else 500, res)
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
                           download=bool(body.get("download")),
                           title=title)
                return self._send(200 if res.get("ok") else 502, res)
            if u.path == "/api/settings":
                s = _read_settings()
                if "download_dir" in body:
                    s["download_dir"] = body["download_dir"]
                _write_settings(s)
                return self._send(200, s)
            if u.path == "/api/play-file":
                path = body.get("path", "")
                player = body.get("player", "default")
                if not path or not os.path.isfile(path):
                    return self._send(404, {"error": "file not found"})
                # Pick the right player binary.
                if player == "vlc" and shutil.which("vlc"):
                    cmd = ["vlc", path]
                elif shutil.which("iina"):
                    cmd = ["iina", "--no-stdin", path]
                elif shutil.which("mpv"):
                    cmd = ["mpv", path]
                elif shutil.which("vlc"):
                    cmd = ["vlc", path]
                elif os.name == "nt":
                    # No player binary found — hand off to the OS default.
                    # `start` is a cmd builtin, not an executable, so Popen
                    # can't run it directly; os.startfile is the right API.
                    os.startfile(path)  # noqa: B606  (Windows-only)
                    return self._send(200, {"ok": True,
                                            "message": f"Opening {os.path.basename(path)}"})
                else:
                    # Last resort on macOS / Linux: let the OS pick.
                    import platform
                    cmd = ["open", path] if platform.system() == "Darwin" \
                        else ["xdg-open", path]
                subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return self._send(200, {"ok": True,
                                        "message": f"Opening {os.path.basename(path)}"})
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
    ap.add_argument("--install-ani-cli", action="store_true",
                    help="install ani-cli (if missing) and exit")
    ap.add_argument("-V", "--version", action="version",
                    version=f"ani-gui {VERSION}")
    args = ap.parse_args(argv)

    if args.install_ani_cli:
        res = install_ani_cli()
        print(res.get("message") or res.get("error"),
              file=sys.stderr if not res.get("ok") else sys.stdout)
        return 0 if res.get("ok") else 1

    if not ani_cli_path():
        print("warning: ani-cli not found on PATH — run `ani-gui --install-ani-cli` "
              "or install it from https://github.com/pystardust/ani-cli",
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
