/* F1 Hub dashboard - Explore > Stewards, radio & reliability (f1hub/controlreport.py): stewarding consistency from
   the FIA decisions, team radio classified and lined up against the timing data, and reliability since 1950.
   A classic script, loaded after stratview.js (it uses SR_YEARS, srBar, xJSON, xErr, vis, esc, ico, safe, store). */
const SW = {data: null, year: null, cat: "", timer: null};
const SW_SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"];      // validated, dark surface
// outcome tiers, least to most severe: one hue, light to dark (a sequential ramp)
const SW_TIERS = [["No further action", ["no_action"], "#2b3445"], ["Warning or reprimand", ["warning", "reprimand"], "#35517a"],
  ["Fine or lap deleted", ["fine", "lap_deleted", "other"], "#3f6fb0"], ["Time penalty", ["time"], "#5a8fe0"],
  ["Drive-through or stop-go", ["drive_through", "stop_go"], "#8fb4f0"], ["Grid drop, pit start or DSQ", ["grid", "pit_start", "dsq"], "#cfe0fb"]];
const swPct = (v, n = 0) => v == null ? "-" : `${(100 * v).toFixed(n)}%`;

async function swLoad() {
  const ys = $("swYear");
  if (!ys.options.length) {
    let pick = store.get("sgyear", null) || store.get("sryear", null);
    if (!pick) { try { pick = ((await xJSON("/api/dataset")).compiled_years || [])[0]; } catch (e) { /* this year */ } }
    ys.innerHTML = SR_YEARS.map(y => `<option>${y}</option>`).join(""); ys.value = SR_YEARS.includes(+pick) ? pick : SR_YEARS[0];
  }
  const year = +ys.value;
  if (year !== SW.year) Object.assign(SW, {year, data: null, cat: ""});
  if (!SW.data) $("swOut").innerHTML = `<div class="empty">Loading…</div>`;
  try { SW.data = await xJSON(`/api/officials?year=${year}`); } catch (e) { $("swOut").innerHTML = xErr(e); return; }
  if (+$("swYear").value !== year) return;
  swRender();
  clearTimeout(SW.timer);
  const busy = (SW.data.job && SW.data.job.running) || (SW.data.history_job && SW.data.history_job.running);
  if (busy && XP.view === "stewards") SW.timer = setTimeout(swLoad, 5000);
}
async function swCompile(what) {
  try { await xJSON(`/api/dataset/compile?year=${SW.year}&${what}=1`); } catch (e) { alert(e.message); return; }
  swLoad();
}
async function swHistory() {
  try { await xJSON("/api/history/fetch"); } catch (e) { alert(e.message); return; }
  swLoad();
}
function swCat(v) { SW.cat = v; swRender(); }

