"use strict";
// COBOL-to-Decision explorer: plain JS against the /v1 API. No external libraries (runs offline).

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let token = localStorage.getItem("cobolToken") || "";
let selected = null;

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers["Authorization"] = "Bearer " + token;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { openLogin(); throw new Error("Please sign in"); }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(body?.error?.message || res.statusText);
  return body;
}

// ---- auth -------------------------------------------------------------------------
function openLogin() { $("#loginDialog").showModal(); $("#tokenInput").focus(); }
$("#loginBtn").onclick = () => {
  if (token) { token = ""; localStorage.removeItem("cobolToken"); $("#loginBtn").textContent = "Sign in"; toast("Signed out"); }
  else openLogin();
};
$("#loginForm").addEventListener("submit", (e) => {
  if (e.submitter?.value === "ok") {
    token = $("#tokenInput").value.trim();
    localStorage.setItem("cobolToken", token);
    $("#loginBtn").textContent = "Sign out";
    refreshAll();
  }
});

// ---- tabs -----------------------------------------------------------------------------
document.querySelectorAll("#tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + name));
  ({ rules: loadRules, review: loadReview, programs: loadPrograms, export: () => {}, score: loadScoreSets,
     insights: loadInsightSets, audit: loadAudit })[name]?.();
}

// ---- rendering helpers --------------------------------------------------------------------
const OPS = { "==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥", in: "is one of", not_in: "is not one of",
  between: "between", is_numeric: "is numeric", is_alphabetic: "is alphabetic", is_positive: "is positive",
  is_negative: "is negative", is_zero: "is zero" };
const fmtVal = (v) => Array.isArray(v) ? v.map(fmtVal).join(", ") : (typeof v === "string" ? `'${v}'` : v);

function condHtml(c) {
  if (!c) return "";
  if (c.all) return c.all.length ? group("ALL of", c.all) : `<span class="muted">always</span>`;
  if (c.any) return group("ANY of", c.any);
  if (c.not) return `<span class="conj">NOT</span> ${condHtml(c.not)}`;
  const rhs = c.ref ? `<span class="fld" title="COBOL field">${esc(c.ref)}</span>` :
    c.op === "between" ? `<span class="val">${esc(c.value[0])}</span> and <span class="val">${esc(c.value[1])}</span>` :
    c.value === undefined || c.value === null ? "" : `<span class="val">${esc(fmtVal(c.value))}</span>`;
  return `<span class="fld" title="COBOL: ${esc(c.source_name)}">${esc(c.field)}</span> ${esc(OPS[c.op] || c.op)} ${rhs}`;
}
function group(label, items) {
  if (items.length === 1) return condHtml(items[0]);
  return `<div><span class="conj">${label}</span><ul>${items.map((x) => `<li>${condHtml(x)}</li>`).join("")}</ul></div>`;
}
function actHtml(a) {
  const tgt = `<span class="fld" title="COBOL: ${esc(a.source_name)}">${esc(a.target || a.source_name)}</span>`;
  if (a.type === "set") return `set ${tgt} = ${a.expr ? `<code>${esc(a.expr)}</code>` : `<span class="val">${esc(fmtVal(a.value))}</span>`}`;
  if (a.type === "compute") return `compute ${tgt} = <code>${esc(a.expr)}</code>`;
  if (a.type === "perform") return `perform paragraph <code>${esc(a.source_name)}</code>`;
  return `call external program <code>${esc(a.source_name)}</code>`;
}
function confBar(score) {
  if (score === null || score === undefined) return `<span class="muted">n/a</span>`;
  const cls = score >= 0.9 ? "" : score >= 0.7 ? "low" : "bad";
  return `<span class="conf"><span class="bar ${cls}"><i style="width:${Math.round(score * 100)}%"></i></span>${score.toFixed(2)}</span>`;
}
const badge = (s) => `<span class="badge ${esc(s)}">${esc(s.replace("_", " "))}</span>`;

function itemHtml(r) {
  return `<div class="item" data-id="${esc(r.rule_id)}">
    <div class="t">${esc(r.title || r.rule_id)}</div>
    <div class="muted">${esc(r.intent || "")}</div>
    <div class="meta">${badge(r.status)} ${confBar(r.confidence?.score)} <span>${esc(r.rule_id)}</span>
      <span>${esc(r.trace.program)}:${r.trace.line_start}-${r.trace.line_end}</span>
      ${(r.concepts || []).slice(0, 4).map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div></div>`;
}

