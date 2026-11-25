# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

O Store builds patched Android apps (arm64-v8a APKs only, no root modules) from **stable** patch releases (Morphe and other GitHub patch bundles), publishes them as GitHub Releases, and shows them on a GitHub Pages store. Cloned apps (new package name, installed next to the original) are the priority; if cloning isn't possible, a "standalone" build is published instead. The owner adds apps without changing code: one `apps/<id>.toml` file per app.

There is no build system, test suite or linter. The code is Python (standard library only, needs 3.11+ for `tomllib`), bash and a plain HTML/JS site.

## Commands

```bash
# Fetch the builder engine at the pinned commit (or --sha HEAD for upstream main)
python3 scripts/ostore.py engine --dir engine

# Build one app end to end (output: out/<id>/ with the APK, meta.json, build.log, or error.json)
python3 scripts/ostore.py build youtube-morpheapp --engine engine

# Which apps would build (needs GitHub API access)
python3 scripts/ostore.py plan [--force] [--only "id1 id2"]

# Store data from releases -> site/data.json + site/lookup.json; preview the site
python3 scripts/ostore.py catalog --out site/data.json
cd site && python3 -m http.server 8000

# Read the package name and version from any APK
python3 scripts/axml.py some.apk

# Check workflow YAML after editing
python3 -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/**/*.yml',recursive=True)]"
```

A local build needs Java 21, `jq`, `zip` and `curl`. Run `python3 scripts/ostore.py -h` for all subcommands.

## Architecture

