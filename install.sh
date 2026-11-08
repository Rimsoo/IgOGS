#!/usr/bin/env bash
# Installe IgOGS pour l'utilisateur : une commande « igogs » et une entrée dans le menu des
# applications. Rien en dehors du dossier personnel, aucun sudo.
#
#   ./install.sh      installe (ou met à jour) depuis ce dossier
#   ./install.sh -u   désinstalle (garde réglages et données)

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

NOM="igogs"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"

if [ "${1:-}" = "-u" ] || [ "${1:-}" = "--uninstall" ]; then
    "$BIN/$NOM" --stop 2>/dev/null || true
    rm -f "$BIN/$NOM" "$APPS/$NOM.desktop" "$ICONS/$NOM.png"
    echo "désinstallé (réglages : ~/.config/igogs, données : ~/.local/share/igogs)."
    exit 0
fi

echo "== environnement Python"
if command -v uv >/dev/null 2>&1; then
    [ -d .venv ] || uv venv -q .venv
    uv pip install -q --python .venv/bin/python -e .
else
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q -e .
fi

echo "== commande et menu"
mkdir -p "$BIN" "$APPS" "$ICONS"
ln -sf "$PWD/.venv/bin/igogs" "$BIN/$NOM"
cp -f igogs/static/igogs.png "$ICONS/$NOM.png"
cat > "$APPS/$NOM.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=IgOGS
GenericName=Goban physique
Comment=Jouer sur OGS et analyser avec Go Sensei sur un vrai goban filmé
Exec=$BIN/$NOM
Icon=$NOM
Terminal=false
Categories=Game;BoardGame;
Keywords=go;baduk;weiqi;goban;ogs;camera;
DESKTOP
chmod 644 "$APPS/$NOM.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

echo
echo "== terminé"
echo "   lancer        :  menu des applications → IgOGS, ou « igogs » (relancer rouvre la page)"
echo "   arrêter       :  bouton ⏻ dans l'interface, menu de l'icône (waybar), ou « igogs --stop »"
echo "   désinstaller  :  ./install.sh -u"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "   (ajoute $BIN à ton PATH pour taper « igogs » dans un terminal)" ;;
esac
