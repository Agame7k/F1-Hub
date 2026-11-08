/* F1 Hub dashboard - race moments, drawn on the track map and across the page.
   On the 2D map: a short tail behind each car and a ring in its tyre colour, a line between cars fighting within a
   second, a flash when a car takes a place, pit stops (in the lane, then the time stationary), a sector lighting up
   purple, the selected car's last lap coloured by speed / throttle / brake, a readout beside it, and the gap to the
   ghost drawn along the track. Across the page: the start lights, safety car / VSC / red flag, fastest lap, final
   lap and the chequered flag.
   Things seen on the map run on the map's clock (a few seconds behind the timing, see MAP_LAG); the rest runs on the
   frame being shown. Either way a delay or a replay never shows a moment early.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const FX = {
  seek: undefined, sess: null, prev: null,
  flashes: [],          // {n, text, t0}: a car that just took a place
  sectors: [],          // {k, code, t0}: a purple sector
  lapStarts: {},        // number -> recent lap start times (epoch ms)
  bounds: {1: [], 2: []},   // outline indices where cars were when S1 / S2 ended (learned, the median is used)
  pit: {},              // number -> pit-stop state on the map's clock
  lights: {phase: "idle"}, lastT: 0,
  lap: {key: null, trace: null, loading: false},
  mode: store.get("mapcolour", "off"),
  banner: null, chequer: null,
};
const PURPLE = "#b45cff";
const LIM = [150, 240];            // location units/s at the pit-lane limiter (~60-100 km/h); 1 unit/s ~ 0.406 km/h
const fxNow = () => performance.now();

/* ---------- small helpers on the position history (TRAIL, from map.js) ---------- */
function covered(n, T, back = 1000) {
  const t = TRAIL[n];
  return t && t.length > 1 && T - back >= t[0][0] && T <= t[t.length - 1][0];
}
function speedAt(n, T, win = 1000) {   // location units per second over the last `win` ms, or null
  if (!covered(n, T, win)) return null;
  const a = carAt(n, T - win), b = carAt(n, T);
  return Math.hypot(b[1] - a[1], b[2] - a[2]) / (win / 1000);
}
function nearIdx(o, x, y) {
  let best = 0, bd = Infinity;
  for (let i = 0; i < o.length; i++) { const d = (o[i][0] - x) ** 2 + (o[i][1] - y) ** 2; if (d < bd) { bd = d; best = i; } }
  return [best, Math.sqrt(bd)];
}
function offTrack(o, x, y) {    // distance from the track's centre line (location units)
  let best = Infinity;
  for (let i = 0; i < o.length; i++) {
    const a = o[i], b = o[(i + 1) % o.length], dx = b[0] - a[0], dy = b[1] - a[1];
    const f = clamp(((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy || 1), 0, 1);
    best = Math.min(best, Math.hypot(a[0] + dx * f - x, a[1] + dy * f - y));
  }
  return best;
}
const median = a => { if (!a.length) return null; const s = a.slice().sort((p, q) => p - q); return s[s.length >> 1]; };
function fxReset() {
  FX.prev = null; FX.flashes = []; FX.sectors = []; FX.pit = {}; FX.lapStarts = {}; FX.lights = {phase: "idle"};
  FX.chequer = null; FX.lap = {key: null, trace: null, loading: false};
}

/* ---------- per frame of data: places gained, pit counts, purple sectors, sector lines, banners ---------- */
function raceMoments(d) {
  if (!d || !d.rows) return;
  if (d.session_key !== FX.sess) { FX.sess = d.session_key; FX.bounds = {1: [], 2: []}; fxReset(); }
  if (d.seek !== FX.seek) { FX.seek = d.seek; fxReset(); }
  const race = isRaceSession(d) && !d.quali, vt = vnow().getTime();
  const prev = FX.prev, cur = {rows: {}, status: d.status, lap: d.lap, fastest: null};
  const fl = d.rows.find(r => r.fastest);
  if (fl) cur.fastest = {n: fl.number, t: fl.best_lap};
  d.rows.forEach(r => {
    const p = prev && prev.rows[r.number];
    const sig = (r.sectors || []).map(s => s && s.t).join();
    cur.rows[r.number] = {pos: r.pos, out: !!r.out, pits: r.pits || 0, sig, pitAt: p ? p.pitAt : 0};
    if (p && (r.pits || 0) > p.pits) cur.rows[r.number].pitAt = vt;
    // lap starts, so a finished lap's sector times can be placed on the track
    if (r.lap_start) {
      const ls = Date.parse(r.lap_start), arr = FX.lapStarts[r.number] || (FX.lapStarts[r.number] = []);
      if (!arr.includes(ls)) { arr.push(ls); if (arr.length > 5) arr.shift(); }
    }
    if (p && sig && sig !== p.sig) newLap(d, r);
  });
  if (prev) {
    const moved = d.rows.filter(r => r.pos && prev.rows[r.number] && prev.rows[r.number].pos !== r.pos).length;
    if (race && moved && moved <= 8 && d.status !== "RED") placesGained(d, prev, cur, vt);
    if (race) banners(d, prev, cur);
  }
  FX.prev = cur;
  if (d.status === "CHEQUERED" && prev && prev.status !== "CHEQUERED") FX.chequer = fxNow();
}

function placesGained(d, prev, cur, vt) {
  d.rows.forEach(g => {
    const was = prev.rows[g.number];
    if (!g.pos || !was || !was.pos || g.pos >= was.pos || g.out) return;
    // the cars it got ahead of - not ones that pitted, retired or are sitting in the pit lane
    const passed = d.rows.filter(r => {
      const p = prev.rows[r.number], c = cur.rows[r.number];
      return p && p.pos && p.pos < was.pos && p.pos >= g.pos && r.pos && r.pos > g.pos && !r.out
        && !(c.pitAt && vt - c.pitAt < 45000) && !(FX.pit[r.number] && FX.pit[r.number].phase);
    });
    if (passed.length) FX.flashes.push({n: g.number, text: `+${passed.length}`, t0: fxNow()});
  });
}

function newLap(d, r) {
  const s = r.sectors.map(x => x && x.t);
  if (s.some(v => !v)) return;
  const total = (s[0] + s[1] + s[2]) * 1000, starts = FX.lapStarts[r.number] || [];
  const a = starts.find(x => starts.some(y => Math.abs(x + total - y) < 1500));
  const o = d.map && d.map.outline;
  if (a != null && o && o.length > 50) [1, 2].forEach(k => {
    const tb = a + (k === 1 ? s[0] : s[0] + s[1]) * 1000;
    if (!covered(r.number, tb, 0)) return;
    const p = carAt(r.number, tb), [i, dist] = nearIdx(o, p[1], p[2]);
    if (dist < 400) { FX.bounds[k].push(i); if (FX.bounds[k].length > 15) FX.bounds[k].shift(); }
  });
  if (!isRaceSession(d) && !d.quali) return;     // practice: too many to be news
  r.sectors.forEach((x, i) => { if (x && x.c === "purple") FX.sectors.push({k: i + 1, code: r.code, t0: fxNow()}); });
}

/* ---------- broadcast-style banners ---------- */
function showBanner(cls, html, ms = 4200) {
  let el = $("fxbanner");
  if (!el) { el = document.createElement("div"); el.id = "fxbanner"; el.setAttribute("role", "status"); document.body.appendChild(el); }
  el.className = ""; void el.offsetWidth;
  el.className = "on " + cls; el.innerHTML = html;
  clearTimeout(FX.banner); FX.banner = setTimeout(() => el.className = "", ms);
}
function banners(d, prev, cur) {
  if (d.status !== prev.status) {
    const b = {SC: ["sc", "SAFETY CAR", "Deployed"], VSC: ["sc", "VIRTUAL SAFETY CAR", "Deployed"], RED: ["red", "RED FLAG", "Session suspended"]}[d.status];
    if (b) showBanner(b[0], `<b>${b[1]}</b><span>${b[2]}</span>`);
    else if (d.status === "GREEN" && ["SC", "VSC", "RED"].includes(prev.status)) showBanner("green", `<b>GREEN FLAG</b><span>Racing resumes</span>`);
  }
  if (d.scheduled_laps && d.lap === d.scheduled_laps && prev.lap < d.scheduled_laps && d.status !== "CHEQUERED")
    showBanner("final", `<b>FINAL LAP</b><span>Lap ${d.lap} of ${d.scheduled_laps}</span>`, 6000);
  const f = cur.fastest, pf = prev.fastest;
  if (f && d.lap > 1 && (!pf || f.n !== pf.n || f.t < pf.t)) {
    const r = d.rows.find(x => x.number === f.n);
    if (r) showBanner("fl", `<b>FASTEST LAP</b><span><i style="background:${esc(r.colour)}"></i>${esc(r.code)} <em class="num">${fmtLap(f.t)}</em></span>`);
  }
}

/* ---------- on the map's clock: the start lights and pit stops (~10 times a second, any tab) ---------- */
function fxTick() {
  if (!D || !D.map || document.hidden) return;
  const T = vnow().getTime() - MAP_LAG;
  if (T < FX.lastT - 3000 || T > FX.lastT + 15000) {          // a jump in time: start over (test-mode stops stay)
    FX.pit = Object.fromEntries(Object.entries(FX.pit).filter(([, st]) => st.demo)); FX.lights = {phase: "idle"};
  }
  FX.lastT = T;
  const cars = (D.map.cars || []).map(c => c.number);
  safe("start lights", () => startLights(cars, T));
  if (D.status !== "RED") safe("pit stops", () => cars.forEach(n => pitState(n, T)));
}
setInterval(fxTick, 100);

// Cars stop on the grid, then all launch together. The lights come on a second apart once the field is still and
// go out the moment it moves (the buffer lets us see a few seconds ahead, so the last light lands just before).
// A field that sat still for over a minute is the pre-race grid before the formation lap, not a start.
function startLights(cars, T) {
  const L = FX.lights, el = lightsEl();
  if (FX.demoLights) {           // test mode: five lights a second apart, a short hold, then out
    const e = fxNow() - FX.demoLights.t0, out = FX.demoLights.out;
    if (e > out + 2600) { FX.demoLights = null; el.className = ""; return; }
    if (e >= out) { el.className = "on out"; return; }
    const n = clamp(Math.floor(e / 1000) + 1, 1, 5);
    el.className = "on"; el.querySelectorAll("i").forEach((x, i) => x.classList.toggle("lit", i < n));
    return;
  }
  if (!isRaceSession(D) || D.quali) { el.className = ""; return; }
  const frac = (t, test) => { const v = cars.map(n => speedAt(n, t, 700)).filter(x => x != null); return v.length >= 10 ? v.filter(test).length / v.length : null; };
  const still = frac(T, v => v < 15), moving = frac(T, v => v > 60);
  if (L.phase === "idle") {
    // the field has to have driven onto the grid just now (a page opened onto cars already parked is no start)
    const arrived = [8, 14, 20, 28, 36].some(s => (frac(T - s * 1000, v => v > 60) || 0) >= 0.5);
    if (still != null && still >= 0.95) Object.assign(L, {phase: arrived ? "grid" : "parked", since: T, go: null, left: null});
  } else if (L.phase === "parked") {
    if (still == null || still < 0.6) L.phase = "idle";
  } else if (L.phase === "grid") {
    if (L.go == null) for (let dt = 250; dt <= MAP_LAG - 300; dt += 250) {        // look ahead for the launch
      const m = frac(T + dt, v => v > 60);
      if (m == null) break;
      if (m >= 0.5) { L.go = T + dt - 600; break; }       // (less the averaging window: when they actually went)
    }
    if ((L.go && T >= L.go) || (moving != null && moving >= 0.5)) {
      if (T - L.since < 60000) Object.assign(L, {phase: "out", at: T}); else L.phase = "idle";
    } else if (still != null && still < 0.6) {      // pulling away: a launch is through 60 units/s within a second or so
      L.left = L.left || T;
      if (T - L.left > 3000) L.phase = "idle";      // rolled off slowly: the formation lap
    } else L.left = null;
  } else if (L.phase === "out" && T - L.at > 2600) L.phase = "idle";
  let n = 0;
  if (L.phase === "grid" && T - L.since < 60000) {
    n = Math.floor((T - L.since - 3000) / 1000) + 1;
    if (L.go) n = Math.max(n, 5 - Math.floor((L.go - 1200 - T) / 1000));
    n = clamp(n, 0, 5);
  }
  if (L.phase === "out") { el.className = "on out"; return; }
  el.className = n ? "on" : "";
  el.querySelectorAll("i").forEach((x, i) => x.classList.toggle("lit", i < n));
}
function lightsEl() {
  let el = $("fxlights");
  if (!el) {
    el = document.createElement("div"); el.id = "fxlights"; el.setAttribute("aria-hidden", "true");
    el.innerHTML = `<div class="rig">${"<i></i>".repeat(5)}</div><b>LIGHTS OUT</b>`;
    document.body.appendChild(el);
  }
  return el;
}

// Pit lane: held at limiter speed. Then stationary (the stop), then away. Measured from position, so it's a
// close guess; the official stationary time replaces it once OpenF1 publishes the stop.
function pitState(n, T) {
  const st = FX.pit[n] || (FX.pit[n] = {phase: null, lim: null});
  if (st.demo) { demoPit(n, st, T); return; }
  const v = speedAt(n, T, 500), v1 = speedAt(n, T);
  if (v == null || v1 == null) return;
  if (!st.phase) {
    // at the limiter for a while, and off to the side of the track (formation laps and SC queues run at that speed too)
    if (v1 >= LIM[0] && v1 <= LIM[1]) {
      st.lim = st.lim || T;
      const o = D.map.outline || [], p = carAt(n, T);
      if (T - st.lim > 2500 && o.length > 50 && offTrack(o, p[1], p[2]) > 120) Object.assign(st, {phase: "lane", since: T, stop: null, go: null});
    }
    else st.lim = null;
    return;
  }
  if (st.phase === "lane") {
    if (v < 20) Object.assign(st, {phase: "stop", stop: T - 450});      // (less the averaging window)
    else if (v1 > 300) { st.fast = st.fast || T; if (T - st.fast > 1500) { st.phase = null; st.lim = null; } }   // drive-through / not the pit lane
    else st.fast = null;
    if (T - st.since > 90000) st.phase = null;
  } else if (st.phase === "stop") {
    if (v > 40) Object.assign(st, {phase: "away", go: T - 100, fast: null});
    else if (T - st.stop > 45000) st.phase = "garage";
  } else if (st.phase === "away" || st.phase === "garage") {
    if (v1 > 300) { st.fast = st.fast || T; if (T - st.fast > 1500) Object.assign(st, {phase: st.phase === "away" ? "out" : null, out: T}); }
    else st.fast = null;
  } else if (st.phase === "out" && T - st.out > 7000) { st.phase = null; st.lim = null; }
}
function demoPit(n, st, T) {
  const e = fxNow() - st.demo;
  if (e < 2000) st.phase = "lane";
  else if (e < 4400) Object.assign(st, {phase: "stop", stop: T - (e - 2000)});
  else if (e < 7000) Object.assign(st, {phase: "away", stop: 0, go: 2400});
  else if (e < 12000) st.phase = "out";
  else delete FX.pit[n];
}
function pitLabel(n, st, T) {
  const row = D.rows.find(r => r.number === n) || {};
  if (st.phase === "lane") return {text: "PIT LANE", cls: "lane"};
  if (st.phase === "garage") return {text: "IN GARAGE", cls: "lane"};
  if (st.phase === "stop") return {text: (Math.max(0, st.demo ? fxNow() - st.demo - 2000 : T - st.stop) / 1000).toFixed(1), cls: "stop"};
  const off = st.demo ? null : (D.pits || []).find(p => p.number === n && p.stop != null && Math.abs((p.lap || 0) - (row.lap || 0)) <= 1);
  const secs = off ? off.stop : (st.go - st.stop) / 1000, tyre = row.compound && row.compound !== "UNKNOWN" ? row.compound : null;
  return {text: `${off ? "" : "~"}${secs.toFixed(1)}s`, cls: "done", tyre};
}

/* ---------- the 2D map, every animation frame (called from animateMaps) ---------- */
function mapFx(svg, P, T) {
  let under = svg.querySelector(".fx-under"), over = svg.querySelector(".fx-over"), lapG = svg.querySelector(".fx-lap");
  if (!under) {
    const cars = svg.querySelector(".t-cars");
    cars.insertAdjacentHTML("beforebegin", `<g class="fx-lap"></g><g class="fx-under"></g>`);
    cars.insertAdjacentHTML("afterend", `<g class="fx-over"></g>`);
    under = svg.querySelector(".fx-under"); over = svg.querySelector(".fx-over"); lapG = svg.querySelector(".fx-lap");
  }
  const m = D.map || {}, o = m.outline || [], rows = {}, now = fxNow(), calm = REDUCED.matches;
  D.rows.forEach(r => rows[r.number] = r);
  const f1 = v => v.toFixed(1), at = {};
  (m.cars || []).forEach(c => { const p = carAt(c.number, T); if (p) at[c.number] = P(p[1], p[2]); });
  const line = (idx) => idx.map((i, k) => (k ? "L" : "M") + P(o[i][0], o[i][1]).map(f1).join(",")).join("");
  const span = (a, b) => { const out = []; for (let i = a; i !== b; i = (i + 1) % o.length) { out.push(i); if (out.length > o.length) break; } out.push(b); return out; };
  let u = "", v = "";

  // selected car's last lap, coloured
  lapLayer(lapG, P);

  // the gap to the ghost, along the track: green = ahead of it, red = behind
  const me = focus && rows[focus];
  if (typeof GHOST !== "undefined" && GHOST.on && GHOST.trace && me && me.lap_start && o.length > 50 && at[focus]) {
    const t = (T - Date.parse(me.lap_start)) / 1000, gp = ghostAt(t), mine = carAt(focus, T);
    if (gp && mine) {
      const [ci] = nearIdx(o, mine[1], mine[2]), [gi] = nearIdx(o, gp[6], gp[7]);
      const fwd = (gi - ci + o.length) % o.length, ahead = fwd > o.length / 2;
      const seg = ahead ? span(gi, ci) : span(ci, gi);
      if (seg.length > 1 && seg.length < o.length / 3)
        u += `<path class="fx-gh ${ahead ? "up" : "down"}" d="${line(seg)}"/>`;
    }
  }

  // purple sectors
  const b1 = median(FX.bounds[1]), b2 = median(FX.bounds[2]);
  FX.sectors = FX.sectors.filter(s => now - s.t0 < 3800);
  if (o.length > 50) FX.sectors.forEach(s => {
    const B1 = b1 ?? (s.demo ? Math.round(o.length / 3) : null), B2 = b2 ?? (s.demo ? Math.round(o.length * 2 / 3) : null);
    const lo = s.k === 1 ? 0 : s.k === 2 ? B1 : B2, hi = s.k === 1 ? B1 : s.k === 2 ? B2 : 0;
    if (lo == null || hi == null || (s.k === 2 && B2 <= B1)) return;
    const seg = span(lo, hi), age = (now - s.t0) / 3800, mid = P(...o[seg[seg.length >> 1]]);
    u += `<path class="fx-sec" d="${line(seg)}" style="opacity:${f1(1 - age * age)}"/>`;
    v += `<g class="fx-seclab" transform="translate(${f1(mid[0])},${f1(mid[1])})" style="opacity:${f1(1 - age * age)}"><rect x="-34" y="-30" width="68" height="18" rx="3"/>
      <text y="-17" text-anchor="middle">S${s.k} · ${esc(s.code)}</text></g>`;
  });

  // tails and tyre rings
  (m.cars || []).forEach(c => {
    const p = at[c.number]; if (!p) return;
    const r = rows[c.number] || {}, col = esc(c.colour), big = c.number === focus;
    if (!calm) {
      const pts = [p];
      for (let k = 1; k <= 12; k++) { const q = carAt(c.number, T - k * 110); if (q) pts.push(P(q[1], q[2])); }
      for (let s = 0; s < 3; s++) {
        const part = pts.slice(s * 4, s * 4 + 5);
        if (part.length > 1 && Math.hypot(part[0][0] - part[part.length - 1][0], part[0][1] - part[part.length - 1][1]) > 1.5)
          u += `<path class="fx-tail" d="M${part.map(q => q.map(f1).join(",")).join("L")}" stroke="${col}" style="opacity:${[.55, .3, .12][s]}"/>`;
      }
    }
    const tc = TYRE[r.compound] || null;
    if (tc && r.compound !== "UNKNOWN") u += `<circle class="fx-tyre" cx="${f1(p[0])}" cy="${f1(p[1])}" r="${big ? 11.6 : 9.6}" stroke="${tc}"><title>${esc(r.compound.toLowerCase())}${r.tyre_age != null ? ", " + r.tyre_age + " laps old" : ""}</title></circle>`;
  });

  // battles: cars within a second of the car ahead (racing only)
  if (isRaceSession(D) && !D.quali && !["SC", "VSC", "RED", "CHEQUERED"].includes(D.status) && D.lap > 1) {
    const order = D.rows.filter(r => r.pos && !r.out);
    for (let i = 1; i < order.length; i++) {
      const a = order[i - 1], b = order[i], pa = at[a.number], pb = at[b.number], iv = b.interval;
      if (typeof iv !== "number" || iv >= 1 || !pa || !pb) continue;
      if ((FX.pit[a.number] || {}).phase || (FX.pit[b.number] || {}).phase) continue;
      const dist = Math.hypot(pa[0] - pb[0], pa[1] - pb[1]);
      if (dist > 120 || dist < 4) continue;
      battle(a, b, pa, pb, iv);
    }
  }
  const db = FX.demoBattle;
  if (db && now < db.until && at[db.a] && at[db.b] && rows[db.a] && rows[db.b]) battle(rows[db.a], rows[db.b], at[db.a], at[db.b], 0.4);
  function battle(a, b, pa, pb, iv) {
    const mx = (pa[0] + pb[0]) / 2, my = (pa[1] + pb[1]) / 2;
    u += `<line class="fx-battle" x1="${f1(pa[0])}" y1="${f1(pa[1])}" x2="${f1(pb[0])}" y2="${f1(pb[1])}"/>`;
    v += `<g class="fx-gap" transform="translate(${f1(mx)},${f1(my)})"><rect x="-17" y="-8" width="34" height="16" rx="8"/><text y="4" text-anchor="middle">${iv.toFixed(1)}</text>
      <title>${esc(b.code)} is ${iv.toFixed(3)} s behind ${esc(a.code)}</title></g>`;
  }

  // places gained
  FX.flashes = FX.flashes.filter(f => now - f.t0 < 2600);
  FX.flashes.forEach(f => {
    const p = at[f.n], r = rows[f.n]; if (!p || !r) return;
    const age = (now - f.t0) / 2600;
    v += `<g class="fx-gain" transform="translate(${f1(p[0])},${f1(p[1])})">
      ${calm ? "" : `<circle r="${f1(10 + age * 26)}" stroke="${esc(r.colour)}" style="opacity:${f1(1 - age)}"/>`}
      <text y="${f1(-16 - age * 14)}" text-anchor="middle" style="opacity:${f1(Math.min(1, (1 - age) * 2.5))}">${esc(f.text)} P${r.pos}</text></g>`;
  });

  // pit stops
  Object.entries(FX.pit).forEach(([n, st]) => {
    if (!st.phase || !at[n]) return;
    const p = at[n], lab = pitLabel(+n, st, T), w = Math.max(40, lab.text.length * 7.2 + 14 + (lab.tyre ? 18 : 0));
    v += `<g class="fx-pit ${lab.cls}" transform="translate(${f1(p[0])},${f1(p[1] + 14)})"><rect x="${f1(-w / 2)}" y="0" width="${f1(w)}" height="18" rx="3"/>
      <text x="${lab.tyre ? -8 : 0}" y="13" text-anchor="middle">${esc(lab.text)}</text>
      ${lab.tyre ? `<circle cx="${f1(w / 2 - 11)}" cy="9" r="5.5" fill="none" stroke="${TYRE[lab.tyre]}" stroke-width="2.4"><title>${esc(lab.tyre.toLowerCase())}</title></circle>` : ""}</g>`;
  });

  // readout beside the selected car
  const tel = D.focus && D.focus.number === focus ? D.focus.tel || [] : [];
  if (me && at[focus] && tel.length) {
    let s = null;
    for (let i = tel.length - 1; i >= 0; i--) { const t = Date.parse(tel[i].date); if (t <= T) { if (T - t < 2500) s = tel[i]; break; } }
    if (s && s.speed != null) {
      const p = at[focus], x = p[0] + 16, y = p[1] + 8;
      v += `<g class="fx-hud" transform="translate(${f1(x)},${f1(y)})"><rect width="92" height="34" rx="4"/>
        <text class="sp" x="7" y="16">${s.speed}<tspan> km/h</tspan></text><text class="gr" x="85" y="16" text-anchor="end">${s.gear ?? ""}</text>
        <rect class="bar" x="7" y="23" width="56" height="4" rx="2"/><rect class="thr" x="7" y="23" width="${f1(0.56 * (s.throttle || 0))}" height="4" rx="2"/>
        <rect class="${s.brake ? "brk on" : "brk"}" x="68" y="22" width="17" height="6" rx="2"/></g>`;
    }
  }

  // chequered flag at the line
  if ((D.status === "CHEQUERED" || FX.demoChequer) && o.length && FX.chequer != null && now - FX.chequer < 90000) {
    const [x, y] = P(o[0][0], o[0][1]);
    let sq = "";
    for (let i = 0; i < 6; i++) for (let j = 0; j < 4; j++) sq += `<rect x="${i * 5}" y="${j * 5}" width="5" height="5" fill="${(i + j) % 2 ? "#111" : "#fff"}"/>`;
    v += `<g class="fx-chq" transform="translate(${f1(x)},${f1(y - 44)})"><line x1="0" y1="0" x2="0" y2="44"/><g class="cloth">${sq}</g></g>`;
  }
  under.innerHTML = u;
  over.innerHTML = v;
}

/* ---------- the selected car's last lap, coloured by speed / throttle / brake ---------- */
function setMapColour(mode) {
  FX.mode = mode; store.set("mapcolour", mode);
  document.querySelectorAll("#mapcolour button").forEach(b => b.classList.toggle("on", b.dataset.m === mode));
  FX.lap.drawn = null;
  if (D) refreshLapColour();
}
async function refreshLapColour() {
  const row = focus && D && D.rows.find(r => r.number === focus);
  if (FX.mode === "off" || !row) { FX.lap.trace = null; FX.lap.key = null; lapInfo(); return; }
  const key = `${D.session_key}|${focus}|${row.lap}`;
  if (key === FX.lap.key || FX.lap.loading) return;
  FX.lap.loading = true;
  try {
    const t = await fetchLap(focus, "last");
    FX.lap.trace = t && t.samples && t.samples.length > 20 ? t : null;
    FX.lap.code = row.code;
  } catch (e) { FX.lap.trace = null; }
  FX.lap.key = key; FX.lap.loading = false; FX.lap.drawn = null; lapInfo();
}
function lapInfo() {
  const el = $("maplapinfo"); if (!el) return;
  const t = FX.lap.trace;
  if (FX.mode === "off") { el.innerHTML = ""; return; }
  if (!focus) { el.innerHTML = `Click a car to colour the track with its last lap.`; return; }
  if (!t) { el.innerHTML = FX.lap.loading ? "Loading the lap…" : "No completed lap for this car yet."; return; }
  const sp = t.samples.map(s => s[2]).filter(x => x != null);
  const legend = {speed: `<span class="ramp speed"></span> ${Math.min(...sp)} to ${Math.max(...sp)} km/h`,
    throttle: `<span class="ramp throttle"></span> lift to full throttle`, brake: `<span class="sw brk"></span> braking`}[FX.mode];
  el.innerHTML = `<b>${esc(FX.lap.code)}</b> lap ${t.lap} · <span class="num">${fmtLap(t.duration)}</span> · ${legend}`;
}
function lapColour(s, lo, hi) {
  if (FX.mode === "brake") return s[4] ? "#ff2a2a" : null;
  if (FX.mode === "throttle") { const f = clamp((s[3] || 0) / 100, 0, 1); return `hsl(${Math.round(f * 125)} 85% ${Math.round(42 + f * 8)}%)`; }
  const f = clamp(((s[2] || 0) - lo) / (hi - lo || 1), 0, 1);              // slow = deep violet, fast = bright yellow
  return `hsl(${Math.round(275 - f * 220)} 90% ${Math.round(38 + f * 22)}%)`;
}
function lapLayer(g, P) {
  const t = FX.lap.trace, key = t ? FX.lap.key + FX.mode + g.ownerSVGElement.dataset.key : "";
  if (g.dataset.k === key) return;
  g.dataset.k = key;
  if (!t || FX.mode === "off") { g.innerHTML = ""; return; }
  const sp = t.samples.map(s => s[2]).filter(x => x != null), lo = Math.min(...sp), hi = Math.max(...sp);
  let out = "";
  for (let i = 1; i < t.samples.length; i++) {
    const a = t.samples[i - 1], b = t.samples[i], c = lapColour(b, lo, hi);
    if (!c || a[6] == null || b[6] == null) continue;
    const p = P(a[6], a[7]), q = P(b[6], b[7]);
    out += `<line x1="${p[0].toFixed(1)}" y1="${p[1].toFixed(1)}" x2="${q[0].toFixed(1)}" y2="${q[1].toFixed(1)}" stroke="${c}"/>`;
  }
  g.innerHTML = out;
}
(function initMapColour() {
  const seg = $("mapcolour"); if (!seg) return;
  seg.querySelectorAll("button").forEach(b => { b.onclick = () => setMapColour(b.dataset.m); b.classList.toggle("on", b.dataset.m === FX.mode); });
})();
