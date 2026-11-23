const app = document.getElementById("app");
let dashboardTimer = null;
let currentProductId = "rag-demo";

document.addEventListener("DOMContentLoaded", () => {
  const select = document.getElementById("product-switch");
  if (select) {
    select.addEventListener("change", async (e) => {
      currentProductId = e.target.value;
      await api(`/v1/active-product?product_id=${currentProductId}`, { method: "PUT" });
      route();
    });
  }
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function words(value) {
  if (!value) return "Not available";
  const known = {
    "prompt.changed": "System prompt changed",
    "retrieval.corpus_updated": "Knowledge corpus updated",
    "impact_ready": "Ready to run",
    "awaiting_approval": "Awaiting approval",
    "quality_fail": "Quality failed",
    "infra_fail": "Infrastructure failed",
    "human_approval_required": "Human approval required",
  };
  return known[value] || String(value)
    .replaceAll("_", " ")
    .replaceAll(".", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function tag(value, inverse = false) {
  return `<span class="tag ${inverse ? "inverse" : ""}">${words(value)}</span>`;
}

function thresholdText(comparison, value) {
  if (value === undefined || value === null) return "Not set";
  const wording = { gte: "at least", lte: "at most", lt: "below", gt: "above" };
  return `${wording[comparison] || words(comparison)} ${value}`;
}

function identifier(value) {
  return String(value || "").replaceAll("_", "–");
}

function pageHead(index, title, description = "", back = "") {
  return `
    ${back ? `<a class="back-link" href="${back}">&larr; Back</a>` : ""}
    <header class="page-head">
      <div>
        <p class="kicker">${index} / AGENT 07</p>
        <h1>${title}</h1>
      </div>
      ${description ? `<p class="posture-note">${description}</p>` : ""}
    </header>`;
}

function stat(label, value) {
  return `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
}

function formatTime(value) {
  if (!value) return "Not available";
  return new Date(value).toLocaleString();
}

function staleFor(change) {
  if (!change.affected_metric_ids?.length) {
    return `<span class="caption">Impact not mapped yet</span>`;
  }
  return `<div class="metric-list">${change.affected_metric_ids
    .map((metric) => `<span class="metric">${metric} · STALE</span>`).join("")}</div>`;
}

function changeItem(change, showRun = true) {
  const openLabel = change.status === "awaiting_approval"
    ? "Review and decide"
    : change.comparisons?.length ? "Open results" : "Open change";
  const action = ["detected", "impact_ready"].includes(change.status) && showRun
    ? `<button class="primary run-now" data-change="${change.change_id}">Run revalidation</button>
       <a class="link-button" href="#/changes/${change.change_id}">Open change</a>`
    : `<a class="link-button" href="#/changes/${change.change_id}">${openLabel}</a>`;
  return `
    <article class="item">
      <div>
        ${tag(change.status, change.status === "awaiting_approval")}
        <p class="caption">${formatTime(change.created_at)}</p>
      </div>
      <div>
        <p class="item-title">${words(change.event.name)}</p>
        <p class="muted">${change.detail}</p>
        ${staleFor(change)}
      </div>
      <div>${action}</div>
    </article>`;
}

function notificationItem(note) {
  return `
    <article class="item">
      <div>${tag(note.channel)}</div>
      <div>
        <p class="item-title">${words(note.kind)}</p>
        <p class="caption">${identifier(note.references.change_ref || "")}</p>
      </div>
      <div>${tag(note.status)}</div>
    </article>`;
}

function bindRunButtons() {
  document.querySelectorAll(".run-now").forEach((button) => {
    button.addEventListener("click", async () => {
      const id = button.dataset.change;
      const label = button.textContent;
      button.disabled = true;
      button.textContent = "Running evaluation from staled metrics…";
      try {
        await api(`/v1/changes/${id}/run`, { method: "POST" });
      } catch {
        button.disabled = false;
        button.textContent = label;
        const notice = document.getElementById("run-error");
        if (notice) notice.textContent = "The revalidation request failed. Try again.";
        return;
      }
      const target = `#/changes/${id}`;
      if (location.hash === target) await renderChange(id);
      else location.hash = target;
    });
  });
}

