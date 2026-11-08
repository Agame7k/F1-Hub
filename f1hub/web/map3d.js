/* F1 Hub dashboard - the 3D track map: the circuit as a raised ribbon at its real elevation (exaggerated), the cars
   on it, an orbiting or car-following camera, and the lap's elevation profile. Drawn on a canvas with a small
   perspective projection and painter's ordering - no 3D library needed for a few hundred quads.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const M3D = Object.assign({on: false, cam: "orbit", spin: true, exag: 3}, store.get("map3d", {}), {
  yaw: Math.PI, pitch: 0.78, dist: 2.25, fdist: 0.55, target: null, drag: null, geo: null, hits: [],
  raf: 0, last: 0, touched: 0, W: 0, H: 0, dpr: 1, followCode: ""});
const M3D_BG = [11, 12, 16];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const hexRgb = h => {
  h = String(h || "#888").replace("#", "");
  if (h.length === 3) h = h.split("").map(c => c + c).join("");
  const v = parseInt(h.slice(0, 6), 16);
  return isNaN(v) ? [136, 136, 136] : [v >> 16 & 255, v >> 8 & 255, v & 255];
};
const mix = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

function m3dSave() { store.set("map3d", {on: M3D.on, cam: M3D.cam, spin: M3D.spin, exag: M3D.exag}); }

/* ---------- the circuit in 3D: built once per outline / height setting ---------- */
function m3dGeo(d) {
  const m = d.map || {}, o = m.outline || [];
  if (o.length < 10) return null;
  const el = m.elevation && m.elevation.length === o.length ? m.elevation : null;
  const key = [o.length, o[0].join(), m.rotation || 0, el ? el.length : 0, M3D.exag].join("|");
  if (M3D.geo && M3D.geo.key === key) return M3D.geo;
  const th = (m.rotation || 0) * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
  const rot = (x, y) => [x * cs - y * sn, x * sn + y * cs];
  const step = Math.max(1, Math.ceil(o.length / 480)), idx = [];
  for (let i = 0; i < o.length; i += step) idx.push(i);
  const raw = idx.map(i => rot(o[i][0], o[i][1]));
  const xs = raw.map(p => p[0]), ys = raw.map(p => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const Rad = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2 || 1;
  const P = raw.map((p, j) => [p[0] - cx, p[1] - cy, el ? el[idx[j]] * 10 * M3D.exag : 0]);   // metres -> OpenF1 units
  const n = P.length, w = Math.max(140, Rad * 0.026), L = [], R = [];
  P.forEach((p, j) => {
    const a = P[(j - 1 + n) % n], b = P[(j + 1) % n];
    const dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1, nx = -dy / l * w / 2, ny = dx / l * w / 2;
    L.push([p[0] + nx, p[1] + ny, p[2]]); R.push([p[0] - nx, p[1] - ny, p[2]]);
  });
  // which marshal sector each piece of track is in
  const secs = (m.sectors || []).slice().sort((a, b) => a.start - b.start);
  const sector = idx.map(i => { let s = secs.length ? secs[secs.length - 1].number : null; for (const x of secs) if (x.start <= i) s = x.number; return s; });
  const zmax = Math.max(1, ...P.map(p => p[2]));
  M3D.geo = {key, P, L, R, idx, n, w, Rad, cx, cy, rot, zmax, el, sector, range: el ? Math.max(...el) : 0, len: o.length};
  if (!M3D.target) M3D.target = [0, 0, zmax / 2];
  return M3D.geo;
}
function m3dNear(g, x, y) {
  let bi = 0, bd = Infinity;
  for (let i = 0; i < g.n; i++) { const p = g.P[i], dd = (p[0] - x) ** 2 + (p[1] - y) ** 2; if (dd < bd) { bd = dd; bi = i; } }
  return bi;
}
function m3dWorld(g, rx, ry, lift = 0) {        // OpenF1 x, y -> a point on (or above) the track surface
  const [a, b] = g.rot(rx, ry), x = a - g.cx, y = b - g.cy, j = m3dNear(g, x, y);
  return {p: [x, y, g.P[j][2] + lift], j};
}

/* ---------- one frame ---------- */
function m3dDraw(now, dt) {
  const cv = $("m3dc"), ctx = cv.getContext("2d"), W = M3D.W, H = M3D.H;
  if (!W || !H) return;
  ctx.setTransform(M3D.dpr, 0, 0, M3D.dpr, 0, 0);
  const bg = ctx.createRadialGradient(W / 2, H * .35, 0, W / 2, H * .35, Math.max(W, H) * .8);
  bg.addColorStop(0, "#161922"); bg.addColorStop(1, "#08090c");
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
  const g = D && m3dGeo(D);
  if (!g) {
    ctx.fillStyle = "#8b91a1"; ctx.font = "600 14px 'Titillium Web', system-ui, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("Waiting for location data…", W / 2, H / 2); ctx.textAlign = "left";
    return;
  }
  // where everything is right now (cars glide along their position samples, a few seconds behind, as on the 2D map)
  const T = vnow().getTime() - MAP_LAG, lift = g.w * 0.42;
  const qrows = D.quali && D.quali.rows ? D.quali.rows : {};
  const cars = ((D.map && D.map.cars) || []).map(c => {
    const s = carAt(c.number, T), w = m3dWorld(g, s ? s[1] : c.x, s ? s[2] : c.y, lift);
    return Object.assign({c, q: qrows[c.number] || {}}, w);
  });

  // camera: follow a car from behind, or orbit the circuit (slowly spinning when left alone)
  const k = s => 1 - Math.exp(-dt * s);
  const followN = focus || (D.rows[0] && D.rows[0].number);
  const fc = M3D.cam === "follow" && cars.find(x => x.c.number === followN);
  if (fc) {
    const a = g.P[(fc.j - 3 + g.n) % g.n], b = g.P[(fc.j + 3) % g.n];
    const want = Math.atan2(-(b[0] - a[0]), -(b[1] - a[1]));
    let dy = want - M3D.yaw; dy = Math.atan2(Math.sin(dy), Math.cos(dy));
    M3D.yaw += dy * k(2.5);
    M3D.target = M3D.target.map((v, i) => v + (fc.p[i] - v) * k(6));
    M3D.dist += (M3D.fdist - M3D.dist) * k(3);
    M3D.pitch += (0.36 - M3D.pitch) * k(3);
    const code = fc.c.code || "";
    if (code !== M3D.followCode) { M3D.followCode = code; $("m3dfollow").textContent = "Follow " + code; }
  } else {
    const home = [0, 0, g.zmax / 2];
    M3D.target = M3D.target.map((v, i) => v + (home[i] - v) * k(3));
    if (M3D.spin && !M3D.drag && !REDUCED.matches && now - M3D.touched > 2500) M3D.yaw += dt * 0.07;
  }

  const cp = Math.cos(M3D.pitch), sp = Math.sin(M3D.pitch), d = M3D.dist * g.Rad, t = M3D.target;
  const C = [t[0] + d * cp * Math.sin(M3D.yaw), t[1] + d * cp * Math.cos(M3D.yaw), t[2] + d * sp];
  let f = [t[0] - C[0], t[1] - C[1], t[2] - C[2]];
  const fl = Math.hypot(...f); f = f.map(v => v / fl);
  let r = [f[1], -f[0], 0]; const rl = Math.hypot(...r) || 1; r = r.map(v => v / rl);          // f x up
  const u = [r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]];
  const focal = Math.min(W * 1.2, H * 1.25), ox = W / 2, oy = H * 0.5, near = g.Rad * 0.03;
  const pr = p => {
    const vx = p[0] - C[0], vy = p[1] - C[1], vz = p[2] - C[2], z = vx * f[0] + vy * f[1] + vz * f[2];
    if (z < near) return null;
    return [ox + (vx * r[0] + vy * r[1] + vz * r[2]) / z * focal, oy - (vx * u[0] + vy * u[1] + vz * u[2]) / z * focal, z];
  };
  const dmin = Math.max(near, d - g.Rad), dmax = d + g.Rad * 1.2;
  const fog = z => clamp((z - dmin) / (dmax - dmin), 0, 1);
  const gz = -g.w * 0.25;

  // ground grid
  const ext = g.Rad * 1.6, N = 14;
  ctx.lineWidth = 1;
  for (let a = 0; a <= N; a++) for (const along of [0, 1]) {
    const v = -ext + 2 * ext * a / N;
    let prev = null;
    for (let s = 0; s <= 10; s++) {
      const q = -ext + 2 * ext * s / 10, p = pr(along ? [v, q, gz] : [q, v, gz]);
      if (p && prev) { ctx.strokeStyle = `rgba(255,255,255,${0.055 * (1 - fog(p[2]))})`; ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(p[0], p[1]); ctx.stroke(); }
      prev = p;
    }
  }
  // the track's shadow on the ground
  ctx.beginPath();
  let any = false;
  g.P.forEach(p => { const q = pr([p[0], p[1], gz]); if (q) { any ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1]); any = true; } });
  ctx.closePath();
  ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineJoin = "round"; ctx.lineWidth = Math.max(2, g.w * focal / d * 1.8); ctx.stroke();

  // track: surface + walls down to the ground, sorted far to near
  const PL = g.L.map(pr), PR = g.R.map(pr);
  const GL = g.el ? g.L.map(p => pr([p[0], p[1], gz])) : null, GR = g.el ? g.R.map(p => pr([p[0], p[1], gz])) : null;
  const rc = D.rc || {}, flags = rc.sector_flags || {}, cleared = rc.sector_cleared || {};
  const neutral = D.status === "RED" ? [225, 6, 0] : D.status === "SC" || D.status === "VSC" ? [255, 176, 0] : null;
  const secCol = {}, blink = (now % 1000) < 500;
  (D.map.sectors || []).forEach(s => {
    const fl = flags[String(s.number)], recent = cleared[String(s.number)] && (vnow() - new Date(cleared[String(s.number)])) < 60000;
    secCol[s.number] = fl === "YELLOW" || (fl === "DOUBLE YELLOW" && blink) ? [255, 211, 0] : fl === "DOUBLE YELLOW" ? [140, 116, 0]
      : recent && D.status !== "RED" ? [57, 181, 74] : neutral;
  });
  const prims = [];
  for (let j = 0; j < g.n; j++) {
    const k2 = (j + 1) % g.n, q = [PL[j], PR[j], PR[k2], PL[k2]];
    if (q.some(x => !x)) continue;
    const z = (q[0][2] + q[1][2] + q[2][2] + q[3][2]) / 4;
    if (g.el) {
      const wl = [PL[j], PL[k2], GL[k2], GL[j]], wr = [PR[j], PR[k2], GR[k2], GR[j]];
      if (!wl.some(x => !x)) prims.push({z: z + 1, wall: wl, j});
      if (!wr.some(x => !x)) prims.push({z: z + 1, wall: wr, j});
    }
    prims.push({z, quad: q, j});
  }
  // chequered start / finish line across the first piece of track
  {
    const a = g.P[0], b = g.P[1 % g.n], l = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1, cell = g.w / 8;
    const al = [(b[0] - a[0]) / l * cell, (b[1] - a[1]) / l * cell];
    for (let i = 0; i < 8; i++) for (let s = 0; s < 2; s++) {
      const L0 = g.L[0], R0 = g.R[0], f0 = i / 8, f1 = (i + 1) / 8;
      const at = (fr, sv) => [L0[0] + (R0[0] - L0[0]) * fr + al[0] * sv, L0[1] + (R0[1] - L0[1]) * fr + al[1] * sv, a[2] + 2];
      const q = [at(f0, s), at(f1, s), at(f1, s + 1), at(f0, s + 1)].map(pr);
      if (q.some(x => !x)) continue;
      prims.push({z: q[0][2] - 2, chq: q, white: (i + s) % 2 === 0});
    }
  }
  // corner numbers, stopped cars, the ghost, the cars: sprites in the same depth order
  const sprite = (z, draw) => prims.push({z, draw});
  const lab = [];
  ((D.map && D.map.corners) || []).forEach(c => {
    const a = (c.angle || 0) * Math.PI / 180, w = m3dWorld(g, c.x + Math.cos(a) * 420, c.y + Math.sin(a) * 420, g.w * 0.6), p = pr(w.p);
    if (!p) return;
    sprite(p[2], () => {
      const s = clamp(focal / p[2] * g.Rad * 0.0036, 6, 11), al = 1 - fog(p[2]) * 0.7;
      ctx.globalAlpha = al; ctx.fillStyle = "#101217"; ctx.strokeStyle = "#353b4a"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p[0], p[1], s, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#9aa0ae"; ctx.font = `700 ${Math.round(s * 1.1)}px 'Titillium Web', system-ui, sans-serif`; ctx.textAlign = "center";
      ctx.fillText(String(c.number), p[0], p[1] + s * 0.4); ctx.textAlign = "left"; ctx.globalAlpha = 1;
    });
  });
  (D.incidents || []).filter(i => i.state === "stopped" && i.kind !== "spin").forEach(i => {
    const p = pr(m3dWorld(g, i.x, i.y, lift).p);
    if (!p) return;
    const col = i.kind === "crash" ? "#ff2a2a" : "#ffb000";
    sprite(p[2] - 3, () => {
      const s = Math.max(8, g.w * 1.2 * focal / p[2]), ph = (now % 1400) / 1400;
      ctx.strokeStyle = col; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(p[0], p[1], s, 0, 7); ctx.stroke();
      ctx.globalAlpha = 1 - ph; ctx.beginPath(); ctx.arc(p[0], p[1], s * (1 + ph * 1.2), 0, 7); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillStyle = col; ctx.font = "900 12px 'Titillium Web', system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillText(i.code || "", p[0], p[1] - s - 6); ctx.textAlign = "left";
    });
  });
  const me = focus && D.rows.find(x => x.number === focus);
  if (typeof GHOST !== "undefined" && GHOST.on && GHOST.trace && me && me.lap_start) {
    const gp = ghostAt((T - new Date(me.lap_start).getTime()) / 1000), p = gp && pr(m3dWorld(g, gp[6], gp[7], lift).p);
    if (p) sprite(p[2], () => {
      const s = Math.max(3.5, g.w * 0.42 * focal / p[2]);
      ctx.fillStyle = "rgba(197,108,255,.35)"; ctx.strokeStyle = "#e0a8ff"; ctx.lineWidth = 2; ctx.setLineDash([3, 2]);
      ctx.beginPath(); ctx.arc(p[0], p[1], s, 0, 7); ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = "#e0a8ff"; ctx.font = "800 10px 'Titillium Web', system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillText("BEST", p[0], p[1] - s - 5); ctx.textAlign = "left";
    });
  }
  const sc = typeof scAt === "function" && MAPOPT.sc ? scAt(T) : null, scp = sc && pr(m3dWorld(g, sc.x, sc.y, lift).p);
  if (scp) sprite(scp[2], () => {
    const s = Math.max(4.5, g.w * 0.55 * focal / scp[2]), ph = (now % 900) / 900;
    ctx.globalAlpha = sc.alpha;
    ctx.fillStyle = `rgba(255,176,0,${sc.phase === "deploying" && !REDUCED.matches ? 0.25 + 0.3 * Math.sin(ph * Math.PI) : 0.28})`;
    ctx.beginPath(); ctx.arc(scp[0], scp[1], s * 2, 0, 7); ctx.fill();
    ctx.fillStyle = "#ffb000"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(scp[0], scp[1], s, 0, 7); ctx.fill(); ctx.stroke();
    ctx.font = "900 11px 'Titillium Web', system-ui, sans-serif"; ctx.textAlign = "center";
    const w = ctx.measureText(sc.label).width + 12;
    ctx.fillStyle = "rgba(11,12,16,.92)"; ctx.strokeStyle = "#ffb000"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(scp[0] - w / 2, scp[1] - s - 24, w, 17, 3) : ctx.rect(scp[0] - w / 2, scp[1] - s - 24, w, 17); ctx.fill(); ctx.stroke();
    ctx.fillStyle = "#ffb000"; ctx.fillText(sc.label, scp[0], scp[1] - s - 12); ctx.textAlign = "left"; ctx.globalAlpha = 1;
  });
  M3D.hits = [];
  const rowOf = {}; D.rows.forEach(r => rowOf[r.number] = r);
  cars.forEach(x => {
    const p = pr(x.p), base = pr([x.p[0], x.p[1], x.p[2] - lift]);
    if (!p) return;
    const col = hexRgb(x.c.colour), mine = x.c.number === focus, push = x.q.on_lap && !mine;
    const s = Math.max(3.5, g.w * 0.42 * focal / p[2]) * (mine ? 1.25 : 1);
    // where it was a moment ago: the tail, and which way the nose points
    const back = [];
    for (let k = 1; k <= 6; k++) {
      const q = carAt(x.c.number, T - k * 150), w = q && pr(m3dWorld(g, q[1], q[2], lift).p);
      if (w) back.push(w);
    }
    let dir = null;
    if (back.length) { const dx = p[0] - back[0][0], dy = p[1] - back[0][1], l = Math.hypot(dx, dy); if (l > 0.6) dir = [dx / l, dy / l]; }
    if (back.length > 1 && dir && !REDUCED.matches && MAPOPT.tails) sprite(p[2] + 0.5, () => {
      ctx.lineCap = "round";
      let a = p;
      back.forEach((b, i) => {
        ctx.strokeStyle = rgba(col, 0.5 * (1 - i / back.length)); ctx.lineWidth = Math.max(1, s * 0.9 * (1 - i / back.length));
        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); a = b;
      });
      ctx.lineCap = "butt";
    });
    const tyre = rowOf[x.c.number] && TYRE[rowOf[x.c.number].compound];
    M3D.hits.push({n: x.c.number, x: p[0], y: p[1], r: s});
    if (MAPOPT.names || mine) lab.push({x: p[0], y: p[1], s, z: p[2], c: x.c, mine});
    sprite(p[2], () => {
      ctx.globalAlpha = x.q.out_in ? 0.35 : 1;
      if (base) { ctx.fillStyle = "rgba(0,0,0,.45)"; ctx.beginPath(); ctx.ellipse(base[0], base[1], s * 1.25, s * 0.55, 0, 0, 7); ctx.fill(); }
      const gr = ctx.createRadialGradient(p[0] - s * .35, p[1] - s * .4, s * .1, p[0], p[1], s);
      gr.addColorStop(0, rgba(mix(col, [255, 255, 255], .55))); gr.addColorStop(.55, rgba(col)); gr.addColorStop(1, rgba(mix(col, [0, 0, 0], .5)));
      ctx.fillStyle = gr; ctx.strokeStyle = mine ? "#fff" : push ? "#2bd46b" : "#0b0c10"; ctx.lineWidth = mine ? 2.2 : push ? 2.5 : 1.4;
      if (dir) {        // the nose
        const nx = -dir[1], ny = dir[0];
        ctx.beginPath(); ctx.moveTo(p[0] + dir[0] * s * 1.9, p[1] + dir[1] * s * 1.9);
        ctx.lineTo(p[0] + dir[0] * s * 0.5 + nx * s * 0.62, p[1] + dir[1] * s * 0.5 + ny * s * 0.62);
        ctx.lineTo(p[0] + dir[0] * s * 0.5 - nx * s * 0.62, p[1] + dir[1] * s * 0.5 - ny * s * 0.62); ctx.closePath();
        ctx.fillStyle = rgba(col); ctx.fill(); ctx.lineWidth = 1.2; ctx.stroke();
        ctx.fillStyle = gr;
      }
      ctx.beginPath(); ctx.arc(p[0], p[1], s, 0, 7); ctx.fill(); ctx.stroke();
      if (tyre && tyre !== TYRE.UNKNOWN && MAPOPT.tails) { ctx.strokeStyle = tyre; ctx.lineWidth = Math.max(1.5, s * 0.22); ctx.beginPath(); ctx.arc(p[0], p[1], s + ctx.lineWidth * 0.5 + 1.2, 0, 7); ctx.stroke(); }
      if (mine) {
        const ph = (now % 1600) / 1600;
        ctx.globalAlpha = 1 - ph; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(p[0], p[1], s * (1.3 + ph * 1.8), 0, 7); ctx.stroke();
      }
      ctx.globalAlpha = 1;
    });
  });

  prims.sort((a, b) => b.z - a.z);
  const edge = [75, 83, 103], low = [24, 27, 35], high = [48, 54, 69];
  ctx.lineJoin = "round";
  for (const pm of prims) {
    if (pm.draw) { pm.draw(); continue; }
    const fz = fog(pm.z) * 0.6;
    if (pm.chq) {
      ctx.fillStyle = rgba(mix(pm.white ? [235, 235, 235] : [17, 17, 17], M3D_BG, fz));
      ctx.beginPath(); pm.chq.forEach((v, i) => i ? ctx.lineTo(v[0], v[1]) : ctx.moveTo(v[0], v[1])); ctx.closePath(); ctx.fill();
      continue;
    }
    const q = pm.quad || pm.wall;
    let col;
    if (pm.wall) col = mix(mix([18, 20, 27], [60, 14, 16], g.P[pm.j][2] / g.zmax * 0.35), M3D_BG, fz);
    else {
      col = mix(low, high, g.P[pm.j][2] / g.zmax);
      const fc2 = secCol[g.sector[pm.j]];
      if (fc2) col = mix(col, fc2, 0.62);
      col = mix(col, M3D_BG, fz);
    }
    ctx.fillStyle = ctx.strokeStyle = rgba(col); ctx.lineWidth = 0.8;
    ctx.beginPath(); ctx.moveTo(q[0][0], q[0][1]); ctx.lineTo(q[1][0], q[1][1]); ctx.lineTo(q[2][0], q[2][1]); ctx.lineTo(q[3][0], q[3][1]); ctx.closePath();
    ctx.fill(); ctx.stroke();
    if (pm.quad) {      // kerb-like edges
      ctx.strokeStyle = rgba(mix(edge, M3D_BG, fz)); ctx.lineWidth = Math.max(1, Math.min(2.2, focal / pm.z * g.w * 0.08));
      ctx.beginPath(); ctx.moveTo(q[0][0], q[0][1]); ctx.lineTo(q[3][0], q[3][1]); ctx.moveTo(q[1][0], q[1][1]); ctx.lineTo(q[2][0], q[2][1]); ctx.stroke();
    }
  }

  // labels on top, nearest (and the selected car) first so they win the space
  ctx.font = "800 11.5px 'Titillium Web', system-ui, sans-serif";
  const placed = [];
  lab.sort((a, b) => (b.mine - a.mine) || a.z - b.z).forEach(l => {
    const pos = String(l.c.pos ?? ""), code = String(l.c.code || "");
    ctx.font = "600 10.5px 'Titillium Web', system-ui, sans-serif"; const pw = pos ? ctx.measureText(pos).width + 5 : 0;
    ctx.font = "800 11.5px 'Titillium Web', system-ui, sans-serif"; const cw = ctx.measureText(code).width;
    const w = 13 + pw + cw, h = 18;
    for (const [dx, dy] of [[l.s + 6, -l.s - 20], [-l.s - 6 - w, -l.s - 20], [l.s + 6, l.s + 2], [-l.s - 6 - w, l.s + 2], [-w / 2, -l.s - 26]]) {
      const b = {x: l.x + dx, y: l.y + dy, w, h};
      if (b.x < 2 || b.y < 2 || b.x + w > W - 2 || b.y + h > H - 2) continue;
      if (placed.some(o => b.x < o.x + o.w + 2 && b.x + w + 2 > o.x && b.y < o.y + o.h + 2 && b.y + h + 2 > o.y)) continue;
      placed.push(b);
      const ax = clamp(l.x, b.x, b.x + w), ay = clamp(l.y, b.y, b.y + h), col = l.c.colour || "#888";
      ctx.strokeStyle = col; ctx.globalAlpha = .7; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(l.x, l.y); ctx.lineTo(ax, ay); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillStyle = "rgba(11,12,16,.9)"; ctx.strokeStyle = l.mine ? "rgba(255,255,255,.45)" : "rgba(255,255,255,.1)";
      ctx.beginPath(); ctx.roundRect ? ctx.roundRect(b.x, b.y, w, h, 3) : ctx.rect(b.x, b.y, w, h); ctx.fill(); ctx.stroke();
      ctx.fillStyle = col; ctx.fillRect(b.x, b.y, 3, h);
      if (pos) { ctx.fillStyle = "#8b91a1"; ctx.font = "600 10.5px 'Titillium Web', system-ui, sans-serif"; ctx.fillText(pos, b.x + 8, b.y + 13); }
      ctx.fillStyle = "#fff"; ctx.font = "800 11.5px 'Titillium Web', system-ui, sans-serif"; ctx.fillText(code, b.x + 8 + pw, b.y + 13);
      break;
    }
  });

  m3dProfile(ctx, g, cars, W, H);
}

