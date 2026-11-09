/* F1 Hub dashboard - Explore > Season report: the lap-time model section (f1hub/lapmodel.py).
   A classic script, loaded after seasonreport.js (it uses SR, TYRE_COL, srTrendCard, srBar). Every estimate comes
   with a standard error; intervals drawn and printed are 95% (1.96 standard errors). */
const LM_TYRES = ["SOFT", "MEDIUM", "HARD"];
const lmCi = e => e && e.se ? 1.96 * e.se : 0;
const lmS = (e, n = 3, unit = "s") => !e || e.est == null ? "-" : `${e.est > 0 ? "+" : ""}${e.est.toFixed(n)}${unit}`;
const lmPm = (e, n = 3, unit = "s") => !e || e.est == null ? "-"
  : `${lmS(e, n, unit)}${e.se ? ` <span class="muted">± ${lmCi(e).toFixed(n)}</span>` : e.fixed ? ' <span class="muted">fixed</span>' : ""}`;
const lmTip = (e, n = 3, unit = " s") => !e || e.est == null ? "" : `${e.est.toFixed(n)}${unit}${e.se ? ` (95% ${(e.est - lmCi(e)).toFixed(n)} to ${(e.est + lmCi(e)).toFixed(n)})` : e.fixed ? " (fixed)" : ""}`;
const lmTyre = c => `<span class="tyre t-${esc(c)}">${esc(c[0])}</span>`;

function lmSection(r) {
  const m = r.model;
  if (!m) return "";
  const ok = m.races.filter(f => !f.skipped);
  if (!ok.length) return `<div class="card" style="margin-top:16px"><h2>Lap-time model</h2><div class="empty">No race has enough clean dry laps to model yet.</div></div>`;
  return `<div class="srsec"><b>Lap-time model</b> every clean dry race lap split into driver pace, fuel and track, tyre compound and age,
      and dirty air, race by race. ± is a 95% interval: a gap smaller than it is noise.</div>
    <div class="two">${srTrendCard("model", "Race pace, corrected", "quicker car per team, % off the best · fuel, tyres and dirty air taken out · whiskers 95%", m.pace)}
      ${lmSeason(m)}</div>
    <div style="margin-top:16px">${lmRaces(m)}</div>
    <div class="two" style="margin-top:16px">${lmRace(m)}<div class="stack">${lmDirty(m)}${lmDegTemp(m)}</div></div>`;
}

