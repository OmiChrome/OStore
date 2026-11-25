// O Store front end: reads data.json (made from GitHub releases) and renders it.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const state = { data: null, q: "", maker: "all", lookup: null };

// Who makes the original app, from its package name. Anything not listed is "Other".
const MAKERS = [
  ["Google", /^com\.google\./],
  ["Meta", /^com\.(facebook|instagram|whatsapp|meta|oculus)\b|^com\.facebook\./],
  ["Microsoft", /^com\.(microsoft|skype|linkedin)\b/],
  ["Amazon", /^(com|in)\.amazon\./],
  ["X", /^com\.twitter\./],
  ["Reddit", /^com\.reddit\./],
  ["Spotify", /^com\.spotify\./],
  ["TikTok", /^com\.(zhiliaoapp\.musically|ss\.android)\b/],
  ["Snap", /^com\.snapchat\./],
  ["Adobe", /^com\.adobe\./],
];
const maker = (a) => {
  const pkg = a.latest.original_package || a.latest.package || "";
  return (MAKERS.find(([, re]) => re.test(pkg)) || ["Other"])[0];
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mb = (n) => (n / 1e6).toFixed(1) + " MB";
const day = (iso) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const ago = (iso) => {
  const d = Math.round((Date.now() - new Date(iso)) / 864e5);
  return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
};
const kindTag = (k) => k === "clone"
  ? `<span class="tag clone" title="Installs next to the original app">Clone</span>`
  : `<span class="tag standalone" title="Replaces the original app">Standalone</span>`;
const icon = (url, name, big) => url
  ? `<img class="icon${big ? " big" : ""}" src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
  : `<span class="icon letter${big ? " big" : ""}">${esc((name || "?")[0])}</span>`;
const repo = () => state.data?.repo || "OmiChrome/OStore";

function reportLink(title, body) {
  const u = new URL(`https://github.com/${repo()}/issues/new`);
  u.searchParams.set("title", title);
  u.searchParams.set("body", body);
  u.searchParams.set("labels", "store-problem");
  return u.toString();
}

async function load() {
  try {
    const r = await fetch("data.json", { cache: "no-cache" });
    if (!r.ok) throw new Error(`data.json: HTTP ${r.status}`);
    state.data = await r.json();
    const up = $("#updated");
    up.textContent = `Updated ${ago(state.data.generated_at)}`;
    up.title = new Date(state.data.generated_at).toLocaleString();
    up.hidden = false;
  } catch (e) {
    view.innerHTML = `<div class="note"><b>The store could not load its app list.</b><br>${esc(e.message)}.
      <a href="${esc(reportLink("Store: data.json failed to load", "Error: " + e.message + "\nPage: " + location.href))}">Report this</a>.</div>`;
    throw e;
  }
}

// ---------------------------------------------------------------- list
function renderList() {
  const apps = state.data.apps.filter((a) => a.active !== false || state.q);
  const counts = {};
  apps.forEach((a) => { const k = maker(a); counts[k] = (counts[k] || 0) + 1; });
  const makers = Object.keys(counts).sort((x, y) => (x === "Other") - (y === "Other") || counts[y] - counts[x] || x.localeCompare(y));
  if (state.maker !== "all" && !counts[state.maker]) state.maker = "all";
  view.innerHTML = `
    <label class="search">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input type="search" id="q" placeholder="Search apps, packages, patch sources" value="${esc(state.q)}" aria-label="Search apps">
    </label>
    ${makers.length > 1 ? `<div class="chips" role="group" aria-label="Filter by app maker">
      ${chip("all", "All apps")}${makers.map((k) => chip(k, k, counts[k])).join("")}</div>` : `<div class="chips"></div>`}
    <p class="count-line" id="countline"></p>
    <div id="results"></div>`;
  const input = $("#q");
  input.addEventListener("input", () => { state.q = input.value; renderResults(apps); });
  view.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    state.maker = b.dataset.maker;
    view.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c === b));
    renderResults(apps);
  }));
  renderResults(apps);
}

