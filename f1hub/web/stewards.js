/* F1 Hub dashboard - stewarding: the FIA rule behind a decision, provisional grid, penalty points, red flags.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- helpers ---------- */
const RULE_KINDS = new Set(["penalty", "penalty_served", "investigation", "decision", "reprimand", "black", "black_white", "black_orange", "lap_deleted"]);
function msgRow(m) {
  const rule = RULE_KINDS.has(m.kind) && (m.cars || []).length && !/TRACK LIMITS/i.test(m.text || "") || ["penalty", "reprimand"].includes(m.kind);
  return `<div class="msg k-${esc(m.kind)}"><span class="when num">${m.lap ? "L" + m.lap : ""} ${esc(m.time)}</span>
    <span><span class="tag">${esc(KIND_LABEL[m.kind] || m.kind)}</span>${esc(m.text)}${rule
      ? `<button class="rulebtn" title="The rule and the stewards' official decision" data-cars="${esc((m.cars || []).join(","))}" data-text="${esc(m.text)}" onclick="openRule(this)">${ico("doc")}Rule</button>` : ""}</span></div>`;
}

/* ---------- the rule behind a stewards' message, from fia.com (f1hub/fia.py) ---------- */
const RULE = {gen: 0};
function closeRule() { $("rulebox").classList.add("hidden"); RULE.gen++; }
document.addEventListener("keydown", e => { if (e.key === "Escape") closeRule(); });
function fiaBefore() {      // spoiler mode while delayed or replaying: nothing the stewards published after the moment on screen
  const st = (D && D.settings) || {};
  return st.spoiler !== false && (D.mode === "replay" || curDelay() > 0) ? "&before=" + encodeURIComponent(vnow().toISOString()) : "";
}
const ext = (url, label) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;
async function openRule(btn) {
  const gen = ++RULE.gen, cars = btn.dataset.cars || "", text = btn.dataset.text || "";
  $("rulebox").classList.remove("hidden");
  const head = `<button class="x" onclick="closeRule()" title="Close (Esc)">×</button><h3>${ico("doc")}The rule & the stewards' decision</h3><div class="msgtxt">${esc(text)}</div>`;
  $("rulein").innerHTML = head + `<div class="muted">Looking up the official documents on fia.com…</div>`;
  let r;
  try { r = await (await fetch(`/api/fia/stewarding?car=${encodeURIComponent(cars)}&text=${encodeURIComponent(text)}${fiaBefore()}`, {cache: "no-store"})).json(); }
  catch (e) { r = {error: "The local server didn't answer"}; }
  if (gen !== RULE.gen) return;
  if (r.error) { $("rulein").innerHTML = head + `<div class="rulenote">Couldn't reach the FIA documents: ${esc(r.error)}</div>`; return; }
  let body = "";
  const dec = r.decision;
  if (dec) {
    const chips = (dec.articles || []).map((a, i) => a.source === "isc"
      ? ext("https://www.fia.com/regulation/category/123", esc(a.label))
      : `<button class="artchip" data-ref="${esc(a.ref)}" onclick="showArticle(this, '${esc(a.ref)}', ${r.year})">${esc(a.label || a.ref)} ▸</button>`).join("");
    body += `<dl class="dec">
      ${dec.no ? `<dt>Driver</dt><dd>${esc(dec.no)}${dec.competitor ? ` · <span class="muted">${esc(dec.competitor)}</span>` : ""}</dd>` : ""}
      ${dec.session ? `<dt>Session</dt><dd>${esc(dec.session)}${dec.time ? ` · ${esc(dec.time)}` : ""}</dd>` : ""}
      ${dec.fact ? `<dt>Fact</dt><dd>${esc(dec.fact)}</dd>` : ""}
      <dt>Rule broken</dt><dd>${esc(dec.infringement || "-")}<div style="margin-top:6px">${chips}</div><div id="artout"></div></dd>
      ${dec.decision ? `<dt>Decision</dt><dd class="verdict">${esc(dec.decision)}</dd>` : ""}
    </dl>
    ${dec.reason ? `<details><summary>The stewards' reasons</summary><div class="arttext" style="border-color:var(--line)">${esc(dec.reason)}</div></details>` : ""}`;
  } else {
    body += `<div class="rulenote"><b style="color:var(--text)">The stewards haven't published a decision on this yet.</b>
      Once they do, it appears here with the exact rule they applied.</div>`;
    if (r.decision_error) body += `<div class="rulenote">${esc(r.decision_error)}</div>`;
    if ((r.suggest || []).length) body += `<div class="rulenote">Possibly relevant rules, matched on the wording of the message (not the stewards' ruling):</div>
      <div>${r.suggest.map(x => `<button class="artchip" onclick="showArticle(this, '${esc(x.ref)}', ${r.year})" title="${esc(x.preview)}">${esc(x.ref)} ▸</button>`).join("")}</div><div id="artout"></div>`;
  }
  const others = (r.docs || []).filter(x => !dec || x.url !== dec.url);
  if (others.length) body += `<div class="rulenote" style="margin-top:12px">${dec ? "Other documents about this car" : "Documents about this car so far"}:</div>
    <div class="relDocs">${others.map(x => ext(x.url, esc(x.title)) + ` <span class="muted" style="font-size:11px">${esc(x.published)}</span>`).join("")}</div>`;
  body += `<div class="fialinks">${dec ? ext(dec.url, `<b>Official FIA decision${dec.document ? " · Doc " + dec.document : ""} (PDF)</b>`) : ""}
    ${ext(r.page, `All FIA documents · ${esc(r.meeting)} ${r.year}`)}</div>`;
  $("rulein").innerHTML = head + body;
  const first = $("rulein").querySelector(".artchip");
  if (first && dec) first.click();                 // show the rule they broke straight away
}
async function showArticle(btn, ref, year) {
  const out = $("artout");
  if (!out) return;
  if (btn.classList.contains("on")) { btn.classList.remove("on"); out.innerHTML = ""; return; }
  $("rulein").querySelectorAll(".artchip.on").forEach(b => b.classList.remove("on"));
  btn.classList.add("on");
  out.innerHTML = `<div class="muted" style="font-size:12px;margin-top:6px">Opening ${esc(ref)} in the regulations…</div>`;
  let a;
  try { a = await (await fetch(`/api/fia/article?ref=${encodeURIComponent(ref)}&year=${year}`, {cache: "no-store"})).json(); }
  catch (e) { a = {error: "The local server didn't answer"}; }
  if (!btn.classList.contains("on")) return;
  if (a.error) { out.innerHTML = `<div class="rulenote">${esc(a.error)}</div>`; return; }
  const see = (a.see_also || []).map(x => `<button class="artchip" onclick="showArticle(this, '${esc(x)}', ${year})">${esc(x)} ▸</button>`).join("");
  out.innerHTML = `<div class="arttext">${esc(a.text)}</div>
    <div class="artsrc">${ext(a.url, esc(a.regulations))}${see ? `<div style="margin-top:6px">Refers to: ${see}</div>` : ""}</div>`;
}
/* ---------- provisional grid + penalty points, from the FIA decisions (f1hub/stewards.py) ---------- */
const GRIDV = {at: 0, busy: false, key: null}, PTS = {at: 0, busy: false, open: new Set(), key: null};
const shortWhy = x => x.pit_lane ? "pit lane" : x.back && !x.places ? "back of grid" : `+${x.places}`;
const docTopic = t => { const p = (t || "").split(" - "); return p.length > 2 ? p[p.length - 1] : t; };
function indexNote(st) {
  if (!st) return "";
  if (st.state === "building") return `Reading the stewards' decisions on fia.com… ${st.done}/${st.total || "?"}`;
  if (st.state === "paused") return `Paused at ${st.done}/${st.total || "?"} decisions: ${esc(st.error || "fia.com asked for fewer requests")}. What's read so far is shown.`;
  if (st.state === "error" || st.error) return `fia.com: ${esc(st.error || "error")}`;
  return "";
}
async function loadGrid(force) {
  const d = D; if (!d || !d.session_key) return;
  const show = !isRaceSession(d);
  $("gridcard").classList.toggle("hidden", !show);
  if (!show || GRIDV.busy) return;
  const key = d.session_key + "|" + $("gridtarget").value;
  const building = GRIDV.last && GRIDV.last.status && GRIDV.last.status.state === "building";
  if (!force && GRIDV.key === key && Date.now() - GRIDV.at < (building ? 8000 : 30000)) return;
  GRIDV.busy = true; GRIDV.key = key; GRIDV.at = Date.now();
  let r;
  try { r = await (await fetch(`/api/fia/grid?target=${$("gridtarget").value}${fiaBefore()}`, {cache: "no-store"})).json(); }
  catch (e) { r = {error: "The local server didn't answer"}; }
  GRIDV.busy = false; GRIDV.last = r;
  renderGrid(r);
}
function renderGrid(r) {
  if (r.error) { $("pgrid").innerHTML = `<div class="pad muted">${esc(r.error)}</div>`; $("gridnote").textContent = ""; return; }
  $("gridtarget").classList.toggle("hidden", !(D && /sprint/i.test(D.session || "")) && r.target !== "Sprint");
  $("gridh").textContent = `Provisional ${r.target === "Sprint" ? "Sprint" : "Race"} grid`;
  if (!r.grid || !r.grid.length) {
    $("pgrid").innerHTML = `<div class="pad muted">No qualifying order yet.</div>`;
    $("gridnote").innerHTML = indexNote(r.status); return;
  }
  const why = g => (g.reasons || []).map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer"
      title="${esc(x.title + (x.carried ? " (" + x.event + ")" : "") + " - " + x.decision)}">${esc(shortWhy(x))} ${esc(docTopic(x.title))}${x.carried ? " · " + esc(x.event.replace(/ Grand Prix$/, " GP")) : ""}</a>`).join("");
  const row = g => {
    const pen = (g.reasons || []).length > 0;
    const mv = g.pit_lane ? "PL" : g.moved ? (g.moved > 0 ? `<span style="color:#2bd46b">▲${g.moved}</span>` : `<span style="color:#ff7070">▼${-g.moved}</span>`) : "";
    return `<div class="gr${pen ? " pen" : ""}"><span class="p">${g.pos ?? "PL"}</span>
      <span class="drv"><span class="stripe" style="background:${esc(g.colour || "#888")}"></span>${esc(g.code || g.number)}${g.classified ? "" : ` <span class="muted" style="font-size:11px">no time</span>`}</span>
      <span class="q muted" title="Qualifying position">Q${g.quali_pos}</span><span class="mv">${mv}</span>
      ${pen ? `<div class="why">${why(g)}</div>` : ""}</div>`;
  };
  const main = r.grid.filter(g => !g.pit_lane), pit = r.grid.filter(g => g.pit_lane);
  $("pgrid").innerHTML = main.map(row).join("") + (pit.length ? `<div class="sep">Pit lane start</div>` + pit.map(row).join("") : "");
  const npen = r.grid.filter(g => (g.reasons || []).length).length;
  $("gridnote").innerHTML = [
    `From the ${esc(r.source || "qualifying order")}${r.live ? " - still changing" : ""}, with ${npen ? npen + " driver" + (npen > 1 ? "s'" : "'s") : "no"} grid penalties from the stewards' decisions${r.previous_event ? ` (this weekend's, and race penalties carried from ${esc(r.previous_event)})` : ""}.`,
    `Unofficial: the FIA publishes the real provisional grid 2 hours before the start, and cars that can't start or are changed in parc fermé move again.`,
    indexNote(r.status), (r.unplaced || []).length ? `Penalised but not in the order: ${r.unplaced.map(p => "#" + p.number).join(", ")}` : ""].filter(Boolean).join("<br>");
}
async function loadPoints(force) {
  const d = D; if (!d || !d.session_key || PTS.busy) return;
  const building = PTS.last && PTS.last.status && PTS.last.status.state === "building";
  if (!force && PTS.key === d.session_key && Date.now() - PTS.at < (building ? 8000 : 120000)) return;
  PTS.busy = true; PTS.key = d.session_key; PTS.at = Date.now();
  let r;
  try { r = await (await fetch(`/api/fia/points?x=1${fiaBefore()}`, {cache: "no-store"})).json(); }
  catch (e) { r = {error: "The local server didn't answer"}; }
  PTS.busy = false; PTS.last = r;
  renderPoints(r);
}
function ptsToggle(k) { PTS.open.has(k) ? PTS.open.delete(k) : PTS.open.add(k); renderPoints(PTS.last); }
function renderPoints(r) {
  if (!r) return;
  if (r.error) { $("ptstab").innerHTML = `<tr><td colspan="5" class="muted">${esc(r.error)}</td></tr>`; $("ptsnote").textContent = ""; return; }
  const ban = r.ban_at || 12, rows = r.drivers || [];
  const code = n => { const x = D && (D.rows || []).find(x => x.number === n); return x ? x : null; };
  $("ptstab").innerHTML = rows.map(p => {
    const k = p.name || p.number, info = code(p.number), hot = p.points >= ban - 3;
    const warn = (p.points >= ban - 2 ? `<span class="warnchip" title="${ban} points in 12 months = a one-race ban">${ban - p.points <= 0 ? "BAN" : ban - p.points + " to ban"}</span>` : "") +
      (p.reprimand_risk ? `<span class="warnchip" title="A fifth reprimand (with four for driving) is a 10-place grid drop">1 reprimand from −10</span>` : "");
    const nx = p.next_expiry;
    const det = PTS.open.has(k) ? `<tr class="det"><td colspan="5">${p.items.map(i => `<div>+${i.points} · ${esc(i.event.replace(/ Grand Prix$/, " GP"))} ${i.year} ${esc(i.session || "")} -
        <a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(docTopic(i.title))} ↗</a> <span class="muted">· off on ${esc(i.expires)}</span></div>`).join("") || ""}
        ${p.reprimands.map(i => `<div>Reprimand${i.kind === "driving" ? " (driving)" : ""}${i.no ? " #" + i.no : ""} · ${esc(i.event.replace(/ Grand Prix$/, " GP"))} ${esc(i.session || "")} -
        <a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(docTopic(i.title))} ↗</a></div>`).join("")}
        ${p.stated_total != null && p.stated_total !== p.points ? `<div class="muted">The latest FIA decision gave a total of ${p.stated_total}${r.status && r.status.state !== "ready" ? " - older decisions are still being read" : " (points from outside F1, or an expiry since)"}.</div>` : ""}</td></tr>` : "";
    return `<tr class="main${hot ? " hot" : ""}" onclick="ptsToggle(${JSON.stringify(String(k)).replace(/"/g, "&quot;")})">
      <td><span class="stripe" style="background:${esc(info ? info.colour : "#888")}"></span>${esc(info ? info.code : (p.name || "#" + p.number))}${warn}</td>
      <td class="r"><b>${p.points}</b></td><td><div class="bar"><i style="width:${Math.min(100, p.points / ban * 100)}%"></i></div></td>
      <td class="r muted">${nx ? `${nx.points} on ${esc(nx.expires.slice(5))}` : "-"}</td>
      <td class="r">${p.reprimand_count ? `${p.reprimand_count}${p.driving_reprimands ? ` <span class="muted">(${p.driving_reprimands})</span>` : ""}` : "-"}</td></tr>${det}`;
  }).join("") || `<tr><td colspan="5" class="muted">${r.status && r.status.state === "building" ? "…" : "No penalty points or reprimands in the FIA decisions."}</td></tr>`;
  $("ptsnote").innerHTML = [indexNote(r.status),
    `${ban} points within 12 months is a one-race ban; points drop off 12 months after they were given. Five reprimands in a season (four for driving) is a 10-place grid drop. Counted from the stewards' decision documents on fia.com.`].filter(Boolean).join("<br>");
}

