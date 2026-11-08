/* F1 Hub dashboard - mini tower, layouts, lap compare, ghost car, tyre degradation.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- picture-in-picture mini tower ---------- */
let pipWin = null;
async function openPip() {
  if (pipWin && !pipWin.closed) { pipWin.focus && pipWin.focus(); return; }
  try {
    if ("documentPictureInPicture" in window) pipWin = await documentPictureInPicture.requestWindow({width: 260, height: 560});
    else pipWin = window.open("", "f1hub-mini", "width=260,height=560,popup=yes");
  } catch (e) { pipWin = window.open("", "f1hub-mini", "width=260,height=560,popup=yes"); }
  if (!pipWin) { toast("flags", "Pop-up blocked", "Allow pop-ups for this page to use the mini tower", "#8b91a1"); return; }
  pipWin.document.title = "F1 Hub mini";
  pipWin.document.body.style.cssText = "margin:0;background:#0d0e12;color:#e8eaf0;font:12px/1.25 system-ui,sans-serif";
  renderPip(D);
}
function renderPip(d) {
  if (!pipWin || pipWin.closed || !d || !d.rows) return;
  const st = {GREEN: "#2bd46b", SC: "#ffb000", VSC: "#ffb000", RED: "#e10600", CHEQUERED: "#fff"}[d.status] || "#8b91a1";
  const Q = d.quali && d.quali.phase ? d.quali : null;
  if (Q) {
    const qx = r => Q.rows[r.number] || {}, cut = Q.cut;
    pipWin.document.body.innerHTML = `<div style="display:flex;justify-content:space-between;padding:6px 8px;background:#15171e;border-bottom:2px solid ${st}">
      <b>${esc(Q.label)} ${fmtClock(qRemaining(Q))}</b><b style="color:${st}">${Q.suspended ? "STOPPED" : esc(d.status || "")}</b>
      ${d.settings && d.settings.delay ? `<span style="color:#3d8bff">-${d.settings.delay}s</span>` : ""}</div>
    <table style="width:100%;border-collapse:collapse">${d.rows.map((r, i) => { const x = qx(r);
      return `<tr style="${x.out_in ? "opacity:.4" : ""}${cut && i === cut ? ";border-top:2px dashed #e10600" : ""}">
      <td style="padding:2px 6px;text-align:right;width:18px;font-weight:800">${r.pos}</td>
      <td style="padding:2px 4px"><span style="display:inline-block;width:3px;height:11px;background:${esc(r.colour)};margin-right:5px;vertical-align:-1px"></span><b>${esc(r.code)}</b>${x.on_lap ? ' <span style="color:#2bd46b">●</span>' : ""}</td>
      <td style="padding:2px 6px;text-align:right;font-variant-numeric:tabular-nums;color:#aab">${x.out_in ? "Q" + x.out_in + " OUT" : x.gap == null ? (x.best ? "" : "-") : x.gap === 0 ? fmtLap(x.best) : "+" + x.gap.toFixed(3)}</td>
      <td style="padding:2px 6px"><span style="color:${TYRE[r.compound] || "#666"}">●</span></td></tr>`; }).join("")}</table>`;
    return;
  }
  pipWin.document.body.innerHTML = `<div style="display:flex;justify-content:space-between;padding:6px 8px;background:#15171e;border-bottom:2px solid ${st}">
      <b>L${d.lap ?? "-"}${d.scheduled_laps ? "/" + d.scheduled_laps : ""}</b><b style="color:${st}">${esc(d.status || "")}</b>
      ${d.settings && d.settings.delay ? `<span style="color:#3d8bff">-${d.settings.delay}s</span>` : ""}</div>
    <table style="width:100%;border-collapse:collapse">${d.rows.map(r => `<tr style="${r.out ? "opacity:.4" : ""}">
      <td style="padding:2px 6px;text-align:right;width:18px;font-weight:800">${r.out ? "-" : r.pos}</td>
      <td style="padding:2px 4px"><span style="display:inline-block;width:3px;height:11px;background:${esc(r.colour)};margin-right:5px;vertical-align:-1px"></span><b>${esc(r.code)}</b></td>
      <td style="padding:2px 6px;text-align:right;font-variant-numeric:tabular-nums;color:#aab">${r.out ? "OUT" : r.pos === 1 ? "" : fmtGap(r.interval)}</td>
      <td style="padding:2px 6px"><span style="color:${TYRE[r.compound] || "#666"}">●</span></td></tr>`).join("")}</table>`;
}

/* ---------- layout presets ---------- */
const LAYOUTS = {standard: "Standard", compact: "Compact", tv: "Large (TV)", tower: "Tower only"};
function setLayout(k) {
  Object.keys(LAYOUTS).forEach(x => document.body.classList.toggle("lay-" + x, x === k && k !== "standard"));
  store.set("layout", k);
  $("layouts").innerHTML = Object.entries(LAYOUTS).map(([x, label]) => `<button class="btn ${x === k ? "primary" : ""}" onclick="setLayout('${x}')">${label}</button>`).join("");
}
setLayout(store.get("layout", "standard"));