// the lap's elevation, with every car on it
function m3dProfile(ctx, g, cars, W, H) {
  if (!g.el) {
    ctx.fillStyle = "#8b91a1"; ctx.font = "600 12px 'Titillium Web', system-ui, sans-serif";
    ctx.fillText("Flat for now: the heights come from a clean lap of position data.", 16, H - 18);
    return;
  }
  const pw = Math.min(400, W - 28), ph = 70, x0 = 14, y0 = H - ph - 14, ix = x0 + 10, iy = y0 + 24, iw = pw - 20, ih = ph - 32;
  ctx.fillStyle = "rgba(11,12,16,.8)"; ctx.strokeStyle = "rgba(255,255,255,.08)"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x0, y0, pw, ph, 6) : ctx.rect(x0, y0, pw, ph); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#8b91a1"; ctx.font = "700 10px 'Titillium Web', system-ui, sans-serif";
  ctx.fillText("ELEVATION", ix, y0 + 15);
  ctx.textAlign = "right"; ctx.fillStyle = "#c9ccd4"; ctx.fillText(`${g.range.toFixed(0)} m from lowest to highest`, x0 + pw - 10, y0 + 15); ctx.textAlign = "left";
  const el = g.el, n = el.length, top = g.range || 1, X = i => ix + i / (n - 1) * iw, Y = v => iy + ih - v / top * ih;
  ctx.beginPath(); ctx.moveTo(ix, iy + ih);
  for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 300))) ctx.lineTo(X(i), Y(el[i]));
  ctx.lineTo(ix + iw, Y(el[n - 1])); ctx.lineTo(ix + iw, iy + ih); ctx.closePath();
  const gr = ctx.createLinearGradient(0, iy, 0, iy + ih); gr.addColorStop(0, "rgba(225,6,0,.35)"); gr.addColorStop(1, "rgba(225,6,0,0)");
  ctx.fillStyle = gr; ctx.fill();
  ctx.beginPath();
  for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 300))) i ? ctx.lineTo(X(i), Y(el[i])) : ctx.moveTo(X(i), Y(el[i]));
  ctx.strokeStyle = "#ff4d4d"; ctx.lineWidth = 1.5; ctx.stroke();
  cars.slice().sort((a, b) => (a.c.number === focus) - (b.c.number === focus)).forEach(x => {
    const i = g.idx[x.j], mine = x.c.number === focus;
    ctx.fillStyle = x.c.colour || "#888"; ctx.strokeStyle = mine ? "#fff" : "#0b0c10"; ctx.lineWidth = mine ? 2 : 1;
    ctx.beginPath(); ctx.arc(X(i), Y(el[i]), mine ? 5 : 3.5, 0, 7); ctx.fill(); ctx.stroke();
  });
}

