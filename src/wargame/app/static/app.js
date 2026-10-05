"use strict";
// WARGAME spectator viewer. Talks to the local engine server (wargame/app/server.py) over a small JSON API.

const $ = (id) => document.getElementById(id);
const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
};

// ---------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------

const S = {
  meta: null,
  year: 2026,
  world: null,            // {countries, provinces: [[name, owner, controller, lat, lon, area]]} at the start date
  shapes: [],             // per province (index = id - 1): {path: Path2D, bbox: [x0, y0, x1, y1]}
  control: [],            // per province: [owner, controller]
  names: {},              // tag -> country name
  war: null,              // latest state from /api/state
  running: false,
  sinceEvent: 0,
  mapVersion: -1,
  claims: new Set(),
  picking: false,
  setupSides: { attacker: null, defender: null },
  peaceShown: false,
  hover: null,
  selected: null,
  view: { scale: 4, tx: 0, ty: 0 },
};

// ---------------------------------------------------------------------------------------------
// Projection: Miller cylindrical, in "degrees" so world coordinates stay small.
// ---------------------------------------------------------------------------------------------

const DEG = Math.PI / 180;
const project = (lon, lat) => {
  const phi = Math.max(-85, Math.min(85, lat)) * DEG;
  return [lon, -1.25 * Math.log(Math.tan(Math.PI / 4 + 0.4 * phi)) / DEG];
};

function buildShapes(geometry) {
  const scale = geometry.scale;
  S.shapes = geometry.provinces.map((polys) => {
    const path = new Path2D();
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const rings of polys) {
      for (const flat of rings) {
        let ix = 0, iy = 0;
        for (let i = 0; i < flat.length; i += 2) {
          ix = i === 0 ? flat[0] : ix + flat[i];
          iy = i === 0 ? flat[1] : iy + flat[i + 1];
          const [x, y] = project(ix / scale, iy / scale);
          if (i === 0) path.moveTo(x, y); else path.lineTo(x, y);
          if (x < x0) x0 = x; if (y < y0) y0 = y; if (x > x1) x1 = x; if (y > y1) y1 = y;
        }
        path.closePath();
      }
    }
    return { path, bbox: [x0, y0, x1, y1] };
  });
}

// ---------------------------------------------------------------------------------------------
// Colours
// ---------------------------------------------------------------------------------------------

function hash(tag) {
  let h = 2166136261;
  for (const c of tag) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

function sides() {
  const out = { attacker: [], defender: [] };
  if (S.running && S.war) {
    for (const w of S.war.wars) {
      for (const m of w.sides.attacker || []) out.attacker.push(m.tag);
      for (const m of w.sides.defender || []) out.defender.push(m.tag);
    }
  } else {
    if (S.setupSides.attacker) out.attacker.push(S.setupSides.attacker);
    if (S.setupSides.defender) out.defender.push(S.setupSides.defender);
  }
  return out;
}

function colourFor(tag, side) {
  const h = hash(tag);
  if (side) {
    const list = side.list;
    const i = list.indexOf(tag);
    if (side.name === "attacker") return i === 0 ? "hsl(2, 62%, 46%)" : `hsl(${12 + (h % 28)}, ${48 + (h % 14)}%, ${38 + (h % 10)}%)`;
    return i === 0 ? "hsl(212, 62%, 48%)" : `hsl(${190 + (h % 45)}, ${38 + (h % 16)}%, ${36 + (h % 10)}%)`;
  }
  return `hsl(${h % 360}, ${9 + (h % 7)}%, ${20 + ((h >> 8) % 9)}%)`;
}

function palette() {
  const s = sides();
  const sideOf = {};
  for (const t of s.attacker) sideOf[t] = { name: "attacker", list: s.attacker };
  for (const t of s.defender) sideOf[t] = { name: "defender", list: s.defender };
  const cache = {};
  return (tag) => (cache[tag] ??= colourFor(tag, sideOf[tag]));
}

const patternCache = new Map();
function hatch(ctx, fg, bg) {
  const key = fg + bg;
  if (!patternCache.has(key)) {
    const c = document.createElement("canvas");
    c.width = c.height = 8;
    const g = c.getContext("2d");
    g.fillStyle = bg; g.fillRect(0, 0, 8, 8);
    g.strokeStyle = fg; g.lineWidth = 3;
    g.beginPath(); g.moveTo(-2, 10); g.lineTo(10, -2); g.moveTo(6, 10); g.lineTo(10, 6); g.moveTo(-2, 2); g.lineTo(2, -2); g.stroke();
    patternCache.set(key, ctx.createPattern(c, "repeat"));
  }
  const pattern = patternCache.get(key);
  pattern.setTransform(new DOMMatrix().scaleSelf(1 / S.view.scale));  // Stripes stay 8 px wide at any zoom.
  return pattern;
}

// ---------------------------------------------------------------------------------------------
// Map drawing
// ---------------------------------------------------------------------------------------------

const canvas = $("map");
const ctx = canvas.getContext("2d");
let drawQueued = false;
const redraw = () => { if (!drawQueued) { drawQueued = true; requestAnimationFrame(draw); } };

function resize() {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr);
  canvas.height = Math.round(r.height * dpr);
  redraw();
}

