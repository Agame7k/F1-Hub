/* F1 Hub dashboard - Explore: the season report, built on the compiled season dataset (f1hub/seasonreport.py).
   A classic script, loaded after explore.js (it uses vis, xJSON, xErr, xSpoilGate). */
const SR = {data: {}, sel: {}, sort: store.get("srsort", "made"), poll: null};
const SR_YEARS = (() => { const y = new Date().getUTCFullYear(); return [...Array(y - 2022).keys()].map(i => y - i); })();
const TYRE_COL = {SOFT: "#e10600", MEDIUM: "#ffd300", HARD: "#f0f0f0", INTERMEDIATE: "#39b54a", WET: "#0067ff"};
const srPct = v => v == null ? "-" : (v > 0 ? "+" : "") + v.toFixed(2) + "%";
const srAgo = t => { const s = Date.now() / 1000 - t; return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago"
  : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago"; };

async function srLoad(force) {
  const ys = $("srYear");
  if (!ys.options.length) {      // first time: the year you looked at last, else the newest compiled one, else this year
    let pick = store.get("sryear", null);
    if (!pick) { try { pick = ((await xJSON("/api/dataset")).compiled_years || [])[0]; } catch (e) { /* this year */ } }
    ys.innerHTML = SR_YEARS.map(y => `<option>${y}</option>`).join(""); ys.value = SR_YEARS.includes(+pick) ? pick : SR_YEARS[0];
  }
  const year = +ys.value; store.set("sryear", year);
  if (force || !SR.data[year] || Date.now() - SR.data[year].at > 300000) {
    if (!SR.data[year]) $("srOut").innerHTML = `<div class="empty">Loading the ${year} season report…</div>`;
    try { SR.data[year] = Object.assign(await xJSON("/api/season-report?year=" + year), {at: Date.now()}); }
    catch (e) { $("srOut").innerHTML = xErr(e); return; }
  }
  if (+$("srYear").value !== year) return;
  srRender(year);
}

async function srCompile() {
  const year = +$("srYear").value;
  try { await xJSON("/api/dataset/compile?year=" + year); } catch (e) { $("srStatus").innerHTML = xErr(e); return; }
  srWatch(year);
}
function srWatch(year) {
  clearTimeout(SR.poll);
  SR.poll = setTimeout(async () => {
    let st; try { st = await xJSON("/api/dataset?year=" + year); } catch (e) { return; }
    const j = st.job || {};
    if (+$("srYear").value !== year) return;
    if (j.running) { $("srStatus").innerHTML = srStatusLine(st); srWatch(year); return; }
    await srLoad(true);
  }, 2000);
}
function srStatusLine(st) {
  const j = st.job || {};
  if (j.running) return `${ico("loader", "spin")} Compiling ${st.year}: ${esc(j.msg || "")}…`;
  const err = j.error ? ` <span class="down">${ico("alert")}${esc(j.error)}</span>` : "";
  const failed = (j.failed || []).length ? ` <span class="down">${j.failed.length} sessions could not be compiled</span>` : "";
  if (!st.exists) return `Not compiled yet.${err}`;
  return `${st.sessions} sessions · ${st.rounds} rounds · ${st.laps.toLocaleString()} laps · compiled ${srAgo(st.compiled_at)}${
    st.provisional ? ` · ${st.provisional} still provisional` : ""}${err}${failed}`;
}

function srRender(year) {
  const r = SR.data[year];
  const st = Object.assign({}, r.status || {year, exists: r.exists}, {job: r.job});
  $("srStatus").innerHTML = srStatusLine(st);
  $("srBtn").textContent = r.exists ? "Update" : "Compile season";
  $("srBtn").disabled = !!(r.job && r.job.running);
  if (r.job && r.job.running) srWatch(year);
  if (!r.exists) {
    $("srOut").innerHTML = `<div class="card"><div class="empty" style="padding:36px 14px">${ico("chart")}<b>The ${year} season hasn't been compiled yet</b><br>
      Compiling fetches every finished race, sprint and qualifying session once (a few minutes the first time) and keeps it in a local dataset.
      <div style="margin-top:12px"><button class="btn primary" onclick="srCompile()">Compile ${year}</button></div>
      <div class="xnote" style="margin-top:10px">Or from a terminal: <code>python3 -m f1hub compile ${year}</code></div></div></div>`;
    return;
  }
  const lr = H && H.last_race;
  if (year === new Date().getUTCFullYear() && lr && !lr.hidden && !r.spoiler_hidden) {
    const gate = xSpoilGate(lr.key, `This season's report includes the ${lr.meeting} result`, "srLoad()");
    if (gate) { $("srOut").innerHTML = gate; return; }
  }
  safe("seasonreport", () => {
    const h = r.headline;
    if (!h.rounds) { $("srOut").innerHTML = `<div class="empty">No races in the ${year} dataset yet.</div>`; return; }
    $("srOut").innerHTML = `${r.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: rounds from the meeting you're replaying or watching delayed are left out (showing up to round ${r.upto}).</div>` : ""}
      <div class="card"><h2>${year} at a glance <span class="sub">up to round ${r.upto}</span></h2><div class="pad"><div class="facts">
        <div><b>${h.rounds}</b><span>races${h.sprints ? ` + ${h.sprints} sprints` : ""}</span></div>
        <div><b>${h.winners}</b><span>different winners</span></div><div><b>${h.polesitters}</b><span>different pole-sitters</span></div>
        <div><b>${h.laps.toLocaleString()}</b><span>laps raced</span></div><div><b>${h.overtakes.toLocaleString()}</b><span>overtakes on track</span></div>
        <div><b>${h.stops}</b><span>pit stops (Grands Prix)</span></div>
        <div><b>${h.sc} / ${h.vsc} / ${h.red}</b><span>Safety Cars / VSCs / red flags</span></div></div></div></div>
      <div class="two" style="margin-top:16px">
        ${srTrendCard("race", "Race pace", "quicker car's median clean dry lap, % off the best team · not fuel or traffic corrected", r.race_pace)}
        ${srTrendCard("quali", "Qualifying pace", "each team's best lap, % off pole", r.quali_pace)}
      </div>
      <div class="two" style="margin-top:16px">${srTeammates(r.teammates)}${srPits(r.pits)}</div>
      <div style="margin-top:16px">${srRacecraft(r.racecraft)}</div>
      <div class="two" style="margin-top:16px">${srTyres(r.tyres)}<div class="stack">${srNeutral(r.neutral)}${srWeather(r.weather)}</div></div>
      <p class="xnote" style="margin-top:14px">From the local dataset (${esc((r.status || {}).path || "")}). Overtakes are passes on track: OpenF1's position swaps without pit stops, Safety Cars, slow cars and timing glitches (docs/dataset.md). Grid positions are the qualifying order when OpenF1 has no starting grid, so grid penalties aren't applied.</p>`;
  });
}

/* ---- pace trends: one line per team, three highlighted at a time (ten lines at once can't be read) ---- */
function srTrendCard(kind, title, sub, d) {
  if (!d.teams.length) return `<div class="card"><h2>${title}</h2><div class="empty">Not enough laps yet.</div></div>`;
  const sel = SR.sel[kind] || (SR.sel[kind] = new Set(d.teams.slice(0, 3).map(t => t.team)));
  const rows = d.teams.map((t, i) => `<tr class="click${sel.has(t.team) ? " on" : ""}" onclick="srPick('${kind}', '${esc(t.team.replace(/'/g, ""))}')">
      <td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.team)}</div></td>
      <td class="r num">${srPct(t.avg)}</td><td class="r num">${t.best || ""}</td><td class="r num">${t.rounds}</td></tr>`).join("");
  return `<div class="card"><h2>${title} <span class="sub">${sub}</span></h2><div class="pad" id="sr-${kind}">${srTrend(kind, d)}</div>
    <div class="scroll"><table><thead><tr><th></th><th>Team <span class="muted">(click to highlight)</span></th><th class="r">Avg gap</th><th class="r">Quickest</th><th class="r">Rounds</th></tr></thead>
    <tbody>${rows}</tbody></table></div></div>`;
}
function srPick(kind, team) {
  const sel = SR.sel[kind], t = [...SR.data[+$("srYear").value][kind + "_pace"].teams].find(x => x.team.replace(/'/g, "") === team);
  if (!t) return;
  sel.has(t.team) ? sel.delete(t.team) : sel.add(t.team);
  srRender(+$("srYear").value);
}
function srTrend(kind, d) {
  const sel = SR.sel[kind], W = 900, H = 280, L = 52, R = 14, T = 14, B = 28;
  const rounds = d.rounds.map(x => x.round), name = Object.fromEntries(d.rounds.map(x => [x.round, x.name]));
  const all = d.teams.flatMap(t => t.points.map(p => p[1])).sort((a, b) => a - b);
  const cap = Math.max(0.5, Math.min(all[Math.floor(all.length * .97)] || 1, 5)), x0 = Math.min(...rounds), x1 = Math.max(...rounds);
  const X = v => L + (v - x0) / ((x1 - x0) || 1) * (W - L - R), Y = v => T + Math.min(v, cap) / cap * (H - T - B);
  let g = "";
  for (let i = 0; i <= 4; i++) { const v = cap * i / 4;
    g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2e3a"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${i ? "+" + v.toFixed(1) + "%" : "0"}</text>`; }
  const step = Math.max(1, Math.ceil(rounds.length / 12));
  rounds.forEach((rn, i) => { if (i % step === 0) g += `<text x="${X(rn)}" y="${H - 8}" font-size="11" fill="#8b91a1" text-anchor="middle">R${rn}</text>`; });
  const order = [...d.teams].sort((a, b) => sel.has(a.team) - sel.has(b.team));      // highlighted lines drawn last, on top
  const lines = order.map(t => { const on = sel.has(t.team), c = on ? vis(t.colour) : "#3a3f4d";
    const pts = t.points.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    const dots = on ? t.points.map(p => `<circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="4" fill="${esc(c)}" stroke="#16181f" stroke-width="2"/>
      <circle cx="${X(p[0]).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="10" fill="transparent"><title>${esc(t.team)} · R${p[0]} ${esc(name[p[0]] || "")}: ${p[1] === 0 ? "quickest" : srPct(p[1])}${p[1] > cap ? " (off the scale)" : ""}</title></circle>`).join("") : "";
    return `<polyline fill="none" stroke="${esc(c)}" stroke-width="${on ? 2 : 1.2}" points="${pts}"/>${dots}`; }).join("");
  const legend = d.teams.filter(t => sel.has(t.team)).map(t => `<span><i style="background:${esc(vis(t.colour))}"></i>${esc(t.team)}</span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="${kind} pace by round">${g}${lines}</svg>
    <div class="legend">${legend || `<span class="muted">Click teams in the table to highlight them.</span>`}<span class="muted">top line = quickest that day${all.some(v => v > cap) ? ` · values above +${cap.toFixed(1)}% drawn at the bottom edge` : ""}</span></div>`;
}

