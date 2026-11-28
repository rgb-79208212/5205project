"use strict";

const $ = (id) => document.getElementById(id);
const PHASES = ["planning", "fetching", "verifying", "synthesizing"];
const PALETTE = ["#4f46e5", "#10b981", "#f59e0b", "#0ea5e9", "#ec4899", "#8b5cf6"];

let currentEvidence = [];
let timerHandle = null;
let runStartedAt = 0;

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}

function fmtNum(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "Unavailable";
  return new Intl.NumberFormat("en", { maximumSignificantDigits: 5 }).format(Number(val));
}

function fmtTick(val) {
  if (val === null || val === undefined || Number.isNaN(Number(val))) return "";
  return new Intl.NumberFormat("en", {
    notation: Math.abs(val) >= 10000 ? "compact" : "standard",
    maximumSignificantDigits: 3,
  }).format(Number(val));
}

/* Subtle particle shimmer in the dark workspace command hero */
function initHeroParticles() {
  const canvas = $("hero-particles");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const pts = [];
  let w = 0;
  let h = 0;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    w = rect.width;
    h = rect.height;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pts.length = 0;
    const count = Math.min(28, Math.max(14, Math.floor(w / 48)));
    for (let i = 0; i < count; i++) {
      pts.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.25,
        vy: (Math.random() - 0.5) * 0.25,
        r: 1.2 + Math.random() * 1.2,
        hue: i % 3 === 0 ? "52, 211, 153" : "129, 140, 248",
      });
    }
  }

  function frame() {
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      if (!reduced) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;
      }
      for (let j = i + 1; j < pts.length; j++) {
        const q = pts[j];
        const d = Math.hypot(p.x - q.x, p.y - q.y);
        if (d < 120) {
          ctx.strokeStyle = `rgba(148, 163, 184, ${((1 - d / 120) * 0.14).toFixed(3)})`;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.stroke();
        }
      }
      ctx.fillStyle = `rgba(${p.hue}, 0.5)`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    if (!reduced && !document.hidden) requestAnimationFrame(frame);
  }

  window.addEventListener("resize", () => {
    resize();
    if (reduced) frame();
  });
  resize();
  frame();
}

function setPhase(phaseName, skillName, label) {
  const norm = phaseName === "visualizing" ? "synthesizing" : phaseName;
  const idx = PHASES.indexOf(norm);
  [...$("phase-bar").querySelectorAll("li")].forEach((li, i) => {
    li.classList.remove("active", "done");
    if (idx >= 0) {
      if (i < idx) li.classList.add("done");
      else if (i === idx) li.classList.add("active");
    }
  });
  if (label) $("wf-status").textContent = label;
  const pill = $("wf-skill");
  if (skillName) {
    pill.textContent = skillName.includes("mcp") ? `MCP · ${skillName}` : `Skills · ${skillName}`;
    pill.hidden = false;
  }
}

function markDone(activated) {
  const names = ["planner", "reader", "auditor", "writer"];
  [...$("phase-bar").querySelectorAll("li")].forEach((li, index) => {
    li.classList.remove("active");
    li.classList.toggle("done", activated ? activated.includes(names[index]) : true);
  });
}

function finishUnresolvedSteps() {
  document.querySelectorAll(".step-row:not(.ok):not(.fail)").forEach(row => {
    row.classList.add("fail");
    row.querySelector(".step-icon").textContent = "!";
    row.querySelector(".step-meta").textContent = "Not completed";
  });
}

function startTimer() {
  stopTimer();
  runStartedAt = performance.now();
  $("wf-timer").textContent = "0.0s";
  timerHandle = setInterval(() => {
    $("wf-timer").textContent = `${((performance.now() - runStartedAt) / 1000).toFixed(1)}s`;
  }, 150);
}

function stopTimer(finalMs) {
  if (timerHandle) {
    clearInterval(timerHandle);
    timerHandle = null;
  }
  if (typeof finalMs === "number") {
    $("wf-timer").textContent = `${(finalMs / 1000).toFixed(1)}s`;
  }
}

