#!/usr/bin/env python3
"""O Store helper: plans builds, drives the j-hc engine, writes store data.

Everything the workflows do goes through here, so the same steps can be run
locally. Only the Python standard library is used.

Subcommands
  plan            which apps need a build (new stable patches or changed config)
  engine          fetch the j-hc builder at a commit and install our keystore
  build           build one app with the engine, verify it, write out/<id>/
  release         publish out/<id>/ as a GitHub release
  record          remember what was built in state/built.json
  catalog         make site/data.json from the GitHub releases
  cleanup         delete releases older than N days (keeps newest per app)
  add-app         turn an "Add app" issue into apps/<id>.toml
  issue-fail      open or update the failure issue for an app
  issue-ok        close the failure issue for an app after a good build
  health          check that the published store and its downloads work
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from axml import manifest_info  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APPS_DIR = ROOT / "apps"
STATE_DIR = ROOT / "state"
OUT_DIR = ROOT / "out"
ARCH = "arm64-v8a"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:108.0) Gecko/20100101 Firefox/108.0"
AWESOME_BUNDLES = "https://raw.githubusercontent.com/nvbangg/awesome-morphe/main/web/public/bundles.json"
FAIL_LABEL = "build-failure"


# ----------------------------------------------------------------- helpers

def log(msg):
    print(f"[ostore] {msg}", file=sys.stderr, flush=True)


def gh_token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def repo_slug():
    return os.environ.get("GITHUB_REPOSITORY", "OmiChrome/OStore")


def http(url, *, data=None, method=None, headers=None, api=False, timeout=60):
    h = {"User-Agent": UA}
    if api:
        h["Accept"] = "application/vnd.github+json"
        if gh_token():
            h["Authorization"] = f"Bearer {gh_token()}"
    h.update(headers or {})
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def api(path, **kw):
    url = path if path.startswith("http") else f"https://api.github.com/{path.lstrip('/')}"
    raw = http(url, api=True, **kw)
    return json.loads(raw) if raw else None


def api_paged(path):
    out, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        chunk = api(f"{path}{sep}per_page=100&page={page}")
        if not chunk:
            return out
        out.extend(chunk)
        if len(chunk) < 100:
            return out
        page += 1


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def gh_output(key, value):
    path = os.environ.get("GITHUB_OUTPUT")
    line = f"{key}={value}"
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")
    print(line)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ----------------------------------------------------------------- apps

def parse_source(text):
    """Accept the link styles people paste. Returns 'owner/repo' on GitHub."""
    text = text.strip()
    q = urllib.parse.urlparse(text)
    params = urllib.parse.parse_qs(q.query)
    if "github" in params:
        return params["github"][0].strip("/")
    if "gitlab" in params or "codeberg" in params or "gitlab.com" in q.netloc or "codeberg.org" in q.netloc:
        raise ValueError("Only GitHub patch sources are supported right now (GitLab/Codeberg are not).")
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)", text)
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    m = re.match(r"^([\w.-]+)/([\w.-]+)$", text)
    if m:
        return text
    raise ValueError(f"Could not read a GitHub repo from: {text!r}")


def load_apps():
    apps = {}
    for f in sorted(APPS_DIR.glob("*.toml")):
        cfg = tomllib.loads(f.read_text())
        cfg["id"] = f.stem
        cfg.setdefault("mode", "clone")
        cfg.setdefault("enabled", True)
        cfg["patches"] = parse_source(cfg["patches"])
        apps[f.stem] = cfg
    return apps


def config_hash(cfg):
    keep = {k: v for k, v in cfg.items() if k not in ("id",)}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def latest_stable(repo):
    """Latest non-prerelease GitHub release of a patch source."""
    rel = api(f"repos/{repo}/releases/latest")
    return {"tag": rel["tag_name"], "url": rel["html_url"], "published": rel["published_at"]}


# ----------------------------------------------------------------- plan

def cmd_plan(a):
    apps = load_apps()
    built = read_json(STATE_DIR / "built.json", {})
    only = {x for x in (a.only or "").replace(",", " ").split() if x}
    todo, reasons, cache = [], {}, {}
    for app_id, cfg in apps.items():
        if only and app_id not in only:
            continue
        if not cfg["enabled"]:
            continue
        src = cfg["patches"]
        try:
            cache.setdefault(src, latest_stable(src))
        except Exception as e:  # a broken source must not stop the others
            log(f"{app_id}: cannot read releases of {src}: {e}")
            reasons[app_id] = f"source error: {e}"
            todo.append(app_id)
            continue
        prev = built.get(app_id, {})
        if a.force or only:
            why = "requested"
        elif prev.get("patches_tag") != cache[src]["tag"]:
            why = f"new stable patches {prev.get('patches_tag', '-')} -> {cache[src]['tag']}"
        elif prev.get("config_hash") != config_hash(cfg):
            why = "app config changed"
        else:
            continue
        todo.append(app_id)
        reasons[app_id] = why
    for k, v in reasons.items():
        log(f"build {k}: {v}")
    if not todo:
        log("nothing to build")
    write_json(OUT_DIR / "plan.json", {k: {"reason": reasons[k], **cache.get(apps[k]["patches"], {})} for k in todo})
    gh_output("apps", " ".join(todo))
    gh_output("count", str(len(todo)))


# ----------------------------------------------------------------- engine

def cmd_engine(a):
    lock = dict(l.split("=", 1) for l in (ROOT / "engine.lock").read_text().split() if "=" in l)
    repo = lock["JHC_REPO"]
    sha = a.sha or lock["JHC_COMMIT"]
    url = f"https://github.com/{repo}"
    if sha == "HEAD":
        out = subprocess.check_output(["git", "ls-remote", url, "refs/heads/main"], text=True)
        sha = out.split()[0]
    d = Path(a.dir)
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    run = lambda *c: subprocess.check_call(["git", "-C", str(d), *c])  # noqa: E731
    run("init", "-q")
    run("fetch", "-q", "--depth", "1", url, sha)
    run("checkout", "-q", "FETCH_HEAD")
    # Sign with our own key, not the one that ships with the engine.
    shutil.copy(ROOT / "keystore" / "ostore.keystore", d / "ks.keystore")
    log(f"engine {repo}@{sha[:10]} ready in {d}")
    gh_output("sha", sha)


# ----------------------------------------------------------------- download sources

def _get(url):
    try:
        return http(url, timeout=40).decode("utf-8", "replace")
    except Exception as e:
        log(f"GET {url} failed: {e}")
        return ""


def apkmirror_pkg(html):
    m = re.search(r'id=([\w.]+)" class="accent_color', html)
    return m.group(1) if m else None


def find_apkmirror(pkg):
    html = _get(f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s={pkg}")
    seen = []
    for dev, app in re.findall(r'href="/apk/([\w-]+)/([\w-]+)/[\w-]*-release/"', html):
        url = f"https://www.apkmirror.com/apk/{dev}/{app}"
        if url not in seen:
            seen.append(url)
    for url in seen[:6]:
        if apkmirror_pkg(_get(url + "/")) == pkg:
            return url
    return None


def find_archive(pkg):
    url = f"https://archive.org/download/jhc-apks/apks/{pkg}"
    html = _get(url + "/")
    return url if ".apk" in html else None


def resolve_sources(cfg, refresh=False):
    """Where to download the original app. Cached in state/sources.json."""
    cache = read_json(STATE_DIR / "sources.json", {})
    pkg = cfg["package"]
    found = {} if refresh else dict(cache.get(pkg, {}))
    for key, finder in (("apkmirror", find_apkmirror), ("archive", find_archive)):
        if cfg.get(key):
            found[key] = cfg[key]
        elif not found.get(key):
            hit = finder(pkg)
            if hit:
                found[key] = hit
    if not found:
        raise RuntimeError(f"No download source found for {pkg} (tried APKMirror and archive.org). "
                           f"Add apkmirror = \"https://www.apkmirror.com/apk/<dev>/<app>\" to apps/{cfg['id']}.toml")
    cache[pkg] = {**cache.get(pkg, {}), **found}
    write_json(STATE_DIR / "sources.json", cache)
    return found


# ----------------------------------------------------------------- build

def toml_str(v):
    return json.dumps(v)


def write_engine_config(cfg, sources, path, extra):
    included = " ".join([cfg.get("included-patches", "")] + [f'"{n}"' for n in extra]).strip()
    lines = [
        "parallel-jobs = 1",
        "enable-module-update = false",
        f"patches-source = {toml_str(cfg['patches'])}",
        f"cli-source = {toml_str(cfg.get('cli', 'MorpheApp/morphe-cli'))}",
        "patches-version = \"latest\"",
        "",
        f"[{toml_str(cfg['id'])}]",
        f"app-name = {toml_str(cfg['id'])}",
        "build-mode = \"apk\"",
        f"arch = {toml_str(ARCH)}",
        "version = \"auto\"",
        "enable-update-checks = false",
        f"pkg-name = {toml_str(cfg['package'])}",
    ]
    if "apkmirror" in sources:
        lines.append(f"apkmirror-dlurl = {toml_str(sources['apkmirror'])}")
    if "archive" in sources:
        lines.append(f"archive-dlurl = {toml_str(sources['archive'])}")
    if cfg.get("dpi"):
        lines.append(f"dpi = {toml_str(cfg['dpi'])}")
    if included:
        lines.append(f"included-patches = {toml_str(included)}")
    if cfg.get("excluded-patches"):
        lines.append(f"excluded-patches = {toml_str(cfg['excluded-patches'])}")
    Path(path).write_text("\n".join(lines) + "\n")


def run_engine(engine, conf_name, log_path):
    for d in ("build",):
        shutil.rmtree(engine / d, ignore_errors=True)
    with open(log_path, "a") as lf:
        lf.write(f"\n===== engine run {dt.datetime.now(dt.UTC).isoformat()} =====\n")
        lf.flush()
        p = subprocess.run(["bash", "build.sh", conf_name], cwd=engine, stdout=lf, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=3 * 3600)
    apks = sorted((engine / "build").glob("*.apk"))
    return p.returncode, apks


def parse_patch_log(text):
    run = text.split("===== engine run")[-1]
    applied = re.findall(r"INFO: Applied: (.+)", run)
    disabled = [re.sub(r"\s*\(default\)$", "", x) for x in re.findall(r"INFO: Skipping disabled: (.+)", run)]
    failed = re.findall(r"(?:SEVERE|ERROR).*?[\"']?([^\"'\n]+?)[\"']? failed", run)
    return applied, disabled, failed


def parse_list_patches(text):
    """Parse `morphe-cli list-patches -p -v` text into {name: {...}}."""
    out = {}
    for block in re.split(r"\n\s*\n", text):
        name = re.search(r"^Name: (.+)$", block, re.M)
        if not name:
            continue
        desc = re.search(r"^Description: (.*)$", block, re.M)
        enabled = re.search(r"^Enabled: (\w+)", block, re.M)
        versions = re.findall(r"^\t\t(\S+)$", block, re.M)
        out[name.group(1).strip()] = {
            "packages": re.findall(r"Package name: (\S+)", block),
            "description": desc.group(1).strip() if desc else "",
            "default": (enabled.group(1) == "true") if enabled else True,
            "versions": versions,
        }
    return out


def list_args(text):
    """Patch names from a config string like '"One" "Two"'."""
    return re.findall(r'"([^"]+)"', text or "")


def find_clone_patch(listed, pkg):
    """The bundle's patch that renames the package: app-specific first, then universal."""
    names = [n for n in listed if re.fullmatch(r"(clone( app)?|change package name)", n, re.I)]
    names.sort(key=lambda n: (pkg not in listed[n].get("packages", []), n.lower() != "clone app"))
    return names[0] if names else None


