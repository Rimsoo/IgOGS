"use strict";
// Langue de l'interface. Les textes sont écrits en français (dans index.html et app.js) ;
// EN donne leur traduction, clé = le texte français exact (espaces regroupés pour le HTML).

const EN = {
  // ------------------------------------------------------------ index.html
  "Redémarrer IgOGS (après une mise à jour)": "Restart IgOGS (after an update)",
  "Quitter IgOGS (libère la caméra)": "Quit IgOGS (frees the camera)",
  "Langue de l'interface": "Interface language",
  "IgOGS est arrêté": "IgOGS is stopped",
  "La caméra est libérée. Pour le relancer : menu des applications → IgOGS, ou la commande":
    "The camera has been released. To start it again: applications menu → IgOGS, or the command",
  "Caméra": "Camera",
  "Source vidéo": "Video source",
  "Taille du goban": "Goban size",
  "Où êtes-vous assis sur l'image de la caméra ? L'aperçu est tourné pour montrer le goban depuis votre place.":
    "Where do you sit in the camera image? The preview is rotated to show the goban from your seat.",
  "Ma place : en bas de l'image": "My seat: bottom of the image",
  "Ma place : en haut (face à la caméra)": "My seat: top (facing the camera)",
  "Ma place : à gauche de l'image": "My seat: left of the image",
  "Ma place : à droite de l'image": "My seat: right of the image",
  "Vue redressée": "Straightened view",
  "Choisissez une caméra (ou le simulateur) ci-dessus.": "Choose a camera (or the simulator) above.",
  "Placer les 4 coins": "Place the 4 corners",
  "Pendant une partie (OGS, Go Sensei…) : trouve la rotation et le miroir qui font coïncider le goban et la partie":
    "During a game (OGS, Go Sensei…): finds the rotation and mirroring that make the goban match the game",
  "Orienter automatiquement": "Auto-orient",
  "Fait tourner les coordonnées d'un quart de tour": "Rotates the coordinates by a quarter turn",
  "Tourner ↻": "Rotate ↻",
  "Miroir": "Mirror",
  "Cale la grille sur les lignes du goban": "Snaps the grid onto the goban lines",
  "Ajuster sur les lignes": "Snap to lines",
  "À faire goban vide : mémorise le bois à chaque intersection": "Do this with an empty goban: memorises the wood at every point",
  "Mémoriser goban vide": "Learn empty goban",
  "Partie": "Game",
  "Numéro du coup sur chaque pierre": "Move number on each stone",
  "Numéros": "Numbers",
  "Annonce les coups de l'adversaire à voix haute": "Reads the opponent's moves out loud",
  "Voix": "Voice",
  "Simulateur : clic = pose la couleur au trait (ou retire), Maj+clic = l'autre couleur.":
    "Simulator: click = place the colour to play (or remove), Shift+click = the other colour.",
  "Enregistrement": "Recording",
  "Applications": "Applications",
  "Nouvelle partie": "New game",
  "Annuler le coup": "Undo move",
  "Passer": "Pass",
  "Télécharger le SGF": "Download SGF",
  "Analyser dans Go Sensei": "Analyse in Go Sensei",
  "Les pierres déjà posées au démarrage d'une partie deviennent la position de départ (handicap). Retirer la ou les dernières pierres annule les coups.":
    "Stones already on the board when a game starts become the starting position (handicap). Removing the last stone(s) undoes the moves.",
  "Connectez votre compte online-go.com. Le mot de passe sert une seule fois à obtenir un jeton ; il n'est pas enregistré.":
    "Sign in to your online-go.com account. The password is only used once to get a token; it is not saved.",
  "Pseudo OGS": "OGS username",
  "Mot de passe": "Password",
  "Se connecter": "Sign in",
  "Déconnexion": "Sign out",
  "Accepter l'annulation": "Accept undo",
  "Abandonner": "Resign",
  "Quitter la partie": "Leave the game",
  "Parties en cours": "Games in progress",
  "Actualiser": "Refresh",
  "IgOGS est un driver : d'autres applications peuvent suivre le goban comme avec une planche électronique. Elles s'abonnent à":
    "IgOGS is a driver: other applications can follow the goban as with an electronic board. They subscribe to",
  "et indiquent la partie attendue avec": "and set the expected game with",
  "(voir PROTOCOL.md).": "(see PROTOCOL.md).",
  "Utilisé par :": "Used by:",
  "Reprendre la main (enregistrement local)": "Take back control (local recording)",

  // ------------------------------------------------------------ app.js
  "Noir": "Black",
  "Blanc": "White",
  "Redémarrage…": "Restarting…",
  "Coup annulé": "Move undone",
  "{n} coups annulés": "{n} moves undone",
  "IgOGS ne répond plus…": "IgOGS is not responding…",
  "{color} passe": "{color} passes",
  "Pas de caméra": "No camera",
  "Simulateur · {fps} img/s": "Simulator · {fps} fps",
  "Caméra · {fps} img/s": "Camera · {fps} fps",
  "Non calibré": "Not calibrated",
  "Lecture en cours…": "Reading…",
  "Goban lu": "Goban read",
  "OGS : {user}": "OGS: {user}",
  "OGS déconnecté": "OGS disconnected",
  "— Caméra —": "— Camera —",
  "Cliquez le coin {k}/4 de la grille (les lignes extérieures), dans n'importe quel ordre.":
    "Click corner {k}/4 of the grid (the outer lines), in any order.",
  "Calibration : cliquez « Placer les 4 coins », puis les quatre coins de la grille sur l'image.":
    "Calibration: click “Place the 4 corners”, then the four corners of the grid in the image.",
  "Vérifiez que la grille recouvre les lignes et que les coordonnées (en jaune) sont lues depuis votre place : A1 en bas à gauche. Sinon « Tourner » / « Miroir », ou « Orienter automatiquement » pendant une partie. Les coins se déplacent à la souris.":
    "Check that the grid covers the lines and that the coordinates (in yellow) read from your seat: A1 bottom left. Otherwise “Rotate” / “Mirror”, or “Auto-orient” during a game. Corners can be dragged with the mouse.",
  "Annuler": "Cancel",
  "Enregistrement local": "Local recording",
  "Choisissez une caméra pour commencer.": "Choose a camera to start.",
  "Calibrez la caméra : placez les 4 coins.": "Calibrate the camera: place the 4 corners.",
  "En attente d'une image stable du goban…": "Waiting for a steady image of the goban…",
  "Envoi de {moves}…": "Sending {moves}…",
  "Retirez les pierres prises : {pts}": "Remove the captured stones: {pts}",
  "posez {color} en {pts}": "place {color} at {pts}",
  "retirez {pts}": "remove {pts}",
  "Le goban semble tourné ou en miroir : cliquez « Orienter automatiquement »":
    "The goban seems rotated or mirrored: click “Auto-orient”",
  "Partie terminée": "Game over",
  "Partie terminée : {result}": "Game over: {result}",
  "À vous de jouer ({color})": "Your move ({color})",
  "{color} joue": "{color} to play",
  "En attente du coup de l'adversaire ({color})": "Waiting for the opponent's move ({color})",
  "{color} au trait": "{color} to play",
  "Connecté : {user}": "Signed in: {user}",
  "Connexion à OGS…": "Connecting to OGS…",
  "vous avez {color}": "you are {color}",
  "spectateur": "spectator",
  "en cours": "in progress",
  "retrait des pierres mortes": "dead stone removal",
  "terminée": "finished",
  "{n} coups": "{n} moves",
  "({n} coups)": "({n} moves)",
  "Chargement…": "Loading…",
  "Aucune partie en cours. Lancez-en une sur online-go.com puis actualisez.":
    "No game in progress. Start one on online-go.com, then refresh.",
  "À vous": "Your move",
  "Taille non gérée": "Size not supported",
  "Suivie": "Followed",
  "Jouer sur le goban": "Play on the goban",
  "Orientation corrigée": "Orientation fixed",
  "L'orientation était déjà la bonne": "The orientation was already right",
  "Grille ajustée ({px} px)": "Grid adjusted ({px} px)",
  "Grille déjà en place (ou trop peu de lignes visibles)": "Grid already in place (or too few visible lines)",
  "Le goban est-il vide ? Sa couleur sera mémorisée à chaque intersection.":
    "Is the goban empty? Its colour will be memorised at every point.",
  "Goban vide mémorisé": "Empty goban learnt",
  "Quitter IgOGS ? La caméra sera libérée.": "Quit IgOGS? The camera will be released.",
  "Quitter IgOGS ? La caméra sera libérée et la partie OGS ne sera plus suivie sur le goban.":
    "Quit IgOGS? The camera will be released and the OGS game will no longer be followed on the goban.",
  "Partie envoyée à Go Sensei": "Game sent to Go Sensei",
  "Passer votre tour sur OGS ?": "Pass your turn on OGS?",
  "Abandonner la partie sur OGS ? C'est définitif.": "Resign the game on OGS? This cannot be undone.",
};