function setTransform() {
  const dpr = window.devicePixelRatio || 1;
  const v = S.view;
  ctx.setTransform(v.scale * dpr, 0, 0, v.scale * dpr, v.tx * dpr, v.ty * dpr);
}

function visibleBox() {
  const v = S.view, r = canvas.getBoundingClientRect();
  return [-v.tx / v.scale, -v.ty / v.scale, (r.width - v.tx) / v.scale, (r.height - v.ty) / v.scale];
}

function centroid(id) {
  const p = S.world.provinces[id - 1];
  return project(p[4], p[3]);
}

function draw() {
  drawQueued = false;
  if (!S.shapes.length) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#0a0e13";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  setTransform();
  const colour = palette();
  const [vx0, vy0, vx1, vy1] = visibleBox();
  const px = 1 / S.view.scale;
  const contested = new Map((S.running && S.war ? S.war.contested : []).map(([pid, tag, p]) => [pid, [tag, p]]));
  const goal = new Set(S.running && S.war ? S.war.wars.flatMap((w) => w.goal_provinces) : [...S.claims]);

  for (let i = 0; i < S.shapes.length; i++) {
    const { path, bbox } = S.shapes[i];
    if (bbox[2] < vx0 || bbox[0] > vx1 || bbox[3] < vy0 || bbox[1] > vy1) continue;
    const [owner, controller] = S.control[i];
    ctx.fillStyle = controller === owner ? colour(owner) : hatch(ctx, colour(owner), colour(controller));
    ctx.fill(path, "evenodd");
  }
  // Province edges, only when zoomed in enough to read them.
  if (S.view.scale > 6) {
    ctx.lineWidth = px * 0.6;
    ctx.strokeStyle = "rgba(0, 0, 0, 0.35)";
    for (let i = 0; i < S.shapes.length; i++) {
      const { path, bbox } = S.shapes[i];
      if (bbox[2] < vx0 || bbox[0] > vx1 || bbox[3] < vy0 || bbox[1] > vy1) continue;
      ctx.stroke(path);
    }
  }
  // Ground being taken: the attacker's colour, stronger as the province falls.
  for (const [pid, [tag, progress]] of contested) {
    const shape = S.shapes[pid - 1];
    ctx.globalAlpha = 0.2 + 0.6 * progress;
    ctx.fillStyle = colour(tag);
    ctx.fill(shape.path, "evenodd");
    ctx.globalAlpha = 1;
    ctx.setLineDash([4 * px, 3 * px]);
    ctx.lineWidth = 1.4 * px;
    ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
    ctx.stroke(shape.path);
    ctx.setLineDash([]);
  }
  // War goals.
  ctx.lineWidth = 1.6 * px;
  ctx.strokeStyle = "#e0b44c";
  for (const pid of goal) if (S.shapes[pid - 1]) ctx.stroke(S.shapes[pid - 1].path);
  // Offensives under way.
  if (S.running && S.war) drawAttacks(colour, px);
  // Nuclear detonations.
  if (S.running && S.war) {
    for (const w of S.war.wars) for (const n of w.nuclear_strikes) {
      const [x, y] = centroid(n.province);
      ctx.beginPath(); ctx.arc(x, y, 9 * px, 0, 2 * Math.PI);
      ctx.fillStyle = n.intercepted ? "rgba(255,255,255,0.25)" : "rgba(255, 245, 200, 0.9)";
      ctx.fill();
      ctx.lineWidth = 2 * px; ctx.strokeStyle = "#fff"; ctx.stroke();
    }
  }
  // Hover and selection.
  for (const [id, style] of [[S.hover, "rgba(255,255,255,0.7)"], [S.selected, "#ffffff"]]) {
    if (!id) continue;
    ctx.lineWidth = 2 * px; ctx.strokeStyle = style; ctx.stroke(S.shapes[id - 1].path);
  }
  drawLabels(colour, px);
}

