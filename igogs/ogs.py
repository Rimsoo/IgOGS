"""online-go.com : connexion OAuth, temps réel (websocket) et pont avec le goban physique.

Connexion : application OAuth publique (grant « password »). Le mot de passe n'est envoyé qu'une
fois à OGS pour obtenir des jetons ; seuls les jetons sont gardés (config en mode 600).
Temps réel : websocket wss://online-go.com/ — messages JSON `[commande, données, id?]`,
authentification par le `user_jwt` de /api/v1/ui/config (protocole de online-go/goban).
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import websocket

from . import __version__, i18n
from .goban import B, W, Game, from_ogs, from_sgf, to_ogs
from .i18n import tr

UA = f"IgOGS/{__version__} (physical goban driver)"


class OGSError(Exception):
    pass


class OGSClient:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.jwt = ""
        self.user = None  # {"id", "username"}
        self.ws = None
        self.connected = False
        self.games = set()  # parties suivies (ré-abonnées après une coupure)
        self.handlers = []  # fn(nom, données)
        self.lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.clock_offset = 0  # ms : heure serveur - heure locale

    @property
    def base(self):
        return self.cfg["ogs"]["url"].rstrip("/")

    @property
    def logged_in(self):
        return bool(self.cfg["ogs"]["access_token"])

    # ------------------------------------------------------------ HTTP
    def _http(self, path, form=None, token=None, method=None):
        url = path if path.startswith("http") else self.base + path
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", UA)
        req.add_header("Accept", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read()
                return r.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OGSError(tr("online-go.com injoignable ({e})", e=e))

    def _token(self, form):
        form = dict(form, client_id=self.cfg["ogs"]["client_id"])
        status, r = self._http("/oauth2/token/", form)
        if status != 200 or not isinstance(r, dict) or "access_token" not in r:
            why = (r or {}).get("error_description") or (r or {}).get("error") or f"HTTP {status}"
            if why == "invalid_grant" or "credentials" in str(why):
                why = tr("identifiant ou mot de passe incorrect")
            raise OGSError(tr("connexion refusée : {why}", why=why))
        self.cfg.update({"ogs": {"access_token": r["access_token"],
                                 "refresh_token": r.get("refresh_token", ""),
                                 "expires": int(time.time()) + int(r.get("expires_in", 2592000))}})

    def _refresh(self):
        rt = self.cfg["ogs"]["refresh_token"]
        if not rt:
            return False
        try:
            self._token({"grant_type": "refresh_token", "refresh_token": rt})
            return True
        except OGSError as e:
            self.log(f"OGS : jeton non renouvelé ({e})")
            return False

    def api(self, path):
        o = self.cfg["ogs"]
        if o["expires"] and o["expires"] - time.time() < 24 * 3600:
            self._refresh()
        status, r = self._http(path, token=self.cfg["ogs"]["access_token"])
        if status == 401 and self._refresh():
            status, r = self._http(path, token=self.cfg["ogs"]["access_token"])
        if status == 401:
            self.logout(revoke=False)
            raise OGSError(tr("session OGS expirée : reconnectez-vous"))
        if status != 200:
            raise OGSError(f"OGS {path} : HTTP {status}")
        return r

    # ------------------------------------------------------------ session
    def login(self, username, password):
        self._token({"grant_type": "password", "username": username, "password": password,
                     "scope": "read write"})
        self.open_session()

    def open_session(self):
        conf = self.api("/api/v1/ui/config")
        user = (conf or {}).get("user") or {}
        if user.get("anonymous", False) or not conf.get("user_jwt"):
            raise OGSError(tr("OGS n'a pas reconnu la session"))
        self.jwt = conf["user_jwt"]
        self.user = {"id": user.get("id"), "username": user.get("username")}
        self.cfg.update({"ogs": {"username": self.user["username"], "user_id": self.user["id"]}})
        self.log(f"OGS : connecté en tant que {self.user['username']} ({self.user['id']})")
        self._start_socket()

    def logout(self, revoke=True):
        tok = self.cfg["ogs"]["access_token"]
        self._stop.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        if revoke and tok:
            try:
                self._http("/oauth2/revoke_token/", {"token": tok, "client_id": self.cfg["ogs"]["client_id"]})
            except OGSError:
                pass
        self.jwt, self.user, self.connected = "", None, False
        self.games.clear()
        self.cfg.update({"ogs": {"access_token": "", "refresh_token": "", "expires": 0}})

    def active_games(self):
        r = self.api("/api/v1/ui/overview")
        out = []
        me = (self.user or {}).get("id")
        for g in (r or {}).get("active_games", []):
            d = g.get("json") or {}
            out.append({
                "id": g.get("id"),
                "name": g.get("name", ""),
                "size": g.get("width", 19),
                "square": g.get("width") == g.get("height"),
                "black": (g.get("black") or {}).get("username", ""),
                "white": (g.get("white") or {}).get("username", ""),
                "my_turn": (d.get("clock") or {}).get("current_player") == me,
                "speed": (d.get("time_control") or {}).get("speed", ""),
                "moves": len(d.get("moves") or []),
            })
        out.sort(key=lambda g: not g["my_turn"])
        return out

    # ------------------------------------------------------------ websocket
    def _start_socket(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._socket_loop, daemon=True, name="ogs-socket")
        self._thread.start()

    def _socket_loop(self):
        delay = 1
        url = self.base.replace("https://", "wss://").replace("http://", "ws://") + "/"
        while not self._stop.is_set():
            ws = websocket.WebSocketApp(url, header=[f"User-Agent: {UA}"], on_open=self._on_open,
                                        on_message=self._on_message, on_close=self._on_close,
                                        on_error=lambda ws, e: self.log(f"OGS websocket : {e}"))
            self.ws = ws
            t0 = time.monotonic()
            ws.run_forever(ping_interval=0)
            self.connected = False
            self._dispatch("socket", {"connected": False})
            if self._stop.is_set():
                break
            delay = 1 if time.monotonic() - t0 > 30 else min(delay * 2, 30)
            self._stop.wait(delay)

    def _on_open(self, ws):
        self.connected = True
        self.send("authenticate", {"jwt": self.jwt, "client": "igogs", "client_version": __version__,
                                   "user_agent": UA, "language": i18n.LANG})
        self._ping()
        for gid in list(self.games):
            self.send("game/connect", {"game_id": gid, "chat": False})
        self._dispatch("socket", {"connected": True})
        threading.Thread(target=self._ping_loop, args=(ws,), daemon=True).start()

    def _ping(self):
        self.send("net/ping", {"client": int(time.time() * 1000), "drift": 0, "latency": 0})

    def _ping_loop(self, ws):
        while self.ws is ws and self.connected and not self._stop.wait(10):
            self._ping()

    def _on_close(self, ws, *a):
        self.connected = False

    def _on_message(self, ws, text):
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, list) or not msg:
            return
        if not isinstance(msg[0], str):
            if len(msg) >= 3 and msg[2]:
                self.log(f"OGS : erreur {msg[2]}")
            return
        name, data = msg[0], msg[1] if len(msg) > 1 else None
        if name == "net/pong" and isinstance(data, dict) and "server" in data:
            now = int(time.time() * 1000)
            self.clock_offset = data["server"] + (now - data["client"]) // 2 - now
            return
        self._dispatch(name, data)

    def _dispatch(self, name, data):
        for fn in list(self.handlers):
            try:
                fn(name, data)
            except Exception as e:
                self.log(f"OGS : événement {name} non traité ({e!r})")

    def send(self, cmd, data=None):
        ws = self.ws
        if not ws or not self.connected:
            return False
        try:
            with self._send_lock:
                ws.send(json.dumps([cmd, data] if data is not None else [cmd]))
            return True
        except Exception as e:
            self.log(f"OGS : envoi {cmd} impossible ({e})")
            return False

    def connect_game(self, gid):
        self.games.add(gid)
        self.send("game/connect", {"game_id": gid, "chat": False})

    def disconnect_game(self, gid):
        self.games.discard(gid)
        self.send("game/disconnect", {"game_id": gid})


def game_from_gamedata(d):
    """gamedata OGS → (Game, infos SGF). Lève OGSError si la partie n'est pas jouable ici."""
    w, h = d.get("width", 19), d.get("height", 19)
    if w != h or w not in (9, 13, 19):
        raise OGSError(tr("goban {w}x{h} non géré (9, 13 ou 19)", w=w, h=h))
    setup = {B: [], W: []}
    init = d.get("initial_state") or {}
    for color, key in ((B, "black"), (W, "white")):
        s = init.get(key) or ""
        setup[color] = [from_sgf(s[i:i + 2], w) for i in range(0, len(s) - 1, 2)]
    handicap = int(d.get("handicap") or 0)
    free = handicap if d.get("free_handicap_placement") else 0
    game = Game(w, setup, W if d.get("initial_player") == "white" else B, free)
    for mv in d.get("moves") or []:
        pt = from_ogs(mv[0], mv[1])
        if pt is None and game.free_handicap - len(game.moves) > 0:
            continue  # ancienne passe implicite pendant le handicap (ignorée par OGS aussi)
        game.play(pt)
    players = d.get("players") or {}
    pb, pw = players.get("black") or {}, players.get("white") or {}
    info = {
        "GN": d.get("game_name", ""),
        "PB": pb.get("username", ""), "PW": pw.get("username", ""),
        "KM": d.get("komi"), "RU": d.get("rules", ""),
        "HA": handicap or None,
        "PC": f"OGS #{d.get('game_id')}",
        "DT": time.strftime("%Y-%m-%d", time.localtime(d.get("start_time") or time.time())),
        "black_id": d.get("black_player_id") or pb.get("id"),
        "white_id": d.get("white_player_id") or pw.get("id"),
    }
    return game, info


