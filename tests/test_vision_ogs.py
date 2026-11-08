import time

import numpy as np
import pytest

from igogs.config import Config
from igogs.driver import Driver
from igogs.goban import B, W, Position, from_gtp
from igogs.ogs import OGSBridge, game_from_gamedata
from igogs.simulate import Renderer, default_corners
from igogs.vision import Calibration, Detector, order_corners


def random_position(size, density, seed):
    rng = np.random.default_rng(seed)
    p = Position(size)
    for pt in p.points():
        u = rng.random()
        p[pt] = B if u < density / 2 else W if u < density else None
    return p


@pytest.mark.parametrize("size", [9, 13, 19])
@pytest.mark.parametrize("density", [0.0, 0.25, 0.6])
def test_reads_simulated_board(size, density):
    r = Renderer(size, seed=size)
    cal = Calibration(default_corners(), size)
    det = Detector(size)
    p = random_position(size, density, seed=size * 7)
    grid, _ = det.classify(det.features(cal.warp(r.render(p))))
    assert grid == p.grid


def test_refine_snaps_misclicked_corners():
    r = Renderer(19, seed=4)
    frame = r.render(random_position(19, 0.2, 1))
    rng = np.random.default_rng(0)
    cal = Calibration((np.array(default_corners()) + rng.uniform(-9, 9, (4, 2))).tolist(), 19)
    cal.refine(frame)
    assert np.abs(np.array(cal.corners) - default_corners()).max() < 1.5


def test_lighting_change_after_learning():
    r = Renderer(19, seed=2)
    cal = Calibration(default_corners(), 19)
    det = Detector(19)
    p = random_position(19, 0.3, 3)
    f = det.features(cal.warp(r.render(p)))
    grid, _ = det.classify(f)
    det.learn(f, grid)
    for light in (-0.3, 0.3):
        grid, _ = det.classify(det.features(cal.warp(r.render(p, light=light))))
        assert grid == p.grid


def test_corner_order_is_clockwise_from_top_left():
    pts = [[1010, 640], [420, 120], [270, 640], [860, 120]]
    assert order_corners(pts).tolist() == [[420, 120], [860, 120], [1010, 640], [270, 640]]


# ---------------------------------------------------------------------------- OGS
GAMEDATA = {
    "game_id": 123, "width": 9, "height": 9, "phase": "play", "initial_player": "black",
    "handicap": 0, "free_handicap_placement": False, "komi": 6.5, "rules": "japanese",
    "initial_state": {"black": "", "white": ""},
    "moves": [[4, 4, 1000], [2, 2, 1000]],
    "players": {"black": {"id": 1, "username": "moi"}, "white": {"id": 2, "username": "lui"}},
    "black_player_id": 1, "white_player_id": 2,
}


def test_gamedata_handicap_and_passes():
    d = dict(GAMEDATA, handicap=2, free_handicap_placement=True,
             moves=[[2, 2, 0], [-1, -1, 0], [6, 6, 0], [4, 4, 0]])
    game, info = game_from_gamedata(d)
    assert game.moves == [(B, (2, 2)), (B, (6, 6)), (W, (4, 4))]
    assert info["PB"] == "moi" and info["HA"] == 2
    d = dict(GAMEDATA, initial_state={"black": "ccgg", "white": ""}, initial_player="white", moves=[])
    game, _ = game_from_gamedata(d)
    assert game.setup[B] == [(2, 2), (6, 6)] and game.to_play() == W


class FakeClient:
    def __init__(self):
        self.handlers, self.sent, self.user = [], [], {"id": 1, "username": "moi"}
        self.connected, self.clock_offset = True, 0
        self.cfg = {"ogs": {"client_id": "x"}}

    @property
    def logged_in(self):
        return True

    def send(self, cmd, data=None):
        self.sent.append((cmd, data))
        return True

    def connect_game(self, gid):
        self.sent.append(("game/connect", {"game_id": gid}))

    def disconnect_game(self, gid):
        pass

    def emit(self, name, data):
        for h in self.handlers:
            h(name, data)


def test_bridge_plays_physical_move_and_follows_opponent(tmp_path):
    driver = Driver(Config(str(tmp_path / "c.json")), log=lambda m: None)
    client = FakeClient()
    bridge = OGSBridge(client, driver, log=lambda m: None)
    bridge.connect(123)
    client.emit("game/123/gamedata", GAMEDATA)
    assert driver.owner == "ogs" and bridge.my_color == B
    assert driver.tracker.camera_colors == {B}

    # Le goban montre la partie, puis le joueur pose Noir en G7 (6, 2).
    t = driver.tracker
    driver._after_observe(t.observe(t.game.position()))
    seen = t.game.position()
    seen[(6, 2)] = B
    driver._after_observe(t.observe(seen))
    assert client.sent[-1] == ("game/move", {"game_id": 123, "move": "gc"})
    assert t.game.moves[-1] != (B, (6, 2))  # pas joué tant qu'OGS n'a pas confirmé

    client.emit("game/123/move", {"game_id": 123, "move_number": 3, "move": [6, 2, 900]})
    client.emit("game/123/move", {"game_id": 123, "move_number": 3, "move": [6, 2, 900]})  # doublon
    assert t.game.moves[-1] == (B, (6, 2)) and len(t.game.moves) == 3
    assert t.sync["status"] == "ok"

    client.emit("game/123/move", {"game_id": 123, "move_number": 4, "move": [0, 0, 900]})
    assert t.sync["status"] == "mismatch" and t.sync["missing"] == [((0, 0), W)]
    n_sent = len(client.sent)
    seen = t.game.position()
    driver._after_observe(t.observe(seen))  # le joueur pose la pierre de l'adversaire
    assert t.sync["status"] == "ok" and len(client.sent) == n_sent