// ---- rules list -------------------------------------------------------------------------------
async function loadRules() {
  const p = new URLSearchParams({ page_size: 200 });
  const q = $("#q").value.trim();
  if (q) p.set("q", q);
  if ($("#fStatus").value) p.set("status", $("#fStatus").value);
  if ($("#fDomain").value) p.set("domain", $("#fDomain").value);
  if ($("#fProgram").value) p.set("program", $("#fProgram").value);
  if (+$("#fConf").value > 0) p.set("min_confidence", $("#fConf").value);
  try {
    const data = await api("/v1/rules?" + p);
    $("#resultCount").textContent = `${data.total} rule${data.total === 1 ? "" : "s"}${q ? ` for “${q}”` : ""}`;
    $("#ruleList").innerHTML = data.items.map(itemHtml).join("") || `<div class="empty">No rules match.</div>`;
    bindList("#ruleList", "#ruleDetail");
  } catch (e) { toast(e.message); }
}
function bindList(listSel, detailSel) {
  document.querySelectorAll(listSel + " .item").forEach((el) => el.onclick = () => {
    document.querySelectorAll(listSel + " .item").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel");
    showRule(el.dataset.id, detailSel);
  });
}
["#fStatus", "#fDomain", "#fProgram"].forEach((s) => $(s).onchange = loadRules);
$("#fConf").oninput = () => { $("#fConfOut").textContent = (+$("#fConf").value).toFixed(2); };
$("#fConf").onchange = loadRules;
$("#searchForm").onsubmit = (e) => { e.preventDefault(); showTab("rules"); };

