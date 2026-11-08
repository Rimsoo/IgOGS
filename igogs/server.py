"""Serveur local (127.0.0.1) : interface web, API JSON, événements SSE et flux vidéo.

C'est aussi le « protocole driver » que d'autres applications utilisent pour se brancher au
goban physique (voir PROTOCOL.md) : GET /events pour recevoir les coups, POST /api/claim pour
indiquer la partie attendue.
"""

import json
import os
import queue
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, i18n
from .config import Config
from .driver import Driver
from .goban import B, W, Game, IllegalMove, from_gtp
from .i18n import set_lang, tr
from .ogs import OGSBridge, OGSClient, OGSError

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PROTOCOL = 1
LOGS = []


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOGS.append(line)
    del LOGS[:-300]


class Hub:
    """Diffusion des événements aux clients SSE."""

    def __init__(self):
        self.queues = set()
        self.lock = threading.Lock()

    def add(self):
        q = queue.Queue(maxsize=500)
        with self.lock:
            self.queues.add(q)
        return q

    def remove(self, q):
        with self.lock:
            self.queues.discard(q)

    def send(self, kind, data):
        msg = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()
        with self.lock:
            for q in list(self.queues):
                try:
                    q.put_nowait(msg)
                except queue.Full:  # client trop lent : on le laisse tomber
                    self.queues.discard(q)