/* ---------- head-to-head lap compare (speed / throttle / brake / delta) + braking zones on the map ---------- */
const H2H = {a: null, b: null};
function fillH2HSelects(d) {
  const opts = sel => d.rows.map(r => `<option value="${r.number}" ${String(r.number) === sel ? "selected" : ""}>${esc(r.code)} · ${esc(r.name || "")}</option>`).join("");
  ["h2ha", "h2hb"].forEach((id, i) => {
    const el = $(id); if (document.activeElement === el && el.options.length) return;
    const cur = el.value || String((i === 0 ? (focus || d.rows[0]) : d.rows[1] || d.rows[0]).number || "");
    el.innerHTML = opts(cur);
  });
}
async function fetchLap(driver, lap) {
  const r = await fetch(`/api/lap?driver=${driver}&lap=${lap}&before=${encodeURIComponent(vnow().toISOString())}` + (D && D.mode === "replay" ? `&session=${D.session_key}` : ""));
  if (r.status === 404) throw new Error("restart");
  return r.json();
}
async function loadH2H() {
  $("h2hnote").textContent = "Loading laps…"; $("h2hbody").innerHTML = "";
  try {
    const [a, b] = await Promise.all([fetchLap($("h2ha").value, $("h2hal").value), fetchLap($("h2hb").value, $("h2hbl").value)]);
    if (a.error || b.error || !a.samples || !b.samples) { $("h2hnote").textContent = a.error || b.error || "No lap data."; return; }
    H2H.a = a; H2H.b = b; $("h2hnote").textContent = ""; renderH2H();
  } catch (e) {
    $("h2hnote").textContent = e.message === "restart" ? "Lap compare switches on after the next F1 Hub server restart." : "Couldn't load: " + e.message;
  }
}
function renderH2H() {
  const {a, b} = H2H; if (!a || !b || !D) return;
  const row = n => D.rows.find(r => r.number === n) || {code: "#" + n, colour: "#fff"};
  const ra = row(a.driver), rb = row(b.driver);
  const colB = rb.colour === ra.colour ? "#ffffff" : rb.colour;
  const pts = (tr, i) => tr.samples.map(s => [s[1] * 100, s[i]]);
  // time delta along the lap: where is B relative to A at the same distance?
  const tAt = (tr, f) => { const s = tr.samples; let j = s.findIndex(x => x[1] >= f);
    if (j < 0) return s[s.length - 1][0]; if (j === 0) return s[0][0];
    const p = s[j - 1], q = s[j]; return p[0] + (q[0] - p[0]) * ((f - p[1]) / (q[1] - p[1] || 1)); };
  // positions are sampled ~4x a second, so pin the line: 0 at the start, the real lap-time difference at the finish
  const raw = [...Array(101).keys()].map(k => tAt(b, k / 100) - tAt(a, k / 100));
  const drift = raw[100] - raw[0] - (b.duration - a.duration);
  const delta = raw.map((v, k) => [k, +(v - raw[0] - drift * k / 100).toFixed(3)]);
  const fmt = v => v.toFixed(0) + "%";
  const head = (r, t, c) => `<span class="drv"><span class="stripe" style="background:${esc(c)}"></span><b>${esc(r.code)}</b></span> lap ${t.lap} · <b class="num">${fmtLap(t.duration)}</b>
    <span class="muted num">(${t.sectors.map(x => x ? x.toFixed(3) : "-").join(" / ")})</span>`;
  const d = b.duration - a.duration;
  $("h2hbody").innerHTML = `<div class="pad">${head(ra, a, ra.colour)} &nbsp; vs &nbsp; ${head(rb, b, colB)} &nbsp;
      <b style="color:${d > 0 ? ra.colour : colB}">${esc(d > 0 ? ra.code : rb.code)} faster by ${Math.abs(d).toFixed(3)}s</b></div>
    <div class="two"><div>
      <h2>Speed (km/h)</h2>${lineChart([{colour: ra.colour, points: pts(a, 2)}, {colour: colB, points: pts(b, 2), dash: true}], {H: 220, xFmt: fmt})}
      <h2>Gap along the lap <span class="sub">above 0 = ${esc(rb.code)} behind</span></h2>
      ${lineChart([{colour: "#8b91a1", points: [[0, 0], [100, 0]], width: 1}, {colour: colB, points: delta}], {H: 160, xFmt: fmt, yFmt: v => v.toFixed(2) + "s"})}
      <h2>Throttle % / brake</h2>${lineChart([{colour: ra.colour, points: pts(a, 3)}, {colour: colB, points: pts(b, 3), dash: true},
        {colour: "#e10600", points: pts(a, 4).map(p => [p[0], p[1] * -20]), width: 2}, {colour: "#ff9090", points: pts(b, 4).map(p => [p[0], p[1] * -40]), width: 2}],
        {H: 160, xFmt: fmt, yMin: -45, yMax: 100})}
      <div class="muted" style="font-size:12px">Red bars under the throttle: braking (dark ${esc(ra.code)}, light ${esc(rb.code)}).</div>
    </div><div><h2>Braking zones - ${esc(ra.code)} (red) vs ${esc(rb.code)} (white)</h2>${brakeMap(a, b, ra.colour, colB)}</div></div>`;
}
function brakeMap(a, b, ca, cb) {
  const m = D.map || {}; const outline = m.outline || [];
  const base = outline.length ? outline : a.samples.map(s => [s[6], s[7]]);
  const th = (m.rotation || 0) * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th), W = 520;
  const rot = (x, y) => [x * cs - y * sn, x * sn + y * cs], R = base.map(p => rot(p[0], p[1]));
  const minX = Math.min(...R.map(p => p[0])), maxX = Math.max(...R.map(p => p[0])), minY = Math.min(...R.map(p => p[1])), maxY = Math.max(...R.map(p => p[1]));
  const pad = 16, k = (W - 2 * pad) / (maxX - minX || 1), H = Math.round((maxY - minY) * k + 2 * pad);
  const P = (x, y) => { const [u, v] = rot(x, y); return [(pad + (u - minX) * k).toFixed(1), (H - (pad + (v - minY) * k)).toFixed(1)]; };
  const path = arr => arr.map((p, i) => (i ? "L" : "M") + P(p[0], p[1]).join(",")).join("");
  const zones = (tr, col, w, off) => { let out = "", seg = [];
    tr.samples.forEach(s => { if (s[4]) seg.push([s[6] + off, s[7] + off]); else if (seg.length) { out += `<path d="${path(seg)}" stroke="${col}" stroke-width="${w}" fill="none" stroke-linecap="round"/>`; seg = []; } });
    if (seg.length) out += `<path d="${path(seg)}" stroke="${col}" stroke-width="${w}" fill="none" stroke-linecap="round"/>`; return out; };
  return `<svg viewBox="0 0 ${W} ${H}" width="100%"><path d="${path(base)}Z" fill="none" stroke="#2c303b" stroke-width="10" stroke-linejoin="round"/>
    ${zones(a, "#e10600", 7, 0)}${zones(b, "#ffffff", 3, 0)}</svg>`;
}


