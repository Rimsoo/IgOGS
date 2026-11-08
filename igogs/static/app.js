"use strict";

const LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ";
const colorName = (c) => t(c === "B" ? "Noir" : "Blanc");
const $ = (id) => document.getElementById(id);

let S = null;          // état complet (/api/state, puis mis à jour par les événements)
let placing = null;    // coins en cours de placement (null = pas en mode placement)
let dragging = -1;     // index du coin déplacé
let lastSpoken = "";
let langSynced = false;

// ------------------------------------------------------------------ utilitaires
async function api(path, body = {}) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-IgOGS": "1" },
    body: JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

async function act(path, body, okMsg) {
  try {
    const r = await api(path, body);
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
  }
}

let toastTimer = null;
function toast(msg, error = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("hidden", false);
  el.style.background = error ? "var(--bad)" : "";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), error ? 6000 : 3000);
}

const gtp = (x, y, n) => `${LETTERS[x]}${n - y}`;
function fromGtp(s, n) {
  if (!s || s === "pass") return null;
  return [LETTERS.indexOf(s[0]), n - parseInt(s.slice(1), 10)];
}
const list = (items) => items.join(", ");

// ------------------------------------------------------------------ événements serveur
function connect() {
  const es = new EventSource("/events");
  es.addEventListener("state", (e) => {
    const next = JSON.parse(e.data);
    if (S && S.boot && next.boot !== S.boot) { location.reload(); return; }  // IgOGS a redémarré
    S = next;
    // Langue choisie ailleurs (autre onglet, config) : on suit le serveur.
    if (S.lang && S.lang !== LANG && !langSynced) setLang(S.lang);
    langSynced = true;
    $("stopped").classList.add("hidden");
    renderAll();
  });
  es.addEventListener("shutdown", (e) => {
    const d = JSON.parse(e.data);
    if (d.restart) { toast(t("Redémarrage…")); return; }  // la page se recharge à la reconnexion
    es.close();
    $("stopped").classList.remove("hidden");
  });
  es.addEventListener("live", (e) => { if (S) { S.live = JSON.parse(e.data); drawOverlay(); renderStatus(); } });
  for (const k of ["observed", "game"]) {
    es.addEventListener(k, (e) => { if (S) { S.board = JSON.parse(e.data); renderBoard(); renderStatus(); } });
  }
  es.addEventListener("calibration", (e) => { if (S) { S.calibration = JSON.parse(e.data); renderCamera(); } });
  es.addEventListener("ogs", (e) => { if (S) { S.ogs = JSON.parse(e.data); renderOgs(); renderBoard(); } });
  es.addEventListener("ogs-move", (e) => {
    const m = JSON.parse(e.data);
    if (!m.mine) speak(m.color, m.move);
  });
  es.addEventListener("move", (e) => {
    const m = JSON.parse(e.data);
    if (m.applied && m.owner === "local") toast(`${colorName(m.color)} ${m.move}`);
  });
  es.addEventListener("undo", (e) => { const n = JSON.parse(e.data).count; toast(n > 1 ? t("{n} coups annulés", { n }) : t("Coup annulé")); });
  es.onerror = () => { $("status-line").innerHTML = `<span class="pill bad">${t("IgOGS ne répond plus…")}</span>`; };
}

function speak(color, move) {
  if (!$("speak").checked || !window.speechSynthesis) return;
  const key = color + move;
  if (key === lastSpoken) return;
  lastSpoken = key;
  const text = move === "pass" ? t("{color} passe", { color: colorName(color) }) : `${colorName(color)}, ${move[0]} ${move.slice(1)}`;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = LANG === "en" ? "en-US" : "fr-FR";
  speechSynthesis.speak(u);
}

// ------------------------------------------------------------------ rendu général
function renderAll() {
  $("version").textContent = "v" + S.version;
  renderCamera();
  renderBoard();
  renderOgs();
  renderStatus();
}