function updateSkillsTrace(data) {
  const box = $("skills-trace");
  if (!data) return;
  const disc = (data.discovered || []).length;
  const act = data.activated || [];
  box.textContent = `Progressive Skills (${disc} discovered → ${act.length} loaded on demand): ${act.join(" · ")}`;
  box.hidden = false;
}

function addStepRow(data) {
  const li = el("li", undefined, "step-row");
  li.id = `step-row-${data.step_index}`;

  const main = el("div", undefined, "step-main");
  const left = el("div", undefined, "step-left");
  left.append(el("span", "⋯", "step-icon"), el("span", data.step || data.tool_label || data.tool, "step-desc"));
  const right = el("div", undefined, "step-right");
  right.append(el("span", data.source_group || "MCP", "step-src"), el("span", "Running…", "step-meta"));
  main.append(left, right);
  li.append(main);

  const argPairs = Object.entries(data.arguments || {})
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`)
    .join(" · ");
  li.append(el("div", `MCP tool: ${data.tool}${argPairs ? `  (${argPairs})` : ""}`, "step-tech"));
  $("step-list").append(li);
}

function finishStepRow(data) {
  const li = $(`step-row-${data.step_index}`);
  if (!li) return;
  li.classList.add(data.ok ? "ok" : "fail");
  li.querySelector(".step-icon").textContent = data.ok ? "✓" : "!";
  const sec = typeof data.duration_ms === "number" ? `${(data.duration_ms / 1000).toFixed(1)}s` : "";
  const right = li.querySelector(".step-right");
  right.replaceChildren(el("span", data.source || "MCP", "step-src"), el("span", sec));
  if (data.ok && data.evidence_id) {
    right.append(el("span", data.evidence_id, "step-ref"));
    li.tabIndex = 0;
    li.setAttribute("role", "button");
    const open = () => openDrawer(data.evidence_id);
    li.addEventListener("click", open);
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
  }
  const tech = li.querySelector(".step-tech");
  if (tech && data.summary) tech.textContent += ` → ${data.summary}`;
}

/* Native fallback renderers for all 5 chart types (line, column, bar, pie, mind_map) */
function buildFallbackVisual(chart) {
  const type = chart.chart_type;
  const data = chart.data;
  const ns = "http://www.w3.org/2000/svg";

  if (type === "mind_map" && data && typeof data === "object") {
    const wrap = el("div", undefined, "mm-fallback");
    wrap.append(el("div", data.name || chart.title, "mm-root"));
    const grid = el("div", undefined, "mm-branches");
    for (const branch of data.children || []) {
      const card = el("div", undefined, "mm-branch");
      card.append(el("strong", branch.name));
      if (Array.isArray(branch.children) && branch.children.length) {
        const ul = el("ul");
        branch.children.forEach((leaf) => ul.append(el("li", leaf.name)));
        card.append(ul);
      }
      grid.append(card);
    }
    wrap.append(grid);
    return wrap;
  }

  const rows = Array.isArray(data) ? data : [];
  const wrap = el("div");
  wrap.style.width = "100%";
  if (rows.length < 2) {
    wrap.append(el("p", "Insufficient data points to render visual."));
    return wrap;
  }

  if (type === "pie") {
    const total = rows.reduce((s, r) => s + Math.max(0, Number(r.value) || 0), 0) || 1;
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 260 190");
    svg.setAttribute("class", "inline-svg-chart");
    svg.style.maxWidth = "260px";
    svg.style.marginInline = "auto";
    let angle = -Math.PI / 2;
    const cx = 130;
    const cy = 95;
    const rOuter = 74;
    const rInner = 40;
    rows.forEach((r, idx) => {
      const frac = Math.max(0, Number(r.value) || 0) / total;
      const sweep = frac * Math.PI * 2;
      const end = angle + sweep;
      const large = sweep > Math.PI ? 1 : 0;
      const x1 = cx + rOuter * Math.cos(angle);
      const y1 = cy + rOuter * Math.sin(angle);
      const x2 = cx + rOuter * Math.cos(end);
      const y2 = cy + rOuter * Math.sin(end);
      const x3 = cx + rInner * Math.cos(end);
      const y3 = cy + rInner * Math.sin(end);
      const x4 = cx + rInner * Math.cos(angle);
      const y4 = cy + rInner * Math.sin(angle);
      const d = `M ${x1} ${y1} A ${rOuter} ${rOuter} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${rInner} ${rInner} 0 ${large} 0 ${x4} ${y4} Z`;
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", PALETTE[idx % PALETTE.length]);
      const t = document.createElementNS(ns, "title");
      t.textContent = `${r.category}: ${fmtNum(r.value)} (${(frac * 100).toFixed(1)}%)`;
      path.append(t);
      svg.append(path);
      angle = end;
    });
    wrap.append(svg);
    const legend = el("div", undefined, "svg-legend");
    rows.forEach((r, idx) => {
      const item = el("span");
      const sw = el("i");
      sw.style.background = PALETTE[idx % PALETTE.length];
      item.append(sw, document.createTextNode(`${r.category} (${fmtNum(r.value)})`));
      legend.append(item);
    });
    wrap.append(legend);
    return wrap;
  }

  const width = 520;
  const height = 220;
  const pad = { top: 16, right: 18, bottom: 32, left: type === "bar" ? 115 : 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("class", "inline-svg-chart");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", chart.title || "Chart");

  const values = rows.map((r) => Number(r.value));
  const rawMax = Math.max(...values, 1);
  const rawMin = Math.min(...values, 0);

  if (type === "bar") {
    const slotH = plotH / rows.length;
    const barH = Math.min(24, slotH * 0.62);
    rows.forEach((r, idx) => {
      const cy = pad.top + slotH * (idx + 0.5);
      const bw = Math.max(4, (Math.max(0, Number(r.value)) / rawMax) * plotW);
      const lbl = document.createElementNS(ns, "text");
      lbl.setAttribute("x", pad.left - 8);
      lbl.setAttribute("y", cy + 4);
      lbl.setAttribute("text-anchor", "end");
      lbl.setAttribute("fill", "#475569");
      lbl.setAttribute("font-size", "10.5");
      lbl.textContent = String(r.category).slice(0, 18);
      svg.append(lbl);

      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", pad.left);
      rect.setAttribute("y", cy - barH / 2);
      rect.setAttribute("width", bw);
      rect.setAttribute("height", barH);
      rect.setAttribute("rx", "4");
      rect.setAttribute("fill", PALETTE[idx % PALETTE.length]);
      svg.append(rect);

      const val = document.createElementNS(ns, "text");
      val.setAttribute("x", pad.left + bw + 6);
      val.setAttribute("y", cy + 4);
      val.setAttribute("fill", "#334155");
      val.setAttribute("font-size", "10");
      val.textContent = fmtTick(r.value);
      svg.append(val);
    });
    wrap.append(svg);
    return wrap;
  }

  const minV = type === "column" ? Math.min(0, rawMin) : rawMin - (rawMax - rawMin || 1) * 0.08;
  const maxV = rawMax + (rawMax - rawMin || 1) * 0.1;
  const spanV = maxV - minV || 1;

  for (let i = 0; i <= 4; i++) {
    const ratio = i / 4;
    const y = pad.top + plotH * (1 - ratio);
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", pad.left);
    line.setAttribute("x2", width - pad.right);
    line.setAttribute("y1", y);
    line.setAttribute("y2", y);
    line.setAttribute("stroke", "#e2e8f0");
    line.setAttribute("stroke-dasharray", i === 0 ? "" : "3 3");
    svg.append(line);

    const lbl = document.createElementNS(ns, "text");
    lbl.setAttribute("x", pad.left - 8);
    lbl.setAttribute("y", y + 4);
    lbl.setAttribute("text-anchor", "end");
    lbl.setAttribute("fill", "#64748b");
    lbl.setAttribute("font-size", "10.5");
    lbl.textContent = fmtTick(minV + spanV * ratio);
    svg.append(lbl);
  }

  if (type === "column") {
    const slotW = plotW / rows.length;
    const barW = Math.min(44, slotW * 0.56);
    rows.forEach((r, idx) => {
      const cx = pad.left + slotW * (idx + 0.5);
      const y = pad.top + plotH * (1 - (Number(r.value) - minV) / spanV);
      const y0 = pad.top + plotH * (1 - (0 - minV) / spanV);
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", cx - barW / 2);
      rect.setAttribute("y", Math.min(y, y0));
      rect.setAttribute("width", barW);
      rect.setAttribute("height", Math.max(2, Math.abs(y0 - y)));
      rect.setAttribute("rx", "5");
      rect.setAttribute("fill", PALETTE[idx % PALETTE.length]);
      svg.append(rect);

      const xLbl = document.createElementNS(ns, "text");
      xLbl.setAttribute("x", cx);
      xLbl.setAttribute("y", height - 10);
      xLbl.setAttribute("text-anchor", "middle");
      xLbl.setAttribute("fill", "#475569");
      xLbl.setAttribute("font-size", "10.5");
      xLbl.textContent = String(r.category).slice(0, 12);
      svg.append(xLbl);
    });
  } else {
    // line chart
    const times = [...new Set(rows.map((r) => String(r.time)))];
    const groups = [...new Set(rows.map((r) => r.group || "Series"))];
    const xOf = (t) => pad.left + (times.indexOf(String(t)) / Math.max(times.length - 1, 1)) * plotW;
    const yOf = (v) => pad.top + plotH * (1 - (Number(v) - minV) / spanV);

    times.forEach((t) => {
      const lbl = document.createElementNS(ns, "text");
      lbl.setAttribute("x", xOf(t));
      lbl.setAttribute("y", height - 10);
      lbl.setAttribute("text-anchor", "middle");
      lbl.setAttribute("fill", "#64748b");
      lbl.setAttribute("font-size", "10.5");
      lbl.textContent = t.slice(0, 10);
      svg.append(lbl);
    });

    groups.forEach((g, idx) => {
      const color = PALETTE[idx % PALETTE.length];
      const pts = rows.filter((r) => (r.group || "Series") === g);
      const d = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${xOf(p.time).toFixed(1)} ${yOf(p.value).toFixed(1)}`).join(" ");
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", "2.4");
      svg.append(path);
      pts.forEach((p) => {
        const c = document.createElementNS(ns, "circle");
        c.setAttribute("cx", xOf(p.time).toFixed(1));
        c.setAttribute("cy", yOf(p.value).toFixed(1));
        c.setAttribute("r", "3.2");
        c.setAttribute("fill", "#fff");
        c.setAttribute("stroke", color);
        c.setAttribute("stroke-width", "2");
        svg.append(c);
      });
    });
  }
  wrap.append(svg);
  return wrap;
}

