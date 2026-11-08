/* F1 Hub dashboard - test mode: a panel of buttons that plays each race moment on demand, using whatever session
   is loaded, so you can see them without waiting for a real start, pit stop or title decider. Switched on in
   Settings; nothing it does reaches the server or changes the data.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const DEMO = {on: store.get("testmode", false)};
const DEMO_BUTTONS = [
  ["Broadcast", [
    ["Start lights", () => { FX.demoLights = {t0: fxNow(), out: 5000 + 200 + Math.random() * 2000}; }],
    ["Safety car", () => showBanner("sc", `<b>SAFETY CAR</b><span>Deployed</span>`)],
    ["Virtual SC", () => showBanner("sc", `<b>VIRTUAL SAFETY CAR</b><span>Deployed</span>`)],
    ["Red flag", () => showBanner("red", `<b>RED FLAG</b><span>Session suspended</span>`)],
    ["Green flag", () => showBanner("green", `<b>GREEN FLAG</b><span>Racing resumes</span>`)],
    ["Fastest lap", () => { const r = demoCar(0, true); if (!r) return;
      showBanner("fl", `<b>FASTEST LAP</b><span><i style="background:${esc(r.colour)}"></i>${esc(r.code)} <em class="num">${fmtLap(r.best_lap || 81.234)}</em></span>`); }],
    ["Final lap", () => showBanner("final", `<b>FINAL LAP</b><span>Lap ${D && D.scheduled_laps || 57} of ${D && D.scheduled_laps || 57}</span>`, 6000)],
  ]],
  ["Track map", [
    ["Overtake", () => { const r = demoCar(1 + Math.floor(Math.random() * 5)); if (r) FX.flashes.push({n: r.number, text: "+1", t0: fxNow()}); }],
    ["Battle", () => { const a = demoCar(2), b = demoCar(3); if (a && b) FX.demoBattle = {a: a.number, b: b.number, until: fxNow() + 10000}; }],
    ["Pit stop", () => { const r = demoCar(4 + Math.floor(Math.random() * 6)); if (r) FX.pit[r.number] = {demo: fxNow(), phase: "lane", lim: null}; }],
    ["Purple sector", () => { const r = demoCar(0); if (r) [1, 2, 3].forEach(k => setTimeout(() => FX.sectors.push({k, code: r.code, t0: fxNow(), demo: true}), (k - 1) * 1500)); }],
    ["Chequered flag", () => { if (!demoCar(0)) return; FX.demoChequer = true; FX.chequer = fxNow(); setTimeout(() => FX.demoChequer = false, 12000); }],
    ["Select leader", () => { const r = demoCar(0); if (!r) return; if (FX.mode === "off") setMapColour("speed"); if (focus !== r.number) setFocus(r.number, true); }],
  ]],
  ["Celebrations", [
    ["Race win", () => demoCelebration({})],
    ["World Champion", () => demoCelebration({champion: true})],
    ["Constructors' Champion", () => demoCelebration({team: true})],
    ["All three", () => demoCelebration({champion: true, team: true})],
  ]],
];
// a car from the loaded session, by place; the map ones open the map so you can see them
function demoCar(i, anyTab) {
  const rows = D && D.rows ? D.rows.filter(r => r.pos && !r.out) : [];
  const onMap = rows.filter(r => D.map && (D.map.cars || []).some(c => c.number === r.number));
  const list = anyTab ? rows : onMap;
  if (!list.length) { toast("testmode", "Test mode", anyTab ? "Load a session first (live or a replay from Settings)." : "No cars on the map yet - load a session or a replay first."); return null; }
  if (!anyTab && activeTab !== "map") openTab("map");
  return list[Math.min(i, list.length - 1)];
}
function demoCelebration(want) {
  const top = D && D.rows ? D.rows.filter(r => r.pos && !r.out).slice(0, 3) : [];
  if (!top.length) { toast("testmode", "Test mode", "Load a session first (live or a replay from Settings)."); return; }
  const w = top[0], year = new Date(D.session_start || Date.now()).getFullYear();
  const mates = D.rows.filter(r => r.team && r.team === w.team).slice(0, 2);
  const ev = {podium: top, winner: w, meeting: D.meeting, session: D.session, test: true, champion: null, team: null};
  if (want.champion) ev.champion = {champion: w.name || w.code, champion_code: w.code, year, rounds_left: 2,
    champion_info: {number: w.number, colour: w.colour, team: w.team, headshot: w.headshot, points: 412, wins: 9, podiums: 16, poles: 7, margin: 61}};
  if (want.team) ev.team = {champion: w.team || "Team", year, rounds_left: 2,
    champion_info: {name: w.team || "Team", colour: w.colour, points: 688, wins: 12, margin: 97,
      drivers: (mates.length ? mates : [w]).map(r => ({code: r.code, name: r.name, headshot: r.headshot}))}};
  showCelebration(ev);        // the win first, then the titles, as on the day
}
function setTestMode(on) {
  DEMO.on = !!on; store.set("testmode", DEMO.on);
  const cb = $("testmode"); if (cb) cb.checked = DEMO.on;
  let el = $("demopanel");
  if (!DEMO.on) { if (el) el.remove(); return; }
  if (el) return;
  el = document.createElement("div"); el.id = "demopanel";
  el.innerHTML = `<div class="dp-head"><b>TEST MODE</b><span>plays each moment on the loaded session</span>
      <button class="gear iconbtn" id="dpmin" title="Fold the panel" aria-label="Fold the panel">–</button>
      <button class="gear iconbtn" title="Turn test mode off" aria-label="Turn test mode off" onclick="setTestMode(false)">${ico("x")}</button></div>
    <div class="dp-body">${DEMO_BUTTONS.map(([h, list], gi) => `<div class="dp-group"><h4>${esc(h)}</h4>
      ${list.map(([label], bi) => `<button class="btn" data-g="${gi}" data-b="${bi}">${esc(label)}</button>`).join("")}</div>`).join("")}</div>`;
  document.body.appendChild(el);
  el.querySelectorAll(".dp-body button").forEach(b => b.onclick = () => { try { DEMO_BUTTONS[+b.dataset.g][1][+b.dataset.b][1](); } catch (e) { console.warn("test mode", e); } });
  $("dpmin").onclick = () => el.classList.toggle("min");
}
setTestMode(DEMO.on);