let LANG = (() => {
  try { const v = localStorage.getItem("igogs.lang"); if (v === "fr" || v === "en") return v; } catch (e) {}
  return (navigator.language || "").toLowerCase().startsWith("fr") ? "fr" : "en";
})();

function t(fr, vars) {
  let s = LANG === "en" && Object.prototype.hasOwnProperty.call(EN, fr) ? EN[fr] : fr;
  if (vars) s = s.replace(/\{(\w+)\}/g, (all, k) => (k in vars ? vars[k] : all));
  return s;
}

// Textes fixes de la page : relevés une fois (en français), puis réécrits à chaque changement de langue.
const I18N_ATTRS = ["title", "placeholder"];
const STATIC_TEXT = [], STATIC_ATTRS = [];
(function collectStatic() {
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (n.parentElement.closest("script, style, svg") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    const key = n.nodeValue.replace(/\s+/g, " ").trim();
    if (key && Object.prototype.hasOwnProperty.call(EN, key)) {
      const [, lead, , trail] = n.nodeValue.match(/^(\s*)([\s\S]*?)(\s*)$/);
      STATIC_TEXT.push({ node: n, key, lead, trail });
    }
  }
  for (const el of document.querySelectorAll(I18N_ATTRS.map((a) => `[${a}]`).join(","))) {
    for (const a of I18N_ATTRS) {
      const v = el.getAttribute(a);
      if (v && Object.prototype.hasOwnProperty.call(EN, v)) STATIC_ATTRS.push({ el, a, key: v });
    }
  }
})();

function applyStaticLang() {
  document.documentElement.lang = LANG;
  for (const { node, key, lead, trail } of STATIC_TEXT) node.nodeValue = lead + t(key) + trail;
  for (const { el, a, key } of STATIC_ATTRS) el.setAttribute(a, t(key));
  const sel = document.getElementById("lang-select");
  if (sel) sel.value = LANG;
}

function setLang(lang) {
  LANG = lang === "en" ? "en" : "fr";
  try { localStorage.setItem("igogs.lang", LANG); } catch (e) {}
  applyStaticLang();
}

applyStaticLang();