/* ---- bars ---- */
const srBar = (pct, colour, title) => `<div class="srbar" title="${esc(title)}"><i style="width:${Math.max(1.5, Math.min(100, pct)).toFixed(1)}%;background:${esc(colour)}"></i></div>`;

function srTeammates(list) {
  if (!list.length) return `<div class="card"><h2>Team-mates in qualifying</h2><div class="empty">Not enough qualifying sessions yet.</div></div>`;
  const max = Math.max(...list.map(p => p.median_pct), 0.1);
  const rows = list.map(p => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span><b>${esc(p.a)}</b>&nbsp;<span class="muted">v</span>&nbsp;${esc(p.b)}</div></td>
    <td style="width:40%">${srBar(p.median_pct / max * 100, vis(p.colour), `${p.a} quicker by a median ${p.median_pct.toFixed(3)}% (${p.median_gap.toFixed(3)} s) over ${p.rounds.length} qualifyings`)}</td>
    <td class="r num">${p.median_pct.toFixed(2)}%</td><td class="r num">${p.median_gap.toFixed(3)}s</td><td class="r num">${p.ahead[0]}-${p.ahead[1]}</td></tr>`).join("");
  return `<div class="card"><h2>Team-mates in qualifying <span class="sub">median gap in the last segment both reached · ahead count</span></h2><div class="scroll"><table>
    <thead><tr><th>Quicker v slower</th><th></th><th class="r">Median</th><th class="r">Seconds</th><th class="r">Ahead</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function srPits(p) {
  if (!p.teams.length) return `<div class="card"><h2>Pit stops</h2><div class="empty">No stationary times in the dataset.</div></div>`;
  const lo = 1.5, hi = 6, W = 520, X = t => 8 + (Math.min(t, hi) - lo) / (hi - lo) * (W - 16);
  const strip = t => `<svg viewBox="0 0 ${W} 22" width="100%" style="display:block">
      <line x1="${X(p.slow_after)}" x2="${X(p.slow_after)}" y1="0" y2="22" stroke="#2a2e3a" stroke-dasharray="3 3"/>
      ${t.times.map(s => `<circle cx="${X(s).toFixed(1)}" cy="11" r="3.5" fill="${esc(vis(t.colour))}" opacity=".5"><title>${s.toFixed(1)} s${s > hi ? " (off the scale)" : ""}</title></circle>`).join("")}
      <line x1="${X(t.median)}" x2="${X(t.median)}" y1="2" y2="20" stroke="#eef0f5" stroke-width="2"><title>median ${t.median} s</title></line></svg>`;
  const rows = p.teams.map(t => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.team)}</div></td>
    <td style="width:46%">${strip(t)}</td><td class="r num"><b>${t.median.toFixed(1)}</b></td><td class="r num">${t.best.toFixed(1)}</td><td class="r num">${t.slow_pct.toFixed(0)}%</td><td class="r num">${t.stops}</td></tr>`).join("");
  const axis = [2, 3, 4, 5, 6].map(v => `<span style="left:${(X(v) / W * 100).toFixed(1)}%">${v}${v === hi ? "+" : ""}s</span>`).join("");
  const fast = p.fastest.slice(0, 5).map(f => `<span><b>${f.time.toFixed(1)}s</b> ${esc(f.code || "")} <span class="muted">${esc(f.team)}, R${f.round}</span></span>`).join("");
  return `<div class="card"><h2>Pit stops <span class="sub">time stationary, races and sprints · each dot a stop · white line = median · dashed = ${p.slow_after} s (slow)</span></h2>
    <div class="scroll"><table><thead><tr><th>Team</th><th><div class="sraxis">${axis}</div></th><th class="r">Median</th><th class="r">Best</th><th class="r">Slow</th><th class="r">Stops</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad"><div class="srlist"><span class="muted">Fastest:</span>${fast}</div>
    <div class="srlist" style="margin-top:6px"><span class="muted">Cheapest pit lanes:</span>${p.circuits.slice(0, 4).map(c => `<span><b>${c.median}s</b> ${esc(c.circuit)}</span>`).join("")}
      <span class="muted">dearest:</span>${p.circuits.slice(-3).reverse().map(c => `<span><b>${c.median}s</b> ${esc(c.circuit)}</span>`).join("")}</div></div></div>`;
}

const SR_COLS = [["made", "Overtakes", "passes made on track"], ["lost", "Passed", "times passed on track"], ["net", "Net", "made minus passed"],
  ["led", "Laps led", "laps at the front at the end of the lap"], ["lap1_avg", "Lap 1", "average places gained on the opening lap (Grands Prix)"],
  ["gained_avg", "Grid → flag", "average places gained from the grid when finishing (Grands Prix)"], ["starts", "Starts", "races and sprints"]];
function srSortBy(k) { SR.sort = k; store.set("srsort", k); srRender(+$("srYear").value); }
function srRacecraft(list) {
  const k = SR.sort, rows = [...list].sort((a, b) => k === "lost" ? (a[k] ?? 99) - (b[k] ?? 99) : (b[k] ?? -99) - (a[k] ?? -99));
  const max = Math.max(...list.map(e => e.made), 1);
  const sgn = (v, n = 1) => v == null ? "-" : `<span class="${v > 0 ? "up" : v < 0 ? "down" : ""}">${v > 0 ? "+" : ""}${v.toFixed(n)}</span>`;
  const body = rows.map((e, i) => `<tr><td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(e.colour)}"></span>${esc(e.code)} <small>${esc(e.team || "")}</small></div></td>
    <td class="r num"><b>${e.made}</b></td><td style="width:18%">${srBar(e.made / max * 100, vis(e.colour), `${e.code}: ${e.made} overtakes`)}</td>
    <td class="r num">${e.lost}</td><td class="r num">${sgn(e.net, 0)}</td><td class="r num">${e.led || ""}</td>
    <td class="r num">${sgn(e.lap1_avg, 2)}</td><td class="r num">${sgn(e.gained_avg, 2)}</td><td class="r num">${e.starts}</td></tr>`).join("");
  const th = ([key, label, tip]) => `<th class="r click${k === key ? " on" : ""}" title="${esc(tip)}" onclick="srSortBy('${key}')">${label}${k === key ? " ▾" : ""}</th>`;
  return `<div class="card"><h2>Race craft <span class="sub">races and sprints · click a column to sort</span></h2><div class="scroll"><table>
    <thead><tr><th></th><th>Driver</th>${th(SR_COLS[0])}<th></th>${SR_COLS.slice(1).map(th).join("")}</tr></thead><tbody>${body}</tbody></table></div></div>`;
}

function srTyres(t) {
  const rows = t.rounds.map(r => { const tot = Object.values(r.laps).reduce((a, b) => a + b, 0) || 1;
    const segs = Object.entries(r.laps).filter(([, n]) => n).map(([c, n]) =>
      `<i style="width:${(n / tot * 100).toFixed(2)}%;background:${TYRE_COL[c]}" title="${esc(c)}: ${(n / tot * 100).toFixed(0)}% of laps (${n})"></i>`).join("");
    return `<tr><td class="pos">R${r.round}</td><td>${esc(r.circuit)}</td><td style="width:55%"><div class="srstack">${segs}</div></td><td class="r num">${r.stops_per_car == null ? "-" : r.stops_per_car.toFixed(1)}</td></tr>`; }).join("");
  const comp = t.compounds.map(c => `<tr><td><span class="tyre t-${esc(c.compound)}">${esc(c.compound[0])}</span> ${esc(c.compound.toLowerCase())}</td>
    <td class="r num">${c.stints}</td><td class="r num">${c.avg}</td><td class="r num">${c.median}</td><td class="r num">${c.max}</td></tr>`).join("");
  const longest = t.longest.slice(0, 5).map(l => `<span><b>${l.laps}</b> ${esc(l.code || "")} <span class="muted">${esc(l.compound.toLowerCase())}, R${l.round}</span></span>`).join("");
  return `<div class="card"><h2>Tyres <span class="sub">share of race laps on each compound · stops per car</span></h2>
    <div class="pad"><div class="legend">${Object.entries(TYRE_COL).map(([c, col]) => `<span><i style="background:${col};height:8px"></i>${c.toLowerCase()}</span>`).join("")}</div></div>
    <div class="scroll"><table><thead><tr><th></th><th>Circuit</th><th>Laps by compound</th><th class="r">Stops</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="scroll" style="margin-top:8px"><table><thead><tr><th>Stint lengths</th><th class="r">Stints</th><th class="r">Average</th><th class="r">Median</th><th class="r">Longest</th></tr></thead><tbody>${comp}</tbody></table></div>
    <div class="pad"><div class="srlist"><span class="muted">Longest stints:</span>${longest}</div></div></div>`;
}

function srNeutral(list) {
  const max = Math.max(...list.map(r => r.pct || 0), 1);
  const rows = list.filter(r => r.sc || r.vsc || r.red || r.pct).map(r => `<tr><td class="pos">R${r.round}</td><td>${esc(r.circuit)}${r.session === "Sprint" ? ' <span class="muted">sprint</span>' : ""}</td>
    <td class="r num">${r.sc || ""}</td><td class="r num">${r.vsc || ""}</td><td class="r num">${r.red || ""}</td>
    <td style="width:34%">${srBar(r.pct / max * 100, "#ffc906", `${r.pct}% of laps neutralised`)}</td><td class="r num">${r.pct}%</td></tr>`).join("");
  const clean = list.filter(r => !(r.sc || r.vsc || r.red || r.pct)).length;
  return `<div class="card"><h2>Safety Cars <span class="sub">share of laps under SC, VSC or red flag</span></h2><div class="scroll"><table>
    <thead><tr><th></th><th>Circuit</th><th class="r">SC</th><th class="r">VSC</th><th class="r">Red</th><th>Laps neutralised</th><th class="r"></th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">${clean} of ${list.length} races and sprints ran without a neutralisation.</div></div>`;
}

