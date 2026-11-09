/* F1 Hub dashboard - Explore > Corners: per-corner telemetry from the season dataset (f1hub/cornerreport.py).
   A classic script, loaded after seasonreport.js (it uses SR_YEARS, srBar, srStatusLine, xJSON, xErr, vis). */
const CN = {data: null, year: null, session: null, a: null, b: null, poll: null};
const CN_KIND = {slow: "slow (under 120 km/h)", medium: "medium (120-200 km/h)", fast: "fast (200 km/h and over)"};
const cnSgn = (v, n = 1, unit = "") => v == null ? "-" : `${v > 0 ? "+" : ""}${v.toFixed(n)}${unit}`;

async function cnLoad(force) {
  const ys = $("cnYear");
  if (!ys.options.length) {
    let pick = store.get("cnyear", null) || store.get("sryear", null);
    if (!pick) { try { pick = ((await xJSON("/api/dataset")).compiled_years || [])[0]; } catch (e) { /* this year */ } }
    ys.innerHTML = SR_YEARS.map(y => `<option>${y}</option>`).join(""); ys.value = SR_YEARS.includes(+pick) ? pick : SR_YEARS[0];
  }
  const year = +ys.value; store.set("cnyear", year);
  if (year !== CN.year) Object.assign(CN, {year, session: null, a: null, b: null});
  const q = new URLSearchParams({year});
  for (const k of ["session", "a", "b"]) if (CN[k]) q.set(k, CN[k]);
  if (!CN.data || force !== false) $("cnOut").innerHTML = CN.data ? $("cnOut").innerHTML : `<div class="empty">Loading…</div>`;
  try { CN.data = await xJSON("/api/corners?" + q); } catch (e) { $("cnOut").innerHTML = xErr(e); return; }
  if (+$("cnYear").value !== year) return;
  const d = CN.data;
  if (d.session) Object.assign(CN, {session: d.session.session_key, a: d.a, b: d.b});
  cnRender();
}
function cnPick(k, v) { CN[k] = +v || null; if (k === "session") { CN.a = CN.b = null; } cnLoad(); }

async function cnCompile(kind) {
  const year = +$("cnYear").value;
  try { await xJSON(`/api/dataset/compile?year=${year}&telemetry=${kind}`); } catch (e) { $("cnStatus").innerHTML = xErr(e); return; }
  cnWatch(year);
}
function cnWatch(year) {
  clearTimeout(CN.poll);
  CN.poll = setTimeout(async () => {
    let st; try { st = await xJSON("/api/dataset?year=" + year); } catch (e) { return; }
    if (+$("cnYear").value !== year) return;
    if ((st.job || {}).running) { $("cnStatus").innerHTML = srStatusLine(st); cnWatch(year); return; }
    await cnLoad(true);
  }, 2000);
}

