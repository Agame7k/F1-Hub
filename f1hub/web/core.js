/* F1 Hub dashboard - shared helpers and state, tabs, the driver picker.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const $ = id => document.getElementById(id);
// an icon from the sprite at the top of dashboard.html (scales with the text, takes its colour)
const ico = (name, cls = "") => `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtLap = t => { if (t == null) return "-"; const m = Math.floor(t / 60), s = (t - m * 60).toFixed(3).padStart(6, "0"); return m ? `${m}:${s}` : (+s).toFixed(3); };
const fmtGap = v => v == null ? "" : (typeof v === "number" ? "+" + v.toFixed(3) : esc(v));
const TYRE = {SOFT: "#e10600", MEDIUM: "#ffd300", HARD: "#f0f0f0", INTERMEDIATE: "#39b54a", WET: "#0067ff", UNKNOWN: "#666"};
const KIND_LABEL = {red: "red flag", sc: "safety car", vsc: "vsc", sc_end: "sc ending", sector: "sector", blue: "blue flag",
  green: "green", track_clear: "clear", chequered: "chequered", black: "black flag", black_white: "black/white", black_orange: "black/orange",
  penalty: "penalty", penalty_served: "penalty served", investigation: "investigation", decision: "decision", track_limits: "track limits", lap_deleted: "lap deleted",
  reprimand: "reprimand", session: "session", overtake_mode: "overtake mode", info: "info", pit: "pit", weather: "weather",
  start: "start", incident: "incident"};
let D = null, S = null, lastPos = {}, rcFilter = "all", seenRc = 0, activeTab = "live", focus = null;

// each browser tab reports what it's showing; the server prioritises the union of all visible tabs
const CID = (() => { try { let c = sessionStorage.getItem("f1hub.cid"); if (!c) { c = Math.random().toString(36).slice(2, 10); sessionStorage.setItem("f1hub.cid", c); } return c; } catch (e) { return Math.random().toString(36).slice(2, 10); } })();
function sendView() { fetch(`/api/view?cid=${CID}&tab=` + (document.hidden ? "hidden" : activeTab)).catch(() => {}); }
document.addEventListener("visibilitychange", sendView);
setInterval(sendView, 30000);
setTimeout(sendView, 500);
const TAB_ORDER = ["home", "live", "rc", "map", "strat", "ins", "gaps", "drv", "radio", "champ", "cal", "explore"];
const DIGIT_TABS = TAB_ORDER.slice(1);      // 1-9, 0 = live timing … calendar; H = home
function openTab(t) {
  const sub = String(t || "").split("/");      // #explore/report opens an Explore view directly (bookmarkable)
  if (sub[0] === "explore" && sub[1] && typeof XP !== "undefined") XP.view = sub[1];
  t = sub[0];
  if (!TAB_ORDER.includes(t)) t = "live";
  activeTab = t;
  try { localStorage.setItem("f1hub.tab", JSON.stringify(t)); } catch (e) {}
  if (location.hash.slice(1) !== t) history.replaceState(null, "", "#" + t);
  sendView();
  document.querySelectorAll("nav button").forEach(x => x.classList.toggle("on", x.dataset.tab === t));
  document.querySelectorAll("main").forEach(m => m.classList.toggle("hidden", m.id !== "tab-" + t));
  if (t === "rc" && D) { seenRc = D.rc.messages.length; $("rcbadge").classList.add("hidden"); }
  if (t === "home") loadHome();
  if (t === "explore") xShow(XP.view);
  if (D) render(D);
}
document.querySelectorAll("nav button").forEach(b => b.onclick = () => openTab(b.dataset.tab));
document.querySelectorAll("#rcfilters button").forEach(b => b.onclick = () => {
  rcFilter = b.dataset.g;
  document.querySelectorAll("#rcfilters button").forEach(x => x.classList.toggle("on", x === b));
  if (D) renderRC(D);
});
$("drvsel").onchange = e => setFocus(e.target.value ? +e.target.value : null);

// stay: pick the driver without leaving the tab (the track map, where it lights up the car and drives the ghost)
async function setFocus(n, stay) {
  if (stay && n === focus) n = null;          // clicking the selected car again lets it go
  focus = n;
  try { const r = await fetch("/api/focus?driver=" + (n || ""), {cache: "no-store"}); render(await r.json()); } catch (e) {}
  if (n && !stay) openTab("drv");
}