function drawAttacks(colour, px) {
  for (const [origin, target, tag, power, amphibious] of S.war.attacks) {
    const [x0, y0] = centroid(origin), [x1, y1] = centroid(target);
    const dx = x1 - x0, dy = y1 - y0, len = Math.hypot(dx, dy);
    if (len === 0) continue;
    const ux = dx / len, uy = dy / len;
    const w = Math.max(1.5, Math.min(6, Math.log10(power + 1) * 2)) * px;
    const head = Math.min(len * 0.35, 10 * px + w * 2);
    const ex = x1 - ux * 2 * px, ey = y1 - uy * 2 * px;
    ctx.strokeStyle = colour(tag);
    ctx.lineWidth = w;
    ctx.setLineDash(amphibious ? [5 * px, 4 * px] : []);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(ex - ux * head * 0.6, ey - uy * head * 0.6); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colour(tag);
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - ux * head - uy * head * 0.55, ey - uy * head + ux * head * 0.55);
    ctx.lineTo(ex - ux * head + uy * head * 0.55, ey - uy * head - ux * head * 0.55);
    ctx.closePath(); ctx.fill();
    ctx.lineWidth = px; ctx.strokeStyle = "rgba(0,0,0,0.6)"; ctx.stroke();
  }
}

let labelAnchors = null;
function countryAnchors() {
  // Area-weighted centre of each country's provinces at the start date.
  const acc = {};
  S.world.provinces.forEach((p, i) => {
    const [x, y] = centroid(i + 1);
    const a = (acc[p[1]] ??= { x: 0, y: 0, w: 0 });
    a.x += x * p[5]; a.y += y * p[5]; a.w += p[5];
  });
  return Object.fromEntries(Object.entries(acc).map(([t, a]) => [t, { x: a.x / a.w, y: a.y / a.w, area: a.w }]));
}

function drawLabels(colour, px) {
  labelAnchors ??= countryAnchors();
  const s = sides();
  const featured = new Set([...s.attacker, ...s.defender]);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const [tag, a] of Object.entries(labelAnchors)) {
    const onScreen = Math.sqrt(a.area) / 111 * S.view.scale;  // Rough country size in pixels.
    if (!featured.has(tag) && onScreen < 70) continue;
    const size = featured.has(tag) ? 13 : 11;
    ctx.font = `${featured.has(tag) ? 700 : 500} ${size * px}px system-ui, sans-serif`;
    ctx.lineWidth = 3 * px;
    ctx.strokeStyle = "rgba(0,0,0,0.75)";
    const name = S.names[tag] || tag;
    ctx.strokeText(name, a.x, a.y);
    ctx.fillStyle = featured.has(tag) ? "#fff" : "rgba(220, 228, 236, 0.7)";
    ctx.fillText(name, a.x, a.y);
  }
}

// ---------------------------------------------------------------------------------------------
// Interaction: pan, zoom, hover, click
// ---------------------------------------------------------------------------------------------

function provinceAt(clientX, clientY) {
  const r = canvas.getBoundingClientRect();
  const sx = clientX - r.left, sy = clientY - r.top;
  const v = S.view;
  const wx = (sx - v.tx) / v.scale, wy = (sy - v.ty) / v.scale;
  const dpr = window.devicePixelRatio || 1;
  setTransform();
  for (let i = S.shapes.length - 1; i >= 0; i--) {
    const b = S.shapes[i].bbox;
    if (wx < b[0] || wx > b[2] || wy < b[1] || wy > b[3]) continue;
    if (ctx.isPointInPath(S.shapes[i].path, sx * dpr, sy * dpr, "evenodd")) return i + 1;
  }
  return null;
}

