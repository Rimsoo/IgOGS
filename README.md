# IgOGS

**Un driver caméra pour goban physique.** Une webcam filme ton goban ; IgOGS lit les pierres, en déduit
les coups et les transmet à qui veut les recevoir — comme une planche électronique (DGT Pegasus), mais
avec ton propre goban en bois.

- **Jouer sur OGS** (online-go.com) sur ton goban : tes coups partent tout seuls, ceux de l'adversaire
  s'affichent (et sont annoncés à voix haute) pour que tu les poses.
- **Analyser avec [Go Sensei](https://forgejo.aaaoz.fr/Rimso/go-sensei)** : bouton « 📷 Goban », la position
  affichée et le goban restent synchronisés dans les deux sens.
- **Enregistrer** une partie jouée à deux et la télécharger en SGF (ou l'envoyer à Go Sensei).
- **Toute autre appli** peut se brancher via un petit protocole HTTP local : voir [PROTOCOL.md](PROTOCOL.md).

Interface en français et en anglais (FR / EN en haut à droite).

Recréation en Python d'[igoki](https://github.com/CmdrDats/igoki) (Clojure), sans réseau de neurones ni Java.

## Installation

**AppImage (le plus simple)** : télécharge `IgOGS-x86_64.AppImage` depuis les
[releases](https://forgejo.aaaoz.fr/Rimso/IgOGS/releases) — Python, OpenCV et tout le reste sont
dedans.

```sh
chmod +x IgOGS-x86_64.AppImage
./IgOGS-x86_64.AppImage                     # ou, pour l'avoir dans le menu :
./install.sh IgOGS-x86_64.AppImage          # (install.sh est dans le dépôt)
```

**Depuis les sources** :

```sh
git clone https://forgejo.aaaoz.fr/Rimso/IgOGS.git && cd IgOGS
./install.sh          # environnement Python, commande « igogs », entrée « IgOGS » dans le menu
./build-appimage.sh   # construit l'AppImage
```

- **Lancer** : menu des applications → IgOGS, ou `igogs`. S'il tourne déjà, ça rouvre simplement la page.
- **Arrêter** : bouton ⏻ en haut à droite de l'interface, menu de l'icône dans la zone de notification
  (Waybar, KDE…), ou `igogs --stop`. La caméra est libérée.
- **Redémarrer** (après une mise à jour) : bouton ⟳ ; la page se recharge toute seule.
- Options : `--port N`, `--no-browser`, `--no-tray`. Désinstaller : `./install.sh -u`.

Dépendances : Python ≥ 3.10, `numpy`, `opencv-python-headless`, `websocket-client`, `dbus-fast` (icône).

## Mise en route

1. **Caméra** : choisis-la dans la liste (ou « Simulateur » pour essayer sans goban). Place la caméra au-dessus
   du goban, le plus à la verticale possible, tout le plateau visible.
2. **Coins** : « Placer les 4 coins », puis clique les 4 coins de la grille (lignes extérieures) dans
   n'importe quel ordre. La grille est ensuite **calée automatiquement sur les lignes** ; les coins restent
   déplaçables à la souris.
3. **Orientation** : le coin orange marqué « A19 » doit être en haut à gauche *vu du joueur*. Sinon :
   « Tourner ↻ ».
4. **Goban vide** (recommandé) : sur un goban vide, « Mémoriser goban vide » enregistre la couleur du bois à
   chaque intersection — la lecture est alors insensible aux ombres et reflets. Sans cette étape, IgOGS
   apprend le bois tout seul au fil de la partie.

Les pastilles sur l'image montrent la lecture en direct (noir, blanc, rouge = incertain).

## Jouer sur OGS

Onglet **OGS** → pseudo + mot de passe. Le mot de passe n'est utilisé qu'une fois pour obtenir un jeton
OAuth (application publique, comme NXGo) ; seuls les jetons sont gardés, dans `~/.config/igogs/config.json`
(mode 600). Ensuite « Jouer sur le goban » sur une partie en cours.

- Pose ta pierre : dès que ta main est partie et l'image stable (~1 s), le coup est envoyé.
- Le coup de l'adversaire apparaît en **cercle pointillé** : pose-le. Les pierres à retirer (prises) sont
  **barrées en rouge**.
- Rien n'est jamais envoyé tant que le goban ne correspond pas exactement à la partie + un coup légal de ta
  couleur. Pendules, « Passer », « Abandonner » (avec confirmation) et acceptation d'annulation sont dans
  l'onglet.

## Enregistrer une partie

Onglet **Enregistrement** : « Nouvelle partie » (les pierres déjà posées deviennent la position de départ,
pratique pour le handicap), puis jouez. Retirer la ou les dernières pierres annule les coups. Plusieurs coups
joués vite (jusqu'à 4) sont reconnus dans le bon ordre. « Télécharger le SGF » ou « Analyser dans Go Sensei ».

## Comment ça marche

| Étape | Module | Principe |
|---|---|---|
| Image | `vision.Camera` | lecture continue (V4L2, MJPEG 1280×720) |
| Redressement | `vision.Calibration` | homographie 4 coins → plan du goban ; `refine()` maximise la réponse « ligne sombre fine » (black-hat) le long de la grille, du flou au net, en n'utilisant que les segments entre intersections vides |
| Lecture | `vision.Detector` | Lab médian sur un disque autour de chaque intersection, comparé à 3 modèles : bois *propre à chaque intersection*, noir, blanc ; démarrage à froid par itérations modèle ↔ étiquettes ; modèles affinés à chaque position stable ; gain global pour les variations de lumière |
| Stabilité | `tracker.Stabilizer` | une position n'est lue qu'après ~0,8 s sans mouvement ni intersection douteuse (main au-dessus du goban) |
| Coups | `tracker.Tracker` | diff avec la position attendue → séquence de coups légaux (prises, ko), pierres prises tolérées tant qu'elles ne sont pas retirées, annulation seulement si des pierres ont été *retirées* |
| OGS | `ogs.py` | OAuth2 (grant password, client public) + websocket temps réel du protocole officiel `online-go/goban` |

## Développement

```sh
uv pip install -e '.[dev]' && .venv/bin/pytest
```

Les tests utilisent un **goban simulé** rendu en perspective (bois, éclairage inégal, bruit, pierres
décalées) : lecture sur 9/13/19, ajustement de la grille, changements de lumière, inférence des coups, flux
OGS complet, et un bout-en-bout caméra simulée → SGF.

Fichiers : config `~/.config/igogs/config.json`, modèle de couleurs appris `~/.local/share/igogs/model.npz`.

## Limites connues

- Une caméra déjà ouverte par une autre application (Discord, caméra virtuelle…) n'est pas lisible :
  IgOGS l'indique en nommant l'application.
- Gobans carrés 9×9, 13×13, 19×19.
- L'ajustement automatique de la grille s'abstient sur un plateau très rempli (trop peu de lignes visibles) :
  calibrer de préférence goban vide ou peu rempli.
- Pas encore de projecteur ni de marquage des pierres mortes depuis le goban (à faire sur OGS).
