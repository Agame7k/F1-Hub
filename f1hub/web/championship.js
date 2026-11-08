/* F1 Hub dashboard - championship extras: progression chart, driver cards, title simulator.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- championship extras: progression chart, driver season cards, title simulator ---------- */
const GP_PTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1], SPRINT_PTS = [8, 7, 6, 5, 4, 3, 2, 1];
function renderProgression(s) {
  const prog = s.progression || [];
  if (!prog.length) { $("progchart").innerHTML = '<div class="muted">No completed rounds yet.</div>'; return; }
  const live = D && D.title && D.title[0] && !s.spoiler_hidden && D.mode === "live" ? D.title[0].table : null;
  const series = teammateDash(prog.map(p => ({colour: p.colour, points: p.points, code: p.code})));
  if (live) prog.forEach(p => {   // dashed extension to "if it finished now"
    const t = live.find(x => x.code === p.code), last = p.points[p.points.length - 1];
    if (t && last) series.push({colour: p.colour, dash: true, width: 1.5, points: [last, [last[0] + 1, t.points]]});
  });
  const names = Object.fromEntries((s.calendar || []).map(r => [r.round, r.name]));
  $("progchart").innerHTML = lineChart(series, {H: 300, yMin: 0, yLabel: "points", xFmt: v => "R" + Math.round(v)}) +
    `<div class="row2" style="margin-top:6px">${prog.map(p => `<span class="drv click" onclick="driverCard('${esc(p.code)}')"><span class="stripe" style="background:${esc(p.colour)}"></span>${esc(p.code)} ${p.points[p.points.length - 1][1]}</span>`).join("")}</div>`;
}
function driverCard(code) {
  if (!S) return;
  const i = S.drivers.findIndex(e => e.code === code), e = S.drivers[i];
  if (!e) return;
  const row = D && D.rows ? D.rows.find(r => r.code === code) : null;
  const fins = e.finishes || [], done = S.calendar.filter(r => r.done && !r.hidden);
  const classified = fins.filter(f => f), avg = classified.length ? classified.reduce((a, b) => a + b, 0) / classified.length : null;
  const prog = (S.progression || []).find(p => p.code === code);
  const leader = S.drivers[0];
  const stats = [["Position", "P" + (i + 1)], ["Points", e.points], ["Wins", e.wins], ["Podiums", e.podiums], ["Poles", e.poles],
    ["DNFs", e.dnfs], ["Avg finish", avg ? avg.toFixed(1) : "-"], ["Best", classified.length ? "P" + Math.min(...classified) : "-"],
    ["To leader", i ? "-" + (leader.points - e.points) : "leads"], ["Sprint wins", e.sprint_wins]];
  $("dcardin").style.setProperty("--tc", e.colour);
  $("dcardin").innerHTML = `<div class="dc-head">${row && row.headshot ? `<img src="${esc(row.headshot)}" onerror="this.remove()">` : ""}
      <div style="flex:1"><h3 style="color:${esc(e.colour)}">${esc(e.name)}</h3><div class="muted">${esc(e.team)} · ${S.year} season${e.alive ? ' · <span class="up">still in title contention</span>' : ""}</div></div>
      <button class="btn" onclick="$('dcard').classList.add('hidden')">✕</button></div>
    <div class="dc-stats">${stats.map(x => `<div><b>${esc(x[1])}</b><span>${x[0]}</span></div>`).join("")}</div>
    <h2>Race by race</h2>
    <div class="finrow">${fins.map((f, k) => { const r = done[k], cls = !f ? "dnf" : f <= 3 ? "p" + f : f <= 10 ? "pts" : "";
      return `<div class="fin ${cls}" title="${esc(r ? "R" + r.round + " " + r.name : "")}">${f ? f : "DNF"}<small>${r ? esc(r.country ? r.country.slice(0, 3).toUpperCase() : "R" + r.round) : ""}</small></div>`; }).join("")}</div>
    ${prog ? `<h2 style="margin-top:14px">Points through the season</h2>${lineChart([{colour: e.colour, points: prog.points, width: 3},
      ...(i ? [{colour: "#8b91a1", dash: true, points: (S.progression[0] || {}).points || []}] : [])], {H: 220, yMin: 0, xFmt: v => "R" + Math.round(v)})}
      ${i ? `<div class="muted" style="font-size:12px">dashed: ${esc(S.progression[0].code)} (leader)</div>` : ""}` : ""}`;
  $("dcard").classList.remove("hidden");
}
document.addEventListener("keydown", e => { if (e.key === "Escape") $("dcard").classList.add("hidden"); });

