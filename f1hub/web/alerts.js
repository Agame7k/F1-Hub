/* F1 Hub dashboard - alerts (toasts, sound, notifications) and crash detection.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- alerts: toasts, sound, desktop notifications ---------- */
const ALERT_KINDS = {flags: "Safety car / VSC / red flag", lead: "Lead changes", fastest: "Fastest lap", penalty: "Penalties",
  out: "Retirements", weather: "Rain", pits: "Leaders pitting", finish: "Chequered flag", crash: "Crashes, spins & stopped cars",
  radio: "Team radio alert words"};
const AL = {prev: null, kinds: store.get("alerts", Object.fromEntries(Object.keys(ALERT_KINDS).map(k => [k, true]))),
  sound: store.get("alertSound", false), notify: store.get("alertNotify", false)};
$("alertkinds").innerHTML = Object.entries(ALERT_KINDS).map(([k, label]) =>
  `<label class="switch" style="font-weight:600"><input type="checkbox" data-k="${k}" ${AL.kinds[k] !== false ? "checked" : ""}> ${label}</label>`).join("");
$("alertkinds").addEventListener("change", e => { AL.kinds[e.target.dataset.k] = e.target.checked; store.set("alerts", AL.kinds); });
$("al-sound").checked = AL.sound; $("al-sound").onchange = e => { AL.sound = e.target.checked; store.set("alertSound", AL.sound); if (AL.sound) beep("#fff"); };
$("al-notify").checked = AL.notify && "Notification" in window && Notification.permission === "granted";
$("al-notify").onchange = async e => {
  if (!e.target.checked) { AL.notify = false; store.set("alertNotify", false); return; }
  if (!("Notification" in window)) { $("al-note").textContent = "This browser doesn't support desktop notifications."; e.target.checked = false; return; }
  const p = await Notification.requestPermission();
  AL.notify = p === "granted"; e.target.checked = AL.notify; store.set("alertNotify", AL.notify);
  $("al-note").textContent = AL.notify ? "Desktop notifications on - they show even when this tab is in the background." : "Notifications were blocked by the browser.";
};
let audioCtx = null;
function beep(colour) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain(), t = audioCtx.currentTime;
    o.frequency.value = colour === "#e10600" ? 440 : colour === "#ffb000" ? 660 : 880;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.15, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
    o.connect(g).connect(audioCtx.destination); o.start(t); o.stop(t + 0.4);
  } catch (e) {}
}
const TOAST_ICON = {flags: "flag", finish: "flag", radio: "mic", crash: "alert", lead: "trend", fastest: "clock", penalty: "doc",
  out: "x", weather: "rain", pits: "activity"};