def engine_prebuilts(engine, repo):
    d = engine / "temp" / f"{repo.split('/')[0].lower()}-rv"
    mpp = sorted(d.glob("patches-*"), key=lambda p: p.stat().st_mtime)
    cli_dir = engine / "temp" / "morpheapp-rv"
    jars = sorted(list(cli_dir.glob("*cli-*.jar")) + list(cli_dir.glob("*desktop-*.jar")),
                  key=lambda p: p.stat().st_mtime)
    mpp = [p for p in mpp if p.suffix in (".mpp", ".jar", ".rvp") and not p.name.endswith("-zip")]
    return (mpp[-1] if mpp else None), (jars[-1] if jars else None)


def java_cmd():
    home = os.environ.get("JAVA_HOME_21_X64") or os.environ.get("JAVA_HOME")
    return [str(Path(home) / "bin" / "java")] if home else ["java"]


def list_patches(cli, mpp, pkg, experimental):
    cmd = [*java_cmd(), "-jar", str(cli), "list-patches", f"--patches={mpp}", "-f", pkg, "-p", "-v"]
    if experimental:
        cmd.append("-x")
    p = subprocess.run(cmd, capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})
    return parse_list_patches(p.stdout)


def signer_sha256(engine, apk):
    p = subprocess.run([*java_cmd(), "-jar", str(engine / "bin" / "apksigner.jar"), "verify", "--print-certs", str(apk)],
                       capture_output=True, text=True)
    m = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]+)", p.stdout)
    return m.group(1) if m else None


