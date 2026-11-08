/* F1 Hub dashboard - the virtual clock, delay and replay controls, the replay timeline.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- virtual clock, settings, replay ---------- */
let receivedAt = Date.now(), SESSIONS = [];
const store = {get(k, d) { try { const v = localStorage.getItem("f1hub." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
               set(k, v) { try { localStorage.setItem("f1hub." + k, JSON.stringify(v)); } catch (e) {} }};
// The clock the whole page runs on. Three rules keep it from jumping about:
//  - a change you make (seek, pause, speed, delay) shows at once (OPT), before the server has caught up
//  - between two server frames of the same "seek" the replay clock never runs backwards (CLOCK.floor)
//  - frames older than your last change are dropped (render / ctl / livePoll)
const CLOCK = {seek: null, boot: null, floor: 0};
let OPT = null;                   // {t, at, playing, speed} while a replay change is on its way, or {delay}
const CTL = {gen: 0, busy: 0};
function replayState() {          // playing/speed as you last set them
  const r = (D && D.replay) || {};
  return {playing: OPT && OPT.playing != null ? OPT.playing : !!r.playing, speed: OPT && OPT.speed ? OPT.speed : (r.speed || 1)};
}
function curDelay() { return OPT && OPT.delay != null ? OPT.delay : (D && D.settings && D.settings.delay) || 0; }
function vnow() {   // the moment the dashboard is showing (live, delayed or replay)
  if (!D) return new Date();
  const now = Date.now();
  if (OPT && OPT.t != null) {     // the floor follows, so the server's reply can't pull the clock back (seeks reset it)
    CLOCK.floor = OPT.t + (OPT.playing ? (now - OPT.at) * OPT.speed : 0);
    return new Date(CLOCK.floor);
  }
  if (D.mode === "replay" && D.replay) {
    const r = D.replay;
    let t = new Date(r.t).getTime() + (r.playing ? (now - receivedAt) * r.speed : 0);
    if (r.limit) t = Math.min(t, new Date(r.limit).getTime());
    if (D.seek === CLOCK.seek) t = Math.max(t, CLOCK.floor);
    CLOCK.floor = t;
    return new Date(t);
  }
  return new Date(now - curDelay() * 1000);
}
function optimistic(params) {
  const rep = D && D.mode === "replay" && D.replay;
  if (params.mode === "live") return {delay: curDelay()};
  if (params.mode === "replay" && (!rep || String(params.session) !== String(D.session_key))) return null;   // new session: wait for it
  if (rep && ("at" in params || "play" in params || "speed" in params)) {
    const st = replayState();
    const t = !("at" in params) ? vnow().getTime() : isNaN(+params.at) ? Date.parse(params.at)
      : new Date(D.replay.start).getTime() + params.at * 1000;     // a number = seconds from the session start
    return {t, at: Date.now(), playing: "play" in params ? +params.play === 1 : st.playing, speed: +(params.speed || st.speed)};
  }
  if ("delay" in params) return {delay: +params.delay};
  return OPT;
}
function togglePlay() { if (D && D.replay) ctl({play: replayState().playing ? 0 : 1}); }
function toggleDrawer() { $("drawer").classList.toggle("open"); if ($("drawer").classList.contains("open")) loadSessions(); }
async function ctl(params) {
  const gen = ++CTL.gen;
  CTL.busy++;
  OPT = optimistic(params);
  safe("mode", () => D && renderMode(D));
  try {
    const r = await fetch("/api/control?" + new URLSearchParams(params), {cache: "no-store"});
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
    if (gen === CTL.gen) { OPT = null; render(d, true); }    // a newer click supersedes this reply
  } catch (e) {
    if (gen === CTL.gen) { OPT = null; toast("error", "Couldn't change that", String(e.message || e), "#ff5050"); }
  } finally { CTL.busy--; }
  loadSeason();
}
function setDelay(v) { v = Math.max(0, Math.round(v)); $("delayrange").value = Math.min(180, v); $("delaynum").value = v; store.set("delay", v); ctl({delay: v}); }
$("delayrange").oninput = e => { $("delaynum").value = e.target.value; };
$("delayrange").onchange = e => setDelay(+e.target.value);
$("delaynum").onchange = e => setDelay(+e.target.value);
function syncNow() {
  const start = D && (D.live_lead_lap_start || D.lead_lap_start);
  if (!start) { $("syncinfo").textContent = "No lap data yet - try again in a moment."; return; }
  // the newest lap the leader started in live data is the line crossing just seen on the stream
  // (live data, even when a delay is already set - the delayed frame would give an old lap)
  const d = Math.max(0, Math.round((Date.now() - new Date(start)) / 1000));
  $("syncinfo").textContent = `Stream is ~${d}s behind live data - delay set to ${d}s.`;
  setDelay(d);
}
const SESS_BY_YEAR = {};
async function loadSessions(force) {
  const ys = $("yearsel"), thisYear = new Date().getUTCFullYear();
  if (!ys.options.length) ys.innerHTML = [...Array(thisYear - 2017).keys()].map(i => thisYear - i).map(y => `<option>${y}</option>${y === 2023 ? '<option disabled>── FastF1 ──</option>' : ""}`).join("");
  const year = ys.value || thisYear;
  if (!SESS_BY_YEAR[year] || force) {
    $("sessnote").textContent = "Loading sessions…";
    try {
      const r = await fetch("/api/sessions?year=" + year), j = await r.json();
      if (!r.ok) { $("sessnote").textContent = j.error || "Couldn't load sessions."; $("sessionsel").innerHTML = ""; return; }
      SESS_BY_YEAR[year] = j;
    } catch (e) { SESS_BY_YEAR[year] = []; }
  }
  let list = SESS_BY_YEAR[year] || [];
  const full = list.length && list[0].type !== undefined;     // older server: only this season's races and sprints
  $("sessnote").textContent = full ? `${list.length} sessions in ${year}. Practice and qualifying replay too.` +
      (year < 2023 ? " Before 2023 replays come from F1's archive through FastF1 (no team radio or overtakes; the first load takes a minute or two)." : "")
    : "Showing this season's races and sprints. Restart the F1 Hub server (after the race) to list every session of every season.";
  const type = $("typesel").value;
  if (full && type) list = list.filter(x => x.type === type || (type === "Race" && x.type === "Race") || (type === "Qualifying" && /Qualifying|Shootout/.test(x.name || "")));
  const sel = $("sessionsel"), cur = D && D.session_key;
  if (!full) { sel.innerHTML = list.slice().reverse().map(x => `<option value="${x.key}" ${x.key === cur ? "selected" : ""}>${esc(x.label)}</option>`).join(""); return; }
  const groups = [];
  list.forEach(x => { const g = groups.find(g => g.k === x.meeting_key); g ? g.items.push(x) : groups.push({k: x.meeting_key, name: x.meeting, items: [x]}); });
  sel.innerHTML = groups.reverse().map(g => `<optgroup label="${esc(g.name || "")}">${g.items.slice().reverse().map(x =>
    `<option value="${x.key}" ${x.key === cur ? "selected" : ""}>${esc(x.name)} · ${new Date(x.start).toLocaleDateString()}</option>`).join("")}</optgroup>`).join("");
}
function startReplay(at) {
  const key = $("sessionsel").value;
  if (at === -1) ctl({mode: "replay", session: key, at: new Date(Date.now() - 60000).toISOString(), play: 1});
  else ctl({mode: "replay", session: key, at: -120, play: 1});
  $("drawer").classList.remove("open");
}
function seekRel(sec) { if (D && D.replay) ctl({at: new Date(vnow().getTime() + sec * 1000).toISOString()}); }
$("scrub").onchange = e => {
  if (!D || !D.replay) return;
  const a = new Date(D.replay.start).getTime() - 120000, b = new Date(D.replay.end).getTime() + 600000;
  ctl({at: new Date(a + (b - a) * e.target.value / 1000).toISOString()});
};
let scrubbing = false; $("scrub").onpointerdown = () => scrubbing = true; $("scrub").onpointerup = () => scrubbing = false;
function streamLag(lag) {
  const v = lag && (lag.position ?? lag.intervals ?? lag.location);
  return v == null ? "" : ` · OpenF1 delay ${v.toFixed(1)} s`;
}
function renderMode(d) {
  const st = d.settings || {}, chip = $("modechip");
  const fs = d.feed && d.feed.stream, fc = $("feedchip");
  if (d.mode === "replay") { fc.className = "feedchip"; fc.textContent = "RECORDED DATA"; }
  else if (fs && fs.healthy) { fc.className = "feedchip on"; fc.textContent = "REAL-TIME STREAM"; fc.title = `MQTT stream · ${fs.received} messages` + streamLag(fs.lag) + (d.feed.push ? " · pushed to this page" : ""); }
  else { fc.className = "feedchip"; fc.textContent = "POLLING"; fc.title = fs && fs.error ? "Stream unavailable: " + fs.error : "REST polling, priority on the tab you're viewing"; }
  if (d.control_error) { $("error").classList.remove("hidden"); $("error").textContent = "Settings: " + d.control_error; }
  if (d.mode === "replay") { chip.className = "modechip replay"; chip.textContent = "REPLAY"; }
  else if (st.delay) { chip.className = "modechip delayed"; chip.textContent = `DELAYED ${st.delay}s`; }
  else { chip.className = "modechip live"; chip.textContent = "LIVE"; }
  $("spoiler").checked = !!st.spoiler;
  if (document.activeElement !== $("delaynum")) { $("delaynum").value = st.delay || 0; $("delayrange").value = Math.min(180, st.delay || 0); }
  $("scrubbar").classList.toggle("on", d.mode === "replay");
  if (d.mode === "replay" && d.replay) {
    const r = d.replay, a = new Date(r.start).getTime() - 120000, b = new Date(r.end).getTime() + 600000;
    const rs = replayState();
    $("playbtn").textContent = rs.playing ? "❚❚" : "▶";
    $("speedsel").value = String(rs.speed);
    $("replayname").textContent = r.name;
    if (!scrubbing) $("scrub").value = Math.round((vnow() - a) / (b - a) * 1000);
    renderTimeline(r, a, b);
  }
}
/* ---------- replay timeline: chapters (Q1/Q2/Q3, race start), flags and shaded spans; click to jump ---------- */
const TL = {key: "", events: []};
const TL_NAME = {red: "Red flag", sc: "Safety Car", vsc: "Virtual Safety Car", restart: "Restart", chequered: "Chequered flag", chapter: ""};
function renderTimeline(r, a, b) {
  const tl = r.timeline || {events: [], bands: []};
  const pct = t => Math.max(0, Math.min(100, (new Date(t || r.t) - a) / (b - a) * 100));
  TL.events = tl.events;
  $("bands").style.setProperty("--p", pct(vnow()) + "%");
  // the active chapter, from the replay clock (cheap, every frame)
  const now = vnow().getTime(), chapters = tl.events.filter(e => e.kind === "chapter");
  const cur = chapters.filter(e => new Date(e.t) - e.lead * 1000 <= now).pop();
  document.querySelectorAll("#chapters .btn").forEach(x => x.classList.toggle("on", !!cur && x.dataset.t === cur.t));
  // markers and buttons only when something changed: rebuilding them under the mouse would eat clicks
  const key = JSON.stringify([tl, a, b]);
  if (key === TL.key) return;
  TL.key = key;
  let alt = false;
  $("bands").innerHTML = tl.bands.map(x => {
    const l = pct(x.from), w = Math.max(0.2, pct(x.to) - l);
    const cls = x.kind === "seg" ? `b-seg${(alt = !alt) ? "" : " alt"}` : "b-red";
    return `<i class="${cls}" style="left:${l}%;width:${w}%"></i>`;
  }).join("");
  $("markers").innerHTML = tl.events.map((e, i) => {
    const what = e.kind === "chapter" ? e.label : TL_NAME[e.kind] || e.kind;
    const tip = `${what} · ${new Date(e.t).toLocaleTimeString()}${e.lap ? " · lap " + e.lap : ""}${e.text && e.kind !== "chapter" ? "\n" + e.text : ""}\nClick to jump there`;
    return `<button class="mk ${e.kind === "chapter" ? "lbl" : "dot"} k-${e.kind}" style="left:${pct(e.t)}%" title="${esc(tip)}"
      onclick="jumpTo(${i})">${e.kind === "chapter" ? esc(e.label) : ""}</button>`;
  }).join("");
  $("chapters").innerHTML = chapters.length > 1 ? chapters.map(e =>
    `<button class="btn" data-t="${esc(e.t)}" onclick="jumpTo(${tl.events.indexOf(e)})" title="Jump to the start of ${esc(e.label)}">${esc(e.label)}</button>`).join("")
    + `<button class="btn" onclick="jumpEvent(-1)" title="Previous flag or chapter (,)">⏮</button><button class="btn" onclick="jumpEvent(1)" title="Next flag or chapter (.)">⏭</button>` : "";
}
function jumpTo(i) {      // a little before the moment, to see it happen (90 s before a chequered flag: the last laps)
  const e = TL.events[i];
  if (e) ctl({at: new Date(new Date(e.t).getTime() - e.lead * 1000).toISOString()});
}
function jumpEvent(dir) {
  if (!D || !D.replay) return;
  const now = vnow().getTime(), at = e => new Date(e.t).getTime() - e.lead * 1000;
  const list = TL.events.map((e, i) => [at(e), i]).sort((x, y) => x[0] - y[0]);
  const hit = dir > 0 ? list.find(([t]) => t > now + 2000) : list.filter(([t]) => t < now - 3000).pop();
  if (hit) jumpTo(hit[1]);
}
// restore saved settings once the server answers
let restored = false;
function restoreSettings(d) {
  if (restored || !d.settings) return;
  restored = true;
  // Settings live on the server and are shared by every open tab, so only put back a saved delay when the
  // server has just (re)started with none - a second tab must never override the delay someone is watching with.
  const delay = store.get("delay", 0), spoiler = store.get("spoiler", true);
  if (d.mode === "live" && !d.settings.delay && delay > 0) ctl({delay, spoiler: spoiler ? 1 : 0});
}
$("spoiler").addEventListener("change", e => store.set("spoiler", e.target.checked));
