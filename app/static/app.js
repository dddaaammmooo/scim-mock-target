"use strict";
const ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User";
const $app = document.getElementById("app");
let CONFIG = { token: "target-token" };
let timer = null;
let cleanup = null;

/* ---------- helpers ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const api = async (path, opts) => {
  const r = await fetch("/api" + path, opts);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.status === 204 ? null : r.json();
};
const putJson = (path, body) => api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function toast(msg) {
  const t = h("div", { class: "toast" }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), 2800);
}
function ago(ts) {
  if (!ts) return "never";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 5) return "just now";
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return new Date(ts).toLocaleDateString();
}
const fmtTime = (ts) => new Date(ts).toLocaleTimeString([], { hour12: false }) + "." + String(new Date(ts).getMilliseconds()).padStart(3, "0");
const statusClass = (s) => "s" + String(s)[0];
const pretty = (s) => { try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s || ""; } };
const json = (o) => JSON.stringify(o, null, 2);
function copyBox(text) {
  return h("div", { class: "copy" }, h("code", {}, text),
    h("button", { class: "sm", onclick: () => { navigator.clipboard?.writeText(text); toast("Copied"); } }, "Copy"));
}
function badge(text, kind) { return h("span", { class: "badge " + (kind || "") }, text); }
function statCard(label, value, sub) {
  return h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    sub && h("div", { class: "small muted" }, sub));
}
function setTimer(fn, ms) { clearTimer(); timer = setInterval(fn, ms); }
function clearTimer() { if (timer) clearInterval(timer); timer = null; }
function nav(hash) { location.hash = hash; }

/* ---------- layout ---------- */
function shell(tenant, active, main) {
  const link = (page, label, count) => h("a", { href: `#/t/${tenant}/${page}`, class: active === page ? "active" : "" }, label,
    count != null && h("span", { class: "muted" }, count));
  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" }, h("div", { class: "logo" }, "T"), h("div", {}, h("b", {}, "MockSCIMTarget"), h("small", {}, "SCIM 2.0 target"))),
    h("div", { class: "tenant-box" }, h("div", { class: "small muted" }, "Tenant"), h("b", {}, tenant),
      h("a", { href: "#/", class: "small" }, "Switch tenant")),
    h("nav", {}, link("dashboard", "Dashboard"), link("users", "Users"), link("groups", "Groups"),
      link("requests", "Request log"), link("faults", "Fault injection"), link("connection", "Connection")),
    h("div", { class: "foot" }, h("button", { class: "danger", onclick: () => resetTenant(tenant) }, "Reset tenant")));
  return h("div", { class: "shell" }, sidebar, h("main", {}, main));
}
function head(title, sub, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), sub && h("div", { class: "muted" }, sub)),
    h("div", { class: "row" }, actions));
}
async function resetTenant(tenant) {
  if (!confirm(`Delete ALL users, groups, request log and fault settings for tenant "${tenant}"?`)) return;
  await api(`/tenants/${encodeURIComponent(tenant)}`, { method: "DELETE" });
  toast(`Tenant ${tenant} reset`);
  route();
}

/* ---------- pages ---------- */
async function pagePicker() {
  const tenants = await api("/tenants");
  const input = h("input", { placeholder: "new-tenant-slug", pattern: "[A-Za-z0-9._-]+" });
  const go = () => { const v = input.value.trim(); if (/^[A-Za-z0-9._-]{1,64}$/.test(v)) nav(`#/t/${v}/dashboard`); else toast("Use letters, digits, . _ -"); };
  input.addEventListener("keydown", (e) => e.key === "Enter" && go());
  const remove = async (e, t) => {
    e.stopPropagation();  // don't open the tenant
    if (!confirm(`Delete tenant "${t.tenant}"? This removes ${t.users} users, ${t.groups} groups, the request log and fault settings.`)) return;
    try {
      await api(`/tenants/${encodeURIComponent(t.tenant)}`, { method: "DELETE" });
      toast(`Tenant ${t.tenant} deleted`);
    } catch (err) { toast(`Delete failed: ${err.message}`); }
    pagePicker();
  };
  const cards = tenants.map((t) => h("div", { class: "card tenant-card", onclick: () => nav(`#/t/${t.tenant}/dashboard`) },
    h("button", { class: "tenant-del", title: `Delete tenant ${t.tenant}`, "aria-label": `Delete tenant ${t.tenant}`,
      onclick: (e) => remove(e, t) }, "×"),
    h("div", { class: "row" }, h("b", {}, t.tenant), h("span", { class: "spacer" }), h("span", { class: "small muted" }, ago(t.lastActivity))),
    h("div", { class: "row", style: "margin-top:8px" }, badge(`${t.users} users`, "accent"), badge(`${t.usersActive} active`, "ok"),
      t.usersInactive ? badge(`${t.usersInactive} suspended`, "warn") : null, badge(`${t.groups} groups`, "info"), badge(`${t.requests} requests`))));
  $app.replaceChildren(h("main", { style: "max-width:960px;margin:0 auto" },
    h("div", { class: "brand" }, h("div", { class: "logo" }, "T"), h("div", {}, h("b", {}, "MockSCIMTarget"), h("small", {}, "SCIM 2.0 target - choose a tenant"))),
    head("Tenants", "Tenants are created automatically on the first SCIM request, or open one here."),
    h("div", { class: "card row", style: "margin-bottom:16px" }, input, h("button", { class: "primary", onclick: go }, "Open tenant")),
    tenants.length ? h("div", { class: "grid cols-2" }, cards) : h("div", { class: "card empty" }, "No tenants yet. Point a SCIM client at this server or open one above.")));
}

