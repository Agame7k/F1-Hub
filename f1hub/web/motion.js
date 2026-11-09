/* F1 Hub dashboard - motion for the data itself: charts draw in, bars grow from their baseline, dots pop in, headline
   numbers count up, table rows and panels cascade in, and panels further down wait until they are scrolled to.
   It works on whatever the views render (no view needs to know about it): a MutationObserver sees new panels
   (.card) and animates the ones it has not shown in this visit to the tab. A live refresh re-renders the same
   panels, so it isn't animated again; opening a tab or view, or picking something else in a dropdown, is.
   Follows the system's reduced-motion setting (REDUCED, polish.js). Loaded after polish.js. */
const MO = {
  seen: new Set(),            // panels shown in this visit: "scope|title"
  fresh: new WeakSet(),       // scopes whose next render should animate (a tab or view just opened, a dropdown changed)
  queue: new Set(), frame: 0, io: null, timers: new WeakMap(),
  EASE: "cubic-bezier(.2,.8,.2,1)", SPRING: "cubic-bezier(.3,1.35,.5,1)",
};
const moOff = () => REDUCED.matches || !document.body.animate;

// the panel's identity: the nearest tab or view, and its title (or its place when it has none)
function moScope(el) { return el.closest("main > section[id], main[id]") || el.closest("main") || document.body; }
function moKey(card) {
  const sc = moScope(card), h = card.querySelector(":scope > h2, :scope > .h2, h2");
  let t = h ? (h.firstChild && h.firstChild.textContent || h.textContent).trim() : "";
  if (!t) t = "#" + Array.prototype.indexOf.call(sc.querySelectorAll(".card"), card);
  return (sc.id || "page") + "|" + t;
}
function moForget(scope) {
  if (!scope) return;
  const pre = [scope.id || "page", ...[...scope.querySelectorAll("section[id]")].map(s => s.id)].map(id => id + "|");
  for (const k of [...MO.seen]) if (pre.some(p => k.startsWith(p))) MO.seen.delete(k);
}
// where a tab or view isn't rendered again, play what is already there
function moReplay(scope, wait) {
  if (moOff() || !scope) return;
  moForget(scope);
  clearTimeout(MO.timers.get(scope));
  MO.timers.set(scope, setTimeout(() => {
    scope.querySelectorAll(".card").forEach(c => { if (!MO.seen.has(moKey(c))) MO.queue.add(c); });
    moFlush();
  }, wait));
}

/* ---------- finding new panels ---------- */
const MO_SKIP = "#tower, #bigmap, .m3d, header, nav, #drawer, #cmdk, #celeb, #tip, .toast, #scrubbar";
new MutationObserver(list => {
  if (moOff()) return;
  for (const m of list) for (const n of m.addedNodes) {
    if (n.nodeType !== 1 || n.closest(MO_SKIP)) continue;
    const scope = moScope(n);
    clearTimeout(MO.timers.get(scope));         // the view rendered itself: no need to replay the old content
    if (n.classList.contains("card")) MO.queue.add(n);
    else if (n.querySelector) {
      const inner = n.querySelectorAll(".card");
      if (inner.length) inner.forEach(c => MO.queue.add(c));
      else { const c = n.closest(".card"); if (c) MO.queue.add(c); }   // new content inside a panel
    }
  }
  if (MO.queue.size && !MO.frame) MO.frame = requestAnimationFrame(moFlush);
}).observe(document.body, {childList: true, subtree: true});

function moFlush() {
  MO.frame = 0;
  const cards = [...MO.queue].filter(c => document.contains(c) && !c.closest(MO_SKIP) && !c.closest(".hidden"));
  MO.queue.clear();
  let i = 0;
  for (const card of cards) {
    if (card.parentElement && card.parentElement.closest(".card") && cards.includes(card.parentElement.closest(".card"))) continue;
    const key = moKey(card);
    if (MO.seen.has(key)) continue;
    MO.seen.add(key);
    const r = card.getBoundingClientRect();
    if (r.top > innerHeight + 40) moDefer(card);        // further down: when it is scrolled to
    else moPlay(card, i++);
  }
}

