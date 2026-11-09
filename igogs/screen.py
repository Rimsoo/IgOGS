"""Jeu à l'écran (ex. The Conquest of Go contre l'IA) : IgOGS lit le goban affiché par le jeu et y
clique les coups joués sur le goban physique ; les coups de l'adversaire lus à l'écran sont
annoncés et attendus sur le goban, comme ceux d'un adversaire OGS.

Sous Hyprland : la fenêtre est trouvée par `hyprctl clients`, capturée par `grim`, le curseur est
placé par `hyprctl dispatch movecursor` et le clic vient d'une souris virtuelle (/dev/uinput).
Le jeu doit rester visible (pas sur un autre bureau, pas recouvert).
"""

import fcntl
import json
import os
import struct
import subprocess
import threading
import time

import cv2
import numpy as np

from .goban import B, W, Game, IllegalMove, Position, other, to_gtp
from .i18n import tr

OWNER = "screen"
SIZES = (19, 13, 9)


class ScreenError(Exception):
    pass


def _run(cmd, timeout=3.0):
    from .server import host_env  # dans l'AppImage : sans ses bibliothèques embarquées

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=host_env())
    except FileNotFoundError:
        raise ScreenError(tr("commande introuvable : {cmd}", cmd=cmd[0]))
    except subprocess.TimeoutExpired:
        raise ScreenError(tr("{cmd} ne répond pas", cmd=cmd[0]))
    if r.returncode != 0:
        raise ScreenError(f"{cmd[0]} : {r.stderr.decode(errors='replace').strip()[:200]}")
    return r.stdout


def _hypr(*args):
    return json.loads(_run(["hyprctl", "-j", *args]))


def move_cursor(x, y):
    """Place le curseur (coordonnées globales). Hyprland ≥ 0.55 (config Lua) : `hl.dsp.cursor.move` ;
    avant : le dispatcher `movecursor`."""
    x, y = int(round(x)), int(round(y))
    try:
        _run(["hyprctl", "dispatch", f"hl.dsp.cursor.move({{x={x},y={y}}})"])
    except ScreenError:
        _run(["hyprctl", "dispatch", "movecursor", str(x), str(y)])


# ---------------------------------------------------------------------------- fenêtre et capture
def find_window(pattern):
    """Fenêtre du jeu (titre ou classe contenant `pattern`) : {"at", "size", "title"}."""
    pattern = pattern.lower()
    clients = [c for c in _hypr("clients") if c.get("mapped", True)
               and (pattern in (c.get("title") or "").lower() or pattern in (c.get("class") or "").lower())]
    if not clients:
        raise ScreenError(tr("fenêtre du jeu introuvable (« {p} »)", p=pattern))
    visible = set()
    for m in _hypr("monitors"):
        visible.add((m.get("activeWorkspace") or {}).get("id"))
        visible.add((m.get("specialWorkspace") or {}).get("id"))
    shown = [c for c in clients if (c.get("workspace") or {}).get("id") in visible]
    if not shown:
        raise ScreenError(tr("la fenêtre du jeu n'est pas visible (autre bureau ?)"))
    c = max(shown, key=lambda c: c["size"][0] * c["size"][1])
    return {"at": tuple(c["at"]), "size": tuple(c["size"]), "title": c.get("title") or c.get("class")}


