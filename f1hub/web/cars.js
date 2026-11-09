/* F1 Hub dashboard - Explore > Cars & circuits: car characteristics, energy clipping and circuit groups
   (f1hub/carreport.py). A classic script, loaded after corners.js (it uses SR_YEARS, CN, srBar, xJSON, xErr, vis). */
const CA = {data: null, year: null, session: null, kind: store.get("cakind", "corners")};
const CA_GROUP = {"Power": "#ff8a3d", "High-speed corners": "#4cc9f0", "Slow and twisty": "#c77dff"};
const CA_KIND = {corners: "all corners", slow: "slow corners", medium: "medium corners", fast: "fast corners"};
const caSgn = (v, n = 1) => v == null ? "-" : `${v > 0 ? "+" : ""}${v.toFixed(n)}`;
const caShort = t => (t || "").replace(/ F1 Team| Racing$/g, "").replace("Kick Sauber", "Sauber");

async function caLoad() {
  const ys = $("caYear");
  if (!ys.options.length) {
    let pick = store.get("cnyear", null) || store.get("sryear", null);
    if (!pick) { try { pick = ((await xJSON("/api/dataset")).compiled_years || [])[0]; } catch (e) { /* this year */ } }
    ys.innerHTML = SR_YEARS.map(y => `<option>${y}</option>`).join(""); ys.value = SR_YEARS.includes(+pick) ? pick : SR_YEARS[0];
  }
  const year = +ys.value;
  if (year !== CA.year) Object.assign(CA, {year, session: null});
  if (!CA.data) $("caOut").innerHTML = `<div class="empty">Loading…</div>`;
  try { CA.data = await xJSON(`/api/cars?year=${year}${CA.session ? "&session=" + CA.session : ""}`); } catch (e) { $("caOut").innerHTML = xErr(e); return; }
  if (+$("caYear").value !== year) return;
  if (CA.data.session) CA.session = CA.data.session.session_key;
  caRender();
}
function caPick(v) { CA.session = +v; caLoad(); }
function caKind(k) { CA.kind = k; store.set("cakind", k); caRender(); }