class App:
    def __init__(self, cfg=None, port=None):
        self.cfg = cfg or Config()
        self.port = port or self.cfg["port"]
        set_lang(self.cfg["ui"]["lang"])
        self.hub = Hub()
        self.stopping = False
        self.server = None
        self.tray = None
        self.restart_requested = False
        self.boot = os.urandom(4).hex()  # change à chaque lancement : l'interface se recharge
        self.driver = Driver(self.cfg, log=log)
        # L'état complet du driver est enrichi (OGS, version) avant d'être diffusé.
        self.driver.subscribe(lambda kind, data: self.hub.send(kind, self.state() if kind == "state" else data))
        self.ogs = OGSClient(self.cfg, log=log)
        self.bridge = OGSBridge(self.ogs, self.driver, log=log, notify=self.hub.send)

    def start(self):
        self.driver.start()
        if self.ogs.logged_in:
            threading.Thread(target=self._resume_ogs, daemon=True).start()

    def _resume_ogs(self):
        try:
            self.ogs.open_session()
        except OGSError as e:
            log(f"OGS : {e}")
        self.hub.send("ogs", self.bridge.state())

    def state(self):
        return dict(self.driver.state(), ogs=self.bridge.state(), version=__version__, boot=self.boot,
                    lang=i18n.LANG)

    def shutdown(self, reason="", restart=False):
        """Arrête (ou relance) IgOGS : caméra libérée, connexion OGS fermée."""
        if self.stopping:
            return
        self.restart_requested = restart
        log(("Redémarrage" if restart else "Arrêt") + (f" ({reason})" if reason else ""))
        self.hub.send("shutdown", {"restart": restart})

        def later():
            time.sleep(0.4)  # laisse partir la réponse et l'événement
            self.stopping = True
            if self.server:
                self.server.shutdown()

        threading.Thread(target=later, daemon=True).start()

    # ------------------------------------------------------------ API POST
    def api(self, path, body):
        d = self.driver
        size = d.tracker.game.size
        if path == "/api/camera":
            d.set_camera(str(body.get("id", "")))
        elif path == "/api/calibration":
            d.set_corners(body["corners"], keep_order=bool(body.get("keep_order")))
        elif path == "/api/calibration/rotate":
            d.rotate()
        elif path == "/api/calibration/mirror":
            d.set_mirror(body.get("mirror"))
        elif path == "/api/calibration/size":
            d.set_size(body["size"])
        elif path == "/api/calibration/refine":
            return {"shift": d.refine()}
        elif path == "/api/lang":
            lang = body.get("lang")
            if lang not in i18n.LANGS:
                raise ValueError("lang : fr | en")
            self.cfg.update({"ui": {"lang": lang}})
            set_lang(lang)
            self.hub.send("state", self.state())  # noms des caméras… dans la nouvelle langue
        elif path == "/api/shutdown":
            self.shutdown("interface")
        elif path == "/api/restart":
            self.shutdown("interface", restart=True)
        elif path == "/api/view":
            d.set_view(body.get("rotation", 0))
        elif path == "/api/calibration/orient":
            return self.driver.auto_orient()
        elif path == "/api/calibration/empty":
            d.learn_empty()
        elif path == "/api/local/new":
            if d.owner != "local":
                raise ValueError(tr("une partie est déjà suivie par {owner}", owner=d.owner))
            d.new_local_game(from_board=body.get("from_board", True))
        elif path == "/api/local/undo":
            self._local_only()
            d.undo(int(body.get("count", 1)))
        elif path == "/api/local/pass":
            self._local_only()
            d.push(None)
        elif path == "/api/sim":
            color = body.get("color")
            d.sim_set(from_gtp(body["move"], d.cal.size), color if color in (B, W) else None)
        # -------------------------------------------------- OGS
        elif path == "/api/ogs/login":
            self.ogs.login(body["username"].strip(), body["password"])
            self.hub.send("ogs", self.bridge.state())
        elif path == "/api/ogs/logout":
            self.bridge.disconnect()
            self.ogs.logout()
            self.hub.send("ogs", self.bridge.state())
        elif path == "/api/ogs/connect":
            if d.owner not in ("local", "ogs"):
                raise ValueError(tr("le goban est utilisé par {owner}", owner=d.owner))
            self.bridge.connect(body["game_id"])
        elif path == "/api/ogs/disconnect":
            self.bridge.disconnect()
        elif path == "/api/ogs/pass":
            self.bridge.pass_move()
        elif path == "/api/ogs/resign":
            self.bridge.resign()
        elif path == "/api/ogs/undo-accept":
            self.bridge.accept_undo()
        # -------------------------------------------------- applications externes
        elif path == "/api/claim":
            return self._claim(body)
        elif path == "/api/release":
            d.release(body.get("owner"))
        elif path == "/api/push":
            self._owner_only(body)
            d.push(from_gtp(body["move"], size), body.get("color"))
        elif path == "/api/gosensei/send":
            return self._send_to_gosensei()
        else:
            raise KeyError(path)
        return {"ok": True}

    def _local_only(self):
        if self.driver.owner != "local":
            raise ValueError(tr("partie gérée par {owner}", owner=self.driver.owner))

    def _owner_only(self, body):
        owner = body.get("owner")
        if not owner or owner != self.driver.owner:
            raise ValueError(tr("cette application n'a pas la main sur le goban (POST /api/claim)"))

    def _claim(self, body):
        owner = str(body.get("owner") or "").strip()
        if not owner or owner in ("local", "ogs"):
            raise ValueError(tr("owner : nom de l'application (ex. « go-sensei »)"))
        d = self.driver
        if d.owner == "ogs" and self.bridge.game_id and not body.get("force"):
            raise ValueError(tr("une partie OGS est en cours sur le goban"))
        size = int(body.get("size", 19))
        setup = {c: [from_gtp(m, size) for m in (body.get("setup") or {}).get(c, [])] for c in (B, W)}
        game = Game(size, setup, body.get("first", B) if body.get("first") in (B, W) else B,
                    int(body.get("free_handicap", 0)))
        try:
            for item in body.get("moves") or []:
                color, mv = (item[0], item[1]) if isinstance(item, (list, tuple)) else (None, item)
                game.play(from_gtp(mv, size), color if color in (B, W) else None)
        except (IllegalMove, ValueError) as e:
            raise ValueError(tr("suite de coups invalide : {e}", e=e))
        colors = [c for c in body.get("camera_colors", [B, W]) if c in (B, W)]
        d.claim(owner, game, colors, bool(body.get("autoplay", True)), bool(body.get("allow_undo", True)),
                body.get("info") or {})
        return {"ok": True, "board": d.board_state()}

    def _send_to_gosensei(self):
        """Envoie la partie enregistrée à Go Sensei (import SGF) pour l'analyser."""
        url = self.cfg["gosensei"]["url"].rstrip("/") + "/api/import-sgf"
        name = "goban-" + time.strftime("%Y%m%d-%H%M")
        payload = json.dumps({"content": self.driver.sgf(), "name": name}).encode()
        req = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json", "X-Go-Sensei": "1"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return {"ok": True, "gosensei": json.loads(r.read() or b"{}")}
        except urllib.error.HTTPError as e:
            raise ValueError(tr("Go Sensei a refusé la partie (HTTP {code})", code=e.code))
        except (urllib.error.URLError, OSError):
            raise ValueError(tr("Go Sensei ne répond pas : est-il lancé ?"))


