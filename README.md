# O Store

Patched Android apps, built automatically by GitHub Actions from **stable** patch releases
(Morphe and others), published on GitHub Releases and shown on a GitHub Pages store.

- **Store:** https://omichrome.github.io/OStore/
- **arm64-v8a APKs only**, no root modules.
- **Cloned apps first:** builds get a new package name so they install next to the original app.
  If an app cannot be cloned, a normal ("Standalone") build is published instead.

## How it works

```
every 2 days ──► Build workflow
                   1. plan     : which apps have new stable patches (or a changed config)?
                   2. engine   : download j-hc's builder at the pinned commit (engine.lock)
                   3. build    : download original APK, check its real signature, patch, sign
                   4. verify   : clone check (package name), our signing key, patches applied
                   5. release  : GitHub release with the APK, meta.json and the build log
                   6. store    : Store workflow turns the releases into site/data.json
```

| Folder / file | What it is |
|---|---|
| `apps/*.toml` | One file per app. This is the only thing you ever change. |
| `engine.lock` | The j-hc builder commit we trust. Moves forward by itself. |
| `keystore/` | The O Store signing key (see "Signing key" below). |
| `scripts/ostore.py` | All the logic (plan, build, release, store data, issues). |
| `site/` | The store page (plain HTML/JS, no build step). |
| `state/` | What was built last, and where each app is downloaded from. Written by the bot. |

## Adding an app (copy, paste, submit)

1. Open the store's **Add app** page.
2. Paste the patch source link, e.g. `https://morphe.software/add-source?github=MorpheApp/morphe-patches`
   (from [morphe-patches.software](https://morphe-patches.software/) or [awesome-morphe](https://awesome-morphe.pages.dev/)).
3. Paste the package name, e.g. `com.google.android.youtube`. The preview shows the icons so you can confirm.
4. Press **Submit on GitHub**, then **Create** on the issue page.

The `Add app` workflow writes `apps/<id>.toml`, starts the first build and closes the issue.
Only issues from the repo owner are accepted. You can also add or edit a file in `apps/` by hand.

The builder finds the version itself (the newest app version the patches mark as **stable**, never
experimental or beta) and finds the download (APKMirror or archive.org).

## Staying alive without maintenance

- **Upstream code is linked, not copied.** The builder is [j-hc/revanced-magisk-module](https://github.com/j-hc/revanced-magisk-module),
  used at the commit in `engine.lock`. Morphe CLI and patches are always fetched fresh from their stable releases.
- **Engine update (weekly):** tries the newest j-hc code with a test YouTube build. Passes → pin moves forward.
  Fails → pin stays, an issue says so, nothing breaks.
- **Self-healing builds:** if a build fails on the pinned engine, it is retried once with the newest upstream engine.
  If that works, the pin moves forward.
- **Download sources** are looked up again automatically if a download stops working.
- **Maintenance (weekly):** checks that the store and its download links work, deletes releases older than
  6 months (the newest build of every app is always kept), and makes a heartbeat commit if the repo has been quiet for 40 days
  (GitHub pauses schedules after 60 quiet days).
- **Free:** the repo is public, so GitHub Actions minutes are free. A check with nothing new takes seconds.

## When something breaks (logs)

Every failure opens or updates a GitHub issue with the label **`build-failure`**:
the app, the step that failed, the error, the engine commit, the last log lines and a link to the run.
It closes itself after the next good build. Each release also carries its full `build-log.txt`.

| Issue title | Meaning |
|---|---|
| `Build failed: <app>` | That app could not be built. Other apps are not affected. |
| `Engine update blocked` | Newest j-hc code failed the test build; still using the older one. |
| `Store update failed` / `Store health check failed` | The store page or a download link has a problem. |
| `Build workflow crashed` | The workflow itself broke (not a single app). |

To have an AI agent fix it: "read the open `build-failure` issues in OmiChrome/OStore and fix them".

## Signing key

All APKs are signed with `keystore/ostore.keystore` (BKS, alias `jhc`, password `123456789`, the
values the j-hc engine expects). Its certificate SHA-256 is in `keystore/fingerprint.txt`, and every
build is checked against it, so a build signed with any other key is never published.
**Never replace this key:** installed apps can only update when the key stays the same.
The key is public in this repo (same as j-hc does), which is fine for a personal store.

## Run it locally

```bash
python3 scripts/ostore.py engine --dir engine
python3 scripts/ostore.py build youtube-morpheapp --engine engine
```

Needs Java 21, `jq`, `zip`, `curl`. Output goes to `out/<app>/`.

## Credits

[j-hc](https://github.com/j-hc/revanced-magisk-module) (builder engine), [MorpheApp](https://github.com/MorpheApp)
(patches, CLI, MicroG-RE), [awesome-morphe](https://github.com/nvbangg/awesome-morphe) (app icons and patch index),
ideas from [nullcpy/rvb](https://github.com/nullcpy/rvb) and [FiorenMas](https://github.com/FiorenMas/Revanced-And-Revanced-Extended-Non-Root).
Patches belong to their authors. Licensed GPL-3.0.
