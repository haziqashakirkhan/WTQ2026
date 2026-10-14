const $ = (s, r = document) => r.querySelector(s);
const st = { id: null, data: null, history: null, file: null, previewUrl: null, preview: false };

/* ---------- helpers ---------- */
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const PKR = (n) => n == null ? null : "PKR " + Number(n).toLocaleString("en-PK", { minimumFractionDigits: Number.isInteger(n) ? 0 : 2, maximumFractionDigits: 2 });
const NR = '<span class="nr">Not detected</span>';
const money = (n) => PKR(n) ?? NR;
const val = (v) => (v == null || v === "") ? NR : esc(v);
const dateFmt = (d) => d ? new Date(d + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : null;
const monthFull = (ym) => ym ? new Date(ym + "-01T00:00:00Z").toLocaleDateString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }) : null;
const monthShort = (ym) => new Date(ym + "-01T00:00:00Z").toLocaleDateString("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" });
const sizeFmt = (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB";
const LABELS = { energy: "Energy", fixed: "Fixed", fpa: "Fuel price adjustment", quarterly_adjustment: "Quarterly adjustment", surcharge: "Surcharge", meter_rent: "Meter rent", subsidy: "Subsidy", other: "Other", gst: "Sales tax (GST)", electricity_duty: "Electricity duty", income_tax: "Income tax", municipal_tax: "Municipal tax", other_tax: "Other tax" };
const label = (t) => LABELS[t] || t;
const PAL = ["#123D32", "#2E6B58", "#5E9C85", "#9CC7B3", "#C8D8CC", "#B7C95A"];

function toast(msg, kind = "ok") {
  const t = document.createElement("div"); t.className = "toast " + kind; t.textContent = msg;
  $("#toasts").append(t); setTimeout(() => t.remove(), 3600);
}
const show = (sel, on) => { $(sel).hidden = !on; };

/* ---------- nav: status + active section ---------- */
fetch("/api/health").then((r) => r.ok ? ok(true) : ok(false)).catch(() => ok(false));
function ok(live) { const s = $("#status"); s.className = "status " + (live ? "live" : "off"); $("#statusText").textContent = live ? "Backend live" : "Backend offline"; }
const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && document.querySelectorAll(".links a").forEach((a) => a.classList.toggle("on", a.dataset.link === e.target.id))), { rootMargin: "-45% 0px -50% 0px" });
["overview", "analyze", "intelligence", "ask"].forEach((id) => io.observe($("#" + id)));

/* ---------- upload ---------- */
const drop = $("#drop"), input = $("#file");
function pick(file) {
  if (!file) return;
  if (!/\.(png|jpe?g)$/i.test(file.name)) return toast("Please choose a PNG or JPG bill image.", "err");
  if (file.size > 10 * 1024 * 1024) return toast("That image is larger than 10 MB.", "err");
  st.file = file; if (st.previewUrl) URL.revokeObjectURL(st.previewUrl); st.previewUrl = URL.createObjectURL(file);
  $("#thumb").src = st.previewUrl; $("#pName").textContent = file.name; $("#pSize").textContent = sizeFmt(file.size);
  show("#picked", true); $("#decodeBtn").disabled = false; showErr(null);
}
function clearPick() { st.file = null; input.value = ""; show("#picked", false); $("#decodeBtn").disabled = true; }
input.addEventListener("change", () => pick(input.files[0]));
drop.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click()));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));
$("#pRemove").addEventListener("click", clearPick);
$("#decodeBtn").addEventListener("click", decode);
$("#retryBtn").addEventListener("click", decode);

function showErr(msg) { show("#err", !!msg); if (msg) $("#errMsg").textContent = msg; }
function busy(on, title, pct) {
  show("#busy", on); show("#idle", !on && $("#err").hidden);
  if (title) $("#busyTitle").textContent = title;
  const bar = $("#busyBar"); bar.classList.toggle("indet", pct == null); bar.style.width = pct == null ? "" : pct + "%";
}

function postFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest(); x.open("POST", "/api/decode");
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    x.onload = () => { let b = {}; try { b = JSON.parse(x.responseText); } catch {} x.status < 300 ? resolve(b) : reject(new Error(b.detail || "Extraction failed.")); };
    x.onerror = () => reject(new Error("Could not reach the server. Is it still running?"));
    const fd = new FormData(); fd.append("file", file); x.send(fd);
  });
}

async function decode() {
  if (!st.file) return;
  showErr(null); $("#decodeBtn").disabled = true; show("#report", false);
  busy(true, "Uploading bill…", 0);
  const t0 = Date.now(); let timer;
  try {
    const body = await postFile(st.file, (p) => {
      busy(true, p < 100 ? "Uploading bill…" : "Reading the bill…", p < 100 ? p : null);
      if (p === 100 && !timer) timer = setInterval(() => $("#busySub").textContent = `Extracting details with the model · ${Math.round((Date.now() - t0) / 1000)}s`, 500);
    });
    Object.assign(st, { id: body.id, data: body.level1, preview: body.preview_mode, history: null });
    saveSession(); renderReport(); loadHistory(); toast("Bill decoded.");
    $("#report").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { showErr(e.message); toast(e.message, "err"); }
  finally { clearInterval(timer); $("#busySub").textContent = ""; busy(false); $("#decodeBtn").disabled = !st.file; }
}

/* ---------- report ---------- */
function sum(items) { return (items || []).reduce((a, i) => a + (Number(i.amount) || 0), 0); }
function rows(items, total) {
  const body = (items || []).length ? items.map((i) => `<tr><td>${esc(label(i.type))}</td><td class="${i.amount < 0 ? "neg" : ""}">${money(i.amount)}</td></tr>`).join("") : `<tr><td colspan="2" style="color:var(--slate)">No separate lines printed</td></tr>`;
  return `<table>${body}<tr class="tot"><td>Printed total</td><td>${money(total)}</td></tr></table>`;
}
function highlight(json) {
  return esc(JSON.stringify(json, null, 2)).replace(/(&quot;[^&]*?&quot;)(\s*:)?|\b(-?\d+\.?\d*)\b|\b(null|true|false)\b/g, (m, s, colon, n, z) => s ? (colon ? `<span class="k">${s}</span>${colon}` : `<span class="s">${s}</span>`) : n ? `<span class="n">${n}</span>` : `<span class="z">${z}</span>`);
}
function donut(a, b) {
  const t = a + b, C = 2 * Math.PI * 50, pa = (a / t) * C;
  return `<svg viewBox="0 0 120 120" role="img" aria-label="Charges versus taxes"><circle cx="60" cy="60" r="50" fill="none" stroke="#B7C95A" stroke-width="16"/><circle cx="60" cy="60" r="50" fill="none" stroke="#123D32" stroke-width="16" stroke-dasharray="${pa} ${C}"/></svg>`;
}

function renderReport() {
  const d = st.data;
  const checks = [d.provider, d.tariff, d.bill_month, d.due_date, d.units_consumed, d.current_bill, d.payable_within_due_date, d.payable_after_due_date, d.previous_reading, d.current_reading];
  const missing = checks.filter((v) => v == null).length;
  const cDiff = d.total_charges != null && (d.charges || []).length ? Math.abs(sum(d.charges) - d.total_charges) : null;
  const tDiff = d.total_taxes != null && (d.taxes || []).length ? Math.abs(sum(d.taxes) - d.total_taxes) : null;
  const flags = [];
  if (missing) flags.push(`<span class="pill warn">${missing} key field${missing > 1 ? "s" : ""} not detected</span>`);
  if (cDiff != null) flags.push(cDiff <= 1 ? '<span class="pill ok">Charge lines add up to printed total</span>' : `<span class="pill warn">Charge lines differ from printed total by ${PKR(+cDiff.toFixed(2))} — review</span>`);
  if (tDiff != null) flags.push(tDiff <= 1 ? '<span class="pill ok">Tax lines add up to printed total</span>' : `<span class="pill warn">Tax lines differ from printed total by ${PKR(+tDiff.toFixed(2))} — review</span>`);
  if (st.preview) flags.push('<span class="pill warn">Preview data — decoder not connected</span>');

  const pos = (d.charges || []).filter((c) => c.amount > 0).map((c) => ({ n: label(c.type), v: c.amount }));
  if (d.total_taxes > 0) pos.push({ n: "Taxes", v: d.total_taxes });
  const tot = pos.reduce((a, s) => a + s.v, 0) || 1;
  const stack = pos.map((s, i) => `<span title="${esc(s.n)}" style="width:${(s.v / tot) * 100}%;background:${s.n === "Taxes" ? "#B7C95A" : PAL[i % 4]}"></span>`).join("");
  const legend = pos.map((s, i) => `<span><i style="background:${s.n === "Taxes" ? "#B7C95A" : PAL[i % 4]}"></i>${esc(s.n)} ${((s.v / tot) * 100).toFixed(0)}%</span>`).join("");
  const share = d.total_charges != null && d.total_taxes != null ? d.total_taxes / (d.total_charges + d.total_taxes) * 100 : null;

  const meter = [["Previous reading", val(d.previous_reading)], ["Current reading", val(d.current_reading)], ["Sanctioned load", d.sanctioned_load_kw == null ? NR : d.sanctioned_load_kw + " kW"], ["Reading date", dateFmt(d.reading_date) ?? NR], ["Issue date", dateFmt(d.issue_date) ?? NR], ["Tariff", val(d.tariff)]];
  const pay = [["Arrears", money(d.arrears)], ["Payable within due date", money(d.payable_within_due_date)], ["Payable after due date", money(d.payable_after_due_date)]];

  $("#report").innerHTML = `
  <div class="rep-head">
    <div><p class="eyebrow">${esc(d.provider ?? "Provider not detected")} · ${monthFull(d.bill_month) ?? "Month not detected"}</p><h3>Your bill, decoded.</h3></div>
    <div class="chips-row">${flags.join("")}${st.previewUrl ? '<button class="btn ghost sm" id="viewSrc">View source bill</button>' : ""}</div>
  </div>
  <div class="metrics">
    <div class="metric hi"><div class="k">Current bill</div><div class="v">${money(d.current_bill)}</div><div class="n">Payable by due date: ${money(d.payable_within_due_date)}</div></div>
    <div class="metric"><div class="k">Total taxes</div><div class="v">${money(d.total_taxes)}</div><div class="n">${share == null ? "Share not computable" : share.toFixed(1) + "% of charges + taxes"}</div></div>
    <div class="metric"><div class="k">Units consumed</div><div class="v">${val(d.units_consumed)}</div><div class="n">kWh this month</div></div>
    <div class="metric"><div class="k">Due date</div><div class="v">${dateFmt(d.due_date) ?? NR}</div><div class="n">After: ${money(d.payable_after_due_date)}</div></div>
  </div>
  <div class="card">
    <h4>Where your money goes</h4>
    <div class="split">
      <div>${pos.length ? `<div class="stack">${stack}</div><div class="legend">${legend}</div>` : '<span class="nr">No line items detected</span>'}</div>
      ${share == null ? "" : `<div class="donut">${donut(d.total_charges, d.total_taxes)}<div><div class="c">${share.toFixed(1)}%</div><small>of charges + taxes<br>is tax</small></div></div>`}
    </div>
  </div>
  <div class="cols"><div class="card"><h4>Charges</h4>${rows(d.charges, d.total_charges)}</div><div class="card"><h4>Taxes</h4>${rows(d.taxes, d.total_taxes)}</div></div>
  <details><summary>Meter, dates and payment details</summary><div><dl>${[...meter, ...pay].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></div></details>
  <details><summary>View extracted JSON</summary><div>
    <div class="tools"><button class="btn ghost sm" id="copyJson"><svg><use href="#i-copy"/></svg>Copy</button><button class="btn ghost sm" id="dlJson"><svg><use href="#i-down"/></svg>Download .json</button></div>
    <pre class="code" tabindex="0">${highlight(d)}</pre></div></details>`;
  show("#report", true);
  $("#copyJson").onclick = () => navigator.clipboard.writeText(JSON.stringify(d, null, 2)).then(() => toast("JSON copied."), () => toast("Copy failed.", "err"));
  $("#dlJson").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" })); a.download = `bill_${d.bill_month || "extract"}.json`; a.click(); };
  const vs = $("#viewSrc"); if (vs) vs.onclick = () => { $("#srcImg").src = st.previewUrl; $("#srcDlg").showModal(); };
  setAsk(true);
}
$("#srcClose").onclick = () => $("#srcDlg").close();

/* ---------- consumption intelligence ---------- */
function intelEmpty(msg) {
  $("#intel").innerHTML = `<div class="intel-empty"><h3>No usage history yet</h3><p>${msg}</p></div>`;
}
async function loadHistory() {
  intelEmpty("Reading the usage table on your bill…");
  try {
    const r = await fetch("/api/history/" + st.id); const b = await r.json();
    if (!r.ok) throw new Error(b.detail);
    st.history = b.available ? b.months : null; saveSession();
  } catch (e) { st.history = null; }
  renderIntel();
}
function renderIntel() {
  const h = st.history;
  if (!h || !h.length) return intelEmpty(st.data ? "This bill's usage table isn't available, or the history connection isn't set up yet. Nothing is estimated or invented here." : "Analyze a bill and the months printed on it will appear here as an interactive chart.");
  const max = Math.max(...h.map((m) => m.units)), avg = h.reduce((a, m) => a + m.units, 0) / h.length;
  const peak = h.reduce((a, m) => (m.units > a.units ? m : a), h[0]);
  const cur = st.data?.units_consumed;
  $("#intel").innerHTML = `
  <div class="istats" id="istats"></div>
  <div class="chart-card">
    <div class="ctl"><label for="thr">Highlight months above</label><input id="thr" type="range" min="0" max="${Math.ceil(max / 10) * 10}" step="10" value="${Math.min(200, Math.ceil(max / 10) * 10)}"><b id="thrV"></b><span>units</span></div>
    <div id="chartWrap" style="position:relative"></div>
    <div class="leg"><span><i style="background:#D7F36B"></i>Above threshold</span><span><i style="background:#4D7B6B"></i>At or below</span><span>Actual units printed on the bill</span></div>
  </div>`;
  const draw = () => {
    const thr = +$("#thr").value; $("#thrV").textContent = thr;
    const above = h.filter((m) => m.units > thr).length;
    $("#istats").innerHTML = [
      ["Highest month", monthFull(peak.month), `${peak.units} units`, "lime"],
      ["Average per month", avg.toFixed(1), `across ${h.length} months listed`, ""],
      ["Months above " + thr, above, `of ${h.length}`, ""],
      ["This bill vs average", cur == null ? "—" : (cur >= avg ? "+" : "") + (cur - avg).toFixed(1), cur == null ? "Units not detected" : `${cur} units vs ${avg.toFixed(1)} avg`, ""],
    ].map(([k, v, n, c]) => `<div class="istat ${c}"><div class="k">${k}</div><div class="v">${v}</div><div class="n">${n}</div></div>`).join("");
    const W = 760, H = 260, pl = 34, pb = 26, bw = (W - pl) / h.length, sc = (H - pb - 14) / (Math.ceil(max / 10) * 10);
    const bars = h.map((m, i) => { const bh = m.units * sc, x = pl + i * bw + bw * .16, y = H - pb - bh; return `<g class="bar-hit" data-i="${i}"><rect x="${pl + i * bw}" y="0" width="${bw}" height="${H - pb}" fill="transparent"/><rect class="b" x="${x}" y="${y}" width="${bw * .68}" height="${bh}" rx="4" fill="${m.units > thr ? "#D7F36B" : "#4D7B6B"}"/><text x="${x + bw * .34}" y="${H - 8}" text-anchor="middle">${esc(monthShort(m.month))}</text></g>`; }).join("");
    const ty = H - pb - thr * sc;
    $("#chartWrap").innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly units consumed">${bars}<line x1="${pl}" x2="${W}" y1="${ty}" y2="${ty}" stroke="#D7F36B" stroke-dasharray="4 4" opacity=".7"/><text x="0" y="${ty + 4}">${thr}</text></svg><div class="tip" id="tip" hidden></div>`;
    document.querySelectorAll(".bar-hit").forEach((g) => {
      const m = h[g.dataset.i], tip = $("#tip");
      g.onmousemove = (e) => { const r = $("#chartWrap").getBoundingClientRect(); tip.hidden = false; tip.textContent = `${monthShort(m.month)} · ${m.units} units`; tip.style.left = e.clientX - r.left + "px"; tip.style.top = e.clientY - r.top + "px"; };
      g.onmouseleave = () => (tip.hidden = true);
    });
  };
  $("#thr").addEventListener("input", draw); draw();
}

/* ---------- ask ---------- */
const QS = ["How much of my bill is taxes?", "Which charges contribute most to the total?", "How many months exceeded 200 units?", "What is my payable amount before the due date?", "Explain this bill in simple language."];
$("#chips").innerHTML = QS.map((q) => `<button class="chip">${esc(q)}</button>`).join("");
$("#thread").innerHTML = '<div class="thread-empty">Answers will appear here.</div>';
function setAsk(on) { $("#askHint").textContent = on ? "Ready. Ask anything about the bill you just decoded." : "Analyze a bill first, then ask about it."; }
function fmtAnswer(t) {
  const blocks = esc(t).split(/\n{2,}/).map((b) => {
    const lines = b.split("\n");
    if (lines.every((l) => /^\s*[-•]\s+/.test(l))) return "<ul>" + lines.map((l) => `<li>${l.replace(/^\s*[-•]\s+/, "")}</li>`).join("") + "</ul>";
    return "<p>" + lines.join("<br>") + "</p>";
  }).join("");
  return blocks.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(?<![>\w])(PKR\s?[\d,]+(?:\.\d+)?)/g, "<b>$1</b>");
}
async function ask(q) {
  q = q.trim(); if (!q) return;
  if (!st.id) { toast("Analyze a bill first.", "err"); $("#analyze").scrollIntoView(); return; }
  $("#q").value = ""; $("#askBtn").disabled = true;
  const th = $("#thread"); if ($(".thread-empty", th)) th.innerHTML = "";
  const box = document.createElement("div"); box.className = "qa loading";
  box.innerHTML = `<div class="q">${esc(q)}</div><div class="a">Reading your bill…</div>`; th.prepend(box);
  try {
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: st.id, questions: [q] }) });
    const b = await r.json(); if (!r.ok) throw new Error(b.detail || "Something went wrong.");
    box.className = "qa"; $(".a", box).innerHTML = fmtAnswer(b.answers[0].answer);
  } catch (e) { box.className = "qa error"; $(".a", box).textContent = e.message; toast(e.message, "err"); }
  finally { $("#askBtn").disabled = false; }
}
$("#chips").addEventListener("click", (e) => e.target.classList.contains("chip") && ask(e.target.textContent));
$("#askBtn").addEventListener("click", () => ask($("#q").value));
$("#q").addEventListener("keydown", (e) => e.key === "Enter" && ask($("#q").value));

/* ---------- session persistence (results only, never the image) ---------- */
function saveSession() { try { sessionStorage.setItem("wattwise", JSON.stringify({ id: st.id, data: st.data, history: st.history, preview: st.preview })); } catch {} }
(function restore() {
  try { const s = JSON.parse(sessionStorage.getItem("wattwise") || "null"); if (!s || !s.data) return renderIntel(); Object.assign(st, s); renderReport(); renderIntel(); }
  catch { renderIntel(); }
})();