function chip(k, label, n) {
  return `<button type="button" class="chip" data-maker="${esc(k)}" aria-pressed="${state.maker === k}">${esc(label)}${n ? `<span class="n">${n}</span>` : ""}</button>`;
}

function renderResults(apps) {
  const q = state.q.trim().toLowerCase();
  const shown = apps.filter((a) => {
    const m = a.latest;
    if (state.maker !== "all" && maker(a) !== state.maker) return false;
    return !q || [a.name, m.package, m.original_package, m.patches_source, m.category, maker(a)].join(" ").toLowerCase().includes(q);
  });
  $("#countline").textContent = shown.length ? `Showing ${shown.length} ${shown.length === 1 ? "app" : "apps"}` : "";
  $("#results").innerHTML = shown.length ? `<div class="grid">${shown.map(card).join("")}</div>`
    : `<p class="empty">${state.data.apps.length ? "No apps match. Try a different name or clear the filter." : "No apps built yet. The first build runs automatically and they will show up here."}</p>`;
}

const fmtCount = (n) => n >= 1e3 ? (n / 1e3).toFixed(n >= 1e4 ? 0 : 1).replace(/\.0$/, "") + "k" : String(n);

function card(a) {
  const m = a.latest;
  return `<a class="card" href="#/app/${encodeURIComponent(a.id)}">
    ${icon(m.icon, a.name)}
    <div class="body"><div class="name">${esc(a.name)}${m.kind === "clone" ? "" : ` <span class="tag standalone" title="Replaces the original app">Standalone</span>`}</div>
    <div class="sub">v${esc(m.version)} · ${mb(m.size)}</div></div>
    <span class="dl" title="${a.downloads ?? 0} downloads"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v12m-5-5 5 5 5-5M5 20h14"/></svg>${fmtCount(a.downloads ?? 0)}</span></a>`;
}

// ---------------------------------------------------------------- detail
function patchList(title, items, open, hint) {
  if (!items?.length) return "";
  return `<details class="patches"${open ? " open" : ""}><summary>${title} <span class="count">(${items.length})</span></summary>
    ${hint ? `<p class="muted hint">${hint}</p>` : ""}
    <ul>${items.map((p) => `<li><b>${esc(p.name)}</b>${p.description ? `<span>${esc(p.description)}</span>` : ""}</li>`).join("")}</ul></details>`;
}

