/* F1 Hub dashboard - drawing each live frame: timing, race control, map, radio, insights.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- render ---------- */
// one broken piece (a bad row, an odd message) must never blank the whole dashboard: each step is isolated
const BROKEN = {};
function safe(name, fn) {
  try { fn(); if (BROKEN[name]) { delete BROKEN[name]; showBroken(); } }
  catch (e) { if (!BROKEN[name]) { console.error(name, e); BROKEN[name] = String(e && e.message || e); showBroken(); } }
}
function showBroken() {
  const names = Object.keys(BROKEN);
  $("warn").classList.toggle("hidden", !names.length);
  $("warn").textContent = names.length ? `Couldn't draw: ${names.join(", ")} - the rest of the dashboard is fine (details in the browser console)` : "";
}
function render(d, fromCtl) {
  if (!d || typeof d !== "object") return;
  if (d.boot !== CLOCK.boot) {        // server (re)started: start over, and put your saved delay back on it
    if (CLOCK.boot != null) restored = false;
    CLOCK.boot = d.boot; CLOCK.seek = null;
  }
  if (!fromCtl && CLOCK.seek != null && d.seek < CLOCK.seek) return;          // an old frame overtaken by a newer one
  if (d.seek !== CLOCK.seek) { CLOCK.seek = d.seek; CLOCK.floor = 0; }
  D = d;
  receivedAt = Date.now();
  safe("mode", () => renderMode(d));
  safe("settings", () => restoreSettings(d));
  safe("map trails", () => mergeTrails(d));
  safe("sync bar", renderSync);
  safe("celebrations", () => checkCelebrations(d));
  safe("alerts", () => checkAlerts(d));
  safe("crash banner", () => { if (AL.kinds.crash !== false) renderCrash(d); else $("crashbanner").classList.add("hidden"); });
  safe("incidents", () => renderIncidents(d));
  safe("provisional grid", () => loadGrid());
  safe("penalty points", () => { if (activeTab === "rc") loadPoints(); });
  safe("mini tower", () => renderPip(d));
  safe("ghost", () => refreshGhost(false));
  safe("race moments", () => raceMoments(d));
  safe("lap colours", () => refreshLapColour());
  $("error").classList.toggle("hidden", !d.error);
  $("error").textContent = d.error || "";
  if (!d.rows) return;
  document.body.classList.toggle("nonrace", !!d.session && !isRaceSession(d));   // practice / qualifying: no race-only panels
  safe("header", () => renderHeader(d));
  safe("red flag panel", () => $("redbox").innerHTML = redPanel(d));
  const tabs = {live: renderLive, rc: renderRC, map: renderMap, strat: renderStrat, gaps: renderGaps, ins: renderInsights, drv: renderDriver};
  if (tabs[activeTab]) safe(TAB_NAMES[activeTab] || activeTab, () => tabs[activeTab](d));
  safe("team radio", () => renderRadio(d));     // always: cheap (incremental), and auto-play works from any tab
}
const TAB_NAMES = {live: "timing", rc: "race control", map: "track map", strat: "strategy", gaps: "gaps", ins: "insights", drv: "driver"};

function renderHeader(d) {
  $("event").innerHTML = `${esc(d.meeting)} <small>${esc(d.session)} · ${esc(d.circuit)}</small>`;
  const st = $("status"); st.className = "pill " + d.status;
  st.textContent = {SC: "SAFETY CAR", VSC: "VIRTUAL SC", RED: "RED FLAG", CHEQUERED: "CHEQUERED"}[d.status] || d.status;
  const Q = d.quali;
  $("laplbl").textContent = Q && Q.phase ? Q.label : "Lap";
  if (Q && Q.phase) {
    $("lap").textContent = fmtClock(qRemaining(Q));
    $("lapof").textContent = Q.finished ? (Q.provisional ? "last laps" : "over") : Q.suspended ? "stopped" : "left";
    $("lapbar").style.width = Q.duration ? Math.min(100, (1 - qRemaining(Q) / Q.duration) * 100) + "%" : "0";
  } else {
    $("lap").textContent = d.lap || "-";
    $("lapof").textContent = d.scheduled_laps ? "/ " + d.scheduled_laps : "";
    $("lapbar").style.width = d.scheduled_laps ? Math.min(100, d.lap / d.scheduled_laps * 100) + "%" : "0";
  }
  $("om").classList.toggle("on", !!d.overtake_mode);
  $("om").textContent = "OVERTAKE MODE " + (d.overtake_mode == null ? "?" : d.overtake_mode ? "ON" : "OFF");
  $("meta").innerHTML = d.mode === "replay" ? `Replay · showing ${new Date(d.now).toLocaleTimeString()}` :
    `Live data from OpenF1${d.settings && d.settings.delay ? ` · <b class="blue">shown ${d.settings.delay}s late</b>` : ""}<br>Data time ${d.now ? new Date(d.now).toLocaleTimeString() : "-"} · <span class="age" id="age">${AGE.shown == null ? "" : AGE.shown <= 1 ? "data up to date" : `data ~${AGE.shown} s behind`}</span>`;
  offAirHeader();
  const msgs = (d.rc && d.rc.messages) || [];
  const newRc = msgs.length - seenRc;
  if (activeTab === "rc") seenRc = msgs.length;
  $("rcbadge").textContent = newRc; $("rcbadge").classList.toggle("hidden", activeTab === "rc" || newRc <= 0 || !seenRc);
  if (!seenRc && activeTab !== "rc") seenRc = msgs.length;

  const sel = $("drvsel");
  if (sel.options.length <= 1) d.rows.forEach(r => sel.add(new Option(`${r.code} - ${r.name || ""}`, r.number)));
  sel.value = focus || "";
}

