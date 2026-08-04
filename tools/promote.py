#!/usr/bin/env python3
"""Promote entries from the test catalog into this (official) one.

NeoVitaDB-Catalog-Test takes pull requests with a much lower bar than this
repo - entries sit there first, and only get promoted here by hand once
they've proven themselves. This script does the mechanical half of that: it
diffs apps/vita/*.json and apps/psp/*.json between a local checkout of the
test catalog and this repo, copies over whatever entries exist only in the
test catalog (plus their icon), and opens a PR here with the result. It never
merges anything itself - the actual trust decision (which entries to promote)
is made by whoever invokes it, via --ids, before this ever runs.

Usage:
    python tools/promote.py --dry-run                 # see what's new, touch nothing
    python tools/promote.py                            # promote every new entry, open a PR
    python tools/promote.py --ids 1801 1802             # promote only these ids
    python tools/promote.py --test-repo /path/to/checkout
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLATFORMS = ["vita", "psp"]
ICON_DIRS = {"vita": "icons_vita", "psp": "icons_psp"}
REPO_ROOT = Path(__file__).resolve().parent.parent


def find_entries(repo_root: Path, platform: str) -> dict[int, Path]:
    """id -> file path, for every non-template entry under apps/<platform>/."""
    entries: dict[int, Path] = {}
    d = repo_root / "apps" / platform
    for f in sorted(d.glob("*.json")):
        if f.name.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            print(f"warning: skipping unparsable {f}: {e}", file=sys.stderr)
            continue
        entries[data["id"]] = f
    return entries


def run(cmd: list[str], cwd: Path) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--test-repo",
        default=str(REPO_ROOT.parent / "NeoVitaDB-Catalog-Test"),
        help="Path to a local checkout of NeoVitaDB-Catalog-Test (default: sibling directory)",
    )
    ap.add_argument(
        "--ids",
        type=int,
        nargs="*",
        help="Only consider these ids. Without this, every new entry is promoted.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be promoted without touching git or opening a PR",
    )
    args = ap.parse_args()

    test_repo = Path(args.test_repo).resolve()
    if not test_repo.is_dir():
        sys.exit(f"test repo not found at {test_repo} - pass --test-repo")

    to_promote: list[tuple[str, Path, Path, int]] = []  # platform, official_path, test_path, id
    skipped_collisions: list[tuple[str, int, str, str]] = []

    for platform in PLATFORMS:
        official = find_entries(REPO_ROOT, platform)
        test = find_entries(test_repo, platform)
        for tid, tpath in test.items():
            if args.ids is not None and tid not in args.ids:
                continue
            official_path = REPO_ROOT / "apps" / platform / tpath.name
            if official_path.exists():
                continue  # already promoted, or independently added here too
            if tid in official:
                # Same id already used by a *different* file here - the two
                # catalogs' id counters drifted independently. Needs a human
                # to renumber one side; never silently pick a winner.
                skipped_collisions.append((platform, tid, tpath.name, official[tid].name))
                continue
            to_promote.append((platform, official_path, tpath, tid))

    if skipped_collisions:
        print("Skipped - id already used by a different entry here (renumber one side by hand):")
        for platform, tid, test_name, official_name in skipped_collisions:
            print(f"  [{platform}] id {tid}: test has {test_name}, official already has {official_name}")
        print()

    if not to_promote:
        print("Nothing new to promote.")
        return

    print(f"Promoting {len(to_promote)} entr{'y' if len(to_promote) == 1 else 'ies'}:")
    for platform, _official_path, test_path, tid in to_promote:
        print(f"  [{platform}] id {tid}: {test_path.name}")

    if args.dry_run:
        return

    branch = "promote-" + "-".join(f"{platform}{tid}" for platform, _, _, tid in to_promote)[:60]
    run(["git", "checkout", "main"], cwd=REPO_ROOT)
    run(["git", "pull"], cwd=REPO_ROOT)
    run(["git", "checkout", "-b", branch], cwd=REPO_ROOT)

    for platform, official_path, test_path, tid in to_promote:
        official_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(test_path, official_path)
        icon_name = test_path.stem + ".png"
        test_icon = test_repo / ICON_DIRS[platform] / icon_name
        official_icon = REPO_ROOT / ICON_DIRS[platform] / icon_name
        if test_icon.exists() and not official_icon.exists():
            shutil.copyfile(test_icon, official_icon)

    run(["git", "add", "apps", "icons_vita", "icons_psp"], cwd=REPO_ROOT)
    names = ", ".join(test_path.name for _, _, test_path, _ in to_promote)
    commit_msg = f"Promote from test catalog: {names}"
    run(["git", "commit", "-m", commit_msg], cwd=REPO_ROOT)
    run(["git", "push", "-u", "origin", branch], cwd=REPO_ROOT)

    body_lines = ["Promoted from NeoVitaDB-Catalog-Test:", ""]
    for platform, _official_path, test_path, tid in to_promote:
        body_lines.append(f"- [{platform}] id {tid}: `{test_path.name}`")
    run(
        ["gh", "pr", "create", "--title", commit_msg, "--body", "\n".join(body_lines)],
        cwd=REPO_ROOT,
    )


if __name__ == "__main__":
    main()