/* ---------- ghost car: the session's fastest lap, replayed against your selected driver's current lap ---------- */
const GHOST = {on: false, trace: null, key: null, loading: false};
async function toggleGhost(on) {
  GHOST.on = on;
  if (!on) { GHOST.trace = null; $("ghostinfo").textContent = ""; document.querySelectorAll(".ghost").forEach(e => e.remove()); return; }
  refreshGhost(true);
}
async function refreshGhost(force) {
  if (!GHOST.on || !D || GHOST.loading) return;
  const fl = D.rows.find(r => r.fastest && r.best_lap);
  if (!fl) { $("ghostinfo").textContent = "No timed lap yet."; return; }
  const key = fl.number + ":" + fl.best_lap;
  if (!force && key === GHOST.key) return;
  GHOST.loading = true;
  try {
    const t = await fetchLap(fl.number, "best");
    if (t.error || !t.samples) $("ghostinfo").textContent = t.error || "No lap data.";
    else { GHOST.trace = t; GHOST.key = key; GHOST.code = fl.code; }
  } catch (e) { $("ghostinfo").textContent = e.message === "restart" ? "The ghost switches on after the next F1 Hub server restart." : "Couldn't load the ghost lap."; GHOST.on = false; $("ghoston").checked = false; }
  GHOST.loading = false;
}
function ghostAt(t) {    // position of the ghost t seconds into its lap
  const s = GHOST.trace && GHOST.trace.samples; if (!s || !s.length) return null;
  if (t <= s[0][0]) return s[0]; if (t >= s[s.length - 1][0]) return null;
  let lo = 0, hi = s.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (s[m][0] <= t) lo = m; else hi = m; }
  const a = s[lo], b = s[hi], f = (t - a[0]) / (b[0] - a[0] || 1);
  return [t, a[1] + (b[1] - a[1]) * f, 0, 0, 0, 0, a[6] + (b[6] - a[6]) * f, a[7] + (b[7] - a[7]) * f];
}
function drawGhost(svg, P, T) {
  let g = svg.querySelector(".ghost");
  const me = D && focus ? D.rows.find(r => r.number === focus) : null;
  if (!GHOST.on || !GHOST.trace || !me || !me.lap_start) {
    if (g) g.remove();
    const hint = `Ghost ready: <b>${esc(GHOST.code || "")}</b>'s fastest lap. Click a car on the map to race it.`;
    if (GHOST.on && GHOST.trace && !me && $("ghostinfo").innerHTML !== hint) $("ghostinfo").innerHTML = hint;
    return;
  }
  const t = (T - new Date(me.lap_start).getTime()) / 1000, p = ghostAt(t);
  if (!p) { if (g) g.remove(); return; }
  if (!g) { svg.insertAdjacentHTML("beforeend", `<g class="ghost"><circle class="gh-halo" r="13"/><circle r="7.5" fill="#c56cff" fill-opacity=".35" stroke="#e0a8ff" stroke-width="2" stroke-dasharray="3 2"/><text font-size="10" dy="-14" text-anchor="middle">BEST</text></g>`); g = svg.querySelector(".ghost"); }
  const [x, y] = P(p[6], p[7]);
  g.setAttribute("transform", `translate(${x.toFixed(1)},${y.toFixed(1)})`);
  // how far ahead is the ghost? compare lap fraction with the selected car's own progress
  const mine = carAt(me.number, T);
  if (mine && Math.random() < 0.05) {
    let best = 0, bd = 1e18; GHOST.trace.samples.forEach(s => { const dd = (s[6] - mine[1]) ** 2 + (s[7] - mine[2]) ** 2; if (dd < bd) { bd = dd; best = s[0]; } });
    const gap = t - best;
    $("ghostinfo").innerHTML = `Ghost: <b>${esc(GHOST.code)}</b>'s fastest lap (${fmtLap(GHOST.trace.duration)}) · <b>${esc(me.code)}</b> is ${gap >= 0 ? "<b style='color:#ff6b6b'>" + gap.toFixed(1) + "s down</b>" : "<b style='color:#2bd46b'>" + (-gap).toFixed(1) + "s up</b>"} on it this lap`;
  }
}

