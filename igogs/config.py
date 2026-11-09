"""Configuration (~/.config/igogs/config.json, mode 600 : contient les jetons OGS)."""

import copy
import json
import os
import threading

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "igogs")
DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "igogs")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Application OAuth publique enregistrée sur online-go.com (grant « password », sans secret),
# la même que NXGo. Remplaçable dans la config : ogs.client_id.
OGS_CLIENT_ID = "uPs4SZw6MBvacOWVfrNaYn9gfKbtPW78AgXQ1T4n"

DEFAULTS = {
    "port": 8742,
    "ui": {"lang": "auto"},  # auto (langue du système) | fr | en
    "camera": "",
    "calibration": {"corners": [], "size": 19, "mirror": False},
    "view_rotation": 0,  # place du joueur : 0 = en bas de l'image, 180 = face à la caméra…
    "stable_hold": 0.8,  # secondes d'immobilité avant de lire une position
    "sounds": True,
    "ogs": {
        "client_id": OGS_CLIENT_ID,
        "url": "https://online-go.com",
        "username": "",
        "user_id": 0,
        "access_token": "",
        "refresh_token": "",
        "expires": 0,
    },
    "gosensei": {"url": "http://127.0.0.1:8765"},
    # Jeu à l'écran : fenêtre dont le titre ou la classe contient ce texte.
    "screen": {"window": "conquest of go"},
}


def _merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, path=CONFIG_FILE):
        self.path = path
        self.lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(path) as f:
                _merge(self.data, json.load(f))
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        return self.data[k]

    def update(self, patch):
        with self.lock:
            _merge(self.data, patch)
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
