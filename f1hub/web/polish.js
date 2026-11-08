/* F1 Hub dashboard - how it feels to use: the sliding tab marker, the tab bar that stays on screen, each tab
   keeping its own scroll position, cars sliding to their new place in the timing tower, the dimmed backdrop
   behind the settings drawer, and the command palette (Ctrl+K).
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
const REDUCED = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : {matches: false};
const TAB_TITLES = {home: "Home", live: "Live timing", rc: "Race control", map: "Track map", strat: "Strategy & pits", ins: "Insights",
  gaps: "Gaps", drv: "Driver telemetry", radio: "Team radio", champ: "Championship", cal: "Calendar", explore: "Explore"};

/* ---------- the tab bar: a marker that slides to the open tab, and stays on screen when you scroll ---------- */
const NAVBAR = document.querySelector("nav");
const NAVINK = document.createElement("i");
NAVINK.id = "navink";
NAVBAR.appendChild(NAVINK);
function moveInk(instant) {
  const b = NAVBAR.querySelector("button.on");
  if (!b) return;
  if (instant) NAVINK.style.transition = "none";
  NAVINK.style.width = Math.max(b.offsetWidth - 20, 8) + "px";
  NAVINK.style.transform = `translateX(${b.offsetLeft + 10}px) skewX(-30deg)`;
  NAVINK.style.opacity = 1;
  if (instant) { void NAVINK.offsetWidth; NAVINK.style.transition = ""; }
  // on a narrow screen the bar scrolls sideways: bring the open tab into view
  const l = b.offsetLeft, r = l + b.offsetWidth;
  if (l < NAVBAR.scrollLeft || r > NAVBAR.scrollLeft + NAVBAR.clientWidth)
    NAVBAR.scrollTo({left: l - 24, behavior: REDUCED.matches || instant ? "auto" : "smooth"});
}
if (window.ResizeObserver) new ResizeObserver(() => moveInk(true)).observe(NAVBAR);   // the race-control badge, fonts, resizing
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveInk(true));

// the replay bar is sticky too: the tab bar sits just below it
const SCRUBBAR = $("scrubbar");
function stickyOffset() { document.documentElement.style.setProperty("--scrub-h", SCRUBBAR.offsetHeight + "px"); }
if (window.ResizeObserver) new ResizeObserver(stickyOffset).observe(SCRUBBAR);
stickyOffset();
let stuckFrame = 0;
addEventListener("scroll", () => {
  if (stuckFrame) return;
  stuckFrame = requestAnimationFrame(() => {
    stuckFrame = 0;
    NAVBAR.classList.toggle("stuck", scrollY > 0 && NAVBAR.getBoundingClientRect().top <= SCRUBBAR.offsetHeight + 1);
  });
}, {passive: true});

/* ---------- switching tabs: each one remembers where you were, and the window title follows ---------- */
const TABSCROLL = {};
const openTabBase = openTab;
openTab = function (t) {
  const from = activeTab;
  TABSCROLL[from] = scrollY;
  openTabBase(t);
  if (activeTab !== from) {
    // back where you left it, but never above the tab bar if you'd scrolled past the header
    const navTop = NAVBAR.offsetTop - SCRUBBAR.offsetHeight;
    const y = TABSCROLL[activeTab];
    scrollTo({top: y != null ? y : Math.min(scrollY, Math.max(navTop, 0)), behavior: "auto"});
  }
  moveInk();
  document.title = (TAB_TITLES[activeTab] || "Dashboard") + " · F1 Hub";
  // opening the map draws the circuit in again
  const trk = activeTab !== from && activeTab === "map" && document.querySelector("#bigmap svg.trk");
  if (trk && !REDUCED.matches) { trk.classList.remove("intro"); void trk.getBoundingClientRect(); trk.classList.add("intro"); }
};
moveInk(true);
document.title = (TAB_TITLES[activeTab] || "Dashboard") + " · F1 Hub";