_STORE_CACHE = {}


def app_store_info(pkg):
    """Icon and blurb for an app, from awesome-morphe, else Google Play."""
    if "awesome" not in _STORE_CACHE:
        try:
            _STORE_CACHE["awesome"] = json.loads(http(AWESOME_BUNDLES, timeout=90)).get("store", {})
        except Exception as e:
            log(f"awesome-morphe data unavailable: {e}")
            _STORE_CACHE["awesome"] = {}
    s = _STORE_CACHE["awesome"].get(pkg) or {}
    info = {"icon": s.get("iconUrl", ""), "summary": s.get("description", ""), "category": s.get("category", "")}
    if not info["icon"]:
        html = _get(f"https://play.google.com/store/apps/details?id={pkg}&hl=en")
        m = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        if m:
            info["icon"] = m.group(1)
    return info


def tail(path, n=80):
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return ""
    keep = [l for l in lines if not re.match(r"INFO: (Applied|Skipping disabled):", l)]
    return "\n".join(keep[-n:])


def cmd_build(a):
    apps = load_apps()
    cfg = apps[a.app]
    engine = Path(a.engine).resolve()
    out = OUT_DIR / a.app
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    log_path = out / "build.log"
    conf = engine / f"ostore-{a.app}.toml"
    sources_state = read_json(STATE_DIR / "sources.json", {})

    def fail(step, msg):
        (out / "error.json").write_text(json.dumps({"app": a.app, "step": step, "message": msg,
                                                     "engine": a.engine_sha or "", "log_tail": tail(log_path)}, indent=2))
        log(f"FAILED at {step}: {msg}")
        sys.exit(1)

    try:
        sources = resolve_sources(cfg)
    except Exception as e:
        fail("find-download-source", str(e))
    log(f"{a.app}: sources {sources}")

    # Patches added on top of the bundle defaults: remembered clone patch, then
    # anything the first build shows is needed (clone patch, all-patches).
    remembered = sources_state.get(cfg["package"], {}).get("clone_patch") if cfg["mode"] == "clone" else None
    extra = [remembered] if remembered else []
    excluded = set(list_args(cfg.get("excluded-patches", "")))
    result = None
    for attempt in range(1, 4):
        write_engine_config(cfg, sources, conf, extra)
        rc, apks = run_engine(engine, conf.name, log_path)
        if not apks and attempt == 1:
            # The download source may have moved: look it up again once.
            log("no APK produced; refreshing download sources and retrying")
            try:
                sources = resolve_sources(cfg, refresh=True)
            except Exception as e:
                fail("find-download-source", str(e))
            write_engine_config(cfg, sources, conf, extra)
            rc, apks = run_engine(engine, conf.name, log_path)
        if not apks:
            fail("patch", f"engine produced no APK (exit {rc}); see log tail")
        apk = apks[0]
        info = manifest_info(apk)
        cloned = info["package"] != cfg["package"]
        result = (apk, info, cloned)

        more = []
        mpp, cli = engine_prebuilts(engine, cfg["patches"])
        listed = list_patches(cli, mpp, cfg["package"], False) if mpp and cli else {}
        if cfg.get("all-patches"):
            _, skipped, _ = parse_patch_log(log_path.read_text(errors="replace"))
            more += [n for n in skipped if listed.get(n, {}).get("packages") and n not in excluded and n not in extra]
        if cfg["mode"] == "clone" and not cloned:
            name = find_clone_patch(listed, cfg["package"])
            if not name:
                log("this bundle has no clone patch; publishing a standalone build")
            elif name not in extra and name not in more:
                more.append(name)
        if not more:
            break
        log(f"rebuilding with extra patches: {more}")
        extra += more
    apk, info, cloned = result
    if cfg["mode"] == "clone" and cloned:
        used = next((n for n in extra if n == find_clone_patch(listed, cfg["package"])), None)
        st = read_json(STATE_DIR / "sources.json", {})
        if used:
            st.setdefault(cfg["package"], {})["clone_patch"] = used
        else:
            st.setdefault(cfg["package"], {}).pop("clone_patch", None)
        write_json(STATE_DIR / "sources.json", st)

    # Signature guard: if the engine ever stops using our key, stop here.
    want = (ROOT / "keystore" / "fingerprint.txt").read_text().strip()
    got = signer_sha256(engine, apk)
    if got != want:
        fail("verify-signature", f"APK is signed with {got}, expected our key {want}")

    log_text = log_path.read_text(errors="replace")
    applied, disabled, failed = parse_patch_log(log_text)
    if not applied:
        fail("verify-patches", "no patches were applied")

    mpp, cli = engine_prebuilts(engine, cfg["patches"])
    stable = list_patches(cli, mpp, cfg["package"], False) if mpp and cli else {}
    every = list_patches(cli, mpp, cfg["package"], True) if mpp and cli else {}
    stable_versions = {v for x in stable.values() for v in x["versions"]}
    exp_versions = sorted({v for x in every.values() for v in x["versions"]} - stable_versions,
                          key=lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v)], reverse=True)
    every = {**stable, **every}
    desc = lambda n: every.get(n, {}).get("description", "")  # noqa: E731
    applied_set, disabled_set = set(applied), set(disabled)
    experimental = sorted(n for n, p in every.items()
                          if n not in applied_set and n not in disabled_set and p["versions"]
                          and info["versionName"] not in p["versions"])

    planned = read_json(OUT_DIR / "plan.json", {}).get(a.app, {})
    if planned.get("tag"):
        patches_tag = planned
    else:  # built outside a plan: read the version from the downloaded bundle name
        ver = re.sub(r"^patches-|\.(mpp|jar|rvp)$", "", mpp.name) if mpp else "unknown"
        patches_tag = {"tag": f"v{ver}", "url": f"https://github.com/{cfg['patches']}/releases"}
    name = cfg.get("name", a.app)
    file_name = f"{slug(name)}-{'clone' if cloned else 'standalone'}-v{info['versionName']}-{ARCH}.apk"
    shutil.move(apk, out / file_name)
    store = app_store_info(cfg["package"])
    meta = {
        "id": a.app,
        "name": name,
        "original_package": cfg["package"],
        "package": info["package"],
        "kind": "clone" if cloned else "standalone",
        "version": info["versionName"],
        "version_code": info["versionCode"],
        "arch": ARCH,
        "file": file_name,
        "size": (out / file_name).stat().st_size,
        "sha256": sha256_file(out / file_name),
        "signer_sha256": got,
        "patches_source": cfg["patches"],
        "patches_version": patches_tag["tag"],
        "patches_release_url": patches_tag.get("url", ""),
        "cli": cli.name if cli else "",
        "engine_commit": a.engine_sha or "",
        "built_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "run_url": os.environ.get("RUN_URL", ""),
        "needs_microg": any(re.search(r"gmscore|microg", n, re.I) for n in applied),
        "icon": store["icon"],
        "summary": store["summary"],
        "category": store["category"],
        "patches": {
            "included": [{"name": n, "description": desc(n)} for n in applied],
            "not_included": [{"name": n, "description": desc(n)} for n in disabled],
            "experimental": [{"name": n, "description": desc(n)} for n in experimental],
            "failed": failed,
        },
        "experimental_versions": exp_versions,
        "config_hash": config_hash(cfg),
    }
    write_json(out / "meta.json", meta)
    log(f"built {file_name} ({meta['kind']}, {len(applied)} patches)")
    gh_output("file", file_name)