**The builder engine is not in this repo.** `engine.lock` pins a commit of [j-hc/revanced-magisk-module](https://github.com/j-hc/revanced-magisk-module). `ostore.py engine` fetches that commit into `engine/` and overwrites the engine's `ks.keystore` with ours. Don't copy engine code into this repo. Work around engine behavior from the outside: generate its config, read its output and logs.

- **Self-updating pin:** `scripts/build-all.sh` retries failed apps once with upstream HEAD and moves the pin if that works. `engine-update.yml` runs a weekly test build (canary) on HEAD and moves the pin only if it passes.

**One app build** (`cmd_build` in `scripts/ostore.py`):

1. `resolve_sources`: finds download URLs and caches them in `state/sources.json`. Order: `apkmirror`/`archive` fields in the app's toml, the cache, j-hc's archive.org mirror, then NullStore's maintained `nullcpy/rvb` `data` branch configs (`known_sources`, fetched with git, not the API). APKMirror's own search is last because it returns 403 to bots.
   - **Stock APK inbox:** `inbox_restore` reads files the owner uploaded to the `stock-apks` release (asset name must start with the package name) and identifies each one with `apk_identity`. A `.apkm` is fed to j-hc through an offline `direct-dlurl` (`file:///<pkg>-<ver>-arm64-v8a.apkm`) so j-hc merges it and checks its signature. When no source has the version, `missing_stock_hint` makes the failure issue say exactly what to upload. `build-all.sh` creates the release when it is missing; this session can't create releases.
   - **Stock APK cache:** `cache_restore` and `cache_store` keep one original APK (plus its `.apkm`) per app in `apk-cache/`. That folder is saved with `actions/cache`, keyed by a hash of its file names; older cache keys are deleted. Never cache `engine/temp/*-rv`: with `latest`, j-hc reuses a local patches or CLI file without checking for a newer release.
2. `write_engine_config`: writes a j-hc TOML with `arch="arm64-v8a"`, `version="auto"` (newest *stable*, i.e. non-experimental, target of the patches), `patches-version="latest"` (latest non-prerelease release) and `pkg-name`.
3. Runs `bash build.sh` in the engine with stdin set to `/dev/null`. This matters: j-hc's `build.sh` can block on `cat` reading stdin.
4. **Extra patches, found after the first engine run** (up to 3 runs, the stock APK stays cached):
   - **Clone:** reads the APK manifest with `axml.py`. If the package didn't change, `find_clone_patch` picks the bundle's own rename patch (`Clone` in Piko, the universal `Clone app` in Morphe) and the app is rebuilt. The name is remembered as `clone_patch` in `state/sources.json`. For YouTube, the GmsCore/MicroG patch already renames the package to `app.morphe.android.youtube`.
   - **`all-patches = true`:** patches the log reports as `Skipping disabled` are turned on, but only those `list-patches` shows as made for this app. Universal ones such as "Spoof signature" stay off.
5. **Signature guard:** the APK's certificate must match `keystore/fingerprint.txt`, otherwise the build fails.
6. **Patch lists** come from the engine log lines `INFO: Applied: X` and `INFO: Skipping disabled: X`. Descriptions and the experimental versions skipped come from `morphe-cli list-patches -p -v [-x]`, run on the jar and `.mpp` file the engine cached in `engine/temp/<owner>-rv/`.
7. Writes `out/<id>/meta.json`. That file is the data contract with the site.

**Data flow:**
- `plan` writes `out/plan.json` (the patch tag of each app) and is the input to `build`.
- `build-all.sh` releases each app right after its build and starts `pages.yml` each time. Pages runs queue (`cancel-in-progress: false`), and deploy is retried because the artifact sometimes isn't listed yet.
- `release` uploads the APK, `meta.json` and `build-log.txt` as a GitHub release tagged `<id>-v<version>-<patches>-<stamp>`. `cleanup` relies on the `<id>-v<digit>` prefix of that tag.
- An app whose original APK is missing is recorded in `state/waiting.json` together with the inbox file names at that time. `plan` skips it until the `stock-apks` inbox changes, or until it's requested with `--only`/`--force`. `record` clears it.
- `record` updates `state/built.json`. The next `plan` compares its `patches_tag` and `config_hash` to decide what to rebuild.
- `catalog` turns all releases' `meta.json` files into `site/data.json`. It also writes `site/lookup.json`, a compact app and bundle index from awesome-morphe's `bundles.json` that the Add page preview uses.
- **Releases are the source of truth for the store.** `site/data.json` and `site/lookup.json` are generated and ignored by git.

**Workflows** (`.github/workflows/`):
- `build.yml` (cron every 2 days) → `pages.yml` (deploys `site/`)
- `add-app.yml`: an issue titled "Add app" or labelled `add-app`, opened by the repo owner, is parsed by `ostore.py add-app` into `apps/<id>.toml`, then the build is dispatched.
- `engine-update.yml` (weekly canary)
- `maintenance.yml` (health check, deleting releases older than 180 days, heartbeat commit after 40 quiet days)
- The workflows that write `state/`, `apps/` or `engine.lock` share the concurrency group `state-writer`. They commit through `scripts/commit.sh`, which retries the push after a rebase.

**Failure logging:** failures open or update a GitHub issue labelled `build-failure` (`issue-fail`), using the step, message and log tail from `out/<id>/error.json`. The next success closes it (`issue-ok`). Titles: `Build failed: <id>`, `Engine update blocked`, `Store update failed`, `Store health check failed`, `Build workflow crashed`. To debug, read these issues first. Each release also has its full `build-log.txt`.

**Site** (`site/`): vanilla JS, hash routes `#/`, `#/app/<id>`, `#/add`, `#/about`. Escape all data with `esc()`: patch names and descriptions come from third-party bundles. Colors are CSS tokens with a dark-mode override. The Add page builds a pre-filled issue URL; issue form field ids (`patch_source`, `package`, `app_name`, `type`) must match `.github/ISSUE_TEMPLATE/add-app.yml`, and its headings must match `parse_issue_form` (case-insensitive).

## Rules that matter

- `signers.txt` pins the real developer certificate of an original app. `ostore.py engine` appends it to j-hc's `sig.txt`, so j-hc's `check_sig` refuses re-signed copies. Verify a new pin against an independent copy (for example archive.org) before adding it. NullStore's `nullcpy/apks` mirror re-signs merged APKs with its own key, so it is not a usable source. Don't try to get past APKMirror/Cloudflare bot protection.

- **Never change or regenerate `keystore/ostore.keystore`.** Installed apps can only update while the key stays the same. Its alias and password (`jhc` / `123456789`) are what j-hc hardcodes.
- Only GitHub patch sources are supported, because the engine reads GitHub releases only.
- Never build beyond stable: no `version="experimental"`, no prerelease patches.
- In workflow YAML, a value containing `: ` must be in a block scalar (`run: |`).
