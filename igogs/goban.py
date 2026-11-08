"""Règles du Go et conversions de coordonnées.

Coordonnées internes : (x, y), x = colonne de gauche à droite, y = ligne de haut en bas,
toutes deux de 0 à size-1 (même convention qu'OGS et SGF). Un coup « passe » vaut None.
"""

from .i18n import tr

B, W = "B", "W"
LETTERS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"  # GTP : pas de I


def other(color):
    return W if color == B else B


def to_gtp(pt, size):
    if pt is None:
        return "pass"
    x, y = pt
    return f"{LETTERS[x]}{size - y}"


def from_gtp(s, size):
    s = s.strip().upper()
    if s in ("PASS", ""):
        return None
    x = LETTERS.index(s[0])
    y = size - int(s[1:])
    if not (0 <= x < size and 0 <= y < size):
        raise ValueError(tr("coordonnée hors du plateau : {s}", s=s))
    return (x, y)


def to_sgf(pt):
    if pt is None:
        return ""
    return chr(97 + pt[0]) + chr(97 + pt[1])


def from_sgf(s, size=19):
    if not s or (s == "tt" and size <= 19):
        return None
    return (ord(s[0]) - 97, ord(s[1]) - 97)


def to_ogs(pt):
    """Encodage des coups OGS (« dd », passe = « .. »)."""
    return ".." if pt is None else to_sgf(pt)


def from_ogs(x, y):
    return None if x < 0 or y < 0 else (x, y)


def star_points(size):
    if size < 7:
        return []
    e = 2 if size < 13 else 3
    far = size - 1 - e
    pts = [(e, e), (far, e), (e, far), (far, far)]
    if size % 2 == 1:
        mid = size // 2
        pts.append((mid, mid))
        if size >= 13:
            pts += [(mid, e), (mid, far), (e, mid), (far, mid)]
    return pts


class IllegalMove(Exception):
    pass