// ---- rule detail ----------------------------------------------------------------------------------
async function showRule(id, detailSel) {
  selected = id;
  const box = $(detailSel);
  box.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const [r, tr, hist] = await Promise.all([api(`/v1/rules/${id}`), api(`/v1/rules/${id}/trace?context=10`), api(`/v1/rules/${id}/history`)]);
    const comps = r.confidence?.components || {};
    const compRows = ["structural", "differential", "consistency", "naming"].map((k) =>
      `<span>${k}</span>${comps[k] === null || comps[k] === undefined ? `<span class="muted">not measured</span><span></span>` :
        `<span class="bar ${comps[k] >= 0.9 ? "" : comps[k] >= 0.7 ? "low" : "bad"}"><i style="width:${Math.round(comps[k] * 100)}%"></i></span><span>${comps[k].toFixed(2)}</span>`}`).join("");
    const src = tr.lines.map((l) => `<div class="${l.in_rule ? "on" : ""}"><span class="n">${l.n}</span>${esc(l.text)}</div>`).join("");
    box.innerHTML = `
      <div class="dhead"><div><h2>${esc(r.title || r.rule_id)}</h2>
        <div class="muted">${esc(r.rule_id)} · v${r.version}${r.domain ? " · " + esc(r.domain) : ""} · ${badge(r.status)}</div></div>
        <div>${confBar(r.confidence?.score)}</div></div>
      <div class="intent">${esc(r.intent || "No intent yet (AST-only extraction).")}</div>
      ${(r.concepts || []).map((c) => `<span class="chip">${esc(c)}</span>`).join("")}
      <div class="cols">
        <div>
          <h4>When</h4><div class="cond">${condHtml(r.conditions)}</div>
          <h4>Then</h4><ul class="acts">${r.actions.map((a) => `<li>${actHtml(a)}</li>`).join("") || "<li class='muted'>no actions</li>"}</ul>
          ${r.else_actions?.length ? `<h4>Else</h4><ul class="acts">${r.else_actions.map((a) => `<li>${actHtml(a)}</li>`).join("")}</ul>` : ""}
          <h4>Confidence</h4><div class="comps">${compRows}</div>
          ${(r.confidence?.warnings || []).map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}
          ${r.external_dependency ? `<div class="warn">⚠ external dependency</div>` : ""}
          <h4>Trace</h4><div class="muted">${esc(tr.file)} · paragraph ${esc(r.trace.paragraph || "-")} · lines ${r.trace.line_start}–${r.trace.line_end}
            ${r.trace.copybooks?.length ? " · copybooks " + r.trace.copybooks.map(esc).join(", ") : ""}</div>
        </div>
        <div><h4>Source</h4><div class="src" id="srcBox">${src}</div></div>
      </div>
      <div class="review">
        <h4>Review</h4>
        <textarea id="reason" placeholder="Reason (required to approve or reject), e.g. matches policy section 4"></textarea>
        <div class="row">
          <button id="approveBtn">Approve</button><button id="rejectBtn" class="danger">Reject</button>
          <button id="editBtn" class="ghost">Edit title / intent</button>
        </div>
        <h4>History</h4>
        <div class="muted">${hist.reviews.map((h) => `${esc(h.ts)} · ${esc(h.user)} · <b>${esc(h.action)}</b> v${h.version} — ${esc(h.reason)}`).join("<br>") || "No review actions yet."}</div>
      </div>`;
    const on = box.querySelector(".src .on");
    if (on) on.scrollIntoView({ block: "center" });
    box.querySelector("#approveBtn").onclick = () => review(id, "approve", detailSel);
    box.querySelector("#rejectBtn").onclick = () => review(id, "reject", detailSel);
    box.querySelector("#editBtn").onclick = () => editRule(r, detailSel);
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
async function review(id, action, detailSel) {
  const reason = $("#reason").value.trim();
  if (!reason) return toast("Please give a reason.");
  try {
    await api(`/v1/rules/${id}`, { method: "PATCH", json: { action, reason } });
    toast(`${id} ${action === "approve" ? "approved" : "rejected"}`);
    showRule(id, detailSel);
    refreshCounts();
  } catch (e) { toast(e.message); }
}
async function editRule(r, detailSel) {
  const title = prompt("Title", r.title);
  if (title === null) return;
  const intent = prompt("Intent", r.intent);
  if (intent === null) return;
  const reason = $("#reason").value.trim() || "edited title/intent";
  try {
    await api(`/v1/rules/${r.rule_id}`, { method: "PATCH", json: { action: "edit", reason, changes: { title, intent } } });
    toast("Saved as a new version");
    showRule(r.rule_id, detailSel);
  } catch (e) { toast(e.message); }
}

// ---- review queue -------------------------------------------------------------------------------
async function loadReview() {
  try {
    const [a, b] = await Promise.all([api("/v1/rules?status=needs_review&page_size=200"), api("/v1/rules?status=candidate&page_size=200")]);
    const items = [...a.items, ...b.items].sort((x, y) => (x.confidence.score ?? 0) - (y.confidence.score ?? 0));
    $("#reviewList").innerHTML = items.map(itemHtml).join("") || `<div class="empty">Nothing to review.</div>`;
    bindList("#reviewList", "#reviewDetail");
  } catch (e) { toast(e.message); }
}
async function refreshCounts() {
  try {
    const a = await api("/v1/rules?status=needs_review&page_size=1");
    $("#reviewCount").textContent = a.total || "";
  } catch (e) { /* signed out */ }
}

// ---- programs ------------------------------------------------------------------------------------
async function loadPrograms() {
  try {
    const data = await api("/v1/programs");
    $("#programList").innerHTML = `<table><thead><tr><th>Program</th><th>Status</th><th>Rules</th><th>Report</th><th></th></tr></thead><tbody>${
      data.items.map((p) => `<tr><td><b>${esc(p.program_file)}</b><div class="muted">${esc(p.id)} · ${esc(p.created_at)}</div></td>
        <td>${esc(p.status)}</td>
        <td>${Object.entries(p.rule_counts).map(([s, n]) => `${badge(s)} ${n}`).join(" ") || "-"}</td>
        <td class="muted">${p.report?.model ? `model ${esc(p.report.model)}` : ""}${p.report?.differential ? ` · COBOL agreement ${p.report.differential.agreement}` : ""}</td>
        <td><button class="ghost" data-extract="${esc(p.id)}">Extract</button></td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("[data-extract]").forEach((b) => b.onclick = () => startExtract(b.dataset.extract));
    fillSelects(data.items);
  } catch (e) { toast(e.message); }
}
$("#uploadForm").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData();
  fd.append("program", $("#upProgram").files[0]);
  for (const f of $("#upCopybooks").files) fd.append("copybooks", f);
  try {
    const p = await api("/v1/programs", { method: "POST", body: fd });
    $("#uploadStatus").textContent = `Uploaded ${p.name} (${p.program_id}).`;
    startExtract(p.program_id);
  } catch (err) { toast(err.message); }
};
async function startExtract(pid) {
  try {
    const job = await api(`/v1/programs/${pid}/extract`, { method: "POST" });
    pollJob(job.job_id);
  } catch (e) { toast(e.message); }
}
async function pollJob(jid) {
  const box = $("#uploadStatus");
  try {
    const j = await api(`/v1/jobs/${jid}`);
    const pct = j.slices_total ? Math.round(100 * j.slices_done / j.slices_total) : 5;
    box.innerHTML = `Job ${esc(jid)}: <b>${esc(j.state)}</b> · ${esc(j.stage)} ${j.slices_done}/${j.slices_total}
      <span class="progress"><i style="width:${j.state === "done" ? 100 : pct}%"></i></span> ${j.error ? `<div class="warn">${esc(j.error)}</div>` : ""}`;
    if (j.state === "done" || j.state === "failed") { loadPrograms(); refreshCounts(); if (j.state === "done") toast("Extraction finished"); return; }
  } catch (e) { toast(e.message); return; }
  setTimeout(() => pollJob(jid), 1000);
}