/* ---------- tyre degradation per driver (current stint) ---------- */
function renderTyreDeg(d) {
  const rows = d.rows.filter(r => !r.out && r.stint_laps && r.stint_laps.length >= 4);
  if (!d.rows.some(r => "stint_laps" in r)) { $("tyredeg").innerHTML = '<div class="muted">Switches on after the next F1 Hub server restart.</div>'; return; }
  if (!rows.length) { $("tyredeg").innerHTML = '<div class="muted">Needs 4+ clean laps on the current tyres.</div>'; return; }
  const fit = pts => {   // least squares, ignoring slow laps (traffic, SC) more than 3% off the stint median
    const med = pts.map(p => p[1]).sort((a, b) => a - b)[Math.floor(pts.length / 2)];
    const c = pts.filter(p => p[1] < med * 1.03); if (c.length < 3) return null;
    const n = c.length, sx = c.reduce((a, p) => a + p[0], 0), sy = c.reduce((a, p) => a + p[1], 0);
    const sxx = c.reduce((a, p) => a + p[0] * p[0], 0), sxy = c.reduce((a, p) => a + p[0] * p[1], 0);
    const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx || 1); return {slope, n, clean: c};
  };
  const list = rows.map(r => ({r, f: fit(r.stint_laps)})).filter(x => x.f).sort((a, b) => b.f.slope - a.f.slope);
  const spark = pts => { const ys = pts.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys) || lo + 1, xs = pts.map(p => p[0]), x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
    return `<svg width="110" height="22"><polyline fill="none" stroke="#8b91a1" stroke-width="1.5" points="${pts.map(p => ((p[0] - x0) / (x1 - x0 || 1) * 106 + 2).toFixed(1) + "," + (20 - (p[1] - lo) / (hi - lo || 1) * 18).toFixed(1)).join(" ")}"/></svg>`; };
  $("tyredeg").innerHTML = `<table class="num"><thead><tr><th>Driver</th><th>Tyre</th><th class="r">Age</th><th class="r">Deg / lap</th><th>Lap times</th></tr></thead><tbody>${list.map(({r, f}) => `<tr>
      <td><span class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)}</span></td>
      <td><span style="color:${TYRE[r.compound] || "#888"}">●</span> ${esc((r.compound || "?").toLowerCase())}</td><td class="r">${r.tyre_age ?? "-"}</td>
      <td class="r" style="color:${f.slope > 0.08 ? "#ff6b6b" : f.slope > 0.03 ? "#ffb000" : "#2bd46b"}"><b>${f.slope >= 0 ? "+" : ""}${f.slope.toFixed(3)}s</b></td>
      <td>${spark(f.clean)}</td></tr>`).join("")}</tbody></table>
    <div class="muted" style="font-size:12px;margin-top:6px">Fuel burn makes cars ~0.03-0.06 s/lap faster, so a flat line still means the tyres are wearing.</div>`;
}