function caRender() {
  const d = CA.data;
  if (!d.exists) { $("caOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("chart")}<b>The ${d.year} season hasn't been compiled yet</b><br>Compile it in Explore > Season report first.</div></div>`; return; }
  if (!d.sessions.length) {
    $("caOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("activity")}<b>No qualifying telemetry for ${d.year} yet</b><br>
      Car characteristics come from the per-corner telemetry of qualifying sessions. Measure them in Explore > Corners, or run
      <code>python3 -m f1hub compile ${d.year} --telemetry</code>.</div></div>`;
    return;
  }
  safe("cars", () => {
    const opts = d.sessions.map(x => `<option value="${x.session_key}"${x.session_key === d.session.session_key ? " selected" : ""}>R${x.round} ${esc(x.circuit)}</option>`).join("");
    const kinds = Object.entries(CA_KIND).map(([k, l]) => `<button class="btn${CA.kind === k ? " primary" : ""}" onclick="caKind('${k}')">${l}</button>`).join("");
    $("caOut").innerHTML = `${d.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: the meeting you're replaying or watching delayed, and later ones, are left out.</div>` : ""}
      <div class="xpick" style="margin-bottom:12px"><select onchange="caPick(this.value)" aria-label="qualifying session">${opts}</select>
        <span class="muted">corner speed from:</span>${kinds}</div>
      <div class="two">${caScatterCard(d)}${caTable(d)}</div>
      <div style="margin-top:16px">${caStraights(d)}</div>
      <div class="two" style="margin-top:16px">${caSeasonCard(d)}${caSeasonTable(d)}</div>
      ${caCircuits(d.circuits)}
      <p class="xnote" style="margin-top:14px">Qualifying only (${d.sessions.length} measured): everyone is flat out on low fuel, so differences come from the cars and drivers, not strategy.
        A team's figure is the mean of its two drivers' typical laps (their three quickest), against the median team. Speeds are from OpenF1's ~4 samples a second, so
        single readings are good to a few km/h; differences across a whole lap and a season are steadier. Measure more rounds with <code>python3 -m f1hub compile ${d.year} --telemetry</code>.</p>`;
  });
}

/* ---- straight-line speed against corner speed ---- */
function caScatter(rows, getX, getY, opts = {}) {
  const W = 520, H = 360, P = 44;
  const xs = rows.map(getX).filter(v => v != null), ys = rows.map(getY).filter(v => v != null);
  const ex = rows.map(r => (opts.errX && opts.errX(r)) || 0), ey = rows.map(r => (opts.errY && opts.errY(r)) || 0);
  const mx = Math.max(1, ...xs.map((v, i) => Math.abs(v) + ex[i])) * 1.15, my = Math.max(1, ...ys.map((v, i) => Math.abs(v) + ey[i])) * 1.15;
  const X = v => P + (v + mx) / (2 * mx) * (W - 2 * P), Y = v => H - P - (v + my) / (2 * my) * (H - 2 * P);
  const tick = m => m > 6 ? 2 : m > 3 ? 1 : 0.5;
  let g = `<line x1="${X(0)}" x2="${X(0)}" y1="${P / 2}" y2="${H - P}" stroke="#3a3f4d"/><line x1="${P}" x2="${W - P / 2}" y1="${Y(0)}" y2="${Y(0)}" stroke="#3a3f4d"/>`;
  for (let v = -Math.floor(mx / tick(mx)) * tick(mx); v <= mx; v += tick(mx)) if (Math.abs(v) > 1e-9)
    g += `<text x="${X(v)}" y="${H - P + 16}" font-size="11" fill="#8b91a1" text-anchor="middle">${caSgn(v, tick(mx) < 1 ? 1 : 0)}</text>`;
  for (let v = -Math.floor(my / tick(my)) * tick(my); v <= my; v += tick(my)) if (Math.abs(v) > 1e-9)
    g += `<text x="${P - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${caSgn(v, tick(my) < 1 ? 1 : 0)}</text>`;
  const q = (x, y, t, a) => `<text x="${x}" y="${y}" font-size="11" fill="#5c6273" text-anchor="${a}">${t}</text>`;
  g += q(W - P / 2, P / 2 + 24, "quick everywhere", "end") + q(P + 4, P / 2 + 24, "more downforce", "start")
     + q(W - P / 2, H - P - 8, "less drag", "end") + q(P + 4, H - P - 8, "slow everywhere", "start");
  g += `<text x="${W - P / 2}" y="${H - 6}" font-size="11" fill="#8b91a1" text-anchor="end">straight-line speed, km/h →</text>
        <text x="12" y="${P / 2 - 4}" font-size="11" fill="#8b91a1">↑ ${opts.yLabel || "corner speed"}, km/h</text>`;
  const dots = rows.map((r, i) => { const x = getX(r), y = getY(r); if (x == null || y == null) return "";
    const c = vis(r.colour || "#888"), cx = X(x).toFixed(1), cy = Y(y).toFixed(1);
    const whisk = (ex[i] ? `<line x1="${X(x - ex[i]).toFixed(1)}" x2="${X(x + ex[i]).toFixed(1)}" y1="${cy}" y2="${cy}" stroke="${esc(c)}" opacity=".5" stroke-width="1.5"/>` : "")
      + (ey[i] ? `<line x1="${cx}" x2="${cx}" y1="${Y(y - ey[i]).toFixed(1)}" y2="${Y(y + ey[i]).toFixed(1)}" stroke="${esc(c)}" opacity=".5" stroke-width="1.5"/>` : "");
    return `<g>${whisk}<circle cx="${cx}" cy="${cy}" r="6" fill="${esc(c)}" stroke="#16181f" stroke-width="2"/>
      <text x="${(+cx + 9).toFixed(1)}" y="${(+cy + 4).toFixed(1)}" font-size="11" fill="#eef0f5">${esc(caShort(r.team))}</text>
      <circle cx="${cx}" cy="${cy}" r="14" fill="transparent"><title>${esc(r.team)}: ${caSgn(x)} km/h on the straights, ${caSgn(y)} km/h ${esc(opts.yLabel || "in the corners")}${ex[i] || ey[i] ? " (lines: 95%)" : ""}</title></circle></g>`; }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="straight-line speed against corner speed">${g}${dots}</svg>`;
}
function caScatterCard(d) {
  const k = CA.kind;
  return `<div class="card"><h2>${esc(d.session.circuit)}: drag v downforce <span class="sub">each team against the median team · ${CA_KIND[k]}</span></h2>
    <div class="pad">${caScatter(d.cars, r => r.straight, r => r[k], {yLabel: CA_KIND[k]})}
    <p class="xnote">Right = quicker on the straights, up = more speed through the corners. A car low and right is trimmed for low drag; high and left, for downforce.</p></div></div>`;
}

function caTable(d) {
  const cols = [["straight", "Straights", "top speed on each straight against the median team"], ["slow", "Slow", "minimum speed in slow corners"],
    ["medium", "Medium", ""], ["fast", "Fast", ""], ["accel", "Traction", "km/h gained over the first 200 m of each straight"]];
  const best = Object.fromEntries(cols.map(([k]) => [k, Math.max(...d.cars.map(r => r[k] ?? -1e9))]));
  const has = k => d.cars.some(r => r[k] != null);
  const rows = d.cars.map(r => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.team)}</div></td>
    ${cols.filter(([k]) => has(k)).map(([k]) => `<td class="r num${r[k] != null && r[k] === best[k] ? " up" : ""}">${caSgn(r[k])}</td>`).join("")}
    <td class="r num">${r.clip == null ? "-" : r.clip.toFixed(1)}</td><td class="r num">${r.plateau == null ? "-" : Math.round(r.plateau)}</td></tr>`).join("");
  const lg = (d.straights || []).find(s => s.straight === d.longest);
  return `<div class="card"><h2>Car by car <span class="sub">km/h against the median team · green = best</span></h2>
    <div class="scroll"><table><thead><tr><th>Team</th>${cols.filter(([k]) => has(k)).map(([, l, t]) => `<th class="r" title="${esc(t)}">${l}</th>`).join("")}
      <th class="r" title="speed lost at full throttle before the braking point, km/h, on the longest straight">Clip</th>
      <th class="r" title="metres flat out within ${3} km/h of top speed before braking, on the longest straight">Flat (m)</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Clip and Flat are on the longest straight${lg ? ` (${esc(lg.label)}, ${Math.round(lg.length)} m)` : ""}. Speed that stops rising, or falls, while the driver is still flat out
      is the car running out of electrical energy (clipping) or meeting its drag limit; under the 2026 rules, with half the power electric, it shows far more.</div></div>`;
}

/* ---- every straight of the session ---- */
function caStraights(d) {
  if (!d.straights || !d.straights.length) return "";
  const teams = d.cars.map(r => r.team);
  const head = teams.map(t => { const c = d.cars.find(r => r.team === t); return `<th class="r"><span class="stripe" style="background:${esc(c.colour)};display:inline-block;width:3px;height:11px;vertical-align:-1px"></span> ${esc(caShort(t))}</th>`; }).join("");
  const rows = d.straights.map(s => { const tops = teams.map(t => (s.teams[t] || {}).top).filter(v => v != null), mx = Math.max(...tops);
    return `<tr><td>${esc(s.label)}</td><td class="r num">${Math.round(s.length)}</td>${teams.map(t => { const v = s.teams[t];
      return `<td class="r num${v && v.top === mx ? " up" : ""}" title="${v ? `${esc(t)}: ${v.top} km/h, ${v.clip ?? 0} km/h lost flat out, ${v.plateau ?? 0} m flat` : ""}">${v ? Math.round(v.top) : "-"}${v && v.clip >= 2 ? `<small class="down"> −${v.clip.toFixed(0)}</small>` : ""}</td>`; }).join("")}</tr>`; }).join("");
  return `<div class="card"><h2>Straights <span class="sub">top speed, km/h · red = km/h lost while still flat out (2 or more) · green = quickest</span></h2>
    <div class="scroll"><table><thead><tr><th>Straight</th><th class="r">m</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">The pit straight is left out in qualifying: drivers back off once they cross the line.</div></div>`;
}

/* ---- the season ---- */
function caSeasonCard(d) {
  const k = CA.kind, rows = d.season.map(r => ({team: r.team, colour: r.colour, x: r.straight && r.straight.est, y: r[k] && r[k].est,
    ex: r.straight && r.straight.se ? 1.96 * r.straight.se : 0, ey: r[k] && r[k].se ? 1.96 * r[k].se : 0}));
  return `<div class="card"><h2>${d.year} season: drag v downforce <span class="sub">average over ${d.sessions.length} qualifying sessions · lines = 95% · ${CA_KIND[k]}</span></h2>
    <div class="pad">${caScatter(rows, r => r.x, r => r.y, {errX: r => r.ex, errY: r => r.ey, yLabel: CA_KIND[k]})}</div></div>`;
}
function caSeasonTable(d) {
  const cell = e => !e ? "-" : `${caSgn(e.est)}${e.se ? `<span class="muted"> ±${(1.96 * e.se).toFixed(1)}</span>` : ""}`;
  const rows = d.season.map(r => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.team)}</div></td>
    <td class="r num">${cell(r.straight)}</td><td class="r num">${cell(r.slow)}</td><td class="r num">${cell(r.medium)}</td><td class="r num">${cell(r.fast)}</td>
    <td class="r num">${cell(r.accel)}</td><td class="r num">${r.clip ? r.clip.est.toFixed(1) : "-"}</td><td class="r num">${r.straight ? r.straight.n : ""}</td></tr>`).join("");
  return `<div class="card"><h2>Car characteristics, ${d.year} <span class="sub">km/h against the median team, season average ± 95%</span></h2>
    <div class="scroll"><table><thead><tr><th>Team</th><th class="r">Straights</th><th class="r">Slow</th><th class="r">Medium</th><th class="r">Fast</th><th class="r">Traction</th><th class="r" title="km/h lost flat out on the longest straight">Clip</th><th class="r">Rounds</th></tr></thead>
    <tbody>${rows}</tbody></table></div></div>`;
}

/* ---- circuits grouped by layout, and who suits them ---- */
function caCircuits(c) {
  if (!c || !c.circuits.length) return "";
  if (!c.groups.length) return `<div class="card" style="margin-top:16px"><h2>Circuits</h2><div class="empty">Circuits are grouped by layout once four or more qualifying sessions are measured (${c.circuits.length} so far).</div></div>`;
  const W = 520, H = 320, P = 30, xs = c.circuits.map(r => r.x), ys = c.circuits.map(r => r.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const X = v => P + (v - x0) / ((x1 - x0) || 1) * (W - 2 * P - 70), Y = v => H - P - (v - y0) / ((y1 - y0) || 1) * (H - 2 * P);
  const dots = c.circuits.map(r => `<g><circle cx="${X(r.x).toFixed(1)}" cy="${Y(r.y).toFixed(1)}" r="6" fill="${CA_GROUP[r.group]}" stroke="#16181f" stroke-width="2"/>
    <text x="${(X(r.x) + 9).toFixed(1)}" y="${(Y(r.y) + 4).toFixed(1)}" font-size="11" fill="#eef0f5">${esc(r.circuit)}</text>
    <circle cx="${X(r.x).toFixed(1)}" cy="${Y(r.y).toFixed(1)}" r="14" fill="transparent"><title>${esc(r.circuit)} (${esc(r.group)}): ${r.full_throttle}% full throttle, ${r.corners} corners, average corner ${r.corner_speed} km/h, top ${r.top_speed} km/h, longest straight ${r.longest} m</title></circle></g>`).join("");
  const load = c.pca.loadings[0], top = Object.entries(load).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 2);
  const fname = Object.fromEntries(c.features.map(f => [f[0], f[1].toLowerCase()]));
  const feats = c.features.map(f => f[0]);
  const rows = [...c.circuits].sort((a, b) => a.group.localeCompare(b.group) || b.full_throttle - a.full_throttle).map(r => `<tr><td class="pos">R${r.round}</td><td>${esc(r.circuit)}</td>
    <td><span class="dot" style="background:${CA_GROUP[r.group]};display:inline-block;width:8px;height:8px;border-radius:50%"></span> ${esc(r.group)}</td>
    ${feats.map(f => `<td class="r num">${r[f]}</td>`).join("")}</tr>`).join("");
  const groups = c.groups.map(g => g.name);
  const suits = c.suits.map(t => { const vals = groups.map(g => t.groups[g] && t.groups[g].est), best = Math.min(...vals.filter(v => v != null));
    return `<tr><td><div class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.team)}</div></td>
      ${groups.map((g, i) => { const e = t.groups[g]; return `<td class="r num${vals[i] === best ? " up" : ""}">${e ? `+${e.est.toFixed(2)}%${e.se ? `<span class="muted"> ±${(1.96 * e.se).toFixed(2)}</span>` : ""}` : "-"}<small class="muted"> ${e ? e.n : ""}</small></td>`; }).join("")}
      <td class="r num">${t.r_full_throttle == null ? "-" : caSgn(t.r_full_throttle, 2)}</td></tr>`; }).join("");
  return `<div class="two" style="margin-top:16px">
    <div class="card"><h2>Circuits by layout <span class="sub">grouped by what the fastest qualifying lap does (k-means) · nearer = more alike</span></h2>
      <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="circuits by layout">${dots}</svg>
      <div class="legend">${groups.map(g => `<span><i style="background:${CA_GROUP[g]}"></i>${esc(g)}</span>`).join("")}</div>
      <p class="xnote">Left to right mostly follows ${top.map(([k, v]) => `${v > 0 ? "more" : k.endsWith("_km") ? "fewer" : "less"} ${esc(fname[k])}`).join(" and ")} (${Math.round(c.pca.share[0] * 100)}% of the difference between circuits).</p></div></div>
    <div class="card"><h2>Which cars suit which circuits <span class="sub">qualifying gap to pole by circuit group, % ± 95% · small = rounds · green = the team's best</span></h2>
      <div class="scroll"><table><thead><tr><th>Team</th>${groups.map(g => `<th class="r">${esc(g)}</th>`).join("")}<th class="r" title="correlation of the team's gap to pole with the share of the lap at full throttle: negative = closer to pole at power circuits">r (power)</th></tr></thead>
      <tbody>${suits}</tbody></table></div>
      <div class="pad xnote">r (power) below zero: the team is closer to pole the more of the lap is flat out. Few rounds in a group make its figure rough.</div></div></div>
    <div class="card" style="margin-top:16px"><h2>Circuit layouts <span class="sub">from the fastest qualifying lap</span></h2><div class="scroll"><table><thead><tr><th></th><th>Circuit</th><th>Group</th>
      ${c.features.map(f => `<th class="r" title="${esc(f[2])}">${esc(f[1])}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
