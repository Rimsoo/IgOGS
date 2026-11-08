"""Caméra simulée : un goban dessiné en perspective, avec bois, éclairage inégal, bruit et
pierres légèrement décentrées. Sert aux tests et à essayer IgOGS sans goban ni caméra."""

import threading
import time

import cv2
import numpy as np

from .goban import B, Position, star_points

FRAME = (1280, 720)


def default_corners():
    """Coins de la grille dans l'image : vue plongeante depuis le joueur (trapèze)."""
    return [[420.0, 120.0], [860.0, 120.0], [1010.0, 640.0], [270.0, 640.0]]


class Renderer:
    def __init__(self, size=19, corners=None, seed=1, cell=48):
        self.size = size
        self.cell = cell
        self.corners = corners or default_corners()
        self.rng = np.random.default_rng(seed)
        self._board = self._wood()

    def _wood(self):
        n, c = self.size, self.cell
        side = (n + 1) * c
        rng = self.rng
        y = np.linspace(0, 1, side)[:, None]
        x = np.linspace(0, 1, side)[None, :]
        grain = np.sin((x * 40 + np.sin(y * 9) * 1.5 + rng.random() * 6) * np.pi) * 0.5 + 0.5
        base = np.array([95, 175, 220], dtype=np.float32)  # BGR, bois clair (kaya)
        img = base[None, None, :] * (0.9 + 0.1 * grain[..., None]) + rng.normal(0, 3, (side, side, 1))
        img = np.clip(img, 0, 255).astype(np.uint8)
        for i in range(n):
            v = (i + 1) * c
            cv2.line(img, (v, c), (v, n * c), (30, 30, 30), 2)
            cv2.line(img, (c, v), (n * c, v), (30, 30, 30), 2)
        for x0, y0 in star_points(n):
            cv2.circle(img, ((x0 + 1) * c, (y0 + 1) * c), max(3, c // 10), (30, 30, 30), -1)
        return img

    def render(self, pos, hand=False, light=0.0):
        n, c = self.size, self.cell
        img = self._board.copy()
        r = int(c * 0.47)
        for y in range(n):
            for x in range(n):
                s = pos[(x, y)]
                if s is None:
                    continue
                jx, jy = self.rng.normal(0, c * 0.04, 2)
                cx, cy = int((x + 1) * c + jx), int((y + 1) * c + jy - c * 0.06)
                cv2.circle(img, (cx + 3, cy + 4), r, (40, 80, 110), -1, cv2.LINE_AA)  # ombre
                if s == B:
                    cv2.circle(img, (cx, cy), r, (28, 26, 25), -1, cv2.LINE_AA)
                    cv2.circle(img, (cx - r // 3, cy - r // 3), r // 4, (90, 90, 90), -1, cv2.LINE_AA)
                else:
                    cv2.circle(img, (cx, cy), r, (225, 230, 232), -1, cv2.LINE_AA)
                    cv2.circle(img, (cx + r // 5, cy + r // 5), int(r * 0.8), (205, 210, 212), 2, cv2.LINE_AA)
        src = np.float32([[c, c], [n * c, c], [n * c, n * c], [c, n * c]])
        H = cv2.getPerspectiveTransform(src, np.float32(self.corners))
        frame = np.full((FRAME[1], FRAME[0], 3), (60, 70, 75), np.uint8)
        cv2.warpPerspective(img, H, FRAME, dst=frame, borderMode=cv2.BORDER_TRANSPARENT)
        # Éclairage inégal (lampe en haut à gauche) + variation globale.
        yy, xx = np.mgrid[0:FRAME[1], 0:FRAME[0]].astype(np.float32)
        shade = 1.1 - 0.35 * (xx / FRAME[0] + yy / FRAME[1]) / 2 + light
        frame = np.clip(frame.astype(np.float32) * shade[..., None] + self.rng.normal(0, 4, frame.shape), 0, 255)
        frame = frame.astype(np.uint8)
        if hand:
            cv2.ellipse(frame, (700, 400), (160, 90), 30, 0, 360, (120, 150, 210), -1)
        return frame


class SimCamera:
    """Même interface que vision.Camera ; la position simulée se modifie par `set_point`."""

    def __init__(self, size=19, fps=10):
        self.renderer = Renderer(size)
        self.pos = Position(size)
        self.error = ""
        self.fps = fps
        self.frame = None
        self.stamp = 0.0
        self._hand_until = 0.0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._dirty = True
        threading.Thread(target=self._run, daemon=True, name="sim-camera").start()

    def resize(self, size):
        with self._cond:
            self.renderer = Renderer(size)
            self.pos = Position(size)
            self._dirty = True

    def set_point(self, pt, color):
        """Pose (B / W) ou retire (None) une pierre, comme une main le ferait."""
        with self._cond:
            self.pos[pt] = color
            self._hand_until = time.monotonic() + 0.3
            self._dirty = True

    def set_position(self, pos):
        with self._cond:
            self.pos = pos.copy()
            self._hand_until = time.monotonic() + 0.3
            self._dirty = True

    def _run(self):
        frame = None
        while not self._stop.is_set():
            with self._cond:
                hand = time.monotonic() < self._hand_until
                pos = self.pos.copy()
                dirty = self._dirty or hand
                self._dirty = hand
            if dirty or frame is None:
                frame = self.renderer.render(pos, hand=hand)
            with self._cond:
                self.frame = frame
                self.stamp = time.monotonic()
                self._cond.notify_all()
            self._stop.wait(1 / self.fps)

    def latest(self):
        with self._cond:
            return self.frame, self.stamp

    def wait(self, after, timeout=1.0):
        with self._cond:
            self._cond.wait_for(lambda: self.stamp > after or self._stop.is_set(), timeout)
            return self.frame, self.stamp

    def stop(self):
        self._stop.set()