function fmtClock(sec) { if (sec == null) return "-"; sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0"); }
// the segment clock, counted down between server frames (replays and stopped clocks don't tick)
function qRemaining(Q) {
  if (!Q || Q.remaining == null) return null;
  const live = Q.running && D && D.mode !== "replay";
  return Math.max(0, Q.remaining - (live ? (Date.now() - receivedAt) / 1000 : 0));
}
setInterval(() => {
  const Q = D && D.quali;
  if (!Q || !Q.running) return;
  const t = fmtClock(qRemaining(Q));
  $("lap").textContent = t;
  const el = document.getElementById("qclock"); if (el) el.textContent = t;
}, 1000);

function qualiCard(d) {
  const Q = d.quali;
  if (!Q || !Q.phase) return "";
  const rem = qRemaining(Q);
  const state = Q.finished ? (Q.provisional ? ["Chequered flag · last laps still counting", "amber"] : [`${Q.label} is over`, ""])
              : Q.suspended ? ["Clock stopped · session suspended", "red"] : Q.running ? ["Clock running", ""] : ["Waiting to start", ""];
  const delta = v => v == null ? "" : (v <= 0 ? "−" : "+") + Math.abs(v).toFixed(3);
  const laps = (Q.on_lap || []).map(o => {
    const secs = [0, 1, 2].map(i => { const x = o.sectors[i];
      return x ? `<span class="sec ${x.c}" title="${x.t.toFixed(3)}">${x.d == null ? x.t.toFixed(3) : delta(x.d)}</span>` : `<span class="muted">S${i + 1}</span>`; }).join("");
    const where = o.projected_pos ? `→ P${o.projected_pos}${o.safe === true ? ' <span class="safe">safe</span>' : o.safe === false ? ' <span class="danger">drop zone</span>' : ""}` : "";
    return `<div class="qlap num" style="--tc:${esc(o.colour)}" onclick="setFocus(${o.number})" title="Started ${Math.round(o.elapsed)}s ago · compared with ${o.ref ? "their best this segment " + fmtLap(o.ref) : "the best sectors"}">
      <b>${esc(o.code)} <span class="muted" style="font-weight:400">P${o.pos ?? "-"}</span></b>
      ${o.compound ? `<span><span class="tyre t-${esc(o.compound)}">${esc(o.compound[0])}</span></span>` : "<span></span>"}
      ${secs}
      <span class="proj">${o.projected ? fmtLap(o.projected) + " " + where : o.sectors.length ? "" : '<span class="muted">lap started</span>'}</span></div>`;
  }).join("");
  const kn = Object.entries(Q.knocked || {}).filter(([, v]) => v.length)
    .map(([k, v]) => `Out in ${k.toUpperCase().replace("Q", Q.label.startsWith("SQ") ? "SQ" : "Q")}: <b>${v.map(esc).join(", ")}</b>`).join(" · ");
  return `<div class="card qcard">
    <div class="qtop">
      <div class="qphase">${esc(Q.label)}<small>${Q.label.startsWith("SQ") ? "SPRINT QUALIFYING" : "QUALIFYING"}</small></div>
      <div><div class="qclock ${Q.suspended ? "stop" : rem != null && rem < 120 && Q.running ? "low" : ""}" id="qclock">${fmtClock(rem)}</div>
        <div class="qstate ${state[1]}">${state[0]}</div></div>
      <div class="qfacts num">
        <span>${Q.phase === 3 ? "Provisional pole" : "Fastest"}</span><span>${Q.pole ? `<b>${esc(Q.pole_driver)}</b> ${fmtLap(Q.pole)}` : "-"}</span>
        ${Q.cut ? `<span>Cut line (P${Q.cut})</span><span>${Q.cut_time ? `<b>${esc(Q.cut_driver)}</b> ${fmtLap(Q.cut_time)} <span class="muted">+${(Q.cut_time - Q.pole).toFixed(3)}</span>` : "-"}</span>` : ""}
        <span>Through from Q1 / Q2</span><span>top ${Q.advance[0]} / top ${Q.advance[1]}</span>
        ${Q.wet != null ? `<span>Track</span><span style="color:${Q.wet ? "#4aa3ff" : "inherit"}">${Q.wet ? "declared wet" : "declared dry"}</span>` : ""}
        ${Object.keys(Q.tyres || {}).length ? `<span>Tyres (still in)</span><span>${Object.entries(Q.tyres).map(([c, k]) =>
          `<span class="tyre t-${esc(c)}" title="${esc(c)}">${esc(c[0])}</span>${k}`).join(" ")}</span>` : ""}
      </div>
    </div>
    <div class="qbar"><i style="width:${Q.duration ? Math.min(100, (1 - (rem || 0) / Q.duration) * 100) : 0}%"></i></div>
    ${laps ? `<div class="qlaps"><div class="sub muted" style="font-size:11px;letter-spacing:1px;text-transform:uppercase">On a lap now · sector deltas vs their best</div>${laps}</div>`
           : `<div class="muted" style="font-size:12px;margin-top:6px">${Q.finished ? "" : "Nobody on a flying lap right now."}</div>`}
    ${kn ? `<div class="qknock">${kn}${Q.provisional ? " <span class='muted'>(provisional)</span>" : ""}</div>` : ""}
  </div>`;
}

// Pre-race briefing (d.prerace): countdown, qualifying recap and penalties, last year here, the title going in.
function preCount(iso) {
  const s = Math.max(0, Math.round((new Date(iso) - Date.now()) / 1000)), h = Math.floor(s / 3600);
  return (h ? h + ":" + String(Math.floor(s / 60) % 60).padStart(2, "0") : Math.floor(s / 60)) + ":" + String(s % 60).padStart(2, "0");
}
setInterval(() => { const el = document.getElementById("pclock"); if (el && D && D.session_start) el.textContent = preCount(D.session_start); }, 1000);

function preCard(d) {
  const P = d.prerace, q = P.quali || {}, ly = P.last_year, st = P.standings, loss = P.pit_loss || {};
  const chip = x => `<b style="border-left:3px solid ${esc(x.colour)};padding-left:5px">${esc(x.code)}</b>`;
  const tyres = list => list.map(c => `<span class="tyre sm t-${esc(c)}" title="${esc(c)}">${c === "UNKNOWN" ? "?" : esc(c[0])}</span>`).join("");
  const stops = ly && Object.entries(ly.stops || {}).map(([k, v]) => `${v}× ${k}-stop`).join(", ");
  const facts = [
    P.laps ? ["Distance", `<b>${P.laps}</b> laps`] : null,
    q.pole ? ["Pole", `${chip(q.pole)} ${fmtLap(q.pole.time)}`] : null,
    (q.front_row || []).length ? ["Front row", q.front_row.map(chip).join(" ")] : null,
    loss.normal ? ["Pit stop costs", `<b>${loss.normal.toFixed(1)}s</b>${loss.sc ? ` <span class="muted">· SC ${loss.sc.toFixed(1)}s</span>` : ""}${loss.vsc ? ` <span class="muted">· VSC ${loss.vsc.toFixed(1)}s</span>` : ""}`] : null,
    d.weather ? ["Track / air", `<b>${d.weather.track ?? "-"}°C</b> / ${d.weather.air ?? "-"}°C${d.weather.rain ? ' <b style="color:#4aa3ff">rain</b>' : ""}`] : null,
  ].filter(Boolean).map(([k, v]) => `<span>${k}</span><span>${v}</span>`).join("");
  const pens = [...(q.penalties || []).map(p => `${chip(p)} P${p.quali} → <b>P${p.grid}</b>`),
                ...(q.missing || []).map(p => `${chip(p)} P${p.quali} → <b>pit lane / not starting</b>`)];
  const isSprint = /sprint/i.test(d.session || "");
  return `<div class="card qcard pcard">
    <div class="qtop">
      <div class="qphase">${isSprint ? "SPRINT" : "RACE"}<small>${esc(d.meeting || "")}</small></div>
      <div><div class="qclock" id="pclock">${preCount(d.session_start)}</div>
        <div class="qstate">Starts ${esc(fmtTime(d.session_start))} · grid ${(q.penalties || []).length || (q.missing || []).length ? "after penalties" : "provisional"}</div></div>
      <div class="qfacts num">${facts}</div>
    </div>
    <div class="pgrid2">
      ${ly ? `<div><div class="plbl">Here last year (${ly.year})</div>
        <div>${chip(ly.winner)} <span class="muted">${esc(ly.winner.team || "")}</span> won${ly.winner.grid ? ` from <b>P${ly.winner.grid}</b>` : ""}
          ${ly.winner.stints.length ? ` · ${tyres(ly.winner.stints)}` : ""}</div>
        <div class="muted">${stops ? `Top 10: ${esc(stops)}` : ""}${ly.dnfs ? ` · ${ly.dnfs} DNF${ly.dnfs > 1 ? "s" : ""}` : ""}</div></div>` : ""}
      ${st ? `<div><div class="plbl">Championship going in</div>
        ${st.table.slice(0, 4).map((e, i) => `<div class="ptsrow num"><span class="muted">${i + 1}</span><b>${esc(e.code)}</b>
          <span class="muted" style="overflow:hidden;text-overflow:ellipsis">${esc(e.team || "")}</span><span class="r">${e.points}</span>
          <span class="delta muted">${e.behind ? "−" + e.behind : ""}</span></div>`).join("")}
        <div class="muted" style="font-size:12px;margin-top:4px">${st.decided ? `<b style="color:var(--gold)">${esc(st.decided)}</b> is already champion`
          : `${st.points_left} pts left · still in it: <b style="color:var(--text)">${st.contenders.map(esc).join(", ")}</b>`}</div></div>` : ""}
    </div>
    ${pens.length ? `<div class="qknock">Grid changes: ${pens.join(" · ")}</div>` : ""}
  </div>`;
}

function renderLive(d) {
  // qualifying / sprint qualifying: segment gaps from that segment's best laps, Q1/Q2/Q3 columns, drop-zone lines
  const Q = d.quali && d.quali.phase ? d.quali : null;
  const quali = Q || /qualifying|shootout/i.test(d.session || "");
  $("qualibox").innerHTML = Q ? qualiCard(d) : d.pre_session && d.prerace ? preCard(d) : "";
  const preQ = d.pre_session && d.prerace && d.prerace.quali ? d.prerace.quali.times : null;   // qualifying times on the grid
  const qx = r => (Q && Q.rows[r.number]) || {};
  const n = d.rows.length;
  let cuts = {};
  if (Q) {
    const [a1, a2] = Q.advance, seg = Q.label.startsWith("SQ") ? "SQ" : "Q";
    if (Q.phase === 1) cuts[a1] = `${seg}1 drop zone below`;
    if (Q.phase === 2) { cuts[a2] = `${seg}2 drop zone below`; cuts[a1] = `Knocked out in ${seg}1`; }
    if (Q.phase >= 3) { cuts[a2] = `Knocked out in ${seg}2`; cuts[a1] = `Knocked out in ${seg}1`; }
  } else if (quali) cuts = {[n - 6]: "Q1 drop zone below", 10: "Q2 drop zone below"};
  const pole = quali ? Math.min(...d.rows.map(r => (Q ? qx(r).best : r.best_lap) || 1e9)) : null;
  const qbest = r => Q ? qx(r).best : r.best_lap;
  const qgap = r => {
    const x = qx(r);
    if (x.out_in) return `<span class="qtag">${Q.label.startsWith("SQ") ? "SQ" : "Q"}${x.out_in} OUT</span>`;
    const b = qbest(r);
    return !b ? '<span class="muted">no time</span>' : b === pole ? (Q && Q.phase === 3 ? "Pole" : "P1") : "+" + (b - pole).toFixed(3);
  };
  const qint = (r, i) => {
    const prev = d.rows[i - 1];
    if (!i || !prev || qx(r).out_in || qx(prev).out_in) return "";
    const a = qbest(r), b = qbest(prev);
    return a && b ? "+" + (a - b).toFixed(3) : "";
  };
  ["Grid", "+/-", "Pts"].forEach((t, i) => $("th1" + (2 + i)).textContent = Q ? ["Q1", "Q2", "Q3"][i].replace("Q", Q.label.startsWith("SQ") ? "SQ" : "Q") : t);
  const qcol = (r, k) => {
    const x = qx(r), v = x["q" + k];
    return `<td class="r ${Q.phase === k && !x.out_in ? "qseg" : "muted"}">${v != null ? fmtLap(v) : ""}</td>`;
  };
  flipRows($("tower"), () => $("tower").innerHTML = !n ? `<tr><td colspan="16" class="muted" style="padding:28px 14px;text-align:center">${airState() === "upcoming"
    ? `No timing yet - ${esc(d.session || "the session")} starts ${cdShort(new Date(d.session_start) - Date.now())}.` : "No timing data for this session yet."}</td></tr>`
    : d.rows.map((r, i) => {
    const moved = lastPos[r.number] && lastPos[r.number] !== r.pos, gain = moved && r.pos < lastPos[r.number];
    const gained = r.grid ? r.grid - r.pos : null;
    const comp = r.compound || "UNKNOWN";
    const x = qx(r);
    const cutRow = d.rows[i + 1], below = cutRow ? qx(cutRow) : {};
    const cutNote = !cuts[i + 1] || !cutRow ? "" : Q ? (below.needs != null ? ` · ${esc(cutRow.code)} is ${below.needs.toFixed(3)}s off safety` : "")
      : cutRow.best_lap && r.best_lap ? ` · P${i + 2} is ${(cutRow.best_lap - r.best_lap).toFixed(3)}s off safety` : "";
    return `<tr data-n="${r.number}" class="click ${moved ? (gain ? "flash gain" : "flash lose") : ""} ${r.number === focus ? "focus" : ""} ${r.out ? "out" : ""} ${x.out_in ? "qout" : ""}${CMP.picked.includes(r.number) ? " cmp" : ""}" onclick="towerClick(event, ${r.number})" onmousedown="if (event.shiftKey) event.preventDefault()" title="${r.out ? esc("OUT - " + r.out.reason) : x.on_lap ? "On a flying lap" : ""}">
      <td class="pos"${CMP.picked.includes(r.number) ? ` data-cmp="${CMP.picked.indexOf(r.number) + 1}"` : ""}>${r.out ? "OUT" : r.pos}${x.on_lap ? '<span class="onlap" title="On a flying lap">●</span>' : ""}</td>
      <td><div class="drv" title="${esc(r.team || "")}"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)} <span class="nm">${esc(r.name || "")}</span></div></td>
      <td class="r">${quali ? qgap(r) : r.out ? `<span style="color:#ff5050">OUT L${r.out.lap ?? "?"}</span>` : r.pos === 1 ? "Leader" : fmtGap(r.gap)}</td>
      <td class="r">${quali ? qint(r, i) : r.out || r.pos === 1 ? "" : fmtGap(r.interval)}</td>
      ${(r.sectors || [null, null, null]).map(x => `<td class="r">${x && x.t ? `<span class="sec ${x.c}">${x.t.toFixed(3)}</span>` : '<span class="muted">-</span>'}</td>`).join("")}
      <td class="r">${fmtLap(r.last_lap)}</td>
      <td class="r ${r.fastest ? "purple" : ""}">${preQ && !r.best_lap && preQ[r.number] ? `<span class="muted" title="Qualifying (P${preQ[r.number].pos})">${fmtLap(preQ[r.number].time)}</span>` : fmtLap(r.best_lap)}${x.deleted ? ` <span class="muted" title="${x.deleted} lap time(s) deleted">✕${x.deleted}</span>` : ""}</td>
      <td><span class="tyre t-${esc(comp)}">${comp === "UNKNOWN" ? "?" : esc(comp[0])}</span>${r.tyre_age ?? "-"}</td>
      <td class="r">${r.pits}</td>
      ${Q ? qcol(r, 1) + qcol(r, 2) + qcol(r, 3) : `<td class="r">${r.grid ?? "-"}</td>
      <td class="r ${gained > 0 ? "up" : gained < 0 ? "down" : "muted"}">${gained == null ? "" : gained > 0 ? "▲" + gained : gained < 0 ? "▼" + -gained : "–"}</td>
      <td class="r" style="color:var(--gold)">${r.live_points ? "+" + r.live_points : ""}</td>`}
    </tr>${cuts[i + 1] ? `<tr class="cutline"><td colspan="14">${cuts[i + 1]}${cutNote}</td></tr>` : ""}`;
  }).join(""));
  lastPos = Object.fromEntries(d.rows.map(r => [r.number, r.pos]));
  paintTrack($("minimap"), d, 380, {});

  $("title").innerHTML = (d.title || []).map(t => `
    <div class="title pad"><div class="lbl">${esc(t.label)}</div>
      ${t.champion ? `<div class="crown"><small>World Champion</small><div>${esc(t.champion)}</div><small>title decided at the flag</small></div>`
                   : `<div class="muted" style="margin-bottom:8px">Still in it: <b style="color:var(--text)">${t.contenders.map(esc).join(", ")}</b> · ${t.points_left} pts left in ${t.rounds_left} rounds</div>`}
      ${t.table.slice(0, 5).map((e, i) => `<div class="ptsrow num"><span class="muted">${i + 1}</span><b>${esc(e.code)}</b>
        <span class="muted" style="overflow:hidden;text-overflow:ellipsis">${esc(e.name)}</span><span class="r">${e.points}</span>
        <span class="delta">${e.delta ? "+" + e.delta : ""}</span></div>`).join("")}
    </div>`).join("") || `<div class="title pad muted">${d.pre_session ? "The live title picture starts with the race - the standings going in are above the timing."
      : "No championship data for this session."}</div>`;

  const w = d.weather;
  $("wx").innerHTML = w ? `<div><span class="muted">Track</span><b>${w.track ?? "-"}°C</b></div>
    <div><span class="muted">Air</span><b>${w.air ?? "-"}°C</b></div><div><span class="muted">Humidity</span><b>${w.humidity ?? "-"}%</b></div>
    <div><span class="muted">Wind</span><b>${w.wind ?? "-"} m/s</b></div>
    <div><span class="muted">Rain</span><b style="color:${w.rain ? "#4aa3ff" : "inherit"}">${w.rain ? "YES" : "No"}</b></div>` : `<span class="muted">-</span>`;
  $("rcmini").innerHTML = d.rc.messages.filter(m => !["sector", "blue"].includes(m.kind)).slice(0, 12).map(msgRow).join("") || `<div class="msg muted">No messages yet</div>`;
}

