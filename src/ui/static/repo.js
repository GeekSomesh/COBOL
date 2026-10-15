"use strict";
// Repository view (call graph, impact, drift), collaboration (assign, comments, approvals),
// notifications, glossary and audit-chain check. Uses $, api, esc, toast, badge from app.js.

// ---- repository -------------------------------------------------------------------------------------
const KIND_COLOR = { job: "#7a5af5", step: "#a78bfa", program: "#2f5bd3", external: "#98a2b3", copybook: "#1f8a4c", dataset: "#b26a00" };
const EDGE_COLOR = { CALLS: "#2f5bd3", RUNS: "#7a5af5", BUILDS: "#c4b5fd", INCLUDES: "#1f8a4c", READS: "#b26a00", WRITES: "#c0362c",
  UPDATES: "#c0362c", HAS_STEP: "#a78bfa" };

function graphSvg(g) {
  // columns: jobs | steps | programs | copybooks + datasets; rows in order of appearance
  const col = { job: 0, step: 1, program: 2, external: 2, copybook: 3, dataset: 3 };
  const rows = [0, 0, 0, 0];
  const pos = {};
  const W = 190, H = 26, GX = 70, GY = 10;
  for (const n of g.nodes) {
    const c = col[n.kind] ?? 3;
    pos[n.id] = { x: 10 + c * (W + GX), y: 10 + rows[c] * (H + GY), n };
    rows[c] += 1;
  }
  const height = 20 + Math.max(...rows, 1) * (H + GY);
  const width = 20 + 4 * (W + GX);
  const edges = g.edges.filter((e) => pos[e.from] && pos[e.to]).map((e) => {
    const a = pos[e.from], b = pos[e.to];
    const x1 = a.x + (b.x >= a.x ? W : 0), y1 = a.y + H / 2, x2 = b.x + (b.x >= a.x ? 0 : W), y2 = b.y + H / 2;
    const mx = (x1 + x2) / 2 + (x1 === x2 ? 40 : 0);
    return `<path d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" fill="none" stroke="${EDGE_COLOR[e.type] || "#999"}"
      stroke-width="1.4" marker-end="url(#arrow)" opacity="0.8"><title>${esc(e.type)}${e.via ? " via " + esc(e.via) : ""}</title></path>`;
  }).join("");
  const nodes = Object.values(pos).map(({ x, y, n }) => `<g class="node" tabindex="0" data-node="${esc(n.id)}" role="button"
      aria-label="${esc(n.kind)} ${esc(n.label)}"><rect x="${x}" y="${y}" width="${W}" height="${H}" rx="5"
      fill="${KIND_COLOR[n.kind] || "#888"}22"></rect><text x="${x + 8}" y="${y + 17}">${esc(n.kind[0].toUpperCase())} · ${esc(n.label.slice(0, 26))}</text>
      <title>${esc(n.kind)} ${esc(n.label)}${n.files ? " (" + esc(n.files.join(", ")) + ")" : ""}${n.rules ? ", " + n.rules + " rules" : ""}</title></g>`).join("");
  const legend = Object.entries(KIND_COLOR).map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("");
  return `<div class="legend muted">${legend} · edges: ${Object.keys(EDGE_COLOR).join(", ").toLowerCase()} (hover for details)</div>
    <svg width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="#667085"></path></marker></defs>${edges}${nodes}</svg>`;
}

async function loadRepository() {
  try {
    const g = await api("/v1/repository");
    const s = g.summary;
    $("#repoSummary").textContent = `${s.programs} programs · ${s.jobs} JCL jobs · ${s.copybooks} copybooks · ${s.datasets} datasets · ${s.rules} rules`;
    $("#repoGraph").innerHTML = g.nodes.length ? graphSvg(g) : `<div class="empty">Upload programs (and JCL) to see the graph.</div>`;
    $("#repoFindings").innerHTML = g.findings.length ? `<h4>Findings</h4><ul>${g.findings.map((f) =>
      `<li><b>${esc(f.kind.replaceAll("_", " "))}</b>: ${esc(f.detail)}</li>`).join("")}</ul>` : "";
    document.querySelectorAll("#repoGraph [data-node]").forEach((el) => {
      const go = () => {
        const [kind, name] = el.dataset.node.split(/:(.*)/s);
        const map = { program: "program", external: "program", copybook: "copybook", dataset: "dataset" };
        if (!map[kind]) return;
        $("#impKind").value = map[kind]; $("#impName").value = name; showImpact();
      };
      el.onclick = go;
      el.onkeydown = (e) => { if (e.key === "Enter") go(); };
    });
    const progs = (await api("/v1/programs")).items;
    for (const sel of ["#driftOld", "#driftNew"]) {
      $(sel).innerHTML = progs.map((p) => `<option value="${esc(p.id)}">${esc(p.program_file)} · ${esc(p.id)} · ${esc(p.created_at)}</option>`).join("");
    }
    if (progs.length > 1) $("#driftOld").selectedIndex = 1;
  } catch (e) { toast(e.message); }
}
window.loadRepository = loadRepository;