function renderApp(id) {
  const a = state.data.apps.find((x) => x.id === id);
  if (!a) { view.innerHTML = `<div class="detail"><a class="back" href="#/">‹ All apps</a><p class="empty">This app is not in the store. It may have been removed.</p></div>`; return; }
  const m = a.latest;
  const p = m.patches || {};
  const fact = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
  view.innerHTML = `<div class="detail">
    <a class="back" href="#/">‹ All apps</a>
    <section class="hero">
      ${icon(m.icon, a.name, true)}
      <div><h1>${esc(a.name)} ${kindTag(m.kind)}</h1>
      <div class="sub">${esc(m.summary || m.category || "")}</div></div>
    </section>
    <div class="cta">
      <a class="btn" id="dl" href="${esc(m.download_url)}" download>Download v${esc(m.version)} · ${mb(m.size)}</a>
      ${m.needs_microg ? `<span class="tag microg">Needs MicroG-RE</span>` : ""}
    </div>
    ${m.kind === "clone"
      ? `<div class="note info"><b>Cloned app.</b> It installs <b>next to</b> the original ${esc(a.name)} as <code>${esc(m.package)}</code>, so you can keep both.</div>`
      : `<div class="note"><b>Standalone app.</b> It uses the same package name as the original (<code>${esc(m.package)}</code>) — uninstall the original first. No clone was possible for this app.</div>`}
    <dl class="facts">
      ${fact("Version", `${esc(m.version)} <span class="muted">(stable)</span>`)}
      ${fact("Size", mb(m.size))}
      ${fact("Architecture", esc(m.arch))}
      ${fact("Package", `<code>${esc(m.package)}</code>`)}
      ${fact("Original package", `<code>${esc(m.original_package)}</code>`)}
      ${fact("Patches", `<a href="https://github.com/${esc(m.patches_source)}" rel="noopener">${esc(m.patches_source)}</a> ${esc(m.patches_version)}`)}
      ${fact("Built", `${day(m.built_at)} <span class="muted">(${ago(m.built_at)})</span>`)}
      ${fact("Downloads", String(a.downloads ?? 0))}
      ${fact("SHA-256", `<code>${esc(m.sha256)}</code>`)}
      ${fact("Signed by", `<code title="Signing certificate SHA-256">${esc(m.signer_sha256.slice(0, 16))}…</code>`)}
    </dl>
    ${m.experimental_versions?.length ? `<div class="note info">Newer app versions exist only as <b>experimental</b> patch targets (${m.experimental_versions.map(esc).join(", ")}). This store stays on the newest <b>stable</b> one.</div>` : ""}
    <h2>Patches</h2>
    ${patchList("Included", p.included, true)}
    ${patchList("Not included (off by default)", p.not_included, false, "These patches exist but are off by default, so they are not in this build.")}
    ${patchList("Experimental only", p.experimental, false, "These only work with experimental app versions, so they are not in this build.")}
    ${p.failed?.length ? `<div class="note">Some patches reported errors during the build: ${p.failed.map(esc).join(", ")}</div>` : ""}
    ${a.history?.length ? `<h2>Older builds</h2><div class="scroll"><table class="hist"><tr><th>Version</th><th>Patches</th><th>Built</th><th></th></tr>
      ${a.history.map((h) => `<tr><td>${esc(h.version)}</td><td>${esc(h.patches_version)}</td><td>${day(h.built_at)}</td>
        <td><a href="${esc(h.download_url)}">${mb(h.size)}</a></td></tr>`).join("")}</table></div>` : ""}
    <p class="muted" style="margin-top:28px">
      <a href="${esc(m.release_url)}">Release page &amp; build log</a> ·
      <a href="${esc(reportLink(`${a.name}: problem with v${m.version}`, `App: ${a.id}\nVersion: ${m.version}\nPatches: ${m.patches_source} ${m.patches_version}\nRelease: ${m.release_url}\n\nWhat went wrong?\n`))}">Report a problem with this app</a>
    </p></div>`;
  if (m.needs_microg) {
    $("#dl").addEventListener("click", () => setTimeout(() => $("#microg").showModal(), 300));
  }
}

// ---------------------------------------------------------------- about
function renderAbout() {
  view.innerHTML = `<div class="prose">
    <h1>About O Store</h1>
    <p>Patched Android apps, built automatically by GitHub Actions from <b>stable</b> patch releases (Morphe and other patch makers). Every two days the builder checks for new stable patches and only rebuilds apps that changed.</p>
    <h2>Clone or standalone?</h2>
    <p>${kindTag("clone")} apps get a new package name, so they install <b>next to</b> the original app — you keep both.
    ${kindTag("standalone")} apps replace the original and are only used when an app cannot be cloned.</p>
    <h2>MicroG-RE</h2>
    <p>Google apps such as YouTube need <a href="${esc(state.data?.microg?.page || "https://github.com/MorpheApp/MicroG-RE/releases/latest")}">MicroG-RE</a> to sign in. The store reminds you when you download one.</p>
    <h2>Is it safe?</h2>
    <p>Original APKs are downloaded from APKMirror or archive.org and their signatures are checked against the real developer's key before patching. Every build shows its SHA-256 and full build log. All builds are signed with the same O Store key, so updates install over each other.</p>
    <p>Patches belong to their authors. Support them if you like their work.</p>
    <p><a href="https://github.com/${esc(repo())}">Source code on GitHub</a></p></div>`;
}