def parse_ppm(data):
    """PPM binaire (P6) → image BGR (sans dépendre du décodeur PxM d'OpenCV)."""
    fields, pos = [], 0
    while len(fields) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        fields.append(data[pos:end])
        pos = end
    if fields[0] != b"P6" or int(fields[3]) != 255:
        raise ScreenError("grim : format d'image inattendu")
    w, h = int(fields[1]), int(fields[2])
    rgb = np.frombuffer(data, np.uint8, w * h * 3, pos + 1).reshape(h, w, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def capture(win):
    (x, y), (w, h) = win["at"], win["size"]
    return parse_ppm(_run(["grim", "-s", "1", "-t", "ppm", "-g", f"{x},{y} {w}x{h}", "-"]))


# ---------------------------------------------------------------------------- souris virtuelle
class VirtualMouse:
    """Souris uinput (clics seulement) ; le curseur est placé par Hyprland."""

    UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
    UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
    EV_SYN, EV_KEY, EV_REL = 0, 1, 2
    BTN_LEFT, REL_X = 0x110, 0

    def __init__(self):
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            raise ScreenError(tr("souris virtuelle impossible (/dev/uinput : {e})", e=e.strerror))
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, self.EV_KEY)
        fcntl.ioctl(self.fd, self.UI_SET_EVBIT, self.EV_REL)
        fcntl.ioctl(self.fd, self.UI_SET_KEYBIT, self.BTN_LEFT)
        fcntl.ioctl(self.fd, self.UI_SET_RELBIT, self.REL_X)
        fcntl.ioctl(self.fd, self.UI_SET_RELBIT, 1)  # REL_Y
        # struct uinput_user_dev : nom, input_id, ff_effects_max, absmax/min/fuzz/flat[64]
        dev = struct.pack("80sHHHHi", b"IgOGS virtual mouse", 0x06, 0x1209, 0x6f67, 1, 0) + bytes(4 * 64 * 4)
        os.write(self.fd, dev)
        fcntl.ioctl(self.fd, self.UI_DEV_CREATE)
        time.sleep(0.4)  # le compositeur doit d'abord découvrir le périphérique

    def _ev(self, typ, code, value):
        os.write(self.fd, struct.pack("llHHi", 0, 0, typ, code, value))

    def _syn(self):
        self._ev(self.EV_SYN, 0, 0)

    def _nudge(self):
        """Petit mouvement réel : Hyprland met à jour la fenêtre sous le curseur."""
        for d in (1, -1):
            self._ev(self.EV_REL, self.REL_X, d)
            self._syn()
            time.sleep(0.01)

    def click_at(self, x, y):
        try:
            back = [int(float(v)) for v in _run(["hyprctl", "cursorpos"]).decode().split(",")]
        except (ScreenError, ValueError):
            back = None
        move_cursor(x, y)
        if [int(float(v)) for v in _run(["hyprctl", "cursorpos"]).decode().split(",")] != [int(round(x)), int(round(y))]:
            raise ScreenError(tr("le curseur n'a pas pu être placé sur le goban du jeu"))
        # Les jeux lisent souris et boutons à chaque image : on laisse le temps de voir le survol.
        self._nudge()
        time.sleep(0.12)
        self._ev(self.EV_KEY, self.BTN_LEFT, 1)
        self._syn()
        time.sleep(0.08)
        self._ev(self.EV_KEY, self.BTN_LEFT, 0)
        self._syn()
        time.sleep(0.2)
        if back:
            move_cursor(*back)
            self._nudge()

    def close(self):
        try:
            fcntl.ioctl(self.fd, self.UI_DEV_DESTROY)
            os.close(self.fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------- lecture du goban
def _line_response(gray, axis):
    """Lignes fines (claires ou sombres) parallèles à `axis` (0 : horizontales) : le pixel diffère
    de ses deux voisins à ±3 px dans le même sens. Un simple bord (dégradé, cadre) ne répond pas."""
    g = gray.astype(np.float32)
    d = 3
    a, b = np.roll(g, d, axis=axis), np.roll(g, -d, axis=axis)
    r = np.clip(np.maximum(np.minimum(g - a, g - b), np.minimum(a - g, b - g)), 0, None)
    if axis == 0:
        r[:d], r[-d:] = 0, 0
    else:
        r[:, :d], r[:, -d:] = 0, 0
    # Ne garder que les structures longues (lignes de la grille, pas le texte ni les pierres).
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (31, 1) if axis == 0 else (1, 31))
    return cv2.morphologyEx(r, cv2.MORPH_OPEN, k)


def _axis_candidates(profile, n, keep=6):
    """Meilleures suites de n lignes équidistantes dans un profil : [(score, début, pas)]."""
    p = profile / (profile.max() + 1e-6)
    m = cv2.dilate(p.reshape(-1, 1), np.ones((5, 1))).ravel()
    peaks = [i for i in range(len(p)) if p[i] > 0.15 and p[i] == m[i]]
    out = []
    for i, a in enumerate(peaks):
        for b in peaks[i + 1:]:
            s = (b - a) / (n - 1)
            if s < 8:
                continue
            idx = np.rint(a + s * np.arange(n)).astype(int)
            out.append((float(m[idx].mean()), float(a), float(s)))
    out.sort(reverse=True)
    return out[:keep]