const JOURNEY = [
  "Change detected in the AI Registry",
  "Agent: find RMTE risks and map metrics on those risks",
  "Pipeline: mark the mapped metrics stale",
  "Pipeline: run evaluation from the stale metrics",
  "Pipeline: compare new scores with previous scores",
  "Human approval and assurance posture update",
];

function completedStages(change) {
  if (change.status === "approved" || change.status === "rejected") return 6;
  if (change.status === "awaiting_approval") return 5;
  if (change.status === "running") return 4;
  if (change.status === "impact_ready") return 3;
  return change.affected_risks?.length ? 2 : 1;
}

function journey(change) {
  const done = completedStages(change);
  const halted = ["failed", "killed", "debounced"].includes(change.status);
  const steps = JOURNEY.map((label, index) => {
    const step = index + 1;
    let state = "Pending";
    if (step <= done) state = "Complete";
    else if (step === done + 1) state = halted ? "Stopped" : "Next";
    return `
      <li class="journey-step ${state.toLowerCase()}">
        <span class="journey-index">${String(step).padStart(2, "0")}</span>
        <span class="journey-label">${label}</span>
        <span class="journey-state">${state}</span>
      </li>`;
  }).join("");
  return `
    <section class="section">
      <p class="kicker">Revalidation flow</p>
      <ol class="journey">${steps}</ol>
    </section>`;
}

function changeFacts(change) {
  return `
    <section class="section">
      <div class="detail-grid">
        <div class="detail-cell">
          <h3>Trigger</h3>
          <p>${identifier(change.classification.trigger_code)}</p>
          <p class="caption">${words(change.event.name)}</p>
        </div>
        <div class="detail-cell">
          <h3>Severity and scope</h3>
          <p>${words(change.classification.severity)} · ${words(change.classification.scope)}</p>
        </div>
        <div class="detail-cell">
          <h3>Registry version</h3>
          <p>${change.event.resource_version}</p>
          <p class="caption">Detected ${formatTime(change.created_at)}</p>
        </div>
      </div>
    </section>`;
}

function planSection(change) {
  if (!change.selected_test_ids?.length) return "";
  const pinned = change.pinned_context;
  return `
    <section class="section">
      <h2>Selected revalidation plan</h2>
      <div class="detail-grid">
        <div class="detail-cell">
          <h3>Approved test suites</h3>
          <div class="metric-list">
            ${change.selected_test_ids.map((test) => `<span class="metric">${test}</span>`).join("")}
          </div>
        </div>
        <div class="detail-cell">
          <h3>Estimate</h3>
          <p>${change.estimated_cost.toFixed(2)} cost units</p>
          <p class="caption">${Math.round(change.estimated_duration_seconds)} seconds</p>
        </div>
        <div class="detail-cell">
          <h3>Pinned versions</h3>
          <p>${pinned ? pinned.pin_reference : "Pinned when the run starts"}</p>
          <p class="caption">${pinned ? `Dataset ${pinned.dataset_id} · ${pinned.dataset_hash}` : "Datasets and test versions are frozen for reproducibility"}</p>
        </div>
      </div>
    </section>`;
}

function outcomeSection(change) {
  if (!change.posture_after && !change.trust_factor) return "";
  const notes = ["killed", "failed"].includes(change.status) ? [] : change.recommendations || [];
  return `
    <section class="section">
      <h2>Proposed outcome</h2>
      <div class="detail-grid">
        <div class="detail-cell">
          <h3>Status before</h3>
          <p>${change.posture_before}</p>
        </div>
        <div class="detail-cell">
          <h3>Proposed status</h3>
          <p>${change.posture_after || "Not computed"}</p>
        </div>
        <div class="detail-cell">
          <h3>Trust factor (TFa)</h3>
          <p>${change.trust_factor ? change.trust_factor.score : "Not scored"}</p>
          <p class="caption">${change.trust_factor ? identifier(change.trust_factor.reference) : "Computed via GaaS Algorithm 1"}</p>
        </div>
      </div>
      ${notes.length ? `<ul class="notes">${notes.map((note) => `<li>${note}</li>`).join("")}</ul>` : ""}
    </section>`;
}