/* ---------- further down the page: wait until it comes into view ---------- */
function moDefer(card) {
  if (!window.IntersectionObserver) return moPlay(card, 0);
  if (!MO.io) MO.io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    MO.io.unobserve(e.target);
    e.target.classList.remove("mo-wait");
    moPlay(e.target, 0);
  }), {rootMargin: "0px 0px -8% 0px"});
  card.classList.add("mo-wait");
  MO.io.observe(card);
}

/* ---------- one panel ---------- */
function moPlay(card, order) {
  if (moOff()) return;
  const d0 = Math.min(order, 6) * 55;
  // the panel rises in, unless a stylesheet animation already does that
  if (!card.getAnimations().some(a => a instanceof CSSAnimation && a.playState === "running"))
    card.animate([{opacity: 0, transform: "translateY(14px) scale(.995)"}, {opacity: 1, transform: "none"}],
                 {duration: 520, delay: d0, easing: MO.EASE, fill: "backwards"});
  const t0 = d0 + 140;
  card.querySelectorAll("svg").forEach(svg => moChart(svg, t0));
  moBars(card, t0);
  moRows(card, t0);
  moCount(card, t0);
}

/* ---------- charts (inline SVG) ---------- */
function moChart(svg, t0) {
  if (svg.classList.contains("ico") || svg.closest(".ico, .trk") || svg.classList.contains("trk")) return;
  const box = svg.getBoundingClientRect();
  if (box.width < 140 || box.height < 50) return;                  // icons, sparks, chips
  const els = svg.querySelectorAll("*");
  if (els.length > 2500) { svg.animate([{opacity: 0}, {opacity: 1}], {duration: 500, delay: t0, fill: "backwards"}); return; }
  const vb = svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width ? svg.viewBox.baseVal : {x: 0, width: box.width, height: box.height};
  const xPos = x => Math.max(0, Math.min(1, ((x || 0) - (vb.x || 0)) / (vb.width || 1)));      // 0 left .. 1 right
  const num = (el, a) => { const v = parseFloat(el.getAttribute(a)); return isFinite(v) ? v : null; };

  // the furniture first: grid, axes and labels fade in
  svg.querySelectorAll("line, text, g > title").forEach(el => {
    if (el.tagName === "title") return;
    el.animate([{opacity: 0}, {opacity: 1}], {duration: 420, delay: t0 + (el.tagName === "text" ? 260 : 0), fill: "backwards"});
  });

  // bars grow from their common edge (the baseline), left to right
  const rects = [...svg.querySelectorAll("rect")].filter(r => !r.hasAttribute("transform") && !r.closest("clipPath, mask, defs, pattern"));
  const geo = rects.map(r => ({r, x: num(r, "x"), y: num(r, "y"), w: num(r, "width"), h: num(r, "height")}))
    .filter(g => g.x != null && g.y != null && g.w != null && g.h != null && g.w > 0 && g.h > 0);
  const mode = arr => { const c = {}; let best = null, n = 0; arr.forEach(v => { const k = Math.round(v); c[k] = (c[k] || 0) + 1; if (c[k] > n) { n = c[k]; best = k; } }); return [best, n]; };
  const vert = geo.filter(g => g.h > g.w), horiz = geo.filter(g => g.w >= g.h);
  const [bottom, nb] = mode(vert.map(g => g.y + g.h)), [left, nl] = mode(horiz.map(g => g.x));
  let k = 0;
  for (const g of geo) {
    if (k > 400) break;
    const big = g.w > .85 * (vb.width || box.width) || g.h > .85 * (vb.height || box.height);
    let origin = null, axis = null;
    if (!big && g.h > g.w && nb >= 2) {
      if (Math.abs(g.y + g.h - bottom) < 1.5) { origin = "center bottom"; axis = "Y"; }
      else if (Math.abs(g.y - bottom) < 1.5) { origin = "center top"; axis = "Y"; }
    } else if (!big && g.w >= g.h && nl >= 2) {
      if (Math.abs(g.x - left) < 1.5) { origin = "left center"; axis = "X"; }
      else if (Math.abs(g.x + g.w - left) < 1.5) { origin = "right center"; axis = "X"; }
    }
    const delay = t0 + 80 + xPos(g.x) * (axis === "Y" ? 420 : 0) + (axis === "X" ? Math.min(k, 30) * 14 : 0);
    if (!axis) { g.r.animate([{opacity: 0}, {opacity: 1}], {duration: 500, delay: t0, fill: "backwards"}); continue; }
    moTransform(g.r, origin, [{transform: `scale${axis}(0)`}, {transform: `scale${axis}(1)`}], {duration: 640, delay, easing: MO.EASE});
    k++;
  }

  // lines draw themselves, left to right; filled shapes (areas, bands) fade and lift
  const paths = [...svg.querySelectorAll("polyline, path")].filter(p => !p.closest("clipPath, mask, defs, marker, pattern, .ico"));
  paths.slice(0, 80).forEach((p, i) => {
    const cs = getComputedStyle(p);
    const stroked = cs.stroke && cs.stroke !== "none", filled = cs.fill && cs.fill !== "none" && parseFloat(cs.fillOpacity || 1) > 0;
    const delay = t0 + 60 + Math.min(i, 8) * 20;
    if (stroked && !filled && p.getTotalLength) {
      let len = 0;
      try { len = p.getTotalLength(); } catch (e) { len = 0; }
      if (len > 4) {
        const dashed = cs.strokeDasharray && cs.strokeDasharray !== "none";
        if (dashed) { p.animate([{opacity: 0}, {opacity: 1}], {duration: 600, delay: delay + 700, fill: "backwards"}); return; }   // reference lines follow the data
        const dur = len < 300 ? 600 : 1100;         // long series at one pace, so the dots on them keep up
        p.style.strokeDasharray = `${len} ${len}`;
        const a = p.animate([{strokeDashoffset: len}, {strokeDashoffset: 0}], {duration: dur, delay, easing: "cubic-bezier(.33,.15,.3,1)", fill: "backwards"});
        const done = () => { p.style.strokeDasharray = ""; };
        a.onfinish = done; a.oncancel = done;
        return;
      }
    }
    p.animate([{opacity: 0}, {opacity: 1}], {duration: 600, delay: delay + 120, fill: "backwards"});
  });

  // dots pop in, left to right
  const dots = [...svg.querySelectorAll("circle")].filter(c => !c.hasAttribute("transform") && !c.closest("defs, .focusring"));
  if (dots.length > 500) dots.forEach(c => c.animate([{opacity: 0}, {opacity: 1}], {duration: 500, delay: t0 + 200, fill: "backwards"}));
  else dots.forEach(c => moTransform(c, "center", [{transform: "scale(0)", opacity: 0}, {transform: "scale(1)", opacity: 1}],
                                       {duration: 420, delay: t0 + 120 + xPos(num(c, "cx")) * 1000, easing: MO.SPRING}));
}
// a transform on an SVG element needs its own box as the origin; put things back once done
function moTransform(el, origin, frames, opt) {
  const s = el.style, was = [s.transformBox, s.transformOrigin];
  s.transformBox = "fill-box"; s.transformOrigin = origin;
  const a = el.animate(frames, Object.assign({fill: "backwards"}, opt));
  const done = () => { s.transformBox = was[0]; s.transformOrigin = was[1]; };
  a.onfinish = done; a.oncancel = done;
}