// ---- selects shared by filters / export / score / insights -----------------------------------------
async function fillSelects(programs) {
  if (!programs) { try { programs = (await api("/v1/programs")).items; } catch (e) { return; } }
  const names = [...new Set(programs.map((p) => p.program_file))].sort();
  for (const sel of ["#fProgram", "#exProgram"]) {
    const cur = $(sel).value;
    $(sel).innerHTML = `<option value="">${sel === "#exProgram" ? "all" : "any"}</option>` + names.map((n) => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("");
  }
  for (const sel of ["#scSet", "#inSet"]) {
    const cur = $(sel).value;
    $(sel).innerHTML = names.map((n) => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("");
  }
  try {
    const rules = await api("/v1/rules?page_size=200");
    const domains = [...new Set(rules.items.map((r) => r.domain).filter(Boolean))].sort();
    const cur = $("#fDomain").value;
    $("#fDomain").innerHTML = `<option value="">any</option>` + domains.map((d) => `<option ${d === cur ? "selected" : ""}>${esc(d)}</option>`).join("");
  } catch (e) { /* ignore */ }
}

// ---- export ----------------------------------------------------------------------------------------
document.querySelectorAll("[data-export]").forEach((b) => b.onclick = async () => {
  const fmt = b.dataset.export;
  const p = new URLSearchParams({ format: fmt });
  if ($("#exProgram").value) p.set("program", $("#exProgram").value);
  if ($("#exStatus").value) p.set("status", $("#exStatus").value);
  try {
    const res = await fetch("/v1/export?" + p, { headers: { Authorization: "Bearer " + token } });
    if (!res.ok) throw new Error((await res.json()).error.message);
    const text = await res.text();
    const pretty = fmt === "pmml" ? text : JSON.stringify(JSON.parse(text), null, 2);
    $("#exportPreview").textContent = pretty.slice(0, 20000);
    const blob = new Blob([pretty], { type: fmt === "pmml" ? "application/xml" : "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `rules-${fmt}.${fmt === "pmml" ? "pmml" : "json"}`;
    a.click();
  } catch (e) { toast(e.message); }
});

// ---- score -------------------------------------------------------------------------------------------
async function loadScoreSets() { await fillSelects(); buildScoreForm(); }
$("#scSet").onchange = buildScoreForm;
async function buildScoreForm() {
  const set = $("#scSet").value;
  if (!set) return;
  try {
    const info = await api(`/v1/rule_sets/${encodeURIComponent(set)}/inputs`);
    if (!info.approved_rules) {
      $("#scoreForm").innerHTML = `<div class="muted">No approved rules in ${esc(set)} yet. Approve some in the review queue first.</div>`;
      return;
    }
    $("#scoreForm").innerHTML = Object.entries(info.inputs).map(([k, v]) => `<label>${esc(k)}
        ${v.kind === "number" ? `<input name="${esc(k)}" type="number" step="any" value="${esc(v.values[0] ?? 0)}">`
        : `<select name="${esc(k)}">${v.values.map((x) => `<option>${esc(x)}</option>`).join("")}<option value="">(other)</option></select>`}</label>`).join("")
      + `<div><button type="submit">Decide</button></div>`;
  } catch (e) { toast(e.message); }
}
$("#scoreForm").onsubmit = async (e) => {
  e.preventDefault();
  const inputs = {};
  for (const el of $("#scoreForm").elements) if (el.name) inputs[el.name] = el.type === "number" ? Number(el.value) : el.value;
  try {
    const r = await api("/v1/score", { method: "POST", json: { rule_set: $("#scSet").value, inputs } });
    $("#scoreResult").innerHTML = `<div class="decision"><b>Decision:</b> ${Object.entries(r.decision).map(([k, v]) => `${esc(k)} = <span class="val">${esc(v)}</span>`).join(", ")}</div>
      <div class="muted">Fired rules: ${r.fired_rules.map(esc).join(", ") || "none"} · ${r.latency_ms} ms</div>`;
  } catch (err) { toast(err.message); }
};

// ---- insights -------------------------------------------------------------------------------------------
async function loadInsightSets() { await fillSelects(); loadShap(); }
$("#inSet").onchange = loadShap;
async function loadShap() {
  const set = $("#inSet").value;
  if (!set) return;
  $("#shap").innerHTML = `<div class="muted">Training surrogate…</div>`;
  try {
    const r = await api(`/v1/explain/${encodeURIComponent(set)}`);
    const max = Math.max(...r.features.map((f) => f.importance), 0.0001);
    $("#shap").innerHTML = `<div class="muted">Target <b>${esc(r.target)}</b> · ${esc(r.method)} · surrogate fidelity ${r.surrogate_fidelity ?? "-"} · rules ${r.rule_statuses.join(", ")}</div>` +
      r.features.map((f) => `<div class="shapbar"><span>${esc(f.name)}</span><span class="bar"><i style="width:${Math.round(100 * f.importance / max)}%"></i></span><span>${(100 * f.importance).toFixed(1)}%</span></div>`).join("");
  } catch (e) { $("#shap").innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
}
$("#conflictBtn").onclick = async () => {
  $("#conflicts").innerHTML = `<div class="muted">Checking…</div>`;
  try {
    const r = await api("/v1/conflicts");
    $("#conflicts").innerHTML = r.items.length ? `<table><thead><tr><th>Rules</th><th>Kind</th><th>Reason</th><th>Example inputs</th></tr></thead><tbody>${
      r.items.map((c) => `<tr><td>${c.rules.map(esc).join(" ↔ ")}</td><td>${esc(c.kind)}</td><td>${esc(c.reason)}</td><td><code>${esc(JSON.stringify(c.example_inputs || {}))}</code></td></tr>`).join("")}</tbody></table>`
      : `<div class="muted">No duplicates or conflicts found.</div>`;
  } catch (e) { toast(e.message); }
};

// ---- audit -------------------------------------------------------------------------------------------------
async function loadAudit() {
  try {
    const r = await api("/v1/audit?limit=200");
    $("#auditList").innerHTML = `<table><thead><tr><th>Time</th><th>User</th><th>Action</th><th>Rule</th><th>Reason</th></tr></thead><tbody>${
      r.items.map((a) => `<tr><td>${esc(a.ts)}</td><td>${esc(a.user)}</td><td>${esc(a.action)}</td><td>${esc(a.rule_id)} v${a.version}</td><td>${esc(a.reason)}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { $("#auditList").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

// ---- start --------------------------------------------------------------------------------------------------
async function health() {
  try {
    const h = await (await fetch("/v1/health")).json();
    const el = $("#health");
    el.className = "health " + (h.toolchain && h.llm_available ? "ok" : "partial");
    el.title = `GnuCOBOL: ${h.toolchain || "not found"} · LLM: ${h.llm_model || "off"}${h.llm_available ? "" : " (unavailable)"}`;
  } catch (e) { /* offline */ }
}
function refreshAll() { fillSelects(); loadRules(); refreshCounts(); }
if (token) $("#loginBtn").textContent = "Sign out";
health();
if (token) refreshAll(); else openLogin();