function route() {
  clearInterval(dashboardTimer);
  const path = location.hash.replace("#", "") || "/dashboard";
  document.querySelectorAll("nav a").forEach((link) => {
    const target = link.getAttribute("href").replace("#", "");
    link.classList.toggle("active", path.startsWith(target));
  });
  const onDashboard = path === "/dashboard" || path === "/";
  topbar.hidden = !onDashboard;
  if (!onDashboard) toggleBell(false);
  if (path.startsWith("/packs/")) return renderPackDetail(path.split("/")[2]);
  if (path.startsWith("/changes/")) return renderChange(path.split("/")[2]);
  if (path.startsWith("/registry")) return renderRegistry();
  if (path.startsWith("/changes")) return renderChanges();
  if (path.startsWith("/packs")) return renderPacks();
  if (path.startsWith("/notifications")) return renderNotifications();
  renderDashboard();
  dashboardTimer = setInterval(() => {
    if ((location.hash || "#/dashboard") === "#/dashboard") renderDashboard(false);
  }, 5000);
}

async function renderDashboard(showLoading = true) {
  if (showLoading) app.innerHTML = pageHead("01", "Dashboard");
  const [dashboard, changes] = await Promise.all([
    api(`/v1/dashboard?product_id=${currentProductId}`), 
    api(`/v1/changes?product_id=${currentProductId}`)
  ]);
  const recentRuns = changes.filter((change) =>
    ["running", "awaiting_approval", "approved", "rejected", "failed"].includes(change.status)
  );
  app.innerHTML = `
    ${pageHead("01", "Dashboard")}
    <section class="stats">
      ${stat("Completed revalidations", dashboard.completed_revalidations)}
      ${stat("Registered changes", changes.length)}
      ${stat("Awaiting human approval", dashboard.awaiting_approval)}
      ${stat("Evidence packs", dashboard.evidence_packs)}
    </section>
    <section class="section">
      <h2>Recent revalidations</h2>
      <div class="stack">
        ${recentRuns.slice(0, 8).map((change) => changeItem(change, false)).join("")
          || `<p class="empty">No revalidation has been run yet.</p>`}
      </div>
      <p class="caption">Updates automatically every five seconds.</p>
    </section>`;
}

async function renderRegistry() {
  const [registry, dashboard] = await Promise.all([
    api(`/v1/ai-registry?product_id=${currentProductId}`),
    api(`/v1/dashboard?product_id=${currentProductId}`),
  ]);
  app.innerHTML = `
    ${pageHead(
      "02",
      "AI Registry",
      "The registry is the source of truth for product and asset versions. Agent 7 consumes its change records; operators do not type versions or outcomes."
    )}
    <section class="section">
      <p class="kicker">REGISTERED PRODUCT</p>
      <div class="detail-grid">
        <div class="detail-cell"><h3>Product</h3><p>${registry.product.name}</p><p class="caption">${registry.product.id}</p></div>
        <div class="detail-cell"><h3>Architecture</h3><p>${registry.product.archetype}</p></div>
        <div class="detail-cell"><h3>Criticality</h3><p>${words(registry.product.criticality)}</p></div>
        <div class="detail-cell">
          <h3>Revalidation status</h3>
          <p>${dashboard.posture}</p>
          <p class="caption">${dashboard.stale_metrics.length} stale metrics</p>
        </div>
        <div class="detail-cell">
          <h3>Last approved pack</h3>
          <p>${identifier(dashboard.last_pack_id || "None")}</p>
        </div>
        <div class="detail-cell">
          <h3>Active version</h3>
          <p>${registry.product.activeVersion || "Not recorded"}</p>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Registered assets</h2>
      <div class="registry-assets">
        ${registry.assets.map((asset) => `
          <article class="asset">
            ${tag(asset.status)}
            <strong>${asset.type}</strong>
            <span class="caption">${asset.version}</span>
          </article>`).join("")}
      </div>
    </section>
    <section class="section">
      <h2>Registry changes</h2>
      <div class="stack">
        ${registry.changes.map((change) => `
          <article class="item">
            <div>${tag(change.status)}</div>
            <div>
              <p class="item-title">${words(change.type)}</p>
              <p>${change.detail}</p>
              <p class="caption">${change.version} · ${formatTime(change.detectedAt)}</p>
            </div>
            <a class="link-button" href="#/changes/${change.changeId}">Review</a>
          </article>`).join("")}
      </div>
    </section>`;
}