async function pageDashboard(tenant) {
  const render = async () => {
    const [s, recent] = await Promise.all([api(`/tenants/${tenant}/summary`), api(`/tenants/${tenant}/requests?limit=12`)]);
    const c = s.counts;
    const dist = (obj, cls) => {
      const entries = Object.entries(obj).sort((a, b) => b[1] - a[1]);
      const max = Math.max(1, ...entries.map((e) => e[1]));
      return entries.length ? entries.map(([k, v]) => h("div", { class: "lr" },
        h("div", {}, h("div", { class: "row" }, h("span", { class: cls ? cls(k) : "" }, k), h("span", { class: "spacer" })),
          h("div", { class: "bar" }, h("span", { style: `width:${(v / max) * 100}%` }))), h("b", {}, v))) : [h("div", { class: "muted" }, "No requests yet")];
    };
    const pct = c.users ? (c.usersActive / c.users) * 100 : 0;
    const body = h("div", { class: "stack" },
      head("Dashboard", `Tenant ${tenant} - last activity ${ago(s.lastActivity)}`,
        h("button", { onclick: () => render().then((el) => show(el, true)) }, "Refresh")),
      h("div", { class: "grid cols-4" }, statCard("Users", c.users), statCard("Groups", c.groups),
        statCard("Requests", c.requests), statCard("Faults", Object.values(s.faults).some((v) => v) ? "ON" : "off", "latency / errors / 429 / read-only")),
      h("div", { class: "grid cols-2" },
        h("div", { class: "card" }, h("h2", {}, "Active vs suspended"),
          h("div", { class: "row" }, badge(`${c.usersActive} active`, "ok"), badge(`${c.usersInactive} suspended`, "warn")),
          h("div", { class: "bar ok", style: "margin-top:10px" }, h("span", { style: `width:${pct}%` }))),
        h("div", { class: "card" }, h("h2", {}, "Requests by method"), dist(s.requestsByMethod))),
      h("div", { class: "card" }, h("h2", {}, "Requests by status"), h("div", { class: "grid cols-2" }, dist(s.requestsByStatus, statusClass))),
      h("div", { class: "card" }, h("h2", {}, "Recent activity"), recent.length ? h("div", { class: "table-wrap" }, h("table", {},
        h("thead", {}, h("tr", {}, ["Time", "Method", "Path", "Status", "ms"].map((x) => h("th", {}, x)))),
        h("tbody", {}, recent.map((r) => h("tr", {}, h("td", {}, fmtTime(r.ts)), h("td", { class: "method" }, r.method),
          h("td", { class: "mono" }, r.path), h("td", { class: statusClass(r.status) }, r.status), h("td", {}, r.durationMs)))))) : h("div", { class: "muted" }, "Nothing yet - send SCIM requests to see them here."),
        h("div", { style: "margin-top:10px" }, h("a", { href: `#/t/${tenant}/requests` }, "Open full request log"))),
      connectionCard(tenant));
    return shell(tenant, "dashboard", body);
  };
  const show = (el, quiet) => { el.classList.toggle("no-fade", !!quiet); $app.replaceChildren(el); };
  show(await render());
  // polled refreshes swap content silently: only the first render fades in
  setTimer(async () => { if (!document.hidden) show(await render(), true); }, 5000);
}