function renderRC(d) {
  const rc = d.rc;
  $("redbox2").innerHTML = redPanel(d);
  let sec = "";
  for (let i = 1; i <= Math.max(rc.sectors, 1); i++) {
    const f = rc.sector_flags[String(i)];
    sec += `<span class="${f === "DOUBLE YELLOW" ? "DOUBLE" : f || ""}" title="Marshal sector ${i}${f ? ": " + f : ""}">${i}</span>`;
  }
  $("sectors").innerHTML = sec;
  $("periods").innerHTML = rc.periods.map(p => `<div class="p ${p.kind}">${p.kind.toUpperCase()} · L${p.start_lap ?? "?"}${p.end ? "–L" + (p.end_lap ?? "?") : " → now"}</div>`).join("") || `<span class="muted">None so far</span>`;
  const c = rc.counts;
  $("rcstats").innerHTML = [["Messages", rc.messages.length], ["Penalties", c.penalty || 0], ["Investigations", c.investigation || 0],
    ["Laps deleted", (c.lap_deleted || 0) + (c.track_limits || 0)], ["Blue flags", c.blue || 0], ["Red flags", rc.red_flags_total]]
    .map(([k, v]) => `<div class="stat"><b class="num">${v}</b><span>${k}</span></div>`).join("");
  $("rcall").innerHTML = rc.messages.filter(m => rcFilter === "all" || m.group === rcFilter).map(msgRow).join("") || `<div class="msg muted">Nothing here</div>`;
  $("stewards").innerHTML = rc.stewards.map(msgRow).join("") || `<div class="msg muted">Nothing from the stewards</div>`;
  $("rcdrivers").innerHTML = rc.drivers.map(x => {
    const row = d.rows.find(r => r.number === x.number);
    return `<tr class="click" onclick="setFocus(${x.number})"><td><div class="drv"><span class="stripe" style="background:${row ? esc(row.colour) : "#666"}"></span>${esc(x.code)}</div></td>
    <td class="r">${x.lap_deleted + x.track_limits || ""}</td><td class="r">${x.black_white || ""}</td><td class="r">${x.investigations || ""}</td>
    <td class="r ${x.penalties ? "down" : ""}">${x.penalties || ""}</td><td class="r muted">${x.blue || ""}</td></tr>`;
  }).join("") || `<tr><td colspan="6" class="muted">Clean so far</td></tr>`;
}