async function showImpact() {
  const p = new URLSearchParams({ kind: $("#impKind").value, name: $("#impName").value.trim() });
  try {
    const r = await api("/v1/impact?" + p);
    $("#impactResult").innerHTML = `<p>Programs: <b>${r.programs.map(esc).join(", ") || "none"}</b>
      ${r.callers.length ? ` · callers (transitively): <b>${r.callers.map(esc).join(", ")}</b>` : ""}
      ${r.jobs.length ? ` · JCL jobs: <b>${r.jobs.map(esc).join(", ")}</b> (steps ${r.job_steps.map(esc).join(", ")})` : ""}</p>
      ${r.rules.length ? `<table><thead><tr><th>Rule</th><th>Program</th><th>Paragraph</th><th>How</th></tr></thead><tbody>${
        r.rules.map((x) => `<tr><td><a href="#" data-rule="${esc(x.rule_id)}">${esc(x.rule_id)}</a></td><td>${esc(x.program)} <span class="muted">${esc(x.file || "")}</span></td>
          <td>${esc(x.paragraph || "")}</td><td>${x.how.map(esc).join(", ")}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">No rules involved directly.</div>`}`;
    document.querySelectorAll("#impactResult [data-rule]").forEach((a) => a.onclick = (e) => {
      e.preventDefault(); showTab("rules"); showRule(a.dataset.rule, "#ruleDetail");
    });
  } catch (e) { toast(e.message); }
}
$("#impactForm").onsubmit = (e) => { e.preventDefault(); showImpact(); };

$("#jclForm").onsubmit = async (e) => {
  e.preventDefault();
  const f = $("#jclFile").files[0];
  if (!f) return toast("Choose a JCL file.");
  const fd = new FormData();
  fd.append("jcl", f);
  try {
    const r = await api("/v1/jcl", { method: "POST", body: fd });
    toast(`JCL ${r.name}: ${r.jobs.map((j) => `${j.job} (${j.steps.length} steps)`).join(", ")}`);
    loadRepository();
  } catch (err) { toast(err.message); }
};

$("#driftForm").onsubmit = async (e) => {
  e.preventDefault();
  const p = new URLSearchParams({ old: $("#driftOld").value, new: $("#driftNew").value });
  $("#driftResult").innerHTML = `<div class="muted">Comparing…</div>`;
  try {
    const d = await api("/v1/diff?" + p);
    const s = d.summary;
    $("#driftResult").innerHTML = `<p>${s.changed} changed · ${s.added} added · ${s.removed} removed · ${s.moved} moved · ${s.unchanged} unchanged
      ${s.approved_rules_affected ? ` · <b class="warn">${s.approved_rules_affected} approved rule(s) affected</b>` : ""}</p>
      ${d.changed.map((c) => `<div class="card"><b>${esc(c.old)}</b> → <b>${esc(c.new)}</b> <span class="muted">${esc(c.paragraph || "")}</span>
        ${c.was_approved ? badge("approved") : ""}<ul>${c.changes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <div class="muted">Before: ${esc(c.before.when)} → ${c.before.then.map(esc).join("; ")}</div>
        <div class="muted">After: ${esc(c.after.when)} → ${c.after.then.map(esc).join("; ")}</div>
        ${c.example_where_they_differ ? `<div>Input where they differ: <code>${esc(JSON.stringify(c.example_where_they_differ))}</code></div>` : ""}</div>`).join("")}
      ${d.added.length ? `<h4>Added</h4><ul>${d.added.map((a) => `<li>${esc(a.rule_id)}: ${esc(a.when)}</li>`).join("")}</ul>` : ""}
      ${d.removed.length ? `<h4>Removed</h4><ul>${d.removed.map((a) => `<li>${esc(a.rule_id)} ${a.was_approved ? badge("approved") : ""}</li>`).join("")}</ul>` : ""}`;
  } catch (err) { $("#driftResult").innerHTML = ""; toast(err.message); }
};

// ---- collaboration in the rule panel -------------------------------------------------------------------
window.bindCollaboration = async (r, detailSel) => {
  const box = $(detailSel);
  const render = async () => {
    try {
      const [c, q] = await Promise.all([api(`/v1/rules/${r.rule_id}/comments`), api("/v1/review_queue")]);
      const item = q.items.find((x) => x.rule_id === r.rule_id);
      const a = c.assignment;
      box.querySelector("#assignInfo").innerHTML = (a ? `Assigned to <b>${esc(a.assignee)}</b> by ${esc(a.assigned_by)}. ` : "Not assigned. ")
        + (item ? `Approvals: ${item.approvals.length} of ${item.approvals_required}${item.approvals.length ? " (" + item.approvals.map(esc).join(", ") + ")" : ""}.` : "");
      box.querySelector("#commentList").innerHTML = c.items.map((x) => `<div><b>${esc(x.user)}</b> <span class="muted">${esc(x.ts)} · v${x.version}</span><br>${esc(x.text)}</div>`).join("") || "No comments yet.";
    } catch (e) { /* viewer without access to some parts */ }
  };
  box.querySelector("#assignBtn").onclick = async () => {
    const who = box.querySelector("#assignee").value.trim();
    if (!who) return toast("Who should review it?");
    try { await api(`/v1/rules/${r.rule_id}/assign`, { method: "POST", json: { assignee: who } }); toast(`Assigned to ${who}`); render(); }
    catch (e) { toast(e.message); }
  };
  box.querySelector("#commentBtn").onclick = async () => {
    const text = box.querySelector("#commentText").value.trim();
    if (!text) return;
    try { await api(`/v1/rules/${r.rule_id}/comments`, { method: "POST", json: { text } }); box.querySelector("#commentText").value = ""; render(); }
    catch (e) { toast(e.message); }
  };
  render();
};

// ---- notifications ----------------------------------------------------------------------------------------
async function loadBell() {
  if (!token) return;
  try {
    const r = await api("/v1/notifications");
    const unread = r.items.filter((n) => !n.read).length;
    $("#bellCount").textContent = unread || "";
    $("#bellPanel").innerHTML = `<h4>Notifications</h4>` + (r.items.map((n) => `<div class="n ${n.read ? "" : "unread"}" tabindex="0"
        data-id="${n.id}" data-rule="${esc(n.rule_id || "")}">${esc(n.text)}<div class="muted">${esc(n.ts)}</div></div>`).join("") || `<div class="muted">Nothing yet.</div>`);
    document.querySelectorAll("#bellPanel .n").forEach((el) => {
      const go = async () => {
        await api(`/v1/notifications/${el.dataset.id}/read`, { method: "POST" }).catch(() => {});
        if (el.dataset.rule) { showTab("rules"); showRule(el.dataset.rule, "#ruleDetail"); }
        $("#bellPanel").hidden = true; $("#bellBtn").setAttribute("aria-expanded", "false"); loadBell();
      };
      el.onclick = go;
      el.onkeydown = (e) => { if (e.key === "Enter") go(); };
    });
  } catch (e) { /* signed out */ }
}
$("#bellBtn").onclick = () => {
  const panel = $("#bellPanel");
  panel.hidden = !panel.hidden;
  $("#bellBtn").setAttribute("aria-expanded", panel.hidden ? "false" : "true");
  if (!panel.hidden) loadBell();
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#bellPanel").hidden) { $("#bellPanel").hidden = true; $("#bellBtn").focus(); } });
setInterval(loadBell, 30000);
loadBell();

// ---- glossary ------------------------------------------------------------------------------------------------
async function loadGlossary() {
  try { $("#glossaryText").value = JSON.stringify(await api("/v1/glossary"), null, 2); } catch (e) { /* signed out */ }
}
$("#glossarySave").onclick = async () => {
  let body;
  try { body = JSON.parse($("#glossaryText").value || "{}"); } catch (e) { return toast("Glossary is not valid JSON."); }
  try {
    const g = await api("/v1/glossary", { method: "PUT", json: body });
    $("#glossaryText").value = JSON.stringify(g, null, 2);
    $("#glossaryStatus").textContent = `Saved: ${Object.keys(g.fields).length} fields, ${Object.keys(g.terms).length} terms.`;
  } catch (e) { toast(e.message); }
};
document.querySelector('[data-tab="programs"]').addEventListener("click", loadGlossary);

// ---- audit chain ----------------------------------------------------------------------------------------------
$("#verifyChain").onclick = async () => {
  try {
    const v = await api("/v1/audit/verify");
    $("#chainStatus").textContent = v.ok ? `Intact: ${v.entries} entries, head ${v.head.slice(0, 12)}…`
      : `BROKEN at entry ${v.broken_at}: the log was changed after it was written.`;
    $("#chainStatus").className = v.ok ? "muted" : "warn";
  } catch (e) { toast(e.message); }
};
