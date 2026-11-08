# Protocole driver IgOGS (v1)

IgOGS écoute sur `http://127.0.0.1:8742` (local uniquement). Toute application peut s'y brancher pour
suivre un goban physique, à la manière d'une planche électronique. `GET /api/status` →
`{"app": "igogs", "version": "…", "protocol": 1}`.

Coordonnées : notation GTP (`D4`, `Q16`, `pass`), lettre I sautée, ligne 1 en bas. Couleurs : `"B"` / `"W"`.

## Sécurité

- `POST` exige l'en-tête `X-IgOGS: 1` ; l'en-tête `Host` doit être `127.0.0.1:8742` ou `localhost:8742`.
- Les requêtes d'un navigateur venant d'une autre origine sont refusées (403). Un programme local (sans
  en-tête `Origin`) est accepté.

## Principe

Une seule partie est suivie à la fois, par un **propriétaire** (`owner`) : `local` (enregistrement, par
défaut), `ogs`, ou le nom de ton application. L'application **réclame** le goban en donnant la partie
attendue ; IgOGS compare en continu avec ce que voit la caméra :

- le goban a **un ou plusieurs coups de plus** (couleurs `camera_colors`, coups légaux) → événement `move` ;
- des pierres ont été **retirées** et le goban revient à une position antérieure → événement `undo`
  (si `allow_undo`) ;
- sinon → état de synchronisation (`missing` : à poser, `extra` : à retirer), affiché au joueur.

Chaque fois que la position change dans l'application (navigation, coup à l'écran, coup reçu d'un serveur),
elle renvoie simplement `POST /api/claim` avec la nouvelle partie : c'est idempotent.

## `POST /api/claim`

```json
{
  "owner": "mon-appli",
  "size": 19,
  "setup": {"B": ["D4", "Q16"], "W": []},
  "first": "W",
  "moves": [["W", "C3"], ["B", "R4"]],
  "camera_colors": ["B", "W"],
  "autoplay": true,
  "allow_undo": true,
  "info": {"PB": "Alice", "PW": "Bob"}
}
```

| Champ | Défaut | Rôle |
|---|---|---|
| `owner` | obligatoire | nom de l'application (pas `local` ni `ogs`) |
| `size` | 19 | 9, 13 ou 19 |
| `setup` | vide | pierres posées avant le premier coup |
| `first` | `B` | couleur du premier coup |
| `free_handicap` | 0 | pierres de handicap libre posées d'affilée par Noir (règle OGS) |
| `moves` | `[]` | `[couleur, coup]` (ou simplement `"coup"`, couleur alternée) |
| `camera_colors` | `["B","W"]` | couleurs jouées sur le goban physique (ex. `["B"]` en ligne) |
| `autoplay` | `true` | `true` : le coup lu est joué aussitôt dans IgOGS (`applied: true`). `false` : il est seulement proposé (`applied: false`) et l'application le confirme avec `/api/push` (ex. après acceptation par un serveur) |
| `allow_undo` | `true` | retirer des pierres annule des coups |
| `force` | `false` | prendre la main même pendant une partie OGS |

Réponse : `{"ok": true, "board": <état>}`. Refus si une partie OGS est en cours (sauf `force`).

## Autres commandes

| Requête | Corps | Effet |
|---|---|---|
| `POST /api/push` | `{"owner", "move", "color"?}` | ajoute un coup confirmé (adversaire distant, ou coup proposé accepté) |
| `POST /api/release` | `{"owner"}` | rend la main (retour à l'enregistrement local) |
| `GET /api/board` | | état de la partie suivie (voir ci-dessous) |
| `GET /api/game.sgf` | | partie suivie en SGF |

## Événements : `GET /events` (Server-Sent Events)

Un `state` complet est envoyé à la connexion, puis :

| Événement | Données |
|---|---|
| `move` | `{"color", "move", "sgf", "x", "y", "applied", "owner", "move_number"}` — un coup lu sur le goban |
| `undo` | `{"count", "owner", "move_number"}` |
| `game`, `observed` | état de la partie (après un changement de partie / une nouvelle lecture stable) |
| `live` | `{"grid", "busy"}` — lecture brute en direct (`B`, `W`, `.`, `?` ligne par ligne depuis A19) |
| `calibration`, `ogs`, `ogs-move` | usage interne de l'interface |

Ne traiter `move` / `undo` que si `owner` est le sien.

### État de la partie (`board`)

```json
{
  "owner": "mon-appli", "size": 19, "info": {},
  "moves": [["B", "Q16"], ["W", "D4"]],
  "expected": "…361 caractères B/W/. …",
  "observed": "…ou null…",
  "to_play": "B", "camera_colors": ["B", "W"], "pending": [],
  "sync": {"status": "ok | mismatch | remove-captured | pending | unknown",
           "missing": [["Q3", "B"]], "extra": [["D5", "W"]], "camera_turn": true}
}
```

## Exemple minimal (Python)

```python
import json, urllib.request

def post(path, body):
    req = urllib.request.Request("http://127.0.0.1:8742" + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "X-IgOGS": "1"})
    return json.load(urllib.request.urlopen(req))

post("/api/claim", {"owner": "demo", "size": 9, "moves": []})
with urllib.request.urlopen("http://127.0.0.1:8742/events") as events:
    kind = None
    for line in events:
        line = line.decode().strip()
        if line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:") and kind == "move":
            ev = json.loads(line[5:])
            if ev["owner"] == "demo":
                print("coup joué sur le goban :", ev["color"], ev["move"])
```

Go Sensei (`gosensei/goban_link.py`) est une implémentation complète de ce protocole.