# ----------------------------------------------------------------- release / record

def release_notes(m):
    mb = m["size"] / 1_000_000
    lines = [
        f"**{m['name']}** {m['version']} ({'Cloned app' if m['kind'] == 'clone' else 'Standalone app'})",
        "",
        f"- Package: `{m['package']}`" + (f" (original: `{m['original_package']}`)" if m["kind"] == "clone" else ""),
        f"- Patches: [{m['patches_source']}](https://github.com/{m['patches_source']}) {m['patches_version']}",
        f"- Architecture: {m['arch']} · Size: {mb:.1f} MB",
        f"- SHA-256: `{m['sha256']}`",
        f"- Included patches: {len(m['patches']['included'])}",
    ]
    if m["needs_microg"]:
        lines.append("- Needs [MicroG-RE](https://github.com/MorpheApp/MicroG-RE/releases/latest) to sign in")
    return "\n".join(lines) + "\n"


def cmd_release(a):
    out = OUT_DIR / a.app
    m = read_json(out / "meta.json", None)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M")
    tag = f"{a.app}-v{m['version']}-{m['patches_version'].lstrip('v')}-{stamp}"
    m["release_tag"] = tag
    write_json(out / "meta.json", m)
    (out / "notes.md").write_text(release_notes(m))
    shutil.copy(out / "build.log", out / "build-log.txt")
    subprocess.check_call(["gh", "release", "create", tag, str(out / m["file"]), str(out / "meta.json"),
                           str(out / "build-log.txt"),
                           "--title", f"{m['name']} {m['version']} ({m['kind']})",
                           "--notes-file", str(out / "notes.md"), "--latest=false"])
    gh_output("tag", tag)