/* ---------- loop, sizing, controls ---------- */
function m3dVisible() { return M3D.on && activeTab === "map" && !document.hidden && !!D; }
function m3dLoop(ts) {
  if (!m3dVisible()) { M3D.raf = 0; return; }
  M3D.raf = requestAnimationFrame(m3dLoop);
  const dt = Math.min(0.1, M3D.last ? (ts - M3D.last) / 1000 : 0);
  M3D.last = ts;
  try { m3dDraw(ts, dt); } catch (e) { console.warn("3D map", e); }
}
function m3dKick() { if (!M3D.raf && m3dVisible()) { M3D.last = 0; M3D.raf = requestAnimationFrame(m3dLoop); } }
document.addEventListener("visibilitychange", m3dKick);
function m3dSize() {
  const c = $("m3dc"), r = c.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1);
  if (!r.width) return;
  M3D.W = r.width; M3D.H = r.height; M3D.dpr = dpr;
  c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
}
if (window.ResizeObserver) new ResizeObserver(m3dSize).observe($("m3dc"));

function m3dSet(on, quiet) {
  M3D.on = !!on;
  $("bigmap").classList.toggle("hidden", M3D.on);
  $("map3d").classList.toggle("hidden", !M3D.on);
  document.querySelectorAll("#mapview button").forEach(b => b.classList.toggle("on", (b.dataset.v === "3d") === M3D.on));
  if (!quiet) m3dSave();
  if (M3D.on) { m3dSize(); m3dKick(); } else if (D && activeTab === "map") renderMap(D);
}
function m3dCam(mode) {
  M3D.cam = mode;
  document.querySelectorAll("#m3dcam button").forEach(b => b.classList.toggle("on", b.dataset.c === mode));
  if (mode === "orbit") { M3D.dist = Math.max(M3D.dist, 1.4); M3D.pitch = Math.max(M3D.pitch, 0.6); }
  m3dSave();
}
function m3dReset() { M3D.yaw = Math.PI; M3D.pitch = 0.78; M3D.dist = 2.25; M3D.fdist = 0.55; M3D.touched = performance.now(); m3dCam("orbit"); }
function m3dHit(x, y) {
  let best = null, bd = Infinity;
  for (const h of M3D.hits) { const dd = Math.hypot(h.x - x, h.y - y); if (dd < h.r + 6 && dd < bd) { bd = dd; best = h.n; } }
  return best;
}
(() => {
  const cv = $("m3dc");
  const local = e => { const r = cv.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); M3D.drag = {x: e.clientX, y: e.clientY, moved: 0}; M3D.touched = performance.now(); });
  cv.addEventListener("pointermove", e => {
    const dr = M3D.drag;
    if (!dr) { cv.style.cursor = m3dHit(...local(e)) ? "pointer" : "grab"; return; }
    const dx = e.clientX - dr.x, dy = e.clientY - dr.y;
    dr.moved += Math.abs(dx) + Math.abs(dy); dr.x = e.clientX; dr.y = e.clientY;
    if (dr.moved < 5) return;
    cv.style.cursor = "grabbing";
    if (M3D.cam === "follow") m3dCam("orbit");
    M3D.yaw -= dx * 0.006; M3D.pitch = clamp(M3D.pitch + dy * 0.005, 0.1, 1.5); M3D.touched = performance.now();
  });
  const up = e => {
    const dr = M3D.drag;
    M3D.drag = null; cv.style.cursor = "grab";
    if (dr && dr.moved < 5 && e.type === "pointerup") { const n = m3dHit(...local(e)); if (n) setFocus(n, true); }
  };
  cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
  cv.addEventListener("wheel", e => {
    e.preventDefault();
    const z = Math.exp(e.deltaY * 0.0012);
    if (M3D.cam === "follow") M3D.fdist = clamp(M3D.fdist * z, 0.15, 2);
    else M3D.dist = clamp(M3D.dist * z, 0.35, 5);
    M3D.touched = performance.now();
  }, {passive: false});
  cv.addEventListener("dblclick", m3dReset);
  document.querySelectorAll("#mapview button").forEach(b => b.onclick = () => m3dSet(b.dataset.v === "3d"));
  document.querySelectorAll("#m3dcam button").forEach(b => b.onclick = () => m3dCam(b.dataset.c));
  $("m3dspin").checked = M3D.spin;
  $("m3dspin").onchange = e => { M3D.spin = e.target.checked; m3dSave(); };
  $("m3dex").value = M3D.exag; $("m3dexv").textContent = M3D.exag;
  $("m3dex").oninput = e => { M3D.exag = +e.target.value; $("m3dexv").textContent = M3D.exag; m3dSave(); };
  $("m3dreset").onclick = m3dReset;
  m3dCam(M3D.cam);
  m3dSet(M3D.on, true);
})();
