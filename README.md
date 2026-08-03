# NeoVitaDB Catalog

The homebrew list behind [NeoVitaDB Downloader](https://github.com/robin994/NeoVitaDB-Downloader).

There is no server. This repository *is* the database: every homebrew is one small
JSON file pointing at the GitHub repository that publishes it. A scheduled workflow
resolves each project's latest release and generates the static files the app
downloads. Binaries stay with their authors — the catalog only ever stores metadata.

Published at `https://robin994.github.io/NeoVitaDB-Catalog/`.

## Adding your homebrew

Entries live under `apps/vita/` or `apps/psp/`, kept separate for tidiness.

1. Copy `apps/vita/_template.json` (or `apps/psp/_template.json` for a PSP
   homebrew) to `apps/<platform>/NNNN-your-slug.json`, using the next free id
   *within that platform's own folder* - vita and psp each have their own id
   space, so you only need to check the folder you're adding to.
2. Add a 128×128 PNG icon under `icons_vita/` or `icons_psp/` (matching
   platform), named `NNNN-your-slug.png`.
3. Open a pull request. CI validates your entry and fails the check if something
   is off, so you get the answer without waiting for a human.

You only describe the project. Version, release date, download size, download
count, checksums and the download URL all come from your latest GitHub release —
publish a new release and the catalog follows within six hours. PSP entries
also get `folder` derived automatically — the top-level folder EBOOT.PBP sits
in inside your release archive, e.g. "APOLLO" — since that's where Adrenaline
expects the game installed (`ux0:pspemu/PSP/GAME/APOLLO/`); nothing to set by
hand for it.

The one exception is `trusted`: unlike the fields above, it *is* written into
your `apps/<platform>/NNNN-your-slug.json`, but not by you. Every build
recomputes it from your repository's GitHub star count (currently more than
50) and, when it changes, rewrites the field in place and commits it back —
same mechanism as `cache/hashes.json`. Setting it yourself in a PR has no
lasting effect; the next scheduled build overwrites it with the real star
count.

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

**`id` is permanent, and only unique within its own platform.** It is the
catalog's primary key: the app writes it into the user's favourites file.
Never reuse an id, never renumber an existing entry. `apps/vita/` and
`apps/psp/` each have their own id space, so a vita entry and a psp entry may
legitimately share the same number - CI only rejects a collision within the
same platform.

**`titleid` is exactly 9 uppercase alphanumerics** and must match what your VPK
actually installs. Two homebrew sharing a title id can only have one installed at
a time, and the app warns users about it.

**Nightly builds should not be listed as releases.** The app decides "update
available" by comparing checksums, so a project that publishes a build every night
will nag users daily. Keep `prerelease` false and tag real releases.

**`direct_url` pins an entry to one download instead of "latest release +
`asset` glob".** Useful when a repo publishes several unrelated projects in one
release (the glob would risk matching a sibling's asset), tags releases in a way
the normal resolution mishandles, or you deliberately want to freeze on a known-good
build instead of always tracking latest. Two cases:

- **A `https://github.com/{repo}/releases/download/{tag}/{file}` URL, for this
  entry's own `repo`.** The build still fetches that exact tagged release to read
  version/date/changelog/download count - identical fidelity to the normal path,
  just pinned to `{tag}` instead of "latest". `asset`/`prerelease` are ignored.
- **Any other URL** (a `raw.githubusercontent.com` link, another repo's asset,
  a third-party host). There's no release for the build to introspect, so:
  - `version` becomes a **required, contributor-maintained field** - bump it by
    hand whenever you update `direct_url`. This is the one field that flips from
    build-derived to contributor-owned, and only for this case. (It's cosmetic
    only: the app's actual "outdated" detection compares checksums, not this
    string, so a stale `version` never causes a functional bug - just a
    misleading label until you update it.)
  - `date` is whichever day the build last noticed the file, not the true
    publish date; `changelog` is always empty; `downloads` stays 0 (untrackable
    without a release to read a count from).
  - The build appends a warning to the end of the displayed description,
    telling users this download is hosted on an external server rather than
    GitHub's own release infrastructure and may stop working without notice.
    You don't write this yourself - it's added automatically, only for this
    sub-case (the pinned-release sub-case above stays on GitHub, so it doesn't
    get one).

Either way, `hash`/`hash2`/`size` are still computed for real by downloading and
inspecting `direct_url` itself, so update detection works exactly like any other
entry. The app shows a "Direct Download" badge on any entry using `direct_url`,
in either sub-case.

> **⚠️ It is strictly forbidden to use `direct_url` (or any other field) to link
> pirated material** — copyrighted commercial games, ROMs, BIOS files, or any
> other content you don't have the right to redistribute. This catalog only
> ever indexes freely-distributable homebrew published by its own author. A PR
> linking infringing content will be rejected and the contributor blocked; if
> infringing content is ever found already merged, it will be removed on sight.
> Repeated or ignored violations risk a DMCA takedown against this entire
> repository and its GitHub Pages hosting, which would shut the catalog down
> for every contributor and user — not just the offending entry. When in
> doubt about a project's licensing, don't add it.

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

1. Validates every `apps/vita/*.json` and `apps/psp/*.json` against
   `schema/app.schema.json`, and rejects duplicate ids within the same
   platform or duplicate icon names.
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
