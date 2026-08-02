#!/usr/bin/env python3
"""Build the on-device catalog from apps/*.json.

For every entry this resolves the latest GitHub release, picks the asset, and
derives the fields the app cannot compute for itself: version, date, size,
download count, the MD5 checksums used for update detection, and a trust flag
from the repository's star count.

The output format is dictated by the on-device parser, which is not a JSON
parser: get_value_from_json() in source/database.cpp walks the text with strstr
looking for `"key": "` and cuts at the next quote. So the key order below is
load-bearing, every value is a string, and two fields have reserved
terminators. See FIELD_ORDER and emit_entry.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
DIST_DIR = ROOT / "dist"
CACHE_FILE = ROOT / "cache" / "hashes.json"
DOWNLOADS_CACHE_FILE = ROOT / "cache" / "downloads.json"
SCHEMA_FILE = ROOT / "schema" / "app.schema.json"
CATEGORIES_FILE = ROOT / "categories.json"

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Order is fixed: the parser reads these sequentially. hash2 is vita-only.
# New fields are appended at the very end deliberately (see "trusted", then
# "folder"): an app build that predates a field simply stops reading before
# it and is unaffected, whereas inserting one earlier would shift every field
# the old parser reads afterwards. A future app release can start reading a
# new field once it lands.
FIELD_ORDER = [
    "name", "icon", "version", "author", "type", "id", "date", "titleid",
    "screenshots", "long_description", "downloads", "source", "release_page",
    "trailer", "size", "data_size", "hash", "hash2", "requirements",
    "trophies", "ai", "data", "url", "changelog", "trusted", "folder",
]

# A repo needs more stars than this to be flagged trusted.
TRUSTED_STARS = 50

# Engine loaders keep eboot.bin identical across releases, so the app also
# checksums the engine's main asset. Kept in sync with aux_main_files in
# source/database.cpp.
AUX_FILES = [
    "Media/sharedassets0.assets.resS",   # Unity
    "games/game.win",                    # GameMaker Studio
    "index.lua",                         # LuaPlayer Plus Vita
    "main.lua",                          # LifeLua
    "game.apk",                          # YoYo Loader
    "game_data/game.pck",                # Godot
]


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def api_get(path: str) -> dict | list:
    req = urllib.request.Request(API + path)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "NeoVitaDB-Catalog")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "NeoVitaDB-Catalog")
    if TOKEN and url.startswith(API):
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def repo_stars(repo: str) -> int:
    try:
        info = api_get(f"/repos/{repo}")
    except urllib.error.HTTPError as e:
        log(f"  ! {repo}: repo info unavailable ({e.code})")
        return 0
    return info.get("stargazers_count", 0)


def head_size(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "NeoVitaDB-Catalog")
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get("Content-Length") or 0)
    except (urllib.error.URLError, ValueError):
        return 0


def pick_release(repo: str, allow_prerelease: bool) -> dict | None:
    """Latest published release, honouring the prerelease preference."""
    try:
        releases = api_get(f"/repos/{repo}/releases?per_page=20")
    except urllib.error.HTTPError as e:
        log(f"  ! {repo}: releases unavailable ({e.code})")
        return None
    for rel in releases:
        if rel.get("draft"):
            continue
        if rel.get("prerelease") and not allow_prerelease:
            continue
        return rel
    return None


def pick_asset(release: dict, pattern: str) -> dict | None:
    """Match case-insensitively: some authors publish `.VPK`, and the
    extension's case carries no meaning worth rejecting a release over."""
    for asset in release.get("assets", []):
        if fnmatch.fnmatch(asset["name"].lower(), pattern.lower()):
            return asset
    return None


