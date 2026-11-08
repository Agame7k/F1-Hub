/* F1 Hub dashboard - race-winner and champion celebrations.
   A classic script: it shares the page's global scope with the others, loaded in the order dashboard.html
   lists them (functions in a later file aren't defined yet while an earlier one runs). */
/* ---------- celebrations: race winner, and a bigger show when the World Championship is decided ---------- */
// Driven by the snapshot being shown, so with a delay or in a replay it happens when *you* see the flag - never early.
const celeb = {timer: null, raf: null, parts: [], last: null, armed: {}};
function onceKey(kind) { return `celebrated.${D.session_key}.${kind}`; }
function checkCelebrations(d) {
  if (!d || !d.rows || !d.rows.length || d.mode === undefined) return;
  const isRace = /race|sprint/i.test((d.session || "") + " " + (d.session_type || "")) || d.scheduled_laps;
  const finished = d.status === "CHEQUERED" || (d.rc && d.rc.red_flag && d.rc.red_flag.state === "abandoned");
  const ev = finished && isRace ? buildEvent(d) : null;
  $("celebbtn").classList.toggle("on", !!ev);
  celeb.last = ev;
  if (!ev) { celeb.armed[d.session_key] = true; return; }      // seen it unfinished -> a later finish is "live"
  const replay = d.mode === "replay";
  // live/delayed: once per session (survives reloads). replay: once per replay run, only if we watched it happen
  const key = onceKey(ev.champion ? "champ" : "win") + (replay ? ".replay" : "");
  if (replay ? celeb.armed[d.session_key] && !celeb.armed[key] : !store.get(key, false) && celeb.armed[d.session_key]) {
    celeb.armed[key] = true;
    if (!replay) store.set(key, true);
    if (document.hidden) celeb.pending = ev;     // tab in the background: save it for when you come back
    else showCelebration(ev);
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && celeb.pending) { const ev = celeb.pending; celeb.pending = null; showCelebration(ev); }
});
function buildEvent(d) {
  const top = d.rows.filter(r => !r.out && r.pos).slice(0, 3);
  if (!top.length) return null;
  const t = (d.title || []).find(t => t.champion && t.champion_info);
  const abandoned = d.rc && d.rc.red_flag && d.rc.red_flag.state === "abandoned";
  const tt = abandoned ? ((d.title || []).find(t => /stopped|reduced|red/i.test(t.label) && t.champion) || t) : t;
  const champion = tt && tt.champion && !tt.decided_before ? tt : null;
  return {podium: top, winner: top[0], champion, meeting: d.meeting, session: d.session};
}
function replayCelebration() { if (celeb.last) showCelebration(celeb.last); }
function closeCelebration() {
  $("celeb").className = ""; clearTimeout(celeb.timer); cancelAnimationFrame(celeb.raf); celeb.parts = [];
}
function showCelebration(ev) {
  closeCelebration();
  const el = $("celeb"), st = $("cstage");
  el.className = "on";
  const w = ev.winner;
  st.innerHTML = `<div class="chq"></div>
    ${w.headshot ? `<img class="c-head" style="--tc:${esc(w.colour)}" src="${esc(w.headshot)}" onerror="this.style.display='none'">` : ""}
    <div class="c-kicker">${esc((ev.meeting || "").toUpperCase())} · ${esc((ev.session || "").toUpperCase())}</div>
    <div class="c-win" style="color:${esc(w.colour)}">${esc(w.code)} WINS</div>
    <div class="c-name">${esc(w.name || w.code)}</div><div class="c-team">${esc(w.team || "")}</div>
    <div class="podium">${[1, 0, 2].map((i, k) => { const r = ev.podium[i]; if (!r) return ""; const h = [150, 200, 110][k] * (i === 0 ? 1 : 1);
      return `<div class="step"><div class="lbl" style="animation-delay:${3 + k * .25}s;color:${esc(r.colour)}">${esc(r.code)}</div>
        <div class="blk" style="height:${[120, 170, 90][k]}px;background:linear-gradient(${esc(r.colour)},${esc(r.colour)}66);animation-delay:${2.8 + k * .25}s">${i + 1}</div></div>`; }).join("")}</div>`;
  const colours = [w.colour, "#ffffff", ...(ev.podium.map(r => r.colour))];
  fx(colours, false);
  if (ev.champion) celeb.timer = setTimeout(() => showChampion(ev), 7000);
  else celeb.timer = setTimeout(closeCelebration, 14000);
}
function showChampion(ev) {
  const t = ev.champion, c = t.champion_info, el = $("celeb"), st = $("cstage");
  el.className = "on gold";
  const tc = c.colour || "#ffd34d";
  const words = "WORLD CHAMPION";
  const letters = [...words].map((ch, i) => `<span style="animation-delay:${2.2 + i * .06}s, ${3.4 + i * .06}s">${ch === " " ? "&nbsp;" : ch}</span>`).join("");
  const stats = [["POINTS", c.points], ["WINS", c.wins], ["PODIUMS", c.podiums], ["POLES", c.poles], ["LEAD", c.margin]]
    .filter(x => x[1] != null);
  const hi = c.headshot ? c.headshot.replace("/1col/", "/2col/") : null;     // bigger photo when F1's CDN has one
  // the season, race by race (from the championship data the dashboard already has)
  const me = S && S.drivers ? S.drivers.find(e => e.code === t.champion_code) : null;
  const fins = me ? me.finishes || [] : [];
  const parts = t.champion.split(" "), first = parts.slice(0, -1).join(" "), last = parts.slice(-1)[0];
  st.innerHTML = `<div class="wc-rays" style="--tc:${esc(tc)}"></div>
    <div class="wc-num" style="-webkit-text-stroke-color:${esc(tc)}">${esc(c.number ?? "")}</div>
    <div class="wc-flash"></div>
    <div class="wc-kicker">${t.year || ""} FIA FORMULA ONE</div>
    <div class="wc-title">${letters}</div>
    <div class="wc-hero" style="--tc:${esc(tc)}">
      <div class="wc-ring"></div>
      ${c.headshot ? `<img src="${esc(hi)}" onerror="if (this.src !== '${esc(c.headshot)}') this.src='${esc(c.headshot)}'; else this.remove()">` : `<div class="wc-initials">${esc(t.champion_code || "")}</div>`}
      <div class="wc-cup">🏆</div>
    </div>
    <div class="wc-name"><small>${esc(first)}</small>${esc(last)}</div>
    <div class="wc-sub"><span class="stripe" style="background:${esc(tc)};display:inline-block;width:4px;height:14px;vertical-align:-2px;margin-right:6px"></span>${esc(c.team || "")} · crowned at the ${esc(ev.meeting || "")} with ${t.rounds_left} round${t.rounds_left === 1 ? "" : "s"} to spare</div>
    <div class="wc-stats">${stats.map((x, i) => `<div style="animation-delay:${4.6 + i * .2}s"><b data-count="${x[1]}">0</b><span>${x[0]}</span></div>`).join("")}</div>
    ${fins.length ? `<div class="wc-season">${fins.map((f, i) => `<i class="${!f ? "dnf" : f === 1 ? "g" : f <= 3 ? "s" : ""}" style="animation-delay:${6 + i * .08}s">${f || "×"}</i>`).join("")}<i class="g now" style="animation-delay:${6 + fins.length * .08}s">🏆</i></div>` : ""}`;
  setTimeout(() => st.querySelectorAll("[data-count]").forEach(b => countUp(b, +b.dataset.count, 1800)), 4700);
  fx(["#ffd34d", "#fff3b0", "#ffffff", tc, "#ffb800"], true);
  celeb.timer = setTimeout(closeCelebration, 40000);
}
function countUp(el, to, ms) {
  const t0 = performance.now(), dec = to % 1 ? 1 : 0;
  const step = now => { const f = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - f, 3);
    el.textContent = (to * e).toFixed(dec); if (f < 1) requestAnimationFrame(step); };
  requestAnimationFrame(step);
}
// confetti (always) + fireworks (champion), one canvas
function fx(colours, fireworks) {
  const cv = $("fx"), ctx = cv.getContext("2d"), dpr = window.devicePixelRatio || 1;
  const resize = () => { cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; };
  resize();
  const W = () => cv.width, H = () => cv.height, rnd = (a, b) => a + Math.random() * (b - a);
  const parts = celeb.parts = [];
  const confetti = n => { for (let i = 0; i < n; i++) parts.push({k: "c", x: rnd(0, W()), y: rnd(-H() * .6, -10), vx: rnd(-1, 1) * dpr, vy: rnd(1.5, 4) * dpr,
    r: rnd(0, 6.28), vr: rnd(-.2, .2), w: rnd(6, 12) * dpr, h: rnd(3, 7) * dpr, col: colours[i % colours.length], life: 1e9}); };
  const burst = () => { const x = rnd(.15, .85) * W(), y = rnd(.12, .45) * H(), col = colours[Math.floor(rnd(0, colours.length))], n = 90;
    for (let i = 0; i < n; i++) { const a = i / n * 6.283, sp = rnd(2, 7) * dpr;
      parts.push({k: "f", x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, col, life: rnd(55, 90), age: 0}); } };
  confetti(fireworks ? 260 : 180);
  let frame = 0;
  const tick = () => {
    frame++;
    if (fireworks && frame % 28 === 0 && frame < 1500) burst();
    if (fireworks && frame % 240 === 0 && frame < 1500) confetti(80);
    ctx.clearRect(0, 0, W(), H());
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      if (p.k === "c") {
        p.x += p.vx + Math.sin((frame + i) / 20) * .6 * dpr; p.y += p.vy; p.r += p.vr;
        if (p.y > H() + 20) { parts.splice(i, 1); continue; }
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.fillStyle = p.col;
        ctx.fillRect(-p.w / 2, -p.h / 2 * Math.abs(Math.cos(p.r * 2)), p.w, p.h * Math.abs(Math.cos(p.r * 2)) + 1); ctx.restore();
      } else {
        p.age++; p.vx *= .97; p.vy = p.vy * .97 + .06 * dpr; p.x += p.vx; p.y += p.vy;
        if (p.age > p.life) { parts.splice(i, 1); continue; }
        ctx.globalAlpha = 1 - p.age / p.life; ctx.fillStyle = p.col;
        ctx.beginPath(); ctx.arc(p.x, p.y, 2.2 * dpr, 0, 6.283); ctx.fill(); ctx.globalAlpha = 1;
      }
    }
    if (parts.length || (fireworks && frame < 1500)) celeb.raf = requestAnimationFrame(tick);
  };
  celeb.raf = requestAnimationFrame(tick);
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeCelebration(); });