def cmd_record(a):
    built = read_json(STATE_DIR / "built.json", {})
    m = read_json(OUT_DIR / a.app / "meta.json", None)
    built[a.app] = {k: m.get(k) for k in ("version", "patches_version", "config_hash", "engine_commit",
                                         "built_at", "release_tag", "kind")}
    built[a.app]["patches_tag"] = m["patches_version"]
    write_json(STATE_DIR / "built.json", built)


# ----------------------------------------------------------------- catalog

def cmd_catalog(a):
    apps_cfg = load_apps()
    releases = api_paged(f"repos/{repo_slug()}/releases")
    builds = {}
    for rel in releases:
        if rel.get("draft"):
            continue
        assets = {x["name"]: x for x in rel.get("assets", [])}
        if "meta.json" not in assets:
            continue
        try:
            m = json.loads(http(assets["meta.json"]["browser_download_url"], timeout=60))
        except Exception as e:
            log(f"skip {rel['tag_name']}: {e}")
            continue
        apk = assets.get(m.get("file"))
        if not apk:
            continue
        m["download_url"] = apk["browser_download_url"]
        m["downloads"] = apk.get("download_count", 0)
        m["release_url"] = rel["html_url"]
        m["release_tag"] = rel["tag_name"]
        builds.setdefault(m["id"], []).append(m)
    apps = []
    for app_id, bs in builds.items():
        bs.sort(key=lambda b: b["built_at"], reverse=True)
        latest = bs[0]
        apps.append({
            "id": app_id,
            "name": latest["name"],
            "active": app_id in apps_cfg and apps_cfg[app_id]["enabled"],
            "latest": latest,
            "history": [{k: b[k] for k in ("version", "patches_version", "built_at", "download_url", "size",
                                            "sha256", "kind", "release_url")} for b in bs[1:]],
            "downloads": sum(b["downloads"] for b in bs),
        })
    apps.sort(key=lambda x: x["name"].lower())
    microg = {}
    try:
        rel = api("repos/MorpheApp/MicroG-RE/releases/latest")
        names = [x for x in rel["assets"] if x["name"].endswith(".apk")]
        # arm64 build with the launcher icon (this store is arm64-only), else the universal APK.
        apk = (next((x for x in names if x["name"].endswith("-icon-arm64-v8a.apk")), None)
               or next((x for x in names if re.fullmatch(r"microg-[\d.]+\.apk", x["name"])), None)
               or names[0])
        microg = {"version": rel["tag_name"], "url": apk["browser_download_url"], "page": rel["html_url"]}
    except Exception as e:
        log(f"MicroG-RE lookup failed: {e}")
        microg = {"page": "https://github.com/MorpheApp/MicroG-RE/releases/latest"}
    data = {"generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "repo": repo_slug(), "microg": microg, "apps": apps}
    write_json(a.out, data)
    write_lookup(Path(a.out).parent / "lookup.json")
    log(f"catalog: {len(apps)} apps from {len(releases)} releases -> {a.out}")


def write_lookup(path):
    """Small app + bundle index for the Add page preview (from awesome-morphe)."""
    try:
        d = json.loads(http(AWESOME_BUNDLES, timeout=120))
    except Exception as e:
        log(f"lookup skipped: {e}")
        if not Path(path).exists():
            write_json(path, {"apps": {}, "bundles": {}})
        return
    apps = {k: [v.get("name", ""), v.get("iconUrl", "")] for k, v in d.get("store", {}).items()
            if not k.startswith("__")}
    comp = d.get("compatibilities", [])
    bundles = {}
    for b in d.get("bundles", []):
        pk = set()
        for p in b.get("patches", []):
            key = p.get("compatiblePackagesKey")
            if isinstance(key, int) and 0 <= key < len(comp):
                pk.update(c["packageName"] for c in comp[key])
        bundles[b["repo"].lower()] = {"repo": b["repo"], "name": b.get("name", ""), "avatar": b.get("avatarUrl", ""),
                                      "stars": b.get("stars", 0), "patches": len(b.get("patches", [])),
                                      "packages": sorted(pk), "source": b.get("source", "github")}
    Path(path).write_text(json.dumps({"apps": apps, "bundles": bundles}, separators=(",", ":")))


# ----------------------------------------------------------------- cleanup

def cmd_cleanup(a):
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=a.days)
    releases = api_paged(f"repos/{repo_slug()}/releases")
    newest = {}
    for rel in releases:
        m = re.match(r"^(.*?)-v\d", rel["tag_name"])
        if not m:
            continue  # not one of our build releases
        app_id = m.group(1)
        if app_id not in newest or rel["created_at"] > newest[app_id]["created_at"]:
            newest[app_id] = rel
    keep = {r["id"] for r in newest.values()}
    for rel in releases:
        created = dt.datetime.fromisoformat(rel["created_at"].replace("Z", "+00:00"))
        if not re.match(r"^.*?-v\d", rel["tag_name"]) or rel["id"] in keep or created > cutoff:
            continue
        log(f"delete {rel['tag_name']} (created {rel['created_at']})")
        if not a.dry_run:
            subprocess.check_call(["gh", "release", "delete", rel["tag_name"], "--yes", "--cleanup-tag"])


