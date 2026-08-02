# NeoVitaDB Catalog

The homebrew list behind [NeoVitaDB Downloader](https://github.com/robin994/NeoVitaDB-Downloader).

There is no server. This repository *is* the database: every homebrew is one small
JSON file pointing at the GitHub repository that publishes it. A scheduled workflow
resolves each project's latest release and generates the static files the app
downloads. Binaries stay with their authors — the catalog only ever stores metadata.

Published at `https://robin994.github.io/NeoVitaDB-Catalog/`.

## Adding your homebrew

1. Copy `apps/_template.json` to `apps/NNNN-your-slug.json`, using the next free id.
2. Add a 128×128 PNG icon under `icons/`, named `NNNN-your-slug.png`.
3. Open a pull request. CI validates your entry and fails the check if something
   is off, so you get the answer without waiting for a human.

You only describe the project. Version, release date, download size, download
count, checksums and the download URL all come from your latest GitHub release —
publish a new release and the catalog follows within six hours.

The one exception is `trusted`: unlike the fields above, it *is* written into
your `apps/NNNN-your-slug.json`, but not by you. Every build recomputes it from
your repository's GitHub star count (currently more than 50) and, when it
changes, rewrites the field in place and commits it back — same mechanism as
`cache/hashes.json`. Setting it yourself in a PR has no lasting effect; the next
scheduled build overwrites it with the real star count.

```json
{
  "id": 42,
  "name": "My Homebrew",
  "author": "Your Name",
  "category": "game",
  "platform": "vita",
  "titleid": "MYHB00001",
  "repo": "youruser/your-repo",
  "asset": "*.vpk",
  "icon": "0042-my-homebrew.png",
  "description": "What it does."
}
```

### Rules worth knowing before you write the file

**`id` is permanent.** It is the catalog's primary key: the app writes it into the
user's favourites file. Never reuse an id, never renumber an existing entry.

**`titleid` is exactly 9 uppercase alphanumerics** and must match what your VPK
actually installs. Two homebrew sharing a title id can only have one installed at
a time, and the app warns users about it.

**Nightly builds should not be listed as releases.** The app decides "update
available" by comparing checksums, so a project that publishes a build every night
will nag users daily. Keep `prerelease` false and tag real releases.

**Some characters are reserved.** The parser on the console is not a JSON parser:
it scans for `"key": "` and cuts at the next quote. So `requirements` cannot
contain quotes at all, and `description` cannot contain the two-character sequence
`",`. CI rejects both.

## Where the initial entries came from

The catalog was seeded from a VitaDB dump with `tools/import_vitadb.py`. VitaDB
stores the *resolved* result — a download URL behind its own redirector, plus a
version, size and checksum computed by a server nobody else can run. This
catalog stores the input instead, so the import keeps only what a contributor
would have written by hand and drops everything the build recomputes.

An entry survives the import only if its GitHub repository still publishes a
stable release with a `.vpk` asset. Ids are carried over unchanged, so an entry
here has the same id it had on VitaDB.

Screenshots and trailers are *not* imported: the dump references files under
VitaDB's own web root, which this catalog does not host. Re-add them per entry
once the images live under `screenshots/` here.

```bash
python tools/import_vitadb.py --report-only    # resolve, write nothing
python tools/import_vitadb.py                  # write apps/ and icons/
```

The tool never touches an id that already exists, so re-running it is safe and
only picks up projects that have since published a usable release.

## How the build works

`tools/build_catalog.py`, on a schedule and on every push:

1. Validates `apps/*.json` against `schema/app.schema.json`, and rejects duplicate
   ids or icon names.
2. Asks the GitHub API for each project's latest release and picks the asset
   matching `asset`.
3. Downloads that asset, opens it as a zip, and computes the MD5 of `eboot.bin`.
   For Unity, GameMaker, Godot, LuaPlayer, LifeLua and YoYo Loader projects the
   loader is identical across releases, so the engine's main asset is checksummed
   too. This is what lets the app recognise an outdated homebrew even when it was
   installed by hand rather than through the store.
4. Emits `dist/vita.json`, `dist/psp.json` and `dist/icons.db`, and publishes them
   to GitHub Pages.

Checksums are cached in `cache/hashes.json`, keyed by asset id and upload time, so
a scheduled run only downloads what actually changed.

### Why the output looks the way it does

`dist/*.json` has a rigid shape: fixed key order, every value a string, `hash2`
present only for Vita entries. That is not a stylistic choice — it is what
`get_value_from_json()` in the app's `source/database.cpp` requires. `FIELD_ORDER`
in the build script is the authoritative list; changing it without changing the app
breaks the catalog silently, which is exactly the failure mode the schema and the
CI check exist to prevent.

## Forking this catalog

Forking this repo to run your own catalog (for NeoVitaDB Downloader's in-app catalog switcher)
needs one manual step the workflow can't do for you: **enable GitHub Pages on your fork**
(Settings → Pages → Source: GitHub Actions). `dist/` is gitignored — it only ever exists as the
build workflow's output, never committed — so without Pages enabled nothing serves it anywhere,
not even `raw.githubusercontent.com`. The build will keep succeeding either way; your fork just
stays unreachable to any client pointed at it until Pages is turned on.

Once Pages is enabled, the existing `build.yml` (runs on push, on a schedule, and via
`workflow_dispatch`) starts publishing to `https://<your-username>.github.io/NeoVitaDB-Catalog/`
automatically — that's the URL to give NeoVitaDB Downloader's `catalogs.cfg`.

## Local build

```bash
pip install jsonschema
GITHUB_TOKEN=$(gh auth token) python tools/build_catalog.py
```

Without a token you share the anonymous API budget of 60 calls an hour, which runs
out quickly once the catalog grows.
