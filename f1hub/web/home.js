/* F1 Hub dashboard - the home page between sessions.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- home: between sessions ---------- */
let H = null;
const DAY = 86400000;
function cdParts(ms) { ms = Math.max(0, ms); return [Math.floor(ms / DAY), Math.floor(ms / 3600000) % 24, Math.floor(ms / 60000) % 60, Math.floor(ms / 1000) % 60]; }
function cdShort(ms) {
  if (ms <= 0) return "now";
  const [d, h, m] = cdParts(ms);
  return d ? `in ${d}d ${h}h` : h ? `in ${h}h ${m}m` : `in ${m} min`;
}
const fmtWhen = iso => new Date(iso).toLocaleString([], {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"});
const fmtTime = iso => new Date(iso).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
const fmtDay = iso => new Date(iso).toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"});
async function loadHome() {
  await poll("/api/home", h => {
    const was = H && H.state;
    H = h;
    renderHome();
    if (LANDING.auto && !LANDING.touched) { LANDING.auto = false; if (h.state !== "live" && activeTab === "live") openTab("home"); }
    if (was && was !== "live" && h.state === "live" && h.live) toast("start", `${h.live.name} is live`, h.live.meeting, "#e10600");
  });
}
function renderHome() {
  if (!H) return;
  safe("home", () => {
    const h = H, now = Date.now();
    // hero
    const hero = $("homehero");
    if (h.state === "live" && h.live) {
      const x = h.live;
      hero.className = "hero live";
      hero.innerHTML = `<div class="kick">Live now</div><h1>${esc(x.name)}<small>${esc(x.meeting)}${x.round ? ` · Round ${x.round}` : ""} · ${esc(x.circuit || "")}</small></h1>
        <div class="when">Started ${fmtTime(x.start)} <span>· ends ~${fmtTime(x.end)} your time</span></div>
        <div class="row2" style="margin-top:16px"><button class="btn primary" onclick="openTab('live')">Open live timing</button>
        <button class="btn" onclick="openTab('map')">Track map</button><button class="btn" onclick="toggleDrawer()">Set a delay</button></div>
        ${x.round ? `<div class="num-bg">${x.round}</div>` : ""}`;
    } else if (h.next) {
      const x = h.next, race = (h.next_weekend || []).find(s => s.name === "Race");
      hero.className = "hero";
      hero.innerHTML = `<div class="kick">Next up${x.round ? ` · Round ${x.round} of ${h.rounds}` : ""}</div>
        <h1>${esc(x.name)}<small>${esc(x.meeting)} · ${esc(x.circuit || x.location || "")}</small></h1>
        <div class="when">${fmtWhen(x.start)} <span>your time</span></div>
        <div class="cd" data-cdto="${esc(x.start)}"></div>
        <div class="row2">${race && race.key !== x.key ? `<span class="muted">Race ${fmtWhen(race.start)} · <b data-cdshort="${esc(race.start)}"></b></span>` : ""}
        <button class="btn" onclick="downloadIcs()">📅 Add the weekend to your calendar</button></div>
        ${x.round ? `<div class="num-bg">${x.round}</div>` : ""}`;
    } else {
      hero.className = "hero off";
      hero.innerHTML = `<div class="kick">Season complete</div><h1>${h.year} is in the books<small>${S && S.driver_champion ? "World Champion: " + esc(S.driver_champion) : "See the final standings"}</small></h1>
        <div class="row2" style="margin-top:14px"><button class="btn" onclick="openTab('champ')">Final standings</button></div>`;
    }
    tickHome();
    // results (latest race, plus qualifying etc. when that's newer)
    $("homeresults").innerHTML = [h.last_session, h.last_race].filter(Boolean).map(resultCard).join("");
    // weekend schedule: the upcoming weekend, or the one in progress
    const wk = h.next_weekend && h.next_weekend.length ? h.next_weekend : h.this_weekend || [];
    $("homewkh").textContent = wk.length ? wk[0].meeting : "Weekend schedule";
    $("homeweekend").innerHTML = wk.length ? wk.map(sx => {
      const on = h.live && h.live.key === sx.key;
      return `<div class="sched ${sx.done && !on ? "done" : ""} ${on ? "nowon" : ""} ${sx.name === "Race" ? "race" : ""}">
        <b>${esc(sx.name)}</b><span class="st" ${sx.done || on ? "" : `data-cdshort="${esc(sx.start)}"`}>${on ? "LIVE" : sx.done ? "✓ done" : ""}</span>
        <span class="t">${fmtDay(sx.start)} · ${fmtTime(sx.start)} - ${fmtTime(sx.end)}</span></div>`;
    }).join("") : `<div class="empty">No sessions scheduled.</div>`;
    // replays
    $("homerecent").innerHTML = (h.recent || []).length ? h.recent.map(x =>
      `<button class="btn" onclick="replaySession(${+x.key})">▶ ${esc(x.name)}<small>${esc(x.meeting)} · ${fmtDay(x.start)}</small></button>`).join("")
      : `<span class="muted">Nothing to replay yet this season.</span>`;
    homeSeason();
    tickHome();
  });
  homeCircuit();
}
function resultCard(r) {
  const key = "revealed." + r.key;
  const title = `${esc(r.meeting)} - ${esc(r.name)}${r.round ? ` <span class="sub">Round ${r.round} · ${fmtDay(r.start)}</span>` : ""}`;
  if (r.hidden || !r.results) return `<div class="card"><h2>${title}</h2><div class="empty">🙈 Result hidden - you're watching this one delayed or as a replay (spoiler mode).</div></div>`;
  const covered = (D && D.settings ? D.settings.spoiler : true) && !store.get(key, false);   // unsure yet? keep it covered
  const res = r.results, quali = r.type === "Qualifying";
  const pod = [res[1], res[0], res[2]].filter(Boolean).map(x => `<div class="pod p${x.pos}" style="--tc:${esc(x.colour)}">
      ${x.headshot ? `<img src="${esc(x.headshot)}" alt="" loading="lazy">` : `<div class="ph"></div>`}
      <div class="pp">P${x.pos}</div><div class="nm2">${esc(x.last || x.code)}</div><div class="tm">${esc(x.team || "")}</div></div>`).join("");
  const rows = res.slice(3, 10).map(x => `<tr><td class="pos">${x.pos ?? "-"}</td>
      <td><div class="drv"><span class="stripe" style="background:${esc(x.colour)}"></span>${esc(x.code)} <small>${esc(x.name || "")}</small></div></td>
      <td class="r num">${x.status ? `<span class="down">${x.status}</span>` : quali ? fmtLap(x.time) : fmtGap(x.gap)}</td>
      <td class="r num muted">${x.points ? "+" + x.points : ""}</td></tr>`).join("");
  return `<div class="card"><h2>${title}</h2><div class="spoilwrap ${covered ? "covered" : ""}">
    <div class="spoilcover"><div style="font-size:28px">🙈</div><b>Result hidden (spoiler mode)</b><span class="muted">Haven't watched it yet? Replay it below first.</span>
      <button class="btn primary" onclick="revealResult('${esc(key)}')">Show the result</button></div>
    <div class="podium3">${pod}</div>
    <div class="scroll"><table class="num"><tbody>${rows}</tbody></table></div></div></div>`;
}
function revealResult(key) { store.set(key, true); renderHome(); }
function homeSeason() {
  if (!S || !S.drivers) return;
  const top = S.drivers.slice(0, 5), lead = top.length ? top[0].points : 0;
  // the standings include the latest race, so they stay covered for as long as that result is
  const lr = H && H.last_race, key = lr && "revealed." + lr.key;
  const covered = lr && !lr.hidden && (D && D.settings ? D.settings.spoiler : true) && !store.get(key, false);
  $("homechamp").innerHTML = `<div class="spoilwrap ${covered ? "covered" : ""}"><div class="spoilcover"><b>🙈 Includes the ${esc(lr ? lr.meeting : "")} result</b>
      <button class="btn primary" onclick="revealResult('${esc(key || "")}')">Show standings</button></div><div>` + (S.driver_champion ? `<div class="crown"><small>World Champion</small><div>${esc(S.driver_champion)}</div></div>` : "") +
    top.map((e, i) => `<div class="champrow"><b>${i + 1}</b><div class="drv"><span class="stripe" style="background:${esc(e.colour)}"></span>${esc(e.name)}</div>
      <b class="num">${e.points}</b><span class="gp num">${i ? "-" + (lead - e.points) : ""}</span></div>`).join("") +
    `<p class="muted" style="margin:8px 0 0;font-size:12px">${S.completed}/${S.rounds} rounds · ${S.points_left} points left${S.spoiler_hidden ? " · this race hidden" : ""}</p></div></div>`;
  const cal = (S.calendar || []).filter(c => !c.done).slice(0, 4);
  $("homecal").innerHTML = cal.length ? cal.map(c => `<div class="sched"><b>R${c.round} ${esc(c.name)}${c.sprint ? ' <span class="tag">sprint</span>' : ""}</b>
      <span class="st">${new Date(c.date).toLocaleDateString([], {day: "numeric", month: "short"})}</span><span class="t">${esc(c.country || "")}</span></div>`).join("")
    : `<div class="empty">No more rounds this season.</div>`;
}
// Live mode, following a session that isn't running: "upcoming" (not started) or "ended". The schedule alone
// isn't enough - a red flag can run a session well past its planned end - so "ended" also needs the data to have
// gone quiet, and "upcoming" needs there to be none yet.
function airState() {
  if (!D || D.mode !== "live" || D.error || (H && H.state === "live")) return "on";
  const now = Date.now(), quiet = !D.last_event || now / 1000 - D.last_event > 600;
  if (D.session_start && now < new Date(D.session_start).getTime() && !(D.rows || []).length) return "upcoming";
  if (D.session_end && now > new Date(D.session_end).getTime() && quiet) return "ended";
  return "on";
}
function offAirHeader() {   // header while nothing is on: no "live" chips, and when the next session starts
  const air = airState(), off = air !== "on";
  document.body.classList.toggle("offair", off);
  if (!off) return;
  const now = Date.now(), nx = air === "upcoming" ? {name: D.session, meeting: D.meeting, start: D.session_start} : H && H.next;
  const fc = $("feedchip");
  fc.className = "feedchip"; fc.textContent = "OFF AIR";
  fc.title = "No session running. The app checks every minute and switches to the next session when it starts.";
  $("meta").innerHTML = air === "upcoming" ? `Session not started yet<br>${esc(nx.name || "")} <b>${cdShort(new Date(nx.start) - now)}</b>`
    : nx ? `No session running<br>Next: ${esc(nx.name)} · ${esc(nx.meeting || "")} <b>${cdShort(new Date(nx.start) - now)}</b>` : "No session running";
}
function tickHome() {     // once a second: the countdowns, and the "session has ended" bar on live timing
  const now = Date.now();
  document.querySelectorAll("[data-cdto]").forEach(el => {
    const p = cdParts(new Date(el.dataset.cdto) - now);
    el.innerHTML = ["days", "hrs", "min", "sec"].map((u, i) => `<div><b>${String(p[i]).padStart(2, "0")}</b><span>${u.toUpperCase()}</span></div>`).join("");
  });
  document.querySelectorAll("[data-cdshort]").forEach(el => el.textContent = cdShort(new Date(el.dataset.cdshort) - now));
  const bar = $("endedbar");
  const air = airState(), ended = air === "ended";
  bar.classList.toggle("hidden", air === "on");
  if (air === "upcoming") bar.innerHTML = `⏱ <span><b>${esc(D.session || "This session")} hasn't started yet</b> - it starts <b>${cdShort(new Date(D.session_start) - now)}</b> (${fmtWhen(D.session_start)}). Timing appears here as soon as it does.</span>
    <button class="btn" onclick="openTab('home')">Home</button>`;
  if (ended) bar.innerHTML = `🏁 <span><b>This session has finished.</b> ${H.next ? `Next: ${esc(H.next.name)} · ${esc(H.next.meeting)} <b>${cdShort(new Date(H.next.start) - now)}</b>` : ""}</span>
    <button class="btn" onclick="openTab('home')">Home</button><button class="btn" onclick="replaySession(${+D.session_key})">Replay it</button>`;
  offAirHeader();
}
function replaySession(key) { openTab("live"); ctl({mode: "replay", session: key, at: -120, play: 1}); }
function downloadIcs() {
  const wk = (H && H.next_weekend) || [];
  if (!wk.length) return;
  const z = iso => new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const ev = wk.map(x => ["BEGIN:VEVENT", `UID:f1hub-${x.key}@localhost`, `DTSTAMP:${z(new Date().toISOString())}`, `DTSTART:${z(x.start)}`,
    `DTEND:${z(x.end)}`, `SUMMARY:F1 ${x.meeting} - ${x.name}`, `LOCATION:${x.circuit || x.location || ""}`, "END:VEVENT"].join("\r\n"));
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//F1 Hub//EN", ...ev, "END:VCALENDAR"].join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([ics], {type: "text/calendar"}));
  a.download = (wk[0].meeting || "f1-weekend").replace(/[^\p{L}\p{N}]+/gu, "_") + ".ics";
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
