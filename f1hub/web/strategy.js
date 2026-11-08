/* F1 Hub dashboard - strategy extras: undercut watch, pit stop league.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- strategy extras: undercut watch + pit stop league ---------- */
function renderUndercut(d) {
  const el = $("undercut");
  if (d.status === "RED" || d.status === "SC" || d.status === "VSC") {
    el.innerHTML = `<div class="muted">Under ${d.status === "RED" ? "a red flag" : "the safety car"} the field is neutralised - stops are cheap, so the usual undercut maths doesn't apply.</div>`;
    return;
  }
  const loss = (d.insights && d.insights.pit_loss) || 22;
  const rows = d.rows.filter(r => !r.out && r.pos);
  const pairs = [];
  for (let i = 1; i < rows.length; i++) {
    const a = rows[i - 1], b = rows[i];
    if (typeof b.interval !== "number" || b.interval > 3.5) continue;
    // fresh tyres are worth roughly 0.05 s/lap per lap of age on the old set (capped), minus out/in-lap traffic
    const ageA = a.tyre_age || 0, gain = Math.min(1.8, ageA * 0.05) - 0.3;
    const net = gain - b.interval;
    const verdict = net > 0.4 ? ["LIKELY", "#2bd46b"] : net > -0.4 ? ["MARGINAL", "#ffb000"] : ["UNLIKELY", "#8b91a1"];
    pairs.push(`<tr><td><span class="drv"><span class="stripe" style="background:${esc(b.colour)}"></span>${esc(b.code)}</span></td>
      <td class="muted">on</td><td><span class="drv"><span class="stripe" style="background:${esc(a.colour)}"></span>${esc(a.code)}</span></td>
      <td class="r num">${b.interval.toFixed(2)}s</td>
      <td class="num muted"><span style="color:${TYRE[b.compound] || "#888"}">●</span>${b.tyre_age ?? "?"} vs <span style="color:${TYRE[a.compound] || "#888"}">●</span>${ageA}</td>
      <td class="r num">${gain > 0 ? "+" : ""}${gain.toFixed(1)}s</td><td style="color:${verdict[1]};font-weight:800">${verdict[0]}</td></tr>`);
  }
  el.innerHTML = pairs.length ? `<table class="num"><thead><tr><th>Behind</th><th></th><th>Ahead</th><th class="r">Gap</th><th>Tyre age</th><th class="r">Fresh-tyre gain</th><th></th></tr></thead><tbody>${pairs.join("")}</tbody></table>
    <div class="muted" style="font-size:12px;margin-top:6px">If the car behind pits now and the one ahead stays out a lap: does a fresh set gain more than the gap? Pit loss here ≈ ${loss.toFixed ? loss.toFixed(1) : loss}s.</div>`
    : '<div class="muted">No cars close enough for an undercut right now.</div>';
}
function renderPitLeague(d) {
  const stops = (d.pits || []).filter(p => typeof p.lane === "number");
  if (!stops.length) { $("pitleague").innerHTML = '<div class="muted">No pit stops yet.</div>'; return; }
  const sorted = stops.map(p => p.lane).sort((a, b) => a - b), med = sorted[Math.floor(sorted.length / 2)];
  const normal = stops.filter(p => p.lane < med * 1.6);          // drop drive-throughs / stops under a red flag
  const best = normal.slice().sort((a, b) => a.lane - b.lane).slice(0, 8);
  const teams = {};
  normal.forEach(p => { const r = d.rows.find(x => x.number === p.number) || {}; const t = r.team || p.code;
    (teams[t] = teams[t] || {team: t, colour: p.colour, laps: []}).laps.push(p.lane); });
  const tl = Object.values(teams).map(t => ({...t, avg: t.laps.reduce((a, b) => a + b, 0) / t.laps.length})).sort((a, b) => a.avg - b.avg);
  const fastest = best[0] ? best[0].lane : 1;
  $("pitleague").innerHTML = `<div class="two" style="gap:14px"><div><h2 style="margin-top:0">Fastest stops</h2><table class="num"><tbody>${best.map((p, i) => `<tr><td class="muted">${i + 1}</td>
      <td><span class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span>${esc(p.code)}</span></td><td class="muted">L${p.lap}</td>
      <td class="r"><b>${p.lane.toFixed(1)}s</b></td><td class="r muted">${i ? "+" + (p.lane - fastest).toFixed(1) : ""}</td></tr>`).join("")}</tbody></table></div>
    <div><h2 style="margin-top:0">Team average</h2><table class="num"><tbody>${tl.map((t, i) => `<tr><td class="muted">${i + 1}</td>
      <td><span class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.team)}</span></td>
      <td class="r"><b>${t.avg.toFixed(1)}s</b></td><td class="r muted">${t.laps.length} stop${t.laps.length > 1 ? "s" : ""}</td></tr>`).join("")}</tbody></table></div></div>
    ${stops.length > normal.length ? `<div class="muted" style="font-size:12px">${stops.length - normal.length} unusually long stop(s) left out (penalties, red flag, repairs).</div>` : ""}`;
}