function fitTo(ids, pad = 0.15) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const id of ids) {
    const b = S.shapes[id - 1].bbox;
    // Skip far-flung bits (overseas islands) by trusting the bulk of the area: clamp later via percentile.
    x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]);
  }
  if (!isFinite(x0)) return;
  const r = canvas.getBoundingClientRect();
  const w = (x1 - x0) * (1 + pad * 2), h = (y1 - y0) * (1 + pad * 2);
  const scale = Math.max(1.2, Math.min(r.width / w, r.height / h, 80));
  S.view = { scale, tx: r.width / 2 - scale * (x0 + x1) / 2, ty: r.height / 2 - scale * (y0 + y1) / 2 };
  redraw();
}

function fitWar(tags) {
  // Frame the belligerents' home provinces, ignoring scattered overseas territory beyond 25 degrees from the bulk.
  const ids = [];
  S.world.provinces.forEach((p, i) => { if (tags.includes(p[1])) ids.push(i + 1); });
  if (!ids.length) return;
  const xs = ids.map((id) => centroid(id)[0]).sort((a, b) => a - b);
  const ys = ids.map((id) => centroid(id)[1]).sort((a, b) => a - b);
  const mx = xs[Math.floor(xs.length / 2)], my = ys[Math.floor(ys.length / 2)];
  const near = ids.filter((id) => { const [x, y] = centroid(id); return Math.abs(x - mx) < 40 && Math.abs(y - my) < 30; });
  fitTo(near.length ? near : ids);
}

function frameWar(state) {
  // The defender's home provinces, the war's goals and the first assaults, with context around them.
  const w = state.wars[0];
  const ids = new Set(w.goal_provinces);
  S.world.provinces.forEach((p, i) => { if (p[1] === w.target) ids.add(i + 1); });
  for (const [origin, target] of state.attacks) { ids.add(origin); ids.add(target); }
  const pts = [...ids].map((id) => centroid(id));
  const xs = pts.map((p) => p[0]).sort((a, b) => a - b), ys = pts.map((p) => p[1]).sort((a, b) => a - b);
  const mx = xs[Math.floor(xs.length / 2)], my = ys[Math.floor(ys.length / 2)];
  const near = [...ids].filter((id) => { const [x, y] = centroid(id); return Math.abs(x - mx) < 25 && Math.abs(y - my) < 20; });
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const id of near.length ? near : ids) {
    const b = S.shapes[id - 1].bbox;
    x0 = Math.min(x0, b[0]); y0 = Math.min(y0, b[1]); x1 = Math.max(x1, b[2]); y1 = Math.max(y1, b[3]);
  }
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const half = Math.max((x1 - x0) * 0.8, 9), halfY = Math.max((y1 - y0) * 0.8, 6);
  const r = canvas.getBoundingClientRect();
  const scale = Math.max(1.2, Math.min(r.width / (2 * half), r.height / (2 * halfY), 80));
  S.view = { scale, tx: r.width / 2 - scale * cx, ty: r.height / 2 - scale * cy };
  redraw();
}

let drag = null;
canvas.addEventListener("mousedown", (e) => { drag = { x: e.clientX, y: e.clientY, moved: false }; });
window.addEventListener("mouseup", (e) => {
  if (drag && !drag.moved) click(e);
  drag = null;
  canvas.classList.remove("dragging");
});
window.addEventListener("mousemove", (e) => {
  if (drag) {
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) { drag.moved = true; canvas.classList.add("dragging"); }
    if (drag.moved) {
      S.view.tx += dx; S.view.ty += dy; drag.x = e.clientX; drag.y = e.clientY;
      redraw();
    }
    return;
  }
  if (e.target !== canvas || !S.shapes.length) return;
  const id = provinceAt(e.clientX, e.clientY);
  if (id !== S.hover) { S.hover = id; redraw(); }
  showTooltip(id, e);
});
canvas.addEventListener("mouseleave", () => { S.hover = null; $("tooltip").hidden = true; redraw(); });
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const sx = e.clientX - r.left, sy = e.clientY - r.top;
  const k = Math.exp(-e.deltaY * 0.0015);
  const scale = Math.max(1, Math.min(400, S.view.scale * k));
  const f = scale / S.view.scale;
  S.view.tx = sx - (sx - S.view.tx) * f;
  S.view.ty = sy - (sy - S.view.ty) * f;
  S.view.scale = scale;
  redraw();
}, { passive: false });

