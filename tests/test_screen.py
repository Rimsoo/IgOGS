import cv2
import numpy as np
import pytest

from igogs import screen
from igogs.config import Config
from igogs.driver import Driver
from igogs.goban import B, W, Position, from_gtp
from igogs.screen import ScreenBridge, detect_grid, parse_ppm, read_board

THEMES = {  # (bois, lignes) en BGR : bois foncé à lignes claires, bois clair à lignes noires
    "dark": ((60, 70, 105), (150, 225, 245)),
    "light": ((120, 190, 225), (20, 20, 20)),
}


def render_screen(pos, theme="light", size=(1920, 1080), x0=533, y0=114, step=71):
    """Écran d'un logiciel de go : panneaux, goban 2D net, pierres ombrées, marque du dernier coup."""
    wood, line = THEMES[theme]
    img = np.full((size[1], size[0], 3), (70, 80, 75), np.uint8)
    n = pos.size
    end = (n - 1) * step
    cv2.rectangle(img, (x0 - 72, y0 - 72), (x0 + end + 72, y0 + end + 72), wood, -1)
    cv2.rectangle(img, (1480, 40), (1890, 890), (170, 205, 215), -1)  # panneau de droite
    cv2.rectangle(img, (1480, 40), (1890, 890), (60, 60, 60), 2)
    cv2.putText(img, "1. Black - K10", (1490, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (40, 40, 40), 2)
    for i in range(n):
        cv2.line(img, (x0 + i * step, y0), (x0 + i * step, y0 + end), line, 2)
        cv2.line(img, (x0, y0 + i * step), (x0 + end, y0 + i * step), line, 2)
        cv2.putText(img, "ABCDEFGHJKLMNOPQRST"[i], (x0 + i * step - 8, y0 + end + 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
    r = int(step * 0.47)
    last = None
    for (x, y) in pos.points():
        c = pos[(x, y)]
        if c:
            p = (x0 + x * step, y0 + y * step)
            cv2.circle(img, (p[0] + 4, p[1] + 5), r, tuple(int(v * 0.6) for v in wood), -1, cv2.LINE_AA)
            cv2.circle(img, p, r, (30, 30, 30) if c == B else (238, 238, 238), -1, cv2.LINE_AA)
            last = (p, c)
    if last:
        cv2.circle(img, last[0], int(r * 0.4), (40, 40, 40) if last[1] == W else (230, 230, 230), 4, cv2.LINE_AA)
    return img


@pytest.mark.parametrize("theme", list(THEMES))
@pytest.mark.parametrize("n", [9, 13, 19])
def test_detects_and_reads_screen_board(theme, n):
    rng = np.random.default_rng(n)
    pos = Position(n)
    for pt in pos.points():
        u = rng.random()
        pos[pt] = B if u < 0.2 else W if u < 0.4 else None
    step = 71 if n == 13 else 100 if n == 9 else 48
    img = render_screen(pos, theme, step=step)
    g = detect_grid(img)
    assert g["size"] == n and abs(g["x0"] - 533) <= 1.5 and abs(g["step"] - step) < 0.5
    assert read_board(img, g) == pos.grid


def test_parse_ppm():
    img = np.random.default_rng(0).integers(0, 255, (5, 7, 3), dtype=np.uint8)
    rgb = img[..., ::-1]
    data = b"P6\n# grim\n7 5\n255\n" + rgb.tobytes()
    assert np.array_equal(parse_ppm(data), img)


class FakeMouse:
    def __init__(self):
        self.clicks = []

    def click_at(self, x, y):
        self.clicks.append((x, y))


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    cfg = Config(str(tmp_path / "c.json"))
    cfg.update({"stable_hold": 0.0, "calibration": {"size": 13, "corners": []}})
    driver = Driver(cfg, log=lambda m: None)
    shown = {"pos": Position(13)}
    monkeypatch.setattr(screen, "find_window", lambda p: {"at": (1920, 0), "size": (1920, 1080), "title": "Go"})
    monkeypatch.setattr(screen, "capture", lambda win: render_screen(shown["pos"]))
    events = []
    br = ScreenBridge(cfg, driver, log=lambda m: None, notify=lambda k, d: events.append((k, d)))
    br.mouse = FakeMouse()
    br.POLL = 3600  # pas de boucle de fond : les lectures sont faites à la main
    br.start(B)
    return br, driver, shown, events


def play(pos, *moves):
    p = pos.copy()
    for color, m in moves:
        p.play(color, from_gtp(m, 13))
    return p


def step(br, shown):
    br._sync(read_board(render_screen(shown["pos"]), br.grid))


def test_my_move_is_clicked_then_opponent_reply_is_followed(bridge):
    br, driver, shown, events = bridge
    # Je pose K10 sur le goban : l'événement « move » proposé déclenche un clic dans le jeu.
    driver._after_observe([("move", B, from_gtp("K10", 13), False)])
    (x, y), = br.mouse.clicks
    assert abs(x - (1920 + 533 + 9 * 71)) <= 2 and abs(y - (114 + 3 * 71)) <= 2
    # Le jeu affiche mon coup et, aussitôt, la réponse de l'IA.
    shown["pos"] = play(shown["pos"], (B, "K10"), (W, "C4"))
    step(br, shown)
    assert driver.board_state()["moves"] == [["B", "K10"], ["W", "C4"]]
    spoken = [d for k, d in events if k == "screen-move" and not d["mine"]]
    assert spoken == [{"color": W, "move": "C4", "mine": False}]
    assert br.sent is None


def test_capture_and_pass_are_inferred(bridge):
    br, driver, shown, _ = bridge
    for mv in [(B, "B1"), (W, "A1"), (B, "A2")]:  # Noir prend A1 (une lecture après chaque coup)
        shown["pos"] = play(shown["pos"], mv)
        step(br, shown)
    assert [m for _, m in driver.board_state()["moves"]] == ["B1", "A1", "A2"]
    assert driver.tracker.game.position() == shown["pos"]
    # Blanc passe (rien ne change à l'écran), puis Noir joue : une passe est insérée.
    shown["pos"] = play(shown["pos"], (B, "G7"))
    step(br, shown)
    assert driver.board_state()["moves"][-2:] == [["W", "pass"], ["B", "G7"]]


def test_undo_in_game_resyncs(bridge, monkeypatch):
    br, driver, shown, _ = bridge
    shown["pos"] = play(shown["pos"], (B, "D4"), (W, "K10"))
    step(br, shown)
    shown["pos"] = play(Position(13), (B, "D4"), (W, "J10"))  # annulation puis autre coup
    clock = [100.0]
    monkeypatch.setattr(screen.time, "monotonic", lambda: clock[0])
    step(br, shown)
    clock[0] += 3
    step(br, shown)
    assert driver.tracker.game.position() == shown["pos"]