function srWeather(list) {
  if (!list.length) return "";
  const lo = Math.floor(Math.min(...list.map(r => r.track_min)) / 5) * 5, hi = Math.ceil(Math.max(...list.map(r => r.track_max)) / 5) * 5;
  const P = v => ((v - lo) / ((hi - lo) || 1) * 100).toFixed(1);
  const rows = list.map(r => `<tr><td class="pos">R${r.round}</td><td>${esc(r.circuit)}</td>
    <td style="width:46%"><div class="srrange" title="track ${r.track_min}-${r.track_max} °C, median ${r.track_median} °C">
      <i style="left:${P(r.track_min)}%;width:${Math.max(.8, P(r.track_max) - P(r.track_min))}%"></i><b style="left:${P(r.track_median)}%"></b></div></td>
    <td class="r num">${r.track_median}°</td><td class="r num">${r.air_median ?? "-"}°</td><td class="r num">${r.rain_pct ? `${ico("rain")}${r.rain_pct}%` : ""}</td></tr>`).join("");
  return `<div class="card"><h2>Weather <span class="sub">track temperature range during each race (${lo}-${hi} °C) · rain = share of readings</span></h2><div class="scroll"><table>
    <thead><tr><th></th><th>Circuit</th><th>Track</th><th class="r">Track</th><th class="r">Air</th><th class="r">Rain</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
