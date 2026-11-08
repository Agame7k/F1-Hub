/* F1 Hub dashboard - the moving track map, the sync / loading bar.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- fluid map: animate cars between updates along the real position samples ---------- */
const TRAIL = {};                 // number -> [[epoch_ms, x, y], ...]
// Draw the map this far behind "now" so there is always a next sample to move towards. Positions reach us a few
// seconds after they happen (OpenF1's own latency), so the buffer adapts to what we actually measure.
let MAP_LAG = 4500, lagObs = [];
function mergeTrails(d) {
  const cars = (d.map && d.map.cars) || [];
  const newest = Math.max(0, ...cars.map(c => c.trail && c.trail.length ? c.trail[c.trail.length - 1][0] : 0));
  if (newest && !(d.delay_status && d.delay_status.buffering)) {
    // positions come in bursts, so size the buffer to the worst lag of the last ~30 s, not the average
    lagObs.push(vnow().getTime() - newest + (LIVE.open ? 400 : 1100));   // + time until the next frame brings more
    if (lagObs.length > 30) lagObs.shift();
    const want = Math.min(15000, Math.max(800, ...lagObs));
    MAP_LAG = want > MAP_LAG ? want : MAP_LAG * 0.95 + want * 0.05;    // grow at once, shrink gently
  }
  for (const c of cars) {
    const t = TRAIL[c.number] || (TRAIL[c.number] = []);
    for (const p of c.trail || []) if (!t.length || p[0] > t[t.length - 1][0]) t.push(p);
    while (t.length > 2 && t[0][0] < t[t.length - 1][0] - 20000) t.shift();
  }
}
function carAt(n, T) {
  const t = TRAIL[n];
  if (!t || !t.length) return null;
  if (T <= t[0][0]) return t[0];
  for (let i = t.length - 1; i > 0; i--) {
    if (t[i - 1][0] <= T) {
      const a = t[i - 1], b = t[i];
      if (T >= b[0]) {            // past the newest sample: glide on for up to 1 s, then wait
        if (i !== t.length - 1 || T - b[0] > 1000 || b[0] === a[0]) return b;
        const f = (T - b[0]) / (b[0] - a[0]);
        return [T, b[1] + (b[1] - a[1]) * Math.min(f, 1), b[2] + (b[2] - a[2]) * Math.min(f, 1)];
      }
      const f = (T - a[0]) / (b[0] - a[0] || 1);
      return [T, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    }
  }
  return t[0];
}
let lastAnim = 0;
function animateMaps(fromTimer) {
  if (!fromTimer) requestAnimationFrame(() => animateMaps(false));
  if (performance.now() - lastAnim < 30) return;
  lastAnim = performance.now();
  if (D && !document.hidden) try {
    const T = vnow().getTime() - MAP_LAG;
    document.querySelectorAll("main:not(.hidden) svg.trk").forEach(svg => {
      const [cs, sn, pad, minX, minY, k, H] = svg.dataset.proj.split(",").map(Number);
      const P = (x, y) => [pad + (x * cs - y * sn - minX) * k, H - (pad + (x * sn + y * cs - minY) * k)];
      if (svg.closest("#bigmap")) drawGhost(svg, P, T);
      svg.querySelectorAll("circle[data-n]").forEach(el => {
        const p = carAt(+el.dataset.n, T);
        if (!p) return;
        const [x, y] = P(p[1], p[2]);
        el.setAttribute("cx", x.toFixed(1)); el.setAttribute("cy", y.toFixed(1));
        const lab = svg.querySelector(`g[data-n="${el.dataset.n}"]`);
        if (lab) lab.setAttribute("transform", `translate(${(x - lab.dataset.x).toFixed(1)},${(y - lab.dataset.y).toFixed(1)})`);
      });
    });
  } catch (e) { console.warn("map animation", e); }
}
requestAnimationFrame(() => animateMaps(false));
setInterval(() => animateMaps(true), 100);    // keeps moving where animation frames are throttled

/* ---------- sync / loading bar with countdown ---------- */
function renderSync() {
  const bar = $("syncbar");
  if (!D) return;
  const since = (Date.now() - receivedAt) / 1000;
  if (D.loading) {
    bar.className = "load";
    bar.innerHTML = `<span>⏳ ${esc(D.loading.text)}…</span><span class="big">${Math.round(D.loading.elapsed + since)} s</span><span class="muted">a full race takes ~20-40 s the first time, then it's cached</span>`;
    return;
  }
  const ds = D.delay_status;
  if (ds && ds.buffering && D.settings && D.settings.delay) {
    const left = Math.max(0, ds.ready_in - since), pct = Math.min(100, (D.settings.delay - left) / D.settings.delay * 100);
    bar.className = "";
    bar.innerHTML = `<span>⏸ Building your ${D.settings.delay}s delay - holding the picture so nothing is spoiled</span>
      <span class="big">${Math.ceil(left)} s</span><span class="bar"><i style="width:${pct.toFixed(1)}%"></i></span><span class="muted">until the delayed view starts moving</span>`;
    return;
  }
  bar.className = "hidden";
}
// how far the data on screen trails the moment you're meant to be seeing (live: OpenF1's own lag). Updates arrive in
// bursts, so the raw figure saw-tooths every second; show a smoothed, whole-second value that only moves on a real change.
const AGE = {v: null, shown: null};
function dataAge() {
  if (!D || !D.now || D.mode === "replay") return "";
  const raw = Math.max(0, D.data_time ? (vnow().getTime() / 1000 - D.data_time) : (Date.now() - new Date(D.now)) / 1000 - curDelay());
  AGE.v = AGE.v == null || Math.abs(raw - AGE.v) > 15 ? raw : AGE.v * 0.9 + raw * 0.1;
  const n = Math.round(AGE.v);
  if (AGE.shown == null || Math.abs(AGE.v - AGE.shown) > 0.75) AGE.shown = n;    // a little hysteresis: no 4 <-> 5 flicker
  return AGE.shown <= 1 ? "data up to date" : `data ~${AGE.shown} s behind`;
}