function cnRender() {
  const d = CN.data, year = d.year;
  const running = d.job && d.job.running;
  $("cnStatus").innerHTML = running ? srStatusLine(Object.assign({}, d.status, {job: d.job})) : d.sessions && d.sessions.length
    ? `${d.sessions.length} sessions with telemetry` : "";
  if (running) cnWatch(year);
  const measure = `<div style="margin-top:12px" class="row2"><button class="btn primary" onclick="cnCompile('quali')"${running ? " disabled" : ""}>Measure qualifying</button>
      <button class="btn" onclick="cnCompile('all')"${running ? " disabled" : ""}>Qualifying and races</button></div>
    <div class="xnote" style="margin-top:10px">From a terminal: <code>python3 -m f1hub compile ${year} --telemetry</code> (add <code>all</code> for races too, <code>--round 24</code> for one round).
      It fetches every car's telemetry from OpenF1: about 5 minutes a qualifying session and 10-15 a race, the first time.</div>`;
  if (!d.exists) { $("cnOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("chart")}<b>The ${year} season hasn't been compiled yet</b><br>Compile it in Explore > Season report first.</div></div>`; return; }
  if (!d.sessions.length) {
    $("cnOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("activity")}<b>No telemetry measured for ${year} yet</b><br>
      Measuring reads every car's speed, throttle, brake and position through a session and measures each corner of each lap.${measure}</div></div>`;
    return;
  }
  safe("corners", () => {
    const s = d.session, opts = d.sessions.map(x => `<option value="${x.session_key}"${x.session_key === s.session_key ? " selected" : ""}>R${x.round} ${esc(x.circuit)} - ${esc(x.session_name)}</option>`).join("");
    const dopt = sel => d.drivers.map(x => `<option value="${x.number}"${x.number === sel ? " selected" : ""}>${esc(x.code)}</option>`).join("");
    const A = d.drivers.find(x => x.number === d.a), B = d.drivers.find(x => x.number === d.b);
    $("cnOut").innerHTML = `${d.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: the meeting you're replaying or watching delayed, and later ones, are left out.</div>` : ""}
      <div class="xpick" style="margin-bottom:12px"><select onchange="cnPick('session', this.value)">${opts}</select>
        <select onchange="cnPick('a', this.value)" aria-label="first driver">${dopt(d.a)}</select><span class="muted">v</span>
        <select onchange="cnPick('b', this.value)" aria-label="second driver">${dopt(d.b)}</select></div>
      <div class="two">${cnMap(d, A, B)}${cnSummary(d, A, B)}</div>
      <div style="margin-top:16px">${cnTable(d, A, B)}</div>
      <div class="two" style="margin-top:16px">${cnKinds(d)}${d.laps ? cnRace(d) : cnTop(d)}</div>
      ${d.styles && d.styles.pairs.length ? `<div style="margin-top:16px">${cnStyles(d.styles)}</div>` : ""}
      <p class="xnote" style="margin-top:14px">Corners are found from the session's fastest lap (where speed dips) and named after the official corner numbers.
        Each corner owns the track from halfway to the previous one to halfway to the next, so corner times add up to the lap.
        ${d.quali ? "A driver's typical corner is the median of their three quickest laps." : "A driver's typical corner is the median of their clean laps."}
        OpenF1 samples about four times a second (every ~20 m at 300 km/h): braking points are good to about ±10 m.
        Measure more sessions with <code>python3 -m f1hub compile ${year} --telemetry all</code>.</p>`;
  });
}

/* ---- the track, each corner coloured by which of the two is quicker through it ---- */
function cnMap(d, A, B) {
  const pts = d.outline, xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const W = 520, H = 360, pad = 26, k = Math.min((W - 2 * pad) / ((x1 - x0) || 1), (H - 2 * pad) / ((y1 - y0) || 1));
  const X = x => pad + (x - x0) * k + ((W - 2 * pad) - (x1 - x0) * k) / 2, Y = y => H - pad - (y - y0) * k - ((H - 2 * pad) - (y1 - y0) * k) / 2;
  const line = pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const pa = d.profiles[String(d.a)] || {}, pb = d.profiles[String(d.b)] || {};
  const ca = vis(A ? A.colour : "#888"), cb = vis(B ? B.colour : "#aaa"), same = ca === cb;
  const marks = d.corners.map(c => {
    const ta = (pa[c.corner] || {}).seg_time, tb = (pb[c.corner] || {}).seg_time, dt = ta != null && tb != null ? tb - ta : null;
    const col = dt == null ? "#555" : dt > 0 ? ca : cb, r = 5 + Math.min(9, Math.abs(dt || 0) * 60);
    const who = dt == null ? "" : `${dt > 0 ? A.code : B.code} quicker by ${Math.abs(dt).toFixed(3)} s`;
    return `<g><circle cx="${X(c.x).toFixed(1)}" cy="${Y(c.y).toFixed(1)}" r="${r.toFixed(1)}" fill="${esc(col)}" stroke="#16181f" stroke-width="2"${same && dt != null && dt < 0 ? ' stroke-dasharray="2 2"' : ""}/>
      <text x="${(X(c.x) + r + 3).toFixed(1)}" y="${(Y(c.y) + 4).toFixed(1)}" font-size="11" fill="#eef0f5">${esc(c.label)}</text>
      <circle cx="${X(c.x).toFixed(1)}" cy="${Y(c.y).toFixed(1)}" r="14" fill="transparent"><title>${esc(c.label)} · ${c.kind}, ${Math.round(c.min_speed)} km/h on the reference lap${who ? " · " + who : ""}</title></circle></g>`; }).join("");
  return `<div class="card"><h2>${esc(d.session.circuit)} <span class="sub">${d.corners.length} corners · ${(d.length / 1000).toFixed(2)} km · dot = who is quicker through the corner, bigger = bigger gap</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="circuit map with corners">
      <polyline points="${line}" fill="none" stroke="#3a3f4d" stroke-width="7" stroke-linejoin="round"/><polyline points="${line}" fill="none" stroke="#16181f" stroke-width="3" stroke-linejoin="round"/>${marks}</svg>
    <div class="legend"><span><i style="background:${esc(ca)}"></i>${esc(A ? A.code : "")} quicker</span><span><i style="background:${esc(cb)}"></i>${esc(B ? B.code : "")} quicker${same ? " (dashed)" : ""}</span></div></div></div>`;
}

function cnSummary(d, A, B) {
  const pa = d.profiles[String(d.a)] || {}, pb = d.profiles[String(d.b)] || {};
  const by = {slow: 0, medium: 0, fast: 0}, cnt = {slow: [0, 0], medium: [0, 0], fast: [0, 0]};
  let total = 0, n = 0;
  for (const c of d.corners) { const ta = (pa[c.corner] || {}).seg_time, tb = (pb[c.corner] || {}).seg_time;
    if (ta == null || tb == null) continue; by[c.kind] += tb - ta; total += tb - ta; n++; cnt[c.kind][tb - ta > 0 ? 0 : 1]++; }
  const name = v => v > 0 ? A.code : B.code;
  const kinds = ["slow", "medium", "fast"].filter(k => d.kinds[k]).map(k => `<div><b>${Math.abs(by[k]).toFixed(3)}s</b><span>${esc(name(by[k]))} quicker in the ${d.kinds[k]} ${k} corner${d.kinds[k] > 1 ? "s" : ""} (${cnt[k][0]}-${cnt[k][1]})</span></div>`).join("");
  const avg = (p, m) => { const v = Object.values(p).map(x => x[m]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const brake = avg(pa, "brake_dist") != null && avg(pb, "brake_dist") != null ? avg(pa, "brake_dist") - avg(pb, "brake_dist") : null;
  const minS = avg(pa, "min_speed") != null && avg(pb, "min_speed") != null ? avg(pb, "min_speed") - avg(pa, "min_speed") : null;
  const lap = (x, y) => x && y && x.best && y.best ? y.best - x.best : null, gap = lap(A, B);
  return `<div class="card"><h2>${esc(A.code)} v ${esc(B.code)} <span class="sub">${d.quali ? "typical of their three quickest laps" : "typical of their clean laps"}</span></h2>
    <div class="pad"><div class="facts">
      <div><b>${Math.abs(total).toFixed(3)}s</b><span>${esc(name(total))} quicker over the ${n} corners</span></div>
      ${gap != null ? `<div><b>${Math.abs(gap).toFixed(3)}s</b><span>between their best laps (${esc(gap >= 0 ? A.code : B.code)})</span></div>` : ""}
      ${kinds}
      ${brake != null ? `<div><b>${Math.abs(brake).toFixed(1)} m</b><span>${esc(brake > 0 ? B.code : A.code)} brakes later, on average</span></div>` : ""}
      ${minS != null ? `<div><b>${Math.abs(minS).toFixed(1)} km/h</b><span>more minimum speed for ${esc(minS > 0 ? B.code : A.code)}, on average</span></div>` : ""}</div>
    <p class="xnote">Corner times are each driver's typical lap, so they needn't add up to the gap between best laps.</p></div></div>`;
}

/* ---- corner by corner ---- */
function cnTable(d, A, B) {
  const pa = d.profiles[String(d.a)] || {}, pb = d.profiles[String(d.b)] || {};
  const ca = vis(A.colour || "#888"), cb = vis(B.colour || "#aaa");
  const deltas = d.corners.map(c => { const ta = (pa[c.corner] || {}).seg_time, tb = (pb[c.corner] || {}).seg_time; return ta != null && tb != null ? tb - ta : null; });
  const max = Math.max(0.05, ...deltas.map(v => Math.abs(v || 0)));
  const v = (p, m, n = 0, sgn) => p && p[m] != null ? (sgn ? cnSgn(p[m], n) : p[m].toFixed(n)) : "-";
  const better = (x, y, higher) => x == null || y == null || x === y ? ["", ""] : (higher ? x > y : x < y) ? [' class="r num up"', ' class="r num"'] : [' class="r num"', ' class="r num up"'];
  const pair = (m, n, higher, sgn) => d.corners.map(c => { const x = (pa[c.corner] || {})[m], y = (pb[c.corner] || {})[m]; const [s1, s2] = better(x, y, higher);
    return [`<td${s1 || ' class="r num"'}>${v(pa[c.corner], m, n, sgn)}</td>`, `<td${s2 || ' class="r num"'}>${v(pb[c.corner], m, n, sgn)}</td>`]; });
  const brake = pair("brake_dist", 0, false), mins = pair("min_speed", 0, true), thr = pair("throttle_dist", 0, false), ex = pair("exit_speed", 0, true);
  const rows = d.corners.map((c, i) => { const dt = deltas[i], w = dt == null ? 0 : Math.abs(dt) / max * 50;
    const bar = dt == null ? "" : `<div class="cnbar"><i style="${dt > 0 ? `right:50%;background:${esc(ca)}` : `left:50%;background:${esc(cb)}`};width:${w.toFixed(1)}%"></i><b></b></div>`;
    return `<tr><td class="pos">${esc(c.label)}</td><td class="muted">${c.kind}</td><td class="r num">${Math.round(c.min_speed)}</td>
      ${brake[i].join("")}${mins[i].join("")}${thr[i].join("")}${ex[i].join("")}
      <td style="width:16%" title="${dt == null ? "" : `${dt > 0 ? A.code : B.code} quicker by ${Math.abs(dt).toFixed(3)} s`}">${bar}</td>
      <td class="r num">${dt == null ? "-" : Math.abs(dt).toFixed(3)}</td>
      <td class="muted">${c.quickest_code ? `<span class="stripe" style="background:${esc(c.quickest_colour)};display:inline-block;width:3px;height:11px;vertical-align:-1px"></span> ${esc(c.quickest_code)}` : ""}</td></tr>`; }).join("");
  const two = t => `<th class="r" colspan="2" title="${esc(t[1])}">${t[0]}</th>`;
  return `<div class="card"><h2>Corner by corner <span class="sub">${esc(A.code)} | ${esc(B.code)} in each pair · green = better of the two · bar: who is quicker through the corner (left ${esc(A.code)}, right ${esc(B.code)})</span></h2>
    <div class="scroll"><table><thead><tr><th>Corner</th><th></th><th class="r" title="minimum speed on the reference (fastest) lap, km/h">Ref</th>
      ${two(["Brakes (m)", "braking point, metres before the apex: smaller = later"])}${two(["Min km/h", "minimum speed"])}
      ${two(["Throttle (m)", "full throttle again, metres after the apex: smaller = sooner"])}${two(["Exit km/h", `speed 150 m after the apex`])}
      <th colspan="2" class="r">Time</th><th title="quickest typical time through the corner, whole field">Quickest</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Brakes "-" means the corner is taken without braking (a lift, or flat out).</div></div>`;
}

/* ---- the whole field: who loses time where ---- */
function cnKinds(d) {
  if (!d.by_kind.length) return "";
  const best = d.by_kind[0].total || 0, max = Math.max(...d.by_kind.map(r => r.total), 0.1);
  const ks = ["slow", "medium", "fast"].filter(k => d.kinds[k]);
  const kmin = Object.fromEntries(ks.map(k => [k, Math.min(...d.by_kind.map(r => r[k]))]));
  const rows = d.by_kind.map((r, i) => `<tr><td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)} <small>${esc(r.team || "")}</small></div></td>
    ${ks.map(k => `<td class="r num${r[k] === kmin[k] ? " up" : ""}">${r[k].toFixed(3)}</td>`).join("")}
    <td style="width:22%">${srBar(r.total / max * 100, vis(r.colour), `${r.code}: ${r.total.toFixed(3)} s lost to the quickest in each corner`)}</td>
    <td class="r num"><b>${r.total.toFixed(3)}</b></td><td class="r num">${r.wins || ""}</td></tr>`).join("");
  return `<div class="card"><h2>Where time is lost <span class="sub">s lost to the quickest typical time through each corner, added up by corner type · green = least in that type</span></h2>
    <div class="scroll"><table><thead><tr><th></th><th>Driver</th>${ks.map(k => `<th class="r" title="${esc(CN_KIND[k])}">${k[0].toUpperCase() + k.slice(1)} (${d.kinds[k]})</th>`).join("")}<th></th><th class="r">Total</th><th class="r" title="corners where this driver was quickest">Quickest</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Slow, medium and fast are the minimum speed on the reference lap: under 120, 120-200 and over 200 km/h. A car strong in one type and weak in another shows its set-up and downforce.</div></div>`;
}

function cnTop(d) {
  if (!d.top_speed.length) return "";
  const lo = Math.min(...d.top_speed.map(r => r.top)) - 5, hi = Math.max(...d.top_speed.map(r => r.top));
  const rows = d.top_speed.map(r => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)} <small>${esc(r.team || "")}</small></div></td>
    <td style="width:50%">${srBar((r.top - lo) / ((hi - lo) || 1) * 100, vis(r.colour), `${r.code}: ${r.top} km/h`)}</td><td class="r num">${r.top}</td></tr>`).join("");
  return `<div class="card"><h2>Top speed <span class="sub">highest speed on any lap, km/h · bars start at ${Math.round(lo)}</span></h2><div class="scroll"><table><tbody>${rows}</tbody></table></div></div>`;
}

/* ---- races: lift and coast, consistency ---- */
function cnRace(d) {
  const L = d.laps;
  if (!L.length) return cnTop(d);
  const max = Math.max(...L.map(r => r.lico), 0.2);
  const spark = r => { const pts = r.lico_trend, W = 120, H = 22, x0 = pts[0][0], x1 = pts[pts.length - 1][0], m = Math.max(1, ...pts.map(p => p[1]));
    return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block"><polyline fill="none" stroke="${esc(vis(r.colour))}" stroke-width="1.5" points="${pts.map(p => `${((p[0] - x0) / ((x1 - x0) || 1) * (W - 2) + 1).toFixed(1)},${(H - 2 - p[1] / m * (H - 4)).toFixed(1)}`).join(" ")}"/><title>lift and coast per lap through the race (max ${m.toFixed(1)} s)</title></svg>`; };
  const rows = L.map(r => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)}</div></td>
    <td style="width:22%">${srBar(r.lico / max * 100, vis(r.colour), `${r.code}: ${r.lico} s of lift and coast a lap`)}</td><td class="r num">${r.lico.toFixed(2)}</td>
    <td>${spark(r)}</td><td class="r num">${r.consistency == null ? "-" : r.consistency.toFixed(3)}</td><td class="r num">${r.moments || ""}</td><td class="r num">${r.clean_laps}</td></tr>`).join("");
  return `<div class="card"><h2>Lift and coast, consistency <span class="sub">clean laps · lift and coast = s a lap off the throttle before braking, at over 200 km/h</span></h2>
    <div class="scroll"><table><thead><tr><th>Driver</th><th></th><th class="r">Lift/coast</th><th>Through the race</th><th class="r" title="average spread of the time through each corner, lap to lap (s): lower = more consistent">Spread</th>
      <th class="r" title="corners taken more than 0.5 s slower than the driver's usual: mistakes, traffic, saving">Moments</th><th class="r">Laps</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Lifting and coasting saves fuel, energy and brakes and cools the tyres: a lot of it means a driver was managing their race.</div></div>`;
}

/* ---- the season: team-mates' styles ---- */
function cnStyles(st) {
  const ks = ["slow", "medium", "fast"];
  const cell = (e, n, unit, word) => !e ? "-" : `<span class="${Math.abs(e.est) > 1.96 * (e.se || 1e9) ? "up" : ""}">${cnSgn(e.est, n)}${unit}</span>${e.se ? `<span class="muted"> ±${(1.96 * e.se).toFixed(n)}</span>` : ""}`;
  const rows = st.pairs.map(p => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span><b>${esc(p.a)}</b>&nbsp;<span class="muted">v</span>&nbsp;${esc(p.b)}</div></td>
    ${ks.map(k => { const e = p.kinds[k]; return `<td class="r num">${cell(e.time, 3, "s")}</td><td class="r num">${cell(e.min, 1, "")}</td><td class="r num">${cell(e.brake, 1, "m")}</td>`; }).join("")}
    <td class="r num">${p.rounds}</td></tr>`).join("");
  return `<div class="card"><h2>Team-mates' styles, ${st.year} <span class="sub">qualifying, every round with telemetry · in the first driver's favour: time per corner, km/h more at the apex, metres later on the brakes · green = clear (beyond the 95% interval)</span></h2>
    <div class="scroll"><table><thead><tr><th rowspan="2">Team-mates</th>${ks.map(k => `<th colspan="3" class="c" style="text-align:center">${k} corners</th>`).join("")}<th rowspan="2" class="r">Rounds</th></tr>
      <tr>${ks.map(() => `<th class="r">Time</th><th class="r">Apex</th><th class="r">Brakes</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">The first driver gains time overall. A driver who brakes later but carries less speed through the apex is attacking the entry; one with more apex speed and earlier braking rolls more speed through the corner.</div></div>`;
}