# ----------------------------------------------------------------- add-app

def parse_issue_form(body):
    fields, cur = {}, None
    for line in body.splitlines():
        h = re.match(r"^###\s+(.+?)\s*$", line)
        if h:
            cur = h.group(1).strip().lower()
            fields[cur] = ""
        elif cur is not None:
            fields[cur] += line + "\n"
    return {k: v.strip() for k, v in fields.items() if v.strip() and v.strip() != "_No response_"}


def check_bundle_supports(repo, pkg):
    """Fail early if the bundle clearly has no patches for this package."""
    rel = latest_stable(repo)  # also proves the repo exists and has a stable release
    plist = None
    for ref in (rel["tag"], "HEAD"):  # some bundles only commit the list after tagging
        try:
            plist = json.loads(http(f"https://raw.githubusercontent.com/{repo}/{ref}/patches-list.json", timeout=60))
            break
        except Exception:
            continue
    if plist is None:
        log("no patches-list.json in this bundle; the first build will check compatibility")
        return
    pkgs = {c["packageName"] for x in plist.get("patches", []) for c in (x.get("compatiblePackages") or [])}
    if pkgs and pkg not in pkgs:
        raise SystemExit(f"{repo} {rel['tag']} has no patches for {pkg}. It supports: {', '.join(sorted(pkgs))}")


