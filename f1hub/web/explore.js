/* F1 Hub dashboard - Explore: session analysis, telemetry lab, season stats, circuit preview, FastF1.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- explore: session analysis, telemetry lab, season stats, circuit preview, FastF1 ---------- */
const XP = {view: store.get("xview", "analysis"), ff1: null, sessions: {}, key: store.get("xsession", null), analysis: null,
            lab: null, labPicks: store.get("labpicks", {}), traces: null, stats: {}, circuit: {}, statsShown: null,
            lapSel: new Set(), meetings: null};
const XYEARS = (() => { const y = new Date().getUTCFullYear(); return [...Array(y - 2017).keys()].map(i => y - i); })();
const SRC_BADGE = s => `<span class="src ${s === "fastf1" ? "fastf1" : "openf1"}">${s === "fastf1" ? "FastF1" : "OpenF1"}</span>`;
document.querySelectorAll("#xnav button").forEach(b => b.onclick = () => xShow(b.dataset.x));
function xOpen(view) { XP.view = view; openTab("explore"); }
// team colours that are too dark to read on the dark background (navy, black) get lightened, for text and lines
function vis(c) {
  const m = /^#?([0-9a-f]{6})$/i.exec(c || ""); if (!m) return c || "#888";
  const n = parseInt(m[1], 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  if (0.2126 * r + 0.7152 * g + 0.0722 * b > 95) return "#" + m[1];
  const mix = v => Math.round(v + (255 - v) * .45).toString(16).padStart(2, "0");
  return "#" + mix(r) + mix(g) + mix(b);
}
// spoiler mode: a recent session's analysis (and this season's stats) stay covered until you reveal them - the same
// "revealed" switch as the Home page's result cards, so revealing in one place reveals it in both
function xSpoilGate(key, what, retry) {
  if (!(D && D.settings ? D.settings.spoiler : true) || store.get("revealed." + key, false)) return "";
  return `<div class="card"><div class="empty" style="padding:40px 14px">${ico("eyeoff")}<b>${esc(what)}</b><br>Spoiler mode is on and you haven't revealed this result yet.
    <div class="row2" style="justify-content:center;margin-top:12px"><button class="btn primary" onclick="store.set('revealed.${esc(String(key))}', true); ${retry}">Show it</button>
    <button class="btn" onclick="replaySession(${+key || 0})">${ico("play")}Replay it first</button></div></div></div>`;
}
function xRecentSession(key) {      // started in the last 10 days?
  const all = Object.values(XP.sessions).flat(), x = all.find(s => String(s.key) === String(key));
  return x && Date.now() - new Date(x.start) < 10 * DAY && new Date(x.start) < Date.now();
}
async function xJSON(url) {
  const r = await fetch(url, {cache: "no-store"});
  let j; try { j = await r.json(); } catch (e) { throw new Error("HTTP " + r.status); }
  if (!r.ok || (j && j.error && !j.history)) throw new Error((j && j.error) || "HTTP " + r.status);
  return j;
}
function xErr(e) { return `<div class="empty">${ico("alert")}${esc(e.message || e)}</div>`; }
function xShow(view) {
  if (!["analysis", "lab", "stats", "report", "corners", "circuit", "ff1"].includes(view)) view = "analysis";
  XP.view = view; store.set("xview", view);
  document.querySelectorAll("#xnav button").forEach(b => b.classList.toggle("on", b.dataset.x === view));
  ["analysis", "lab", "stats", "report", "corners", "circuit", "ff1"].forEach(v => $("x-" + v).classList.toggle("hidden", v !== view));
  $("xsess").classList.toggle("hidden", !(view === "analysis" || view === "lab"));
  xFF1Status();
  if (view === "analysis" || view === "lab") xLoadSessions();
  if (view === "stats") xStats();
  if (view === "report") srLoad();
  if (view === "corners") cnLoad();
  if (view === "circuit") xCircuit();
  if (view === "ff1") xFF1();
}
async function xFF1Status(force) {
  if (XP.ff1 && !force && Date.now() - XP.ff1.at < 30000) return XP.ff1;
  try { XP.ff1 = Object.assign(await xJSON("/api/ff1/status"), {at: Date.now()}); } catch (e) { XP.ff1 = {available: false, error: e.message, at: Date.now()}; }
  const f = XP.ff1, rec = f.recorder || {};
  $("xff1chip").innerHTML = (f.available ? `${SRC_BADGE("fastf1")} ${esc(f.version || "")} ready` : `<span class="muted">FastF1 not available</span>`) +
    (rec.running ? ` · <span class="rec-dot"></span>recording` : "") + (f.loading ? ` · ${esc(f.loading.text)}…` : "");
  return f;
}
// a session in the analysis / lab pickers. Spoiler mode while replaying that same session: only up to the replay clock
function xBefore(key) {
  return D && D.mode === "replay" && D.settings && D.settings.spoiler && String(D.session_key) === String(key) ? vnow().toISOString() : null;
}
async function xLoadSessions() {
  const ys = $("xyear");
  if (!ys.options.length) ys.innerHTML = XYEARS.map(y => `<option>${y}</option>${y === 2023 ? '<option disabled>── FastF1 ──</option>' : ""}`).join("");
  if (XP.key && !ys.dataset.init) {      // open on the last session you looked at
    ys.dataset.init = 1;
    const y = String(+XP.key >= 200000000 ? Math.floor(+XP.key / 100000) : store.get("xyear", ys.value));
    if ([...ys.options].some(o => o.value === y)) ys.value = y;
  }
  const year = +ys.value; store.set("xyear", year);
  if (!XP.sessions[year]) {
    $("xsessnote").textContent = year < 2023 ? "Loading the season from FastF1…" : "Loading sessions…";
    try { XP.sessions[year] = await xJSON("/api/sessions?year=" + year); }
    catch (e) { $("xsessnote").textContent = e.message; $("xsession").innerHTML = ""; return; }
  }
  const list = XP.sessions[year].filter(x => x.type !== undefined);
  const groups = [];
  list.forEach(x => { const g = groups.find(g => g.k === x.meeting_key); g ? g.items.push(x) : groups.push({k: x.meeting_key, name: x.meeting, items: [x]}); });
  const want = String(XP.key || ""), has = list.some(x => String(x.key) === want);
  const isRec = +want >= 200000000 && +want % 10 > 0;       // a FastF1 recording: not in the season's schedule
  $("xsession").innerHTML = (isRec ? `<optgroup label="FastF1 recording"><option value="${want}" selected>Recording ${esc(want)}</option></optgroup>` : "") + groups.reverse().map(g => `<optgroup label="${esc(g.name || "")}">${g.items.slice().reverse().map(x =>
    `<option value="${x.key}" ${String(x.key) === want ? "selected" : ""}>${esc(x.name)} · ${esc(g.name || "")} · ${new Date(x.start).toLocaleDateString()}</option>`).join("")}</optgroup>`).join("");
  if (!has && !isRec) {      // default: the latest race
    const race = list.slice().reverse().find(x => x.type === "Race") || list[list.length - 1];
    if (race) $("xsession").value = String(race.key);
  }
  $("xsessnote").innerHTML = year < 2023 ? `${SRC_BADGE("fastf1")} F1's archive - the first look at a session downloads it (a minute or two)` : "";
  xSessionChanged();
}
function xSessionChanged() {
  const key = $("xsession").value; if (!key) return;
  XP.key = key; store.set("xsession", key);
  if (XP.view === "analysis") xAnalysis();
  if (XP.view === "lab") labLoad();
}

/* ---- session analysis ---- */
function boxPlot(rows, {W = 900} = {}) {
  if (!rows.length) return `<div class="empty">Not enough clean laps.</div>`;
  const L = 64, R = 70, T = 6, rowH = 22, H = rows.length * rowH + T + 24;
  const lo = Math.min(...rows.map(r => r.q1 - (r.q1 - r.min) * .6)), hi = Math.max(...rows.map(r => Math.min(r.max, r.q3 + (r.q3 - r.q1) * 2.5)));
  const X = v => L + (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1) * (W - L - R);
  let g = "";
  for (let i = 0; i <= 5; i++) { const v = lo + (hi - lo) * i / 5;
    g += `<line x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${H - 20}" stroke="#2a2e3a"/><text x="${X(v)}" y="${H - 6}" font-size="11" fill="#8b91a1" text-anchor="middle">${fmtLap(v)}</text>`; }
  rows.forEach((r, i) => {
    const y = T + i * rowH + rowH / 2, c = esc(vis(r.colour));
    g += `<text x="${L - 8}" y="${y + 4}" font-size="12" font-weight="700" fill="${c}" text-anchor="end">${esc(r.code)}</text>
      <line x1="${X(r.min)}" x2="${X(r.max)}" y1="${y}" y2="${y}" stroke="${c}" opacity=".6"/>
      <rect x="${X(r.q1)}" y="${y - 7}" width="${Math.max(2, X(r.q3) - X(r.q1))}" height="14" fill="${c}" opacity=".45" stroke="${c}" rx="2"><title>${esc(r.code)}: median ${fmtLap(r.med)} · ${r.n} laps · IQR ${fmtLap(r.q1)}-${fmtLap(r.q3)}</title></rect>
      <line x1="${X(r.med)}" x2="${X(r.med)}" y1="${y - 8}" y2="${y + 8}" stroke="#fff" stroke-width="2"/>
      <text x="${W - R + 6}" y="${y + 4}" font-size="11" fill="#8b91a1">${fmtLap(r.med)}${i ? ` +${(r.med - rows[0].med).toFixed(2)}` : ""}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">${g}</svg>`;
}
function stintBars(list, maxLap) {
  return list.map(r => `<div class="row2" style="padding:3px 14px;flex-wrap:nowrap"><span class="drv" style="width:64px"><span class="stripe" style="background:${esc(r.colour)}"></span>${esc(r.code)}</span>
    <div class="stintbar" style="flex:1">${r.stints.map(st => { const n = Math.max(1, (st.to || st.from) - st.from + 1);
      return `<i style="width:${n / maxLap * 100}%;background:${TYRE[st.compound] || "#666"}" title="${esc(st.compound || "?")}: laps ${st.from}-${st.to}${st.age ? ` (${st.age} laps old)` : ""}"></i>`; }).join("")}</div></div>`).join("");
}
async function xAnalysis() {
  const key = XP.key, before = xBefore(key);
  if (!before && xRecentSession(key)) {
    const gate = xSpoilGate(key, "This session finished in the last few days", "xAnalysis()");
    if (gate) { $("xanalysis").innerHTML = gate; return; }
  }
  $("xanalysis").innerHTML = `<div class="empty">Crunching the session…${+key >= 200000000 ? " (FastF1: the first time downloads it - a minute or two)" : ""}</div>`;
  let a;
  try { a = await xJSON(`/api/analysis?session=${key}` + (before ? `&before=${encodeURIComponent(before)}` : "")); }
  catch (e) { if (XP.key === key) $("xanalysis").innerHTML = xErr(e); return; }
  if (XP.key !== key) return;
  XP.analysis = a;
  if (!XP.lapSel.size || ![...XP.lapSel].every(n => a.drivers[n])) XP.lapSel = new Set(a.order.slice(0, 4).map(String));
  xRenderAnalysis();
}
function xRenderAnalysis() {
  const a = XP.analysis; if (!a) return;
  safe("analysis", () => {
    const s = a.session, race = s.type === "Race", D_ = a.drivers, mixed = !!(a.conditions && a.conditions.mixed);
    const maxLap = Math.max(1, ...a.strategy.flatMap(r => r.stints.map(st => st.to || 0)));
    const lapSeries = [...XP.lapSel].filter(n => a.laps[n]).map(n => ({colour: vis(D_[n].colour), points: a.laps[n].filter(p => !p[2]).map(p => [p[0], p[1]])}));
    const mates = a.teammates.map(m => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(m.colour)}"></span>${esc(m.team)}</div></td>
      <td><b>${esc(m.a)}</b> <span class="muted">vs</span> ${esc(m.b)}</td><td class="r num">${m.best_gap.toFixed(3)}s</td>
      <td class="r num">${m.pace_gap == null ? "-" : (m.pace_gap > 0 ? "+" : "") + m.pace_gap.toFixed(3) + "s"}</td></tr>`).join("");
    const best = a.best.map((b, i) => `<tr><td class="pos">${i + 1}</td><td><div class="drv"><span class="stripe" style="background:${esc(b.colour)}"></span>${esc(b.code)}</div></td>
      <td class="num ${i ? "" : "purple"}">${fmtLap(b.best)}</td><td class="r num muted">${i ? "+" + b.gap.toFixed(3) : ""}</td>
      <td><span class="tyre t-${esc(b.compound || "UNKNOWN")}">${esc((b.compound || "?")[0])}</span></td>
      <td class="num">${b.ideal ? fmtLap(b.ideal) : "-"}</td><td class="r num">${b.top_speed ?? "-"}</td><td class="r num muted">${b.laps}</td></tr>`).join("");
    const deg = a.deg.slice().sort((x, y) => (x.deg_fuel ?? x.deg ?? 9) - (y.deg_fuel ?? y.deg ?? 9));
    const degMax = Math.max(.05, ...deg.map(d => Math.abs(d.deg_fuel ?? d.deg ?? 0)));
    const degRows = deg.map(d => { const v = d.deg_fuel ?? d.deg;
      return `<tr><td><div class="drv"><span class="stripe" style="background:${esc(d.colour)}"></span>${esc(d.code)}</div></td>
      <td><span class="tyre t-${esc(d.compound || "UNKNOWN")}">${esc((d.compound || "?")[0])}</span> stint ${d.stint}</td><td class="r num">${d.laps}</td>
      <td class="num">${fmtLap(d.avg)}</td><td class="r num">${d.deg == null ? "-" : (d.deg > 0 ? "+" : "") + d.deg.toFixed(3)}</td>
      <td class="r num"><b>${v == null ? "-" : (v > 0 ? "+" : "") + v.toFixed(3)}</b></td>
      <td style="width:120px"><div class="gauge"><i style="width:${Math.min(100, Math.abs(v || 0) / degMax * 100)}%;background:${(v || 0) > 0.08 ? "#ff5050" : (v || 0) > 0.03 ? "#ffb000" : "#39b54a"}"></i></div></td></tr>`; }).join("");
    const chips = a.order.map(n => { const d = D_[n] || {}; const on = XP.lapSel.has(String(n));
      return `<button class="${on ? "on" : ""}" style="${on ? `background:${esc(d.colour)};border-color:${esc(d.colour)}` : ""}" onclick="xLapToggle('${n}')">${esc(d.code || n)}</button>`; }).join("");
    $("xanalysis").innerHTML = `
      <div class="row2" style="margin-bottom:14px;justify-content:space-between">
        <div><b style="font-size:20px">${esc(s.meeting)} - ${esc(s.name)} ${s.year}</b> ${SRC_BADGE(a.source)}
          ${a.cut ? `<span class="tag" style="background:#3a1f4a;color:#e0a8ff">spoiler mode: up to ${fmtTime(a.cut)}</span>` : ""}</div>
        <div class="row2"><button class="btn" onclick="replaySession(${+s.key})">${ico("play")}Replay it</button><button class="btn" onclick="xShow('lab')">${ico("activity")}Telemetry lab</button></div></div>
      <div class="two">
        <div class="stack">
          ${race ? `<div class="card"><h2>Race pace <span class="sub">clean laps only: no lap 1, pit laps, SC/VSC or slow outliers${mixed ? ` · ${a.conditions.pace_on === "wet" ? "wet-tyre" : "slick"} laps only (mixed conditions)` : ""} · box = middle half, line = median</span></h2><div class="pad">${boxPlot(a.pace)}</div></div>` : ""}
          <div class="card"><h2>Lap times <span class="sub">clean laps · pick drivers</span></h2><div class="pad"><div class="chiprow" style="margin-bottom:8px">${chips}</div>
            ${lineChart(teammateDash(lapSeries), {H: 260, yFmt: v => fmtLap(v), xFmt: v => "L" + Math.round(v)})}</div></div>
          ${race && a.lap_chart.length ? `<div class="card"><h2>Lap chart <span class="sub">position at the end of every lap</span></h2><div class="pad">
            ${lineChart(teammateDash(a.lap_chart.map(r => ({colour: vis(r.colour), points: r.points}))), {H: 340, yInvert: true, yMin: 1, yMax: 20, yFmt: v => "P" + Math.round(v), xFmt: v => "L" + Math.round(v)})}
            <div class="legend">${a.lap_chart.map(r => `<span><i style="background:${esc(vis(r.colour))}"></i>${esc(r.code)}</span>`).join("")}</div></div></div>` : ""}
          <div class="card"><h2>Strategy <span class="sub">tyre stints</span></h2><div style="padding:8px 0">${stintBars(a.strategy, maxLap)}</div></div>
        </div>
        <div class="stack">
          <div class="card"><h2>${race ? "Fastest laps" : "Best laps"} <span class="sub">ideal = best three sectors added up · speed trap km/h</span></h2><div class="scroll"><table>
            <thead><tr><th></th><th>Driver</th><th>Best</th><th class="r">Gap</th><th>Tyre</th><th>Ideal</th><th class="r">Trap</th><th class="r">Laps</th></tr></thead><tbody>${best}</tbody></table></div></div>
          <div class="card"><h2>Team-mates <span class="sub">best lap gap · ${race ? "median race-pace gap" : "median clean-lap gap"}</span></h2><div class="scroll"><table>
            <thead><tr><th>Team</th><th>Faster on one lap</th><th class="r">Best lap</th><th class="r">Pace</th></tr></thead><tbody>${mates}</tbody></table></div></div>
          ${deg.length ? `<div class="card"><h2>Tyre degradation <span class="sub">seconds lost per lap of tyre age${race ? " · fuel-corrected adds back ~0.06 s/lap of fuel burn (an estimate)" : ""}</span></h2>${mixed ? `
            <div class="pad muted">Mixed conditions: this session was run on both wet and slick tyres, so a drying (or wetting) track moves lap times more than tyre wear does. A negative figure here is the track getting faster, not the tyre.</div>` : ""}<div class="scroll"><table>
            <thead><tr><th>Driver</th><th>Stint</th><th class="r">Laps</th><th>Avg</th><th class="r">Raw</th><th class="r">${race ? "Fuel-corr." : "s/lap"}</th><th></th></tr></thead><tbody>${degRows}</tbody></table></div></div>` : ""}
        </div>
      </div>`;
  });
}
function xLapToggle(n) { XP.lapSel.has(n) ? XP.lapSel.delete(n) : XP.lapSel.add(n); xRenderAnalysis(); }

/* ---- telemetry lab ---- */
async function labLoad() {
  const key = XP.key;
  if (!xBefore(key) && xRecentSession(key)) {
    const gate = xSpoilGate(key, "This session finished in the last few days", "labLoad()");
    if (gate) { $("labout").innerHTML = gate; $("laba").innerHTML = $("labb").innerHTML = $("labal").innerHTML = $("labbl").innerHTML = ""; return; }
  }
  $("labnote").textContent = "Loading laps…"; $("labout").innerHTML = "";
  let l;
  try { l = await xJSON("/api/lab/laps?session=" + key); } catch (e) { $("labnote").textContent = e.message; return; }
  if (XP.key !== key) return;
  XP.lab = l;
  const f = await xFF1Status();
  $("labsrc").querySelector('[value="fastf1"]').disabled = !f.available;
  $("labsrc").querySelector('[value="openf1"]').disabled = +key >= 200000000;
  $("labsrcnote").innerHTML = `${SRC_BADGE(l.source)} ${esc(l.session.meeting)} - ${esc(l.session.name)} ${l.session.year}`;
  const opts = sel => `<option value="">-</option>` + l.drivers.map(d => `<option value="${d.number}" ${String(d.number) === String(sel) ? "selected" : ""}>${esc(d.code)} · ${esc(d.name || "")}${d.best ? " · " + fmtLap(d.best) : ""}</option>`).join("");
  const pick = XP.labPicks[key] || {};
  $("laba").innerHTML = opts(pick.a || (l.drivers[0] || {}).number);
  $("labb").innerHTML = opts(pick.b || (l.drivers[1] || {}).number);
  labLaps("a", pick.al); labLaps("b", pick.bl);
  $("labnote").textContent = "";
}
function labLaps(side, sel) {
  const l = XP.lab; if (!l) return;
  const d = l.drivers.find(x => String(x.number) === $("lab" + side).value);
  $("lab" + side + "l").innerHTML = `<option value="best">Best lap${d && d.best ? " · " + fmtLap(d.best) : ""}</option>` + (d ? d.laps.filter(x => x.t).map(x =>
    `<option value="${x.lap}" ${String(x.lap) === String(sel) ? "selected" : ""}>Lap ${x.lap} · ${fmtLap(x.t)}${x.compound ? " · " + x.compound[0] : ""}${x.pit ? " · out-lap" : ""}</option>`).join("") : "");
}
async function labCompare() {
  const key = XP.key, a = $("laba").value, b = $("labb").value, al = $("labal").value, bl = $("labbl").value, src = $("labsrc").value;
  if (!a) { $("labnote").textContent = "Pick a driver for A."; return; }
  XP.labPicks[key] = {a, b, al, bl}; store.set("labpicks", XP.labPicks);
  const before = xBefore(key);
  const url = (n, lap) => `/api/lab/trace?session=${key}&driver=${n}&lap=${lap}&src=${src}` + (before ? `&before=${encodeURIComponent(before)}` : "");
  $("labnote").textContent = src === "fastf1" || +key >= 200000000 ? "Loading telemetry from FastF1 (the first time can take a minute or two)…" : "Loading telemetry…";
  try {
    const [ta, tb] = await Promise.all([xJSON(url(a, al)), b ? xJSON(url(b, bl)) : Promise.resolve(null)]);
    if (!ta || !ta.samples) throw new Error(ta && ta.error || "No lap data.");
    XP.traces = {a: ta, b: tb && tb.samples ? tb : null};
    $("labnote").textContent = "";
    labRender();
  } catch (e) { $("labnote").innerHTML = ico("alert") + esc(e.message); }
}
function tAtFrac(tr, f) { const s = tr.samples; let j = s.findIndex(x => x[1] >= f);
  if (j < 0) return s[s.length - 1][0]; if (j === 0) return s[0][0];
  const p = s[j - 1], q = s[j]; return p[0] + (q[0] - p[0]) * ((f - p[1]) / (q[1] - p[1] || 1)); }
function labRender() {
  const {a, b} = XP.traces || {}; if (!a) return;
  safe("lab", () => {
    const drv = n => (XP.lab.drivers.find(d => d.number === n) || {code: "#" + n, colour: "#fff"});
    const da = drv(a.driver), db = b ? drv(b.driver) : null;
    const ca = vis(da.colour), cb = db ? (db.colour === da.colour ? "#ffffff" : vis(db.colour)) : null;
    const hasDist = a.fields.includes("dist") && (!b || b.fields.includes("dist"));
    const len = a.length_m || 0;
    const X = (tr, s) => hasDist ? s[8] : s[1] * 100;
    const xFmt = hasDist ? v => (v / 1000).toFixed(1) + "km" : v => v.toFixed(0) + "%";
    const ser = (tr, i, c, dash) => ({colour: c, dash, points: tr.samples.filter(s => s[i] != null).map(s => [X(tr, s), s[i]])});
    const both = (i) => [ser(a, i, ca)].concat(b ? [ser(b, i, cb, true)] : []);
    let delta = "", dom = "";
    if (b) {
      const raw = [...Array(201).keys()].map(k => tAtFrac(b, k / 200) - tAtFrac(a, k / 200));
      const drift = raw[200] - raw[0] - (b.duration - a.duration);
      const pts = raw.map((v, k) => [hasDist ? k / 200 * (len || 1) : k / 2, +(v - raw[0] - drift * k / 200).toFixed(3)]);
      delta = `<div class="card"><h2>Gap along the lap <span class="sub">above 0 = ${esc(db.code)} behind ${esc(da.code)}</span></h2><div class="pad">
        ${lineChart([{colour: "#8b91a1", points: [[pts[0][0], 0], [pts[200][0], 0]], width: 1}, {colour: cb, points: pts}], {H: 180, xFmt, yFmt: v => v.toFixed(2) + "s"})}</div></div>`;
      // who's quicker where: 25 mini-sectors, coloured by the faster car
      const N = 25, segs = [];
      for (let k = 0; k < N; k++) {
        const f0 = k / N, f1 = (k + 1) / N;
        const ta = tAtFrac(a, f1) - tAtFrac(a, f0), tb = tAtFrac(b, f1) - tAtFrac(b, f0);
        segs.push({f0, f1, col: Math.abs(ta - tb) < 0.005 ? "#8b91a1" : ta < tb ? ca : cb, d: tb - ta});
      }
      const pts2 = a.samples.filter(s => s[6] != null && (s[6] || s[7]));
      if (pts2.length > 20) {
        const xs = pts2.map(s => s[6]), ys = pts2.map(s => s[7]), minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
        const W = 520, pad = 18, k = (W - 2 * pad) / Math.max(maxX - minX, maxY - minY || 1), H = Math.round((maxY - minY) * k + 2 * pad);
        const P = s => `${(pad + (s[6] - minX) * k).toFixed(1)},${(H - pad - (s[7] - minY) * k).toFixed(1)}`;
        const paths = segs.map(sg => { const part = pts2.filter(s => s[1] >= sg.f0 - 0.004 && s[1] <= sg.f1 + 0.004);
          return part.length > 1 ? `<polyline points="${part.map(P).join(" ")}" fill="none" stroke="${sg.col}" stroke-width="7" stroke-linecap="round"><title>${sg.d > 0 ? esc(da.code) : esc(db.code)} quicker by ${Math.abs(sg.d).toFixed(3)}s</title></polyline>` : ""; }).join("");
        const wa = segs.filter(s => s.col === ca).length, wb = segs.filter(s => s.col === cb).length;
        dom = `<div class="card"><h2>Who's quicker where <span class="sub">${N} mini-sectors · ${esc(da.code)} ${wa} - ${wb} ${esc(db.code)}</span></h2><div class="pad">
          <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-height:60vh"><polyline points="${pts2.map(P).join(" ")}" fill="none" stroke="#2c303b" stroke-width="12" stroke-linejoin="round"/>${paths}</svg>
          <div class="legend"><span><i style="background:${esc(ca)}"></i>${esc(da.code)} faster</span><span><i style="background:${esc(cb)}"></i>${esc(db.code)} faster</span></div></div></div>`;
      }
    }
    const head = (d, t, c) => `<span class="drv" style="display:inline-flex"><span class="stripe" style="background:${esc(c)}"></span><b>${esc(d.code)}</b></span> lap ${t.lap} ·
      <b class="num">${fmtLap(t.duration)}</b> <span class="muted num">(${(t.sectors || []).map(x => x ? x.toFixed(3) : "-").join(" / ")})</span>
      ${t.compound ? `<span class="tyre t-${esc(t.compound)}" title="${esc(t.compound)}">${esc(t.compound[0])}</span>` : ""} ${SRC_BADGE(t.source)}`;
    const dd = b ? b.duration - a.duration : 0;
    const vmax = tr => Math.max(...tr.samples.map(s => s[2] || 0));
    const hasRpm = a.fields.includes("rpm");
    $("labout").innerHTML = `<div class="card"><div class="pad">${head(da, a, ca)}${b ? ` &nbsp; vs &nbsp; ${head(db, b, cb)} &nbsp;
        <b style="color:${dd > 0 ? esc(ca) : esc(cb)}">${esc(dd > 0 ? da.code : db.code)} faster by ${Math.abs(dd).toFixed(3)}s</b>` : ""}
        <div class="facts" style="margin-top:10px"><div><b>${vmax(a)}${b ? ` / ${vmax(b)}` : ""}</b><span>top speed km/h</span></div>
        ${len ? `<div><b>${(len / 1000).toFixed(3)} km</b><span>lap distance (${hasDist ? "measured" : "est."})</span></div>` : ""}
        <div><b>${Math.round(a.samples.filter(s => s[3] >= 98).length / a.samples.length * 100)}%${b ? ` / ${Math.round(b.samples.filter(s => s[3] >= 98).length / b.samples.length * 100)}%` : ""}</b><span>full throttle</span></div>
        <div><b>${a.samples.length}${b ? ` / ${b.samples.length}` : ""}</b><span>samples</span></div></div></div></div>
      <div class="two" style="margin-top:16px"><div class="stack">
        <div class="card"><h2>Speed <span class="sub">km/h</span></h2><div class="pad">${lineChart(both(2), {H: 240, xFmt})}</div></div>
        ${delta}
        <div class="card"><h2>Throttle %</h2><div class="pad">${lineChart(both(3), {H: 150, xFmt, yMin: 0, yMax: 100})}</div></div>
        <div class="card"><h2>Brake</h2><div class="pad">${lineChart([ser(a, 4, ca)].concat(b ? [{...ser(b, 4, cb), points: ser(b, 4, cb).points.map(p => [p[0], p[1] * .6])}] : []), {H: 90, xFmt, yMin: 0, yMax: 1, yFmt: v => v >= .99 ? "on" : v <= .01 ? "off" : ""})}</div></div>
        <div class="card"><h2>Gear</h2><div class="pad">${lineChart(both(5), {H: 140, xFmt, yMin: 1, yMax: 8, yFmt: v => Math.round(v)})}</div></div>
        ${hasRpm ? `<div class="card"><h2>RPM <span class="sub">FastF1</span></h2><div class="pad">${lineChart([ser(a, 9, ca)].concat(b && b.fields.includes("rpm") ? [ser(b, 9, cb, true)] : []), {H: 150, xFmt, yFmt: v => Math.round(v / 100) * 100})}</div></div>` : ""}
      </div><div class="stack">${dom || `<div class="card"><h2>Track</h2><div class="empty">Pick a second lap (B) to see who's quicker where.</div></div>`}
        <div class="card"><h2>About this data</h2><div class="pad xnote">${a.source === "fastf1"
          ? "FastF1 merges F1's car data (~4 Hz) with position data and interpolates both onto one timeline; distance is integrated from speed."
          : "OpenF1 car data (~3.7 Hz) matched to location samples; distance comes from the x/y positions. Switch the source to FastF1 for the merged, smoother trace."}</div></div></div></div>`;
  });
}

/* ---- season stats ---- */
async function xStats() {
  const ys = $("xsyear");
  if (!ys.options.length) { ys.innerHTML = XYEARS.map(y => `<option>${y}</option>`).join(""); ys.value = store.get("xsyear", XYEARS[0]); }
  const year = +ys.value; store.set("xsyear", year);
  $("xstatsnote").innerHTML = year < 2023 ? `${SRC_BADGE("fastf1")} results from the Jolpica-Ergast archive` : `${SRC_BADGE("openf1")}`;
  if (!XP.stats[year] || Date.now() - XP.stats[year].at > 600000) {
    $("xstats").innerHTML = `<div class="empty">Loading the ${year} season…</div>`;
    try { XP.stats[year] = Object.assign(await xJSON("/api/stats?year=" + year), {at: Date.now()}); }
    catch (e) { $("xstats").innerHTML = xErr(e); return; }
  }
  if (+$("xsyear").value !== year) return;
  const lr = H && H.last_race;
  if (year === new Date().getUTCFullYear() && lr && !lr.hidden && !XP.stats[year].spoiler_hidden) {
    const gate = xSpoilGate(lr.key, `This season's stats include the ${lr.meeting} result`, "xStats()");
    if (gate) { $("xstats").innerHTML = gate; return; }
  }
  safe("stats", () => {
    const st = XP.stats[year];
    if (!st.drivers.length) { $("xstats").innerHTML = `<div class="empty">No races yet in ${year}.</div>`; return; }
    const form = f => `<span class="formchips">${f.map(x => typeof x === "number"
      ? `<i class="${x === 1 ? "w" : x <= 3 ? "p" : x <= 10 ? "pts" : ""}">${x}</i>` : `<i class="out" title="${esc(x)}">${esc(String(x || "-")[0])}</i>`).join("")}</span>`;
    const f1 = v => v == null ? "-" : v.toFixed(1);
    const rows = st.drivers.map((d, i) => `<tr class="click" onclick="xStatsDriver('${esc(d.code)}')"><td class="pos">${i + 1}</td>
      <td><div class="drv"><span class="stripe" style="background:${esc(d.colour)}"></span>${esc(d.code)} <small>${esc(d.team || "")}</small></div></td>
      <td class="r num"><b>${d.points}</b></td><td class="r num">${d.wins || ""}</td><td class="r num">${d.podiums || ""}</td><td class="r num">${d.poles || ""}</td>
      <td class="r num">${f1(d.avg_quali)}</td><td class="r num">${f1(d.avg_finish)}</td>
      <td class="r num ${d.avg_gained > 0 ? "up" : d.avg_gained < 0 ? "down" : ""}">${d.avg_gained == null ? "-" : (d.avg_gained > 0 ? "+" : "") + d.avg_gained.toFixed(1)}</td>
      <td class="r num">${d.q3}</td><td class="r num">${d.points_finishes}</td><td class="r num">${d.dnfs || ""}</td><td class="r num">${d.ppr}</td><td>${form(d.form)}</td></tr>`).join("");
    const top = st.drivers.slice(0, 8);
    const h2h = st.h2h.map(h => { const bar = (x, y) => `<div class="h2hbar"><i style="width:${x / ((x + y) || 1) * 100}%;background:${esc(h.colour)}"></i><i style="width:${y / ((x + y) || 1) * 100}%;background:#ffffff40"></i></div>`;
      return `<tr><td><div class="drv"><span class="stripe" style="background:${esc(h.colour)}"></span>${esc(h.team)}</div></td>
        <td><b>${esc(h.a)}</b> <span class="muted">v</span> ${esc(h.b)}</td>
        <td class="num">${h.quali[0]}-${h.quali[1]}${bar(h.quali[0], h.quali[1])}</td><td class="num">${h.race[0]}-${h.race[1]}${bar(h.race[0], h.race[1])}</td>
        <td class="r num">${h.points[0]} - ${h.points[1]}</td></tr>`; }).join("");
    $("xstats").innerHTML = `${st.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: the meeting you're watching delayed or replaying (and anything after) is left out.</div>` : ""}
      <div class="card"><h2>${year} drivers <span class="sub">${st.rounds.length} rounds · avg Q = qualifying position · +/- = places gained from the grid · Q3 = top-10 qualifying · PPR = points per round</span></h2><div class="scroll"><table>
        <thead><tr><th></th><th>Driver</th><th class="r">Pts</th><th class="r">Wins</th><th class="r">Pod</th><th class="r">Poles</th><th class="r">Avg Q</th><th class="r">Avg fin</th>
        <th class="r">+/-</th><th class="r">Q3</th><th class="r">Pts fin</th><th class="r">DNF</th><th class="r">PPR</th><th>Last 5</th></tr></thead><tbody>${rows}</tbody></table></div></div>
      <div class="two" style="margin-top:16px">
        <div class="card"><h2>Points progression <span class="sub">top 8 · from finishing positions (no fastest-lap bonus points)</span></h2><div class="pad">
          ${lineChart(teammateDash(top.map(d => ({colour: vis(d.colour), points: d.by_round}))), {H: 300, xFmt: v => "R" + Math.round(v)})}
          <div class="legend">${top.map(d => `<span><i style="background:${esc(d.colour)}"></i>${esc(d.code)}</span>`).join("")}</div></div></div>
        <div class="card"><h2>Constructors</h2><div class="pad">
          ${lineChart(st.teams.map(t => ({colour: vis(t.colour), points: t.by_round})), {H: 300, xFmt: v => "R" + Math.round(v)})}
          <div class="legend">${st.teams.map(t => `<span><i style="background:${esc(t.colour)}"></i>${esc(t.team)} ${t.points}</span>`).join("")}</div></div></div>
      </div>
      <div class="card" style="margin-top:16px"><h2>Team-mate head-to-heads <span class="sub">qualifying · race (when either finished) · points</span></h2><div class="scroll"><table>
        <thead><tr><th>Team</th><th>Drivers</th><th>Qualifying</th><th>Race</th><th class="r">Points</th></tr></thead><tbody>${h2h}</tbody></table></div></div>
      <div id="xstatsdrv"></div>`;
  });
}
function xStatsDriver(code) {
  const st = XP.stats[+$("xsyear").value]; const d = st && st.drivers.find(x => x.code === code); if (!d) return;
  const per = d.by_round.map((p, i) => [p[0], p[1] - (i ? d.by_round[i - 1][1] : 0)]);
  $("xstatsdrv").innerHTML = `<div class="card" style="margin-top:16px"><h2>${esc(d.name || d.code)} <span class="sub">${esc(d.team || "")}</span></h2><div class="pad">
    <div class="facts"><div><b>${d.points}</b><span>points</span></div><div><b>${d.wins}</b><span>wins</span></div><div><b>${d.podiums}</b><span>podiums</span></div>
    <div><b>${d.poles}</b><span>poles</span></div><div><b>${d.best ?? "-"}</b><span>best finish</span></div><div><b>${d.gained > 0 ? "+" : ""}${d.gained}</b><span>places gained in total</span></div>
    <div><b>${d.starts}</b><span>starts</span></div><div><b>${d.dnfs}</b><span>DNFs</span></div></div>
    <h2 style="margin-top:12px">Points per round</h2>${lineChart([{colour: d.colour, points: per}], {H: 180, xFmt: v => "R" + Math.round(v)})}</div></div>`;
  $("xstatsdrv").scrollIntoView({behavior: "smooth", block: "nearest"});
}

/* ---- circuit preview ---- */
function circuitSVG(m, W = 560, intro = true) {      // the same drawing as the live map, without cars
  if (!m || !m.outline) return `<div class="empty">No circuit map.</div>`;
  const box = document.createElement("div");
  paintTrack(box, {map: {outline: m.outline, corners: m.corners, rotation: m.rotation}, rc: {}, incidents: []}, W, {corners: true, accent: true, intro});
  return `<div class="cprev">${box.innerHTML}</div>`;
}
const seqHTML = seq => `<span class="seq">${(seq || []).map(c => `<i style="border-color:${TYRE[c] || TYRE[{S: "SOFT", M: "MEDIUM", H: "HARD", I: "INTERMEDIATE", W: "WET"}[c]] || "#666"}" title="${esc(c)}"></i>`).join("")}</span>`;
async function xCircuitData(mk) {
  const k = mk || "next";
  if (!XP.circuit[k] || Date.now() - XP.circuit[k].at > 1800000) XP.circuit[k] = Object.assign(await xJSON("/api/circuit" + (mk ? "?meeting=" + mk : "")), {at: Date.now()});
  return XP.circuit[k];
}
async function xCircuit() {
  const sel = $("xcmeet");
  if (!XP.meetings) {
    try {
      const y = new Date().getUTCFullYear(), list = await xJSON("/api/sessions?year=" + y);
      const seen = new Map(); list.forEach(x => { if (x.meeting_key && !seen.has(x.meeting_key)) seen.set(x.meeting_key, x.meeting); });
      XP.meetings = [...seen.entries()];
    } catch (e) { XP.meetings = []; }
    sel.innerHTML = `<option value="">Next round</option>` + XP.meetings.map(([k, n]) => `<option value="${k}">${esc(n)}</option>`).join("");
  }
  const mk = sel.value;
  $("xcircuit").innerHTML = `<div class="empty">Loading the circuit and its past races…</div>`;
  let c;
  try { c = await xCircuitData(mk); } catch (e) { $("xcircuit").innerHTML = xErr(e); return; }
  if ($("xcmeet").value !== mk) return;
  safe("circuit", () => {
    const m = c.meeting, loss = (c.map || {}).pit_loss || {};
    const totStops = Object.values(c.stops || {}).reduce((a, b) => a + b, 0);
    const eds = (c.editions || []).filter(e => !e.error);
    const ed = e => `<tr><td><b>${e.year}</b></td>
      <td>${(e.podium || []).map((p, i) => `<span class="drv" style="display:inline-flex;margin-right:8px"><span class="stripe" style="background:${esc(p.colour)}"></span>${i + 1}. ${esc(p.code)}</span>`).join("")}</td>
      <td class="r num" ${e.winner_grid_from === "quali" ? 'title="Qualifying position - OpenF1 has no starting grid for this race, so grid penalties are not included"' : ""}>${e.winner_grid == null ? "-" : (e.winner_grid_from === "quali" ? "Q" : "P") + e.winner_grid}</td><td>${e.pole ? `${esc(e.pole.code)} <span class="muted num">${e.pole.time ? fmtLap(e.pole.time) : ""}</span>` : "-"}</td>
      <td>${e.fastest ? `${esc(e.fastest.code)} <span class="muted num">${fmtLap(e.fastest.time)}</span>` : "-"}</td>
      <td>${seqHTML(e.winner_strategy)}</td><td class="r num">${e.sc ?? "-"} / ${e.vsc ?? "-"}${e.red ? ` / <span class="down">${e.red} red</span>` : ""}</td>
      <td class="r num">${e.overtakes ?? "-"}</td><td>${e.weather ? ico(e.weather.rain ? "rain" : "sun") + `<span class="muted">${e.weather.track ?? "-"}°</span>` : ""}</td></tr>`;
    const seqs = {}; eds.forEach(e => (e.sequences || []).forEach(([s, n]) => seqs[s] = (seqs[s] || 0) + n));
    const topSeq = Object.entries(seqs).sort((a, b) => b[1] - a[1]).slice(0, 5);
    $("xcircuit").innerHTML = `<div class="hero" style="margin-bottom:16px"><div class="kick">${m.year} · ${esc(m.country || "")}</div>
        <h1>${esc(m.name)}<small>${esc(m.circuit || "")}${m.location && m.location !== m.circuit ? " · " + esc(m.location) : ""}${m.circuit_type ? " · " + esc(m.circuit_type) : ""}</small></h1>
        <div class="when">${m.start ? fmtWhen(m.start) + " <span>weekend starts</span>" : ""}</div></div>
      <div class="grid"><div class="stack">
        <div class="card"><h2>The track <span class="sub">corner numbers · chequered line = start/finish, arrow = direction</span></h2><div class="pad">${circuitSVG(c.map)}</div></div>
        <div class="card"><h2>Past races here <span class="sub">winner's tyres · SC / VSC · overtakes · track temp</span></h2><div class="scroll"><table>
          <thead><tr><th>Year</th><th>Podium</th><th class="r">Won from</th><th>Pole</th><th>Fastest lap</th><th>Winner's tyres</th><th class="r">SC/VSC</th><th class="r">Passes</th><th></th></tr></thead>
          <tbody>${eds.map(ed).join("") || `<tr><td colspan="9" class="muted">No races here in OpenF1's data yet (2023 on).</td></tr>`}</tbody></table></div>
          <div id="xchist"></div></div>
      </div><div class="stack">
        <div class="card"><h2>Key numbers</h2><div class="pad"><div class="facts">
          ${loss.normal ? `<div><b>${loss.normal.toFixed(1)}s</b><span>pit stop loss (green)</span></div>` : ""}
          ${loss.sc ? `<div><b>${loss.sc.toFixed(1)}s</b><span>pit loss under SC</span></div>` : ""}
          ${loss.vsc ? `<div><b>${loss.vsc.toFixed(1)}s</b><span>pit loss under VSC</span></div>` : ""}
          ${eds[0] && eds[0].laps ? `<div><b>${eds[0].laps}</b><span>race laps</span></div>` : ""}
          ${eds.length ? `<div><b>${(eds.reduce((a, e) => a + (e.sc || 0), 0) / eds.length).toFixed(1)}</b><span>safety cars per race</span></div>` : ""}
          ${eds.filter(e => e.winner_grid).length ? `<div><b>${eds.filter(e => e.winner_grid === 1).length}/${eds.filter(e => e.winner_grid).length}</b><span>won from the front${eds.some(e => e.winner_grid_from === "quali") ? " (pole / top qualifier)" : ""}</span></div>` : ""}
          ${(c.map && c.map.corners || []).length ? `<div><b>${c.map.corners.length}</b><span>corners</span></div>` : ""}
        </div></div></div>
        ${totStops ? `<div class="card"><h2>How many stops <span class="sub">finishers, past races here</span></h2><div class="pad">
          ${Object.entries(c.stops).map(([k, v]) => `<div class="row2" style="margin-bottom:6px;flex-wrap:nowrap"><span style="width:70px">${k}-stop</span>
            <div class="gauge" style="flex:1"><i style="width:${v / totStops * 100}%;background:var(--red)"></i></div><span class="num muted" style="width:40px;text-align:right">${Math.round(v / totStops * 100)}%</span></div>`).join("")}
          ${topSeq.length ? `<h2 style="margin:10px -14px 8px">Most used tyre orders</h2>${topSeq.map(([s, n]) => `<div class="row2" style="margin-bottom:4px">${seqHTML(s.split("-"))} <span class="muted">${esc(s)} · ${n} cars</span></div>`).join("")}` : ""}
        </div></div>` : ""}
        <div class="card"><h2>Weekend schedule <span class="sub">your local time</span></h2>${(c.schedule || []).map(sx => `<div class="sched ${sx.name === "Race" ? "race" : ""}">
          <b>${esc(sx.name)}</b><span class="st"></span><span class="t">${fmtDay(sx.start)} · ${fmtTime(sx.start)}</span></div>`).join("")}</div>
      </div></div>`;
    if (c.fastf1) xCircuitHistory(m, mk);
  });
}
async function xCircuitHistory(m, mk) {
  const el = $("xchist"); if (!el) return;
  el.innerHTML = `<div class="pad xnote">${SRC_BADGE("fastf1")} Loading 2018-2022 winners here…</div>`;
  try {
    const h = await xJSON(`/api/circuit/history?name=${encodeURIComponent(m.name || "")}&location=${encodeURIComponent(m.location || "")}&year=${m.year}`);
    if ($("xcmeet").value !== mk || !$("xchist")) return;
    const rows = (h.history || []).filter(e => !e.error);
    $("xchist").innerHTML = rows.length ? `<div class="pad xnote" style="padding-bottom:0">${SRC_BADGE("fastf1")} Earlier years</div><table><tbody>${rows.map(e => `<tr><td><b>${e.year}</b></td>
      <td>${(e.podium || []).map((p, i) => `<span class="drv" style="display:inline-flex;margin-right:8px"><span class="stripe" style="background:${esc(p.colour)}"></span>${i + 1}. ${esc(p.code)}</span>`).join("")}</td>
      <td class="r num muted">from P${e.winner_grid ?? "?"}</td><td>${e.pole ? "pole " + esc(e.pole.code) : ""}</td>
      <td><button class="btn" style="padding:2px 8px;font-size:11px" onclick="replaySession(${+e.key})">${ico("play")}Replay</button></td></tr>`).join("")}</tbody></table>` : "";
  } catch (e) { if ($("xchist")) $("xchist").innerHTML = ""; }
}
async function homeCircuit() {      // the Home page's compact preview of the next circuit
  try {
    const c = await xCircuitData("");
    safe("homecircuit", () => {
      const e = (c.editions || []).find(x => !x.error), loss = (c.map || {}).pit_loss || {};
      $("homecirch").textContent = c.meeting.circuit || "Circuit preview";
      $("homecircuit").innerHTML = `<div class="pad">${circuitSVG(c.map, 380, !$("homecircuit").querySelector("svg.trk"))}
        <div class="facts" style="margin-top:8px">${e ? `<div><b>${esc((e.podium[0] || {}).code || "-")}</b><span>won here in ${e.year}${e.winner_grid ? ` from ${e.winner_grid_from === "quali" ? "Q" : "P"}${e.winner_grid}` : ""}</span></div>` : ""}
        ${e && e.pole ? `<div><b>${e.pole.time ? fmtLap(e.pole.time) : esc(e.pole.code)}</b><span>pole ${e.year} (${esc(e.pole.code)})</span></div>` : ""}
        ${loss.normal ? `<div><b>${loss.normal.toFixed(1)}s</b><span>pit stop loss</span></div>` : ""}
        ${e && e.winner_strategy ? `<div><b>${seqHTML(e.winner_strategy)}</b><span>winner's tyres ${e.year}</span></div>` : ""}</div></div>`;
    });
  } catch (err) { $("homecircuit").innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
}

/* ---- FastF1 status, recorder and recordings ---- */
async function xFF1() {
  const f = await xFF1Status(true);
  let recs = {recordings: [], recorder: f.recorder || {}}, live = {recordings: []};
  try { recs = await xJSON("/api/ff1/recordings"); } catch (e) {}
  try { live = await xJSON("/api/recordings"); } catch (e) {}
  safe("ff1", () => {
    const r = recs.recorder || {}, kb = n => n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1000) + " kB";
    $("xff1main").innerHTML = `
      <div class="card"><h2>Record live timing <span class="sub">FastF1 · F1's own feed</span></h2><div class="pad">
        ${r.running ? `<div style="font-size:16px;margin-bottom:8px"><span class="rec-dot"></span><b>Recording</b> ${esc([r.location, r.session].filter(Boolean).join(" - "))}
            <span class="muted num">· ${kb(r.bytes || 0)} · since ${fmtTime(new Date(r.started * 1000).toISOString())}${r.last_write ? ` · last data ${Math.round(Date.now() / 1000 - r.last_write)}s ago` : ""}</span></div>
            <button class="btn" onclick="xRecord('stop')">■ Stop recording</button>`
          : `<button class="btn primary" onclick="xRecord('start')" ${f.available ? "" : "disabled"}>${ico("rec")}Start recording now</button>
             ${r.file ? `<span class="xnote"> Last: ${esc(r.file)} (${kb(r.bytes || 0)})${r.exit != null ? ` · stopped (exit ${r.exit})` : ""}</span>` : ""}`}
        <p class="xnote">Saves everything F1's live-timing feed sends during a session (timing, positions, car telemetry, race control, weather) to
          <code>recordings/fastf1/</code>. FastF1 can't process it in real time; play it back afterwards, or mid-session to see what has been recorded so far.
          It stops by itself 30 minutes after the feed goes quiet.</p>
        ${xTVLogin(f, r)}
        ${r.auth_failed ? `<p class="xnote down">The sign-in failed${r.auth_error ? `: ${esc(r.auth_error)}` : ""} - this recording has F1's free feed.</p>` : ""}
        ${(r.log || []).length ? `<pre class="log">${esc(r.log.join("\n"))}</pre>` : ""}
      </div></div>
      <div class="card"><h2>Recordings <span class="sub">newest first</span></h2>${(recs.recordings || []).length ? `<table><tbody>${recs.recordings.map(x => `<tr>
          <td>${x.recording ? '<span class="rec-dot"></span>' : ""}<b>${esc([x.location, x.session].filter(Boolean).join(" - ") || x.name)}</b><br><span class="xnote">${esc(x.name)} · ${kb(x.bytes)}${x.auth && x.auth !== "none" ? " · signed in" : " · free feed"}</span></td>
          <td class="r">${x.key ? `<button class="btn" onclick="replaySession(${+x.key})">${ico("play")}Play back</button> <button class="btn" title="Analyse" aria-label="Analyse" onclick="xAnalyseKey(${+x.key})">${ico("chart")}</button>`
            : `<span class="xnote">couldn't match it to a session</span>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing recorded yet.</div>`}</div>
      ${xLiveRecordings(live, kb)}`;
    $("xff1side").innerHTML = `<div class="card"><h2>FastF1 ${f.available ? SRC_BADGE("fastf1") : ""}</h2><div class="pad">
        ${f.available ? `<p>${ico("check")}FastF1 ${esc(f.version || "")} is ready.${f.busy ? ` Working: ${esc(f.busy)}…` : ""}</p>` : `<p class="down">${ico("alert")}${esc(f.error || "Not available")}</p>
          <pre class="log">python3 -m venv .venv
.venv/bin/pip install fastf1</pre><p class="xnote">Then restart F1 Hub.</p>`}
        <p class="xnote">What FastF1 adds:</p>
        <ul class="xnote" style="padding-left:18px;margin:0">
          <li><b>2018-2022</b> in replays, analysis and the telemetry lab (F1's archive - OpenF1 starts in 2023)</li>
          <li>${f.login ? "Signed in to F1 TV: FastF1 can also read 2023 onwards" : "<b>2023 onwards needs an F1 TV login</b>: F1's archive only serves recent seasons when signed in - use <i>Sign in to F1 TV</i> on the recorder card"}</li>
          <li><b>Smoother lap telemetry</b> in the lab: car and position data merged on one timeline, distance in metres, RPM and DRS</li>
          <li><b>A stand-in while OpenF1 is locked</b> during a live session (free accounts): replays and analysis of past sessions keep working</li>
          <li><b>Recording</b> the live feed with an F1 TV Pro login, to play back with full telemetry</li>
          <li>Season stats before 2023, from the Jolpica-Ergast archive</li></ul>
        <p class="xnote">FastF1 downloads into <code>.f1cache/fastf1/</code>: a race with telemetry is ~100 MB the first time, then loads in seconds.</p></div></div>`;
  });
}
/* OpenF1 data recorded live (f1hub/recordings.py): play a session back as F1 Hub saw it, and how late each feed was */
function xLiveRecordings(live, kb) {
  const list = live.recordings || [];
  XP.lat = XP.lat || {};
  return `<div class="card"><h2>Live OpenF1 recordings <span class="sub">${live.recording ? "kept for every session F1 Hub follows live"
      : "recording is off (F1HUB_RECORD=0) - these can still be played back"}</span></h2>
    ${list.length ? `<table><tbody>${list.map(x => `<tr>
      <td><b>${esc([x.year, x.location, x.name].filter(Boolean).join(" "))}</b>${x.replaying ? ' <span class="xnote">· playing</span>' : ""}<br>
        <span class="xnote">${x.start ? esc(fmtWhen(x.start)) + " · " : ""}${kb(x.bytes)} · ${x.endpoints.length} feeds${x.layout === "day" ? " · older format" : ""}</span>
        <div id="lat-${+x.key}">${XP.lat[x.key] || ""}</div></td>
      <td class="r"><button class="btn" onclick="replaySession(${+x.key})">${ico("play")}Play back</button>
        <button class="btn" title="How late the live data was" onclick="xLatency(${+x.key})">Delays</button></td></tr>`).join("")}</tbody></table>`
      : `<div class="empty">Nothing yet. While F1 Hub follows a live session, the OpenF1 data it gets is saved here, to play back later.</div>`}
    <p class="xnote" style="padding:0 14px 12px">Saved in <code>${esc(live.folder || "recordings/openf1")}</code>. A playback shows the session
      as the data arrived live; <code>python3 -m f1hub cleanup</code> lists old ones you can remove.</p></div>`;
}
async function xLatency(key) {
  const show = html => { XP.lat[key] = html; const el = $("lat-" + key); if (el) el.innerHTML = html; };
  show(`<span class="xnote">Measuring…</span>`);
  let d;
  try { d = await xJSON("/api/recordings/latency?session=" + key); } catch (e) { show(`<span class="xnote down">${esc(e.message)}</span>`); return; }
  const rows = Object.entries(d.endpoints || {}).filter(([, v]) => v.stream || v.rest);
  if (!rows.length) { show(`<span class="xnote">Not enough timed data in this recording to measure.</span>`); return; }
  const cell = v => v ? `${v.median.toFixed(1)} s <span class="muted">(90% within ${v.p90.toFixed(1)} s, ${v.n})</span>` : `<span class="muted">-</span>`;
  show(`<table class="lat"><thead><tr><th>Feed</th><th>Stream</th><th>Polling</th></tr></thead><tbody>${rows.map(([ep, v]) =>
      `<tr><td>${esc(ep)}</td><td>${cell(v.stream)}</td><td>${cell(v.rest)}</td></tr>`).join("")}</tbody></table>
    <span class="xnote">From when something happened to when it reached F1 Hub: the median, and how long 90% took. Polling includes the wait for the next request; what came in a first answer (catching up) isn't counted.</span>`);
}
/* F1 TV sign-in for FastF1: the browser method runs on the server (FastF1's CLI) and hands us the link to open */
function xTVLogin(f, r) {
  const a = f.auth || {}, s = f.sign_in || {}, pw = f.password || {};
  const until = a.expires_at ? new Date(a.expires_at * 1000).toLocaleDateString() : "";
  let body;
  if (s.state === "waiting") {
    const left = s.until ? Math.max(0, Math.ceil((s.until * 1000 - Date.now()) / 60000)) : null;
    body = `waiting for you to sign in${left != null ? ` (it gives up in ${left} min)` : ""}.
      ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">Open the F1 sign-in page</a>` : "Starting…"}
      <button class="btn" onclick="xTVAction('cancel')">Cancel</button>
      <br>Sign in with your F1 TV account on that page. It needs FastF1's browser add-on, which the page links to; once it's
      installed, sign in again there. This card updates by itself when it's done.`;
  } else if (a.signed_in) {
    body = `signed in${a.product ? ` (${esc(a.product)})` : ""}${until ? `, valid until ${esc(until)}` : ""}.
      FastF1 can read 2023 onwards, and recordings get the full feed.
      <button class="btn" onclick="xTVAction('signout')">Sign out</button>`;
  } else if (pw.rejected) {
    const when = pw.rejected.at ? new Date(pw.rejected.at * 1000).toLocaleString() : "";
    body = `<span class="down">F1 turned down <code>F1_USERNAME</code> / <code>F1_PASSWORD</code>${when ? ` (${esc(when)})` : ""}: ${esc(pw.rejected.error || "")}</span>
      <br>FastF1 won't send them again until they change, so the account isn't locked by repeated tries. Check the password by
      signing in on formula1.com, then <button class="btn" onclick="xTVAction('retry')">Try the password again</button>
      - or sign in with the browser instead: <button class="btn primary" onclick="xTVAction('start')" ${f.available ? "" : "disabled"}>Sign in to F1 TV</button>`;
  } else if (pw.set && pw.method !== "browser") {
    body = `<code>F1_USERNAME</code> / <code>F1_PASSWORD</code> are set; FastF1 signs in with them the first time it needs to. If F1 turns
      them down, this card says so.`;
  } else {
    body = `${a.expired ? "your sign-in has expired" : "not signed in"}. Without it, recordings use F1's free feed (no car telemetry or
      positions in recent seasons) and FastF1 can't read 2023 onwards.
      <button class="btn primary" onclick="xTVAction('start')" ${f.available ? "" : "disabled"}>Sign in to F1 TV</button>
      ${s.state === "failed" ? `<br><span class="down">${esc(s.error || "Sign-in failed")}</span>` : ""}
      <br>Or put <code>F1_USERNAME=…</code> and <code>F1_PASSWORD=…</code> in <code>.openf1.env</code> and restart F1 Hub.`;
  }
  clearTimeout(XP.tvPoll);
  if (s.state === "waiting") XP.tvPoll = setTimeout(() => { if (activeTab === "explore" && XP.view === "ff1") xFF1(); }, 2000);
  return `<p class="xnote"><b>F1 TV Pro login:</b> ${body}</p>`;
}
async function xTVAction(action) {
  const w = action === "start" ? window.open("", "_blank") : null;   // opened now, so popup blockers allow it
  try {
    const res = await xJSON("/api/ff1/login?action=" + action);
    const url = res.url || (res.sign_in || {}).url;
    if (w) { if (url) w.location = url; else w.close(); }
    if (action === "start" && res.state === "failed") toast("error", "F1 TV sign-in", res.error || "Sign-in failed", "#ff5050");
  } catch (e) {
    if (w) w.close();
    toast("error", "F1 TV sign-in", e.message, "#ff5050");
  }
  xFF1();
}
function xAnalyseKey(key) {
  XP.key = String(key); store.set("xsession", XP.key);
  const y = String(Math.floor(key / 100000)), ys = $("xyear");
  if (!ys.options.length) xLoadSessions();
  else if ([...ys.options].some(o => o.value === y)) ys.value = y;
  xShow("analysis");
}
async function xRecord(action) {
  try { await xJSON("/api/ff1/record?action=" + action); } catch (e) { toast("error", "Recorder", e.message, "#ff5050"); }
  setTimeout(xFF1, 1500);
}
setInterval(() => { if (activeTab === "explore" && XP.view === "ff1" && !document.hidden) xFF1(); }, 10000);