function buildChartCard(chart) {
  const card = el("article", undefined, "chart-card");
  const head = el("div", undefined, "chart-head");
  head.append(el("h4", chart.title || chart.evidence_id));
  const typeLabel = (chart.chart_type || "chart").replace("_", " ");
  const badge = el(
    "span",
    chart.renderer === "antv_mcp" && chart.image_url ? `AntV · ${typeLabel}` : `SVG · ${typeLabel}`,
    "renderer-tag"
  );
  head.append(badge);
  card.append(head);

  const visual = el("div", undefined, "chart-visual");
  if (chart.image_url) {
    const img = el("img");
    img.src = chart.image_url;
    img.alt = chart.title || "Generated chart";
    img.addEventListener("error", () => {
      badge.textContent = `SVG · ${typeLabel}`;
      visual.replaceChildren(buildFallbackVisual(chart));
    });
    visual.append(img);
  } else {
    visual.append(buildFallbackVisual(chart));
  }
  card.append(visual);

  const foot = el("div", undefined, "chart-foot");
  foot.append(el("span", chart.source_note || `Grounded in ${chart.evidence_id}`));
  if (chart.evidence_id) {
    const refBtn = el("button", chart.evidence_id, "citation-btn");
    refBtn.type = "button";
    refBtn.addEventListener("click", () => openDrawer(chart.evidence_id));
    foot.append(refBtn);
  }
  card.append(foot);
  return card;
}