def cmd_add_app(a):
    f = parse_issue_form(Path(a.body_file).read_text())
    link = f.get("patch source link", "")
    pkg = f.get("package name", "").strip()
    name = f.get("app name", "").strip()
    mode = "standalone" if "standalone" in f.get("type", "").lower() else "clone"
    repo = parse_source(link)
    if not re.match(r"^[a-zA-Z][\w]*(\.[\w]+)+$", pkg):
        raise SystemExit(f"'{pkg}' does not look like an Android package name (example: com.google.android.youtube)")
    if not name:
        name = pkg.split(".")[-1].title()
    check_bundle_supports(repo, pkg)
    app_id = f"{slug(name)}-{slug(repo.split('/')[0])}"
    lines = [
        f"# Added from issue #{a.issue} on {dt.date.today().isoformat()}",
        f"name = {toml_str(name)}",
        f"package = {toml_str(pkg)}",
        f"patches = {toml_str(repo)}",
        f"mode = {toml_str(mode)}  # clone = installs next to the original app",
        "# all-patches = true  # also turn on this app's patches that are off by default",
        "",
        "# Optional. Patch names in quotes, separated by spaces:",
        "# included-patches = '\"Patch one\" \"Patch two\"'",
        "# excluded-patches = '\"Patch three\"'",
        "# Optional. Set only if the automatic download lookup fails:",
        "# apkmirror = \"https://www.apkmirror.com/apk/<developer>/<app>\"",
    ]
    APPS_DIR.mkdir(exist_ok=True)
    (APPS_DIR / f"{app_id}.toml").write_text("\n".join(lines) + "\n")
    log(f"wrote apps/{app_id}.toml")
    gh_output("id", app_id)
    gh_output("repo", repo)


# ----------------------------------------------------------------- issues

def _find_issue(title):
    issues = api(f"repos/{repo_slug()}/issues?state=open&labels={FAIL_LABEL}&per_page=100") or []
    return next((i for i in issues if i["title"] == title), None)


def _ensure_label():
    try:
        api(f"repos/{repo_slug()}/labels", data={"name": FAIL_LABEL, "color": "d73a4a",
                                                  "description": "Automatic build failure report"}, method="POST")
    except urllib.error.HTTPError:
        pass  # already exists