/* ---------- the timing tower: cars slide to their new place, new times light up ---------- */
// The tower is rebuilt on every update (several a second), which would cut any CSS animation short. So: FLIP for the
// rows (note where each was, rebuild, animate from there), and highlights remembered by time and re-applied to the
// new rows with a negative delay, so they carry on where they were.
const HL = {rows: {}, cells: {}, fresh: false};
const HL_CELLS = [4, 5, 6, 7, 8, 9, 10];        // S1, S2, S3, last lap, best, tyre, pits
function flipRows(tbody, update) {
  if (REDUCED.matches || tbody.closest("main.hidden") || !tbody.animate) { update(); HL.fresh = false; return; }
  const box0 = tbody.getBoundingClientRect().top, before = {}, text = {};
  tbody.querySelectorAll("tr[data-n]").forEach(r => {
    before[r.dataset.n] = r.getBoundingClientRect().top - box0;
    text[r.dataset.n] = HL_CELLS.map(i => r.cells[i] ? r.cells[i].textContent : "");
  });
  update();
  const box1 = tbody.getBoundingClientRect().top, now = performance.now();
  tbody.querySelectorAll("tr[data-n]").forEach(r => {
    const n = r.dataset.n, y = before[n];
    if (y != null) {
      const dy = y - (r.getBoundingClientRect().top - box1);
      if (Math.abs(dy) >= 2) r.animate([{transform: `translateY(${dy}px)`}, {transform: "none"}],
                                       {duration: Math.min(700, 380 + Math.abs(dy) * 0.6), easing: "cubic-bezier(.2,.8,.2,1)"});
    }
    // a place gained or lost: render.js marks it for this one frame; keep it going for the whole tint
    if (r.classList.contains("flash")) HL.rows[n] = {t: now, cls: r.classList.contains("gain") ? "gain" : "lose"};
    const hr = HL.rows[n];
    if (hr && now - hr.t < 1600) { r.classList.add("flash", hr.cls); r.style.setProperty("--hl-d", -(now - hr.t) + "ms"); }
    else r.classList.remove("flash", "gain", "lose");
    // a new sector / lap time, tyre or stop
    HL_CELLS.forEach((ci, j) => {
      const td = r.cells[ci], key = n + ":" + ci;
      if (!td) return;
      const v = td.textContent.trim();
      if (HL.fresh && text[n] && text[n][j] !== td.textContent && v && v !== "-") HL.cells[key] = now;
      const t = HL.cells[key];
      if (t && now - t < 1400) { td.classList.add("chg"); td.style.animationDelay = -(now - t) + "ms"; }
    });
  });
  HL.fresh = true;
}

/* ---------- the header: a flag change sweeps its colour across, a new lap rolls in ---------- */
const FLAGCOL = {GREEN: "#39b54a", SC: "#ffb000", VSC: "#ffb000", RED: "#e10600", CHEQUERED: "#ffffff", YELLOW: "#ffd300"};
const SWEEP = document.createElement("i");
SWEEP.id = "flagsweep";
document.querySelector("header").appendChild(SWEEP);
const HEAD = {status: "", lap: ""};
new MutationObserver(() => {
  const st = $("status"), now = (st.className.match(/\b(GREEN|SC|VSC|RED|CHEQUERED|YELLOW)\b/) || [""])[0];
  if (now === HEAD.status) return;
  const was = HEAD.status;
  HEAD.status = now;
  if (!was || !now || REDUCED.matches) return;          // the first paint isn't a change
  st.animate([{transform: "scale(1.25)", filter: "brightness(1.6)"}, {transform: "none", filter: "none"}], {duration: 520, easing: "cubic-bezier(.3,1.4,.5,1)"});
  SWEEP.style.setProperty("--fc", FLAGCOL[now] || "#fff");
  SWEEP.animate([{transform: "scaleX(0)", opacity: 1}, {transform: "scaleX(1)", opacity: 1, offset: .55}, {transform: "scaleX(1)", opacity: 0}],
                {duration: 1600, easing: "cubic-bezier(.2,.8,.2,1)"});
}).observe($("status"), {attributes: true, attributeFilter: ["class"]});
new MutationObserver(() => {
  const v = $("lap").textContent;
  if (v === HEAD.lap) return;
  const was = HEAD.lap;
  HEAD.lap = v;
  if (!was || was === "-" || REDUCED.matches) return;
  $("lap").animate([{transform: "translateY(-55%)", opacity: 0}, {transform: "none", opacity: 1}], {duration: 380, easing: "cubic-bezier(.2,.8,.2,1)"});
}).observe($("lap"), {childList: true, characterData: true, subtree: true});

