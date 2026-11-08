"""Langue des messages côté serveur (erreurs affichées dans l'interface, menu de l'icône).

Comme dans Go Sensei : le code est écrit en français et EN donne la traduction anglaise, la
clé étant le texte français exact (avant remplissage des champs {…}). Les journaux restent en
français. La langue est choisie dans l'interface (FR / EN) et gardée dans la config (ui.lang).
"""

import os

LANGS = ("fr", "en")
LANG = "fr"

EN = {
    # caméra
    "Simulateur (sans caméra)": "Simulator (no camera)",
    "Caméra {i}": "Camera {i}",
    "caméra {cam} occupée par {apps}": "camera {cam} in use by {apps}",
    "caméra {cam} indisponible": "camera {cam} unavailable",
    "caméra {cam} : plus d'image": "camera {cam}: no more frames",
    "pas d'image de la caméra": "no image from the camera",
    "caméra ou calibration manquante": "camera or calibration missing",
    "le simulateur n'est pas la caméra active": "the simulator is not the active camera",
    "intersection hors du goban simulé (calibration décalée ?)":
        "point outside the simulated goban (calibration off?)",
    # calibration
    "rotation : 0, 90, 180 ou 270": "rotation: 0, 90, 180 or 270",
    "taille de goban : 9, 13 ou 19": "goban size: 9, 13 or 19",
    "il faut au moins 3 pierres dans la partie pour trouver l'orientation":
        "the game needs at least 3 stones to find the orientation",
    "position symétrique : orientation ambiguë, ajoutez une pierre ou utilisez « Tourner »":
        "symmetric position: ambiguous orientation, add a stone or use “Rotate”",
    "aucune orientation ne correspond à la partie ({n} différences)":
        "no orientation matches the game ({n} differences)",
    # règles
    "coordonnée hors du plateau : {s}": "coordinate off the board: {s}",
    "hors du plateau": "off the board",
    "intersection occupée": "point already occupied",
    "ko": "ko",
    "suicide": "suicide",
    # partie suivie / applications
    "une partie est déjà suivie par {owner}": "a game is already followed by {owner}",
    "le goban est utilisé par {owner}": "the goban is used by {owner}",
    "partie gérée par {owner}": "game managed by {owner}",
    "cette application n'a pas la main sur le goban (POST /api/claim)":
        "this application does not control the goban (POST /api/claim)",
    "owner : nom de l'application (ex. « go-sensei »)": "owner: application name (e.g. “go-sensei”)",
    "une partie OGS est en cours sur le goban": "an OGS game is in progress on the goban",
    "suite de coups invalide : {e}": "invalid move sequence: {e}",
    "Go Sensei a refusé la partie (HTTP {code})": "Go Sensei rejected the game (HTTP {code})",
    "Go Sensei ne répond pas : est-il lancé ?": "Go Sensei is not responding: is it running?",
    # OGS
    "online-go.com injoignable ({e})": "online-go.com unreachable ({e})",
    "identifiant ou mot de passe incorrect": "wrong username or password",
    "connexion refusée : {why}": "sign-in refused: {why}",
    "session OGS expirée : reconnectez-vous": "OGS session expired: please sign in again",
    "OGS n'a pas reconnu la session": "OGS did not recognise the session",
    "goban {w}x{h} non géré (9, 13 ou 19)": "{w}x{h} goban not supported (9, 13 or 19)",
    "aucune partie OGS en cours": "no OGS game in progress",
    "ce n'est pas votre tour": "it is not your turn",
    "coup non envoyé : connexion à OGS perdue": "move not sent: connection to OGS lost",
    # icône
    "Clic : ouvrir IgOGS": "Click: open IgOGS",
    "Ouvrir IgOGS": "Open IgOGS",
    "Quitter IgOGS": "Quit IgOGS",
}


def tr(text, **kw):
    """Le texte dans la langue choisie, avec ses champs {…} remplis."""
    if LANG == "en":
        text = EN.get(text, text)
    return text.format(**kw) if kw else text


def system_lang():
    env = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "fr" if env.lower().startswith("fr") else "en"


def set_lang(choice):
    """`choice` : fr, en, ou auto (langue du système). Renvoie la langue retenue."""
    global LANG
    LANG = choice if choice in LANGS else system_lang()
    return LANG