class OGSBridge:
    """Relie une partie OGS en cours au goban physique."""

    def __init__(self, client, driver, log=print, notify=None):
        self.client = client
        self.driver = driver
        self.log = log
        self.notify = notify or (lambda kind, data: None)
        self.game_id = None
        self.phase = None
        self.my_color = None
        self.server_moves = 0
        self.clock = None
        self.result = ""
        self.error = ""
        self.undo_request = None
        client.handlers.append(self._on_ogs)
        driver.subscribe(self._on_driver)

    def state(self):
        c = self.client
        return {
            "logged_in": c.logged_in,
            "user": c.user,
            "connected": c.connected,
            "game_id": self.game_id,
            "phase": self.phase,
            "my_color": self.my_color,
            "clock": self.clock,
            "clock_offset": c.clock_offset,
            "result": self.result,
            "error": self.error,
            "undo_request": self.undo_request,
            "client_id_set": bool(c.cfg["ogs"]["client_id"]),
        }

    def _changed(self):
        self.notify("ogs", self.state())

    # ------------------------------------------------------------ commandes
    def connect(self, gid):
        gid = int(gid)
        if self.game_id and self.game_id != gid:
            self.disconnect()
        self.game_id, self.phase, self.my_color, self.result, self.error = gid, None, None, "", ""
        self.server_moves, self.clock, self.undo_request = 0, None, None
        self.client.connect_game(gid)
        self._changed()

    def disconnect(self):
        if self.game_id:
            self.client.disconnect_game(self.game_id)
        self.game_id = None
        self.phase = None
        self.driver.release("ogs")
        self._changed()

    def pass_move(self):
        self._require_turn()
        self.client.send("game/move", {"game_id": self.game_id, "move": ".."})

    def resign(self):
        if self.game_id:
            self.client.send("game/resign", {"game_id": self.game_id})

    def accept_undo(self):
        if self.game_id and self.undo_request is not None:
            self.client.send("game/undo/accept", {"game_id": self.game_id, "move_number": self.undo_request})
            self.undo_request = None
            self._changed()

    def _require_turn(self):
        if not self.game_id or self.phase != "play":
            raise OGSError(tr("aucune partie OGS en cours"))
        if self.driver.tracker.game.to_play() != self.my_color:
            raise OGSError(tr("ce n'est pas votre tour"))

    # ------------------------------------------------------------ goban → OGS
    def _on_driver(self, kind, data):
        if kind != "move" or data["owner"] != "ogs" or data["applied"] or not self.game_id:
            return
        if self.phase != "play" or data["color"] != self.my_color:
            return
        pt = None if data["x"] < 0 else (data["x"], data["y"])
        self.log(f"OGS : envoi de {data['move']}")
        if not self.client.send("game/move", {"game_id": self.game_id, "move": to_ogs(pt)}):
            self.error = tr("coup non envoyé : connexion à OGS perdue")
            self._changed()

    # ------------------------------------------------------------ OGS → goban
    def _on_ogs(self, name, data):
        if name == "socket":
            self._changed()
            return
        prefix = f"game/{self.game_id}/"
        if not self.game_id or not name.startswith(prefix):
            return
        ev = name[len(prefix):]
        if ev == "gamedata":
            self._load(data)
        elif ev == "move":
            self._move(data)
        elif ev == "clock":
            self.clock = data
            self._changed()
        elif ev == "phase":
            self.phase = data
            if data == "finished":
                self.client.connect_game(self.game_id)  # gamedata final avec le résultat
            self._set_camera_colors()
            self._changed()
        elif ev in ("undo_accepted", "reset"):
            self.client.connect_game(self.game_id)  # repartir d'un gamedata frais
        elif ev == "undo_requested":
            self.undo_request = data if isinstance(data, int) else (data or {}).get("move_number")
            self._changed()
        elif ev == "undo_canceled":
            self.undo_request = None
            self._changed()
        elif ev == "error":
            self.error = data if isinstance(data, str) else json.dumps(data)
            self.log(f"OGS : {self.error}")
            with self.driver.lock:
                self.driver.tracker.pending = None
            self._changed()

    def _load(self, d):
        try:
            game, info = game_from_gamedata(d)
        except OGSError as e:
            self.error = str(e)
            self._changed()
            return
        me = (self.client.user or {}).get("id")
        self.my_color = B if me and me == info["black_id"] else W if me and me == info["white_id"] else None
        self.phase = d.get("phase", "play")
        self.server_moves = len(d.get("moves") or [])
        self.clock = d.get("clock") or self.clock
        self.result = self._result(d, info)
        self.error = ""
        colors = (self.my_color,) if self.my_color and self.phase == "play" else ()
        info = {k: v for k, v in info.items() if not k.endswith("_id")}
        if self.result:
            info["RE"] = self.result
        self.driver.claim("ogs", game, camera_colors=colors, autoplay=False, allow_undo=False, info=info)
        self._changed()

    @staticmethod
    def _result(d, info):
        if d.get("phase") != "finished":
            return ""
        winner = d.get("winner")
        c = "B" if winner == info["black_id"] else "W" if winner == info["white_id"] else ""
        out = str(d.get("outcome") or "")
        if not c:
            return "0" if out else ""
        if "esign" in out:
            return f"{c}+R"
        if "ime" in out:
            return f"{c}+T"
        if "point" in out:
            return f"{c}+{out.split()[0]}"
        return f"{c}+"

    def _set_camera_colors(self):
        colors = (self.my_color,) if self.my_color and self.phase == "play" else ()
        with self.driver.lock:
            self.driver.tracker.camera_colors = set(colors)
            self.driver.tracker.refresh()
        self.driver.emit("game", self.driver.board_state())

    def _move(self, d):
        number = (d or {}).get("move_number", 0)
        mv = (d or {}).get("move") or []
        if number <= self.server_moves or len(mv) < 2:
            return  # déjà reçu (reconnexion)
        self.server_moves = number
        pt = from_ogs(mv[0], mv[1])
        game = self.driver.tracker.game
        if pt is None and game.free_handicap - len(game.moves) > 0:
            return
        try:
            self.driver.push(pt)
        except Exception as e:  # désynchronisé : on recharge la partie complète
            self.log(f"OGS : coup {mv} incohérent ({e}), rechargement")
            self.client.connect_game(self.game_id)
            return
        self.error = ""
        color = game.moves[-1][0] if game.moves else None
        self.notify("ogs-move", {"color": color, "move": self.driver.board_state()["moves"][-1][1],
                                 "mine": color == self.my_color})
        self._changed()