function toast(kind, title, sub, colour) {
  if (AL.kinds[kind] === false) return;
  const el = document.createElement("div");
  el.className = "toast"; el.style.setProperty("--tc", colour || "#e10600");
  el.setAttribute("role", "status");
  el.innerHTML = `${ico(TOAST_ICON[kind] || "bell")}<div class="tb">${esc(title)}${sub ? `<small>${esc(sub)}</small>` : ""}</div><i class="tbar"></i>`;
  const dismiss = () => { if (el.classList.contains("out")) return; el.classList.add("out"); setTimeout(() => el.remove(), 350); };
  el.onclick = dismiss;
  // the bar along the bottom counts down the 9 s; hovering pauses it (and so the toast stays)
  el.querySelector(".tbar").addEventListener("animationend", dismiss);
  $("toasts").appendChild(el);
  while ($("toasts").children.length > 5) $("toasts").firstChild.remove();
  if (AL.sound) beep(colour);
  if (AL.notify && document.hidden && "Notification" in window && Notification.permission === "granted") {
    try { new Notification("F1 Hub - " + title, {body: sub || "", tag: kind + title, silent: !AL.sound}); } catch (e) {}
  }
}
function checkAlerts(d) {
  if (!d || !d.rows || !d.now) return;
  const p = AL.prev;
  AL.prev = d;
  // first frame, a different session, or a jump in time (replay seek / delay change): just take a new baseline
  if (!p || p.session_key !== d.session_key || Math.abs(new Date(d.now) - new Date(p.now)) > 30000 || d.loading) return;
  const lap = d.lap ? ` · lap ${d.lap}` : "";
  if (d.status !== p.status) {
    const S = {SC: ["Safety Car deployed", "#ffb000"], VSC: ["Virtual Safety Car", "#ffb000"], RED: ["RED FLAG - session suspended", "#e10600"],
               GREEN: [p.status === "RED" ? "Session resumed" : "Green flag - racing again", "#2bd46b"]};
    const Qs = d.quali && d.quali.phase;
    if (S[d.status] && !(Qs && d.status === "GREEN" && p.status === "CHEQUERED")) toast("flags", S[d.status][0], (d.meeting || "") + (Qs ? " · " + d.quali.label : lap), S[d.status][1]);
    if (d.status === "CHEQUERED") {
      if (Qs) toast("finish", `Chequered flag · end of ${d.quali.label}`, "Laps started before the flag still count", "#ffffff");
      else toast("finish", "Chequered flag", `${d.rows[0] ? d.rows[0].name || d.rows[0].code : ""} takes the win`, "#ffffff");
    }
  }
  const Q = d.quali && d.quali.phase ? d.quali : null, pQ = p.quali && p.quali.phase ? p.quali : null;
  if (Q && pQ) {
    if (Q.phase > pQ.phase) {
      toast("flags", `${Q.label} is under way`, "", "#2bd46b");
      const k = (Q.knocked || {})["q" + pQ.phase] || [];
      if (k.length) toast("out", `Knocked out in ${pQ.label}`, k.join(", "), "#ff5050");
    }
    if (Q.pole && Q.phase === pQ.phase && (!pQ.pole || Q.pole < pQ.pole - 0.0005))
      toast("fastest", `${Q.phase === 3 ? "Provisional pole" : "Top of " + Q.label}: ${Q.pole_driver} ${fmtLap(Q.pole)}`,
            pQ.pole ? `${(pQ.pole - Q.pole).toFixed(3)}s quicker than ${pQ.pole_driver}` : "", "#b138dd");
  }
  const lead = d.rows.find(r => r.pos === 1), plead = p.rows.find(r => r.pos === 1);
  if (!Q && lead && plead && lead.number !== plead.number && d.status !== "RED")
    toast("lead", `${lead.code} takes the lead`, `from ${plead.code}${lap}`, lead.colour);
  const fl = d.rows.find(r => r.fastest), pfl = p.rows.find(r => r.fastest);
  if (!Q && fl && fl.best_lap && (!pfl || pfl.best_lap == null || fl.best_lap < pfl.best_lap - 0.0005) && d.lap > 2)
    toast("fastest", `Fastest lap: ${fl.code} ${fmtLap(fl.best_lap)}`, fl.name, "#b138dd");
  d.rows.forEach(r => {
    const q = p.rows.find(x => x.number === r.number);
    if (r.out && q && !q.out) toast("out", `${r.code} is out`, r.out.reason || "", "#ff5050");
    if (!Q && q && r.pits > q.pits && r.pos && r.pos <= 6) toast("pits", `${r.code} pits from P${r.pos}`, `stop ${r.pits}${lap}`, r.colour);
  });
  const seen = new Set(((p.rc && p.rc.messages) || []).map(m => m.date + m.text));
  ((d.rc && d.rc.messages) || []).forEach(m => {
    if (seen.has(m.date + m.text)) return;
    if (m.kind === "penalty") toast("penalty", "Penalty", m.text.replace(/^FIA STEWARDS:\s*/, ""), "#ffb000");
    if (m.kind === "black_orange" || m.kind === "black_white") toast("penalty", m.kind === "black_white" ? "Black & white flag" : "Black & orange flag", m.text, "#ffffff");
    if (m.kind === "black") toast("penalty", "Black flag - disqualified", m.text.replace(/^FIA STEWARDS:\s*/, ""), "#ffffff");
  });
  const pinc = new Map((p.incidents || []).map(i => [i.id, i]));
  (d.incidents || []).forEach(i => {
    const was = pinc.get(i.id);
    if (!was && i.state === "stopped") toast("crash", `${i.kind === "crash" ? "Crash" : "Stopped on track"}: ${i.code}`, incWhere(i) + (i.speed_before && i.kind === "crash" ? ` · from ${i.speed_before} km/h` : ""), "#ff2a2a");
    if (was && was.state === "stopped" && i.state === "rejoined") toast("crash", `${i.code} spun and rejoined`, incWhere(i), "#ffd300");
  });
  if (d.weather && p.weather && d.weather.rain && !p.weather.rain) toast("weather", "Rain reported at the track", `track ${d.weather.track}°C`, "#0067ff");
  if (d.weather && p.weather && !d.weather.rain && p.weather.rain) toast("weather", "Rain has stopped", "", "#39b54a");
}