/* ---------- settings drawer: dim the page behind it, click outside to close ---------- */
const SCRIM = document.createElement("div");
SCRIM.id = "scrim";
SCRIM.onclick = () => { if ($("drawer").classList.contains("open")) toggleDrawer(); };
document.body.appendChild(SCRIM);
new MutationObserver(() => SCRIM.classList.toggle("on", $("drawer").classList.contains("open")))
  .observe($("drawer"), {attributes: true, attributeFilter: ["class"]});

/* ---------- command palette: Ctrl+K, type a few letters, Enter ---------- */
const CMD = {el: null, input: null, list: null, items: [], shown: [], sel: 0, back: null};
const EXPLORE_VIEWS = [["analysis", "Session analysis", "chart"], ["lab", "Telemetry lab", "activity"], ["stats", "Season stats", "trend"],
  ["circuit", "Circuit preview", "map"], ["ff1", "FastF1 & recordings", "rec"]];
const TAB_ICON = {home: "home", live: "clock", rc: "flag", map: "map", strat: "activity", ins: "bolt", gaps: "trend", drv: "user",
  radio: "mic", champ: "trophy", cal: "calendar", explore: "compass"};

function cmdItems() {
  const it = TAB_ORDER.map((t, i) => ({group: "Go to", label: TAB_TITLES[t], icon: TAB_ICON[t],
    key: t === "home" ? "H" : t === "explore" ? "E" : String(i % 10), run: () => openTab(t)}));
  EXPLORE_VIEWS.forEach(([v, label, icon]) => it.push({group: "Explore", label, icon, run: () => xOpen(v)}));
  const replaying = D && D.mode === "replay";
  if (replaying) it.push({group: "Actions", label: "Back to live", icon: "arrow", key: "L", run: () => ctl({mode: "live"})});
  it.push(
    {group: "Actions", label: "Watch a replay…", icon: "play", run: () => { if (!$("drawer").classList.contains("open")) toggleDrawer(); setTimeout(() => $("sessionsel").focus(), 260); }},
    {group: "Actions", label: "Set a delay to match your TV", icon: "clock", key: "S", run: () => { if (!$("drawer").classList.contains("open")) toggleDrawer(); setTimeout(() => $("delaynum").focus(), 260); }},
    {group: "Actions", label: ($("spoiler").checked ? "Turn spoiler mode off" : "Turn spoiler mode on"), icon: "eyeoff",
     run: () => { const on = !$("spoiler").checked; $("spoiler").checked = on; $("spoiler").dispatchEvent(new Event("change")); }},
    {group: "Actions", label: M3D.on ? "Track map in 2D" : "Track map in 3D", icon: "cube", run: () => { openTab("map"); m3dSet(!M3D.on); }},
    {group: "Actions", label: "Mini timing tower", icon: "pip", key: "M", run: openPip},
    {group: "Actions", label: "Setup check", icon: "check", key: "D", run: () => toggleStatus(true)},
    {group: "Actions", label: "Keyboard shortcuts", icon: "help", key: "?", run: () => toggleHelp(true)});
  if ($("celebbtn").classList.contains("on")) it.push({group: "Actions", label: "Replay the celebration", icon: "trophy", run: replayCelebration});
  ((D && D.rows) || []).slice().sort((a, b) => (a.pos || 99) - (b.pos || 99)).forEach(r => it.push({
    group: "Drivers", label: `${r.code} ${r.name || ""}`.trim(), sub: r.team || "", colour: r.colour, icon: "user",
    run: () => setFocus(r.number)}));
  return it;
}
// a word match beats a match inside a word, which beats letters in order ("rcon" finds "Race control")
function cmdScore(label, q) {
  const s = label.toLowerCase();
  if (!q) return 1;
  const at = s.indexOf(q);
  if (at === 0) return 100;
  if (at > 0) return s[at - 1] === " " ? 80 : 60;
  // letters in order, but close together: "nor" shouldn't find "turN spOileR"
  let i = 0, first = -1, last = -1;
  for (let k = 0; k < s.length && i < q.length; k++) if (s[k] === q[i]) { if (first < 0) first = k; last = k; i++; }
  return i === q.length && last - first < q.length * 2.5 ? 20 : 0;
}
function cmdBuild() {
  const el = CMD.el = document.createElement("div");
  el.id = "cmdk"; el.className = "hidden";
  el.innerHTML = `<div class="in" role="dialog" aria-modal="true" aria-label="Command palette">
      <div class="cmdq">${ico("search")}<input id="cmdinput" type="text" autocomplete="off" spellcheck="false"
        placeholder="Jump to a tab, driver or action…" aria-controls="cmdlist"><kbd>Esc</kbd></div>
      <div id="cmdlist" role="listbox"></div>
      <div class="cmdfoot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span></div></div>`;
  document.body.appendChild(el);
  CMD.input = $("cmdinput"); CMD.list = $("cmdlist");
  el.addEventListener("mousedown", e => { if (e.target === el) closeCmd(); });
  CMD.input.addEventListener("input", () => { CMD.sel = 0; cmdRender(); });
  CMD.input.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = CMD.shown.length;
      if (n) { CMD.sel = (CMD.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; cmdRender(true); }
    } else if (e.key === "Enter") { e.preventDefault(); cmdRun(CMD.sel); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeCmd(); }
  });
  CMD.list.addEventListener("mousemove", e => {
    const b = e.target.closest("[data-i]");
    if (b && +b.dataset.i !== CMD.sel) { CMD.sel = +b.dataset.i; cmdRender(true); }
  });
  CMD.list.addEventListener("click", e => { const b = e.target.closest("[data-i]"); if (b) cmdRun(+b.dataset.i); });
}
function cmdRender(selOnly) {
  if (selOnly) {
    CMD.list.querySelectorAll("[data-i]").forEach(b => b.setAttribute("aria-selected", +b.dataset.i === CMD.sel));
    const on = CMD.list.querySelector('[aria-selected="true"]');
    if (on) on.scrollIntoView({block: "nearest"});
    return;
  }
  const q = CMD.input.value.trim().toLowerCase();
  CMD.shown = CMD.items.map((x, i) => ({x, s: Math.max(cmdScore(x.label, q), cmdScore(x.sub || "", q) / 2), i}))
    .filter(o => o.s > 0).sort((a, b) => q ? b.s - a.s || a.i - b.i : a.i - b.i).map(o => o.x);
  let group = "";
  CMD.list.innerHTML = !CMD.shown.length ? `<div class="cmdnone">Nothing matches “${esc(CMD.input.value)}”</div>`
    : CMD.shown.map((x, i) => {
      const head = !q && x.group !== group ? `<div class="cmdgroup">${esc(group = x.group)}</div>` : "";
      return `${head}<div class="cmditem" role="option" data-i="${i}" aria-selected="${i === CMD.sel}">
        ${x.colour ? `<span class="stripe" style="background:${esc(x.colour)}"></span>` : ico(x.icon)}
        <span class="cl">${esc(x.label)}${x.sub ? `<small>${esc(x.sub)}</small>` : ""}</span>
        ${q ? `<span class="cg">${esc(x.group)}</span>` : ""}${x.key ? `<kbd>${esc(x.key)}</kbd>` : ""}</div>`;
    }).join("");
}
function cmdRun(i) {
  const x = CMD.shown[i];
  if (!x) return;
  closeCmd(true);
  try { x.run(); } catch (e) { console.warn("command", e); }
}
function openCmd() {
  if (!CMD.el) cmdBuild();
  if (!CMD.el.classList.contains("hidden")) { closeCmd(); return; }
  CMD.back = document.activeElement;
  CMD.items = cmdItems(); CMD.sel = 0; CMD.input.value = "";
  cmdRender();
  CMD.el.classList.remove("hidden");
  CMD.input.focus();
}
function closeCmd(keepFocus) {
  if (!CMD.el) return;
  CMD.el.classList.add("hidden");
  if (!keepFocus && CMD.back && CMD.back.focus) CMD.back.focus();
}
// its button sits at the end of the tab bar (added here, after core.js has wired the tab buttons)
const CMDBTN = document.createElement("button");
CMDBTN.className = "navcmd"; CMDBTN.type = "button";
CMDBTN.title = "Jump to a tab, driver or action (Ctrl+K)"; CMDBTN.setAttribute("aria-label", "Command palette");
CMDBTN.innerHTML = `<span>${ico("search")}<span class="t">Jump to…</span><kbd>Ctrl K</kbd></span>`;
CMDBTN.onclick = e => { e.stopPropagation(); openCmd(); };
NAVBAR.appendChild(CMDBTN);
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === "k" || e.key === "K")) { e.preventDefault(); openCmd(); }
});