function renderStatus() {
  if (!S) return;
  const pills = [];
  const cam = S.camera;
  if (!cam.id) pills.push([t("Pas de caméra"), "warn"]);
  else if (cam.error) pills.push([cam.error, "bad"]);
  else pills.push([t(cam.sim ? "Simulateur · {fps} img/s" : "Caméra · {fps} img/s", { fps: cam.fps }), "ok"]);
  if (cam.id && !S.calibration.ready) pills.push([t("Non calibré"), "warn"]);
  else if (cam.id) pills.push(S.live.busy ? [t("Lecture en cours…"), "warn"] : [t("Goban lu"), "ok"]);
  if (S.ogs.logged_in) pills.push([S.ogs.connected ? t("OGS : {user}", { user: S.ogs.user?.username || "" }) : t("OGS déconnecté"), S.ogs.connected ? "ok" : "bad"]);
  $("status-line").innerHTML = pills.map(([t, c]) => `<span class="pill ${c}">${escapeHtml(t)}</span>`).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ------------------------------------------------------------------ caméra et calibration
function renderCamera() {
  const sel = $("camera-select");
  const opts = [`<option value="">${t("— Caméra —")}</option>`].concat(
    S.camera.list.map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name)}</option>`));
  if (sel.dataset.sig !== opts.join("")) { sel.innerHTML = opts.join(""); sel.dataset.sig = opts.join(""); }
  sel.value = S.camera.id;
  $("size-select").value = String(S.calibration.size);
  $("view-select").value = String(S.calibration.view_rotation || 0);
  $("btn-mirror").classList.toggle("on", !!S.calibration.mirror);
  const source = S.camera.id ? `${S.camera.id}|${$("show-warped").checked}` : "";
  if (source !== camSource) { camSource = source; setFrame(null); }
  $("cam-empty").classList.toggle("hidden", !!S.camera.id);
  const help = placing
    ? t("Cliquez le coin {k}/4 de la grille (les lignes extérieures), dans n'importe quel ordre.", { k: placing.length + 1 })
    : !S.calibration.ready
      ? t("Calibration : cliquez « Placer les 4 coins », puis les quatre coins de la grille sur l'image.")
      : t("Vérifiez que la grille recouvre les lignes et que les coordonnées (en jaune) sont lues depuis votre place : A1 en bas à gauche. Sinon « Tourner » / « Miroir », ou « Orienter automatiquement » pendant une partie. Les coins se déplacent à la souris.");
  $("calib-help").textContent = help;
  $("btn-corners").textContent = t(placing ? "Annuler" : "Placer les 4 coins");
  $("sim-help").classList.toggle("hidden", !S.camera.sim);
  $("board").classList.toggle("sim", !!S.camera.sim);
  drawOverlay();
}

// Image caméra : récupérée une à une (un flux MJPEG dans <img> ne se redessine pas dans un
// canevas sous Firefox : on n'y verrait que la première image).
let camFrame = null, camSource = "", camFetching = false;
function setFrame(bmp) {
  if (camFrame && camFrame.close) camFrame.close();
  camFrame = bmp;
}
async function pullFrame() {
  if (!S || !S.camera.id || camFetching || document.hidden) return;
  camFetching = true;
  const source = camSource;
  try {
    const url = ($("show-warped").checked ? "/warped.jpg" : "/frame.jpg") + "?t=" + Date.now();
    const r = await fetch(url, { cache: "no-store" });
    if (r.ok) {
      const bmp = await createImageBitmap(await r.blob());
      if (source === camSource) { setFrame(bmp); drawOverlay(); } else bmp.close();
    }
  } catch (e) { /* image manquée : la suivante arrive dans un instant */ }
  finally { camFetching = false; }
}

// Passage pixels caméra ↔ canevas : image centrée, tournée selon la place du joueur
// (« Ma place »), à l'échelle. La vue redressée est déjà dans le sens du joueur.
function frameRect(canvas) {
  const warped = $("show-warped").checked;
  const n = S.calibration.size;
  const f = warped ? [(n + 1) * 32, (n + 1) * 32] : (S.calibration.frame || [1280, 720]);
  const rot = warped ? 0 : (S.calibration.view_rotation || 0);
  const swap = rot % 180 !== 0;
  const s = Math.min(canvas.width / (swap ? f[1] : f[0]), canvas.height / (swap ? f[0] : f[1]));
  const M = new DOMMatrix().translate(canvas.width / 2, canvas.height / 2).rotate(rot).scale(s).translate(-f[0] / 2, -f[1] / 2);
  const inv = M.inverse();
  return {
    s, f, M, warped,
    P: ([x, y]) => { const p = M.transformPoint({ x, y }); return [p.x, p.y]; },
    inv: (x, y) => { const p = inv.transformPoint({ x, y }); return [p.x, p.y]; },
  };
}

function drawOverlay() {
  const cv = $("cam-overlay");
  const rect = cv.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(rect.width * dpr)) { cv.width = Math.round(rect.width * dpr); cv.height = Math.round(rect.height * dpr); }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!S || !S.camera.id) return;
  const R = frameRect(cv);
  const P = R.P;
  if (camFrame) {
    ctx.setTransform(R.M);
    ctx.drawImage(camFrame, 0, 0, R.f[0], R.f[1]);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }
  const cal = S.calibration;
  const n = cal.size;

  let points;
  if (R.warped) {
    points = [];
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) points.push([(x + 1) * 32, (y + 1) * 32]);
  } else if (cal.overlay) {
    ctx.strokeStyle = "rgba(80, 200, 255, 0.75)";
    ctx.lineWidth = 1 * dpr;
    ctx.beginPath();
    for (const [a, b] of cal.overlay.lines) { ctx.moveTo(...P(a)); ctx.lineTo(...P(b)); }
    ctx.stroke();
    points = cal.overlay.points;
  }
  // Lecture en direct : pastille par intersection occupée ou douteuse.
  const grid = S.live && S.live.grid;
  if (points && grid && grid.length === points.length) {
    const r = Math.max(3, 4 * dpr);
    points.forEach((p, i) => {
      const c = grid[i];
      if (c === ".") return;
      const [x, y] = P(p);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, 2 * Math.PI);
      ctx.fillStyle = c === "B" ? "#000" : c === "W" ? "#fff" : "#e5604f";
      ctx.strokeStyle = c === "?" ? "#fff" : "#e09140";
      ctx.lineWidth = 1.5 * dpr;
      ctx.fill();
      ctx.stroke();
    });
  }
  // Coordonnées : lettres sous la ligne 1, numéros à gauche de la colonne A (vus du joueur).
  if (points && points.length === n * n && !placing) {
    const at = (x, y) => P(points[y * n + x]);
    const beyond = (a, b) => [a[0] + (a[0] - b[0]) * 0.85, a[1] + (a[1] - b[1]) * 0.85];
    ctx.font = `bold ${Math.max(9, 11 * dpr)}px system-ui`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3 * dpr;
    ctx.strokeStyle = "rgba(0,0,0,.85)";
    ctx.fillStyle = "#ffd27a";
    const label = (txt, [x, y]) => { ctx.strokeText(txt, x, y); ctx.fillText(txt, x, y); };
    for (let x = 0; x < n; x++) label(LETTERS[x], beyond(at(x, n - 1), at(x, n - 2)));
    for (let y = 0; y < n; y++) label(String(n - y), beyond(at(0, y), at(1, y)));
    ctx.textAlign = "start";
    ctx.textBaseline = "alphabetic";
  }
  if (R.warped) return;
  // Coins (calibrés ou en cours de placement).
  const corners = placing || cal.corners;
  // Coin i → coin du goban (avec « Miroir », gauche et droite sont échangés).
  const TL = `A${n}`, TR = `${LETTERS[n - 1]}${n}`, BR = `${LETTERS[n - 1]}1`, BL = "A1";
  const labels = cal.mirror ? [TR, TL, BL, BR] : [TL, TR, BR, BL];
  corners.forEach((c, i) => {
    const [x, y] = P(c);
    ctx.beginPath();
    ctx.arc(x, y, 7 * dpr, 0, 2 * Math.PI);
    ctx.fillStyle = labels[i] === TL && !placing ? "rgba(224, 145, 64, .9)" : "rgba(80, 200, 255, .9)";
    ctx.fill();
    if (!placing) {
      ctx.font = `bold ${13 * dpr}px system-ui`;
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = "#000";
      ctx.lineWidth = 3 * dpr;
      ctx.strokeText(labels[i], x + 10 * dpr, y - 10 * dpr);
      ctx.fillText(labels[i], x + 10 * dpr, y - 10 * dpr);
    }
  });
}

function eventToFrame(ev) {
  const cv = $("cam-overlay");
  const rect = cv.getBoundingClientRect();
  const dpr = cv.width / rect.width;
  return frameRect(cv).inv((ev.clientX - rect.left) * dpr, (ev.clientY - rect.top) * dpr);
}

function setupCalibrationMouse() {
  const cv = $("cam-overlay");
  cv.addEventListener("pointerdown", (ev) => {
    if (!S || !S.camera.id || $("show-warped").checked) return;
    const p = eventToFrame(ev);
    if (placing) {
      placing.push(p);
      if (placing.length === 4) {
        const corners = placing;
        placing = null;
        act("/api/calibration", { corners }).then(() => act("/api/calibration/refine", {}));
      }
      renderCamera();
      return;
    }
    const R = frameRect(cv);
    const near = S.calibration.corners.findIndex(([x, y]) => Math.hypot(x - p[0], y - p[1]) * R.s < 18 * (window.devicePixelRatio || 1));
    if (near >= 0) { dragging = near; cv.setPointerCapture(ev.pointerId); }
  });
  cv.addEventListener("pointermove", (ev) => {
    if (dragging < 0) return;
    S.calibration.corners[dragging] = eventToFrame(ev);
    S.calibration.overlay = null;
    drawOverlay();
  });
  cv.addEventListener("pointerup", () => {
    if (dragging < 0) return;
    dragging = -1;
    act("/api/calibration", { corners: S.calibration.corners, keep_order: true });
  });
  window.addEventListener("resize", drawOverlay);
}

// ------------------------------------------------------------------ goban
function renderBoard() {
  if (!S) return;
  const b = S.board;
  const n = b.size;
  const svg = $("board");
  const m = 5, step = (100 - 2 * m) / (n - 1);
  const X = (i) => m + i * step;
  const parts = [`<rect x="0" y="0" width="100" height="100" rx="1.5" fill="var(--wood)"/>`];
  for (let i = 0; i < n; i++) {
    parts.push(`<line x1="${X(0)}" y1="${X(i)}" x2="${X(n - 1)}" y2="${X(i)}" stroke="var(--grid)" stroke-width="0.18"/>`);
    parts.push(`<line x1="${X(i)}" y1="${X(0)}" x2="${X(i)}" y2="${X(n - 1)}" stroke="var(--grid)" stroke-width="0.18"/>`);
    const fs = Math.min(2.2, step * 0.45);
    parts.push(`<text x="${X(i)}" y="${m * 0.55}" font-size="${fs}" text-anchor="middle" fill="var(--grid)" opacity=".7">${LETTERS[i]}</text>`);
    parts.push(`<text x="${m * 0.45}" y="${X(i) + fs * 0.35}" font-size="${fs}" text-anchor="middle" fill="var(--grid)" opacity=".7">${n - i}</text>`);
  }
  for (const [x, y] of starPoints(n)) parts.push(`<circle cx="${X(x)}" cy="${X(y)}" r="${step * 0.1}" fill="var(--grid)"/>`);
  const r = step * 0.47;
  const stone = (x, y, c, extra = "") =>
    `<circle cx="${X(x)}" cy="${X(y)}" r="${r}" fill="${c === "B" ? "#151515" : "#f6f6f4"}" stroke="${c === "B" ? "#000" : "#888"}" stroke-width="0.12" ${extra}/>`;
  const missing = new Set(b.sync.missing.map((m) => m[0]));
  const showNumbers = $("numbers").checked;
  const numbers = {};  // intersection → numéro du dernier coup joué là (pierre encore présente)
  b.moves.forEach(([, mv], i) => { if (mv !== "pass") numbers[mv] = i + 1; });
  const lastNumber = b.moves.length;
  for (let i = 0; i < n * n; i++) {
    const c = b.expected[i], x = i % n, y = Math.floor(i / n);
    if (c !== "B" && c !== "W") continue;
    const at = gtp(x, y, n);
    parts.push(stone(x, y, c, missing.has(at) ? `opacity=".4"` : ""));
    const k = numbers[at];
    if (showNumbers && k) {
      const fs = step * (k >= 100 ? 0.36 : 0.46);
      const fill = k === lastNumber ? "#e5604f" : c === "B" ? "#f4f4f4" : "#151515";
      parts.push(`<text x="${X(x)}" y="${X(y)}" font-size="${fs}" font-weight="${k === lastNumber ? 800 : 600}" text-anchor="middle" dominant-baseline="central" fill="${fill}">${k}</text>`);
    }
  }
  // Dernier coup
  const last = [...b.moves].reverse().find((mv) => mv[1] !== "pass");
  if (!showNumbers && last && b.moves.length && b.moves[b.moves.length - 1][1] !== "pass") {
    const [x, y] = fromGtp(last[1], n);
    parts.push(`<circle cx="${X(x)}" cy="${X(y)}" r="${r * 0.45}" fill="none" stroke="${last[0] === "B" ? "#fff" : "#000"}" stroke-width="0.35"/>`);
  }
  // Coup proposé (en attente du serveur)
  for (const [c, mv] of b.pending || []) {
    const [x, y] = fromGtp(mv, n);
    parts.push(stone(x, y, c, `opacity=".55"`));
  }
  // À poser : cercle pointillé pulsant ; à retirer : croix rouge.
  for (const [mv, c] of b.sync.missing) {
    const [x, y] = fromGtp(mv, n);
    parts.push(`<circle class="pulse" cx="${X(x)}" cy="${X(y)}" r="${r * 0.92}" fill="none" stroke="${c === "B" ? "#000" : "#fff"}" stroke-width="0.6" stroke-dasharray="1 0.8"/>`);
  }
  for (const [mv, c] of b.sync.extra) {
    const [x, y] = fromGtp(mv, n);
    const d = r * 0.6;
    parts.push(stone(x, y, c, `opacity=".55"`));
    parts.push(`<path class="pulse" d="M${X(x) - d} ${X(y) - d}L${X(x) + d} ${X(y) + d}M${X(x) + d} ${X(y) - d}L${X(x) - d} ${X(y) + d}" stroke="#e5604f" stroke-width="0.7" stroke-linecap="round"/>`);
  }
  svg.innerHTML = parts.join("");
  svg.dataset.n = n;

  const ownerLabel = { local: t("Enregistrement local"), ogs: "OGS" }[b.owner] || b.owner;
  $("owner-badge").textContent = ownerLabel;
  $("apps-owner").textContent = ownerLabel;
  $("board-title").textContent = b.info.PB || b.info.PW ? `${b.info.PB || "?"} (●) – ${b.info.PW || "?"} (○)` : t("Partie");
  $("moves").innerHTML = b.moves.map(([c, mv]) => `<li>${c === "B" ? "●" : "○"} ${mv}</li>`).join("");
  const isLocal = b.owner === "local";
  for (const id of ["btn-new", "btn-undo", "btn-pass"]) $(id).disabled = !isLocal;
  $("btn-release").disabled = isLocal;
  renderInstruction();
}