def detect_grid(img):
    """Trouve la grille du goban à l'écran : {"size", "x0", "y0", "step"} (pixels de l'image)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    rh, rv = _line_response(gray, 0), _line_response(gray, 1)
    ph, pv = rh.sum(axis=1), rv.sum(axis=0)
    resp = rh + rv

    def midway(profile, a, s, n):
        """Lignes à mi-chemin : la candidate ne garde qu'une ligne sur deux."""
        p = cv2.dilate((profile / (profile.max() + 1e-6)).reshape(-1, 1), np.ones((3, 1))).ravel()
        return float(p[np.clip(np.rint(a + s * (np.arange(n - 1) + 0.5)).astype(int), 0, len(p) - 1)].mean())

    def overflow(x0, y0, s, n):
        """Les lignes continuent au-delà des bords : la candidate n'est qu'un morceau du goban."""
        k = np.arange(n) * s
        u = np.linspace(0.25, 0.75, 6) * s
        out = [(np.repeat(x0 + k, 6), np.tile(y0 - u, n), rv), (np.repeat(x0 + k, 6), np.tile(y0 + k[-1] + u, n), rv),
               (np.tile(x0 - u, n), np.repeat(y0 + k, 6), rh), (np.tile(x0 + k[-1] + u, n), np.repeat(y0 + k, 6), rh)]
        h, w = resp.shape
        return float(np.mean([(r[np.clip(np.rint(ys).astype(int), 0, h - 1), np.clip(np.rint(xs).astype(int), 0, w - 1)] > 0).mean()
                              for xs, ys, r in out]))

    found = []
    for n in SIZES:
        for _, y0, sy in _axis_candidates(ph, n):
            for _, x0, sx in _axis_candidates(pv, n):
                if abs(sx - sy) > 0.03 * sx:
                    continue
                s = (sx + sy) / 2
                # Score : réponse moyenne le long de tous les segments de la grille.
                t = np.linspace(0, (n - 1) * s, 8 * (n - 1) + 1)
                k = np.arange(n) * s
                xs = np.concatenate([np.repeat(x0 + k, len(t)), np.tile(x0 + t, n)])
                ys = np.concatenate([np.tile(y0 + t, n), np.repeat(y0 + k, len(t))])
                xi = np.clip(np.rint(xs).astype(int), 0, img.shape[1] - 1)
                yi = np.clip(np.rint(ys).astype(int), 0, img.shape[0] - 1)
                cover = float((resp[yi, xi] > 0).mean())
                found.append((cover * (1 - midway(ph, y0, s, n)) * (1 - midway(pv, x0, s, n))
                              * (1 - overflow(x0, y0, s, n)), n, x0, y0, s))
    if not found:
        raise ScreenError(tr("aucun goban trouvé dans la fenêtre du jeu"))
    best = max(f[0] for f in found)
    if best < 0.2:  # les pierres cachent une bonne partie des lignes sur un goban chargé
        raise ScreenError(tr("aucun goban trouvé dans la fenêtre du jeu"))
    # Une sous-grille d'un 19×19 marche aussi bien qu'un 9×9 : on préfère le plus grand goban.
    score, n, x0, y0, s = max((f for f in found if f[0] >= 0.85 * best), key=lambda f: (f[1], f[0]))
    return {"size": n, "x0": x0, "y0": y0, "step": s}


def _disc(radius):
    r = int(np.ceil(radius))
    ys, xs = np.mgrid[-r:r + 1, -r:r + 1]
    keep = xs ** 2 + ys ** 2 <= radius ** 2
    return xs[keep], ys[keep]


def read_board(img, grid):
    """Pierres à l'écran : liste ligne par ligne de B / W / None, ou None si une intersection est
    douteuse (curseur, pierre « fantôme » de survol, animation…)."""
    n, s = grid["size"], grid["step"]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]

    def medians(cx, cy, radius):
        dx, dy = _disc(radius)
        xs = np.clip(np.rint(cx).astype(int)[:, None] + dx, 0, w - 1)
        ys = np.clip(np.rint(cy).astype(int)[:, None] + dy, 0, h - 1)
        return np.median(lab[ys, xs], axis=1)

    k = np.arange(n)
    gx, gy = np.meshgrid(grid["x0"] + k * s, grid["y0"] + k * s)
    f = medians(gx.ravel(), gy.ravel(), 0.3 * s)
    # Bois de référence : centres des 4 cases autour de chaque intersection (jamais couverts par
    # une pierre).
    refs = [medians(gx.ravel() + dx * s / 2, gy.ravel() + dy * s / 2, 0.12 * s)
            for dx in (-1, 1) for dy in (-1, 1)]
    ref = np.median(np.stack(refs), axis=0)
    w_ab = 1.6
    d_ref = np.sqrt((f[:, 0] - ref[:, 0]) ** 2 + w_ab ** 2 * ((f[:, 1:] - ref[:, 1:]) ** 2).sum(axis=1))
    chroma = np.hypot(f[:, 1] - 128, f[:, 2] - 128)
    out = []
    for k in range(n * n):
        if d_ref[k] < 18:
            out.append(None)
        elif chroma[k] < 10 and f[k, 0] < min(110, ref[k, 0] - 20):  # pierres : grises, sans couleur
            out.append(B)
        elif chroma[k] < 10 and f[k, 0] > max(150, ref[k, 0] - 15):
            out.append(W)
        else:
            return None
    return out