function showTooltip(id, e) {
  const tip = $("tooltip");
  if (!id) { tip.hidden = true; return; }
  const p = S.world.provinces[id - 1];
  const [owner, controller] = S.control[id - 1];
  let html = `<b>${p[0]}</b>${S.names[owner] || owner}`;
  if (controller !== owner) html += ` · held by ${S.names[controller] || controller}`;
  const c = S.running && S.war ? S.war.contested.find((x) => x[0] === id) : null;
  if (c) html += `<br>${Math.round(c[2] * 100)}% taken by ${S.names[c[1]] || c[1]}`;
  if (S.picking && owner === $("f-defender").value) html += `<br><i>${S.claims.has(id) ? "Click to drop the claim" : "Click to claim"}</i>`;
  tip.innerHTML = html;
  const r = canvas.getBoundingClientRect();
  tip.style.left = `${Math.min(e.clientX - r.left + 14, r.width - 270)}px`;
  tip.style.top = `${e.clientY - r.top + 14}px`;
  tip.hidden = false;
}

async function click(e) {
  if (e.target !== canvas) return;
  const id = provinceAt(e.clientX, e.clientY);
  if (!id) return;
  if (S.picking) {
    const owner = S.control[id - 1][0];
    if (owner !== $("f-defender").value) return;
    S.claims.has(id) ? S.claims.delete(id) : S.claims.add(id);
    updateClaimBar();
    redraw();
    return;
  }
  S.selected = id;
  redraw();
  if (S.running) {
    try { renderProvince(await api(`/api/province/${id}`)); } catch (err) { /* The war may have been replaced. */ }
  }
}

function renderProvince(p) {
  const panel = $("province-panel");
  const stationed = Object.entries(p.stationed).map(([t, v]) => `${S.names[t] || t} ${v}`).join(", ") || "none";
  panel.innerHTML = `<h3>${p.name}</h3>
    <div class="kv">
      <span>Owner</span><b>${p.owner_name}</b>
      <span>Held by</span><b>${p.controller_name}${p.encircled ? " (encircled)" : ""}</b>
      ${p.contested_by ? `<span>Being taken</span><b>${Math.round(p.progress * 100)}% by ${S.names[p.contested_by] || p.contested_by}</b>` : ""}
      <span>Terrain</span><b>${p.terrain}</b>
      <span>Population</span><b>${p.population.toLocaleString()}</b>
      <span>Area</span><b>${p.area_km2.toLocaleString()} km²</b>
      <span>Forces</span><b>${stationed}</b>
      <span>Fieldworks</span><b>${Math.round(p.fortification * 100)}%</b>
      <span>War damage</span><b>${Math.round(p.damage * 100)}%</b>
      ${p.tags.length ? `<span>Features</span><b>${p.tags.join(", ").replaceAll("_", " ")}</b>` : ""}
    </div>`;
  panel.hidden = false;
}

// ---------------------------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------------------------

async function loadWorld(year) {
  S.year = year;
  $("loading").hidden = false;
  S.world = await api(`/api/world?year=${year}`);
  S.names = Object.fromEntries(S.world.countries.map((c) => [c.tag, c.name]));
  S.control = S.world.provinces.map((p) => [p[1], p[2]]);
  labelAnchors = null;
  $("loading").hidden = true;
  fillCountrySelects();
  redraw();
}

function option(value, label) {
  const o = document.createElement("option");
  o.value = value; o.textContent = label;
  return o;
}

function fillCountrySelects() {
  for (const [id, fallback] of [["f-attacker", "RUS"], ["f-defender", "UKR"]]) {
    const sel = $(id), prev = sel.value || fallback;
    sel.replaceChildren(...S.world.countries.map((c) => option(c.tag, `${c.name}${c.nuclear ? " ☢" : ""}`)));
    sel.value = S.names[prev] ? prev : fallback;
  }
  setupSidesChanged();
}

function setupSidesChanged() {
  S.setupSides = { attacker: $("f-attacker").value, defender: $("f-defender").value };
  for (const id of [...S.claims]) if (S.control[id - 1][0] !== S.setupSides.defender) S.claims.delete(id);
  updateClaimHint();
  redraw();
}

function updateClaimHint() {
  const goal = $("f-goal").value;
  const provinceGoal = goal === "border_skirmish" || goal === "territorial_conquest";
  const n = S.claims.size;
  $("claim-hint").textContent = provinceGoal
    ? (n ? `${n} claimed province${n > 1 ? "s" : ""}.` : "No provinces picked: the most valuable border provinces will be claimed.")
    : (n ? `${n} provinces demanded on top of the goal.` : "");
}