function starPoints(n) {
  if (n < 7) return [];
  const e = n < 13 ? 2 : 3, f = n - 1 - e, mid = (n - 1) / 2;
  const pts = [[e, e], [f, e], [e, f], [f, f]];
  if (n % 2) { pts.push([mid, mid]); if (n >= 13) pts.push([mid, e], [mid, f], [e, mid], [f, mid]); }
  return pts;
}

function renderInstruction() {
  const el = $("instruction");
  let text = "", cls = "";
  const b = S.board, sync = b.sync;
  const tp = colorName(b.to_play);
  if (!S.camera.id) [text, cls] = [t("Choisissez une caméra pour commencer."), "warn"];
  else if (!S.calibration.ready) [text, cls] = [t("Calibrez la caméra : placez les 4 coins."), "warn"];
  else if (sync.status === "unknown") [text, cls] = [t("En attente d'une image stable du goban…"), "warn"];
  else if (sync.status === "pending") [text, cls] = [t("Envoi de {moves}…", { moves: list(b.pending.map((p) => p[1])) }), "warn"];
  else if (sync.status === "remove-captured") [text, cls] = [t("Retirez les pierres prises : {pts}", { pts: list(sync.extra.map((e) => e[0])) }), "warn"];
  else if (sync.status === "mismatch") {
    const parts = [];
    for (const c of ["B", "W"]) {
      const pts = sync.missing.filter((m) => m[1] === c).map((m) => m[0]);
      if (pts.length) parts.push(t("posez {color} en {pts}", { color: colorName(c), pts: list(pts) }));
    }
    if (sync.extra.length) parts.push(t("retirez {pts}", { pts: list(sync.extra.map((e) => e[0])) }));
    text = parts.join(" · ");
    text = text.charAt(0).toUpperCase() + text.slice(1);
    if (sync.rotated) text = t("Le goban semble tourné ou en miroir : cliquez « Orienter automatiquement »");
    cls = "bad";
  } else if (S.ogs.phase === "finished" && b.owner === "ogs") [text, cls] = [S.ogs.result ? t("Partie terminée : {result}", { result: S.ogs.result }) : t("Partie terminée"), "ok"];
  else if (sync.camera_turn) [text, cls] = [t(b.owner === "ogs" ? "À vous de jouer ({color})" : "{color} joue", { color: tp }), "ok"];
  else if (b.owner === "ogs") [text, cls] = [t("En attente du coup de l'adversaire ({color})", { color: tp }), ""];
  else [text, cls] = [t("{color} au trait", { color: tp }), ""];
  el.textContent = text;
  el.className = "instruction " + cls;
}