def draw_grid(img, grid, stones=None):
    out = img.copy()
    n, s = grid["size"], grid["step"]
    x0, y0 = grid["x0"], grid["y0"]
    for i in range(n):
        v = i * s
        cv2.line(out, (int(x0 + v), int(y0)), (int(x0 + v), int(y0 + (n - 1) * s)), (255, 200, 80), 2)
        cv2.line(out, (int(x0), int(y0 + v)), (int(x0 + (n - 1) * s), int(y0 + v)), (255, 200, 80), 2)
    for i, c in enumerate(stones or []):
        if c:
            p = (int(x0 + (i % n) * s), int(y0 + (i // n) * s))
            cv2.circle(out, p, max(4, int(s * 0.15)), (0, 0, 0) if c == B else (255, 255, 255), -1)
            cv2.circle(out, p, max(4, int(s * 0.15)), (64, 145, 224), 2)
    return out


# ---------------------------------------------------------------------------- pont jeu ↔ goban
class ScreenBridge:
    """Relie le jeu affiché à l'écran au goban physique."""

    POLL = 0.3
    CLICK_TIMEOUT = 5.0

    def __init__(self, cfg, driver, log=print, notify=None):
        self.cfg = cfg
        self.driver = driver
        self.log = log
        self.notify = notify or (lambda kind, data: None)
        self.active = False
        self.my_color = B
        self.grid = None
        self.window = None
        self.error = ""
        self.mouse = None
        self.sent = None  # (pt, heure) : coup cliqué dans le jeu, pas encore vu à l'écran
        self.preview = None  # dernière capture annotée (JPEG)
        self._stop = threading.Event()
        self._thread = None
        self._last_read = None
        self._mismatch_since = None
        self._img_shape = None
        driver.subscribe(self._on_driver)

    def state(self):
        return {"active": self.active, "my_color": self.my_color, "error": self.error,
                "window": (self.window or {}).get("title"),
                "size": (self.grid or {}).get("size"), "pattern": self.cfg["screen"]["window"]}

    def _changed(self):
        self.notify("screen", self.state())

    # ------------------------------------------------------------ commandes
    def start(self, color):
        if color not in (B, W):
            raise ValueError(tr("couleur : B ou W"))
        self.stop(release=False)
        self.my_color = color
        self.error = ""
        img = self._capture()
        self.grid = detect_grid(img)
        stones = read_board(img, self.grid)
        self._save_preview(img, stones)
        if stones is None:
            raise ScreenError(tr("lecture du goban à l'écran incertaine : déplacez la souris hors du goban"))
        if self.mouse is None:
            self.mouse = VirtualMouse()
        self._load(stones)
        self.active = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True, name="screen")
        self._thread.start()
        self.log(f"Écran : {self.window['title']}, goban {self.grid['size']}×{self.grid['size']}")
        self._changed()

    def stop(self, release=True):
        self._stop.set()
        was = self.active
        self.active = False
        self.sent = None
        if release and was:
            self.driver.release(OWNER)
        self._changed()

    def resync(self):
        """Repart de la position affichée (après une annulation dans le jeu, une partie neuve…)."""
        if not self.active:
            raise ValueError(tr("le jeu à l'écran n'est pas suivi"))
        stones = read_board(self._capture(), self.grid)
        if stones is None:
            raise ScreenError(tr("lecture du goban à l'écran incertaine : déplacez la souris hors du goban"))
        self._load(stones)

    def opponent_passed(self):
        game = self.driver.tracker.game
        if not self.active or game.to_play() == self.my_color:
            raise ValueError(tr("ce n'est pas le tour de l'adversaire"))
        self.driver.push(None)
        self._announce(other(self.my_color), None)

    def close(self):
        self.stop(release=False)
        if self.mouse:
            self.mouse.close()

    # ------------------------------------------------------------ interne
    def _capture(self):
        self.window = find_window(self.cfg["screen"]["window"])
        return capture(self.window)

    def _save_preview(self, img, stones):
        im = draw_grid(img, self.grid, stones) if self.grid else img
        if im.shape[1] > 960:
            im = cv2.resize(im, (960, int(im.shape[0] * 960 / im.shape[1])), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 80])
        self.preview = buf.tobytes() if ok else None

    def _load(self, stones):
        n = self.grid["size"]
        setup = {B: [], W: []}
        for i, c in enumerate(stones):
            if c:
                setup[c].append((i % n, i // n))
        first = B if len(setup[B]) <= len(setup[W]) else W
        names = {self.my_color: tr("Moi"), other(self.my_color): tr("Jeu")}
        info = {"PB": names[B], "PW": names[W], "PC": self.window["title"], "DT": time.strftime("%Y-%m-%d")}
        self.sent = None
        self._mismatch_since = None
        self._last_read = stones
        self.driver.claim(OWNER, Game(n, setup, first), camera_colors=(self.my_color,), autoplay=False,
                          allow_undo=False, info=info)

    def _loop(self, stop):
        while not stop.wait(self.POLL):
            try:
                img = self._capture()
                if img.shape[:2] != (self._img_shape or img.shape[:2]):
                    self.grid = detect_grid(img)
                self._img_shape = img.shape[:2]
                stones = read_board(img, self.grid)
                if stones is not None and stones == self._last_read:
                    self._sync(stones)
                self._last_read = stones
            except ScreenError as e:
                if str(e) != self.error:
                    self.error = str(e)
                    self.log(f"Écran : {e}")
                    self._changed()
                stop.wait(1.0)
                continue
            except Exception as e:
                self.log(f"Écran : {e!r}")
                stop.wait(1.0)
                continue
            if self.sent and time.monotonic() - self.sent[1] > self.CLICK_TIMEOUT:
                self.sent = None
                self.error = tr("le coup n'est pas apparu dans le jeu")
                with self.driver.lock:
                    self.driver.tracker.pending = None
                    self.driver.tracker.refresh()
                self.driver.emit("game", self.driver.board_state())
                self._changed()

    def _sync(self, stones):
        """Rattrape la partie sur ce qu'affiche le jeu : coups (avec prises), passes implicites."""
        d = self.driver
        n = self.grid["size"]
        screen = Position(n, stones)
        with d.lock:
            game = d.tracker.game
            if game.size != n:
                return
            pos = game.position()
        if pos == screen:
            self._mismatch_since = None
            return
        for _ in range(4):
            with d.lock:
                game = d.tracker.game
                pos = game.position()
                if pos == screen:
                    break
                color = game.to_play()
                pt = self._find_move(pos, screen, color)
                passed = pt is None and self._find_move(pos, screen, other(color)) is not None
            if pt is None and not passed:
                break
            if passed:
                d.push(None)  # le joueur au trait a passé dans le jeu
                self._announce(color, None)
                continue
            d.push(pt, color)
            if pt == (self.sent or (None,))[0]:
                self.sent = None
            self._announce(color, pt)
        with d.lock:
            ok = d.tracker.game.position() == screen
        if ok:
            self._mismatch_since = None
            self.error = ""
        else:
            # Annulation dans le jeu, nouvelle partie… : après un moment, on repart de l'écran.
            now = time.monotonic()
            self._mismatch_since = self._mismatch_since or now
            if now - self._mismatch_since > 2.0:
                self.log("Écran : position différente de la partie suivie, resynchronisation")
                self._load(stones)
        self._changed()

    @staticmethod
    def _find_move(pos, screen, color):
        """Coup de `color` qui rapproche la position de l'écran (prises comprises)."""
        before = len(pos.diff(screen))
        for pt, e, s in pos.diff(screen):
            if e is None and s == color:
                p = pos.copy()
                try:
                    p.play(color, pt)
                except IllegalMove:
                    continue
                if len(p.diff(screen)) < before:
                    return pt
        return None

    def _announce(self, color, pt):
        n = self.grid["size"]
        self.notify("screen-move", {"color": color, "move": to_gtp(pt, n), "mine": color == self.my_color})

    # ------------------------------------------------------------ goban → jeu
    def _on_driver(self, kind, data):
        if kind != "move" or data["owner"] != OWNER or data["applied"] or not self.active:
            return
        if data["color"] != self.my_color or data["x"] < 0 or not self.grid or not self.window:
            return
        g = self.grid
        x = self.window["at"][0] + g["x0"] + data["x"] * g["step"]
        y = self.window["at"][1] + g["y0"] + data["y"] * g["step"]
        self.log(f"Écran : clic en {data['move']}")
        try:
            self.mouse.click_at(x, y)
            self.sent = ((data["x"], data["y"]), time.monotonic())
        except (ScreenError, OSError) as e:
            self.log(f"Écran : clic impossible ({e})")
            self.error = tr("clic impossible : {e}", e=e)
            with self.driver.lock:
                self.driver.tracker.pending = None
                self.driver.tracker.refresh()
            self.driver.emit("game", self.driver.board_state())
            self._changed()
