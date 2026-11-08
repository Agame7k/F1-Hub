/* F1 Hub dashboard - start-up: the first tab, then live data (pushed over /api/events, polled otherwise).
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
// landing: a link with #tab opens that tab; otherwise live timing when something is on, the home page when not
const LANDING = {auto: !location.hash.slice(1), touched: false};
openTab(location.hash.slice(1) || "live");
document.querySelector("nav").addEventListener("click", () => LANDING.touched = true);

setInterval(tickHome, 1000);
setInterval(loadHome, 60000);
loadHome();

poll("/api/live", render); loadSeason();
loadStatus(); setInterval(loadStatus, 60000);           // the Status button's colour, and the restart notice
// The server pushes each new frame over /api/events (several a second while the MQTT stream is up). If that
// isn't open - old browser, too many tabs, server restarting - pull /api/live about once a second instead.
const LIVE = {es: null, open: false, retry: 0};
function startEvents() {
  if (!window.EventSource || LIVE.es || document.hidden) return;
  const es = LIVE.es = new EventSource("/api/events");
  es.onopen = () => { LIVE.open = true; };
  es.onmessage = e => {
    let d;
    try { d = JSON.parse(e.data); } catch (err) { return; }
    if (NET.down) { NET.down = 0; $("offline").classList.add("hidden"); }
    if (!CTL.busy) safe("/api/live", () => render(d));
  };
  es.onerror = () => {
    LIVE.open = false;                       // polling takes over (and shows "Reconnecting…" if the server is gone)
    if (es.readyState === EventSource.CLOSED) {          // refused (e.g. too many tabs): try again later
      stopEvents();
      clearTimeout(LIVE.retry);
      LIVE.retry = setTimeout(startEvents, 30000);
    }
  };
}
function stopEvents() { if (LIVE.es) LIVE.es.close(); LIVE.es = null; LIVE.open = false; }
document.addEventListener("visibilitychange", () => document.hidden ? stopEvents() : startEvents());
startEvents();
let liveBusy = false;
async function livePoll() {
  if (!liveBusy && !LIVE.open) {
    liveBusy = true;
    const gen = CTL.gen;      // asked before a click? then it shows the old state - drop it
    await poll("/api/live", d => { if (!CTL.busy && gen === CTL.gen) render(d); });
    liveBusy = false;
  }
  setTimeout(livePoll, document.hidden ? 5000 : 1000);
}
setTimeout(livePoll, 1000);