// Title simulator: pick results for the leader and one rival over the remaining rounds.
const SIM = {rival: null, picks: {}};
function renderSim(s) {
  const alive = s.drivers.filter(e => e.alive);
  const el = $("sim");
  if (s.driver_champion || alive.length < 2) {
    el.innerHTML = `<div class="muted">${s.driver_champion ? esc(s.driver_champion) + " is already champion - nothing left to simulate." : "Only one driver can still win the title."}</div>`;
    return;
  }
  const leader = s.drivers[0];
  if (!SIM.rival || !alive.some(e => e.code === SIM.rival) || SIM.rival === leader.code) SIM.rival = alive[1].code;
  const rival = s.drivers.find(e => e.code === SIM.rival);
  const rounds = s.remaining_rounds || [];
  const opts = (sel, sprint) => [...Array(sprint ? 8 : 10).keys()].map(k => `<option value="${k + 1}" ${sel == k + 1 ? "selected" : ""}>P${k + 1}</option>`).join("") +
    `<option value="0" ${sel == 0 ? "selected" : ""}>no points</option>`;
  const liveRow = c => D && D.rows && D.mode === "live" && !s.spoiler_hidden ? (D.rows.find(r => r.code === c) || {}).pos : null;
  const pick = (code, r, kind) => {
    const k = `${code}.${r.round}.${kind}`;
    if (!(k in SIM.picks)) {   // defaults: current race as it stands, then rival wins / leader 2nd
      const lp = r.round === s.completed + 1 ? liveRow(code) : null;
      SIM.picks[k] = lp && lp <= 10 ? lp : (code === rival.code ? 1 : 2);
    }
    return SIM.picks[k];
  };
  const pts = (p, sprint) => p ? (sprint ? SPRINT_PTS : GP_PTS)[p - 1] || 0 : 0;
  // walk the rounds, find the decisive one
  let L = leader.points, R = rival.points, decided = null;
  const maxLeft = k => rounds.slice(k).reduce((a, r) => a + 25 + (r.sprint ? 8 : 0), 0);
  const rowsHtml = rounds.map((r, k) => {
    const lp = pick(leader.code, r, "gp"), rp = pick(rival.code, r, "gp");
    L += pts(lp); R += pts(rp);
    let sprintCells = "";
    if (r.sprint) { const ls = pick(leader.code, r, "sp"), rs = pick(rival.code, r, "sp"); L += pts(ls, true); R += pts(rs, true);
      sprintCells = `<div class="muted" style="font-size:11px">sprint <select onchange="SIM.picks['${leader.code}.${r.round}.sp']=+this.value;renderSim(S)">${opts(ls, true)}</select>
        <select onchange="SIM.picks['${rival.code}.${r.round}.sp']=+this.value;renderSim(S)">${opts(rs, true)}</select></div>`; }
    const left = maxLeft(k + 1);
    if (!decided && (L - R > left || R - L > left)) decided = {round: r, who: L > R ? leader : rival};
    return `<tr><td class="muted num">R${r.round}</td><td>${esc(r.name)}${r.round === s.completed + 1 && D && D.mode === "live" ? ' <span class="pill" style="background:#e10600;color:#fff;padding:1px 6px;border-radius:3px;font-size:10px">LIVE</span>' : ""}${sprintCells}</td>
      <td><select onchange="SIM.picks['${leader.code}.${r.round}.gp']=+this.value;renderSim(S)">${opts(lp)}</select></td>
      <td><select onchange="SIM.picks['${rival.code}.${r.round}.gp']=+this.value;renderSim(S)">${opts(rp)}</select></td>
      <td class="r num"><b>${L}</b> - <b>${R}</b></td><td class="muted">${decided && decided.round === r ? "🏆 decided" : ""}</td></tr>`;
  }).join("");
  const champ = L === R ? null : L > R ? leader : rival;
  el.innerHTML = `<div class="row2" style="margin-bottom:8px"><span>${esc(leader.name)} (${leader.points}) vs</span>
      <select onchange="SIM.rival=this.value;renderSim(S)">${alive.slice(1).map(e => `<option value="${esc(e.code)}" ${e.code === rival.code ? "selected" : ""}>${esc(e.name)} (${e.points})</option>`).join("")}</select>
      <button class="btn" onclick="SIM.picks={};renderSim(S)">Reset</button>
      <button class="btn" onclick="simWorst()">Rival wins everything</button></div>
    <div class="simgrid"><table class="num"><thead><tr><th></th><th>Round</th><th style="color:${esc(leader.colour)}">${esc(leader.code)}</th><th style="color:${esc(rival.colour)}">${esc(rival.code)}</th><th class="r">Running total</th><th></th></tr></thead>
    <tbody>${rowsHtml}</tbody></table></div>
    <div class="simres ${champ ? "win" : ""}">${champ ? `🏆 ${esc(champ.name)} would be champion by ${Math.abs(L - R)} points${decided ? ` - settled at the ${esc(decided.round.name)}` : " - at the final round"}.`
      : "Dead heat on points - decided on countback (most wins, then most 2nds, ...)."}
      <div class="muted" style="font-weight:400;font-size:12px;margin-top:4px">Only these two drivers' results are simulated; others are assumed not to matter.</div></div>`;
}
function simWorst() {   // rival wins every race: what's the worst the leader can do and still win?
  const s = S, leader = s.drivers[0], rival = s.drivers.find(e => e.code === SIM.rival), rounds = s.remaining_rounds || [];
  rounds.forEach(r => { SIM.picks[`${rival.code}.${r.round}.gp`] = 1; if (r.sprint) SIM.picks[`${rival.code}.${r.round}.sp`] = 1; });
  // find the lowest equal finishing position for the leader that still keeps them ahead
  let best = 2;
  for (let p = 10; p >= 2; p--) {
    const L = leader.points + rounds.reduce((a, r) => a + GP_PTS[p - 1] + (r.sprint ? (SPRINT_PTS[p - 1] || 0) : 0), 0);
    const R = rival.points + rounds.reduce((a, r) => a + 25 + (r.sprint ? 8 : 0), 0);
    if (L > R) { best = p; break; }
  }
  rounds.forEach(r => { SIM.picks[`${leader.code}.${r.round}.gp`] = best; if (r.sprint) SIM.picks[`${leader.code}.${r.round}.sp`] = Math.min(best, 8); });
  renderSim(s);
}