/* ---------- bars drawn with HTML: an empty element sized as a share of its track ---------- */
function moBars(card, t0) {
  const bars = [...card.querySelectorAll("[style*='width']")].filter(el => {
    if (el.children.length || el.textContent.trim() || el.closest("svg, #tower") || el.style.transform) return false;
    const w = el.style.width, h = el.offsetHeight;
    return w && w.endsWith("%") && h >= 2 && h <= 30;
  }).slice(0, 200);
  // the segments of a stacked bar grow together, as one bar
  const done = new Set(), grow = [];
  for (const el of bars) {
    const p = el.parentElement, sib = p ? [...p.children] : [];
    if (sib.length > 1 && sib.every(c => bars.includes(c)) && !p.textContent.trim()) { if (!done.has(p)) { done.add(p); grow.push(p); } }
    else grow.push(el);
  }
  grow.slice(0, 160).forEach((el, k) => {
    const s = el.style, was = s.transformOrigin;
    s.transformOrigin = "left center";
    const a = el.animate([{transform: "scaleX(0)"}, {transform: "scaleX(1)"}],
                         {duration: 700, delay: t0 + 60 + Math.min(k, 40) * 12, easing: MO.EASE, fill: "backwards"});
    a.onfinish = a.oncancel = () => { s.transformOrigin = was; };
  });
}