def cmd_issue_fail(a):
    err = read_json(OUT_DIR / a.app / "error.json", {})
    title = a.title or f"Build failed: {a.app}"
    message = a.message
    if a.message_file and Path(a.message_file).exists():
        message = Path(a.message_file).read_text()
    when = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
    body = (
        f"**When:** {when}\n**Run:** {os.environ.get('RUN_URL', '-')}\n"
        f"**Step:** `{err.get('step', a.step or 'unknown')}`\n**Engine commit:** `{err.get('engine', '-')}`\n\n"
        f"**Error:** {err.get('message', message or 'see run log')}\n\n"
        f"<details><summary>Last log lines</summary>\n\n```\n{err.get('log_tail', '')[-60000:]}\n```\n</details>\n"
    )
    _ensure_label()
    issue = _find_issue(title)
    if issue:
        api(f"repos/{repo_slug()}/issues/{issue['number']}/comments", data={"body": body}, method="POST")
        log(f"updated issue #{issue['number']}")
    else:
        intro = ("This issue is opened and updated automatically when a build fails. "
                 "It closes by itself after the next good build.\n\n")
        i = api(f"repos/{repo_slug()}/issues", data={"title": title, "body": intro + body, "labels": [FAIL_LABEL]},
                method="POST")
        log(f"opened issue #{i['number']}")


def cmd_issue_ok(a):
    issue = _find_issue(a.title or f"Build failed: {a.app}")
    if not issue:
        return
    api(f"repos/{repo_slug()}/issues/{issue['number']}/comments",
        data={"body": f"Fixed: built successfully in {os.environ.get('RUN_URL', 'a later run')}."}, method="POST")
    api(f"repos/{repo_slug()}/issues/{issue['number']}", data={"state": "closed"}, method="PATCH")
    log(f"closed issue #{issue['number']}")


# ----------------------------------------------------------------- health

def cmd_health(a):
    problems = []
    try:
        data = json.loads(http(a.url.rstrip("/") + "/data.json", timeout=60))
    except Exception as e:
        data = None
        problems.append(f"data.json could not be loaded: {e}")
    if data is not None:
        if not data.get("apps") and read_json(STATE_DIR / "built.json", {}):
            problems.append("data.json has no apps, but state/built.json says apps were built")
        for app in data.get("apps", []):
            if not app.get("active"):
                continue
            url = app["latest"]["download_url"]
            try:
                http(url, method="HEAD", timeout=60)
            except Exception as e:
                problems.append(f"{app['name']}: download link broken ({e}): {url}")
    try:
        page = http(a.url, timeout=60).decode("utf-8", "replace")
        if "<title>" not in page:
            problems.append("store page has no <title>")
    except Exception as e:
        problems.append(f"store page could not be loaded: {e}")
    if problems:
        Path(a.report).write_text("\n".join(f"- {p}" for p in problems) + "\n")
        log("health problems:\n" + "\n".join(problems))
        sys.exit(1)
    log("store is healthy")


# ----------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan"); s.add_argument("--force", action="store_true"); s.add_argument("--only", default="")
    s = sub.add_parser("engine"); s.add_argument("--dir", default="engine"); s.add_argument("--sha", default="")
    s = sub.add_parser("build"); s.add_argument("app"); s.add_argument("--engine", default="engine")
    s.add_argument("--engine-sha", default="")
    s = sub.add_parser("release"); s.add_argument("app")
    s = sub.add_parser("record"); s.add_argument("app")
    s = sub.add_parser("catalog"); s.add_argument("--out", default="site/data.json")
    s = sub.add_parser("cleanup"); s.add_argument("--days", type=int, default=180); s.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("add-app"); s.add_argument("--body-file", required=True); s.add_argument("--issue", default="?")
    s = sub.add_parser("issue-fail"); s.add_argument("app"); s.add_argument("--step", default="")
    s.add_argument("--message", default=""); s.add_argument("--message-file", default=""); s.add_argument("--title", default="")
    s = sub.add_parser("issue-ok"); s.add_argument("app"); s.add_argument("--title", default="")
    s = sub.add_parser("health"); s.add_argument("--url", required=True); s.add_argument("--report", default="health.md")
    a = p.parse_args()
    {"plan": cmd_plan, "engine": cmd_engine, "build": cmd_build, "release": cmd_release, "record": cmd_record,
     "catalog": cmd_catalog, "cleanup": cmd_cleanup, "add-app": cmd_add_app, "issue-fail": cmd_issue_fail,
     "issue-ok": cmd_issue_ok, "health": cmd_health}[a.cmd](a)


if __name__ == "__main__":
    main()