function citedBlock(tag, item, cls) {
  const label = { synthesis: "Synthesis: ", inference: "Conditional inference: ", derived: "Calculated: " }[item.kind] || "";
  const node = el(tag, `${label}${item.text} `, cls);
  for (const id of item.refs || []) {
    const btn = el("button", id, "citation-btn");
    btn.type = "button";
    btn.setAttribute("aria-label", `Inspect evidence ${id}`);
    btn.addEventListener("click", () => openDrawer(id));
    node.append(btn);
  }
  return node;
}

function renderReport(data) {
  currentEvidence = data.evidence || [];
  const report = $("live-report");
  report.replaceChildren();

  const header = el("header", undefined, "report-header");
  const left = el("div");
  const kickerText = {
    complete: "Evidence-supported answer · Task covered",
    partial: "Partial answer · Open questions remain",
    sources_only: "Evidence collected · No approved conclusions",
    no_evidence: "No substantive evidence obtained",
  }[data.status] || "Research result";
  left.append(el("span", kickerText, "report-kicker"), el("h2", data.title || "Research Report"));
  if (data.stats) {
    left.append(
      el("p", `Elapsed ${(data.stats.elapsed_ms / 1000).toFixed(1)}s · ${data.stats.steps} MCP calls · ${data.stats.usable_sources} cited source records`, "report-meta")
    );
  }

  const actions = el("div", undefined, "report-actions");
  const evBtn = el("button", `Evidence (${currentEvidence.length})`, "btn-outline");
  evBtn.type = "button";
  evBtn.addEventListener("click", () => openDrawer(currentEvidence[0]?.id));
  const pdfBtn = el("button", "Export PDF", "btn-solid");
  pdfBtn.type = "button";
  pdfBtn.addEventListener("click", () => window.print());
  actions.append(evBtn, pdfBtn);
  header.append(left, actions);
  report.append(header);

  if (data.completion && data.execution) {
    const status = el("div", undefined, "notice-box research-status");
    const published = data.verification?.grounded_claims || 0;
    const criteria = data.research?.criteria || [];
    const met = criteria.filter(c => c.status === "met").length;
    status.append(
      el("p", `Published claims: ${published ? `${published} passed recorded checks` : "none"}. Task: ${data.completion.status}${criteria.length ? ` · ${met}/${criteria.length} acceptance criteria covered` : ""}.`),
      el("p", `Execution: ${data.execution.status.replaceAll("_", " ")} · ${data.execution.stop_reason.replaceAll("_", " ")}. Checks assess evidence support; they do not guarantee truth.`)
    );
    report.append(status);
  }

  if (data.summary) {
    const box = el("section", undefined, "abstract-box");
    box.append(el("h3", "Abstract"), citedBlock("p", data.summary));
    report.append(box);
  }

  let secNum = 1;
  if (data.background) {
    const bg = el("section", undefined, "survey-section");
    bg.append(el("h3", `${secNum}. Background & Scope`), citedBlock("p", data.background, "survey-prose"));
    report.append(bg);
    secNum++;
  }

  const sections = Array.isArray(data.sections) && data.sections.length
    ? data.sections
    : (data.claims && data.claims.length ? [{ heading: "Key Findings", paragraphs: data.claims, takeaways: [] }] : []);
  if (sections.length) {
    const thematic = el("section", undefined, "survey-section");
    thematic.append(el("h3", `${secNum}. Thematic Analysis`));
    sections.forEach((sec, idx) => {
      const sub = el("div", undefined, "survey-subsection");
      const cleanHeading = sec.heading.replace(/^\d+(?:\.\d+)?\s*/, "");
      sub.append(el("h4", `${secNum}.${idx + 1} ${cleanHeading}`));
      for (const para of sec.paragraphs || []) {
        sub.append(citedBlock("p", para, "survey-prose"));
      }
      if (Array.isArray(sec.takeaways) && sec.takeaways.length) {
        const ul = el("ul", undefined, "takeaway-list");
        sec.takeaways.forEach((t) => ul.append(el("li", t)));
        sub.append(ul);
      }
      thematic.append(sub);
    });
    report.append(thematic);
    secNum++;
  }

  if (data.comparison_table && data.comparison_table.columns?.length) {
    const t = data.comparison_table;
    const wrap = el("section", undefined, "comparison-wrap");
    const cap = el("div", undefined, "table-caption");
    cap.append(el("span", t.caption || "Cross-Source Comparison"));
    const refSpan = el("span");
    for (const id of t.refs || []) {
      const btn = el("button", id, "citation-btn");
      btn.type = "button";
      btn.addEventListener("click", () => openDrawer(id));
      refSpan.append(btn);
    }
    cap.append(refSpan);
    const scroll = el("div", undefined, "table-scroll");
    const tbl = el("table");
    const hr = tbl.createTHead().insertRow();
    t.columns.forEach((c) => hr.append(el("th", c)));
    const tb = tbl.createTBody();
    t.rows.forEach((r) => {
      const tr = tb.insertRow();
      r.forEach((cell) => { tr.insertCell().textContent = cell; });
    });
    scroll.append(tbl);
    wrap.append(cap, scroll);
    report.append(wrap);
  }

  const charts = Array.isArray(data.charts) ? data.charts : [];
  if (charts.length) {
    const vis = el("section", undefined, "survey-section");
    vis.append(el("h3", `${secNum}. Visual Analysis`));
    const grid = el("div", undefined, "charts-grid");
    charts.forEach((c) => grid.append(buildChartCard(c)));
    vis.append(grid);
    report.append(vis);
    secNum++;
  }

  if (data.claim_registry || data.research?.criteria?.length) {
    const details = el("details", undefined, "research-review");
    const withheld = (data.claim_registry || []).filter(c => !c.checks?.publishable);
    details.append(el("summary", `Coverage and withheld claims (${withheld.length})`));
    const list = el("ul");
    for (const criterion of data.research?.criteria || []) {
      list.append(el("li", `${criterion.id} · ${criterion.status}: ${criterion.description} — ${criterion.reason}`));
    }
    for (const claim of withheld) {
      const item = el("li");
      item.append(el("strong", `${claim.id} · Withheld (${claim.status}); not a finding`), citedBlock("p", claim), el("p", claim.reason));
      list.append(item);
    }
    for (const comparison of data.comparisons || []) {
      list.append(el("li", `${comparison.claim_ids.join(" / ")} · ${comparison.relationship.replaceAll("_", " ")}: ${comparison.reason}`));
    }
    for (const warning of data.execution?.warnings || []) {
      list.append(el("li", `${warning.stage}: ${warning.reason || warning.type || "Service warning"}`));
    }
    details.append(list);
    report.append(details);
  }

  if (data.methodology || data.limitations) {
    const two = el("section", undefined, "two-col");
    if (data.methodology) {
      const m = el("div", undefined, "info-box");
      m.append(el("h3", "Methodology & Verification"), el("p", data.methodology));
      two.append(m);
    }
    if (data.limitations) {
      const l = el("aside", undefined, "info-box limits");
      l.append(el("h3", "Limitations & Open Questions"), el("p", data.limitations));
      two.append(l);
    }
    report.append(two);
  }

  const usableEvidence = currentEvidence.filter((e) => e.data && !e.data.error);
  if (usableEvidence.length) {
    const refSec = el("section", undefined, "survey-section");
    refSec.append(el("h3", "References"));
    const list = el("ul", undefined, "references-list");
    for (const item of usableEvidence) {
      const d = item.data || {};
      const li = el("li");
      const btn = el("button", item.id, "citation-btn");
      btn.type = "button";
      btn.addEventListener("click", () => openDrawer(item.id));
      li.append(btn);
      const label = d.title || d.dataset || d.source || item.tool;
      const primaryUrl = (typeof d.url === "string" && d.url.startsWith("https://"))
        ? d.url
        : (d.results?.[0]?.url && d.results[0].url.startsWith("https://") ? d.results[0].url : "");
      if (primaryUrl) {
        const a = el("a", `${label} ↗`);
        a.href = primaryUrl;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        li.append(a);
      } else {
        li.append(el("span", label));
      }
      if (d.source && d.source !== label) li.append(el("span", `— ${d.source}`));
      list.append(li);
    }
    refSec.append(list);
    report.append(refSec);
  }

  report.hidden = false;
}