function renderMap(d) {
  if (M3D.on) m3dKick();            // the 3D view draws itself every frame from D
  else paintTrack($("bigmap"), d, 1000, {labels: true, corners: true});
  $("maphint").textContent = d.map.outline ? "" : "(circuit outline appears after the first clean lap is found)";
  $("maporder").innerHTML = d.rows.filter(r => !r.out).map(r => {
    const comp = r.compound || "UNKNOWN";
    return `<div class="mo${r.number === focus ? " on" : ""}" onclick="setFocus(${r.number}, true)" title="Show ${esc(r.code)} on the map">
      <b class="p">${r.pos}</b><span class="stripe" style="background:${esc(r.colour)}"></span><b>${esc(r.code)}</b><span class="nm">${esc(r.name || "")}</span>
      <span class="tyre sm t-${esc(comp)}">${comp === "UNKNOWN" ? "?" : esc(comp[0])}</span><span class="num g">${d.quali || r.gap == null ? (r.best_lap ? fmtLap(r.best_lap) : "") : r.pos === 1 ? "Leader" : fmtGap(r.gap)}</span></div>`;
  }).join("");
}

function renderStrat(d) {
  renderUndercut(d); renderPitLeague(d); renderTyreDeg(d);
  const maxLap = Math.max(d.scheduled_laps || 0, d.max_lap || 1);
  const W = 1000, rowH = 22, L = 52, H = d.strategy.length * rowH + 26;
  const X = l => L + (l - 1) / maxLap * (W - L - 10);
  let svg = "";
  d.strategy.forEach((s, i) => {
    const y = i * rowH + 4;
    svg += `<text x="4" y="${y + 15}" font-size="12" font-weight="700" fill="${esc(s.colour)}">${esc(s.code)}</text>`;
    s.stints.forEach(st => {
      if (!st.from) return;
      const x0 = X(st.from), x1 = X((st.to || st.from) + 1), col = TYRE[st.compound] || "#666";
      svg += `<rect x="${x0}" y="${y}" width="${Math.max(3, x1 - x0 - 2)}" height="${rowH - 6}" rx="3" fill="${col}" opacity=".85"/>
        ${x1 - x0 > 26 ? `<text x="${x0 + 5}" y="${y + 12}" font-size="10" font-weight="800" fill="#000">${st.compound[0]}${st.age ? "*" : ""} ${(st.to || st.from) - st.from + 1}</text>` : ""}`;
    });
  });
  for (let l = 10; l <= maxLap; l += 10) svg += `<line x1="${X(l)}" x2="${X(l)}" y1="0" y2="${H - 22}" stroke="#2a2e3a"/><text x="${X(l)}" y="${H - 6}" font-size="11" fill="#8b91a1" text-anchor="middle">L${l}</text>`;
  if (d.lap) svg += `<line x1="${X(d.lap)}" x2="${X(d.lap)}" y1="0" y2="${H - 22}" stroke="#e10600" stroke-width="2"/>`;
  $("stratchart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="min-width:640px;display:block">${svg}</svg>`;
  $("tlegend").innerHTML = Object.entries(TYRE).filter(([k]) => k !== "UNKNOWN").map(([k, v]) => `<span><i style="background:${v};height:10px;width:10px;border-radius:50%"></i>${k.toLowerCase()}</span>`).join("") + `<span class="muted">* used set</span>`;
  const fastest = Math.min(...d.pits.filter(p => p.stop).map(p => p.stop));
  $("pitlist").innerHTML = d.pits.map(p => `<tr><td>L${p.lap ?? "-"}</td><td><div class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span>${esc(p.code)}</div></td>
    <td class="r">${p.lane != null ? p.lane.toFixed(1) + "s" : "-"}</td><td class="r ${p.stop === fastest ? "purple" : ""}">${p.stop != null ? p.stop.toFixed(1) + "s" : "-"}</td><td class="muted">${esc(p.time)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">No stops yet</td></tr>`;
  $("ovlist").innerHTML = d.overtakes.map(o => `<div class="msg"><span class="when num">${o.lap ? "L" + o.lap : ""} ${esc(o.time)}</span>
    <span><b style="color:${esc(o.by.colour)}">${esc(o.by.code)}</b> passes <b style="color:${esc(o.on.colour)}">${esc(o.on.code)}</b> for P${o.pos}</span></div>`).join("") || `<div class="msg muted">No overtakes yet</div>`;
}

function renderGaps(d) {
  const series = teammateDash(d.gaps.filter(g => g.points.length > 1).map(g => ({colour: g.colour, code: g.code,
    points: g.points.filter(p => typeof p[1] === "number" && p[1] < 120)})));
  const t0 = Math.min(...series.flatMap(s => s.points.map(p => p[0])));
  const bands = (d.rc.periods || []).map(p => ({from: new Date(p.start) / 1000, to: p.end ? new Date(p.end) / 1000 : Date.now() / 1000,
    color: p.kind === "red" ? "#e10600" : "#ffb000"}));
  $("gapchart").innerHTML = lineChart(series, {H: 420, yInvert: true, yLabel: "seconds behind leader", yMin: 0,
    xFmt: v => new Date(v * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}), yFmt: v => v.toFixed(0) + "s", bands});
  $("glegend").innerHTML = series.map(s => `<span><i style="background:${esc(s.colour)}${s.dash ? ";opacity:.6" : ""}"></i>${esc(s.code)}</span>`).join("")
    + `<span class="muted"><i style="background:#ffb000"></i>SC/VSC</span><span class="muted"><i style="background:#e10600"></i>red flag</span>`;
}

function renderDriver(d) {
  fillH2HSelects(d);
  const f = d.focus;
  if (!f) {
    $("drvbody").classList.add("hidden");
    $("drvhead").innerHTML = `<div class="empty">Pick a driver above, or click one in the timing table, to stream live car telemetry.</div>`;
    return;
  }
  $("drvbody").classList.remove("hidden");
  const row = d.rows.find(r => r.number === f.number) || {};
  $("drvhead").innerHTML = `<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
    ${f.headshot ? `<img src="${esc(f.headshot)}" alt="" style="height:64px;border-radius:6px;background:${esc(f.colour)}">` : ""}
    <div><div style="font-size:22px;font-weight:800;color:${esc(f.colour)}">${esc(f.name || f.code)}</div><div class="muted">${esc(f.team || "")}</div></div>
    <div class="stats"><div class="stat"><span>Position</span><b>P${f.pos ?? "-"}</b></div><div class="stat"><span>Gap</span><b class="num" style="font-size:20px">${row.pos === 1 ? "Leader" : fmtGap(row.gap) || "-"}</b></div>
    <div class="stat"><span>Best lap</span><b class="num" style="font-size:20px">${fmtLap(f.best)}</b></div>
    <div class="stat"><span>Tyre</span><b style="font-size:20px">${esc((row.compound || "?").toLowerCase())} · ${row.tyre_age ?? "-"} laps</b></div></div></div>`;
  const tel = f.tel || [], now = tel[tel.length - 1];
  $("telnow").innerHTML = now ? `<div class="stats">
      <div class="stat"><span>Speed</span><b class="num">${now.speed ?? "-"}<small style="font-size:12px"> km/h</small></b></div>
      <div class="stat"><span>Gear</span><b class="num">${now.gear ?? "-"}</b></div><div class="stat"><span>RPM</span><b class="num">${now.rpm ?? "-"}</b></div></div>
    <div style="margin-top:10px;display:grid;grid-template-columns:70px 1fr;gap:6px;align-items:center">
      <span class="muted">Throttle</span><div class="gauge"><i style="width:${now.throttle || 0}%;background:var(--green)"></i></div>
      <span class="muted">Brake</span><div class="gauge"><i style="width:${now.brake ? 100 : 0}%;background:var(--red)"></i></div></div>
    <div class="muted" style="font-size:12px;margin-top:6px">Sample ${esc(now.date.slice(11, 19))} UTC · ~3.7 Hz</div>` : `<span class="muted">Waiting for car data…</span>`;
  if (tel.length > 2) {
    const t0 = new Date(tel[0].date) / 1000, pts = k => tel.map(s => [new Date(s.date) / 1000 - t0, s[k] ?? 0]);
    $("telchart").innerHTML = lineChart([{colour: f.colour, points: pts("speed"), width: 2.5}], {H: 200, yLabel: "speed km/h", yMin: 0, xFmt: v => v.toFixed(0) + "s"})
      + lineChart([{colour: "#39b54a", points: pts("throttle")}, {colour: "#e10600", points: tel.map(s => [new Date(s.date) / 1000 - t0, s.brake ? 100 : 0])}],
        {H: 130, yLabel: "throttle (green) / brake (red) %", yMin: 0, yMax: 100, xFmt: v => v.toFixed(0) + "s"});
  } else $("telchart").innerHTML = "";
  const laps = (f.laps || []).filter(l => l.t);
  const med = laps.map(l => l.t).sort((a, b) => a - b)[Math.floor(laps.length / 2)] || 0;
  const clean = laps.filter(l => l.t < med * 1.08);
  $("lapchart").innerHTML = lineChart([{colour: f.colour, points: clean.map(l => [l.lap, l.t]), width: 2}],
    {H: 200, yInvert: true, yLabel: "lap time (slow laps hidden)", xFmt: v => "L" + v.toFixed(0), yFmt: v => fmtLap(v)});
  const best = k => Math.min(...laps.filter(l => l[k]).map(l => l[k]));
  const b = {t: best("t"), s1: best("s1"), s2: best("s2"), s3: best("s3")};
  $("sectab").innerHTML = laps.slice(-8).reverse().map(l => `<tr><td>L${l.lap}${l.out ? ' <span class="muted">out</span>' : ""}</td>
    ${["t", "s1", "s2", "s3"].map(k => `<td class="r ${l[k] === b[k] ? "purple" : ""}">${k === "t" ? fmtLap(l[k]) : l[k] ? l[k].toFixed(3) : "-"}</td>`).join("")}
    <td class="r muted">${l.st ?? "-"}</td></tr>`).join("");
  $("drvrc").innerHTML = (f.stewarding || []).slice().reverse().map(m => msgRow({...m, lap: m.lap})).join("") || `<div class="msg muted">Nothing from the stewards</div>`;
}

// Team radio is rendered incrementally: an <audio> that is playing is never rebuilt (rebuilding it on every
// update was what cut clips off). New messages are added on top; a new session starts a fresh list.
const RADIO = {skip: new Map(), session: null, seen: new Set(), queue: [], playingAll: false, rows: new Map(), text: new Map(), loaded: false, toasted: new Set()};
function radioRow(r) {
  const el = document.createElement("div");
  el.className = "msg radio"; el.dataset.num = r.number;
  el.style.cssText = "grid-template-columns:110px 90px 1fr auto;align-items:center";
  const dl = `/api/radio_file?url=${encodeURIComponent(r.url)}&lap=${r.lap ?? ""}`;
  el.innerHTML = `<span class="when num">${r.lap ? "L" + r.lap : ""} ${esc(r.time)}</span>
    <span class="drv"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)}</span>
    <audio controls preload="none" src="${esc(r.url)}"></audio>
    <a class="btn" href="${esc(dl)}" download title="Download this clip">⬇</a>
    <div class="rtext wait"></div><div class="rtags"></div>`;
  const au = el.querySelector("audio");
  au.addEventListener("ended", radioNext);
  // starting from the top: jump past F1's intro sting (the server measured how much of it this clip has)
  au.addEventListener("play", () => {
    const skip = RADIO.skip.get(r.url);
    if (!$("radioskip").checked || !skip || au.currentTime > 0.05) return;
    const go = () => { if (au.currentTime < skip) au.currentTime = skip; };
    au.readyState >= 1 ? go() : au.addEventListener("loadedmetadata", go, {once: true});
  });
  RADIO.rows.set(r.url, el);
  return el;
}
function renderRadio(d) {
  const list = $("radiolist");
  if (RADIO.session !== d.session_key) { RADIO.session = d.session_key; RADIO.seen = new Set(); RADIO.queue = []; RADIO.rows = new Map(); RADIO.text = new Map(); RADIO.loaded = false; RADIO.toasted = new Set(); list.innerHTML = ""; }
  const fresh = (d.radio || []).filter(r => r.url && !RADIO.seen.has(r.url));
  (d.radio || []).forEach(r => { if (r.skip != null) RADIO.skip.set(r.url, r.skip); });
  if (!d.radio || !d.radio.length) { if (!list.children.length) list.innerHTML = `<div class="msg muted empty">No team radio published for this session yet.</div>`; }
  const first = !RADIO.seen.size;
  // d.radio is newest first: insert the new ones above what's there, keeping that order
  const anchor = list.querySelector(".msg.radio");
  fresh.forEach(r => { RADIO.seen.add(r.url); list.insertBefore(radioRow(r), anchor); });
  if (fresh.length) { const e = list.querySelector(".empty"); if (e) e.remove(); }
  // driver filter options
  const sel = $("radiodrv"), have = new Set([...sel.options].map(o => o.value));
  (d.radio || []).forEach(r => { if (!have.has(String(r.number))) { have.add(String(r.number)); sel.insertAdjacentHTML("beforeend", `<option value="${r.number}">${esc(r.code)}</option>`); } });
  // transcripts: only touch the text element, never the player
  const stt = d.stt || {};
  (d.radio || []).forEach(r => {
    const el = RADIO.rows.get(r.url); if (!el) return;
    const t = r.stt, key = t ? (t.error ? "!" : t.text) : null;
    if (RADIO.text.get(r.url) === key && key !== null) return;
    const box = el.querySelector(".rtext");
    if (t && !t.error) {
      RADIO.text.set(r.url, key); box.className = "rtext"; box.textContent = t.text ? "“" + t.text + "”" : "(no speech)";
      const hits = radioHits(t.text || "");
      el.classList.toggle("flagged", hits.length > 0);
      if (hits.length) el.style.setProperty("--kc", RADIO_GROUPS[hits[0].group] || "#ffb000");
      el.querySelector(".rtags").innerHTML = [...new Set(hits.map(h => h.group))].map(g =>
        `<span class="rtag" style="--kc:${RADIO_GROUPS[g] || "#ffb000"}">${esc(g)}</span>`).join("");
      // a live call that's just come in (not the backlog, not an old clip transcribed late)
      if (hits.length && RADIO.loaded && !RADIO.toasted.has(r.url) && d.now && r.date && new Date(d.now) - new Date(r.date) < 5 * 60000) {
        RADIO.toasted.add(r.url);
        const grp = [...new Set(hits.map(h => h.group))];
        toast("radio", `${r.code}: ${grp.join(" · ")}`, "“" + (t.text.length > 110 ? t.text.slice(0, 107) + "…" : t.text) + "”",
              RADIO_GROUPS[hits[0].group] || "#ffb000");
      }
    }
    else if (t && t.error) { RADIO.text.set(r.url, key); box.className = "rtext wait"; box.textContent = "couldn't transcribe this clip"; }
    else { box.className = "rtext wait"; box.textContent = stt.state === "unavailable" ? "" : stt.state === "loading model" ? "loading speech-to-text…" : "transcribing…"; }
  });
  $("sttstatus").textContent = stt.state === "unavailable" ? "Speech-to-text not installed - see the README to set it up." :
    stt.state ? `Speech-to-text: ${stt.state}${stt.model ? " (" + stt.model + ", runs on this computer)" : ""}${stt.queued ? ` · ${stt.queued} clip${stt.queued > 1 ? "s" : ""} waiting` : ""}` : "";
  RADIO.loaded = true;
  radioFilter();
  const nflag = document.querySelectorAll("#radiolist .msg.radio.flagged").length;
  $("radiocount").textContent = `${RADIO.seen.size} message${RADIO.seen.size === 1 ? "" : "s"}${nflag ? ` · ${nflag} flagged` : ""}`;
  // auto-play: queue messages that arrive while you're watching (not the backlog on first load)
  if (!first && $("radioauto").checked) fresh.slice().reverse().forEach(r => RADIO.queue.push(r.url));
  if (RADIO.queue.length && !radioBusy()) radioNext();
}
function radioFilter() {
  const v = $("radiodrv").value, q = ($("radiosearch").value || "").trim().toLowerCase(), fl = $("radioflagged").checked;
  document.querySelectorAll("#radiolist .msg.radio").forEach(el => {
    const box = el.querySelector(".rtext"), txt = box.textContent;
    const hit = !q || txt.toLowerCase().includes(q) || el.querySelector(".drv").textContent.toLowerCase().includes(q);
    el.style.display = (!v || el.dataset.num === v) && hit && (!fl || el.classList.contains("flagged")) ? "" : "none";
    // highlight alert words and the search match (re-derived from the plain text each time)
    if (box.classList.contains("wait")) return;
    let html = esc(txt);
    const hits = radioHits(txt);
    if (hits.length) html = html.replace(radioRegex(), (m, ...a) => {
      const g = radioGroupOf(m); return `<mark class="kw" style="--kc:${RADIO_GROUPS[g] || "#ffb000"}">${m}</mark>`; });
    if (q && txt.toLowerCase().includes(q))
      html = html.replace(new RegExp("(?![^<]*>)" + esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), m => `<mark>${m}</mark>`);
    box.innerHTML = html;
  });
  $("radiozip").href = "/api/radio_zip" + (v ? "?driver=" + v : "");
}
// ---- alert words: flag radio messages that mention a pit call, damage, a car problem, rain...
const RADIO_GROUPS = {"pit call": "#2bd4ff", damage: "#ff5050", "car problem": "#ff8a00", weather: "#6aa8ff",
  tyres: "#ffd300", "team orders": "#c77dff", keyword: "#ffb000"};
const RADIO_KW_DEFAULT = [
  "pit call: box", "pit call: box box", "pit call: boxbox", "pit call: stay out", "pit call: plan b", "pit call: plan c", "pit call: pit confirm",
  "damage: damage", "damage: puncture", "damage: front wing", "damage: floor", "damage: broken", "damage: vibration", "damage: debris",
  "car problem: engine", "car problem: gearbox", "car problem: brakes", "car problem: no power", "car problem: lost power",
  "car problem: battery", "car problem: overheating", "car problem: smoke", "car problem: fire", "car problem: stop the car",
  "car problem: retire", "car problem: problem", "car problem: issue",
  "weather: rain", "weather: raining", "weather: wet", "weather: drizzle", "weather: spitting", "weather: inters", "weather: intermediates",
  "tyres: graining", "tyres: blistering", "tyres: no grip", "tyres: tyres are gone", "tyres: tyres are dead",
  "team orders: let him by", "team orders: let him through", "team orders: swap", "team orders: hold position", "team orders: don't fight",
].join("\n");
let RADIO_KW = [], RADIO_RE = null;
function radioKwParse(text) {
  return text.split(/\n/).map(l => l.trim()).filter(Boolean).map(l => {
    const m = l.match(/^([^:]{1,24}):\s*(.+)$/);
    return m ? {group: m[1].trim().toLowerCase(), word: m[2].trim().toLowerCase()} : {group: "keyword", word: l.toLowerCase()};
  }).filter(k => k.word.length > 1);
}
function radioKwLoad(text) {
  RADIO_KW = radioKwParse(text);
  // longest first so "box box" wins over "box"; whole words only; "box" also matches "boxbox"
  const alts = RADIO_KW.map(k => k.word).sort((a, b) => b.length - a.length)
    .map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "[\\s,.-]+").replace(/'/g, "['’]?"));
  RADIO_RE = alts.length ? new RegExp(`(?<![\\w'])(?:${alts.join("|")})(?![\\w'])`, "gi") : null;
  RADIO.text = new Map();      // re-tag every transcript on the next update
}
function radioRegex() { RADIO_RE.lastIndex = 0; return RADIO_RE; }
function radioGroupOf(m) {
  const norm = m.toLowerCase().replace(/[\s,.-]+/g, " ").replace(/['’]/g, "");
  const k = RADIO_KW.find(k => k.word.replace(/'/g, "") === norm) || RADIO_KW.find(k => norm.startsWith(k.word.replace(/'/g, "")));
  return k ? k.group : "keyword";
}
function radioHits(text) {
  if (!RADIO_RE || !text) return [];
  return [...text.matchAll(radioRegex())].map(m => ({word: m[0], group: radioGroupOf(m[0])}));
}
function radioKwSave() {
  const t = $("radiokw").value; store.set("radioKw", t); radioKwLoad(t);
  $("radiokwnote").textContent = `Saved - ${RADIO_KW.length} phrases.`; if (D) renderRadio(D);
}
function radioKwReset() { $("radiokw").value = RADIO_KW_DEFAULT; radioKwSave(); }
$("radiokw").value = store.get("radioKw", RADIO_KW_DEFAULT);
$("radioskip").checked = store.get("radioSkip", true);
radioKwLoad($("radiokw").value);

function radioBusy() { return [...document.querySelectorAll("#radiolist audio")].some(a => !a.paused && !a.ended); }
function radioNext() {
  const url = RADIO.queue.shift(); if (!url) { RADIO.playingAll = false; return; }
  const a = [...document.querySelectorAll("#radiolist audio")].find(x => x.src === url);
  if (!a) return radioNext();
  a.closest(".msg").scrollIntoView({block: "nearest"});
  a.play().catch(() => {});     // the browser may need one click on the page first before it allows sound
}
function radioPlayAll() {   // oldest to newest, respecting the driver filter
  RADIO.queue = [...document.querySelectorAll("#radiolist .msg.radio")].filter(el => el.style.display !== "none")
    .reverse().map(el => el.querySelector("audio").src);
  RADIO.playingAll = true;
  document.querySelectorAll("#radiolist audio").forEach(a => a.pause());
  radioNext();
}

function renderSeason(s) {
  S = s;
  safe("home", homeSeason);
  const lead = s.drivers.length ? s.drivers[0].points : 1;
  $("dh").textContent = `${s.year} Drivers - after ${s.completed}/${s.rounds} rounds · ${s.points_left} pts left` + (s.spoiler_hidden ? " · spoiler mode: this race hidden" : "");
  $("champbanner").innerHTML = s.driver_champion ? `<div class="champ-banner"><div class="t">${s.year} FORMULA 1 WORLD CHAMPION</div><div class="n">${esc(s.driver_champion)}</div></div>` : "";
  renderProgression(s); renderSim(s);
  $("dtable").innerHTML = s.drivers.map((e, i) => `<tr onclick="driverCard('${esc(e.code)}')" title="Season card">
    <td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(e.colour)}"></span>${esc(e.name)} <small>${esc(e.team)}</small></div></td>
    <td class="r"><b>${e.points}</b></td><td style="width:30%"><div class="ptsbar" style="width:${e.points / lead * 100}%;background:${esc(e.colour)}"></div></td>
    <td class="r muted">${e.wins}W ${e.podiums}P</td><td class="${e.alive ? "up" : "muted"}">${e.alive ? "in contention" : ""}</td></tr>`).join("");
  const tl = s.teams.length ? s.teams[0].points : 1;
  $("ttable").innerHTML = s.teams.map((e, i) => `<tr>
    <td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(e.colour)}"></span>${esc(e.name)}</div></td>
    <td class="r"><b>${e.points}</b></td><td style="width:35%"><div class="ptsbar" style="width:${e.points / tl * 100}%;background:${esc(e.colour)}"></div></td>
    <td>${e.name === s.team_champion ? '<span style="color:var(--gold);font-weight:800">CHAMPIONS</span>' : e.alive ? '<span class="up">in contention</span>' : ""}</td></tr>`).join("");
  $("caltable").innerHTML = s.calendar.map(r => `<tr><td class="num muted">R${r.round}</td><td><b>${esc(r.name)}</b>${r.sprint ? ' <span style="color:var(--amber)">SPRINT</span>' : ""}</td>
    <td class="muted num">${r.date}</td><td>${r.hidden && (r.done || new Date(r.date) <= new Date()) ? '<span class="hiddenres">hidden - spoiler mode</span>' : r.winner ? ico("trophy") + esc(r.winner) : r.done ? "results pending" : '<span class="muted">upcoming</span>'}</td></tr>`).join("")
    + (s.cancelled.length ? `<tr><td></td><td colspan="3" class="muted">Cancelled: ${s.cancelled.map(esc).join(", ")}</td></tr>` : "");
}

function raceClock() {
  if (!D || !D.insights) return;
  if (D.quali && D.quali.phase) { $("clock").innerHTML = ""; return; }      // the Q1/Q2/Q3 clock is in the lap slot
  if (!isRaceSession(D)) {        // practice: time left in the session (red flags don't stop the practice clock)
    const end = D.session_end ? Math.round((new Date(D.session_end) - vnow()) / 1000) : null;
    $("clock").innerHTML = end == null || D.status === "CHEQUERED" ? "" : end > 0
      ? `Session ends in <b class="num">${Math.floor(end / 60)}:${String(end % 60).padStart(2, "0")}</b>` : "Session time up";
    return;
  }
  const s = Math.max(0, Math.round((vnow() - new Date(D.insights.session_start)) / 1000));
  const left = 3 * 3600 - s, f = v => `${Math.floor(v / 3600)}:${String(Math.floor(v % 3600 / 60)).padStart(2, "0")}:${String(v % 60).padStart(2, "0")}`;
  $("clock").innerHTML = D.status === "CHEQUERED" ? "" : `Race clock <b class="num">${f(s)}</b> · <span title="A race must finish within 3 hours including stoppages">${left > 0 ? f(left) + " to 3 h limit" : "3 h limit reached"}</span>`;
}

function renderInsights(d) {
  const I = d.insights;
  $("battles").innerHTML = I.battles.map(b => `<div class="row"><b>P${b.pos}</b>
    <span><b style="color:${esc(b.behind.colour)}">${esc(b.behind.code)}</b> <span class="nm">${esc(b.behind.name || "")}</span> → attacking
      <b style="color:${esc(b.ahead.colour)}">${esc(b.ahead.code)}</b></span><span class="num">${b.gap.toFixed(3)}s</span></div>`).join("") || `<div class="row muted"><span></span>No close battles right now</div>`;
  $("pitlossinfo").textContent = I.pit_loss ? `pit loss ${I.pit_loss.toFixed(1)}s (${I.pit_mode === "normal" ? "green flag" : I.pit_mode.toUpperCase()})` : "";
  $("pitnow").innerHTML = d.status === "RED" ? `<tr><td colspan="5" class="muted">Not meaningful during a red flag - everyone gets free tyres in the pit lane.</td></tr>`
    : I.pit_now.map(p => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span>${esc(p.code)} <span class="nm">${esc(p.name || "")}</span></div></td>
      <td class="r">P${p.now}</td><td class="r"><b>P${p.rejoin}</b></td><td class="r ${p.lost > 0 ? "down" : "muted"}">${p.lost > 0 ? "-" + p.lost : "0"}</td>
      <td class="muted">${p.behind ? `${esc(p.behind)} by ${p.margin}s` : "clear"}</td></tr>`).join("") || `<tr><td colspan="5" class="muted">Waiting for gaps…</td></tr>`;
  const best = I.pace.length ? I.pace[0].avg : 0;
  $("pace").innerHTML = I.pace.map((p, i) => `<div class="row"><span class="muted">${i + 1}</span>
    <span class="drv"><span class="stripe" style="background:${esc(p.colour)}"></span>${esc(p.code)} <span class="nm">${esc(p.name || "")}</span></span>
    <span class="num">${fmtLap(p.avg)} <span class="muted">${i ? "+" + (p.avg - best).toFixed(3) : ""}</span></span></div>`).join("") || `<div class="row muted"><span></span>Not enough laps yet</div>`;
  $("theo").innerHTML = I.theoretical.map(t => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.code)} <span class="nm">${esc(t.name || "")}</span></div></td>
    <td class="r">${fmtLap(t.theoretical)}</td><td class="r">${fmtLap(t.best)}</td><td class="r muted">${t.best ? "+" + (t.best - t.theoretical).toFixed(3) : "-"}</td></tr>`).join("");
  const key = $("trapsel").value, traps = I.traps.filter(t => t[key]).sort((a, b) => b[key] - a[key]);
  const top = traps.length ? traps[0][key] : 1, low = traps.length ? traps[traps.length - 1][key] - 5 : 0;
  $("traps").innerHTML = traps.map((t, i) => `<div class="row"><span class="muted">${i + 1}</span>
    <span><span class="drv"><span class="stripe" style="background:${esc(t.colour)}"></span>${esc(t.code)} <span class="nm">${esc(t.name || "")}</span></span>
    <div class="hbar" style="width:${(t[key] - low) / (top - low) * 100}%;background:${esc(t.colour)};margin-top:4px"></div></span>
    <span class="num">${t[key]} km/h</span></div>`).join("");
  const series = teammateDash(I.lap_chart.map(l => ({colour: l.colour, code: l.code, points: l.points})));
  $("lapchart2").innerHTML = lineChart(series, {H: 380, yInvert: true, yMin: 1, yMax: Math.max(20, d.rows.length), yLabel: "position",
    xFmt: v => "L" + v.toFixed(0), yFmt: v => "P" + v.toFixed(0)})
    + `<div class="legend" style="margin-top:6px">${series.map(x => `<span><i style="background:${esc(x.colour)}"></i>${esc(x.code)}</span>`).join("")}</div>`;
  const wh = d.weather_hist || [];
  $("wxchart").innerHTML = wh.length > 1 ? lineChart([{colour: "#ff7a3d", points: wh.map((w, i) => [i, w.track])}, {colour: "#3d8bff", points: wh.map((w, i) => [i, w.air])}],
    {H: 170, yLabel: "°C - track (orange) / air (blue)", xFmt: v => (wh[Math.round(v)] || {}).t || "", yFmt: v => v.toFixed(0)}) : `<span class="muted">Collecting…</span>`;
}
$("trapsel").onchange = () => D && renderInsights(D);

/* live clocks for the red-flag panel */
setInterval(() => {
  raceClock();
  renderSync();
  if ($("age")) $("age").textContent = dataAge();
  document.querySelectorAll("[data-countdown]").forEach(el => el.textContent = countdown(el.dataset.countdown));
  document.querySelectorAll("[data-since]").forEach(el => el.textContent = since(el.dataset.since));
}, 1000);

// the local server going away (restart, laptop asleep) shows one calm banner and clears itself on recovery
const NET = {down: 0};
async function poll(url, fn, onFail) {
  let body;
  try {
    const r = await fetch(url, {cache: "no-store"});
    body = await r.json();
    if (NET.down) { NET.down = 0; $("offline").classList.add("hidden"); }     // it answered: it's there
    if (!r.ok) {
      if (onFail) safe(url.split("?")[0], () => onFail(body, r.status));
      throw new Error(body && body.error || "HTTP " + r.status);
    }
  } catch (e) {
    if (e instanceof TypeError || e instanceof SyntaxError) {     // network down / server half-restarted
      if (!NET.down) NET.down = Date.now();
      $("offline").classList.remove("hidden");
      $("offline").innerHTML = `<b>Reconnecting to F1 Hub…</b> the local server isn't answering (${Math.round((Date.now() - NET.down) / 1000)}s). Is <code>python3 -m f1hub gui</code> still running?`;
    } else console.warn(url, e);
    return;
  }
  safe(url.split("?")[0], () => fn(body));
}
// keyboard: never steal keys while typing in a box
function toggleHelp(on) { $("help").classList.toggle("hidden", on === undefined ? undefined : !on); }
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest("input, select, textarea, [contenteditable]")) { if (e.key === "Escape") e.target.blur(); return; }
  const k = e.key, rep = D && D.mode === "replay" && D.replay;
  let done = true;
  if (k >= "0" && k <= "9") openTab(DIGIT_TABS[(+k + 9) % 10]);
  else if (k === "[" || k === "]") openTab(TAB_ORDER[(TAB_ORDER.indexOf(activeTab) + (k === "]" ? 1 : TAB_ORDER.length - 1)) % TAB_ORDER.length]);
  else if (k === "h" || k === "H") openTab("home");
  else if (k === "e" || k === "E") openTab("explore");
  else if ((k === "," || k === ".") && rep) jumpEvent(k === "." ? 1 : -1);
  else if (k === "s" || k === "S") toggleDrawer();
  else if (k === "m" || k === "M") openPip();
  else if (k === "n" || k === "N") setMapOpt("names");
  else if (k === "?") toggleHelp();
  else if (k === "d" || k === "D") toggleStatus();
  else if (k === "Escape") { toggleHelp(false); toggleStatus(false); $("drawer").classList.remove("open"); }
  else if (k === " " && rep) togglePlay();
  else if ((k === "ArrowLeft" || k === "ArrowRight") && rep) seekRel((k === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 60 : 10));
  else if ((k === "l" || k === "L") && D && D.mode === "replay") ctl({mode: "live"});
  else if ((k === "+" || k === "=" || k === "-") && D && D.mode === "live") setDelay(curDelay() + (k === "-" ? -5 : 5));
  else if ((k === "+" || k === "=" || k === "-") && rep) stepSpeed(k === "-" ? -1 : 1);
  else done = false;
  if (done) e.preventDefault();
});
window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t && t !== activeTab) openTab(t); });