function swRender() {
  const d = SW.data;
  if (!d) return;
  safe("stewards", () => {
    const j = d.job, hj = d.history_job;
    const status = [j && j.running ? `${ico("loader")}Compiling: ${esc(j.msg)}` : "", hj && hj.running ? `${ico("loader")}Results since 1950: ${esc(hj.msg)}` : "",
      j && j.error ? `<span class="sgwarn">${esc(j.error)}</span>` : "", hj && hj.error ? `<span class="sgwarn">${esc(hj.error)}</span>` : ""].filter(Boolean).join(" · ");
    $("swStatus").innerHTML = status;
    $("swOut").innerHTML = `${d.spoiler_hidden ? `<div class="note">${ico("eyeoff")}Spoiler mode: the meeting you're replaying or watching delayed, and later ones, are left out.</div>` : ""}
      <div class="srsec" style="margin-top:4px"><b>Stewarding</b>Every stewards' decision of the season from the FIA's documents: what the offence was, what it got, and the rule cited.</div>
      ${swStewards(d.stewards, d.year)}
      <div class="srsec"><b>Team radio</b>The clips the broadcast played, transcribed, sorted by what they are about and set against the timing data.</div>
      ${swRadio(d.radio, d.year)}
      <div class="srsec"><b>Reliability since 1950</b>Why cars didn't finish, from every World Championship race result.</div>
      ${swRel(d.reliability, d.history)}`;
  });
}

/* ================================================================ stewarding */
function swStewards(s, year) {
  if (!s.exists) return `<div class="card"><div class="empty">The ${year} season hasn't been compiled yet: compile it in Explore > Season report first.</div></div>`;
  if (!s.decisions) return `<div class="card"><div class="empty" style="padding:28px 14px">${ico("doc")}<b>No stewards' decisions read yet</b><br>
    They come from the decision documents on fia.com: a few hundred PDFs a season, read a few a minute so as not to load the site, and kept.<br><br>
    <button class="btn primary" onclick="swCompile('stewards')">Read the ${year} decisions</button> <span class="muted">or <code>python3 -m f1hub compile ${year} --stewards</code></span></div></div>`;
  const pen = Object.entries(s.outcomes).filter(([o]) => !["no_action", "warning"].includes(o)).reduce((a, [, v]) => a + v, 0);
  const tiles = `<div class="srlist" style="margin:0 0 12px;gap:6px 24px"><span><b>${s.decisions}</b> decisions</span><span><b>${pen}</b> with a penalty, fine or reprimand</span>
    <span><b>${s.events}</b> Grands Prix</span><span class="muted">${SW_TIERS.map(t => `<span class="dot" style="background:${t[2]}"></span>${t[0]}`).join(" &nbsp;")}</span></div>`;
  return `${tiles}${swCats(s)}
    <div class="two" style="margin-top:16px">${swBlame(s)}${swDrivers(s)}</div>
    <div style="margin-top:16px">${swPairs(s)}</div>
    <div style="margin-top:16px">${swList(s)}</div>`;
}
function swTierBar(outcomes, n) {
  return `<div class="swstack" title="${esc(Object.entries(outcomes).map(([o, v]) => `${v} ${o.replace("_", " ")}`).join(", "))}">${SW_TIERS.map(t => {
    const v = t[1].reduce((a, o) => a + (outcomes[o] || 0), 0);
    return v ? `<i style="width:${(100 * v / n).toFixed(1)}%;background:${t[2]}"></i>` : ""; }).join("")}</div>`;
}
function swCats(s) {
  const rows = s.categories.map(c => `<tr class="click" onclick="swCat('${c.category}')"><td>${esc(c.label)}</td><td class="r num">${c.n}</td>
      <td style="width:34%">${swTierBar(c.outcomes, c.n)}</td><td class="r num" title="share of decisions with the most common outcome (${esc(c.modal.replace("_", " "))})">${swPct(c.consistency)}</td>
      <td class="muted" style="font-size:12px">${Object.keys(c.seconds).length ? Object.entries(c.seconds).map(([k, v]) => `${k} s ×${v}`).join(", ") : ""}</td>
      <td class="muted" style="font-size:12px">${esc((c.articles[0] || [""])[0])}</td></tr>`).join("");
  return `<div class="card"><h2>Outcomes by offence <span class="sub">bar: outcomes, least to most severe · consistency: share given the most common outcome · click an offence to list its decisions</span></h2>
    <div class="scroll"><table><thead><tr><th>Offence</th><th class="r">Decisions</th><th>Outcomes</th><th class="r">Same outcome</th><th>Time penalties</th><th>Rule cited most</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">The offence is read from the document title, then its Fact. Session matters: track limits in qualifying cost a lap time, in the race a time penalty.</div></div>`;
}
function swBlame(s) {
  const lab = {wholly: "Wholly to blame", predominantly: "Predominantly to blame", not_predominantly: "Not wholly or predominantly", racing_incident: "Racing incident", unstated: "Not stated"};
  const rows = s.blame.sort((a, b) => b.n - a.n).map(b => `<tr><td>${lab[b.blame] || esc(b.blame)}</td><td class="r num">${b.n}</td><td style="width:45%">${swTierBar(b.outcomes, b.n)}</td>
      <td class="muted" style="font-size:12px">${Object.entries(b.outcomes).map(([o, v]) => `${v} ${esc((s.outcome_labels[o] || o).toLowerCase())}`).join(", ")}</td></tr>`).join("");
  return `<div class="card"><h2>Collisions in races and sprints <span class="sub">outcome against how the stewards apportioned blame (from the Reason)</span></h2>
    ${rows ? `<div class="scroll"><table><thead><tr><th>Blame</th><th class="r">Decisions</th><th>Outcomes</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty">No collisions decided yet.</div>`}
    <div class="pad xnote">The guidelines tie the penalty to blame: a driver wholly or predominantly to blame usually gets a time penalty, no driver predominantly to blame usually no further action.</div></div>`;
}
function swDec(x, s) {
  const pen = s.outcome_labels[x.outcome] || x.outcome;
  return `<div class="swdec"><div><span class="stripe" style="background:${esc(x.colour || "#888")};display:inline-block;width:3px;height:12px;vertical-align:-1px"></span>
    <b>${esc(x.code || "Car " + (x.driver_number || "?"))}</b> <span class="muted">R${x.round} · ${esc(x.session || "")} · doc ${x.doc}</span>
    <a href="${esc(x.url)}" target="_blank" rel="noopener" class="muted" style="float:right">PDF</a></div>
    <div class="swout">${esc(pen)}${x.seconds ? ` (${x.seconds} s)` : ""}${x.grid ? ` (${x.grid} places)` : ""}${x.points ? `, ${x.points} penalty point${x.points > 1 ? "s" : ""}` : ""}</div>
    <div class="muted" style="font-size:12px">${esc((x.fact || "").slice(0, 220))}</div></div>`;
}
function swPairs(s) {
  if (!s.pairs.length) return `<div class="card"><h2>Similar decisions, different outcomes</h2><div class="empty">None found: decisions worded alike ended alike.</div></div>`;
  const rows = s.pairs.map(p => `<div class="swpair"><div class="swsim" title="how alike the two decisions' wording is (0-1)">${p.similarity.toFixed(2)}</div>${swDec(p.a, s)}${swDec(p.b, s)}</div>`).join("");
  return `<div class="card"><h2>Similar decisions, different outcomes <span class="sub">same offence and type of session, wording alike (offence, fact and reason), outcomes at least two steps apart · worth a closer look, not proof of inconsistency</span></h2>
    <div class="pad">${rows}</div></div>`;
}
function swDrivers(s) {
  const rows = s.drivers.slice(0, 22).map(x => `<tr><td><div class="drv"><span class="stripe" style="background:${esc(x.colour || "#888")}"></span>${esc(x.code || x.number)}</div></td>
      <td class="r num">${x.decisions}</td><td class="r num">${x.penalties}</td><td class="r num">${x.seconds || ""}</td><td class="r num">${x.grid || ""}</td>
      <td class="r num">${x.reprimands || ""}</td><td class="r num">${x.points || ""}</td></tr>`).join("");
  return `<div class="card"><h2>Drivers <span class="sub">decisions about them this season</span></h2><div class="scroll" style="max-height:440px"><table>
    <thead><tr><th>Driver</th><th class="r">Decisions</th><th class="r" title="time, grid, fines, DSQ...">Penalised</th><th class="r">Seconds</th><th class="r">Grid places</th>
    <th class="r">Reprimands</th><th class="r" title="penalty points handed out this season">Points</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}
function swList(s) {
  const cats = s.categories.map(c => `<option value="${c.category}"${SW.cat === c.category ? " selected" : ""}>${esc(c.label)} (${c.n})</option>`).join("");
  const list = s.decisions_list.filter(x => !SW.cat || x.category === SW.cat).slice(-60).reverse();
  const rows = list.map(x => `<tr><td class="pos">R${x.round}</td><td><div class="drv"><span class="stripe" style="background:${esc(x.colour || "#888")}"></span>${esc(x.code || x.driver_number || "-")}</div></td>
      <td class="muted">${esc(x.session || "")}</td><td title="${esc((x.fact || "") + "\n\n" + (x.reason || ""))}"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc((x.title || "").replace(/^Doc \d+ - /, ""))}</a></td>
      <td>${esc(s.outcome_labels[x.outcome] || x.outcome)}${x.seconds ? ` ${x.seconds} s` : ""}</td>
      <td class="muted" style="font-size:12px" title="the most alike decisions for the same offence">${x.similar.map(y => `R${y.round} ${esc(y.code || "")}: ${esc((s.outcome_labels[y.outcome] || y.outcome).toLowerCase())}${y.seconds ? " " + y.seconds + " s" : ""}`).join("; ")}</td></tr>`).join("");
  return `<div class="card"><h2>Decisions <span class="sub">newest first · hover for the fact and reason · the most alike decisions for the same offence, and what they got</span></h2>
    <div class="pad" style="padding-bottom:0"><select onchange="swCat(this.value)" aria-label="offence"><option value="">Every offence</option>${cats}</select></div>
    <div class="scroll" style="max-height:440px"><table><thead><tr><th></th><th>Driver</th><th>Session</th><th>Document</th><th>Outcome</th><th>Alike</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

/* ================================================================ radio */
function swRadio(r, year) {
  if (!r.exists) return "";
  if (!r.clips) return `<div class="card"><div class="empty" style="padding:28px 14px">${ico("mic")}<b>No team radio in the dataset yet</b><br>
    The races' clips (the ones the broadcast played, 20-30 a race) are downloaded and transcribed on this computer with Whisper: a few seconds a clip, kept.<br><br>
    <button class="btn primary" onclick="swCompile('radio')">Add the ${year} team radio</button> <span class="muted">or <code>python3 -m f1hub compile ${year} --radio</code></span></div></div>`;
  const max = Math.max(1, ...Object.values(r.categories));
  const cats = Object.entries(r.categories).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${esc(r.labels[k])}</td>
      <td style="width:55%">${srBar(v / max * 100, SW_SERIES[0], `${v} clips about ${r.labels[k].toLowerCase()}`)}</td><td class="r num">${v}</td></tr>`).join("");
  const t = r.tyres, ex = t.excess, ct = t.control;
  const pm = e => e ? `${e.est > 0 ? "+" : ""}${e.est.toFixed(2)} s${e.se ? ` ± ${(1.96 * e.se).toFixed(2)}` : ""}` : "-";
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const facts = [
    ["Tyre complaints", `${n(t.calls.length, "call", "calls")}. The three laps before the call were ${pm(ex)} a lap slower than the same driver early in that stint (after the model's fuel, compound and wear); at every other lap at that tyre age: ${pm(ct)}.
      ${t.with_stop ? `A stop followed within 3 laps in ${t.stopped_within_3} of ${t.with_stop}, a median ${n(t.laps_to_stop, "lap", "laps")} later.` : ""}`],
    ["Pit calls", r.box.n ? `${n(r.box.n, "call", "calls")} to box; a stop from the lap before to two laps after in ${r.box.followed} (${swPct(r.box.followed / r.box.n)}). A clip's time can trail the call; the rest are mostly instructions for a later lap.` : "None heard."],
    ["Stay out", r.stay_out.n ? `${n(r.stay_out.n, "call", "calls")}; no stop in the next two laps in ${r.stay_out.followed}.` : "None heard."],
    ["Team orders", r.team_orders.calls.length ? `${r.team_orders.calls.length} heard; the team-mates' order changed within three laps in ${r.team_orders.swapped} of ${r.team_orders.known} that could be checked.` : "None heard."],
    ["Saving", r.saving.after_minus_before ? `The three laps after a call to save were ${pm(r.saving.after_minus_before)} a lap against the three before (n ${r.saving.after_minus_before.n}).` : "Not enough calls with clean laps either side."],
    ["Car problems", r.car.n ? `${n(r.car.n, "clip", "clips")}; the car then retired in ${r.car.retired} (${swPct(r.car.retired / r.car.n)}), against ${swPct(r.car.base_dnf)} of all starts.` : "None heard."]];
  const clip = c => `<div class="swclip"><span class="stripe" style="background:${esc(c.colour || "#888")};display:inline-block;width:3px;height:12px;vertical-align:-1px"></span>
    <b>${esc(c.code || c.number)}</b> <span class="muted">R${c.round} ${esc(c.circuit || "")}${c.lap != null ? ", lap " + c.lap : ""}</span>
    <span class="muted" style="font-size:11px">${c.cats.map(k => esc(r.labels[k])).join(" · ")}</span>
    <a href="${esc(c.url)}" target="_blank" rel="noopener" class="muted" style="float:right">${ico("play")}</a><div>${esc(c.text)}</div></div>`;
  const tyreClips = t.calls.slice(-8).reverse().map(c => clip(c) .replace("</div></div>", `</div><div class="muted" style="font-size:12px">tyre age ${c.age ?? "-"}${c.excess != null ? `, ${c.excess > 0 ? "+" : ""}${c.excess.toFixed(2)} s a lap against early in the stint` : ""}${c.laps_to_stop != null ? `, stopped ${c.laps_to_stop} laps later` : ""}</div></div>`)).join("");
  return `<div class="srlist" style="margin:0 0 12px;gap:6px 24px"><span><b>${r.clips}</b> clips from <b>${r.races}</b> races</span><span><b>${r.transcribed}</b> transcribed</span></div>
    <div class="two"><div class="card"><h2>What the radio is about <span class="sub">a clip can be about several things</span></h2><div class="scroll"><table><tbody>${cats}</tbody></table></div>
      <div class="pad xnote">Sorted by key phrases in the transcript. Whisper mishears some words ("grading" for graining), and a clip is filed under the driver it was sent to, even when it is about a rival.</div></div>
    <div class="card"><h2>Against the data</h2><div class="pad">${facts.map(([h, t2]) => `<div class="srh" style="margin-top:6px">${h}</div><div class="xnote">${t2}</div>`).join("")}</div></div></div>
    <div class="two" style="margin-top:16px"><div class="card"><h2>Tyre complaints <span class="sub">latest first</span></h2><div class="pad">${tyreClips || `<div class="empty">None heard yet.</div>`}</div></div>
    <div class="card"><h2>Latest clips <span class="sub">that matched a topic</span></h2><div class="pad" style="max-height:520px;overflow:auto">${r.recent.slice().reverse().map(clip).join("")}</div></div></div>`;
}

/* ================================================================ reliability */
function swRel(rel, h) {
  const hj = SW.data.history_job || {};
  if (!rel.by_year || !rel.by_year.length) return `<div class="card"><div class="empty" style="padding:28px 14px">${ico("download")}<b>No race results downloaded yet</b><br>
    Every World Championship result since 1950 comes from Jolpica (the successor to the Ergast API), which allows about 500 requests an hour: about an hour the first time, then kept.<br><br>
    ${hj.running ? `${ico("loader")}${esc(hj.msg)}` : `<button class="btn primary" onclick="swHistory()">Download results since 1950</button> <span class="muted">or <code>python3 -m f1hub reliability --fetch</code></span>`}</div></div>`;
  const partial = rel.years[1] < new Date().getFullYear() - 1 ? `<div class="note">${ico("loader")}Results downloaded up to ${rel.years[1]}${hj.running ? ` (downloading: ${esc(hj.msg)})` : ` - <a href="#" onclick="swHistory();return false">download the rest</a>`}.</div>` : "";
  return `${partial}${swTrend(rel)}<div class="two" style="margin-top:16px">${swDecades(rel)}${swPU(rel)}</div>
    <div class="two" style="margin-top:16px">${swTeams(rel.teams, `Teams, ${rel.window[0]}-${rel.window[1]}`, rel)}${swTeams(rel.alltime, "Teams, all time (400 starts or more)", rel)}</div>
    <p class="xnote" style="margin-top:12px">Results: Jolpica / Ergast. The Indianapolis 500 (a championship round 1950-60, with other cars and rules) is left out.
      Mechanical: engine, fuel, transmission, electrical, hydraulics, brakes, suspension, wheels, chassis and other technical causes; incidents: accidents, collisions and spins.
      ${rel.causes_until ? `Causes are published up to ${rel.causes_until}; since then results say only "Retired".` : ""}</p>`;
}
function swTrend(rel) {
  const ys = rel.by_year, W = 900, H = 260, L = 44, R = 16, T = 12, B = 28, cap = 0.5;
  const X = y => L + (y - ys[0].year) / ((ys[ys.length - 1].year - ys[0].year) || 1) * (W - L - R), Y = v => T + (1 - Math.min(v, cap) / cap) * (H - T - B);
  let g = "";
  for (let v = 0; v <= cap + 1e-9; v += 0.1) g += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2e3a"/><text x="${L - 6}" y="${Y(v) + 4}" font-size="11" fill="#8b91a1" text-anchor="end">${Math.round(v * 100)}%</text>`;
  for (let y = Math.ceil(ys[0].year / 10) * 10; y <= ys[ys.length - 1].year; y += 10) g += `<text x="${X(y)}" y="${H - 8}" font-size="11" fill="#8b91a1" text-anchor="middle">${y}</text>`;
  const series = [["mechanical", "Mechanical", SW_SERIES[0]], ["incident", "Accident / collision", SW_SERIES[1]], ["unknown", "Retired, no cause given", "#8b91a1"]];
  const lines = series.map(([k, lab, c]) => { const pts = ys.filter(y => k !== "unknown" || y.unknown > 0.02);
    if (!pts.length) return "";
    return `<polyline fill="none" stroke="${c}" stroke-width="2"${k === "unknown" ? ' stroke-dasharray="5 4"' : ""} points="${pts.map(y => `${X(y.year).toFixed(1)},${Y(y[k]).toFixed(1)}`).join(" ")}"/>`; }).join("");
  const hits = ys.map(y => `<rect x="${(X(y.year) - 4).toFixed(1)}" y="${T}" width="8" height="${H - T - B}" fill="transparent"><title>${y.year}: ${y.starts} starts in ${y.races} races · finished ${swPct(y.finished)} · mechanical ${swPct(y.mechanical)} · accident ${swPct(y.incident)}${y.unknown ? ` · no cause given ${swPct(y.unknown)}` : ""}${y.inferred ? " (mechanical / accident inferred from race control)" : ""}</title></rect>`).join("");
  const legend = series.map(([, lab, c], i) => `<span><i style="background:${i === 2 ? `repeating-linear-gradient(90deg,${c} 0 4px,transparent 4px 7px)` : c}"></i>${lab}</span>`).join("");
  return `<div class="card"><h2>How races ended, season by season <span class="sub">share of starts ending in each kind of retirement · hover a season</span></h2>
    <div class="pad"><svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block" role="img" aria-label="retirements by season">${g}${lines}${hits}</svg><div class="legend">${legend}</div></div></div>`;
}
function swDecades(rel) {
  const top = ["engine", "transmission", "suspension", "fuel", "electrical"], col = Object.fromEntries(top.map((k, i) => [k, SW_SERIES[i]]));
  const rows = rel.decades.map(d => { const tot = Object.values(d.causes).reduce((a, b) => a + b, 0) || 1;
    const other = tot - top.reduce((a, k) => a + (d.causes[k] || 0), 0);
    const segs = top.map(k => d.causes[k] ? `<i style="width:${(100 * d.causes[k] / tot).toFixed(1)}%;background:${col[k]}" title="${esc(rel.labels[k])}: ${d.causes[k]}"></i>` : "").join("")
      + (other ? `<i style="width:${(100 * other / tot).toFixed(1)}%;background:${SW_SERIES[5]}" title="other mechanical: ${other}"></i>` : "");
    return `<tr><td class="pos">${d.decade}s</td><td style="width:55%"><div class="swstack">${segs}</div></td><td class="r num" title="mechanical retirements per 100 starts">${d.per_100.toFixed(1)}</td><td class="r num muted">${d.starts}</td></tr>`; }).join("");
  const legend = top.map(k => `<span><i style="background:${col[k]}"></i>${esc(rel.labels[k])}</span>`).join("") + `<span><i style="background:${SW_SERIES[5]}"></i>Other mechanical</span>`;
  return `<div class="card"><h2>What failed, decade by decade <span class="sub">mechanical retirements by cause · per 100 starts</span></h2>
    <div class="scroll"><table><thead><tr><th></th><th>Cause</th><th class="r">Per 100</th><th class="r">Starts</th></tr></thead><tbody>${rows}</tbody></table></div><div class="pad legend">${legend}</div></div>`;
}
function swPU(rel) {
  if (!rel.power_units.length) return `<div class="card"><h2>Power units, hybrid era</h2><div class="empty">Seasons from 2014 aren't downloaded yet.</div></div>`;
  const years = [...new Set(rel.power_units.map(p => p.year))].sort(), makers = [...new Set(rel.power_units.map(p => p.maker))];
  const cell = (m, y) => { const p = rel.power_units.find(x => x.maker === m && x.year === y);
    if (!p) return `<td></td>`;
    const v = p.causes_published ? p.pu.est : null;
    return `<td class="r num" title="${esc(m)} ${y}: ${p.starts} starts, ${p.pu.k} power-unit retirements (${swPct(p.pu.lo, 1)}-${swPct(p.pu.hi, 1)}, 95%), ${p.mechanical.k} mechanical in all${p.unknown ? `; ${p.unknown} retirements with no cause given` : ""}">${v == null ? `<span class="muted">${swPct(p.mechanical.est, 1)}*</span>` : swPct(v, 1)}</td>`; };
  const rows = makers.map(m => `<tr><td>${esc(m)}</td>${years.map(y => cell(m, y)).join("")}</tr>`).join("");
  return `<div class="card"><h2>Power units, hybrid era <span class="sub">share of starts ending with an engine, ERS, fuel or electrical failure, every car using that maker's power unit</span></h2>
    <div class="scroll"><table><thead><tr><th>Maker</th>${years.map(y => `<th class="r">${String(y).slice(2)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>
    <div class="pad xnote">* No causes published: the share of starts ending in a retirement with no incident on the car (where the season dataset is compiled) or with no cause given, an upper bound. Hover for counts and 95% intervals.</div></div>`;
}
function swTeams(all, title, rel) {
  const list = all.filter(t => t.starts >= 20), few = all.length - list.length;
  const max = Math.max(0.01, ...list.map(t => t.mechanical.hi || 0));
  const rows = list.map(t => `<tr><td>${esc(t.name)} <span class="muted">${t.first === t.last ? t.first : `${t.first}-${String(t.last).slice(2)}`}</span></td><td class="r num">${t.starts}</td>
      <td style="width:34%"><div class="swci" title="${esc(t.name)}: ${swPct(t.mechanical.est, 1)} of starts ended in a mechanical retirement (95%: ${swPct(t.mechanical.lo, 1)}-${swPct(t.mechanical.hi, 1)})">
        <i style="left:${(t.mechanical.lo / max * 100).toFixed(1)}%;width:${((t.mechanical.hi - t.mechanical.lo) / max * 100).toFixed(1)}%"></i><b style="left:${(t.mechanical.est / max * 100).toFixed(1)}%"></b></div></td>
      <td class="r num">${swPct(t.mechanical.est, 1)}</td><td class="r num muted">${swPct(t.finish.est)}</td></tr>`).join("");
  return `<div class="card"><h2>${title} <span class="sub">mechanical retirements per start, with 95% interval · most reliable first</span></h2>
    <div class="scroll" style="max-height:460px"><table><thead><tr><th>Team</th><th class="r">Starts</th><th>Mechanical</th><th class="r"></th><th class="r">Finished</th></tr></thead><tbody>${rows}</tbody></table></div>${few ? `<div class="pad xnote">${few} team${few > 1 ? "s" : ""} with fewer than 20 starts not shown.</div>` : ""}</div>`;
}
