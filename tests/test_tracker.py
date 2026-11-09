from igogs.goban import B, W, Game, IllegalMove, Position, from_gtp
from igogs.tracker import UNKNOWN, Stabilizer, Tracker

import pytest


def pos_after(game, *extra):
    """Position attendue + pierres posées à la main : extra = (couleur, "D4")."""
    p = game.position()
    for c, m in extra:
        p[from_gtp(m, game.size)] = c
    return p


def test_capture_and_ko():
    p = Position.from_rows([
        ". X O .",
        "X O . O",
        ". X O .",
        ". . . .",
    ])
    captured = p.play(B, (2, 1))
    assert captured == {(1, 1)}
    assert p.ko == (1, 1)
    with pytest.raises(IllegalMove):
        p.play(W, (1, 1))  # reprise immédiate du ko interdite


def test_suicide_illegal():
    p = Position.from_rows([". X .", "X . .", ". . ."])
    with pytest.raises(IllegalMove):
        p.play(W, (0, 0))


def test_free_handicap_colors_follow_ogs():
    g = Game(19, first=B, free_handicap=3)
    assert [g.to_play(i) for i in range(5)] == [B, B, B, W, B]


def test_single_move_detected():
    g = Game(9)
    t = Tracker(g)
    ev = t.observe(pos_after(g, (B, "E5")))
    assert ev == [("move", B, (4, 4), True)]
    assert t.sync["status"] == "ok"
    assert g.to_play() == W


def test_wrong_color_is_not_a_move():
    g = Game(9)
    t = Tracker(g)
    assert t.observe(pos_after(g, (W, "E5"))) == []
    assert t.sync["status"] == "mismatch"
    assert g.moves == []


def test_two_moves_between_observations():
    g = Game(9)
    t = Tracker(g)
    ev = t.observe(pos_after(g, (B, "E5"), (W, "C3")))
    assert [e[1:3] for e in ev] == [(B, (4, 4)), (W, (2, 6))]


def test_capture_with_stones_left_on_board_then_removed():
    # Blanc E5 entouré par Noir sur trois côtés ; Noir E6 le capture.
    pts = lambda *ms: [from_gtp(m, 9) for m in ms]
    g = Game(9, setup={B: pts("D5", "F5", "E4"), W: pts("E5")})
    t = Tracker(g)
    t.observe(g.position())
    seen = pos_after(g, (B, "E6"))  # le joueur n'a pas encore retiré la pierre prise
    ev = t.observe(seen)
    assert ev == [("move", B, from_gtp("E6", 9), True)]
    assert t.sync["status"] == "remove-captured"
    assert t.sync["extra"] == [(from_gtp("E5", 9), W)]
    t.observe(g.position())
    assert t.sync["status"] == "ok"


def test_playing_in_the_hole_left_by_a_capture():
    """Noir A3 prend Blanc A1-A2 ; les pierres sont retirées, puis Blanc rejoue A1 dans le trou :
    c'est un coup, pas une pierre prise oubliée sur le goban."""
    pts = lambda *ms: [from_gtp(m, 9) for m in ms]
    g = Game(9, setup={B: pts("B1", "B2"), W: pts("A1", "A2")}, first=B)
    t = Tracker(g)
    t.observe(g.position())
    seen = pos_after(g, (B, "A3"))  # pierres prises encore sur le goban
    t.observe(seen)
    assert g.moves == [(B, from_gtp("A3", 9))] and t.sync["status"] == "remove-captured"
    t.observe(g.position())  # retirées
    assert t.sync["status"] == "ok"
    ev = t.observe(pos_after(g, (W, "A1")))
    assert ev == [("move", W, from_gtp("A1", 9), True)]
    assert t.sync["status"] == "ok"


def test_captured_stones_never_removed_are_still_tolerated():
    pts = lambda *ms: [from_gtp(m, 9) for m in ms]
    g = Game(9, setup={B: pts("B1", "B2"), W: pts("A1", "A2")}, first=B)
    t = Tracker(g)
    t.observe(g.position())
    t.observe(pos_after(g, (B, "A3")))
    assert t.observe(pos_after(g, (W, "A1"), (W, "A2"))) == []  # pas de coup inventé
    assert t.sync["status"] == "remove-captured"


def test_undo_by_removing_last_stone():
    g = Game(9)
    t = Tracker(g)
    t.observe(pos_after(g, (B, "E5")))
    t.observe(pos_after(g, (W, "C3")))
    assert len(g.moves) == 2
    ev = t.observe(Position(9))
    assert ev == [("undo", 2)]
    assert g.moves == []


def test_remote_owner_flow_pending_then_confirmed():
    g = Game(9)
    t = Tracker(g, camera_colors=(B,), autoplay=False, allow_undo=False)
    ev = t.observe(pos_after(g, (B, "E5")))
    assert ev == [("move", B, (4, 4), False)]
    assert t.sync["status"] == "pending" and g.moves == []
    # Même observation : pas de second envoi.
    assert t.observe(pos_after(g, (B, "E5"))) == []
    t.push((4, 4))  # le serveur confirme
    assert t.sync["status"] == "ok"
    # Coup de l'adversaire reçu : il manque sur le goban.
    t.push(from_gtp("C3", 9))
    assert t.sync["status"] == "mismatch"
    assert t.sync["missing"] == [((2, 6), W)]
    # Le joueur le pose : synchro, et aucun coup n'est inventé.
    assert t.observe(g.position()) == []
    assert t.sync["status"] == "ok"


def test_remote_owner_never_plays_opponent_color():
    g = Game(9)
    t = Tracker(g, camera_colors=(W,), autoplay=False, allow_undo=False)
    assert t.observe(pos_after(g, (B, "E5"))) == []  # c'est à l'adversaire (Noir) de jouer en ligne


def test_stabilizer_waits_for_stillness():
    now = [0.0]
    s = Stabilizer(hold=0.5, clock=lambda: now[0])
    grid = [None] * 4
    assert s.feed(grid, 2) is None
    now[0] = 0.3
    assert s.feed(grid, 2) is None
    now[0] = 0.6
    assert s.feed(grid, 2) == Position(2)
    now[0] = 0.7
    assert s.feed(grid, 2) is None  # inchangé : rien de nouveau
    assert s.feed([UNKNOWN, None, None, None], 2) is None
    now[0] = 0.8
    g2 = [B, None, None, None]
    assert s.feed(g2, 2, motion=True) is None
    assert s.feed(g2, 2) is None
    now[0] = 1.4
    assert s.feed(g2, 2).grid == g2


def test_board_lagging_behind_the_game_is_not_an_undo():
    g = Game(9)
    t = Tracker(g)
    t.observe(Position(9))
    t.set_game(Game(9))
    g2 = t.game
    g2.play(from_gtp("E5", 9))
    g2.play(from_gtp("C3", 9))
    t.refresh()
    assert t.sync["status"] == "mismatch"
    # Le joueur pose la première pierre affichée : c'est la position après 1 coup, mais rien
    # n'a été retiré du goban → pas d'annulation, on attend la suite.
    assert t.observe(pos_after(Game(9), (B, "E5"))) == []
    assert len(g2.moves) == 2
