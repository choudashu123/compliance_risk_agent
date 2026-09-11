const $ = (s) => document.querySelector(s);
const api = async (url, opts) => {
  let r;
  try {
    r = await fetch(url, opts);
  } catch (e) {
    return { error: `Cannot reach the server (${e.message}). Is it still running?` };
  }
  let body = null;
  try {
    body = await r.json();
  } catch (_) {
    /* non-JSON response */
  }
  if (!r.ok) {
    const detail = (body && (body.detail || body.error)) || r.statusText;
    return { error: `HTTP ${r.status}: ${detail}` };
  }
  return body ?? {};
};

// --- tabs --------------------------------------------------------------
document.querySelectorAll("nav button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll("nav button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "approvals") loadApprovals();
    if (b.dataset.tab === "registers") loadRegisters();
  };
});

// --- upload ----------------------------------------------------------
$("#doUpload").onclick = async () => {
  const fileInput = $("#files");
  if (!fileInput.files || fileInput.files.length === 0) {
    $("#uploadOut").textContent = "⚠ Please choose at least one PDF file before clicking Ingest.";
    return;
  }
  const fd = new FormData();
  for (const f of fileInput.files) fd.append("files", f);
  $("#uploadOut").textContent = "Uploading & Ingesting…";
  const res = await api("/api/upload", { method: "POST", body: fd });
  $("#uploadOut").textContent = res.error
    ? "⚠ " + res.error
    : JSON.stringify(res.ingested, null, 2);
};

// --- chat ----------------------------------------------------------
function bubble(cls, text) {
  const d = document.createElement("div");
  d.className = "bubble " + cls;
  d.textContent = text;
  $("#log").appendChild(d);
  $("#log").scrollTop = $("#log").scrollHeight;
  return d;
}

$("#send").onclick = async () => {
  const msg = $("#msg").value.trim();
  if (!msg) return;
  bubble("user", msg);
  $("#msg").value = "";
  const b = bubble("bot", "…thinking");
  const res = await api("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: msg }),
  });
  if (res.error) {
    b.textContent = "⚠ " + res.error;
    b.classList.add("warn");
    return;
  }
  b.textContent = res.answer || "(no answer returned)";
  const meta = document.createElement("div");
  meta.className = "meta";
  const cites = (res.citations || []).map((c) => `${c.filename}#${c.chunk_id}`).join(", ");
  let html = cites ? `📎 Citations: ${cites}` : "";
  const d = res.drafted || {};
  if (d.finding_id) {
    const ids = `${d.finding_id} + ${d.risk_id}`;
    if (d.finding_status === "official" || d.risk_status === "official")
      html += `<br>✅ Already tracked as ${ids}, approved earlier — see Registers (use “Reset demo” to start fresh)`;
    else if (d.reused)
      html += `<br>📝 Already drafted as ${ids} — pending in Approvals`;
    else
      html += `<br>📝 Drafted ${ids} (see Approvals)`;
  }
  if (res.warnings && res.warnings.length)
    html += `<br><span class="warn">⚠ ${res.warnings.join(" ")}</span>`;
  meta.innerHTML = html;
  b.appendChild(meta);
};

// --- approvals -----------------------------------------------------
async function loadApprovals() {
  const d = await api("/api/approvals");
  const el = $("#approvalsOut");
  el.innerHTML = "";
  if (!d.findings.length && !d.risks.length) { el.textContent = "Nothing pending."; return; }

  d.findings.forEach((f) => {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = `<h3>${f.id}: ${f.title} <span class="badge">${f.status}</span></h3>
      <p>${f.description}</p>
      <button data-fid="${f.id}">Approve finding</button>`;
    c.querySelector("button").onclick = async (e) => {
      await api(`/api/approvals/finding/${e.target.dataset.fid}/approve`, { method: "POST" });
      loadApprovals();
    };
    el.appendChild(c);
  });

  d.risks.forEach((r) => {
    const c = document.createElement("div");
    c.className = "card";
    c.innerHTML = `<h3>${r.id}: ${r.title}
        <span class="badge high">Inherent: ${r.inherent_score}</span>
        <span class="badge">Residual: pending</span></h3>
      <p>${r.description}</p>
      <div class="row">
        <input type="text" placeholder="Mitigation notes (e.g. interim SCCs)" id="mit-${r.id}">
        <select id="score-${r.id}"><option>Low</option><option selected>Medium</option><option>High</option></select>
        <button data-rid="${r.id}">Grade &amp; approve</button>
      </div>`;
    c.querySelector("button").onclick = async (e) => {
      const rid = e.target.dataset.rid;
      await api(`/api/approvals/risk/${rid}/grade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          residual_score: $("#score-" + rid).value,
          mitigation: $("#mit-" + rid).value,
        }),
      });
      loadApprovals();
    };
    el.appendChild(c);
  });
}
$("#refreshApprovals").onclick = loadApprovals;

// --- registers ---------------------------------------------------
async function loadRegisters() {
  const d = await api("/api/registers");
  const t = (rows, cols) =>
    `<table><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>` +
    rows.map((r) => `<tr>${cols.map((c) => `<td>${fmt(r[c])}</td>`).join("")}</tr>`).join("") +
    `</table>`;
  const fmt = (v) => (v == null ? "—" : Array.isArray(v) ? v.map((x) => x.filename).join(", ") : v);
  $("#registersOut").innerHTML =
    `<h3>Documents</h3>${t(d.documents, ["id", "filename", "n_chunks"])}
     <h3>Findings</h3>${t(d.findings, ["id", "title", "status", "citations"])}
     <h3>Risks</h3>${t(d.risks, ["id", "title", "inherent_score", "residual_score", "mitigation", "status"])}`;
}
$("#refreshRegisters").onclick = loadRegisters;

// --- reset ----------------------------------------------------------
$("#reset").onclick = async () => {
  await api("/api/reset", { method: "POST" });
  $("#log").innerHTML = "";
  $("#uploadOut").textContent = "";
  loadApprovals();
};