async function renderChanges() {
  const changes = await api(`/v1/changes?product_id=${currentProductId}`);
  app.innerHTML = `
    ${pageHead(
      "03",
      "Changes",
      "Agent 7 maps each registry change to RMTE risks and then to metrics. Staling those metrics, running evaluation, and comparing scores to the previous pack are pipeline steps, not agent work."
    )}
    <div class="stack">${changes.map((change) => changeItem(change)).join("")
      || `<p class="empty">No registry changes.</p>`}</div>`;
  bindRunButtons();
}

async function renderChange(id) {
  const [change, kill] = await Promise.all([
    api(`/v1/changes/${id}`),
    api("/v1/kill-switch"),
  ]);
  const hasImpact = change.affected_risks?.length > 0;
  const stalled = ["killed", "failed"].includes(change.status);
  const runnable =
    ["detected", "impact_ready"].includes(change.status) || (stalled && !kill.enabled);
  app.innerHTML = `
    ${pageHead(
      "03",
      words(change.event.name),
      `${tag(change.status)} &nbsp; ${change.detail}`,
      "#/changes"
    )}
    ${changeFacts(change)}
    ${hasImpact ? `
      <section class="section">
        <h2>Agent output: risks and mapped metrics</h2>
        <div class="detail-grid">
          ${change.affected_risks.map((risk) => `
            <article class="detail-cell">
              <p class="kicker">${risk.risk_id} / ${risk.category}</p>
              <h2>${risk.name}</h2>
              ${staleFor({ affected_metric_ids: risk.metric_ids })}
            </article>`).join("")}
          <article class="detail-cell">
            <p class="kicker">Agent explanation</p>
            <p>${change.narrative?.summary || "No explanation available."}</p>
            <p class="caption">Source: ${words(change.narrative?.source)}${change.narrative?.source === "groq" ? " (LLM)" : ""}</p>
          </article>
        </div>
      </section>` : ""}
    <section class="section">
      <h2>Previous approved evidence</h2>
      <p>${change.previous_pack
        ? `<a class="link-button" href="#/packs/${change.previous_pack.pack_id}">Open ${identifier(change.previous_pack.pack_id)}</a>`
        : `<span class="muted">No earlier pack recorded.</span>`}</p>
    </section>
    ${stalled ? `
      <section class="section">
        <h2>Flow stopped</h2>
        <p>${change.status === "killed"
          ? "Governance disabled the autonomous loop, so Agent 7 stopped before running. Turn the kill switch off in the left rail to allow a retry."
          : "The run stopped before a decision could be reached."}</p>
        ${change.recommendations?.length
          ? `<ul class="notes">${change.recommendations.map((note) => `<li>${note}</li>`).join("")}</ul>`
          : ""}
      </section>` : ""}
    ${runnable ? `
      <section class="section">
        <h2>${stalled ? "Retry the revalidation" : "Run revalidation"}</h2>
        <p>The pipeline marks the mapped metrics stale, runs evaluation from that stale set,
        and compares the new scores with the previous evidence pack. Agent work has already
        finished at risk and metric mapping.</p>
        <button class="primary run-now" data-change="${change.change_id}">${stalled ? "Retry revalidation" : "Run revalidation"}</button>
        <p class="caption" id="run-error"></p>
      </section>` : ""}
    ${planSection(change)}
    ${change.comparisons?.length ? comparisonTable(change) : ""}
    ${outcomeSection(change)}
    ${approvalPanel(change)}
    ${journey(change)}`;
  bindRunButtons();
  bindDecisionButtons(change);
}