/* ---------- tooltips: quicker and easier to read than the browser's own, with the shortcut as a key ---------- */
const TIP = {el: null, target: null, timer: 0, watch: 0};
function tipText(t) {
  const m = t.match(/^(.*?)\s*\(((?:Ctrl\+)?[^\s()]{1,5})\)$/);       // "Settings (S)" -> Settings [S]
  return m ? `${esc(m[1])}<kbd>${esc(m[2])}</kbd>` : esc(t);
}
function tipShow(t) {
  if (!document.contains(t) || !t.dataset.tip) return;
  if (!TIP.el) { TIP.el = document.createElement("div"); TIP.el.id = "tip"; TIP.el.setAttribute("role", "tooltip"); document.body.appendChild(TIP.el); }
  const el = TIP.el, r = t.getBoundingClientRect();
  el.innerHTML = tipText(t.dataset.tip);
  el.className = "";
  const w = el.offsetWidth, h = el.offsetHeight;
  const below = r.bottom + h + 12 < innerHeight;
  el.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
  el.style.top = (below ? r.bottom + 8 : r.top - h - 8) + "px";
  el.classList.add(below ? "below" : "above");
  requestAnimationFrame(() => el.classList.add("on"));
  clearInterval(TIP.watch);
  TIP.watch = setInterval(() => { if (!document.contains(t)) tipHide(); }, 250);    // rebuilt under the pointer
}
function tipHide() {
  clearTimeout(TIP.timer); clearInterval(TIP.watch);
  const t = TIP.target;
  TIP.target = null;
  if (TIP.el) TIP.el.classList.remove("on");
  if (t && t.dataset.tip != null) {
    if (!t.hasAttribute("title")) t.setAttribute("title", t.dataset.tip);     // unless the page set a new one meanwhile
    delete t.dataset.tip;
  }
}
document.addEventListener("pointerover", e => {
  if (e.pointerType === "touch") return;
  const t = e.target.closest && e.target.closest("[title]:not(svg *), [data-tip]");
  if (t === TIP.target) return;
  tipHide();
  if (!t || t.closest("#celeb")) return;
  const title = t.getAttribute("title");
  if (title != null) { t.dataset.tip = title; t.removeAttribute("title"); }      // no native tooltip on top of ours
  if (!t.dataset.tip) return;
  TIP.target = t;
  TIP.timer = setTimeout(() => tipShow(t), document.getElementById("tip") && TIP.recent ? 60 : 380);
});
document.addEventListener("pointerout", e => {
  if (TIP.target && !(e.relatedTarget && TIP.target.contains(e.relatedTarget))) { TIP.recent = Date.now(); tipHide(); setTimeout(() => TIP.recent = 0, 400); }
});
["pointerdown", "keydown", "scroll"].forEach(ev => addEventListener(ev, () => { if (TIP.target) tipHide(); }, {passive: true, capture: true}));