/* ---------- table rows: a quick cascade down the first screenful ---------- */
function moRows(card, t0) {
  card.querySelectorAll("tbody").forEach(tb => {
    if (tb.closest("#tower")) return;
    const rows = [...tb.rows];
    if (rows.length > 300) return;
    rows.forEach((tr, i) =>            // the rest of a long table arrives with the 24th row
      tr.animate([{opacity: 0, transform: "translateY(6px)"}, {opacity: 1, transform: "none"}],
                 {duration: 380, delay: t0 + 40 + Math.min(i, 24) * 22, easing: MO.EASE, fill: "backwards"}));
  });
}

/* ---------- headline numbers count up ---------- */
const MO_NUM = /^([+\-−]?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?(?:%|s|pts|km\/h|laps?|places?))?$/;
function moCount(card, t0) {
  const els = [...card.querySelectorAll(".stat b, .srlist b, .facts b, .big, .hero b, .pad div > b:first-child")].slice(0, 260);
  for (const el of els) {
    if (el.children.length || el.closest("#tower, [data-countdown], [data-since], svg") || el.hasAttribute("data-countdown") || el.hasAttribute("data-since")) continue;
    if (el.tagName === "TD" && el.parentElement && el.parentElement.sectionRowIndex > 24) continue;
    const text = el.textContent.trim(), m = text.match(MO_NUM);
    if (!m) continue;
    const intPart = m[2].replace(/,/g, ""), dec = m[3] ? m[3].length - 1 : 0, target = parseFloat(intPart + (m[3] || ""));
    if (!dec && !m[4] && /^(19[5-9]\d|20\d\d)$/.test(intPart)) continue;        // a year, not a quantity
    if (!target) continue;
    const commas = m[2].includes(","), sign = m[1], unit = m[4] || "";
    const fmt = v => {
      let s = v.toFixed(dec);
      if (commas) { const [a, b] = s.split("."); s = a.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (b ? "." + b : ""); }
      return sign + s + unit;
    };
    const dur = 900, start = performance.now() + t0;
    el.textContent = fmt(0);
    const step = now => {
      if (!document.contains(el) || el.textContent !== el._moLast && el._moLast != null) return;     // re-rendered meanwhile
      const p = Math.max(0, Math.min(1, (now - start) / dur)), e = 1 - Math.pow(1 - p, 3);
      el.textContent = el._moLast = p >= 1 ? text : fmt(target * e);
      if (p < 1) requestAnimationFrame(step); else el._moLast = null;
    };
    el._moLast = fmt(0);
    requestAnimationFrame(step);
  }
}

/* ---------- when a tab or view opens, or a dropdown picks something else, play its panels again ---------- */
const openTabMotion = openTab;
openTab = function (t) {
  const from = activeTab;
  openTabMotion(t);
  if (activeTab !== from) moReplay($("tab-" + activeTab), 260);
};
const xShowMotion = xShow;
xShow = function (view) {
  xShowMotion(view);
  moReplay($("x-" + XP.view), 450);
};
document.addEventListener("change", e => {
  const t = e.target;
  if (!t || !t.matches || !t.matches("select, input[type=radio]") || t.closest("#drawer, #cmdk, header, nav")) return;
  moForget(moScope(t));
}, true);

/* ---------- the first paint: the header and tab bar settle in ---------- */
if (!moOff()) {
  document.querySelectorAll("nav > button").forEach((b, i) =>
    b.animate([{opacity: 0, transform: "translateY(-6px)"}, {opacity: 1, transform: "none"}],
              {duration: 420, delay: 80 + i * 28, easing: MO.EASE, fill: "backwards"}));
  const brand = document.querySelector("header .brand");
  if (brand) brand.animate([{opacity: 0, transform: "translateX(-14px)", filter: "blur(4px)"}, {opacity: 1, transform: "none", filter: "none"}],
                           {duration: 600, easing: MO.EASE, fill: "backwards"});
}
