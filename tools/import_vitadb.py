#!/usr/bin/env python3
"""Seed apps/ from a VitaDB catalog dump.

VitaDB stores the resolved output: a download URL behind its own redirector, a
version string, a size and a checksum, all produced by a server nobody else can
run. This catalog stores the input instead — the repository that publishes the
homebrew — and derives the rest at build time. So the import is a narrowing:
everything the build script can recompute is dropped, and an entry survives only
if the GitHub release it needs actually exists.

Fields deliberately not carried over:

  url, version, size, date, downloads, hash, hash2, source, release_page
      recomputed by tools/build_catalog.py from the release.
  status, tags, score, description
      not in FIELD_ORDER; the on-device parser never reads them.
  screenshots, trailer
      paths into VitaDB's own web root. The image and video files are not part
      of the dump, so keeping them would publish links to files this catalog
      does not host.

Usage:
    python tools/import_vitadb.py [--dump sampleData/VitaDB] [--limit N]
    python tools/import_vitadb.py --report-only    # resolve, write nothing
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
# icons_vita/ publishes to dist/icons/ (the path already-published clients
# expect); icons_psp/ gets its own served path - see .github/workflows/build.yml.
ICONS_DIR = {"vita": ROOT / "icons_vita", "psp": ROOT / "icons_psp"}
CACHE_FILE = ROOT / "cache" / "vitadb_releases.json"

API = "https://api.github.com"

# VitaDB's numeric type, which is the same numbering categories.json uses.
# PSP entries in the dump carry the Vita type +10 (11/12/14/15) - the same
# offset build_catalog.py adds back when it writes "type" for a psp entry -
# so normalise_type() below strips it before this lookup runs.
TYPE_TO_CATEGORY = {"1": "game", "2": "port", "4": "utility", "5": "emulator"}

# One dump file per platform; both share the same icons/ folder (VitaDB kept
# a single icon store regardless of platform).
DUMP_FILE = {"vita": "apps.json", "psp": "psp_apps.json"}


def normalise_type(type_str: str, platform: str) -> str:
    if platform == "psp":
        return str(int(type_str) - 10)
    return type_str

# Two entries in the dump carry a title id the schema rejects. Both are typos
# in VitaDB rather than what the VPK installs, so they are corrected here.
TITLEID_FIXES = {
    "REDVITASS ": "REDVITASS",   # trailing space
    "WLFN0001": "WLFN00001",     # eight characters, padded to nine
}

session_headers = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "NeoVitaDB-Catalog-import",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    try:
        return subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def api_get(path: str):
    req = urllib.request.Request(API + path, headers=session_headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "app")[:48]


def derive_repo(entry: dict) -> str | None:
    """owner/name from whichever VitaDB link points at GitHub."""
    for field in ("source", "release_page"):
        m = re.match(
            r"https?://(?:www\.)?github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)",
            entry.get(field) or "",
        )
        if m:
            return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    return None


def sanitise_description(entry: dict) -> str:
    """Prefer the long text; strip the sequence that terminates the field."""
    text = (entry.get("long_description") or entry.get("description") or "").strip()
    text = re.sub(r"\r\n?", "\n", text).replace('",', "', ")
    return text[:4000] or entry["name"]


def sanitise_requirements(entry: dict) -> str:
    """The on-device parser cuts this field at the first quote, so drop quotes."""
    text = re.sub(r"\r\n?", "\n", entry.get("requirements") or "").strip()
    return text.replace('"', "").replace("\\", "")[:1000]


def resolve(repo: str) -> dict:
    """What the build script will find later: a stable release with a VPK -
    or, failing that, a zip whose contents build_catalog.py's checksums()
    can unwrap a VPK from (a wrapper zip alongside a README/changelog, or a
    PSP release with EBOOT.PBP under its own top-level folder)."""
    try:
        releases = api_get(f"/repos/{repo}/releases?per_page=20")
    except urllib.error.HTTPError as e:
        return {"ok": False, "reason": f"repo unreachable (HTTP {e.code})"}
    except urllib.error.URLError as e:
        return {"ok": False, "reason": f"repo unreachable ({e.reason})"}

    stable = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    if not stable:
        reason = "only prereleases" if releases else "no releases"
        return {"ok": False, "reason": reason}

    release = stable[0]
    assets = [a["name"] for a in release.get("assets", [])]
    vpks = [a for a in assets if a.lower().endswith(".vpk")]
    if vpks:
        return {"ok": True, "tag": release.get("tag_name", ""), "vpks": vpks, "ext": ".vpk"}
    zips = [a for a in assets if a.lower().endswith(".zip")]
    if zips:
        return {"ok": True, "tag": release.get("tag_name", ""), "vpks": zips, "ext": ".zip"}
    return {"ok": False, "reason": "latest release has no .vpk asset"}


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def asset_glob(vpks: list[str], name: str, ext: str = ".vpk") -> str:
    """A glob for the entry's own VPK, as loose as it can be without ambiguity.

    One VPK is the common case and needs no narrowing. Releases that ship
    several are the reason this is fiddly: sibling projects built from one
    repository (pSNES/pGEN/pNES, Sonic 1/Sonic 2) all land in the same release,
    so a glob that matches its neighbour would publish the wrong homebrew.
    Looseness only matters for surviving the next version bump, so it is traded
    away the moment it costs uniqueness — the build script picks the *first*
    matching asset, which is not a coin flip worth taking.

    ext is ".zip" instead of ".vpk" when resolve() only found a wrapper zip
    (see its docstring) - the glob still needs to match that zip, not a VPK
    filename that was never published as its own asset.
    """
    if len(vpks) == 1:
        return f"*{ext}"

    target = pick_vpk(vpks, name, ext)
    stem = target[: -len(ext)]
    candidates = [
        # Every digit group is version noise.
        re.sub(r"[0-9]+(?:[._-][0-9]+)*", "*", stem),
        # Only the groups that read as a version: v1.2, 1.2.3, -v110.
        re.sub(r"(?<=[vV])[0-9]+(?:[._][0-9]+)*|[0-9]+(?:\.[0-9]+)+", "*", stem),
        stem,
    ]
    for candidate in candidates:
        glob = re.sub(r"\*{2,}", "*", candidate + "*") + ext
        if sum(fnmatch.fnmatch(v, glob) for v in vpks) == 1:
            return glob
    return target


def pick_vpk(vpks: list[str], name: str, ext: str = ".vpk") -> str:
    """The asset whose file name reads most like the homebrew's name."""
    def normalise(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    wanted = normalise(name)
    return max(vpks, key=lambda v: difflib.SequenceMatcher(None, wanted, normalise(v[: -len(ext)])).ratio())


def build_entry(src: dict, repo: str, vpks: list[str], platform: str, ext: str = ".vpk") -> dict:
    app_id = int(src["id"])
    slug = slugify(src["name"])
    stem = f"{app_id:04d}-{slug}"
    entry = {
        "id": app_id,
        "name": src["name"],
        "author": src["author"] or "Unknown",
        "category": TYPE_TO_CATEGORY[normalise_type(src["type"], platform)],
        "platform": platform,
        "titleid": TITLEID_FIXES.get(src["titleid"], src["titleid"]),
        "repo": repo,
        "asset": asset_glob(vpks, src["name"], ext),
        "prerelease": False,
        "icon": f"{stem}.png",
        "description": sanitise_description(src),
        "requirements": sanitise_requirements(src),
        "screenshots": [],
        "trophies": src.get("trophies") == "1",
        "ai": src.get("ai") == "1",
    }
    if src.get("data"):
        entry["data"] = src["data"]
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="sampleData/VitaDB", help="folder holding the dump file(s)")
    ap.add_argument("--platform", choices=["vita", "psp"], default="vita", help="which dump file to import")
    ap.add_argument("--limit", type=int, default=0, help="stop after N candidates")
    ap.add_argument("--report-only", action="store_true", help="resolve but write nothing")
    ap.add_argument("--skipped-report", help="write the entries left out, grouped by reason")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    dump = (ROOT / args.dump) if not Path(args.dump).is_absolute() else Path(args.dump)
    source = json.loads((dump / DUMP_FILE[args.platform]).read_text())
    log(f"{len(source)} entries in the {args.platform} dump")

    token = github_token()
    if token:
        session_headers["Authorization"] = f"Bearer {token}"
    else:
        log("! no GitHub token: 60 requests an hour, this will not get far")

    # Ids are only unique within their own platform (see build_catalog.py's
    # validate()), so a psp import must only avoid ids already taken under
    # apps/psp/, not apps/vita/ too.
    taken_ids = set()
    # VitaDB assigns its own internal ids, so the same GitHub project can show
    # up in the dump under an id this catalog hasn't used yet even though the
    # project itself was already imported (or added by hand) earlier - the id
    # check alone doesn't catch that. Dedup by repo too (see issue #19: the
    # REALPACKA duplicate of LiEnby/real-package-installer got in this way).
    taken_repos = set()
    for path in (APPS_DIR / args.platform).glob("*.json"):
        if not path.name.startswith("_"):
            existing = json.loads(path.read_text())
            taken_ids.add(existing["id"])
            if existing.get("repo"):
                taken_repos.add(existing["repo"].lower())

    skipped: dict[str, list[str]] = {}

    def skip(reason: str, entry: dict) -> None:
        skipped.setdefault(reason, []).append(f"{entry['id']} {entry['name']}")

    candidates = []
    for entry in source:
        if normalise_type(entry["type"], args.platform) not in TYPE_TO_CATEGORY:
            skip("unknown category", entry)
            continue
        titleid = TITLEID_FIXES.get(entry["titleid"], entry["titleid"])
        if not re.fullmatch(r"[A-Z0-9]{9}", titleid or ""):
            skip("unusable title id", entry)
            continue
        if int(entry["id"]) in taken_ids:
            skip("id already in the catalog", entry)
            continue
        repo = derive_repo(entry)
        if not repo:
            skip("not published on GitHub", entry)
            continue
        if repo.lower() in taken_repos:
            skip("repo already in the catalog", entry)
            continue
        taken_repos.add(repo.lower())
        candidates.append((entry, repo))

    if args.limit:
        candidates = candidates[: args.limit]
    log(f"{len(candidates)} candidates to resolve against the GitHub API")

    cache = load_cache()
    todo = sorted({repo for _, repo in candidates} - cache.keys())
    if todo:
        log(f"resolving {len(todo)} repositories ({len(cache)} already cached)")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for repo, result in zip(todo, pool.map(resolve, todo)):
                cache[repo] = result
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")

    written = 0
    for entry, repo in candidates:
        result = cache[repo]
        if not result["ok"]:
            skip(result["reason"], entry)
            continue
        app = build_entry(entry, repo, result["vpks"], args.platform, result.get("ext", ".vpk"))
        if args.report_only:
            written += 1
            continue
        stem = app["icon"][:-4]
        icon_src = dump / "icons" / entry["icon"][:2] / entry["icon"]
        if not icon_src.exists():
            skip("icon missing from the dump", entry)
            continue
        icons_dir = ICONS_DIR[args.platform]
        icons_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(icon_src, icons_dir / app["icon"])
        # apps/vita/ and apps/psp/ - kept separate for tidiness.
        target_dir = APPS_DIR / args.platform
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"{stem}.json").write_text(json.dumps(app, indent=2, ensure_ascii=False) + "\n")
        written += 1

    verb = "would import" if args.report_only else "imported"
    log(f"\n{verb} {written} entries")
    for reason, names in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
        log(f"  skipped {len(names):4d}  {reason}")

    if args.skipped_report:
        target = Path(args.skipped_report)
        target.write_text(json.dumps(skipped, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        log(f"wrote {target}")


if __name__ == "__main__":
    main()