// ---------------------------------------------------------------- add page
function parseSource(text) {
  text = text.trim();
  if (!text) return null;
  try {
    const u = new URL(text.includes("://") ? text : "https://x/?github=" + text);
    const gh = u.searchParams.get("github");
    if (gh) return { repo: gh.replace(/^\/|\/$/g, "") };
    if (u.searchParams.get("gitlab") || u.searchParams.get("codeberg") || /gitlab|codeberg/.test(u.host)) return { error: "Only GitHub patch sources are supported right now." };
    const m = text.match(/github\.com\/([\w.-]+)\/([\w.-]+)/);
    if (m) return { repo: `${m[1]}/${m[2].replace(/\.git$/, "")}` };
  } catch (_) { /* fall through */ }
  if (/^[\w.-]+\/[\w.-]+$/.test(text)) return { repo: text };
  return { error: "That does not look like a patch source link." };
}

async function getLookup() {
  if (!state.lookup) {
    try { state.lookup = await (await fetch("lookup.json")).json(); } catch (_) { state.lookup = { apps: {}, bundles: {} }; }
  }
  return state.lookup;
}

async function bundlePackages(r) {
  // Live list straight from the bundle repo when it publishes one.
  try {
    const res = await fetch(`https://raw.githubusercontent.com/${r}/HEAD/patches-list.json`);
    if (!res.ok) return null;
    const j = await res.json();
    const pk = new Map();
    for (const p of j.patches || []) for (const c of p.compatiblePackages || []) {
      const e = pk.get(c.packageName) || { name: c.name, count: 0 };
      e.count++; pk.set(c.packageName, e);
    }
    return { version: j.version, packages: pk };
  } catch (_) { return null; }
}