/* Evidence Drawer */
function renderDrawerItem(item) {
  const host = $("drawer-content");
  host.replaceChildren();
  if (!item) {
    host.append(el("p", "No evidence records collected."));
    return;
  }
  const payload = item.data || {};
  const banner = el("div", undefined, "ev-banner");
  banner.append(
    el("h3", `${item.id} · ${payload.title || payload.dataset || payload.source || item.tool}`),
    el("p", `MCP Tool: ${item.tool}${payload.source ? ` · Source: ${payload.source}` : ""}`)
  );
  host.append(banner);

  if (payload.error) host.append(el("div", payload.error, "notice-box"));

  if (payload.quote) {
    const record = el("section", undefined, "evidence-assessment");
    record.append(el("p", `${payload.locator} · ${payload.access}`), el("blockquote", payload.quote));
    for (const claim of payload.related_claims || []) {
      record.append(el("p", `${claim.id} · ${claim.publishable ? "Published" : "Withheld"} (${claim.status}): ${claim.reason}`));
    }
    record.append(el("small", `Saved version SHA-256: ${payload.document_sha256}. This identifies the read version; it does not certify the source's accuracy.`));
    host.append(record);
  }

  const links = [];
  if (typeof payload.url === "string" && payload.url.startsWith("https://")) {
    links.push({ url: payload.url, title: payload.title || payload.dataset || "Open source", meta: payload.source || "" });
  }
  for (const r of payload.results || []) {
    if (typeof r.url === "string" && r.url.startsWith("https://")) {
      links.push({
        url: r.url,
        title: r.title || r.id || "Open link",
        meta: [r.published?.[0], r.venue, r.downloads ? `${fmtNum(r.downloads)} downloads` : ""].filter(Boolean).join(" · "),
        body: r.abstract || r.summary || "",
      });
    }
  }
  if (links.length) {
    const list = el("div", undefined, "ev-links");
    for (const l of links) {
      const a = el("a", undefined, "ev-link-card");
      a.href = l.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.append(el("strong", `${l.title} ↗`));
      if (l.meta) a.append(el("small", l.meta));
      if (l.body) a.append(el("p", l.body));
      list.append(a);
    }
    host.append(list);
  }

  const raw = el("details", undefined, "raw-box");
  raw.append(el("summary", "Raw MCP JSON payload"), el("pre", JSON.stringify(payload, null, 2)));
  host.append(raw);
}