function updateClaimBar() {
  $("claim-count").textContent = `${S.claims.size} province${S.claims.size === 1 ? "" : "s"} of ${S.names[$("f-defender").value]} claimed`;
  updateClaimHint();
}

function setupMeta() {
  const m = S.meta;
  $("version").textContent = `alpha ${m.version}`;
  $("preset-grid").replaceChildren(...m.presets.map((p) => {
    const b = document.createElement("button");
    b.className = "preset";
    b.innerHTML = `<span class="meta">${p.year === 2021 ? "24 Feb 2022" : "1 Jan 2026"} · ${p.attacker} → ${p.defender}</span>
      <b>${p.name}</b><small>${p.blurb}</small>`;
    b.addEventListener("click", () => start({ preset: p.key }));
    return b;
  }));
  $("f-year").replaceChildren(...m.years.map((y) => option(y, y === 2021 ? "24 February 2022 (2021 data)" : "1 January 2026")));
  $("f-year").value = "2026";
  $("f-goal").replaceChildren(...m.goals.map((g) => option(g.value, g.label)));
  $("f-goal").value = "regime_change";
  $("f-tier").replaceChildren(...m.tiers.map((t) => option(t.value, t.label)));
  $("f-tier").value = "2";
  for (const id of ["f-amot", "f-dmot"]) $(id).replaceChildren(...m.motivations.map((x) => option(x.value, x.label)));
  $("speeds").replaceChildren(...m.speeds.map((s) => {
    const b = document.createElement("button");
    b.textContent = s.value === "PAUSED" ? "❚❚" : s.label;
    b.title = s.value === "PAUSED" ? "Pause (space)" : `${s.label} (${m.speeds.filter((x) => x.value !== "PAUSED").findIndex((x) => x.value === s.value) + 1})`;
    b.dataset.speed = s.value;
    b.disabled = true;
    b.addEventListener("click", () => setSpeed(s.value));
    return b;
  }));
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  $("tab-presets").hidden = t.dataset.tab !== "presets";
  $("tab-custom").hidden = t.dataset.tab !== "custom";
  if (t.dataset.tab === "custom") fitWar([$("f-attacker").value, $("f-defender").value]);
}));
$("f-year").addEventListener("change", async (e) => { S.claims.clear(); await loadWorld(Number(e.target.value)); });
$("f-attacker").addEventListener("change", () => { setupSidesChanged(); fitWar([S.setupSides.attacker, S.setupSides.defender]); });
$("f-defender").addEventListener("change", () => { setupSidesChanged(); fitWar([S.setupSides.attacker, S.setupSides.defender]); });
$("f-goal").addEventListener("change", updateClaimHint);
$("pick-claims").addEventListener("click", () => {
  S.picking = true;
  $("setup").hidden = true;
  $("claim-bar").hidden = false;
  updateClaimBar();
  fitWar([S.setupSides.attacker, S.setupSides.defender]);
});
$("claim-clear").addEventListener("click", () => { S.claims.clear(); updateClaimBar(); redraw(); });
$("claim-done").addEventListener("click", () => {
  S.picking = false;
  $("claim-bar").hidden = true;
  $("setup").hidden = false;
});
$("custom-form").addEventListener("submit", (e) => {
  e.preventDefault();
  start({
    year: Number($("f-year").value), attacker: $("f-attacker").value, defender: $("f-defender").value,
    goal: $("f-goal").value, tier: Number($("f-tier").value), nuclear: $("f-nuclear").checked,
    attacker_motivation: $("f-amot").value, defender_motivation: $("f-dmot").value,
    provinces: [...S.claims], seed: Number($("f-seed").value) || 0,
  });
});
$("new-war").addEventListener("click", openSetup);
$("peace-new").addEventListener("click", () => { $("peace").hidden = true; openSetup(); });
$("peace-close").addEventListener("click", () => { $("peace").hidden = true; });

async function openSetup() {
  if (S.running) await setSpeed("PAUSED").catch(() => {});
  $("setup").hidden = false;
}

// ---------------------------------------------------------------------------------------------
// Running a war
// ---------------------------------------------------------------------------------------------