function countdown(iso) {
  const s = Math.round((new Date(iso) - vnow()) / 1000);
  if (s <= 0) return "due now";
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function since(iso) {
  const s = Math.max(0, Math.round((vnow() - new Date(iso)) / 1000));
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}
/* ---------- the circuit map ----------
   Two layers: the circuit itself (asphalt, flags, corners, start line, stopped cars), rebuilt only when one of those
   changes, and the cars, refreshed with every update. Cars are placed where map.js is drawing them right now (a few
   seconds behind the newest sample), so an update never makes them jump. */
function trackLayers(d, W, opts = {}) {
  const m = d.map || {}, cars = m.cars || [], outline = m.outline || [];
  if (!outline.length && !cars.length) return null;
  const big = !!opts.labels;
  const th = (m.rotation || 0) * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
  const rot = (x, y) => [x * cs - y * sn, x * sn + y * cs];
  const base = (outline.length ? outline : cars.map(c => [c.x, c.y])).map(p => rot(p[0], p[1]));
  const xs = base.map(p => p[0]), ys = base.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = big ? 60 : 18;
  const k = (W - 2 * pad) / (maxX - minX || 1);
  const H = Math.round((maxY - minY) * k + 2 * pad);
  const P = (x, y) => { const [a, b] = rot(x, y); return [pad + (a - minX) * k, H - (pad + (b - minY) * k)]; };
  const pts = outline.map(p => P(p[0], p[1]));
  const f1 = v => v.toFixed(1);
  const line = arr => arr.map((p, i) => (i ? "L" : "M") + f1(p[0]) + "," + f1(p[1])).join("");
  const sw = big ? 15 : 9, rc = d.rc || {}, flags = rc.sector_flags || {}, cleared = rc.sector_cleared || {};
  const neutral = d.status === "RED" ? "#e10600" : d.status === "SC" || d.status === "VSC" ? "#ffb000" : "";

  // marshal sectors: only a sector with something to say gets colour
  const secs = (m.sectors || []).slice().sort((a, b) => a.start - b.start).map((sec, i, all) => {
    const f = flags[String(sec.number)];
    const recent = cleared[String(sec.number)] && (vnow() - new Date(cleared[String(sec.number)])) < 60000;
    let col = neutral, cls = "";
    if (f === "YELLOW") col = "#ffd300";
    else if (f === "DOUBLE YELLOW") { col = "#ffd300"; cls = "dbl"; }
    else if (recent && d.status !== "RED") col = "#39b54a";
    const end = i + 1 < all.length ? all[i + 1].start : all[0].start + pts.length;
    return {sec, f, col, cls, end};
  });
  const incs = (d.incidents || []).filter(i => i.state === "stopped" && i.kind !== "spin");
  const key = [W, big ? 1 : 0, outline.length, pts[0] ? pts[0].map(f1).join() : "", m.rotation || 0, (m.corners || []).length,
    secs.map(x => x.col + x.cls).join(), neutral, incs.map(i => i.id + i.kind).join()].join("|");

  let st = "";
  if (pts.length) {
    const dp = line(pts) + "Z";
    st += `<path class="t-glow" d="${dp}" stroke-width="${sw + 22}"/>
      <path class="t-edge draw" d="${dp}" pathLength="1" stroke-width="${sw + 3}"/>
      <path class="t-asph draw" d="${dp}" pathLength="1" stroke-width="${sw}"/>`;
    if (opts.accent) st += `<path class="t-accent draw" d="${dp}" pathLength="1" stroke-width="2"/>`;
    // SC / VSC / red: one unbroken loop; then each flagged sector on top (square ends, so the joins don't blot)
    const band = (dd, col, cls, tip = "") => `<g class="t-flag ${cls}"><path d="${dd}" stroke="${col}" stroke-opacity=".2" stroke-width="${sw + 10}" stroke-linecap="butt"/>
        <path d="${dd}" stroke="${col}" stroke-width="${Math.round(sw * .45)}" stroke-linecap="butt">${tip}</path></g>`;
    if (neutral) st += band(dp, neutral, "");
    secs.forEach(({sec, f, col, cls, end}) => {
      const seg = [];
      for (let j = sec.start; j <= end; j++) seg.push(pts[j % pts.length]);
      const sd = line(seg), tip = `<title>Marshal sector ${sec.number}${f ? ": " + f.toLowerCase() : ""}</title>`;
      st += col && (col !== neutral || cls) ? band(sd, col, cls, tip) : `<path class="t-hit" d="${sd}" stroke-width="${sw}">${tip}</path>`;
    });
  }
  if (pts.length > 12) {
    // start / finish: both outline sources begin at the line, so a chequered bar across pts[0]
    const [x0, y0] = pts[0], a = pts[pts.length - 3], b = pts[3];
    let dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
    const half = sw / 2 + 2, cell = big ? 4 : 3, n = Math.round(2 * half / cell);
    const ang = Math.atan2(dx, -dy) * 180 / Math.PI;
    let sq = "";
    for (let i = 0; i < n; i++) for (let j = 0; j < 2; j++)
      sq += `<rect x="${f1(-half + i * cell)}" y="${(j - 1) * cell}" width="${cell}" height="${cell}" fill="${(i + j) % 2 ? "#111" : "#fff"}"/>`;
    st += `<g class="t-sf" transform="translate(${f1(x0)},${f1(y0)}) rotate(${f1(ang)})"><title>Start / finish</title>${sq}</g>`;
    // direction of travel, a little after the line
    let i = 1;
    for (let dist = 0; i < pts.length - 2 && dist < (big ? 48 : 22); i++) dist += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
    const q0 = pts[i - 1], q1 = pts[i + 1];
    const da = Math.atan2(q1[1] - q0[1], q1[0] - q0[0]) * 180 / Math.PI, s = big ? 1.3 : .8;
    st += `<g class="t-dir" transform="translate(${f1(pts[i][0])},${f1(pts[i][1])}) rotate(${f1(da)}) scale(${s})"><path d="M-6,-3.5 L-2,0 L-6,3.5 M0,-3.5 L4,0 L0,3.5"/></g>`;
  }
  const placed = [];
  if (opts.corners) (m.corners || []).forEach(c => {
    const a = (c.angle || 0) * Math.PI / 180, off = 420;
    const [x, y] = P(c.x + Math.cos(a) * off, c.y + Math.sin(a) * off);
    placed.push({x: x - 9, y: y - 9, w: 18, h: 18});
    st += `<g transform="translate(${f1(x)},${f1(y)})"><g class="t-corner"><circle r="8.5"/><text y="3.4" text-anchor="middle">${+c.number}</text></g></g>`;
  });
  st += incs.map(i => {
    const [x, y] = P(i.x, i.y), col = i.kind === "crash" ? "#ff2a2a" : "#ffb000", r = big ? 15 : 10;
    return `<g class="t-inc" transform="translate(${f1(x)},${f1(y)})" style="--ic:${col}"><circle class="wave" r="${r}"/><circle r="${r}" fill="none" stroke="${col}" stroke-width="2.5"/>
      <text y="${-r - 6}" font-size="${big ? 13 : 10}" text-anchor="middle" fill="${col}">${esc(i.code)}</text>
      <title>${i.kind === "crash" ? "Crash" : "Stopped"}: ${esc(i.code)}${i.corner ? " · Turn " + i.corner : ""}</title></g>`;
  }).join("");

  // the cars, leader on top; labels placed so they don't overlap, each tied to its car by a short line
  const T = vnow().getTime() - (typeof MAP_LAG === "number" ? MAP_LAG : 0);
  const at = c => { const p = typeof carAt === "function" ? carAt(c.number, T) : null; return p ? P(p[1], p[2]) : P(c.x, c.y); };
  const dots = [], labels = [];
  const fits = b => b.x > 2 && b.x + b.w < W - 2 && b.y > 2 && b.y + b.h < H - 2 &&
    !placed.some(o => b.x < o.x + o.w + 2 && b.x + b.w + 2 > o.x && b.y < o.y + o.h + 2 && b.y + b.h + 2 > o.y);
  cars.slice().sort((a, b) => (a.pos || 99) - (b.pos || 99)).forEach(c => {
    const [x, y] = at(c), me = c.number === focus, r = big ? 7 : 4.5;
    const qr = d.quali && d.quali.rows ? d.quali.rows[c.number] || {} : {}, push = qr.on_lap && !me;
    dots.push(`<circle data-n="${c.number}" class="car${me ? " me" : ""}${push ? " push" : ""}${qr.out_in ? " ko" : ""}" cx="${f1(x)}" cy="${f1(y)}"
      r="${me ? r + 2 : r}" fill="${esc(c.colour)}" onclick="setFocus(${c.number}, true)"><title>P${c.pos ?? "?"} ${esc(c.code)}${push ? " · on a flying lap" : qr.out_in ? " · knocked out" : ""}</title></circle>`);
    if (!big) return;
    const pos = String(c.pos ?? ""), code = String(c.code || ""), pw = pos.length * 6.2, w = 12 + pw + (pos ? 5 : 0) + code.length * 7.6, h = 18;
    for (const [dx, dy] of [[13, -24], [13, 6], [-13 - w, -24], [-13 - w, 6], [-w / 2, -36], [-w / 2, 18], [22, -9], [-22 - w, -9]]) {
      const box = {x: x + dx, y: y + dy, w, h};
      if (!fits(box)) continue;
      placed.push(box);
      const ax = Math.min(Math.max(x, box.x), box.x + w), ay = Math.min(Math.max(y, box.y), box.y + h);
      const dl = Math.hypot(ax - x, ay - y) || 1, sx = x + (ax - x) / dl * (r + 1), sy = y + (ay - y) / dl * (r + 1);
      labels.push(`<g class="t-lab${me ? " me" : ""}" data-n="${c.number}" data-x="${f1(x)}" data-y="${f1(y)}" onclick="setFocus(${c.number}, true)">
        <line x1="${f1(sx)}" y1="${f1(sy)}" x2="${f1(ax)}" y2="${f1(ay)}" stroke="${esc(c.colour)}"/>
        <rect class="bg" x="${f1(box.x)}" y="${f1(box.y)}" width="${f1(w)}" height="${h}" rx="3"/>
        <rect x="${f1(box.x)}" y="${f1(box.y)}" width="3" height="${h}" fill="${esc(c.colour)}"/>
        ${pos ? `<text class="p" x="${f1(box.x + 8)}" y="${f1(box.y + 13)}">${esc(pos)}</text>` : ""}
        <text class="c" x="${f1(box.x + 8 + pw + (pos ? 5 : 0))}" y="${f1(box.y + 13)}">${esc(code)}</text></g>`);
      break;
    }
  });
  return {key: key + (opts.accent ? "|a" : ""), W, H, proj: [cs, sn, pad, minX, minY, k, H].join(","), st, cars: dots.reverse().join("") + labels.join(""), big};
}
// draw a map into el: the whole thing the first time (or when the circuit layer changes), otherwise only the cars
function paintTrack(el, d, W, opts = {}) {
  const L = trackLayers(d, W, opts);
  if (!L) { if (!el.querySelector(".mapwait")) el.innerHTML = `<div class="mapwait">${ico("loader", "spin")}Waiting for location data…</div>`; return; }
  const svg = el.querySelector("svg.trk");
  if (svg && svg.dataset.key === L.key) { svg.querySelector(".t-cars").innerHTML = L.cars; return; }
  el.innerHTML = `<svg class="trk${svg || opts.intro === false ? "" : " intro"}${L.big ? " big" : ""}" data-key="${esc(L.key)}" data-proj="${L.proj}" viewBox="0 0 ${L.W} ${L.H}" width="100%"
      preserveAspectRatio="xMidYMid meet"><g class="t-static">${L.st}</g><g class="t-cars">${L.cars}</g>
      <g class="focusring" style="display:none"><circle r="${L.big ? 14 : 9}"/></g></svg>`;
}
function lineChart(series, {W = 900, H = 320, yInvert = false, yLabel = "", xFmt = v => v, yFmt = v => v, yMax = null, yMin = null, bands = []} = {}) {
  const all = series.flatMap(s => s.points);
  if (all.length < 2) return `<div class="muted">Not enough data yet.</div>`;
  const L = 52, R = 12, T = 12, B = 26;
  let x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0]));
  let y0 = yMin ?? Math.min(...all.map(p => p[1])), y1 = yMax ?? Math.max(...all.map(p => p[1]));
  if (y1 === y0) y1 = y0 + 1;
  const X = v => L + (v - x0) / (x1 - x0 || 1) * (W - L - R);
  const Y = v => { const t = (Math.min(y1, Math.max(y0, v)) - y0) / (y1 - y0); return yInvert ? T + t * (H - T - B) : H - B - t * (H - T - B); };
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const v = y0 + (y1 - y0) * i / 4, y = Y(v);
    g += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="#2a2e3a"/><text x="${L - 6}" y="${y + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${yFmt(v)}</text>`;
  }
  for (let i = 0; i <= 5; i++) {
    const v = x0 + (x1 - x0) * i / 5;
    g += `<text x="${X(v)}" y="${H - 6}" font-size="11" fill="#8b91a1" text-anchor="middle">${xFmt(v)}</text>`;
  }
  const bandSvg = bands.map(b => `<rect x="${X(b.from)}" y="${T}" width="${Math.max(2, X(b.to) - X(b.from))}" height="${H - T - B}" fill="${b.color}" opacity=".18"/>`).join("");
  const lines = series.map(s => `<polyline fill="none" stroke="${esc(s.colour)}" stroke-width="${s.width || 2}" ${s.dash ? 'stroke-dasharray="5 4"' : ""}
     points="${s.points.map(p => X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1)).join(" ")}"/>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">${bandSvg}${g}${lines}
    ${yLabel ? `<text x="4" y="12" font-size="11" fill="#8b91a1">${esc(yLabel)}</text>` : ""}</svg>`;
}
function teammateDash(list) {   // second car of a team gets a dashed line
  const seen = {}; return list.map(s => { const d = seen[s.colour]; seen[s.colour] = true; return {...s, dash: !!d}; });
}