function openDrawer(selectedId) {
  if (!currentEvidence.length) return;
  const target = currentEvidence.find((e) => e.id === selectedId) || currentEvidence[0];
  const tabs = $("drawer-tabs");
  tabs.replaceChildren();
  for (const item of currentEvidence) {
    const btn = el("button", item.id, "drawer-tab");
    btn.type = "button";
    btn.role = "tab";
    btn.setAttribute("aria-selected", String(item.id === target.id));
    btn.addEventListener("click", () => {
      tabs.querySelectorAll(".drawer-tab").forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
      renderDrawerItem(item);
    });
    tabs.append(btn);
  }
  renderDrawerItem(target);
  $("drawer-backdrop").hidden = false;
  $("evidence-drawer").hidden = false;
  $("drawer-close").focus();
}

function closeDrawer() {
  $("drawer-backdrop").hidden = true;
  $("evidence-drawer").hidden = true;
}

$("drawer-close").addEventListener("click", closeDrawer);
$("drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("evidence-drawer").hidden) closeDrawer();
});

$("tech-toggle").addEventListener("click", () => {
  const panel = $("workflow-panel");
  const show = panel.classList.toggle("show-tech");
  $("tech-toggle").setAttribute("aria-pressed", String(show));
  $("tech-toggle").textContent = show ? "Hide MCP details" : "Show MCP details";
});

