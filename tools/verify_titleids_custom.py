#!/usr/bin/env python3
"""Cross-check every vita catalog entry's "titleid" against the TITLE_ID
actually embedded in its VPK's sce_sys/param.sfo - without downloading the
VPK. HTTP Range requests fetch only the ZIP central directory and the
param.sfo member itself (a few KB, regardless of how large the asset is),
the same trick zipfile already needs to open any zip that isn't fully
buffered in memory.

Usage:
    python tools/verify_titleids.py            # check every vita entry
    python tools/verify_titleids.py 1447 1980   # only these catalog ids
"""

from __future__ import annotations

import io
import json
import struct
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalog import (  # noqa: E402
    APPS_DIR,
    fetch_release_by_tag,
    parse_github_release_asset_url,
    pick_release_with_asset,
)

USER_AGENT = "NeoVitaDB-Catalog-titleid-check"
WORKERS = 12


class HTTPRangeFile(io.RawIOBase):
    """Seekable file-like object backed by HTTP Range requests - just
    enough for zipfile to read a ZIP's central directory and pull out one
    member without ever fetching the rest of the asset."""

    def __init__(self, url: str):
        self.url = url
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self) -> int:
        return self.pos

    def readinto(self, b) -> int:
        end = min(self.pos + len(b), self.size) - 1
        if self.pos > end:
            return 0
        req = urllib.request.Request(
            self.url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={self.pos}-{end}"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def resolve_download_url(entry: dict) -> str | None:
    """Same asset-resolution precedence as build_catalog.process_entry(),
    minus the hashing/star-count work that function also does."""
    repo = entry.get("repo", "")
    direct_url = entry.get("direct_url")
    pinned = parse_github_release_asset_url(direct_url) if direct_url else None
    if direct_url and pinned and pinned[0] == repo:
        _, tag, filename = pinned
        release = fetch_release_by_tag(repo, tag)
        asset = next((a for a in (release or {}).get("assets", []) if a["name"] == filename), None)
        return asset["browser_download_url"] if asset else None
    if direct_url:
        return direct_url
    _, asset = pick_release_with_asset(repo, entry.get("prerelease", False), entry.get("asset", "*.vpk"))
    return asset["browser_download_url"] if asset else None


def parse_sfo_titleid(data: bytes) -> str | None:
    if len(data) < 20 or struct.unpack_from("<I", data)[0] != 0x46535000:
        return None
    _magic, _version, key_off, val_off, count = struct.unpack_from("<5I", data)
    for i in range(count):
        name_off, _align, _vtype, vsize, _total, data_off = struct.unpack_from(
            "<HBBIII", data, 20 + i * 16
        )
        end = data.index(b"\x00", key_off + name_off)
        key = data[key_off + name_off : end].decode("ascii", "replace")
        if key == "TITLE_ID":
            raw = data[val_off + data_off : val_off + data_off + vsize]
            return raw.rstrip(b"\x00").decode("ascii", "replace")
    return None


def read_titleid_from_zip(zf: zipfile.ZipFile) -> str | None:
    lookup = {n.lower().lstrip("./"): n for n in zf.namelist()}
    sfo_name = lookup.get("sce_sys/param.sfo")
    if not sfo_name:
        # Wrapper zip (README/LICENSE alongside the real .vpk) - mirrors
        # build_catalog.checksums()'s unwrap, so the same asset shape is
        # accepted here too. Only this nested member gets fully fetched,
        # not the rest of the wrapper.
        nested = next((real for path_lower, real in lookup.items() if path_lower.endswith(".vpk")), None)
        if not nested:
            return None
        with zipfile.ZipFile(io.BytesIO(zf.read(nested))) as inner:
            return read_titleid_from_zip(inner)
    return parse_sfo_titleid(zf.read(sfo_name))


def check_entry(path: Path) -> dict:
    entry = json.loads(path.read_text())
    out = {"path": path, "entry": entry}
    try:
        url = resolve_download_url(entry)
        if not url:
            out["error"] = "could not resolve a download URL"
            return out
        out["url"] = url
        with zipfile.ZipFile(HTTPRangeFile(url)) as zf:
            titleid = read_titleid_from_zip(zf)
        if titleid is None:
            out["error"] = "no TITLE_ID found in param.sfo"
        elif titleid != entry["titleid"]:
            out["mismatch"] = titleid
    except zipfile.BadZipFile:
        out["error"] = "asset is not a zip"
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        out["error"] = f"network error: {e}"
    return out


def main() -> None:
    ids = {int(a) for a in sys.argv[1:]} if len(sys.argv) > 1 else None
    paths = sorted(p for p in APPS_DIR.glob("vita/*.json") if not p.name.startswith("_"))
    if ids:
        paths = [p for p in paths if json.loads(p.read_text())["id"] in ids]

    mismatches, errors = [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, result in enumerate(pool.map(check_entry, paths), 1):
            entry = result["entry"]
            print(f"[{i}/{len(paths)}] {entry['id']:04d} {entry['name']}", file=sys.stderr)
            if "mismatch" in result:
                mismatches.append(result)
            elif "error" in result:
                errors.append(result)

    print(f"\n=== {len(mismatches)} titleid mismatch(es) ===")
    for r in mismatches:
        e = r["entry"]
        print(f"{r['path'].name}: catalog={e['titleid']!r} actual={r['mismatch']!r}  ({e.get('repo') or r.get('url')})")

    print(f"\n=== {len(errors)} entr(y/ies) could not be checked ===")
    for r in errors:
        e = r["entry"]
        print(f"{r['path'].name}: {r['error']}  ({e.get('repo') or 'no repo'})")


if __name__ == "__main__":
    main()