function setupBoardClicks() {
  $("board").addEventListener("click", (ev) => {
    if (!S || !S.camera.sim) return;
    const svg = $("board");
    const n = S.board.size;
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    const m = 5, step = (100 - 2 * m) / (n - 1);
    const x = Math.round((p.x - m) / step), y = Math.round((p.y - m) / step);
    if (x < 0 || y < 0 || x >= n || y >= n) return;
    // Ce que « voit » le simulateur : on part de la dernière lecture stable.
    const seen = S.board.observed || S.board.expected;
    const cur = seen[y * n + x];
    let color = null;
    if (cur === ".") color = ev.shiftKey ? (S.board.to_play === "B" ? "W" : "B") : S.board.to_play;
    act("/api/sim", { move: gtp(x, y, n), color });
  });
}

// ------------------------------------------------------------------ OGS
function renderOgs() {
  const o = S.ogs;
  $("ogs-login").classList.toggle("hidden", o.logged_in);
  $("ogs-session").classList.toggle("hidden", !o.logged_in);
  $("ogs-who").textContent = o.user ? t("Connecté : {user}", { user: o.user.username }) : t("Connexion à OGS…");
  $("ogs-error").textContent = o.error || "";
  const inGame = !!o.game_id;
  $("ogs-game").classList.toggle("hidden", !inGame);
  if (inGame) {
    const info = S.board.info || {};
    const mine = " · " + (o.my_color ? t("vous avez {color}", { color: colorName(o.my_color) }) : t("spectateur"));
    const phase = { play: t("en cours"), "stone removal": t("retrait des pierres mortes"), finished: t("terminée") }[o.phase] || o.phase;
    $("ogs-game-title").textContent = `#${o.game_id} ${info.PB || ""} – ${info.PW || ""}${mine}${phase ? " · " + phase : ""}${o.result ? " · " + o.result : ""}`;
  }
  const playing = inGame && o.phase === "play" && o.my_color;
  $("ogs-pass-btn").disabled = !playing;
  $("ogs-resign").disabled = !playing;
  $("ogs-undo-accept").classList.toggle("hidden", o.undo_request == null);
  $("clocks").classList.toggle("hidden", !(inGame && o.clock));
  renderClocks();
}