def make_handler(app):
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _empty(self, code):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, code, data, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _trusted(self):
            """Refuse les requêtes venant d'autres sites (CSRF, DNS rebinding)."""
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in (f"http://{h}" for h in allowed_hosts)

        def do_GET(self):
            if not self._trusted():
                return self._empty(403)
            p = self.path.split("?")[0]
            if p == "/events":
                return self._sse()
            if p == "/video.mjpg":
                return self._mjpeg()
            if p in ("/frame.jpg", "/warped.jpg"):
                jpg = app.driver.jpeg(warped=p == "/warped.jpg")
                return self._send(200, jpg, "image/jpeg") if jpg else self._empty(404)
            if p == "/api/game.sgf":
                return self._send(200, app.driver.sgf().encode(), "application/x-go-sgf; charset=utf-8")
            if p == "/api/ogs/games":
                try:
                    return self._json({"games": app.ogs.active_games()})
                except OGSError as e:
                    return self._json({"error": str(e)}, 400)
            routes = {
                "/api/state": app.state,
                "/api/board": app.driver.board_state,
                "/api/logs": lambda: LOGS,
                "/api/status": lambda: {"app": "igogs", "version": __version__, "protocol": PROTOCOL},
            }
            if p in routes:
                return self._json(routes[p]())
            f = os.path.normpath(os.path.join(STATIC, "index.html" if p == "/" else p.lstrip("/")))
            if not f.startswith(STATIC + os.sep) or not os.path.isfile(f):
                return self._empty(404)
            with open(f, "rb") as fh:
                data = fh.read()
            ext = os.path.splitext(f)[1]
            ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                     ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
            self._send(200, data, ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))

        def do_POST(self):
            if not self._trusted():
                return self._empty(403)
            # Un autre site ne peut pas ajouter cet en-tête sans CORS (que nous n'autorisons pas).
            if self.headers.get("X-IgOGS") != "1":
                return self._empty(403)
            p = self.path.split("?")[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"error": "JSON invalide"}, 400)
            try:
                return self._json(app.api(p, body))
            except KeyError as e:
                return self._json({"error": f"inconnu : {e}"}, 404)
            except (ValueError, OGSError, IllegalMove) as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:
                log(f"API {p} : {e!r}")
                return self._json({"error": str(e)}, 500)

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = app.hub.add()
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(app.state(), ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                while not app.stopping:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = b": ping\n\n"
                    self.wfile.write(msg)
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                app.hub.remove(q)

        def _mjpeg(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            warped = "warped" in self.path
            try:
                while not app.stopping:
                    jpg = app.driver.jpeg(warped=warped)
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.flush()
                    time.sleep(0.1)
            except OSError:
                pass

    return Handler


def running_instance(port):
    """Vrai si un IgOGS répond déjà sur ce port."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read()).get("app") == "igogs"
    except (OSError, ValueError):
        return False


def stop_instance(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-IgOGS": "1"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except OSError:
        return False


def host_env():
    """Environnement pour lancer un programme du système (navigateur) depuis l'AppImage : on
    retire ce qui pointe vers l'image (Python, bibliothèques embarquées). Sans AppImage : inchangé."""
    env = dict(os.environ)
    appdir = env.get("APPDIR")
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"):
        env.pop(k, None)
    if appdir:
        for k, v in list(env.items()):
            if k.endswith("_ORIG"):
                continue
            if appdir in v and k not in ("APPDIR", "APPIMAGE", "OWD", "ARGV0"):
                parts = [p for p in v.split(":") if appdir not in p]
                if parts:
                    env[k] = ":".join(parts)
                else:
                    env.pop(k)
        for k, v in list(env.items()):
            if k.endswith("_ORIG"):
                env[k[:-5]] = v
    return env


def open_browser_at(url):
    import subprocess
    try:
        subprocess.Popen(["xdg-open", url], env=host_env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:  # pas de xdg-open (autre système)
        import webbrowser
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()


def run(port=None, open_browser=True, tray=True):
    port = port or Config()["port"]
    url = f"http://127.0.0.1:{port}/"
    if running_instance(port):  # déjà lancé : on rouvre simplement l'interface
        log(f"IgOGS tourne déjà : {url}")
        if open_browser:
            open_browser_at(url)
        return
    app = App(port=port)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", app.port), make_handler(app))
    except OSError as e:
        log(f"Port {app.port} indisponible ({e})")
        sys.exit(1)
    server.daemon_threads = True
    app.server = server
    app.start()
    log(f"IgOGS {__version__} : {url}")
    if tray:
        from .tray import Tray
        app.tray = Tray(on_open=lambda: open_browser_at(url), on_quit=lambda: app.shutdown("icône"), log=log)
        app.tray.start()
    if open_browser:
        threading.Timer(0.5, open_browser_at, args=(url,)).start()
    signal.signal(signal.SIGTERM, lambda *a: app.shutdown("signal"))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stopping = True
        if app.tray:
            app.tray.stop()
        app.driver.stop()
        if app.restart_requested:
            log("Redémarrage d'IgOGS")
            # OpenCV ouvre la caméra sans O_CLOEXEC : sans cela, le nouveau processus hériterait
            # de l'ancienne ouverture et trouverait sa propre caméra occupée.
            os.closerange(3, 4096)
            args = ["--no-browser", "--port", str(app.port)]
            appimage = os.environ.get("APPIMAGE")
            if appimage:  # dans l'AppImage, on relance l'AppImage elle-même (sys.executable est son lanceur)
                os.execve(appimage, [appimage, *args], host_env())
            os.execv(sys.executable, [sys.executable, "-m", "igogs", *args])