async function start(body) {
  const err = $("setup-error");
  err.hidden = true;
  $("setup").querySelectorAll("button").forEach((b) => (b.disabled = true));
  try {
    const year = body.preset ? S.meta.presets.find((p) => p.key === body.preset).year : body.year;
    if (year !== S.year) await loadWorld(year);
    const state = await api("/api/start", body);
    S.running = true; S.war = null; S.sinceEvent = 0; S.mapVersion = -1; S.peaceShown = false; S.selected = null;
    $("feed").replaceChildren();
    supplyLines.clear();
    $("province-panel").hidden = true;
    applyState(state);
    $("setup").hidden = true;
    frameWar(state);
    document.querySelectorAll("#speeds button").forEach((b) => (b.disabled = false));
    await setSpeed("DAY_BY_DAY");
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    $("setup").querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}

async function setSpeed(speed) {
  await api("/api/speed", { speed });
  document.querySelectorAll("#speeds button").forEach((b) => b.classList.toggle("active", b.dataset.speed === speed));
}

async function poll() {
  if (S.running) {
    try {
      applyState(await api(`/api/state?since=${S.sinceEvent}&map=${S.mapVersion}`));
    } catch (e) { /* Server restarting or no war: keep trying. */ }
  }
  setTimeout(poll, 300);
}

function applyState(st) {
  S.war = st;
  Object.assign(S.names, st.names || {});
  if (st.provinces) { S.control = st.provinces; S.mapVersion = st.map_version; }
  $("scenario-name").textContent = st.scenario;
  $("date").textContent = st.date;
  $("day").textContent = `day ${st.day}`;
  document.querySelectorAll("#speeds button").forEach((b) => b.classList.toggle("active", b.dataset.speed === st.speed));
  addEvents(st.events);
  S.sinceEvent = st.event_count;
  renderWar(st.wars[0]);
  redraw();
  if (st.finished && !S.peaceShown) showPeace(st.wars[0]);
}

const supplyLines = new Map();  // "day|recipient" -> {li, suppliers}
function addEvents(events) {
  const feed = $("feed");
  for (const ev of events) {
    const supply = ev.kind === "lend_lease" && ev.message.match(/^(.*) begins supplying (.*)\.$/);
    if (supply) {  // Fold a day's new suppliers of one country into a single line.
      const key = `${ev.day}|${supply[2]}`;
      let line = supplyLines.get(key);
      if (!line) {
        line = { li: document.createElement("li"), suppliers: [] };
        line.li.className = "k-lend_lease";
        supplyLines.set(key, line);
        feed.prepend(line.li);
      }
      line.suppliers.push(supply[1]);
      const n = line.suppliers.length;
      line.li.innerHTML = `<span class="d">Day ${ev.day}</span>`;
      line.li.append(n === 1 ? ev.message : `${n} countries begin supplying ${supply[2]}: ${line.suppliers.join(", ")}.`);
      continue;
    }
    const li = document.createElement("li");
    li.className = `k-${ev.kind}`;
    li.innerHTML = `<span class="d">Day ${ev.day}</span>`;
    li.append(ev.message);
    feed.prepend(li);
  }
  while (feed.children.length > 400) feed.lastChild.remove();
}

const pct = (x) => `${Math.round(x * 100)}%`;
const goalNames = { border_skirmish: "Border skirmish", territorial_conquest: "Territorial conquest",
  regime_change: "Regime change", total_capitulation: "Total capitulation", coercion: "Coercion" };
const tierNames = { 1: "Vacuum", 2: "Proxy war", 3: "Unrestricted" };

function renderWar(w) {
  $("war-panel").hidden = false;
  $("goal-text").textContent = `${goalNames[w.goal] || w.goal}: ${S.names[w.holder] || w.holder} → ${S.names[w.target] || w.target}`;
  $("tier-text").textContent = `${tierNames[w.tier] || w.tier}${w.ended ? " · ended" : ""}`;
  const fill = $("score-fill"), s = w.war_score;
  fill.style.left = s >= 0 ? "50%" : `${50 + s / 2}%`;
  fill.style.width = `${Math.abs(s) / 2}%`;
  fill.style.background = s >= 0 ? "var(--attacker)" : "var(--defender)";
  $("score-value").textContent = `${s > 0 ? "+" : ""}${s.toFixed(1)}`;
  const colour = palette();
  for (const side of ["attacker", "defender"]) {
    const el = $(`side-${side}`);
    const members = w.sides[side] || [];
    el.innerHTML = `<h4>${side === "attacker" ? "Attacker" : "Defender"} side</h4>` + members.slice(0, 8).map((m) => member(m, colour)).join("")
      + (members.length > 8 ? `<div class="supporters">…and ${members.length - 8} more allies</div>` : "");
  }
  $("supporters").innerHTML = Object.entries(w.supporters).map(([to, from]) =>
    `<div>Arms to <b>${S.names[to] || to}</b> from ${from.map((t) => S.names[t] || t).join(", ")}.</div>`).join("");
}

function member(m, colour) {
  const pressure = m.threshold ? Math.min(1, m.pressure / m.threshold) : 0;
  const posture = m.posture ? m.posture.replaceAll("_", " ") : "—";
  return `<div class="member" style="border-color:${colour(m.tag)}">
    <div class="name">${m.name}<small>${m.role === "primary" ? "" : m.role.replaceAll("_", " ")} ${m.nuclear ? "☢" : ""}</small></div>
    <div class="stats">
      <span>Casualties<b>${m.casualties.toLocaleString()}</b></span>
      <span>Under arms<b>${m.active.toLocaleString()}</b></span>
      <span>Posture<b>${posture}</b></span>
      <span>Air superiority<b>${pct(m.air)}</b></span>
      <span>Drones<b>${pct(m.drones)}</b></span>
      <span>Strike damage<b>${pct(m.strategic_damage)}</b></span>
    </div>
    <div class="stats" style="grid-template-columns:1fr 1fr">
      <span>Resolve ${pct(m.resolve)}<div class="bar"><i style="width:${pct(m.resolve)};background:var(--good)"></i></div></span>
      <span>Collapse ${pct(pressure)}${m.main_driver ? ` · ${m.main_driver.replaceAll("_", " ")}` : ""}
        <div class="bar"><i style="width:${pct(pressure)};background:${pressure > 0.8 ? "var(--danger)" : "var(--warn)"}"></i></div></span>
    </div>
  </div>`;
}

function showPeace(w) {
  S.peaceShown = true;
  const t = w.treaty;
  $("peace-title").textContent = t && t.winner ? `${S.names[t.winner] || t.winner} prevails` : "The war is over";
  $("peace-reason").textContent = t ? `${t.reason[0].toUpperCase()}${t.reason.slice(1)}.` : "";
  $("peace-terms").replaceChildren(...(t ? t.terms : []).map((term) => {
    const li = document.createElement("li");
    const who = `${S.names[term.beneficiary] || term.beneficiary || ""}`;
    const on = `${S.names[term.target] || term.target || ""}`;
    li.textContent = {
      white_peace: "White peace: everyone goes home.",
      province_transfer: `${on} cedes ${term.provinces} province${term.provinces === 1 ? "" : "s"} to ${who}.`,
      annexation: `${who} absorbs ${on}.`,
      puppet: `${on} becomes a puppet of ${who}.`,
      demilitarization: `${on} is demilitarised.`,
      reparations: `${on} pays reparations to ${who}.`,
    }[term.type] || term.type;
    return li;
  }));
  $("peace").hidden = false;
}

// ---------------------------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------------------------

// Keyboard: space pauses or resumes, 1-5 pick a speed.
let lastSpeed = "DAY_BY_DAY";
window.addEventListener("keydown", (e) => {
  if (!S.running || e.target.closest("input, select, textarea") || !$("setup").hidden) return;
  const speeds = S.meta.speeds.map((s) => s.value).filter((v) => v !== "PAUSED");
  if (e.code === "Space") {
    e.preventDefault();
    const now = S.war ? S.war.speed : "PAUSED";
    if (now !== "PAUSED") lastSpeed = now;
    setSpeed(now === "PAUSED" ? lastSpeed : "PAUSED");
  } else if (/^Digit[1-9]$/.test(e.code)) {
    const pick = speeds[Number(e.code.slice(5)) - 1];
    if (pick) setSpeed(pick);
  }
});

async function boot() {
  window.addEventListener("resize", resize);
  resize();
  S.meta = await api("/api/meta");
  setupMeta();
  const [geometry] = await Promise.all([api("/api/geometry"), loadWorld(2026)]);
  buildShapes(geometry);
  const r = canvas.getBoundingClientRect();
  S.view = { scale: r.width / 380, tx: r.width / 2, ty: r.height / 2 - 20 * (r.width / 380) };
  redraw();
  poll();
}

boot().catch((e) => { $("loading").textContent = `Could not load: ${e.message}`; });