function renderAdd() {
  view.innerHTML = `<div class="add-wrap"><div class="prose">
    <h1>Add an app</h1>
    <p class="muted">Paste a patch source link and the app's package name. Check the preview, then submit. Only the store owner's requests are built; anyone else's are closed automatically.</p></div>
    <form class="add" id="addf" autocomplete="off">
      <label>Patch source link <small>From morphe-patches.software, awesome-morphe, or a GitHub repo</small>
        <input id="src" placeholder="https://morphe.software/add-source?github=MorpheApp/morphe-patches" required></label>
      <label>Package name <small>The original app's package</small>
        <input id="pkg" placeholder="com.google.android.youtube" required></label>
      <label>App name <small>Optional — filled in automatically when known</small>
        <input id="nm" placeholder="YouTube"></label>
      <label>Type
        <select id="kind"><option value="clone">Clone (installs next to the original)</option><option value="standalone">Standalone (replaces the original)</option></select></label>
    </form>
    <div class="preview" id="pv">
      <div class="pv dim"><span class="icon letter">?</span><div><b>Patch source</b><div class="muted">MorpheApp · Morphe Patches</div></div></div>
      <div class="pv dim"><span class="icon letter">?</span><div><b>App</b><div class="muted">YouTube · com.google.android.youtube</div></div></div>
    </div>
    <div class="row"><a class="btn" id="go" aria-disabled="true">Submit on GitHub</a></div>
    <p class="muted">Submitting opens a pre-filled GitHub issue — press <b>Create</b> there. The build starts by itself and the app appears here when it is done.</p></div>`;
  const src = $("#src"), pkg = $("#pkg"), nm = $("#nm"), kind = $("#kind"), pv = $("#pv"), go = $("#go");
  let timer, seq = 0;
  const update = () => { clearTimeout(timer); timer = setTimeout(preview, 250); };
  [src, pkg, nm, kind].forEach((el) => el.addEventListener("input", update));

  async function preview() {
    const my = ++seq;
    const s = parseSource(src.value), p = pkg.value.trim();
    const look = await getLookup();
    let srcHtml = `<div class="pv dim"><span class="icon letter">?</span><div><b>Patch source</b><div class="muted">MorpheApp · Morphe Patches</div></div></div>`;
    let appHtml = `<div class="pv dim"><span class="icon letter">?</span><div><b>App</b><div class="muted">YouTube · com.google.android.youtube</div></div></div>`;
    let ready = false, live = null, b = null;
    if (s?.error) srcHtml = `<div class="pv bad"><span class="icon letter">!</span><div><b>Patch source</b><div>${esc(s.error)}</div></div></div>`;
    else if (s?.repo) {
      b = look.bundles[s.repo.toLowerCase()];
      live = await bundlePackages(s.repo);
      if (my !== seq) return;
      const owner = s.repo.split("/")[0];
      const img = b?.avatar || `https://github.com/${owner}.png?size=88`;
      const count = live ? `${[...live.packages.keys()].length} apps · v${esc(live.version)}` : b ? `${b.packages.length} apps · ${b.patches} patches` : "Not in the index yet — the first build will check it";
      srcHtml = `<div class="pv ${live || b ? "ok" : ""}">${icon(img, owner)}<div><b>${esc(s.repo)}</b><div class="muted">${count}</div></div></div>`;
    }
    if (p) {
      const known = look.apps[p];
      const pkgs = live ? live.packages : b ? new Map(b.packages.map((x) => [x, {}])) : null;
      const supported = pkgs ? pkgs.has(p) : null;
      const name = nm.value.trim() || known?.[0] || live?.packages.get(p)?.name || "";
      if (!nm.value.trim() && name) nm.placeholder = name;
      const okPkg = /^[a-zA-Z][\w]*(\.[\w]+)+$/.test(p);
      const msg = !okPkg ? "Not a valid package name" : supported === false ? "This patch source has no patches for this app" : supported ? `Supported${live?.packages.get(p)?.count ? ` · ${live.packages.get(p).count} patches` : ""}` : "Could not confirm support yet";
      appHtml = `<div class="pv ${!okPkg || supported === false ? "bad" : supported ? "ok" : ""}">${icon(known?.[1], name || p)}<div><b>${esc(name || "Unknown app")}</b>
        <div class="muted"><code>${esc(p)}</code> · ${esc(msg)}</div></div></div>`;
      ready = okPkg && s?.repo && supported !== false;
    }
    pv.innerHTML = srcHtml + appHtml;
    if (ready) {
      const name = nm.value.trim() || nm.placeholder;
      const u = new URL(`https://github.com/${repo()}/issues/new`);
      u.searchParams.set("template", "add-app.yml");
      u.searchParams.set("title", `Add app: ${name}`);
      u.searchParams.set("patch_source", src.value.trim());
      u.searchParams.set("package", p);
      u.searchParams.set("app_name", name);
      if (kind.value === "standalone") u.searchParams.set("type", "Standalone (replaces the original)");
      go.href = u.toString(); go.removeAttribute("aria-disabled"); go.target = "_blank"; go.rel = "noopener";
    } else { go.removeAttribute("href"); go.setAttribute("aria-disabled", "true"); }
  }
}

// ---------------------------------------------------------------- router
function route() {
  const h = location.hash.replace(/^#\/?/, "");
  window.scrollTo(0, 0);
  if (h.startsWith("app/")) renderApp(decodeURIComponent(h.slice(4)));
  else if (h === "add") renderAdd();
  else if (h === "about") renderAbout();
  else renderList();
  document.querySelectorAll(".links a[href^='#/']").forEach((l) => {
    if (l.getAttribute("href") === "#/" + h) l.setAttribute("aria-current", "page"); else l.removeAttribute("aria-current");
  });
}

$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch (_) { /* private mode */ }
});

window.addEventListener("error", (e) => {
  const link = $("#report");
  if (link) link.href = reportLink("Store: script error", `${e.message}\n${e.filename}:${e.lineno}\nPage: ${location.href}\nBrowser: ${navigator.userAgent}`);
});

(async () => {
  $("#report").href = reportLink("Store problem", `Page: ${location.href}\n\nWhat went wrong?\n`);
  try { await load(); } catch (_) { return; }
  $("#gh").href = `https://github.com/${repo()}`;
  const mg = state.data.microg || {};
  $("#microg-dl").href = mg.url || mg.page || "https://github.com/MorpheApp/MicroG-RE/releases/latest";
  window.addEventListener("hashchange", route);
  route();
})();
