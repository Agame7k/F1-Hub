/* F1 Hub dashboard - Explore > Strategy & predictions (f1hub/strategyreport.py): the best strategies for a race,
   its pit calls graded against the simulation, race predictions scored after the event, title odds round by round,
   and the inputs (pit loss, Safety Car chance, overtaking). A classic script, loaded after cars.js (it uses
   SR_YEARS, srBar, xJSON, xErr, vis, esc, ico, safe, store). */
const SG = {data: null, year: null, round: null, grading: false};
const SG_SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"];   // validated on the dark surface
const SG_TYRE = {S: "#e8002d", M: "#ffd12e", H: "#eeeeee"};
const sgPct = (v, n = 0) => v == null ? "-" : `${(100 * v).toFixed(n)}%`;
const sgTyres = lab => (lab || "").split("-").map(c => `<span class="sgtyre" style="border-color:${SG_TYRE[c] || "#888"}">${c}</span>`).join("");

async function sgLoad(grade) {
  const ys = $("sgYear");
  if (!ys.options.length) {
    let pick = store.get("sgyear", null) || store.get("sryear", null);
    if (!pick) { try { pick = ((await xJSON("/api/dataset")).compiled_years || [])[0]; } catch (e) { /* this year */ } }
    ys.innerHTML = SR_YEARS.map(y => `<option>${y}</option>`).join(""); ys.value = SR_YEARS.includes(+pick) ? pick : SR_YEARS[0];
  }
  const year = +ys.value;
  store.set("sgyear", year);
  if (year !== SG.year) Object.assign(SG, {year, round: null, data: null});
  if (!SG.data) $("sgOut").innerHTML = `<div class="empty">${ico("loader")}Simulating the season… the first time takes about half a minute, then it is kept.</div>`;
  if (grade) { SG.grading = true; sgRender(); }
  try { SG.data = await xJSON(`/api/strategy?year=${year}${SG.round ? "&round=" + SG.round : ""}${grade ? "&grade=1" : ""}`); }
  catch (e) { SG.grading = false; $("sgOut").innerHTML = xErr(e); return; }
  SG.grading = false;
  if (+$("sgYear").value !== year) return;
  if (SG.data.race) SG.round = SG.data.race.round;
  sgRender();
}
function sgPick(v) { SG.round = +v; sgLoad(); }

