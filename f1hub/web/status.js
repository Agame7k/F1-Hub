/* F1 Hub dashboard - the setup check (Status button, /api/diagnostics), the "restart to use the new settings"
   notice, and the season while it loads (the server starts before the season is in).
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const STATUS = {data: null, open: false, dismissed: false};
const ST_WORD = {ok: "ok", warn: "check", fail: "problem", info: "info"};

async function loadStatus() {
  try {
    const r = await fetch("/api/diagnostics", {cache: "no-store"});
    STATUS.data = await r.json();
  } catch (e) { return; }          // the server not answering is shown by the live polls
  safe("status", renderStatus);
}
function renderStatus() {
  const d = STATUS.data;
  if (!d || !d.checks) return;
  const worst = d.checks.some(c => c.status === "fail") ? "fail" : d.checks.some(c => c.status === "warn") ? "warn" : "ok";
  const btn = $("statusbtn");
  btn.className = "gear statusbtn st-" + worst;
  btn.title = d.problems ? `Setup check: ${d.problems} thing${d.problems === 1 ? "" : "s"} to look at (D)` : "Setup check: all good (D)";
  $("restartnote").classList.toggle("hidden", !d.restart_needed || STATUS.dismissed);
  if (!STATUS.open) return;
  $("statuslist").innerHTML = d.checks.map(c => `<div class="strow st-${c.status}"><span class="stw">${ST_WORD[c.status] || esc(c.status)}</span>
      <b>${esc(c.name)}</b><span>${esc(c.detail)}${c.fix ? `<small>${esc(c.fix)}</small>` : ""}</span></div>`).join("");
  const recent = (d.recent || []).slice().reverse();
  $("statusrecent").innerHTML = recent.length ? "<h4>Latest warnings and errors</h4>" + recent.map(r =>
      `<div class="strecent"><span class="num">${esc(new Date(r.at * 1000).toLocaleTimeString())}</span>${esc(r.message)}</div>`).join("") : "";
  $("statuslog").textContent = d.log_file || "";
}
function toggleStatus(on) {
  STATUS.open = on === undefined ? !STATUS.open : on;
  $("statuspanel").classList.toggle("hidden", !STATUS.open);
  if (STATUS.open) { renderStatus(); loadStatus(); }
}
function dismissRestart() { STATUS.dismissed = true; $("restartnote").classList.add("hidden"); }

/* the season: polled every few seconds until it's in, then every minute */
const SEASON = {ready: false, timer: 0};
async function loadSeason() {
  clearTimeout(SEASON.timer);
  await poll("/api/season", d => { seasonNote(d.season_status); renderSeason(d); },
             b => seasonNote(b && b.season_status, b && b.error));
  clearTimeout(SEASON.timer);
  SEASON.timer = setTimeout(loadSeason, SEASON.ready ? 60000 : 5000);
}
function seasonNote(st, error) {
  st = st || {};
  SEASON.ready = st.state === "ready" || st.state === "stale";
  let text = "";
  if (st.state === "loading")
    text = `<b>Loading the ${esc(st.year)} season</b>${st.progress ? ` - ${esc(st.progress)}` : ""}. Standings, the calendar and the title fight appear as soon as it's in.`;
  else if (st.state === "failed")
    text = `<b>Couldn't load the ${esc(st.year)} season yet</b> - ${esc(st.error || error || "")}. Trying again shortly.`;
  else if (st.state === "stale")
    text = `Showing the ${esc(st.year)} season as it was last loaded: refreshing it failed (${esc(st.error || "")}).`;
  else if ((st.missing || []).length)
    text = `Not in yet: ${st.missing.map(esc).join(", ")} - fetched again shortly.`;
  document.querySelectorAll("[data-seasonnote]").forEach(el => { el.classList.toggle("hidden", !text); el.innerHTML = text; });
}