/* ---- the season in numbers ---- */
function lmSeason(m) {
  const deg = LM_TYRES.filter(c => m.deg[c]).map(c => `<div><b>${lmPm(m.deg[c], 3, "")}</b><span>${lmTyre(c)} s per lap of tyre age</span></div>`).join("");
  const steps = m.pace.teams.filter(t => t.step).map(t => `<span><i class="dot" style="background:${esc(vis(t.colour))}"></i><b>${esc(t.team)}</b>
      ${t.step.change < 0 ? "gained" : "lost"} ${Math.abs(t.step.change).toFixed(2)}% from R${t.step.round}
      <span class="muted">(${srPct(t.step.before)} → ${srPct(t.step.after)})</span></span>`).join("");
  const cliffs = m.cliffs.map(c => `<span>${lmTyre(c.compound)} <b>R${c.round} ${esc(c.circuit)}</b> after ${c.age} laps:
      +${c.extra.toFixed(2)} s/lap more <span class="muted">± ${(1.96 * c.se).toFixed(2)}</span></span>`).join("");
  const skipped = m.races.filter(f => f.skipped).map(f => `R${f.round} ${esc(f.circuit)}`).join(", ");
  const fixed = m.races.filter(f => !f.skipped && f.fuel.fixed).map(f => `R${f.round} ${esc(f.circuit)}`).join(", ");
  return `<div class="card"><h2>The season in the model <span class="sub">every race pooled, weighted by how sure each one is</span></h2>
    <div class="pad"><div class="facts">
      <div><b>${lmPm(m.fuel, 3, "")}</b><span>s per lap from fuel burn and track grip</span></div>
      <div><b>${lmPm(m.traffic, 2, "")}</b><span>s a lap in dirty air (within ${m.settings.traffic} s)</span></div>${deg}</div>
    <h3 class="srh">Steps in pace</h3>
    <div class="srlist col">${steps || `<span class="muted">No team's corrected pace changed clearly at one point in the season (a step needs a t-statistic of 3 or more, with 3 races either side).</span>`}</div>
    <h3 class="srh">Tyre cliffs</h3>
    <div class="srlist col">${cliffs || `<span class="muted">No clear cliff: degradation stayed close to a straight line at every race.</span>`}</div>
    <p class="xnote" style="margin-top:12px">Fuel burn and the track rubbering in can't be separated, so they are one figure.
      Not modelled: ${skipped || "none"}${skipped ? " (wet, or too few clean laps)" : ""}.${fixed ? ` Fuel fixed at the season figure: ${fixed} (one stint each, so fuel and tyre age coincide); their tyre figures are left out of the season numbers.` : ""}</p></div></div>`;
}

/* ---- race by race: degradation per compound, fuel and dirty air ---- */
function lmRaces(m) {
  const ok = m.races.filter(f => !f.skipped), lo = -0.05, hi = 0.2, W = 300, X = v => 6 + (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo) * (W - 12);
  const strip = f => {
    const marks = LM_TYRES.map((c, i) => { const e = f.compounds.find(x => x.compound === c);
      if (!e) return ""; const y = 6 + i * 8, ci = lmCi(e.deg), faint = f.fuel.fixed;
      const off = e.deg.est < lo || e.deg.est > hi ? " (off the scale)" : "";
      return `<g opacity="${faint ? .45 : 1}"><line x1="${X(e.deg.est - ci).toFixed(1)}" x2="${X(e.deg.est + ci).toFixed(1)}" y1="${y}" y2="${y}" stroke="${TYRE_COL[c]}" stroke-width="2"/>
        <circle cx="${X(e.deg.est).toFixed(1)}" cy="${y}" r="3.5" fill="${TYRE_COL[c]}" stroke="#16181f" stroke-width="1.5"/>
        <rect x="0" y="${y - 4}" width="${W}" height="8" fill="transparent"><title>${c.toLowerCase()}: ${lmTip(e.deg)} per lap of age, ${e.laps} laps, ages ${e.ages[0]}-${e.ages[1]}${off}${e.cliff ? ` · cliff after ${e.cliff.age} laps` : ""}${faint ? " · fuel fixed, so this includes the track drying or rubbering in" : ""}</title></rect></g>`; }).join("");
    return `<svg viewBox="0 0 ${W} 28" width="100%" style="display:block"><line x1="${X(0)}" x2="${X(0)}" y1="0" y2="28" stroke="#3a3f4d"/>${marks}</svg>`;
  };
  const axis = [-0.05, 0, 0.05, 0.1, 0.15, 0.2].map(v => `<span style="left:${(X(v) / W * 100).toFixed(1)}%">${v === 0 ? "0" : v.toFixed(2)}${v === hi ? "+" : ""}</span>`).join("");
  const maxT = Math.max(...ok.map(f => f.traffic ? f.traffic.est : 0), 0.5);
  const rows = m.races.map(f => f.skipped
    ? `<tr><td class="pos">R${f.round}</td><td>${esc(f.circuit)}</td><td colspan="5" class="muted">not modelled: ${esc(f.skipped)}</td></tr>`
    : `<tr class="click${SR.race === f.round ? " on" : ""}" onclick="lmPickRace(${f.round})"><td class="pos">R${f.round}</td><td>${esc(f.circuit)}</td>
      <td style="width:34%">${strip(f)}</td><td class="r num">${lmPm(f.fuel, 3, "")}</td>
      <td style="width:12%">${f.traffic ? srBar(Math.max(0, f.traffic.est) / maxT * 100, "#8b91a1", `dirty air ${lmTip(f.traffic, 2)} a lap, ${f.traffic.laps} laps`) : ""}</td>
      <td class="r num">${f.traffic ? lmPm(f.traffic, 2, "") : "-"}</td><td class="r num">${f.sigma.toFixed(2)}</td></tr>`).join("");
  return `<div class="card"><h2>Race by race <span class="sub">degradation per compound (s per lap of tyre age, dot = estimate, line = 95%) · faded = fuel fixed · click a race for its breakdown</span></h2>
    <div class="pad" style="padding-bottom:0"><div class="legend">${LM_TYRES.map(c => `<span><i style="background:${TYRE_COL[c]};height:8px"></i>${c.toLowerCase()}</span>`).join("")}</div></div>
    <div class="scroll"><table><thead><tr><th></th><th>Circuit</th><th><div class="sraxis">${axis}</div></th><th class="r" title="s per lap: fuel burn and track grip together (negative = laps get quicker)">Fuel + track</th>
      <th colspan="2" class="r" title="extra s a lap ending within ${m.settings.traffic} s of the car ahead">Dirty air</th><th class="r" title="typical lap the model can't explain (residual standard deviation), s">Fit ±</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function lmPickRace(rnd) { SR.race = rnd; srRender(+$("srYear").value); }

/* ---- one race: corrected pace of every driver, and the tyre curves ---- */
function lmRace(m) {
  const ok = m.races.filter(f => !f.skipped);
  const f = ok.find(x => x.round === SR.race) || ok[ok.length - 1];
  const opts = ok.map(x => `<option value="${x.round}"${x === f ? " selected" : ""}>R${x.round} ${esc(x.circuit)}</option>`).join("");
  const notes = [];
  if (f.fuel.fixed) notes.push(`every driver ran one dry stint, so fuel is fixed at the season's ${f.fuel.est.toFixed(3)} s/lap and the tyre curves include any drying of the track`);
  if (f.left_out.length) notes.push(`left out: ${f.left_out.map(c => `${c.compound.toLowerCase()} (${c.laps} laps, ${c.stints} stint${c.stints > 1 ? "s" : ""})`).join(", ")}, too little to measure`);
  const noOff = f.compounds.filter(c => !c.reference && !c.offset).map(c => c.compound.toLowerCase());
  if (noOff.length) notes.push(`nobody ran the ${noOff.join(" or ")} and another compound, so its offset is left in the pace of the drivers who used it`);
  return `<div class="card"><h2>Race breakdown <select id="lmRace" onchange="lmPickRace(+this.value)" style="margin-left:8px">${opts}</select></h2>
    <div class="pad"><div class="facts">
      <div><b>${f.laps}</b><span>clean dry laps${f.trimmed ? ` (${f.trimmed} outliers out)` : ""}</span></div>
      <div><b>±${f.sigma.toFixed(2)}s</b><span>typical unexplained lap · R² ${f.r2.toFixed(2)}</span></div>
      <div><b>${lmPm(f.fuel, 3, "")}</b><span>s per lap, fuel + track</span></div>
      <div><b>${f.traffic ? lmPm(f.traffic, 2, "") : "-"}</b><span>s a lap in dirty air</span></div>
      ${f.track_temp != null ? `<div><b>${f.track_temp.toFixed(0)}°</b><span>median track temperature</span></div>` : ""}</div>
    ${notes.length ? `<p class="xnote">${notes.map(esc).join(". ")}.</p>` : ""}
    <h3 class="srh">Corrected race pace <span class="muted">gap to the quickest, s per lap · line = 95%</span></h3>${lmDrivers(f)}
    <h3 class="srh">Tyre curves <span class="muted">time lost against laps on the tyre, compared with a new ${esc((f.ref_compound || "").toLowerCase())} · band = 95%</span></h3>${lmCurves(f)}</div></div>`;
}

function lmDrivers(f) {
  const ds = f.drivers, rowH = 20, W = 560, L = 56, R = 64, T = 4, H = T + ds.length * rowH + 18;
  const hiV = Math.max(0.5, Math.min(4, Math.max(...ds.map(d => d.gap + 1.96 * (d.se || 0))))), X = v => L + Math.min(v, hiV) / hiV * (W - L - R);
  let g = "";
  const tick = hiV > 2 ? 1 : hiV > 1 ? 0.5 : 0.25;
  for (let v = 0; v <= hiV + 1e-9; v += tick)
    g += `<line x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - 16}" stroke="#2a2e3a"/><text x="${X(v)}" y="${H - 4}" font-size="11" fill="#8b91a1" text-anchor="middle">${v ? "+" + v.toFixed(2).replace(/0$/, "") : "0"}</text>`;
  const rows = ds.map((d, i) => { const y = T + i * rowH + rowH / 2, c = vis(d.colour || "#8b91a1"), ci = 1.96 * (d.se || 0);
    return `<g><text x="${L - 8}" y="${y + 4}" font-size="12" fill="#eef0f5" text-anchor="end">${esc(d.code || String(d.number))}</text>
      ${ci ? `<line x1="${X(Math.max(0, d.gap - ci)).toFixed(1)}" x2="${X(d.gap + ci).toFixed(1)}" y1="${y}" y2="${y}" stroke="${esc(c)}" stroke-width="2" opacity=".55"/>` : ""}
      <circle cx="${X(d.gap).toFixed(1)}" cy="${y}" r="4.5" fill="${esc(c)}" stroke="#16181f" stroke-width="2"/>
      <text x="${W - R + 8}" y="${y + 4}" font-size="11" fill="#8b91a1">${i ? "+" + d.gap.toFixed(3) : "quickest"}</text>
      <rect x="0" y="${y - rowH / 2}" width="${W}" height="${rowH}" fill="transparent"><title>${esc(d.code || "")} ${esc(d.team || "")}: ${i ? `+${d.gap.toFixed(3)} s a lap (95% ${Math.max(0, d.gap - ci).toFixed(3)} to ${(d.gap + ci).toFixed(3)})` : "quickest, the reference"} · ${d.laps} laps${d.gap + ci > hiV ? " · off the scale" : ""}</title></rect></g>`; }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="corrected race pace">${g}${rows}</svg>`;
}

function lmCurves(f) {
  const cs = f.compounds, W = 560, H = 220, L = 46, R = 14, T = 10, B = 26;
  const curve = (c, a) => { const off = c.offset ? c.offset.est : 0, v = off + c.deg.est * a;
    const so = c.offset && c.offset.se ? c.offset.se : 0, sd = c.deg.se || 0;
    return [v, 1.96 * Math.sqrt(Math.max(0, so * so + a * a * sd * sd + 2 * a * (c.cov || 0)))]; };
  const xMax = Math.max(...cs.map(c => c.ages[1]), 10);
  const ys = cs.flatMap(c => [c.ages[0], c.ages[1]].map(a => curve(c, a)).flatMap(([v, ci]) => [v - ci, v + ci]));
  let y0 = Math.min(0, ...ys), y1 = Math.max(0.5, ...ys);
  y0 = Math.max(y0, -2); y1 = Math.min(y1, 4);
  const X = a => L + a / xMax * (W - L - R), Y = v => T + (y1 - Math.max(y0, Math.min(y1, v))) / ((y1 - y0) || 1) * (H - T - B);
  let g = "";
  const step = (y1 - y0) > 2 ? 1 : (y1 - y0) > 1 ? 0.5 : 0.25;
  for (let v = Math.ceil(y0 / step) * step; v <= y1 + 1e-9; v += step)
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${Math.abs(v) < 1e-9 ? "#3a3f4d" : "#2a2e3a"}"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${Math.abs(v) < 1e-9 ? "0" : (v > 0 ? "+" : "") + v.toFixed(2).replace(/0$/, "")}</text>`;
  const xStep = xMax > 40 ? 10 : 5;
  for (let a = 0; a <= xMax; a += xStep) g += `<text x="${X(a)}" y="${H - 8}" font-size="11" fill="#8b91a1" text-anchor="middle">${a}</text>`;
  const lines = cs.map(c => { const col = TYRE_COL[c.compound], pts = [], band = [];
    for (let a = c.ages[0]; a <= c.ages[1]; a++) { const [v, ci] = curve(c, a); pts.push([X(a), Y(v)]); band.push([X(a), Y(v + ci), Y(v - ci)]); }
    const poly = band.map(b => `${b[0].toFixed(1)},${b[1].toFixed(1)}`).concat(band.slice().reverse().map(b => `${b[0].toFixed(1)},${b[2].toFixed(1)}`)).join(" ");
    const cliff = c.cliff ? `<line x1="${X(c.cliff.age)}" x2="${X(c.cliff.age)}" y1="${T}" y2="${H - B}" stroke="${col}" stroke-dasharray="3 3" opacity=".8"><title>${c.compound.toLowerCase()} cliff after ${c.cliff.age} laps: +${c.cliff.extra.toFixed(3)} s/lap more (± ${(1.96 * c.cliff.se).toFixed(3)})</title></line>` : "";
    const [vEnd] = curve(c, c.ages[1]);
    return `<polygon points="${poly}" fill="${col}" opacity=".13"/><polyline fill="none" stroke="${col}" stroke-width="2" points="${pts.map(p => p.map(v => v.toFixed(1)).join(",")).join(" ")}"/>${cliff}
      <polyline fill="none" stroke="transparent" stroke-width="12" points="${pts.map(p => p.map(v => v.toFixed(1)).join(",")).join(" ")}"><title>${c.compound.toLowerCase()}: ${lmTip(c.deg, 3)} per lap of age${c.reference ? " (reference compound)" : c.offset ? `, ${lmTip(c.offset, 2)} offset` : ", offset unknown"} · ${c.laps} laps · ages ${c.ages[0]}-${c.ages[1]} · at ${c.ages[1]} laps: ${vEnd > 0 ? "+" : ""}${vEnd.toFixed(2)} s</title></polyline>`; }).join("");
  const legend = cs.map(c => `<span><i style="background:${TYRE_COL[c.compound]}"></i>${c.compound.toLowerCase()} ${lmPm(c.deg, 3, "")} s/lap${c.offset && !c.reference ? `, ${lmS(c.offset, 2)} new` : c.reference ? " (reference)" : ""}</span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="tyre curves">${g}${lines}</svg>
    <div class="legend">${legend}<span class="muted">drawn over the tyre ages actually run · fuel effect taken out</span></div>`;
}

/* ---- dirty air by team, across the season ---- */
function lmDirty(m) {
  if (!m.dirty_teams.length) return "";
  const max = Math.max(...m.dirty_teams.map(t => t.est + 1.96 * t.se), 0.3), W = 240, X = v => 4 + Math.max(0, v) / max * (W - 8);
  const rows = m.dirty_teams.map(t => { const ci = 1.96 * t.se, c = vis(t.colour);
    return `<tr><td><div class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.team)}</div></td>
      <td style="width:42%"><svg viewBox="0 0 ${W} 14" width="100%" style="display:block"><rect x="4" y="4" width="${(X(t.est) - 4).toFixed(1)}" height="6" rx="3" fill="${esc(c)}" opacity=".85"/>
        <line x1="${X(t.est - ci).toFixed(1)}" x2="${X(t.est + ci).toFixed(1)}" y1="7" y2="7" stroke="#eef0f5" stroke-width="1.5"/>
        <rect width="${W}" height="14" fill="transparent"><title>${esc(t.team)}: ${lmTip(t, 2)} a lap in dirty air, ${t.laps} laps</title></rect></svg></td>
      <td class="r num"><b>${t.est.toFixed(2)}s</b> <span class="muted">± ${ci.toFixed(2)}</span></td><td class="r num">${t.laps}</td></tr>`; }).join("");
  return `<div class="card"><h2>Dirty air by team <span class="sub">extra time a lap within ${m.settings.traffic} s of the car ahead · white line = 95%</span></h2>
    <div class="scroll"><table><thead><tr><th>Team</th><th></th><th class="r">Cost</th><th class="r">Laps</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Lower is better: a car that loses less following another can race closer. A quicker car that has just caught the one ahead counts too, so these lean low.</div></div>`;
}

/* ---- does heat make tyres degrade faster? ---- */
function lmDegTemp(m) {
  const sets = LM_TYRES.filter(c => m.deg_temp[c] && m.deg_temp[c].points.length >= 3);
  if (!sets.length) return "";
  const pts = sets.flatMap(c => m.deg_temp[c].points), W = 520, H = 220, L = 46, R = 14, T = 10, B = 28;
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]).sort((a, b) => a - b);
  const x0 = Math.floor(Math.min(...xs) / 5) * 5, x1 = Math.ceil(Math.max(...xs) / 5) * 5;
  const y0 = Math.min(0, ys[0]), y1 = Math.max(0.1, Math.min(ys[ys.length - 1], 0.3));
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = v => T + (y1 - Math.max(y0, Math.min(y1, v))) / ((y1 - y0) || 1) * (H - T - B);
  let g = "";
  for (let v = 0; v <= y1 + 1e-9; v += 0.05) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${v ? "#2a2e3a" : "#3a3f4d"}"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${v ? v.toFixed(2) : "0"}</text>`;
  for (let v = x0; v <= x1; v += 5) g += `<text x="${X(v)}" y="${H - 10}" font-size="11" fill="#8b91a1" text-anchor="middle">${v}°</text>`;
  const name = Object.fromEntries(m.races.map(f => [f.round, f.circuit]));
  const dots = sets.map(c => m.deg_temp[c].points.map(([t, d, rnd]) => `<circle cx="${X(t).toFixed(1)}" cy="${Y(d).toFixed(1)}" r="4.5" fill="${TYRE_COL[c]}" stroke="#16181f" stroke-width="2">
      <title>${c.toLowerCase()} · R${rnd} ${esc(name[rnd] || "")}: ${d.toFixed(3)} s per lap of age at ${t}° track${d > y1 ? " (off the scale)" : ""}</title></circle>`).join("")).join("");
  const r = c => { const v = m.deg_temp[c].r; return v == null ? "" : ` r = ${v.toFixed(2)}`; };
  const strong = sets.filter(c => Math.abs(m.deg_temp[c].r || 0) >= 0.5);
  return `<div class="card"><h2>Degradation and track temperature <span class="sub">each dot a race · s per lap of tyre age against the median track temperature</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="degradation against track temperature">${g}${dots}</svg>
    <div class="legend">${sets.map(c => `<span><i style="background:${TYRE_COL[c]}"></i>${c.toLowerCase()}${r(c)}</span>`).join("")}</div>
    <p class="xnote">${strong.length ? `Hotter races wear the ${strong.map(c => c.toLowerCase()).join(" and ")} faster here (|r| ≥ 0.5).` : "Little link between heat and wear on its own: the circuit (surface, layout, the compounds chosen) matters more."} r is the correlation across races, from -1 to 1.</p></div></div>`;
}
