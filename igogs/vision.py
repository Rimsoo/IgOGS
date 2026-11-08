"""Vision : caméra, calibration (4 coins → homographie) et lecture des pierres.

La lecture n'utilise pas de réseau de neurones (contrairement à igoki) : chaque intersection est
comparée à trois modèles de couleur (bois vide, pierre noire, pierre blanche) dans l'espace Lab.
Le modèle du bois vide est propre à chaque intersection (ombres, reflets, dégradés de lumière) et
s'affine tout seul à chaque position stable ; un gain global suit les variations d'éclairage.
"""

import glob
import os
import threading
import time

import cv2
import numpy as np

from .goban import B, W
from .i18n import tr
from .tracker import UNKNOWN

CELL = 32  # pixels par case dans l'image redressée


# ---------------------------------------------------------------------------- caméras
def list_cameras():
    """Caméras V4L2 (Linux) avec leur nom ; repli sur les index 0..3 ailleurs."""
    out = []
    for dev in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda p: int(p.rsplit("video", 1)[1])):
        idx = int(dev.rsplit("video", 1)[1])
        try:
            with open(os.path.join(dev, "name")) as f:
                name = f.read().strip()
            # Une webcam expose souvent 2 nœuds : seul celui d'index 0 capture des images.
            with open(os.path.join(dev, "index")) as f:
                if f.read().strip() != "0":
                    continue
        except OSError:
            name = f"video{idx}"
        out.append({"id": str(idx), "name": f"{name} (/dev/video{idx})"})
    if not out and not os.path.isdir("/sys/class/video4linux"):
        out = [{"id": str(i), "name": tr("Caméra {i}", i=i)} for i in range(4)]
    out.append({"id": "sim", "name": tr("Simulateur (sans caméra)")})
    return out


def camera_users(path):
    """Applications (du même utilisateur) qui ont ouvert ce périphérique, ex. ["Discord"]."""
    names = set()
    for pid in filter(str.isdigit, os.listdir("/proc") if os.path.isdir("/proc") else []):
        try:
            if pid == str(os.getpid()):
                continue
            fds = os.listdir(f"/proc/{pid}/fd")
            if any(os.readlink(f"/proc/{pid}/fd/{fd}") == path for fd in fds):
                with open(f"/proc/{pid}/comm") as f:
                    names.add(f.read().strip())
        except OSError:
            continue
    return sorted(names)


class Camera:
    """Lit une source vidéo en continu dans un thread ; `latest()` donne la dernière image."""

    def __init__(self, source, width=1280, height=720, log=print):
        self.source = source
        self.log = log
        self.size = (width, height)
        self.frame = None
        self.stamp = 0.0
        self.error = ""
        self._stop = threading.Event()
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera")
        self._thread.start()

    def _open(self):
        src = self.source
        if isinstance(src, str) and src.isdigit():
            src = int(src)
        cap = cv2.VideoCapture(src, cv2.CAP_V4L2) if isinstance(src, int) and os.name == "posix" else cv2.VideoCapture(src)
        if isinstance(src, int):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self):
        delay = 1.0
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                users = camera_users(f"/dev/video{self.source}") if str(self.source).isdigit() else []
                self.error = (tr("caméra {cam} occupée par {apps}", cam=self.source, apps=", ".join(users))
                              if users else tr("caméra {cam} indisponible", cam=self.source))
                self._stop.wait(delay)
                delay = min(delay * 2, 10)
                continue
            self.error = ""
            delay = 1.0
            fails = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails > 20:
                        self.error = tr("caméra {cam} : plus d'image", cam=self.source)
                        break
                    time.sleep(0.05)
                    continue
                fails = 0
                self._publish(frame)
            cap.release()

    def _publish(self, frame):
        with self._cond:
            self.frame = frame
            self.stamp = time.monotonic()
            self._cond.notify_all()

    def latest(self):
        with self._cond:
            return self.frame, self.stamp

    def wait(self, after, timeout=1.0):
        """Attend une image plus récente que `after`."""
        with self._cond:
            self._cond.wait_for(lambda: self.stamp > after or self._stop.is_set(), timeout)
            return self.frame, self.stamp

    def stop(self, wait=True):
        """Arrête la lecture ; attend que la caméra soit vraiment relâchée (sinon une nouvelle
        ouverture, ou un redémarrage d'IgOGS, la trouverait encore occupée)."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if wait and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)


# ---------------------------------------------------------------------------- calibration
def order_corners(pts):
    """Ordonne 4 points dans le sens horaire en partant du coin en haut à gauche de l'image."""
    pts = np.asarray(pts, dtype=np.float64)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # sens horaire (axe y vers le bas)
    start = int(np.argmin(pts.sum(axis=1)))
    return np.roll(pts, -start, axis=0)