function comparisonTable(change) {
  return `
    <section class="section">
      <h2>Previous versus new evaluation</h2>
      <table>
        <thead><tr><th>Metric</th><th>Previous</th><th>New</th><th>Threshold</th><th>Result</th></tr></thead>
        <tbody>${change.comparisons.map((row) => `
          <tr>
            <td>${row.metric_id} / ${row.metric_name}</td>
            <td>${row.previous ?? "—"}</td>
            <td>${row.actual}</td>
            <td>${thresholdText(row.comparison, row.threshold)}</td>
            <td class="${row.passed ? "pass" : "fail"}">${row.passed ? "PASS" : "FAIL"}</td>
          </tr>`).join("")}</tbody>
      </table>
      <p class="caption">Evidence pack:
        <a href="#/packs/${change.evidence_pack?.pack_id}">${identifier(change.evidence_pack?.pack_id || "Not assembled")}</a>
      </p>
    </section>`;
}

function approvalPanel(change) {
  if (change.status !== "awaiting_approval") {
    return change.approval
      ? `<section class="section"><h2>Human decision</h2><p>${words(change.approval.decision)} by ${change.approval.reviewer}</p></section>`
      : "";
  }
  return `
    <section class="section">
      <h2>Human decision required</h2>
      <p>Review score changes and the full evidence pack before deciding.</p>
      <textarea id="review-notes" placeholder="Reviewer rationale"></textarea>
      <div class="action-row">
        <button id="approve-btn" class="primary">Approve evidence</button>
        <button id="reject-btn" class="danger">Reject evidence</button>
      </div>
    </section>`;
}

function bindDecisionButtons(change) {
  const body = () => JSON.stringify({
    reviewer: "Product Owner",
    notes: document.getElementById("review-notes")?.value || "",
  });
  document.getElementById("approve-btn")?.addEventListener("click", async () => {
    await api(`/v1/changes/${change.change_id}/approve`, { method: "POST", body: body() });
    await renderChange(change.change_id);
  });
  document.getElementById("reject-btn")?.addEventListener("click", async () => {
    await api(`/v1/changes/${change.change_id}/reject`, { method: "POST", body: body() });
    await renderChange(change.change_id);
  });
}

async function renderPacks() {
  const packs = await api("/v1/packs");
  app.innerHTML = `
    ${pageHead("04", "Evidence Packs", "Index join only: artefact pointers and gaps. Immutable files stay in MinIO Object Lock.")}
    <div class="stack">
      ${packs.map((pack) => `
        <article class="item">
          <div>${tag(pack.status, pack.status === "approved")}</div>
          <div>
            <p class="item-title">${identifier(pack.pack_id)}</p>
            <p class="muted">${pack.integrity || "HASH_ONLY"} · ${pack.q6_label || "Pending Q6"}</p>
            <p class="caption">${pack.sha256 || "No join hash"}</p>
          </div>
          <a class="link-button" href="#/packs/${pack.pack_id}">Open pack</a>
        </article>`).join("") || `<p class="empty">No evidence packs.</p>`}
    </div>`;
}