function bases(tenant) {
  const o = location.origin;
  return [
    ["Generic (host)", `${o}/t/${tenant}/scim/v2`],
    ["GitHub EMU alias (host)", `${o}/scim/v2/enterprises/${tenant}`],
    ["Generic (from another container)", `http://host.docker.internal:${location.port || 8100}/t/${tenant}/scim/v2`],
    ["EMU alias (from another container)", `http://host.docker.internal:${location.port || 8100}/scim/v2/enterprises/${tenant}`],
  ];
}
function connectionCard(tenant) {
  return h("div", { class: "card" }, h("h2", {}, "Connection info"),
    h("div", { class: "stack" },
      bases(tenant).map(([label, url]) => h("div", {}, h("div", { class: "small muted" }, label), copyBox(url))),
      h("div", {}, h("div", { class: "small muted" }, "Bearer token"), copyBox(CONFIG.token)),
      h("div", {}, h("div", { class: "small muted" }, "Try it"),
        copyBox(`curl -H "Authorization: Bearer ${CONFIG.token}" ${location.origin}/t/${tenant}/scim/v2/Users`))));
}
async function pageConnection(tenant) {
  $app.replaceChildren(shell(tenant, "connection", h("div", {}, head("Connection", "Point your SCIM client at one of these base URLs."), connectionCard(tenant))));
}

function userRow(tenant, u) {
  const e = u[ENT] || {};
  const email = (u.emails || []).find((x) => x.primary) || (u.emails || [])[0];
  return h("tr", { class: "clickable", onclick: () => nav(`#/t/${tenant}/users/${u.id}`) },
    h("td", {}, h("b", {}, u.userName)),
    h("td", {}, u.displayName || (u.name && (u.name.formatted || [u.name.givenName, u.name.familyName].filter(Boolean).join(" "))) || ""),
    h("td", { class: "mono" }, u.externalId || ""),
    h("td", {}, u.active === false ? badge("suspended", "warn") : badge("active", "ok")),
    h("td", {}, email ? email.value : "", (u.emails || []).length > 1 && h("span", { class: "sub" }, `+${u.emails.length - 1} more`)),
    h("td", {}, e.department || "", e.division && h("span", { class: "sub" }, e.division)),
    h("td", { class: "muted" }, ago(u.meta.lastModified)));
}
async function pageUsers(tenant) {
  const users = await api(`/tenants/${tenant}/users`);
  const q = h("input", { placeholder: "Search users...", style: "min-width:260px" });
  const tbody = h("tbody");
  const draw = () => {
    const t = q.value.toLowerCase();
    const rows = users.filter((u) => !t || JSON.stringify([u.userName, u.displayName, u.externalId, u.emails, u.name, u[ENT]]).toLowerCase().includes(t));
    tbody.replaceChildren(...(rows.length ? rows.map((u) => userRow(tenant, u)) : [h("tr", {}, h("td", { colspan: 7, class: "empty" }, "No users"))]));
    count.textContent = `${rows.length} of ${users.length}`;
  };
  const count = h("span", { class: "muted" });
  q.addEventListener("input", draw);
  $app.replaceChildren(shell(tenant, "users", h("div", {}, head("Users", "Provisioned user resources", count, q),
    h("div", { class: "table-wrap tall" }, h("table", {}, h("thead", {}, h("tr", {},
      ["userName", "Display name", "externalId", "Status", "Email", "Department / division", "Modified"].map((x) => h("th", {}, x)))), tbody)))));
  draw();
}
async function pageUser(tenant, id) {
  const [users, reqs] = await Promise.all([api(`/tenants/${tenant}/users`), api(`/tenants/${tenant}/requests?limit=200&resource=${id}`)]);
  const u = users.find((x) => x.id === id);
  if (!u) return $app.replaceChildren(shell(tenant, "users", h("div", { class: "notice warn" }, `User ${id} not found. `, h("a", { href: `#/t/${tenant}/users` }, "Back"))));
  const e = u[ENT] || {};
  $app.replaceChildren(shell(tenant, "users", h("div", { class: "stack" },
    head(u.userName, u.displayName, h("a", { class: "btn", href: `#/t/${tenant}/users` }, "Back to users")),
    h("div", { class: "grid cols-2" },
      h("div", { class: "card" }, h("h2", {}, "Summary"), h("dl", { class: "kv" },
        h("dt", {}, "id"), h("dd", { class: "mono" }, u.id), h("dt", {}, "externalId"), h("dd", { class: "mono" }, u.externalId || "-"),
        h("dt", {}, "status"), h("dd", {}, u.active === false ? badge("suspended", "warn") : badge("active", "ok")),
        h("dt", {}, "emails"), h("dd", {}, (u.emails || []).map((m) => `${m.value}${m.type ? " (" + m.type + ")" : ""}`).join(", ") || "-"),
        h("dt", {}, "department"), h("dd", {}, e.department || "-"), h("dt", {}, "division"), h("dd", {}, e.division || "-"),
        h("dt", {}, "version"), h("dd", {}, u.meta.version), h("dt", {}, "modified"), h("dd", {}, u.meta.lastModified),
        h("dt", {}, "groups"), h("dd", {}, (u.groups || []).length ? h("div", { class: "chips" }, u.groups.map((g) => h("a", { class: "chip", href: `#/t/${tenant}/groups` }, g.display))) : "-"))),
      h("div", { class: "card" }, h("h2", {}, "Full SCIM JSON"), h("pre", { class: "code" }, json(u)))),
    h("div", { class: "card" }, h("h2", {}, `Request history (${reqs.length})`), requestTable(reqs, false)))));
}