class Calibration:
    """Coins de la grille dans l'image caméra → coordonnées du goban.

    corners : 4 points [x, y] (pixels caméra) dans l'ordre horaire, le premier étant le coin
    A19 (en haut à gauche vu par le joueur). `rotate()` change de coin de départ.
    """

    def __init__(self, corners=None, size=19, mirror=False):
        self.size = size
        self.mirror = mirror
        self.corners = [list(map(float, p)) for p in corners] if corners else []
        self._update()

    def to_dict(self):
        return {"corners": self.corners, "size": self.size, "mirror": self.mirror}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(d.get("corners"), d.get("size", 19), d.get("mirror", False))

    @property
    def ready(self):
        return self.H is not None

    @property
    def canvas(self):
        return (self.size + 1) * CELL

    def target(self):
        """Coins de la grille dans l'image redressée, dans l'ordre des `corners`."""
        lo, hi = CELL, self.size * CELL
        t = [[lo, lo], [hi, lo], [hi, hi], [lo, hi]]
        if self.mirror:
            t = [t[1], t[0], t[3], t[2]]
        return np.float32(t)

    def _update(self):
        self.H = None
        if len(self.corners) == 4:
            H = cv2.getPerspectiveTransform(np.float32(self.corners), self.target())
            if H is not None and abs(np.linalg.det(H)) > 1e-12:
                self.H = H

    def set_corners(self, pts, keep_order=False):
        self.corners = [list(map(float, p)) for p in (pts if keep_order else order_corners(pts).tolist())]
        self._update()

    def rotate(self):
        if len(self.corners) == 4:
            self.corners = self.corners[1:] + self.corners[:1]
            self._update()

    def set_size(self, size):
        self.size = int(size)
        self._update()

    def warp(self, frame):
        return cv2.warpPerspective(frame, self.H, (self.canvas, self.canvas), flags=cv2.INTER_LINEAR)

    def grid_points(self):
        """Intersections dans l'image redressée, ordre ligne par ligne (y puis x)."""
        n = self.size
        xs = (np.arange(n) + 1) * CELL
        gx, gy = np.meshgrid(xs, xs)
        return np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float32)

    def to_camera(self, pts):
        """Points de l'image redressée → pixels caméra."""
        if not self.ready:
            return []
        p = cv2.perspectiveTransform(np.float32(pts).reshape(-1, 1, 2), np.linalg.inv(self.H))
        return p.reshape(-1, 2)

    def refine(self, frame, empty=None):
        """Ajuste finement les 4 coins pour que la grille calculée tombe pile sur les lignes du
        goban. `empty` (booléens, ligne par ligne) : intersections vides connues ; seuls les
        segments entre deux intersections vides comptent alors (sur un plateau chargé, les
        interstices entre pierres imitent des lignes). Retourne le décalage maximal appliqué."""
        if not self.ready:
            return 0.0
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        lines = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k).astype(np.float32)
        n = self.size
        lo, hi = CELL, n * CELL
        free = np.ones((n, n), bool) if empty is None else np.asarray(empty, bool).reshape(n, n)
        u = np.linspace(0, CELL, 7)[:-1]
        pts = []
        for a in range(n):
            for b in range(n - 1):
                if free[a, b] and free[a, b + 1]:  # segment horizontal sur la ligne a
                    pts += [[(b + 1) * CELL + d, (a + 1) * CELL] for d in u]
                if free[b, a] and free[b + 1, a]:  # segment vertical sur la colonne a
                    pts += [[(a + 1) * CELL, (b + 1) * CELL + d] for d in u]
        if len(pts) < 6 * 4 * n:  # trop peu de lignes visibles pour être fiable
            return 0.0
        pts = np.float32(pts).reshape(-1, 1, 2)
        target = self.target()
        # Écart d'une case dans l'image : sert à régler le flou du passage grossier.
        cam = self.to_camera([[lo, lo], [hi, lo]])
        cell_px = float(np.hypot(*(cam[1] - cam[0]))) / max(n - 1, 1)
        max_shift = 0.6 * cell_px

        def score(resp, corners):
            H = cv2.getPerspectiveTransform(np.float32(corners), target)
            p = cv2.perspectiveTransform(pts, np.linalg.inv(H))  # (N, 1, 2)
            v = cv2.remap(resp, p[..., 0], p[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            return float(v.mean())

        start = np.array(self.corners, dtype=np.float64)
        best = start.copy()
        # Grossier (lignes très floues : bassin d'attraction large) puis fin.
        c = cell_px
        for blur, steps in ((0.3 * c, (0.25 * c, 0.12 * c)), (0.12 * c, (0.06 * c, 0.03 * c)),
                            (max(1.0, 0.04 * c), (0.015 * c, 0.008 * c, 0.004 * c))):
            resp = cv2.GaussianBlur(lines, (0, 0), blur)
            s_best = score(resp, best)
            for step in steps:
                improved = True
                while improved:
                    improved = False
                    for i in range(4):
                        for j in range(2):
                            for sgn in (1, -1):
                                cand = best.copy()
                                cand[i, j] += sgn * step
                                if abs(cand[i, j] - start[i, j]) > max_shift:
                                    continue
                                s = score(resp, cand)
                                if s > s_best + 1e-6:
                                    best, s_best, improved = cand, s, True
        self.corners = best.round(2).tolist()
        self._update()
        return float(np.abs(best - start).max())

    def overlay(self):
        """Lignes de la grille et intersections, en pixels caméra (pour l'interface)."""
        if not self.ready:
            return None
        n = self.size
        lo, hi = CELL, n * CELL
        segs = []
        for i in range(n):
            v = (i + 1) * CELL
            segs += [[v, lo], [v, hi], [lo, v], [hi, v]]
        lines = self.to_camera(segs).round(1).tolist()
        return {
            "lines": [lines[i:i + 2] for i in range(0, len(lines), 2)],
            "points": self.to_camera(self.grid_points()).round(1).tolist(),
        }


# ---------------------------------------------------------------------------- lecture des pierres
def _disc_offsets(radius):
    r = int(np.ceil(radius))
    ys, xs = np.mgrid[-r:r + 1, -r:r + 1]
    keep = xs ** 2 + ys ** 2 <= radius ** 2
    return xs[keep], ys[keep]


def _surface_fit(xy, values, mask):
    """Surface quadratique (moindres carrés) ajustée sur les points `mask`, évaluée partout."""
    x, y = xy[:, 0], xy[:, 1]
    A = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    if mask.sum() < 8:
        return np.tile(np.median(values[mask] if mask.any() else values, axis=0), (len(xy), 1))
    coef, *_ = np.linalg.lstsq(A[mask], values[mask], rcond=None)
    return A @ coef


class Detector:
    """Classe chaque intersection en B / W / vide / UNKNOWN."""

    W_L, W_AB = 1.0, 1.6  # poids de la luminance et de la couleur dans les distances
    MAX_DIST = 70.0  # au-delà : rien de connu (main, ombre portée…) → UNKNOWN
    MIN_MARGIN = 0.12

    def __init__(self, size=19):
        self.reset(size)
        dx, dy = _disc_offsets(CELL * 0.36)
        self._dx, self._dy = dx, dy

    def reset(self, size=None):
        if size:
            self.size = size
        n = self.size * self.size
        self.ref = None  # (n, 3) Lab du bois vide, par intersection
        self.ref_conf = np.zeros(n)  # 1 = vu vide récemment, 0 = estimé par la surface
        self.black = None  # (3,) Lab des pierres noires
        self.white = None
        self.last_empty = None
        self._prev_gray = None

    # -------------------------------------------------------------- mesures
    def features(self, warped):
        """Lab médian sur un disque autour de chaque intersection → (n, 3) float."""
        lab = cv2.cvtColor(warped, cv2.COLOR_BGR2LAB).astype(np.float32)
        n = self.size
        c = (np.arange(n) + 1) * CELL
        cy, cx = np.meshgrid(c, c, indexing="ij")
        ys = np.clip(cy.reshape(-1, 1) + self._dy, 0, lab.shape[0] - 1)
        xs = np.clip(cx.reshape(-1, 1) + self._dx, 0, lab.shape[1] - 1)
        return np.median(lab[ys, xs], axis=1)

    def motion(self, warped):
        """Mouvement entre deux images (main au-dessus du plateau)."""
        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(warped, (160, 160)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        prev, self._prev_gray = self._prev_gray, g
        if prev is None or prev.shape != g.shape:
            return False
        return float(np.mean(cv2.absdiff(g, prev))) > 3.0 or float(np.percentile(cv2.absdiff(g, prev), 99)) > 40

    # -------------------------------------------------------------- modèles
    def _bootstrap(self, f):
        """Premier modèle sans rien savoir. Étiquetage grossier (le bois est coloré, les noires
        nettement plus sombres, les blanches claires et peu colorées), puis quelques itérations
        « modèles ↔ étiquettes » qui corrigent les erreurs du départ (plateau chargé, pénombre)."""
        L = f[:, 0]
        chroma = np.hypot(f[:, 1] - 128, f[:, 2] - 128)
        xy = self._xy()
        # Le bois est l'intersection la plus colorée : référence de départ robuste même
        # quand les pierres sont majoritaires.
        wood = chroma >= np.percentile(chroma, 90)
        Lw, cw = np.median(L[wood]), np.median(chroma[wood])
        dark = L < 0.6 * Lw
        pale = ~dark & ((chroma < 0.5 * cw) if cw > 8 else (L > Lw + 25))
        labels = np.where(dark, 1, np.where(pale, 2, 0))
        for _ in range(6):
            empty = labels == 0
            self.ref = _surface_fit(xy, f, empty if empty.sum() >= 8 else wood)
            Lr = float(np.median(self.ref[:, 0]))
            self.black = np.median(f[labels == 1], axis=0) if (labels == 1).sum() >= 2 else \
                np.array([min(60.0, 0.35 * Lr), 128.0, 128.0])
            self.white = np.median(f[labels == 2], axis=0) if (labels == 2).sum() >= 2 else \
                np.array([min(250.0, max(Lr * 1.08, Lr + 12)), 128.0, 128.0])
            new = np.argmin(self._distances(f, self.ref, self.black, self.white), axis=1)
            # Une « pierre » à peine différente du bois n'en est pas une.
            for code, proto in ((1, self.black), (2, self.white)):
                if self._dist(proto[None], self.ref.mean(axis=0))[0] < 25:
                    new[new == code] = 0
            if np.array_equal(new, labels):
                break
            labels = new
        self.ref_conf = (labels == 0).astype(float) * 0.5

    def _dist(self, f, proto):
        d = f - proto
        return np.sqrt((self.W_L * d[:, 0]) ** 2 + (self.W_AB * d[:, 1]) ** 2 + (self.W_AB * d[:, 2]) ** 2)

    def _distances(self, f, ref, black, white):
        return np.stack([self._dist(f, ref), self._dist(f, black), self._dist(f, white)], axis=1)

    def _xy(self):
        n = self.size
        i = np.arange(n * n)
        return np.stack([(i % n) / (n - 1), (i // n) / (n - 1)], axis=1)

    def learn_empty(self, f):
        """Bouton « plateau vide » : mémorise le bois à chaque intersection."""
        self.ref = f.copy()
        self.ref_conf = np.ones(len(f))
        Lr = float(np.median(f[:, 0]))
        if self.black is None:
            self.black = np.array([min(60.0, 0.35 * Lr), 128.0, 128.0])
            self.white = np.array([min(250.0, max(Lr * 1.08, Lr + 12)), 128.0, 128.0])

    def learn(self, f, grid, rate=0.25):
        """Affine les modèles sur une position stable (grille sans UNKNOWN)."""
        g = np.array([{B: 1, W: 2}.get(c, 0) for c in grid])
        empty = g == 0
        if empty.any():
            self.ref[empty] = (1 - rate) * self.ref[empty] + rate * f[empty]
            self.ref_conf[empty] = np.minimum(1.0, self.ref_conf[empty] + rate)
            # Intersections jamais vues vides : ré-estimées par la surface du bois visible.
            unsure = self.ref_conf < 0.5
            if unsure.any() and empty.sum() >= 8:
                fit = _surface_fit(self._xy(), self.ref, self.ref_conf >= 0.5)
                self.ref[unsure] = fit[unsure]
        for code, attr in ((1, "black"), (2, "white")):
            m = g == code
            if m.sum():
                cur = getattr(self, attr)
                setattr(self, attr, (1 - rate) * cur + rate * np.median(f[m], axis=0))
        self.last_empty = empty

    # -------------------------------------------------------------- classification
    def classify(self, f):
        if self.ref is None:
            self._bootstrap(f)
        ref, black, white = self.ref.copy(), self.black.copy(), self.white.copy()
        # Gain d'éclairage global, mesuré sur les intersections vides de la dernière position stable.
        if self.last_empty is not None and self.last_empty.sum() >= 5:
            m = self.last_empty
            gain = float(np.clip(np.median(f[m, 0] / np.maximum(ref[m, 0], 1)), 0.6, 1.6))
            ref[:, 0] *= gain
            black[0] *= gain
            white[0] = min(255.0, white[0] * gain)

        d = self._distances(f, ref, black, white)
        order = np.sort(d, axis=1)
        best = np.argmin(d, axis=1)
        margin = (order[:, 1] - order[:, 0]) / np.maximum(order[:, 1] + order[:, 0], 1e-6)
        grid = []
        for k, b in enumerate(best):
            if order[k, 0] > self.MAX_DIST or margin[k] < self.MIN_MARGIN:
                grid.append(UNKNOWN)
            else:
                grid.append((None, B, W)[b])
        return grid, margin