class Position:
    """Un plateau : grille de B / W / None, avec prises et ko simple."""

    __slots__ = ("size", "grid", "ko")

    def __init__(self, size=19, grid=None, ko=None):
        self.size = size
        self.grid = list(grid) if grid is not None else [None] * (size * size)
        self.ko = ko

    @classmethod
    def from_rows(cls, rows):
        """Depuis une liste de chaînes (« X » noir, « O » blanc, « . » vide) — pratique en test."""
        size = len(rows)
        grid = []
        for row in rows:
            row = row.replace(" ", "")
            grid += [B if c in "XB" else W if c in "OW" else None for c in row]
        return cls(size, grid)

    def copy(self):
        return Position(self.size, self.grid, self.ko)

    def __getitem__(self, pt):
        return self.grid[pt[1] * self.size + pt[0]]

    def __setitem__(self, pt, color):
        self.grid[pt[1] * self.size + pt[0]] = color

    def __eq__(self, other):
        return isinstance(other, Position) and self.size == other.size and self.grid == other.grid

    def __hash__(self):
        return hash((self.size, tuple(self.grid)))

    def points(self):
        n = self.size
        return ((x, y) for y in range(n) for x in range(n))

    def stones(self, color=None):
        return [p for p in self.points() if self[p] is not None and (color is None or self[p] == color)]

    def neighbors(self, pt):
        x, y = pt
        n = self.size
        if x > 0:
            yield (x - 1, y)
        if x < n - 1:
            yield (x + 1, y)
        if y > 0:
            yield (x, y - 1)
        if y < n - 1:
            yield (x, y + 1)

    def group(self, pt):
        """Retourne (pierres du groupe, libertés)."""
        color = self[pt]
        stones, libs, todo = {pt}, set(), [pt]
        while todo:
            p = todo.pop()
            for q in self.neighbors(p):
                c = self[q]
                if c is None:
                    libs.add(q)
                elif c == color and q not in stones:
                    stones.add(q)
                    todo.append(q)
        return stones, libs

    def play(self, color, pt):
        """Joue un coup sur place. Retourne l'ensemble des pierres capturées.
        Lève IllegalMove (occupé, suicide, ko)."""
        if pt is None:
            self.ko = None
            return set()
        x, y = pt
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IllegalMove(tr("hors du plateau"))
        if self[pt] is not None:
            raise IllegalMove(tr("intersection occupée"))
        if pt == self.ko:
            raise IllegalMove(tr("ko"))
        self[pt] = color
        captured = set()
        for q in self.neighbors(pt):
            if self[q] == other(color):
                stones, libs = self.group(q)
                if not libs:
                    captured |= stones
        for q in captured:
            self[q] = None
        stones, libs = self.group(pt)
        if not libs:
            self[pt] = None
            for q in captured:  # impossible en pratique (une prise donne une liberté), par sécurité
                self[q] = other(color)
            raise IllegalMove(tr("suicide"))
        # Ko : une seule pierre prise, par une pierre seule qui n'a plus qu'une liberté.
        self.ko = next(iter(captured)) if len(captured) == 1 and len(stones) == 1 and len(libs) == 1 else None
        return captured

    def diff(self, other):
        """Points où les deux plateaux diffèrent : liste de (pt, ici, là-bas)."""
        n = self.size
        return [((i % n, i // n), a, b) for i, (a, b) in enumerate(zip(self.grid, other.grid)) if a != b]

    def ascii(self):
        return "\n".join(
            " ".join({B: "X", W: "O", None: "."}[self[(x, y)]] for x in range(self.size))
            for y in range(self.size))


class Game:
    """Partie : position initiale (pierres posées) + suite de coups, avec la règle de
    couleur d'OGS pour le handicap libre (Noir pose ses pierres d'affilée)."""

    def __init__(self, size=19, setup=None, first=B, free_handicap=0):
        self.size = size
        self.setup = {B: list((setup or {}).get(B, [])), W: list((setup or {}).get(W, []))}
        self.first = first
        self.free_handicap = free_handicap
        self.moves = []  # [(couleur, pt | None)]
        self._positions = None
        self._captures = None

    def copy(self):
        g = Game(self.size, self.setup, self.first, self.free_handicap)
        g.moves = list(self.moves)
        return g

    def initial(self):
        p = Position(self.size)
        for color in (B, W):
            for pt in self.setup[color]:
                p[pt] = color
        return p

    def _replay(self):
        if self._positions is None:
            p = self.initial()
            positions, captures = [p.copy()], [set()]
            for color, pt in self.moves:
                captures.append(p.play(color, pt))
                positions.append(p.copy())
            self._positions, self._captures = positions, captures
        return self._positions

    def position(self, n=None):
        """Position après n coups (par défaut : tous)."""
        ps = self._replay()
        return ps[len(ps) - 1 if n is None else n].copy()

    def captured_by(self, n):
        """Pierres capturées par le n-ième coup (1 = premier coup)."""
        self._replay()
        return set(self._captures[n]) if 0 < n < len(self._captures) else set()

    def to_play(self, n=None):
        """Couleur du coup n (0 = premier). Les coups déjà joués font foi (un SGF peut enchaîner
        des pierres de même couleur) ; au-delà, on alterne, avec la règle d'OGS pour le handicap
        libre (handicapMovesLeft : Noir pose ses pierres d'affilée)."""
        n = len(self.moves) if n is None else n
        color = self.first
        for i in range(n):
            played = self.moves[i][0] if i < len(self.moves) else color
            color = played if self.free_handicap - i >= 2 else other(played)
        return color

    def play(self, pt, color=None):
        color = color or self.to_play()
        pos = self.position()
        pos.play(color, pt)  # valide (lève IllegalMove)
        self.moves.append((color, pt))
        self._positions = None
        return color

    def undo(self, count=1):
        count = min(count, len(self.moves))
        del self.moves[len(self.moves) - count:]
        self._positions = None
        return count

    def to_sgf(self, info=None):
        info = info or {}
        props = [f"GM[1]FF[4]CA[UTF-8]AP[IgOGS]SZ[{self.size}]"]
        for k in ("PB", "PW", "BR", "WR", "DT", "RE", "KM", "GN", "RU", "HA", "PC"):
            if info.get(k) not in (None, ""):
                props.append(f"{k}[{_sgf_escape(str(info[k]))}]")
        if self.setup[B]:
            props.append("AB" + "".join(f"[{to_sgf(p)}]" for p in self.setup[B]))
        if self.setup[W]:
            props.append("AW" + "".join(f"[{to_sgf(p)}]" for p in self.setup[W]))
        if self.first == W and (self.setup[B] or self.setup[W]):
            props.append("PL[W]")
        body = "".join(f";{c}[{to_sgf(p)}]" for c, p in self.moves)
        return f"(;{''.join(props)}{body})\n"


def _sgf_escape(s):
    return s.replace("\\", "\\\\").replace("]", "\\]")