async function pageGroups(tenant) {
  const [groups, users] = await Promise.all([api(`/tenants/${tenant}/groups`), api(`/tenants/${tenant}/users`)]);
  const byId = Object.fromEntries(users.map((u) => [u.id, u]));
  const rows = groups.flatMap((g) => {
    const open = { v: false };
    const tr = h("tr", { class: "clickable" });
    const detail = h("tr", { class: "detail", hidden: true }, h("td", { colspan: 5 }, g.members.length ? h("div", { class: "chips" },
      g.members.map((m) => h("a", { class: "chip", href: `#/t/${tenant}/users/${m.value}` }, byId[m.value]?.userName || m.display || m.value))) : h("span", { class: "muted" }, "No members")));
    tr.append(h("td", {}, h("b", {}, g.displayName)), h("td", { class: "mono" }, g.externalId || ""), h("td", {}, badge(`${g.members.length} members`, "info")),
      h("td", { class: "mono muted" }, g.id), h("td", { class: "muted" }, ago(g.meta.lastModified)));
    tr.addEventListener("click", () => { open.v = !open.v; detail.hidden = !open.v; });
    return [tr, detail];
  });
  $app.replaceChildren(shell(tenant, "groups", h("div", {}, head("Groups", `${groups.length} groups - click a row to expand members`),
    h("div", { class: "table-wrap tall" }, h("table", {}, h("thead", {}, h("tr", {}, ["displayName", "externalId", "Members", "id", "Modified"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: 5, class: "empty" }, "No groups"))))))));
}

