"""Du plateau vu par la caméra aux coups joués.

- `Stabilizer` : ne laisse passer qu'une observation immobile depuis assez longtemps (pas de main
  au-dessus du plateau, aucune intersection douteuse).
- `Tracker` : compare chaque position stable à la position attendue de la partie et en déduit les
  coups joués (prises comprises), les retours en arrière, ou ce qu'il faut corriger sur le plateau.
"""

import itertools
import time

from .goban import B, W, IllegalMove, Position, other

UNKNOWN = "?"


class Stabilizer:
    def __init__(self, hold=0.8, clock=time.monotonic):
        self.hold = hold
        self.clock = clock
        self.candidate = None
        self.since = 0.0
        self.stable = None
        self.busy = True

    def reset(self):
        self.candidate = None
        self.stable = None
        self.busy = True

    def feed(self, grid, size, motion=False):
        """`grid` : liste B / W / None / UNKNOWN. Retourne la nouvelle Position stable, ou None."""
        now = self.clock()
        if motion or UNKNOWN in grid:
            self.candidate = None
            self.busy = True
            return None
        if grid != self.candidate:
            self.candidate = list(grid)
            self.since = now
            self.busy = True
            return None
        if now - self.since < self.hold:
            return None
        self.busy = False
        pos = Position(size, grid)
        if pos != self.stable:
            self.stable = pos
            return pos
        return None


class Tracker:
    """Suivi d'une partie.

    camera_colors : couleurs jouées sur le goban physique (les deux en enregistrement local, la
    sienne seule sur OGS). Les coups de l'autre couleur arrivent par `push` (adversaire en ligne) et
    doivent être reproduits sur le goban.
    autoplay : si False, un coup vu par la caméra n'est que proposé (`pending`) jusqu'à ce que le
    propriétaire de la partie le confirme par `push` (OGS : le serveur renvoie le coup accepté).
    """

    MAX_SEQUENCE = 4  # coups enchaînés reconnus entre deux observations stables
    MAX_UNDO = 10

    def __init__(self, game, camera_colors=(B, W), autoplay=True, allow_undo=True):
        self.game = game
        self.camera_colors = set(camera_colors)
        self.autoplay = autoplay
        self.allow_undo = allow_undo
        self.observed = None
        self.previous = None  # observation stable précédente
        self.pending = None  # [(couleur, pt)] proposés, en attente de confirmation
        self.sync = {"status": "unknown", "missing": [], "extra": [], "message": ""}

    # ------------------------------------------------------------ partie
    def set_game(self, game, camera_colors=None, autoplay=None, allow_undo=None):
        self.game = game
        if camera_colors is not None:
            self.camera_colors = set(camera_colors)
        if autoplay is not None:
            self.autoplay = autoplay
        if allow_undo is not None:
            self.allow_undo = allow_undo
        self.pending = None
        return self.refresh()

    def push(self, pt, color=None):
        """Un coup confirmé (adversaire en ligne, ou notre coup accepté par le serveur)."""
        self.game.play(pt, color)
        self.pending = None
        return self.refresh()

    def undo(self, count=1):
        self.game.undo(count)
        self.pending = None
        return self.refresh()

    def refresh(self):
        """Recalcule l'état de synchronisation (après un changement de la partie)."""
        if self.observed is None:
            return []
        return self.observe(self.observed, infer=False)

    # ------------------------------------------------------------ observation
    def _tolerated(self):
        """Pierres prises par les derniers coups : on tolère qu'elles soient encore sur le goban."""
        n = len(self.game.moves)
        out = {}
        for k in (n, n - 1):
            if k > 0:
                color = other(self.game.moves[k - 1][0])
                for pt in self.game.captured_by(k):
                    out[pt] = color
        return out

    @staticmethod
    def _matches(expected, observed, tolerated):
        for (pt, e, o) in expected.diff(observed):
            if not (e is None and tolerated.get(pt) == o):
                return False
        return True

    def observe(self, observed, infer=True):
        """Nouvelle position stable. Retourne une liste d'événements :
        ("move", couleur, pt, appliqué) ou ("undo", n)."""
        if infer:
            self.previous, self.observed = self.observed, observed
        else:
            self.observed = observed
        game = self.game
        expected = game.position()
        tolerated = self._tolerated()
        events = []

        if infer and self._matches(expected, observed, tolerated):
            self.pending = None  # coup proposé retiré du goban
        elif infer:
            seq = self._infer(expected, observed, tolerated)
            if not seq:
                self.pending = None
            if seq:
                if self.autoplay:
                    for color, pt in seq:
                        game.play(pt, color)
                        events.append(("move", color, pt, True))
                    self.pending = None
                elif seq != self.pending:
                    self.pending = seq
                    events += [("move", color, pt, False) for color, pt in seq]
            elif self.allow_undo and self._stones_removed():
                # Seulement si des pierres ont été retirées du goban : une position simplement
                # « en retard » sur la partie (coup joué à l'écran, pas encore posé) n'annule rien.
                k = self._find_undo(observed)
                if k:
                    game.undo(k)
                    self.pending = None
                    events.append(("undo", k))
        self._update_sync(observed)
        return events

    def _infer(self, expected, observed, tolerated):
        added = {}
        for pt, e, o in expected.diff(observed):
            if e is None and o is not None and tolerated.get(pt) != o:
                added[pt] = o
            elif e is not None:
                return None  # une pierre attendue a disparu ou changé de couleur
        if not added or len(added) > self.MAX_SEQUENCE:
            return None
        base_n = len(self.game.moves)
        colors = [self.game.to_play(base_n + i) for i in range(len(added))]
        if any(c not in self.camera_colors for c in colors):
            return None
        if sorted(added.values()) != sorted(colors):
            return None
        for order in itertools.permutations(added):
            if any(added[pt] != c for pt, c in zip(order, colors)):
                continue
            pos = expected.copy()
            ok_extra = dict(tolerated)
            try:
                for pt, c in zip(order, colors):
                    for q in pos.play(c, pt):
                        ok_extra[q] = other(c)
            except IllegalMove:
                continue
            if self._matches(pos, observed, ok_extra):
                return list(zip(colors, order))
        return None

    def _stones_removed(self):
        prev = self.previous
        if prev is None or prev.size != self.observed.size:
            return False
        return any(p is not None and o is None for _, p, o in prev.diff(self.observed))

    def _find_undo(self, observed):
        n = len(self.game.moves)
        for k in range(1, min(n, self.MAX_UNDO) + 1):
            if self.game.position(n - k) == observed:
                return k
        return 0

    def _update_sync(self, observed):
        expected = self.game.position()
        tolerated = self._tolerated()
        missing, extra, leftover = [], [], []
        for pt, e, o in expected.diff(observed):
            if e is not None:
                missing.append((pt, e))  # à poser (ou pierre de la mauvaise couleur)
            if o is not None:
                (leftover if e is None and tolerated.get(pt) == o else extra).append((pt, o))
        to_play = self.game.to_play()
        if self.pending:
            status = "pending"
        elif not missing and not extra and not leftover:
            status = "ok"
        elif not missing and not extra:
            status = "remove-captured"
        else:
            status = "mismatch"
        self.sync = {
            "status": status,
            "missing": missing,
            "extra": extra + leftover,
            "to_play": to_play,
            "camera_turn": to_play in self.camera_colors,
        }
