"use strict";

const STAGES = [
  {
    tag: "Skill · Planner",
    text: "Defines bounded subquestions and acceptance criteria, then directs searches and rereads toward evidence gaps.",
  },
  {
    tag: "MCP · evidence-research-mcp (stdio)",
    text: "Discovers public sources, reads original text, and registers exact versioned quotations through separate tools.",
  },
  {
    tag: "Skill · Auditor + program checks",
    text: "Checks quote provenance, numeric bindings, semantic support and criterion coverage; uncertain claims remain withheld.",
  },
  {
    tag: "Skills · Reader synthesis + Writer",
    text: "Audits new multi-evidence conclusions again, then organizes approved claims and reports remaining limitations.",
  },
];

const buttons = [...document.querySelectorAll("[data-stage-btn]")];
const track = document.getElementById("flow-track");
const fill = document.getElementById("track-fill");
const tagEl = document.getElementById("stage-tag");
const textEl = document.getElementById("stage-text");
const pauseBtn = document.getElementById("pause");
const motion = matchMedia("(prefers-reduced-motion: reduce)");

let current = 0;
let timer = null;
let running = !motion.matches;

function show(idx) {
  current = idx;
  track.dataset.stage = String(idx);
  fill.style.width = `${(idx / (STAGES.length - 1)) * 100}%`;
  tagEl.textContent = STAGES[idx].tag;
  textEl.textContent = STAGES[idx].text;
  buttons.forEach((btn, i) => btn.setAttribute("aria-pressed", String(i === idx)));
}

function refresh() {
  clearInterval(timer);
  pauseBtn.hidden = motion.matches;
  pauseBtn.textContent = running ? "Pause" : "Play";
  pauseBtn.setAttribute("aria-pressed", String(!running));
  if (running && !motion.matches && !document.hidden) {
    timer = setInterval(() => show((current + 1) % STAGES.length), 3600);
  }
}

buttons.forEach((btn, i) => {
  btn.addEventListener("click", () => {
    show(i);
    running = false;
    refresh();
  });
});

pauseBtn.addEventListener("click", () => {
  running = !running;
  refresh();
});
motion.addEventListener("change", () => {
  running = !motion.matches;
  refresh();
});
document.addEventListener("visibilitychange", refresh);

/* Subtle neural constellation particle background */
function initParticles() {
  const canvas = document.getElementById("particle-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  let width = 0;
  let height = 0;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const pointer = { x: -9999, y: -9999 };
  const particles = [];

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function seed() {
    particles.length = 0;
    const count = Math.min(54, Math.max(28, Math.floor((width * height) / 26000)));
    for (let i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.34,
        vy: (Math.random() - 0.5) * 0.34,
        r: 1.3 + Math.random() * 1.5,
        hue: i % 3 === 0 ? "52, 211, 153" : "129, 140, 248",
      });
    }
  }

  function drawFrame() {
    ctx.clearRect(0, 0, width, height);
    const linkDist = 140;
    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      if (!motion.matches) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;
      }

      for (let j = i + 1; j < particles.length; j++) {
        const q = particles[j];
        const dx = p.x - q.x;
        const dy = p.y - q.y;
        const dist = Math.hypot(dx, dy);
        if (dist < linkDist) {
          const alpha = (1 - dist / linkDist) * 0.16;
          ctx.strokeStyle = `rgba(148, 163, 184, ${alpha.toFixed(3)})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.stroke();
        }
      }

      const pd = Math.hypot(p.x - pointer.x, p.y - pointer.y);
      if (pd < 155) {
        const alpha = (1 - pd / 155) * 0.28;
        ctx.strokeStyle = `rgba(129, 140, 248, ${alpha.toFixed(3)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(pointer.x, pointer.y);
        ctx.stroke();
      }

      ctx.fillStyle = `rgba(${p.hue}, 0.55)`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fill();
    }
    if (!motion.matches && !document.hidden) {
      requestAnimationFrame(drawFrame);
    }
  }

  window.addEventListener("resize", () => {
    resize();
    seed();
    if (motion.matches) drawFrame();
  });
  window.addEventListener("mousemove", (e) => {
    pointer.x = e.clientX;
    pointer.y = e.clientY;
  });
  window.addEventListener("mouseleave", () => {
    pointer.x = -9999;
    pointer.y = -9999;
  });

  resize();
  seed();
  drawFrame();
}

async function loadStatus() {
  const pill = document.getElementById("live-pill");
  if (!pill) return;
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
    // Ignore when opened offline
  }
}

show(0);
refresh();
initParticles();
loadStatus();