function requestTable(rows, expandable = true) {
  if (!rows.length) return h("div", { class: "muted" }, "No requests recorded.");
  const body = rows.flatMap((r) => {
    const detail = h("tr", { class: "detail", hidden: true }, h("td", { colspan: 6 },
      r.error && h("div", { class: "notice warn", style: "margin-bottom:8px" }, r.error),
      h("div", { class: "two" },
        h("div", {}, h("h3", {}, "Request body"), h("pre", { class: "code" }, r.requestBody ? pretty(r.requestBody) : "(empty)")),
        h("div", {}, h("h3", {}, "Response body"), h("pre", { class: "code" }, r.responseBody ? pretty(r.responseBody) : "(empty)"))),
      h("div", { class: "small muted", style: "margin-top:6px" }, `#${r.id} - auth ${r.authOk ? "ok" : "FAILED"}${r.resourceId ? " - resource " + r.resourceId : ""}`)));
    const tr = h("tr", { class: "logrow", onclick: () => { detail.hidden = !detail.hidden; } },
      h("td", { class: "mono" }, fmtTime(r.ts)), h("td", { class: "method" }, r.method), h("td", { class: "mono", style: "word-break:break-all" }, decodeURIComponent(r.path)),
      h("td", { class: "mono" }, r.resourceType || "", r.resourceId && h("span", { class: "sub" }, r.resourceId.slice(0, 8))),
      h("td", { class: "mono " + statusClass(r.status) }, h("b", {}, r.status)), h("td", {}, r.durationMs));
    return [tr, detail];
  });
  return h("div", { class: "table-wrap tall" }, h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Method", "Path", "Resource", "Status", "ms"].map((x) => h("th", {}, x)))), h("tbody", {}, body)));
}
async function pageRequests(tenant) {
  const method = h("select", {}, ["", "GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => h("option", { value: m }, m || "All methods")));
  const status = h("select", {}, [["", "All statuses"], ["2xx", "2xx"], ["4xx", "4xx"], ["401", "401"], ["404", "404"], ["409", "409"], ["429", "429"], ["5xx", "5xx"]].map(([v, l]) => h("option", { value: v }, l)));
  const auto = h("input", { type: "checkbox" });
  const holder = h("div", { class: "lazy" });
  let openIds = new Set();
  let loaded = false;
  const load = async () => {
    const qs = new URLSearchParams({ limit: "300" });
    if (method.value) qs.set("method", method.value);
    if (status.value) qs.set("status", status.value);
    const rows = await api(`/tenants/${tenant}/requests?${qs}`);
    const tbl = requestTable(rows);
    // preserve expanded rows across refreshes
    const trs = [...tbl.querySelectorAll("tr.logrow")];
    trs.forEach((tr, i) => { const d = tr.nextElementSibling; if (openIds.has(rows[i].id)) d.hidden = false;
      tr.addEventListener("click", () => { d.hidden ? openIds.delete(rows[i].id) : openIds.add(rows[i].id); }); });
    if (loaded) holder.classList.add("no-fade");  // only the first load fades in; refreshes must not re-fade
    holder.replaceChildren(tbl);
    loaded = true;
    count.textContent = `${rows.length} shown`;
  };
  const count = h("span", { class: "muted" });
  method.addEventListener("change", load); status.addEventListener("change", load);
  auto.addEventListener("change", () => { auto.checked ? setTimer(load, 2000) : clearTimer(); });
  $app.replaceChildren(shell(tenant, "requests", h("div", {}, head("Request log", "Every request to the SCIM endpoints (latest 5000 kept)",
    count, method, status, h("label", { class: "check" }, auto, "Auto-refresh 2s"), h("button", { onclick: load }, "Refresh"),
    h("button", { class: "danger", onclick: async () => { await api(`/tenants/${tenant}/requests`, { method: "DELETE" }); openIds.clear(); load(); } }, "Clear log")), holder)));
  await load();
}

async function pageFaults(tenant) {
  const f = await api(`/tenants/${tenant}/faults`);
  const field = (label, key, type, step, hint) => {
    const i = h("input", { type, step, value: type === "checkbox" ? null : f[key], checked: type === "checkbox" ? f[key] : null });
    return [h("label", { class: type === "checkbox" ? "check" : "field" }, type === "checkbox" ? [i, label] : [label, i]), i, hint];
  };
  const lat = field("Added latency (ms)", "latencyMs", "number", "50");
  const err = field("Error rate (% returning 500)", "errorRatePercent", "number", "1");
  const rl = field("Rate limit every N requests (429, 0 = off)", "rateLimitEvery", "number", "1");
  const ro = field("Read-only mode (writes return 503)", "readOnly", "checkbox");
  const save = async () => {
    const out = await putJson(`/tenants/${tenant}/faults`, { latencyMs: +lat[1].value, errorRatePercent: +err[1].value, rateLimitEvery: +rl[1].value, readOnly: ro[1].checked });
    toast("Fault settings saved"); Object.assign(f, out);
  };
  const off = async () => { await putJson(`/tenants/${tenant}/faults`, { latencyMs: 0, errorRatePercent: 0, rateLimitEvery: 0, readOnly: false }); pageFaults(tenant); toast("All faults disabled"); };
  $app.replaceChildren(shell(tenant, "faults", h("div", { class: "stack" }, head("Fault injection", "Applies to SCIM requests for this tenant only. Defaults are all off."),
    h("div", { class: "card" }, h("div", { class: "form-grid" }, lat[0], err[0], rl[0]), h("div", { style: "margin-top:14px" }, ro[0]),
      h("div", { class: "row", style: "margin-top:16px" }, h("button", { class: "primary", onclick: save }, "Save"), h("button", { onclick: off }, "Disable all"))),
    h("div", { class: "notice" }, "Order of evaluation: latency, then 429 rate limit (Retry-After: 1), then random 500, then 503 for writes in read-only mode."))));
}

/* ---------- router ---------- */
async function route() {
  clearTimer();
  if (cleanup) { cleanup(); cleanup = null; }
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  try {
    if (parts[0] !== "t" || !parts[1]) return await pagePicker();
    const [, tenant, page = "dashboard", id] = parts;
    if (page === "users" && id) return await pageUser(tenant, id);
    const pages = { dashboard: pageDashboard, users: pageUsers, groups: pageGroups, requests: pageRequests, faults: pageFaults, connection: pageConnection };
    return await (pages[page] || pageDashboard)(tenant);
  } catch (e) {
    $app.replaceChildren(h("main", {}, h("div", { class: "notice warn" }, "Error: " + e.message)));
  }
}
window.addEventListener("hashchange", route);
api("/config").then((c) => { CONFIG = c; }).catch(() => {}).finally(route);
