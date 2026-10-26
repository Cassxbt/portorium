const PAGES = [
  ["/", "Home"], ["/how", "How it works"], ["/console", "Console"], ["/evidence", "Evidence"],
  ["/traces", "Traces"], ["/stack", "Under the hood"], ["/judges", "For judges"], ["/limits", "Limits"],
];
const REPO = "https://github.com/Cassxbt/portorium";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (c) => (c && c.length === 10 ? `${c.slice(0, 4)}.${c.slice(4, 6)}.${c.slice(6)}` : c || "–");
const pct = (x, d = 1) => (x == null ? "–" : `${(100 * x).toFixed(d)}%`);

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

const MENUS = [
  ["Product", [
    ["/how", "How it works", "Eight roles. A model proposes, Zetaris and a second model check, code decides."],
    ["/stack", "Under the hood", "Which model or engine runs each step, and which steps are not a model."],
    ["/limits", "Limits", "Where it stops, how often, and what it cannot see."],
  ], { href: "/how#critic", label: "The Critic", body: "In HTS 2026 Rev 21, <span class=\"code\">3004.90.92.35</span> means tramadol. A 2023 ruling used it for olanzapine. A living code can still mean something else." }],
  ["Proof", [
    ["/evidence", "Evidence", "The gate against seven baselines on 2026 rulings, with 95% intervals."],
    ["/traces", "Traces", "Replay any verdict line by line: roles, models, Zetaris queries."],
    ["/judges", "For judges", "Check every claim from a fresh clone in about five minutes."],
  ], { href: "/evidence", label: "Dead codes given", body: "Share of answers naming a code no longer in the schedule.", stat: true }],
];

function chrome() {
  const here = location.pathname.replace(/\/$/, "") || "/";
  const i = Math.max(0, PAGES.findIndex(([p]) => p === here));
  const num = (path) => String(PAGES.findIndex(([p]) => p === path) + 1).padStart(2, "0");
  const chevron = `<svg width="9" height="6" viewBox="0 0 9 6" aria-hidden="true"><path d="M1 1l3.5 3.5L8 1" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`;
  const menus = MENUS.map(([name, items, f], m) => `
    <div class="menu" data-menu="${m}">
      <button aria-expanded="false" aria-controls="panel-${m}"${items.some(([p]) => p === here) ? " data-here" : ""}>${name}${chevron}</button>
      <div class="panel" id="panel-${m}" role="menu">
        <div class="items">${items.map(([p, t, d]) => `<a class="item" role="menuitem" href="${p}"${p === here ? ' aria-current="page"' : ""}><span class="n">${num(p)}</span><b>${t}</b><span>${d}</span></a>`).join("")}</div>
        <a class="feature" href="${f.href}"><span class="label">${f.label}</span>${f.stat ? `<span class="big" data-stat>–</span>` : ""}<p>${f.body}</p></a>
      </div>
    </div>`).join("");
  document.body.insertAdjacentHTML("afterbegin", `
    <header class="top">
      <a class="wordmark" href="/">Portorium<small>Customs precedent gate</small></a>
      <nav class="menus" aria-label="Pages">${menus}<a href="/console"${here === "/console" ? ' aria-current="page"' : ""}>Console</a></nav>
      <a class="btn" href="/console">Classify a product</a>
      <button class="btn ghost index-btn" aria-expanded="false" aria-controls="sheet">Index</button>
    </header>
    <nav class="sheet-nav" id="sheet" aria-label="All pages"><button class="btn ghost close">Close</button>
      ${PAGES.map(([p, t], k) => `<a href="${p}"><span class="n">${String(k + 1).padStart(2, "0")}</span>${t}</a>`).join("")}</nav>
    <span class="corner bl">${String(i + 1).padStart(2, "0")} / ${String(PAGES.length).padStart(2, "0")} · ${PAGES[i][1]}</span>
    <span class="corner br">HTS 2026 Rev 21</span>`);
  const next = PAGES[(i + 1) % PAGES.length];
  document.body.insertAdjacentHTML("beforeend", `
    <footer class="colophon">
      <span>Open Agent Hackathon 2026<br>Track 03 · Reasoning Architecture</span>
      <span>Zetaris · Meterless Nightshift + H-MEM<br>NVIDIA Nemotron</span>
      <span><a href="${REPO}">Source</a> · <a href="/judges">Verify the claims</a></span>
      <span class="next">Next: <a href="${next[0]}">${next[1]} →</a></span>
    </footer>`);
  menuBehaviour();
}

function menuBehaviour() {
  const menus = [...document.querySelectorAll(".menu")];
  const set = (menu, open) => {
    menu.classList.toggle("open", open);
    menu.querySelector("button").setAttribute("aria-expanded", open);
    if (open) { menus.filter((m) => m !== menu).forEach((m) => set(m, false)); loadStat(); }
  };
  const fine = matchMedia("(hover: hover) and (pointer: fine)").matches;
  menus.forEach((menu) => {
    let timer;
    menu.querySelector("button").addEventListener("click", () => set(menu, !menu.classList.contains("open")));
    if (fine) {
      menu.addEventListener("mouseenter", () => { clearTimeout(timer); timer = setTimeout(() => set(menu, true), 90); });
      menu.addEventListener("mouseleave", () => { clearTimeout(timer); timer = setTimeout(() => set(menu, false), 180); });
    }
    menu.addEventListener("keydown", (e) => {
      const items = [...menu.querySelectorAll(".item")];
      const at = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); set(menu, true); items[(at + 1) % items.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); set(menu, true); items[(at - 1 + items.length) % items.length].focus(); }
    });
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) menus.forEach((m) => set(m, false)); });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = menus.find((m) => m.classList.contains("open"));
    if (open) { set(open, false); open.querySelector("button").focus(); }
    sheet(false);
  });
  const sheetEl = $("sheet"), indexBtn = document.querySelector(".index-btn");
  const sheet = (open) => { sheetEl.classList.toggle("open", open); indexBtn.setAttribute("aria-expanded", open); };
  indexBtn.addEventListener("click", () => sheet(true));
  sheetEl.querySelector(".close").addEventListener("click", () => sheet(false));
}

let statLoaded = false;
async function loadStat() {
  if (statLoaded) return;
  statLoaded = true;
  try {
    const e = await evaluation();
    const el = document.querySelector("[data-stat]");
    el.textContent = pct(e.by.precedent_gate.stale_code_rate, 0);
    el.nextElementSibling.textContent = `Portorium, ${e.split} set. Nemotron Ultra asked directly: ${pct(e.by.ultra_zero_shot.stale_code_rate, 0)}.`;
  } catch { statLoaded = false; }
}

function reveal(root = document) {
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { threshold: 0.15 });
  root.querySelectorAll(".reveal, .struck, .stamp[data-auto]").forEach((el) => io.observe(el));
}

function stamp(el) {
  el.classList.remove("in");
  requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("in")));
}

// Evaluation numbers come from the latest run file of a split, never from page text.
async function evaluation(split) {
  const e = await api(`/api/eval${split ? `?split=${split}` : ""}`);
  e.by = Object.fromEntries(e.table.map((r) => [r.system, r]));
  return e;
}

chrome();
document.addEventListener("DOMContentLoaded", () => reveal());