function renderClocks() {
  const o = S && S.ogs;
  if (!o || !o.game_id || !o.clock) return;
  const c = o.clock;
  const info = S.board.info || {};
  const blackId = c.black_player_id, current = c.current_player;
  const now = Date.now() + (o.clock_offset || 0);
  for (const color of ["B", "W"]) {
    const el = $("clock-" + color);
    const tm = c[color === "B" ? "black_time" : "white_time"];
    const active = o.phase === "play" && (blackId ? (current === blackId) === (color === "B") : false);
    el.classList.toggle("active", active);
    el.querySelector(".name").textContent = color === "B" ? info.PB || colorName("B") : info.PW || colorName("W");
    el.querySelector(".time").textContent = formatClock(tm, active ? (now - (c.last_move || now)) / 1000 : 0);
  }
}

function formatClock(tm, elapsed) {
  if (tm == null) return "";
  const fmt = (s) => {
    s = Math.max(0, Math.floor(s));
    const h = Math.floor(s / 3600), mn = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(mn).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${mn}:${String(sec).padStart(2, "0")}`;
  };
  if (typeof tm === "number") return fmt((tm - Date.now()) / 1000);
  let main = (tm.thinking_time || 0) - elapsed;
  let periods = tm.periods || 0;
  if (main < 0 && tm.period_time) {  // byo-yomi : on consomme les périodes
    let over = -main;
    const used = Math.floor(over / tm.period_time);
    periods = Math.max(0, periods - used);
    main = tm.period_time - (over % tm.period_time);
    return `${fmt(main)} (${periods}×)`;
  }
  return fmt(main) + (tm.periods ? ` + ${tm.periods}×${fmt(tm.period_time)}` : tm.moves_left ? " " + t("({n} coups)", { n: tm.moves_left }) : "");
}

async function loadOgsGames() {
  const ul = $("ogs-games");
  ul.innerHTML = `<li class="help">${t("Chargement…")}</li>`;
  try {
    const r = await fetch("/api/ogs/games").then((x) => x.json());
    if (r.error) throw new Error(r.error);
    if (!r.games.length) { ul.innerHTML = `<li class="help">${t("Aucune partie en cours. Lancez-en une sur online-go.com puis actualisez.")}</li>`; return; }
    ul.innerHTML = r.games.map((g) => `<li>
      <span>${escapeHtml(g.black)} – ${escapeHtml(g.white)} <span class="muted">${g.size}×${g.size} · ${escapeHtml(g.speed)} · ${t("{n} coups", { n: g.moves })}</span>
      ${g.my_turn ? `<span class="turn">${t("À vous")}</span>` : ""}</span>
      <button data-game="${g.id}" ${g.square && [9, 13, 19].includes(g.size) ? "" : `disabled title="${t("Taille non gérée")}"`}>${t(S.ogs.game_id === g.id ? "Suivie" : "Jouer sur le goban")}</button></li>`).join("");
  } catch (e) {
    ul.innerHTML = `<li class="error">${escapeHtml(e.message)}</li>`;
  }
}

// ------------------------------------------------------------------ actions
function setupActions() {
  $("camera-select").addEventListener("change", (e) => act("/api/camera", { id: e.target.value }));
  $("size-select").addEventListener("change", (e) => act("/api/calibration/size", { size: +e.target.value }));
  $("show-warped").addEventListener("change", renderCamera);
  $("view-select").addEventListener("change", (e) => act("/api/view", { rotation: +e.target.value }));
  $("btn-corners").addEventListener("click", () => { placing = placing ? null : []; renderCamera(); });
  $("btn-rotate").addEventListener("click", () => act("/api/calibration/rotate"));
  $("btn-orient").addEventListener("click", () => act("/api/calibration/orient", {}, (r) =>
    t(r.changed ? "Orientation corrigée" : "L'orientation était déjà la bonne")));
  $("btn-mirror").addEventListener("click", () => act("/api/calibration/mirror", { mirror: !S.calibration.mirror }));
  $("btn-refine").addEventListener("click", () => act("/api/calibration/refine", {}, (r) =>
    r.shift > 0.05 ? t("Grille ajustée ({px} px)", { px: r.shift.toFixed(1) }) : t("Grille déjà en place (ou trop peu de lignes visibles)")));
  $("btn-empty").addEventListener("click", () => {
    if (confirm(t("Le goban est-il vide ? Sa couleur sera mémorisée à chaque intersection."))) act("/api/calibration/empty", {}, t("Goban vide mémorisé"));
  });
  $("btn-quit").addEventListener("click", () => {
    if (confirm(t(S && S.ogs.game_id ? "Quitter IgOGS ? La caméra sera libérée et la partie OGS ne sera plus suivie sur le goban." : "Quitter IgOGS ? La caméra sera libérée."))) act("/api/shutdown");
  });
  $("btn-restart").addEventListener("click", () => act("/api/restart"));
  $("lang-select").addEventListener("change", async (e) => {
    setLang(e.target.value);
    await act("/api/lang", { lang: LANG });  // messages du serveur et noms des caméras
    renderAll();
    if (S && S.ogs.logged_in) loadOgsGames();
  });
  $("btn-new").addEventListener("click", () => act("/api/local/new", {}, t("Nouvelle partie")));
  $("btn-undo").addEventListener("click", () => act("/api/local/undo", { count: 1 }));
  $("btn-pass").addEventListener("click", () => act("/api/local/pass"));
  $("btn-gosensei").addEventListener("click", () => act("/api/gosensei/send", {}, t("Partie envoyée à Go Sensei")));
  $("btn-release").addEventListener("click", () => act("/api/release", {}));

  $("ogs-login").addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = await act("/api/ogs/login", { username: $("ogs-user").value, password: $("ogs-pass").value });
    $("ogs-pass").value = "";
    if (r) loadOgsGames();
  });
  $("ogs-logout").addEventListener("click", () => act("/api/ogs/logout"));
  $("ogs-refresh").addEventListener("click", loadOgsGames);
  $("ogs-games").addEventListener("click", (e) => {
    const id = e.target.dataset && e.target.dataset.game;
    if (id) act("/api/ogs/connect", { game_id: +id }).then(loadOgsGames);
  });
  $("ogs-pass-btn").addEventListener("click", () => { if (confirm(t("Passer votre tour sur OGS ?"))) act("/api/ogs/pass"); });
  $("ogs-resign").addEventListener("click", () => { if (confirm(t("Abandonner la partie sur OGS ? C'est définitif."))) act("/api/ogs/resign"); });
  $("ogs-undo-accept").addEventListener("click", () => act("/api/ogs/undo-accept"));
  $("ogs-leave").addEventListener("click", () => act("/api/ogs/disconnect"));

  document.querySelectorAll(".tabs button").forEach((btn) => btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== "tab-" + btn.dataset.tab));
    if (btn.dataset.tab === "ogs" && S && S.ogs.logged_in) loadOgsGames();
  }));

  try { $("speak").checked = localStorage.getItem("igogs.speak") !== "0"; } catch (e) { $("speak").checked = true; }
  try { $("numbers").checked = localStorage.getItem("igogs.numbers") !== "0"; } catch (e) { $("numbers").checked = true; }
  $("numbers").addEventListener("change", () => {
    try { localStorage.setItem("igogs.numbers", $("numbers").checked ? "1" : "0"); } catch (e) {}
    renderBoard();
  });
  $("speak").addEventListener("change", () => { try { localStorage.setItem("igogs.speak", $("speak").checked ? "1" : "0"); } catch (e) {} });
}

setupActions();
setupCalibrationMouse();
setupBoardClicks();
setInterval(renderClocks, 1000);
setInterval(pullFrame, 70);
connect();