def checksums(vpk_bytes: bytes, platform: str = "vita") -> tuple[str, str, str]:
    """MD5 of the loader executable, plus (vita only) the engine asset when
    the loader has one, plus (psp only) the folder the executable lives in.

    Mirrors what the app computes on device, so a freshly installed homebrew
    compares equal and shows as up to date. Vita expects eboot.bin at the zip
    root; PSP releases instead wrap EBOOT.PBP in a top-level folder (the
    game's actual name, e.g. "APOLLO/EBOOT.PBP") that the app extracts as-is
    into ux0:pspemu/PSP/GAME/ - see the "folder" field this returns, matched
    by basename rather than full path since that folder name is arbitrary
    per release and not something a fixed lookup key could anticipate.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(vpk_bytes))
    except zipfile.BadZipFile:
        log("  ! asset is not a zip; cannot derive checksums")
        return "", "", ""

    lookup = {n.lower().lstrip("./"): n for n in zf.namelist()}

    if platform == "psp":
        executable = next(
            (real for path_lower, real in lookup.items() if path_lower.rsplit("/", 1)[-1] == "eboot.pbp"),
            None,
        )
        if not executable:
            log("  ! no EBOOT.PBP inside the asset")
            return "", "", ""
        if "/" not in executable:
            # No wrapping folder to preserve on install - the app's PSP
            # install path always needs one (see main.cpp), so this asset
            # isn't installable as-is rather than something worth guessing a
            # folder name for.
            log("  ! EBOOT.PBP is at the zip root, no folder to install it under")
            return "", "", ""
        main_hash = hashlib.md5(zf.read(executable)).hexdigest()
        folder = executable.rsplit("/", 1)[0]
        return main_hash, "", folder

    main_hash, aux_hash = "", ""
    eboot = lookup.get("eboot.bin")
    if eboot:
        main_hash = hashlib.md5(zf.read(eboot)).hexdigest()
    else:
        log("  ! no eboot.bin inside the asset")

    for candidate in AUX_FILES:
        real = lookup.get(candidate.lower())
        if real:
            aux_hash = hashlib.md5(zf.read(real)).hexdigest()
            break

    return main_hash, aux_hash, ""


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def load_downloads_cache() -> dict:
    if DOWNLOADS_CACHE_FILE.exists():
        return json.loads(DOWNLOADS_CACHE_FILE.read_text())
    return {}


def save_downloads_cache(cache: dict) -> None:
    DOWNLOADS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def accumulate_downloads(cache: dict, app_id: str, tag: str, release_downloads: int) -> int:
    """Running lifetime total, built up across builds instead of re-summing
    every release's assets every time (which would need one API call per
    release instead of one per app - see the "conteggio dei download" thread).

    GitHub's own download_count is per-release: it only ever reflects the
    currently published release's assets, and resets to 0 the moment a repo's
    latest release changes. So each build just needs to remember, per app,
    what the *previous* release's final count was, add it to a running base
    the first time a version change is noticed, and keep tracking the current
    release's count on top of that base until the version changes again.

    This under-counts anything downloaded before an app was first seen by
    this cache (no retroactive history), and the "freeze" amount for a
    release that just got superseded is only as fresh as the last build
    before the change, not the exact moment it happened - both acceptable
    given the alternative is paginating every release of every app.
    """
    prev = cache.get(app_id)
    if prev is None:
        base_total = 0
    elif prev["tag"] != tag:
        base_total = prev["base_total"] + prev["release_downloads"]
    else:
        base_total = prev["base_total"]
    cache[app_id] = {"tag": tag, "release_downloads": release_downloads, "base_total": base_total}
    return base_total + release_downloads


def validate(entries: list[dict]) -> None:
    try:
        import jsonschema
    except ImportError:
        log("! jsonschema not installed, skipping validation")
        return
    schema = json.loads(SCHEMA_FILE.read_text())
    seen_ids, seen_icons = {}, {}
    for path, entry in entries:
        payload = {k: v for k, v in entry.items() if not k.startswith("$")}
        try:
            jsonschema.validate(payload, schema)
        except jsonschema.ValidationError as e:
            raise SystemExit(f"{path.name}: {e.message}")
        if entry["id"] in seen_ids:
            raise SystemExit(
                f"{path.name}: id {entry['id']} already used by {seen_ids[entry['id']]}"
            )
        seen_ids[entry["id"]] = path.name
        if entry["icon"] in seen_icons:
            raise SystemExit(
                f"{path.name}: icon {entry['icon']} already used by {seen_icons[entry['icon']]}"
            )
        seen_icons[entry["icon"]] = path.name


def emit_entry(fields: dict, is_psp: bool) -> str:
    """Serialise one entry with the exact key order the device parser needs."""
    parts = []
    for key in FIELD_ORDER:
        if key == "hash2" and is_psp:
            continue
        value = fields.get(key, "")
        parts.append(f"    {json.dumps(key)}: {json.dumps(str(value))}")
    return "  {\n" + ",\n".join(parts) + "\n  }"


def build() -> None:
    categories = {
        c["slug"]: c["type"]
        for c in json.loads(CATEGORIES_FILE.read_text())["categories"]
    }

    entries = []
    # Recursive: entries live under apps/vita/ or apps/psp/, kept separate
    # for tidiness.
    for path in sorted(APPS_DIR.glob("**/*.json")):
        if path.name.startswith("_"):
            continue
        entries.append((path, json.loads(path.read_text())))

    validate(entries)
    log(f"{len(entries)} entries")

    cache = load_cache()
    downloads_cache = load_downloads_cache()
    out = {"vita": [], "psp": []}
    icons = []

    for path, entry in entries:
        repo = entry["repo"]
        log(f"- {entry['id']:04d} {entry['name']} ({repo})")

        release = pick_release(repo, entry.get("prerelease", False))
        if not release:
            log("  ! skipped: no usable release")
            continue

        asset = pick_asset(release, entry.get("asset", "*.vpk"))
        if not asset:
            log(f"  ! skipped: no asset matching {entry.get('asset', '*.vpk')}")
            continue

        key = f"{repo}@{asset['id']}@{asset['updated_at']}"
        if key in cache:
            main_hash, aux_hash, folder = cache[key]["hash"], cache[key]["hash2"], cache[key].get("folder", "")
            log("  cached")
        else:
            log(f"  downloading {asset['name']} ({asset['size']} bytes)")
            main_hash, aux_hash, folder = checksums(fetch(asset["browser_download_url"]), entry["platform"])
            cache[key] = {"hash": main_hash, "hash2": aux_hash, "folder": folder}

        published = release.get("published_at") or release.get("created_at") or ""
        date = published[:10] if published else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        release_downloads = sum(a.get("download_count", 0) for a in release.get("assets", []))
        lifetime_downloads = accumulate_downloads(
            downloads_cache, str(entry["id"]), release.get("tag_name", ""), release_downloads
        )

        is_psp = entry["platform"] == "psp"
        type_num = categories[entry["category"]] + (10 if is_psp else 0)

        data_url = entry.get("data", "")
        trusted = repo_stars(repo) > TRUSTED_STARS
        if "trusted" not in entry or entry["trusted"] != trusted:
            entry["trusted"] = trusted
            path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
            log(f"  trusted -> {trusted}")

        fields = {
            "name": entry["name"],
            "icon": entry["icon"],
            "version": release.get("tag_name", ""),
            "author": entry["author"],
            "type": str(type_num),
            "id": str(entry["id"]),
            "date": date,
            "titleid": entry.get("titleid", ""),
            "screenshots": ";".join(entry.get("screenshots", [])),
            "long_description": entry["description"],
            "downloads": str(lifetime_downloads),
            "source": release.get("html_url", "").rsplit("/releases/", 1)[0],
            "release_page": release.get("html_url", ""),
            "trailer": entry.get("trailer", ""),
            "size": str(asset["size"]),
            "data_size": str(head_size(data_url) if data_url else 0),
            "hash": main_hash,
            "hash2": aux_hash,
            "requirements": entry.get("requirements", ""),
            "trophies": "1" if entry.get("trophies") else "0",
            "ai": "1" if entry.get("ai") else "0",
            "data": data_url,
            "url": asset["browser_download_url"],
            "changelog": sanitise_changelog(release.get("body") or ""),
            "trusted": "1" if trusted else "0",
            "folder": folder,
        }

        out["psp" if is_psp else "vita"].append(emit_entry(fields, is_psp))
        icons.append(entry["icon"])

    save_cache(cache)
    save_downloads_cache(downloads_cache)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    for platform, blocks in out.items():
        target = DIST_DIR / f"{platform}.json"
        body = "[\n" + ",\n".join(blocks) + "\n]\n" if blocks else "[]\n"
        target.write_text(body)
        log(f"wrote {target.relative_to(ROOT)} ({len(blocks)} entries)")

    (DIST_DIR / "icons.db").write_text("".join(f"{i}\n" for i in sorted(icons)))
    log(f"wrote dist/icons.db ({len(icons)} icons)")


def sanitise_changelog(body: str) -> str:
    """Release notes, trimmed to what the device parser can swallow.

    The parser terminates this field on the two-character sequence quote-comma,
    so that sequence is removed rather than escaped.
    """
    text = re.sub(r"\r\n?", "\n", body).strip()
    text = text.replace('",', "', ")
    return text[:4000]


if __name__ == "__main__":
    build()