/* ---------- crash detection (f1hub/incidents.py): big banner, list, alerts ---------- */
const CRASH = {dismissed: new Set()};
const INC_WORD = {crash: "CRASH", stopped: "STOPPED ON TRACK", spin: "SPUN"};
function incWhere(i) {
  const bits = [i.corner ? `Turn ${i.corner}` : null, i.sector ? `marshal sector ${i.sector}` : null].filter(Boolean);
  return bits.join(" · ") || "on track";
}
function renderCrash(d) {
  const now = vnow().getTime(), list = d.incidents || [];
  // the banner: a crash or stopped car in the last 30 s of what you're watching (delay / replay aware)
  const live = list.filter(i => !CRASH.dismissed.has(i.id) && now - new Date(i.at) < 30000 && now >= new Date(i.at)
    && (i.state === "stopped" || (i.rejoined_at && now - new Date(i.rejoined_at) < 6000)));
  const el = $("crashbanner");
  if (!live.length) { el.classList.add("hidden"); el.innerHTML = ""; CRASH.shown = null; return; }
  const i = live[0];
  const kind = i.state === "rejoined" ? "spin" : i.kind;
  const key = i.id + kind + (i.confirmed_by || []).length + (i.speed_before || "");
  if (CRASH.shown === key) return;          // don't restart the animation every frame
  CRASH.shown = key;
  el.classList.remove("hidden");
  el.innerHTML = `<div class="crash ${kind}" role="alert"><div class="stripes"></div><div class="body">
      <div class="what">${ico("alert")}${INC_WORD[kind]}</div>
      <div class="who"><i style="background:${esc(i.colour)}"></i><div>${esc(i.code)}<small>${esc(i.name || "")}${i.team ? " · " + esc(i.team) : ""}</small></div></div>
      <div class="where">${esc(incWhere(i))}${i.speed_before && kind === "crash" ? ` · from ${i.speed_before} km/h` : ""}
        <small>${kind === "spin" ? "Spun and drove off again" : (i.confirmed_by || []).length ? esc(i.confirmed_by.join(" · ")) : "Detected from car position - race control hasn't said anything yet"}</small></div>
      <button class="x" onclick="CRASH.dismissed.add(${i.id});renderCrash(D)" title="Dismiss" aria-label="Dismiss">${ico("x")}</button>
    </div>${live.length > 1 ? `<div class="more">Also: ${live.slice(1).map(x => `${esc(x.code)} (${INC_WORD[x.kind].toLowerCase()})`).join(", ")}</div>` : ""}</div>`;
  if (i.number) el.querySelector(".who").onclick = () => setFocus(i.number);
}
function renderIncidents(d) {
  const el = $("incidents");
  if (!el) return;
  el.innerHTML = (d.incidents || []).map(i => {
    const kind = i.state === "rejoined" ? "spin" : i.kind;
    return `<div class="inc" onclick="setFocus(${i.number})"><span class="k ${kind}">${INC_WORD[kind]}</span>
      <span><b style="color:${esc(i.colour)}">${esc(i.code)}</b> · ${esc(incWhere(i))}${i.speed_before && kind !== "stopped" ? ` · ${i.speed_before} km/h` : ""}
        ${(i.confirmed_by || []).length ? `<br><span class="muted" style="font-size:12px">${esc(i.confirmed_by.join(" · "))}</span>` : ""}</span>
      <span class="muted num">${new Date(i.at).toLocaleTimeString()}${i.state === "rejoined" ? "<br>rejoined" : ""}</span></div>`;
  }).join("") || `<div class="msg muted">Nothing detected - every car is moving</div>`;
}
