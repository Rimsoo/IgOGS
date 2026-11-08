"""Le driver : caméra → lecture → position stable → coups, et la partie « attendue ».

Une seule partie est suivie à la fois. Son propriétaire (`owner`) est soit l'enregistrement local
(par défaut), soit OGS, soit une application externe (Go Sensei…) qui s'est branchée via l'API.
Les événements sont diffusés à tous les abonnés (`subscribe`).
"""

import os
import threading
import time

import cv2
import numpy as np

from . import config as cfgmod
from .goban import B, W, Game, Position, to_gtp, to_sgf
from .i18n import tr
from .simulate import SimCamera, default_corners
from .tracker import UNKNOWN, Stabilizer, Tracker
from .vision import CELL, Calibration, Camera, Detector, list_cameras

LOCAL = "local"


def grid_str(grid):
    return "".join({B: "B", W: "W", None: ".", UNKNOWN: "?"}[c] for c in grid)


class Driver:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.lock = threading.RLock()
        self.listeners = []
        self.camera = None
        self.camera_id = ""
        self.cal = Calibration.from_dict(cfg["calibration"])
        self.det = Detector(self.cal.size)
        self.stab = Stabilizer(hold=cfg["stable_hold"])
        self.tracker = Tracker(Game(self.cal.size))
        self.owner = LOCAL
        self.info = {}  # métadonnées de la partie (joueurs…) pour le SGF / l'affichage
        self.live = None  # dernière lecture (chaîne B/W/./?)
        self.busy = True
        self.fps = 0.0
        self.empty_mask = None
        self.rotated_hint = False  # le goban lu correspond à la partie, mais tourné / en miroir
        self._stop = threading.Event()
        self._last_live_emit = 0.0
        self._model_saved = 0.0
        self._load_model()

    # ------------------------------------------------------------ événements
    def subscribe(self, fn):
        self.listeners.append(fn)

    def emit(self, kind, data):
        for fn in list(self.listeners):
            try:
                fn(kind, data)
            except Exception as e:  # un abonné défaillant ne doit pas arrêter la lecture
                self.log(f"abonné en erreur ({kind}) : {e}")

    # ------------------------------------------------------------ caméra
    def start(self):
        cam = self.cfg["camera"]
        if cam:
            self.set_camera(cam)
        threading.Thread(target=self._loop, daemon=True, name="driver").start()

    def stop(self):
        self._stop.set()
        if self.camera:
            self.camera.stop()
        self._save_model(force=True)

    def set_camera(self, cam_id):
        with self.lock:
            if self.camera:
                self.camera.stop()
            self.camera_id = cam_id
            if cam_id == "sim":
                self.camera = SimCamera(self.cal.size)
                if not self.cal.ready:
                    self.cal.set_corners(default_corners())
                    self._calibration_changed()
            elif cam_id:
                self.camera = Camera(cam_id, log=self.log)
            else:
                self.camera = None
            self.stab.reset()
            self.det.reset(self.cal.size)
        self.cfg.update({"camera": cam_id})
        self.emit("state", self.state())

    # ------------------------------------------------------------ calibration
    def set_corners(self, corners, keep_order=False):
        with self.lock:
            self.cal.set_corners(corners, keep_order)
            if not keep_order:  # nouvelle pose des coins : A1 en bas à gauche, vu du joueur
                self.cal.mirror = False
                self._orient_to_view()
            self._calibration_changed()

    @property
    def view_rotation(self):
        return int(self.cfg["view_rotation"]) % 360

    def set_view(self, rotation):
        """Place du joueur par rapport à la caméra : rotation (degrés, sens horaire) qui met son
        côté du goban en bas de l'aperçu. Les coordonnées suivent (A1 en bas à gauche pour lui)."""
        rotation = int(rotation) % 360
        if rotation % 90:
            raise ValueError(tr("rotation : 0, 90, 180 ou 270"))
        self.cfg.update({"view_rotation": rotation})
        with self.lock:
            if self.cal.ready:
                self._relabel(self._view_calibration())
            else:
                self._calibration_changed()

    def _orient_to_view(self):
        self.cal = self._view_calibration()

    def _view_calibration(self):
        """Parmi les 4 rotations des coordonnées (miroir inchangé), prend celle qui met A1 en bas à
        gauche de l'aperçu tel que le joueur le voit."""
        if len(self.cal.corners) != 4:
            return self.cal
        n = self.cal.size
        frame, _ = self.camera.latest() if self.camera else (None, 0)
        corners = np.array(self.cal.corners)
        center = corners.mean(axis=0) if frame is None else np.array([frame.shape[1], frame.shape[0]]) / 2
        a = np.radians(self.view_rotation)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])  # sens horaire (y vers le bas)
        best, best_score = None, None
        for r in range(4):
            cand = Calibration(self.cal.corners[r:] + self.cal.corners[:r], n, self.cal.mirror)
            pts = cand.to_camera([[CELL, n * CELL], [n * CELL, CELL]])  # A1 et le coin opposé
            a1, top = (rot @ (pts - center).T).T
            score = (top[0] - a1[0]) + (a1[1] - top[1])  # A1 à gauche et en bas
            if best_score is None or score > best_score:
                best, best_score = cand, score
        return best

    def rotate(self):
        with self.lock:
            c = self.cal.corners
            self._relabel(Calibration(c[1:] + c[:1], self.cal.size, self.cal.mirror))

    def set_mirror(self, mirror):
        with self.lock:
            self._relabel(Calibration(self.cal.corners, self.cal.size, bool(mirror)))

    def _permutation(self, cand):
        """perm[k] = intersection (actuelle) qui devient l'intersection k avec la calibration `cand`
        (même goban physique, autres coordonnées)."""
        base_pts = self.cal.to_camera(self.cal.grid_points())
        pts = cand.to_camera(cand.grid_points())
        return ((pts[:, None, :] - base_pts[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)

    def _relabel(self, cand):
        """Change seulement l'étiquetage des intersections (rotation, miroir, place du joueur) :
        le modèle de couleurs appris suit les intersections physiques, et la partie enregistrée
        localement est renumérotée pour rester fidèle au goban."""
        if not (self.cal.ready and cand.ready):
            self.cal = cand
            self._calibration_changed()
            return
        perm = self._permutation(cand)
        n = self.cal.size
        new_of = {int(j): k for k, j in enumerate(perm)}  # ancien indice → nouvel indice
        move = lambda pt: None if pt is None else (new_of[pt[1] * n + pt[0]] % n, new_of[pt[1] * n + pt[0]] // n)
        if self.det.ref is not None and len(self.det.ref) == len(perm):
            self.det.ref, self.det.ref_conf = self.det.ref[perm], self.det.ref_conf[perm]
        self.det.last_empty = None
        t = self.tracker
        if self.owner == LOCAL:
            old = t.game
            game = Game(n, {c: [move(p) for p in old.setup[c]] for c in (B, W)}, old.first, old.free_handicap)
            game.moves = [(c, move(p)) for c, p in old.moves]
            t.game = game
            t.pending = None
        if t.observed is not None and t.observed.size == n:
            t.observed = Position(n, [t.observed.grid[j] for j in perm])
            t.previous = None
        self.cal = cand
        self.rotated_hint = False
        self._calibration_changed(reset_model=False)
        t.refresh()
        self.emit("game", self.board_state())

    def set_size(self, size):
        size = int(size)
        if size not in (9, 13, 19):
            raise ValueError(tr("taille de goban : 9, 13 ou 19"))
        with self.lock:
            self.cal.set_size(size)
            if isinstance(self.camera, SimCamera):
                self.camera.resize(size)
            if self.owner == LOCAL:
                self.tracker.set_game(Game(size))
            self._calibration_changed()

    def refine(self):
        frame, _ = self.camera.latest() if self.camera else (None, 0)
        if frame is None:
            raise ValueError(tr("pas d'image de la caméra"))
        with self.lock:
            shift = self.cal.refine(frame, self.empty_mask)
            self._calibration_changed(reset_model=False)
        return shift

    # ------------------------------------------------------------ orientation automatique
    def _orientations(self, grid):
        """Les 8 orientations possibles de la calibration (4 rotations × miroir), avec pour
        chacune le nombre de différences entre le goban lu ainsi et la partie attendue.
        Retourne [(différences, calibration, permutation)], la meilleure d'abord."""
        base = self.cal
        expected = self.tracker.game.position().grid
        if not base.ready or len(grid) != len(expected):
            return []
        base_pts = base.to_camera(base.grid_points())
        out = []
        for mirror in (base.mirror, not base.mirror):
            for r in range(4):
                cand = Calibration(base.corners[r:] + base.corners[:r], base.size, mirror)
                pts = cand.to_camera(cand.grid_points())
                # Même goban, autre étiquetage : chaque intersection candidate est une intersection
                # de la calibration actuelle.
                d = ((pts[:, None, :] - base_pts[None, :, :]) ** 2).sum(axis=2)
                perm = d.argmin(axis=1)
                diffs = sum(0.5 if grid[j] == UNKNOWN else grid[j] != e for j, e in zip(perm, expected))
                out.append((diffs, cand, perm))
        out.sort(key=lambda o: o[0])
        return out

    def _rotation_hint(self, grid):
        """Vrai si le goban ne correspond pas à la partie tel quel, mais exactement une fois tourné."""
        if self.owner == LOCAL or sum(c is not None for c in self.tracker.game.position().grid) < 3:
            return False
        opts = self._orientations(grid)
        if not opts or opts[0][0] != 0 or (len(opts) > 1 and opts[1][0] == 0):
            return False
        same = opts[0][1].corners == self.cal.corners and opts[0][1].mirror == self.cal.mirror
        return not same

    def auto_orient(self):
        """Choisit l'orientation (rotation, miroir) qui fait coïncider le goban et la partie."""
        with self.lock:
            grid = [{"B": B, "W": W, ".": None}.get(c, UNKNOWN) for c in (self.live or "")]
            stones = sum(c is not None for c in self.tracker.game.position().grid)
            if stones < 3:
                raise ValueError(tr("il faut au moins 3 pierres dans la partie pour trouver l'orientation"))
            opts = self._orientations(grid)
            if not opts:
                raise ValueError(tr("caméra ou calibration manquante"))
            best, cand, perm = opts[0]
            if len(opts) > 1 and opts[1][0] == best:
                raise ValueError(tr("position symétrique : orientation ambiguë, ajoutez une pierre "
                                    "ou utilisez « Tourner »"))
            if best > max(2, stones // 4):
                raise ValueError(tr("aucune orientation ne correspond à la partie ({n} différences)", n=int(best)))
            changed = cand.corners != self.cal.corners or cand.mirror != self.cal.mirror
            if changed:
                # Le modèle du bois appris suit les intersections physiques.
                if self.det.ref is not None and len(self.det.ref) == len(perm):
                    self.det.ref, self.det.ref_conf = self.det.ref[perm], self.det.ref_conf[perm]
                self.det.last_empty = None
                self.cal = cand
                self.rotated_hint = False
                self._calibration_changed(reset_model=False)
        return {"changed": changed, "differences": best}

    def _calibration_changed(self, reset_model=True):
        if reset_model:
            self.det.reset(self.cal.size)
        self.stab.reset()
        self.empty_mask = None
        self.cfg.update({"calibration": self.cal.to_dict()})
        self.emit("calibration", self.calibration_state())

    def learn_empty(self):
        """À faire sur un goban vide : mémorise la couleur du bois à chaque intersection."""
        frame, _ = self.camera.latest() if self.camera else (None, 0)
        if frame is None or not self.cal.ready:
            raise ValueError(tr("caméra ou calibration manquante"))
        with self.lock:
            warped = self.cal.warp(frame)
            self.det.learn_empty(self.det.features(warped))
            self.stab.reset()
            self._save_model(force=True)

    # ------------------------------------------------------------ boucle de lecture
    def _loop(self):
        stamp = 0.0
        t_fps, n = time.monotonic(), 0
        while not self._stop.is_set():
            cam = self.camera
            if cam is None or not self.cal.ready:
                self._stop.wait(0.2)
                continue
            frame, s = cam.wait(stamp, timeout=1.0)
            if frame is None or s == stamp:
                continue
            stamp = s
            try:
                self._process(frame)
            except Exception as e:
                self.log(f"lecture : {e!r}")
                self._stop.wait(0.5)
            n += 1
            now = time.monotonic()
            if now - t_fps >= 2:
                self.fps, t_fps, n = n / (now - t_fps), now, 0
            # ~12 lectures/s suffisent et laissent le processeur tranquille
            self._stop.wait(max(0.0, 0.08 - (time.monotonic() - now)))

    def _process(self, frame):
        with self.lock:
            size = self.cal.size
            warped = self.cal.warp(frame)
            motion = self.det.motion(warped)
            f = self.det.features(warped)
            grid, _ = self.det.classify(f)
            stable = self.stab.feed(grid, size, motion)
            live = grid_str(grid)
            busy = self.stab.busy
            events = []
            if stable is not None:
                self.det.learn(f, stable.grid)
                self.empty_mask = [c is None for c in stable.grid]
                events = self.tracker.observe(stable)
                self.rotated_hint = self._rotation_hint(stable.grid)
        now = time.monotonic()
        if live != self.live or busy != self.busy or now - self._last_live_emit > 2:
            self.live, self.busy = live, busy
            if now - self._last_live_emit > 0.15 or stable is not None:
                self._last_live_emit = now
                self.emit("live", {"grid": live, "busy": busy})
        if stable is not None:
            self._after_observe(events)
            self._save_model()

    def _after_observe(self, events):
        size = self.tracker.game.size
        for ev in events:
            if ev[0] == "move":
                _, color, pt, applied = ev
                self.emit("move", {"color": color, "move": to_gtp(pt, size), "sgf": to_sgf(pt),
                                   "x": pt[0] if pt else -1, "y": pt[1] if pt else -1,
                                   "applied": applied, "owner": self.owner,
                                   "move_number": len(self.tracker.game.moves)})
            elif ev[0] == "undo":
                self.emit("undo", {"count": ev[1], "owner": self.owner,
                                   "move_number": len(self.tracker.game.moves)})
        self.emit("observed", self.board_state())

    # ------------------------------------------------------------ partie suivie
    def claim(self, owner, game, camera_colors=(B, W), autoplay=True, allow_undo=True, info=None):
        """Une application prend la main : `game` devient la partie attendue sur le goban."""
        with self.lock:
            if game.size != self.cal.size:
                self.cal.set_size(game.size)
                if isinstance(self.camera, SimCamera):
                    self.camera.resize(game.size)
                self._calibration_changed()
            self.owner = owner
            self.info = info or {}
            self.tracker.set_game(game, camera_colors, autoplay, allow_undo)
        self.emit("game", self.board_state())

    def release(self, owner=None):
        """Rend la main à l'enregistrement local (en partant de la position actuelle)."""
        with self.lock:
            if owner and owner != self.owner:
                return
            self.owner = LOCAL
            self.info = {}
        self.new_local_game()

    def push(self, pt, color=None):
        with self.lock:
            self.tracker.push(pt, color)
        self.emit("game", self.board_state())

    def undo(self, count=1):
        with self.lock:
            self.tracker.undo(count)
        self.emit("game", self.board_state())

    def new_local_game(self, from_board=True):
        """Nouvel enregistrement. Les pierres déjà posées (handicap) deviennent la position de départ."""
        with self.lock:
            size = self.cal.size
            setup = {B: [], W: []}
            observed = self.tracker.observed
            if from_board and observed is not None and observed.size == size:
                for pt in observed.points():
                    if observed[pt]:
                        setup[observed[pt]].append(pt)
            first = W if len(setup[B]) > len(setup[W]) else B
            self.owner = LOCAL
            self.info = {"DT": time.strftime("%Y-%m-%d")}
            if setup[B] and not setup[W] and len(setup[B]) > 1:
                self.info["HA"] = len(setup[B])
            self.tracker.set_game(Game(size, setup, first), (B, W), True, True)
        self.emit("game", self.board_state())

    def sgf(self):
        with self.lock:
            return self.tracker.game.to_sgf(self.info)

    def sim_set(self, pt, color):
        """Pose ou retire une pierre sur le goban simulé. `pt` est en coordonnées du joueur : on
        passe par l'image (calibration actuelle) pour retrouver l'intersection du simulateur."""
        cam = self.camera
        if not isinstance(cam, SimCamera):
            raise ValueError(tr("le simulateur n'est pas la caméra active"))
        if self.cal.ready:
            n = self.cal.size
            p = self.cal.to_camera([[(pt[0] + 1) * CELL, (pt[1] + 1) * CELL]])
            grid = np.float32([[1, 1], [n, 1], [n, n], [1, n]])
            H = cv2.getPerspectiveTransform(np.float32(cam.renderer.corners), grid)
            q = cv2.perspectiveTransform(np.float32(p).reshape(-1, 1, 2), H).reshape(2)
            pt = (int(round(q[0])) - 1, int(round(q[1])) - 1)
            if not (0 <= pt[0] < n and 0 <= pt[1] < n):
                raise ValueError(tr("intersection hors du goban simulé (calibration décalée ?)"))
        cam.set_point(pt, color)

    # ------------------------------------------------------------ état
    def board_state(self):
        with self.lock:
            g = self.tracker.game
            n = g.size
            sync = self.tracker.sync
            obs = self.tracker.observed
            return {
                "owner": self.owner,
                "info": self.info,
                "size": n,
                "moves": [[c, to_gtp(p, n)] for c, p in g.moves],
                "expected": grid_str(g.position().grid),
                "observed": grid_str(obs.grid) if obs is not None and obs.size == n else None,
                "to_play": g.to_play(),
                "camera_colors": sorted(self.tracker.camera_colors),
                "pending": [[c, to_gtp(p, n)] for c, p in (self.tracker.pending or [])],
                "sync": {
                    "status": sync.get("status", "unknown"),
                    "missing": [[to_gtp(p, n), c] for p, c in sync.get("missing", [])],
                    "extra": [[to_gtp(p, n), c] for p, c in sync.get("extra", [])],
                    "camera_turn": sync.get("camera_turn", False),
                    "rotated": self.rotated_hint,
                },
            }

    def calibration_state(self):
        frame, _ = self.camera.latest() if self.camera else (None, 0)
        return dict(self.cal.to_dict(), ready=self.cal.ready, overlay=self.cal.overlay(),
                    view_rotation=self.view_rotation,
                    frame=[int(frame.shape[1]), int(frame.shape[0])] if frame is not None else None)

    def state(self):
        cam = self.camera
        return {
            "camera": {"id": self.camera_id, "error": cam.error if cam else "", "fps": round(self.fps, 1),
                       "list": list_cameras(), "sim": isinstance(cam, SimCamera)},
            "calibration": self.calibration_state(),
            "live": {"grid": self.live, "busy": self.busy},
            "board": self.board_state(),
        }

    # ------------------------------------------------------------ images
    def jpeg(self, warped=False, width=960):
        cam = self.camera
        frame, _ = cam.latest() if cam else (None, 0)
        if frame is None:
            return None
        if warped:
            if not self.cal.ready:
                return None
            frame = self.cal.warp(frame)
        elif frame.shape[1] > width:
            frame = cv2.resize(frame, (width, int(frame.shape[0] * width / frame.shape[1])),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    # ------------------------------------------------------------ modèle de couleurs persistant
    def _model_path(self):
        return os.path.join(cfgmod.DATA_DIR, "model.npz")

    def _save_model(self, force=False):
        now = time.monotonic()
        if self.det.ref is None or (not force and now - self._model_saved < 30):
            return
        self._model_saved = now
        try:
            os.makedirs(cfgmod.DATA_DIR, exist_ok=True)
            np.savez(self._model_path(), ref=self.det.ref, conf=self.det.ref_conf, black=self.det.black,
                     white=self.det.white, corners=np.array(self.cal.corners), size=self.cal.size)
        except OSError as e:
            self.log(f"modèle non sauvegardé : {e}")

    def _load_model(self):
        try:
            m = np.load(self._model_path())
            if int(m["size"]) == self.cal.size and np.allclose(m["corners"], self.cal.corners, atol=3):
                self.det.ref, self.det.ref_conf = m["ref"], m["conf"]
                self.det.black, self.det.white = m["black"], m["white"]
        except (OSError, KeyError, ValueError):
            pass