async function renderPackDetail(id) {
  const pack = await api(`/v1/packs/${encodeURIComponent(id)}`);
  const artefacts = pack.artefacts || [];
  const gaps = pack.gaps || [];
  const coverage = pack.coverage || {};
  app.innerHTML = `
    ${pageHead("04", identifier(pack.pack_id), `${tag(pack.status, pack.status === "approved")} &nbsp; ${pack.integrity || "HASH_ONLY"}`, "#/packs")}
    <section class="section">
      <div class="detail-grid">
        <article class="detail-cell"><p class="kicker">Tenant</p><p>${pack.tenant_id || "—"}</p></article>
        <article class="detail-cell"><p class="kicker">Product</p><p>${pack.product_id || "—"}</p></article>
        <article class="detail-cell"><p class="kicker">Release</p><p>${pack.release || "—"}</p></article>
        <article class="detail-cell"><p class="kicker">Q6</p><p>${pack.q6_label || "Pending Q6"}</p></article>
        <article class="detail-cell"><p class="kicker">Storage</p><p>Postgres ${pack.storage?.metadata || "index"}</p><p class="caption">MinIO ${pack.storage?.immutable_file || "worm"}</p></article>
        <article class="detail-cell"><p class="kicker">Join sha256</p><p>${pack.sha256 || "—"}</p></article>
      </div>
    </section>
    <section class="section">
      <h2>Coverage</h2>
      <p>${coverage.artefacts ?? artefacts.length} artefacts · ${coverage.gaps ?? gaps.length} gaps · ${coverage.complete ? "Complete" : "Incomplete"}</p>
    </section>
    <section class="section">
      <h2>Artefacts (index only)</h2>
      <table>
        <thead><tr><th>Artefact</th><th>Kind</th><th>RMTE</th><th>WORM</th><th>sha256</th></tr></thead>
        <tbody>${artefacts.map((item) => `
          <tr>
            <td>${identifier(item.artefact_id)}</td>
            <td>${item.kind || "—"}</td>
            <td>${[item.rmte?.risk_id, item.rmte?.metric_id, item.rmte?.test_id].filter(Boolean).join(" / ") || "—"}</td>
            <td>${item.worm_uri || item.worm_key || "—"}</td>
            <td>${item.sha256 || "—"}</td>
          </tr>`).join("") || `<tr><td colspan="5">No artefact pointers.</td></tr>`}</tbody>
      </table>
      <p class="caption">Files are not copied into the pack. Full objects live behind the WORM URI.</p>
    </section>
    <section class="section">
      <h2>Gaps</h2>
      <div class="stack">${gaps.map((gap) => `
        <article class="item">
          <div>${tag(gap.kind || "gap")}</div>
          <div>
            <p class="item-title">${gap.rmte?.metric_id || gap.rmte?.risk_id || "Unmapped"}</p>
            <p class="muted">${gap.reason || ""}</p>
          </div>
        </article>`).join("") || `<p class="empty">No gaps.</p>`}</div>
    </section>`;
}

async function renderNotifications() {
  const notes = await api("/v1/notifications");
  app.innerHTML = `
    ${pageHead("05", "Notifications", "Reference-only alerts and human review tasks. No prompt or response content is stored here.")}
    <div class="stack">${notes.slice().reverse().map(notificationItem).join("")
      || `<p class="empty">No notifications.</p>`}</div>`;
}

const topbar = document.querySelector(".topbar");
const bell = document.getElementById("bell");
const bellPanel = document.getElementById("bell-panel");
const bellList = document.getElementById("bell-list");
const bellCount = document.getElementById("bell-count");

async function refreshBell() {
  let notes = [];
  try {
    notes = await api("/v1/notifications");
  } catch {
    return;
  }
  bellCount.hidden = notes.length === 0;
  bellCount.textContent = notes.length;
  bellList.innerHTML = notes.slice(-6).reverse().map((note) => `
    <div class="bell-row">
      <p>${words(note.kind)}</p>
      <p class="caption">${words(note.channel)} · ${identifier(note.references.change_ref || "")}</p>
    </div>`).join("") || `<p class="caption">No notifications.</p>`;
}

function toggleBell(open) {
  const next = open ?? bellPanel.hidden;
  bellPanel.hidden = !next;
  bell.setAttribute("aria-expanded", String(next));
  if (next) refreshBell();
}

bell.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleBell();
});
document.addEventListener("click", (event) => {
  if (!bellPanel.hidden && !bellPanel.contains(event.target)) toggleBell(false);
});

async function refreshControls() {
  try {
    const kill = await api("/v1/kill-switch");
    document.getElementById("kill-switch").checked = kill.enabled;
  } catch {
    document.getElementById("kill-switch").checked = false;
  }
}

document.getElementById("kill-switch").addEventListener("change", async (event) => {
  await api("/v1/kill-switch", {
    method: "PUT",
    body: JSON.stringify({ enabled: event.target.checked }),
  });
});
document.getElementById("reset-btn").addEventListener("click", async () => {
  if (!confirm("Reset all synthetic Agent 7 data?")) return;
  await api("/demo/reset", { method: "POST" });
  location.hash = "#/dashboard";
  route();
});
window.addEventListener("hashchange", () => {
  toggleBell(false);
  route();
});
setInterval(() => {
  if (!topbar.hidden) refreshBell();
}, 5000);
refreshBell();
refreshControls().then(route);