/* ---------- red flag ---------- */
const isRaceSession = d => /^(race|sprint)$/i.test((d && d.session) || "");
function redPanel(d) {
  const r = d.rc && d.rc.red_flag;
  if (!r || r.state === "closed" || airState() === "ended") return "";     // over and done with: not news any more
  const race = isRaceSession(d), Q = d.quali && d.quali.phase ? d.quali : null;
  // qualifying: name the segment that was stopped, not whichever one is running now
  const seg = Q && Q.label.startsWith("SQ") ? "SQ" : "Q";
  const what = race ? "RACE" : r.phase ? seg + r.phase : Q ? Q.label : "SESSION";
  if (r.state === "resumed") {
    const mins = Math.floor(r.duration_s / 60), secs = r.duration_s % 60;
    if ((vnow() - new Date(r.resumed_at)) > 15 * 60 * 1000) return "";
    return `<div class="card redpanel resumed"><div class="pad"><div class="big" style="color:var(--green)">${what} RESUMED</div>
      <div>${race ? `Restarted on lap ${r.resumed_lap ?? "-"}` : "Back under way"} after a ${mins} min ${secs} s stoppage${race && r.start_type ? " · " + esc(r.start_type) : ""}${
        Q ? ` · ${fmtClock(qRemaining(Q))} left in ${esc(Q.label)}` : ""}</div></div></div>`;
  }
  if (r.state === "abandoned") {
    return `<div class="card redpanel"><div class="pad"><div class="big">${what} WILL NOT BE RESUMED</div>
      <div>${race ? `Result is taken from before the red flag (lap ${r.lap}). Reduced points apply under 75% distance - see the title fight.`
                  : "The times set before the red flag stand."}</div></div></div>`;
  }
  const where = race ? `Stopped on lap ${r.lap ?? "-"}` : Q ? `${esc(Q.label)} clock stopped at <b style="color:var(--text)" class="num">${fmtClock(Q.remaining)}</b>` : "Session stopped";
  return `<div class="card redpanel"><div class="pad">
    <div style="display:flex;flex-wrap:wrap;gap:24px;align-items:flex-end;justify-content:space-between">
      <div><div class="big" style="color:#ff5050">RED FLAG · ${race ? "RACE" : "SESSION"} SUSPENDED</div>
        <div class="muted">${where} · suspended for <b style="color:var(--text)" class="num" data-since="${esc(r.since)}">${since(r.since)}</b></div></div>
      <div style="text-align:right">${r.resume_at
        ? `<div class="muted">Resumes at ${esc(r.resume_local)} local</div><div class="count num" data-countdown="${esc(r.resume_at)}">${countdown(r.resume_at)}</div>`
        : `<div class="muted">Restart time</div><div style="font-size:20px;font-weight:800">Not announced yet</div>`}</div>
    </div>
    <div class="stats" style="margin-top:10px">
      ${race ? `<div class="stat"><span>Restart</span><b style="font-size:16px">${esc(r.start_type || "TBC")}</b></div>` : ""}
      <div class="stat"><span>Pit exit</span><b style="font-size:16px">${esc(r.pit_exit || "closed")}</b></div>
      ${Q ? `<div class="stat"><span>Times set in ${esc(Q.label)}</span><b style="font-size:16px">${Q.with_time ?? 0} of ${Q.running_cars ?? "-"} cars</b></div>` : ""}
      ${Q && Q.wet != null ? `<div class="stat"><span>Track</span><b style="font-size:16px;color:${Q.wet ? "#4aa3ff" : "inherit"}">${Q.wet ? "Declared wet" : "Declared dry"}</b></div>` : ""}
      ${r.push_codes.length ? `<div class="stat"><span>"Push cars to front" (race control)</span><b style="font-size:16px">${r.push_codes.map(esc).join(" ")}</b></div>` : ""}
    </div>
    ${r.order_codes.length ? `<div style="margin-top:12px"><div class="muted" style="font-size:12px;text-transform:uppercase;letter-spacing:.8px">Restart order (official)</div>
      <div class="gridslots num">${r.order_codes.map((c, i) => {
        const row = (d.rows || []).find(x => x.code === c);
        return `<div style="border-color:${row ? esc(row.colour) : "#666"}">P${i + 1} ${esc(c)}</div>`; }).join("")}</div></div>` : ""}
    ${r.signals.length ? `<div style="margin-top:12px">${r.signals.slice(-5).reverse().map(s => `<div class="muted num" style="font-size:12px">${esc(s.time)} · ${esc(s.text)}</div>`).join("")}</div>` : ""}
  </div></div>`;
}