def test_driver_with_simulated_camera(tmp_path):
    """Bout en bout : caméra simulée → lecture → stabilisation → coups enregistrés."""
    cfg = Config(str(tmp_path / "c.json"))
    cfg.update({"stable_hold": 0.3, "calibration": {"size": 9, "corners": []}})
    driver = Driver(cfg, log=print)
    moves = []
    driver.subscribe(lambda k, d: moves.append(d["move"]) if k == "move" else None)
    driver.set_camera("sim")
    driver.start()
    try:
        deadline = time.time() + 10
        while driver.tracker.observed is None and time.time() < deadline:
            time.sleep(0.05)
        for color, m in ((B, "E5"), (W, "C3"), (B, "G7")):
            driver.sim_set(from_gtp(m, 9), color)
            deadline = time.time() + 10
            while m not in moves and time.time() < deadline:
                time.sleep(0.05)
        assert moves == ["E5", "C3", "G7"]
        sgf = driver.sgf()
        assert ";B[ee];W[cg];B[gc]" in sgf
    finally:
        driver.stop()


@pytest.mark.parametrize("rot,mirror", [(1, False), (2, False), (0, True), (3, True)])
def test_auto_orient_finds_rotation_and_mirror(tmp_path, rot, mirror):
    cfg = Config(str(tmp_path / "c.json"))
    corners = default_corners()
    cfg.update({"stable_hold": 0.0, "calibration": {"size": 9, "mirror": mirror,
                                                     "corners": corners[rot:] + corners[:rot]}})
    driver = Driver(cfg, log=lambda m: None)
    from igogs.goban import Game
    game = Game(9)
    for m in ("C7", "G3", "E5", "D3", "F6"):
        game.play(from_gtp(m, 9))
    driver.claim("test", game)
    frame = Renderer(9, seed=1).render(game.position())  # le goban physique, filmé à l'endroit
    for _ in range(3):
        driver._process(frame)
    assert driver.board_state()["sync"]["status"] == "mismatch"
    assert driver.board_state()["sync"]["rotated"] is True
    assert driver.auto_orient()["changed"] is True
    for _ in range(3):
        driver._process(frame)
    state = driver.board_state()
    assert state["sync"]["status"] == "ok" and not state["sync"]["rotated"]
    assert driver.tracker.game.moves == game.moves  # aucun coup inventé pendant la correction


@pytest.mark.parametrize("view", [0, 90, 180, 270])
def test_corners_follow_player_seat(tmp_path, view):
    """Quel que soit l'ordre des clics, A1 se retrouve en bas à gauche vu depuis la place du joueur."""
    cfg = Config(str(tmp_path / "c.json"))
    cfg.update({"view_rotation": view, "calibration": {"size": 9, "corners": []}})
    driver = Driver(cfg, log=lambda m: None)
    driver.set_corners(default_corners()[::-1])
    from igogs.vision import CELL
    a1, a9 = driver.cal.to_camera([[CELL, 9 * CELL], [CELL, CELL]])
    a = np.radians(view)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    a1v, a9v = rot @ a1, rot @ a9
    assert a1v[1] > a9v[1] + 50  # la colonne A monte de A1 vers A9 dans la vue du joueur
    if view == 0:
        assert driver.cal.corners[3] == default_corners()[3]  # A1 = coin en bas à gauche de l'image
    if view == 180:
        assert driver.cal.corners[3] == default_corners()[1]  # A1 = coin en haut à droite de l'image


def test_changing_seat_renumbers_local_recording(tmp_path):
    """Changer « Ma place » (ou Tourner / Miroir) en enregistrement local renumérote la partie :
    le goban physique n'a pas bougé, la partie reste synchronisée."""
    cfg = Config(str(tmp_path / "c.json"))
    cfg.update({"stable_hold": 0.0, "calibration": {"size": 9, "corners": default_corners()}})
    driver = Driver(cfg, log=lambda m: None)
    physical = Position(9)
    physical[(6, 2)] = B  # G7 vu depuis le bas de l'image
    physical[(3, 1)] = W  # D8
    frame = Renderer(9, seed=2).render(physical)
    driver.new_local_game(from_board=False)
    for _ in range(3):
        driver._process(frame)
    assert driver.board_state()["moves"] == [["B", "G7"], ["W", "D8"]]
    driver.set_view(180)  # le joueur est en face de la caméra
    assert driver.board_state()["moves"] == [["B", "C3"], ["W", "F2"]]
    for _ in range(3):
        driver._process(frame)
    st = driver.board_state()
    assert st["sync"]["status"] == "ok" and len(st["moves"]) == 2
    driver.set_mirror(True)
    driver.rotate()
    for _ in range(3):
        driver._process(frame)
    assert driver.board_state()["sync"]["status"] == "ok" and len(driver.board_state()["moves"]) == 2