function sgRender() {
  const d = SG.data;
  if (!d) return;
  if (!d.exists) { $("sgOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("chart")}<b>The ${d.year} season hasn't been compiled yet</b><br>Compile it in Explore > Season report first.</div></div>`; return; }
  if (!d.races.length) { $("sgOut").innerHTML = `<div class="card"><div class="empty">No races in the ${d.year} dataset yet.</div></div>`; return; }
  safe("strategy", () => {
    const s = d.season, r = d.race;
    const opts = d.races.map(x => `<option value="${x.round}"${x.round === r.round ? " selected" : ""}>R${x.round} ${esc(x.name)}</option>`).join("");
    $("sgOut").innerHTML = `${d.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: the meeting you're replaying or watching delayed, and later ones, are left out.</div>` : ""}
      <div class="xpick" style="margin-bottom:12px"><select onchange="sgPick(this.value)" aria-label="race">${opts}</select></div>
      ${r.skipped ? `<div class="card"><div class="empty">${ico("rain")}No strategy simulation for this race: ${esc(r.skipped)}.</div></div>`
        : `<div class="two">${sgPlanCard(r)}${sgSeqCard(r)}</div>`}
      <div class="two" style="margin-top:16px">${sgPredCard(r.prediction)}${sgGradeCard(r)}</div>
      <div class="srsec"><b>Across the season</b>Predictions made before every race and scored afterwards, title odds round by round, and how good the pit calls were.</div>
      ${sgTitleCard(s, d.upto)}
      <div class="two" style="margin-top:16px">${sgScoreCard(s.backtest.score)}${sgGradedCard(d.graded, d.year)}</div>
      <div class="srsec"><b>What the simulation knows</b>Measured from every race of the season so far.</div>
      <div class="two">${sgInputsCard(s)}${sgPassCard(s.pass)}</div>
      <p class="xnote" style="margin-top:14px">Each simulated race runs lap by lap: every car's lap is its pace, plus fuel, tyre compound and wear, plus random variation; a car that catches the one ahead
        gets past with the overtaking model's chance or is held up; Safety Cars and VSCs arrive at each circuit's rate, bunch the field and make stops cheaper. Strategies and pit calls use the
        pace and tyre wear measured over the whole race (hindsight: teams had to estimate them); predictions use only what was known before the start.</p>`;
  });
}

/* ---- strategies: time lost against the best, by first stop lap ---- */
function sgPlanCard(r) {
  const p = r.plan, names = Object.keys(p.curves).sort((a, b) => (p.sequences.find(s => s.label === a) || {}).loss - (p.sequences.find(s => s.label === b) || {}).loss);
  if (!names.length) return `<div class="card"><h2>Strategies</h2><div class="empty">No viable strategy found.</div></div>`;
  const W = 560, H = 300, L = 46, R = 70, T = 14, B = 34, cap = 20;
  const all = names.flatMap(k => p.curves[k]), x0 = Math.min(...all.map(v => v[0])), x1 = Math.max(...all.map(v => v[0]));
  const lo = Math.min(...all.map(v => v[1]));
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = v => T + Math.min(v - lo, cap) / cap * (H - T - B);
  let g = "";
  for (let v = 0; v <= cap; v += 5) g += `<line x1="${L}" x2="${W - R}" y1="${Y(lo + v)}" y2="${Y(lo + v)}" stroke="#2a2e3a"/><text x="${L - 6}" y="${Y(lo + v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${v ? "+" + v + " s" : "best"}</text>`;
  for (let v = Math.ceil(x0 / 5) * 5; v <= x1; v += 5) g += `<text x="${X(v)}" y="${H - B + 16}" font-size="11" fill="#8b91a1" text-anchor="middle">${v}</text>`;
  g += `<text x="${W - R}" y="${H - 4}" font-size="11" fill="#8b91a1" text-anchor="end">lap of the first stop →</text>`;
  const lines = names.map((k, i) => { const c = SG_SERIES[i % SG_SERIES.length], pts = p.curves[k];
    const best = pts.reduce((a, b) => b[1] < a[1] ? b : a), last = pts[pts.length - 1];
    const hits = pts.map(v => `<circle cx="${X(v[0]).toFixed(1)}" cy="${Y(v[1]).toFixed(1)}" r="7" fill="transparent"><title>${k}, first stop on lap ${v[0]}: ${v[1] - lo < 0.05 ? "the quickest" : "+" + (v[1] - lo).toFixed(1) + " s"} (free air, no Safety Car)</title></circle>`).join("");
    return `<polyline fill="none" stroke="${c}" stroke-width="2" points="${pts.map(v => `${X(v[0]).toFixed(1)},${Y(v[1]).toFixed(1)}`).join(" ")}"/>
      <circle cx="${X(best[0]).toFixed(1)}" cy="${Y(best[1]).toFixed(1)}" r="4.5" fill="${c}" stroke="#16181f" stroke-width="2"/>
      <text x="${(X(last[0]) + 6).toFixed(1)}" y="${(Y(last[1]) + 4).toFixed(1)}" font-size="11" fill="#eef0f5">${k}</text>${hits}`; }).join("");
  const legend = names.map((k, i) => `<span><i style="background:${SG_SERIES[i % SG_SERIES.length]}"></i>${k}</span>`).join("");
  return `<div class="card"><h2>Strategies <span class="sub">time lost against the best plan, by when the first stop is made · free air, no Safety Car · dot = best lap for that plan</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="time lost by first stop lap for each strategy">${g}${lines}</svg>
    <div class="legend">${legend}<span class="muted">S soft · M medium · H hard; curves above +${cap} s are cut off</span></div></div></div>`;
}
function sgSeqCard(r) {
  const p = r.plan, ran = Object.fromEntries(r.actual);
  const rows = p.sequences.slice(0, 8).map((x, i) => `<tr><td class="pos">${i + 1}</td><td>${sgTyres(x.label)}</td>
      <td class="muted">${x.stops.map(s => `lap ${s[0]}`).join(", ")}</td><td class="r num">${x.loss ? "+" + x.loss.toFixed(1) + " s" : "<b>best</b>"}</td>
      <td class="r num">${ran[x.label] || ""}</td></tr>`).join("");
  const other = r.actual.filter(a => !p.sequences.slice(0, 8).some(x => x.label === a[0])).map(a => `${a[0]} ×${a[1]}`).join(", ");
  const tyres = Object.entries(r.compounds).map(([c, v]) => `${c[0]} ${v[0] >= 0 ? "+" : ""}${v[0].toFixed(2)} s, ${v[1].toFixed(3)} s/lap wear, up to ${r.max_stint[c]} laps`).join(" · ");
  return `<div class="card"><h2>Best plan for each tyre sequence <span class="sub">expected race time against the best, with Safety Car risk · ${p.laps} laps · pit loss ${p.pit_loss.toFixed(1)} s (${p.pit_loss_sc.toFixed(1)} s under SC)</span></h2>
    <div class="scroll"><table><thead><tr><th></th><th>Tyres</th><th>Stops</th><th class="r">Cost</th><th class="r" title="cars that ran this sequence">Cars</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">${other ? `Also run: ${esc(other)}. ` : ""}Tyres as measured in this race (offset against the reference compound, wear): ${esc(tyres)}.</div></div>`;
}

/* ---- the prediction made before this race ---- */
function sgPredCard(pr) {
  if (!pr) return `<div class="card"><h2>Prediction before the race</h2><div class="empty">No prediction: no qualifying result for this race.</div></div>`;
  const rows = pr.drivers.slice(0, 12).map(x => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(x.colour || "#888")}"></span>${esc(x.code || x.number)}</div></td>
      <td class="r num">${x.grid || "pit"}</td>
      <td style="width:22%">${srBar(x.win * 100, vis(x.colour || "#888"), `${x.code}: ${sgPct(x.win, 1)} to win, the grid baseline says ${sgPct(x.base_win, 1)}`)}</td>
      <td class="r num">${sgPct(x.win)}</td><td class="r num muted">${sgPct(x.base_win)}</td><td class="r num">${sgPct(x.podium)}</td><td class="r num muted">${sgPct(x.base_podium)}</td>
      <td class="r num">${x.finish ? (x.finish <= 3 ? `<b>P${x.finish}</b>` : "P" + x.finish) : `<span class="muted">${esc(x.status)}</span>`}</td></tr>`).join("");
  return `<div class="card"><h2>Prediction before the race <span class="sub">from earlier races' pace, this qualifying and the grid · grey: grid-position baseline</span></h2>
    <div class="scroll"><table><thead><tr><th>Driver</th><th class="r">Grid</th><th>Win</th><th class="r"></th><th class="r">base</th><th class="r">Podium</th><th class="r">base</th><th class="r">Result</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

/* ---- pit calls ---- */
function sgGradeCard(r) {
  if (r.skipped) return `<div class="card"><h2>Pit calls</h2><div class="empty">Not graded: ${esc(r.skipped)}.</div></div>`;
  const g = r.grade;
  if (!g || !g.stops) return `<div class="card"><h2>Pit calls</h2><div class="empty" style="padding:28px 14px">${SG.grading ? `${ico("loader")}Simulating every stop and its alternatives (about ten seconds)…`
    : `Every stop of the race against stopping 2 or 4 laps later, on another tyre, or not at all, from the state of the race at the time.<br><br><button class="btn primary" onclick="sgLoad(true)">Grade the pit calls</button>`}</div></div>`;
  const rows = g.stops.map(x => { const opt = x.options.filter(o => o.option !== "as run").map(o => `${o.option}: ${o.vs_actual > 0 ? "+" : ""}${o.vs_actual.toFixed(2)} ± ${(1.96 * o.se).toFixed(2)}`).join("\n");
    return `<tr title="${esc("change in expected finishing position against the call made (negative = better), ± 95%:\n" + opt)}">
      <td><div class="drv"><span class="stripe" style="background:${esc(x.colour || "#888")}"></span>${esc(x.code || x.number)}</div></td><td class="r num">${x.lap}</td>
      <td>${sgTyres(x.from[0])} <span class="muted">${x.age}</span> → ${sgTyres(x.to[0])}</td><td class="r num">P${x.position_before}</td><td class="r num">${x.expected.toFixed(1)}</td>
      <td>${x.cost ? `<span class="sgwarn">${esc(x.best)}</span>` : `<span class="muted">as run</span>`}</td><td class="r num">${x.cost ? x.cost.toFixed(1) : ""}</td></tr>`; }).join("");
  return `<div class="card"><h2>Pit calls <span class="sub">expected finishing position for the call made, and the best alternative where it is clearly better (by 2 standard errors) · hover for every option</span></h2>
    <div class="scroll" style="max-height:420px"><table><thead><tr><th>Driver</th><th class="r">Lap</th><th>Tyres</th><th class="r" title="position on track before the stop">Before</th>
      <th class="r" title="expected finishing position for the call made">Expected</th><th>Better</th><th class="r" title="places gained by the better option">Gain</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">${g.runs} simulated races per option, from the lap before the stop, every other car on the plan it actually ran. Hindsight: the pace and tyre wear are the race's own, which the teams could only estimate at the time.</div></div>`;
}

/* ---- title odds through the season ---- */
function sgTitleCard(s, upto) {
  const t = s.title, drv = s.drivers;
  if (!t.length) return "";
  const peak = {};
  t.forEach(x => Object.entries(x.drivers).forEach(([n, p]) => { peak[n] = Math.max(peak[n] || 0, p); }));
  const who = Object.keys(peak).filter(n => peak[n] >= 0.1).sort((a, b) => peak[b] - peak[a]).slice(0, 6);
  const W = 900, H = 260, L = 44, R = 60, T = 12, B = 28;
  const x0 = t[0].after, x1 = t[t.length - 1].after;
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = v => T + (1 - v) * (H - T - B);
  let g = "";
  for (let v = 0; v <= 1.0001; v += 0.25) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2e3a"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${Math.round(v * 100)}%</text>`;
  t.forEach((x, i) => { if (i % Math.max(1, Math.ceil(t.length / 12)) === 0) g += `<text x="${X(x.after)}" y="${H - 8}" font-size="11" fill="#8b91a1" text-anchor="middle">R${x.after}</text>`; });
  const seen = {}, labY = {};
  who.filter(n => (t[t.length - 1].drivers[n] || 0) >= 0.01).map(n => [n, Y(t[t.length - 1].drivers[n] || 0)]).sort((a, b) => a[1] - b[1])     // end labels: pushed apart, 13 px
    .forEach(([n, y], i, a) => { labY[n] = i ? Math.max(y, labY[a[i - 1][0]] + 13) : y; });
  const lines = who.map(n => { const d = drv[n] || {}, c = vis(d.colour || "#888"), dash = seen[d.team] ? ' stroke-dasharray="6 4"' : ""; seen[d.team] = 1;
    const pts = t.map(x => [x.after, x.drivers[n] || 0]), last = pts[pts.length - 1];
    const hits = pts.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="8" fill="transparent"><title>${esc(d.code || n)} after R${p[0]}: ${sgPct(p[1], 1)} to be champion (${t.find(x => x.after === p[0]).points[n] || 0} points)</title></circle>`).join("");
    return `<polyline fill="none" stroke="${esc(c)}" stroke-width="2"${dash} points="${pts.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")}"/>
      ${labY[n] == null ? "" : `<text x="${(X(last[0]) + 6).toFixed(1)}" y="${(labY[n] + 4).toFixed(1)}" font-size="11" fill="#eef0f5">${esc(d.code || n)} ${sgPct(last[1])}</text>`}${hits}`; }).join("");
  const seen2 = {};
  const legend = who.map(n => { const d = drv[n] || {}, dash = seen2[d.team]; seen2[d.team] = 1;
    return `<span><i style="background:${dash ? `repeating-linear-gradient(90deg,${esc(vis(d.colour || "#888"))} 0 4px,transparent 4px 7px)` : esc(vis(d.colour || "#888"))}"></i>${esc(d.code || n)}</span>`; }).join("");
  const now = t[t.length - 1], teams = Object.entries(now.teams).sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([k, p]) => `<span><span class="dot" style="background:${esc(s.team_colour[k] || "#888")}"></span>${esc(k)} ${sgPct(p)}</span>`).join("");
  return `<div class="card"><h2>Drivers' title chances after each round <span class="sub">points so far plus ${"the rest of the season simulated"} 1,500 times from each driver's race pace at the time · dashed: second driver of a team</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="title chances by round">${g}${lines}</svg>
    <div class="legend">${legend}</div>
    <div class="srh">Constructors' title after R${now.after} <span class="muted">(${now.events_left ? now.events_left + " races and sprints left" : "final"})</span></div><div class="srlist">${teams}</div></div></div>`;
}

/* ---- how good the predictions were ---- */
function sgCalib(rows, base, label) {
  const W = 270, H = 230, P = 34, X = v => P + v * (W - P - 22), Y = v => H - P - v * (H - P - 26);
  let g = `<line x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}" stroke="#3a3f4d" stroke-dasharray="4 4"/>`;
  [0, 0.5, 1].forEach(v => { g += `<text x="${X(v)}" y="${H - P + 15}" font-size="10" fill="#8b91a1" text-anchor="middle">${v * 100}%</text><text x="${P - 5}" y="${Y(v) + 3}" font-size="10" fill="#8b91a1" text-anchor="end">${v * 100}%</text>`; });
  const dots = (list, c, name) => list.map(b => `<circle cx="${X(b.predicted).toFixed(1)}" cy="${Y(b.observed).toFixed(1)}" r="${Math.max(4, Math.min(9, Math.sqrt(b.n))).toFixed(1)}" fill="${c}" stroke="#16181f" stroke-width="2" opacity=".9"><title>${name}: predicted ${sgPct(b.lo)}-${sgPct(b.hi)} (mean ${sgPct(b.predicted, 1)}), happened ${sgPct(b.observed, 1)} of ${b.n}</title></circle>`).join("");
  return `<div><div class="srh">${label}</div><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;max-width:300px" role="img" aria-label="${label} calibration">${g}${dots(base, SG_SERIES[1], "grid baseline")}${dots(rows, SG_SERIES[0], "simulation")}
    <text x="${W - 8}" y="${H - 4}" font-size="10" fill="#8b91a1" text-anchor="end">predicted →</text><text x="${P}" y="11" font-size="10" fill="#8b91a1" text-anchor="middle">↑ happened</text></svg></div>`;
}
function sgScoreCard(sc) {
  const line = (k, lab) => { const x = sc[k]; return `<tr><td>${lab}</td><td class="r num">${x.brier.toFixed(4)}</td><td class="r num muted">${x.baseline.toFixed(4)}</td>
    <td class="r num${x.skill > 0 ? " up" : ""}">${x.skill > 0 ? "+" : ""}${(100 * x.skill).toFixed(1)}%</td></tr>`; };
  return `<div class="card"><h2>How good the predictions were <span class="sub">${sc.races} races, each predicted before the start · Brier score: lower is better</span></h2>
    <div class="scroll"><table><thead><tr><th></th><th class="r">Simulation</th><th class="r">Grid baseline</th><th class="r" title="1 - simulation / baseline">Skill</th></tr></thead>
      <tbody>${line("win", "Win")}${line("podium", "Podium")}</tbody></table></div>
    <div class="pad"><div class="two" style="gap:8px">${sgCalib(sc.win.calibration, sc.win.calibration_base, "Win")}${sgCalib(sc.podium.calibration, sc.podium.calibration_base, "Podium")}</div>
    <div class="legend"><span><i style="background:${SG_SERIES[0]}"></i>simulation</span><span><i style="background:${SG_SERIES[1]}"></i>grid baseline</span><span class="muted">on the dashed line = well calibrated · bigger dot = more cases</span></div>
    <div class="xnote" style="margin-top:8px">The favourite won ${sc.favourite_won} of ${sc.races}. The baseline is how often each grid slot has won (or made the podium) earlier in the season.
      Race-day form, measured from how far drivers' race pace strayed from the prediction: ${sc.form_sd == null ? "-" : sc.form_sd.toFixed(2) + "% of a lap"} (one standard deviation).</div></div></div>`;
}
function sgGradedCard(g, year) {
  if (!g.races) return `<div class="card"><h2>Pit calls by team</h2><div class="empty" style="padding:28px 14px">No race graded yet. Grade a race above, or every race with<br><code>python3 -m f1hub strategy ${year} --grade</code> (about four minutes, kept).</div></div>`;
  const max = Math.max(0.1, ...g.teams.map(t => t.per_stop));
  const rows = g.teams.map(t => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(t.colour || "#888")}"></span>${esc(t.team)}</div></td>
      <td class="r num">${t.stops}</td><td class="r num">${t.clear}</td><td style="width:30%">${srBar(t.per_stop / max * 100, vis(t.colour || "#888"), `${t.team}: ${t.lost} positions lost to clearly better options over ${t.stops} stops`)}</td>
      <td class="r num">${t.per_stop.toFixed(2)}</td></tr>`).join("");
  return `<div class="card"><h2>Pit calls by team <span class="sub">${g.races} of ${g.of} races graded${g.of > g.gradable ? ` (${g.of - g.gradable} wet, not modelled)` : ""} · positions lost to a clearly better option, per stop</span></h2>
    <div class="scroll"><table><thead><tr><th>Team</th><th class="r">Stops</th><th class="r" title="stops with a clearly better option">Costly</th><th></th><th class="r">Per stop</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${g.races < g.gradable ? `<div class="pad xnote">Grade the rest with <code>python3 -m f1hub strategy ${year} --grade</code>.</div>` : ""}</div>`;
}

/* ---- inputs ---- */
function sgInputsCard(s) {
  const max = Math.max(...s.inputs.map(x => x.pit_loss));
  const rows = s.inputs.map(x => `<tr><td class="pos">R${x.round}</td><td>${esc(x.circuit)}</td>
      <td style="width:20%">${srBar(x.pit_loss / max * 100, "#8b91a1", `${x.circuit}: ${x.pit_loss} s from ${x.stops} green-flag stops`)}</td><td class="r num">${x.pit_loss.toFixed(1)}</td>
      <td class="r num muted">${x.pit_loss_sc.toFixed(1)}</td><td class="r num" title="${x.sc_seen} Safety Car(s) and ${x.vsc_seen} VSC(s) this year">${sgPct(x.sc_race)}</td>
      <td class="r num${x.passing > 0.2 ? " up" : ""}">${x.passing == null ? `<span class="muted" title="no laps modelled (a wet race)">-</span>` : (x.passing > 0 ? "+" : "") + x.passing.toFixed(2)}</td></tr>`).join("");
  return `<div class="card"><h2>Circuits <span class="sub">pit loss (s), green and under a Safety Car · chance of a Safety Car in the race · how easy passing is</span></h2>
    <div class="scroll" style="max-height:460px"><table><thead><tr><th></th><th>Circuit</th><th></th><th class="r">Pit loss</th><th class="r">Under SC</th><th class="r">SC</th>
      <th class="r" title="the circuit's effect on the chance of a pass (log-odds): positive = easier than average">Passing</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">Pit loss: in-lap plus out-lap against the cars that didn't stop on the same laps (median over the race's green-flag stops; the season's ${s.pit_loss.toFixed(1)} s with fewer than three).
      Under a Safety Car a stop costs ${Math.round(s.sc_ratio * 100)}% of that, measured from stops made with the Safety Car already out. Safety Car chance: one race says little, so each circuit's
      record is blended with the season's rate (${(s.sc_rate * 100).toFixed(1)}% of laps).</div></div>`;
}
function sgPassCard(pm) {
  const W = 520, H = 240, L = 44, R = 12, T = 12, B = 34, c = pm.curve;
  const X = v => L + (v + 1) / 4 * (W - L - R), Y = v => T + (1 - v) * (H - T - B);
  let g = "";
  for (let v = 0; v <= 1.0001; v += 0.25) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2e3a"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${Math.round(v * 100)}%</text>`;
  for (let v = -1; v <= 3; v++) g += `<text x="${X(v)}" y="${H - B + 16}" font-size="11" fill="#8b91a1" text-anchor="middle">${v > 0 ? "+" : ""}${v} s</text>`;
  g += `<text x="${W - R}" y="${H - 4}" font-size="11" fill="#8b91a1" text-anchor="end">pace advantage of the car behind, s/lap →</text>`;
  const hits = c.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="6" fill="transparent"><title>${p[0] > 0 ? "+" : ""}${p[0].toFixed(1)} s/lap quicker: ${sgPct(p[1], 1)} chance of passing on the next lap (average circuit)</title></circle>`).join("");
  const easy = Object.entries(pm.circuits), top = easy.slice(0, 3).map(e => e[0]).join(", "), hard = easy.slice(-3).reverse().map(e => e[0]).join(", ");
  return `<div class="card"><h2>Overtaking <span class="sub">chance the car within a second behind gets past on the next lap · from ${pm.n.toLocaleString()} such laps, ${pm.passes} passes</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="chance of a pass by pace advantage">${g}
      <polyline fill="none" stroke="${SG_SERIES[0]}" stroke-width="2" points="${c.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ")}"/>${hits}</svg>
    <div class="xnote">Pace advantage is the lap-time model's prediction for both cars on their tyres at the time (driver pace, compound, wear). A logistic model with a shrunk effect per circuit:
      easiest ${esc(top)}; hardest ${esc(hard)}.</div></div></div>`;
}