async function submitQuestion(questionText) {
  const question = questionText.trim();
  if (!question) {
    $("notice").replaceChildren(el("div", "Enter a research question to begin.", "notice-box"));
    $("question").focus();
    return;
  }

  const btn = $("submit-btn");
  btn.disabled = true;
  btn.classList.add("loading");
  btn.querySelector(".btn-label").textContent = "Working…";

  currentEvidence = [];
  $("notice").replaceChildren();
  $("starter-deck").hidden = true;
  $("step-list").replaceChildren();
  $("skills-trace").hidden = true;
  $("verify-note").hidden = true;
  $("live-report").hidden = true;
  $("workflow-panel").hidden = false;
  $("wf-dot").className = "live-dot";
  $("wf-skill").hidden = true;
  setPhase("planning", "planner", "Defining acceptance criteria and choosing public sources…");
  startTimer();

  let finished = false;

  function handle(msg) {
    switch (msg.event) {
      case "error":
        throw new Error(msg.message || "Research failed.");
      case "skills_status":
        updateSkillsTrace(msg);
        break;
      case "pipeline_phase":
        setPhase(msg.phase, msg.skill, msg.label);
        break;
      case "tool_start":
        addStepRow(msg);
        break;
      case "tool_end":
        finishStepRow(msg);
        break;
      case "verify_status": {
        const note = $("verify-note");
        note.hidden = false;
        note.className = msg.ok ? "verify-note" : "verify-note warn";
        note.textContent = (msg.ok ? "✓ " : "⚠ ") + (msg.message || "");
        break;
      }
      case "live_result":
        finishUnresolvedSteps();
        markDone(msg.skills?.activated);
        stopTimer(msg.stats?.elapsed_ms);
        if (msg.skills) updateSkillsTrace(msg.skills);
        $("wf-dot").className = msg.execution?.status === "failed" ? "live-dot error" : "live-dot done";
        $("wf-skill").hidden = true;
        $("wf-status").textContent =
          msg.status === "no_evidence"
            ? "Finished — no usable public records matched"
            : `Research ${msg.status.replaceAll("_", " ")} · ${msg.verification?.grounded_claims ?? 0} published claims`;
        renderReport(msg);
        finished = true;
        break;
    }
  }

  try {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!response.ok) {
      const errBody = await response.json().catch(() => null);
      throw new Error(errBody?.message || `Research request failed (HTTP ${response.status}).`);
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += value;
        const lines = buffer.split("\n");
        buffer = lines.pop();
        for (const line of lines) {
          if (line.trim()) handle(JSON.parse(line));
        }
      }
      if (buffer.trim()) handle(JSON.parse(buffer));
    } finally {
      await reader.cancel();
    }
    if (!finished) throw new Error("The research stream ended before the report was ready.");
  } catch (err) {
    finishUnresolvedSteps();
    stopTimer();
    $("wf-dot").className = "live-dot error";
    $("wf-status").textContent = "Research stopped due to an error";
    $("notice").replaceChildren(el("div", err.message || "Unable to complete research.", "notice-box"));
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
    btn.querySelector(".btn-label").textContent = "Research";
  }
}

$("question-form").addEventListener("submit", (e) => {
  e.preventDefault();
  submitQuestion($("question").value);
});

document.querySelectorAll(".starter-card").forEach((card) => {
  card.addEventListener("click", () => {
    const q = card.dataset.q || "";
    $("question").value = q;
    submitQuestion(q);
  });
});

async function init() {
  initHeroParticles();
  const q = new URLSearchParams(location.search).get("q");
  if (q) $("question").value = q;
  const pill = $("engine-status");
  try {
    const res = await fetch("/api/config");
    if (!res.ok) return;
    const cfg = await res.json();
    const span = pill.querySelector("span");
    if (cfg.llm) {
      pill.classList.add("ready");
      span.textContent = cfg.model || "Live MCP Ready";
    } else {
      span.textContent = "Configure .env";
    }
  } catch {
    // Ignore offline preview
  }
}

init();
