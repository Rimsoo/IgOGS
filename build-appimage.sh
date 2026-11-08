#!/usr/bin/env bash
# Construit IgOGS-x86_64.AppImage à partir de ce dossier (même méthode que Go Sensei).
#
#   ./build-appimage.sh
#
# Résultat : un fichier unique et exécutable qui embarque Python, IgOGS et ses dépendances
# (OpenCV, numpy, websocket-client, dbus-fast). Rien à installer chez l'utilisateur.
# La construction télécharge une image Python manylinux, appimagetool et le runtime AppImage
# depuis leurs dépôts officiels : il lui faut un accès réseau. Les téléchargements sont gardés
# dans .build-cache/ pour les constructions suivantes.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PROJET="$PWD"

PYVER="${PYVER:-3.12}"
VENV="${VENV:-.build-venv}"
CACHE="$PROJET/.build-cache"

APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"

echo "== vérification des fichiers"
for f in pyproject.toml igogs/__main__.py igogs/static/index.html \
         appimage/igogs.desktop appimage/igogs.png appimage/entrypoint.sh; do
    [ -f "$f" ] || { echo "manquant : $f" >&2; exit 1; }
done
if grep -q '^Name=.* ' appimage/igogs.desktop; then
    echo "le champ Name du .desktop contient un espace : python-appimage ne le protège pas." >&2
    exit 1
fi

echo "== environnement de construction"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip python-appimage
mkdir -p "$CACHE"

# python-appimage lance « pip install » depuis son propre dossier : la recette donne donc le
# chemin ABSOLU du projet.
RECETTE="$(mktemp -d)"
TRAVAIL="$(mktemp -d)"
trap 'rm -rf "$RECETTE" "$TRAVAIL"' EXIT
cp appimage/igogs.desktop appimage/igogs.png appimage/entrypoint.sh "$RECETTE/"
printf '%s\n' "$PROJET" > "$RECETTE/requirements.txt"

echo "== construction (Python $PYVER manylinux)"
# python-appimage prépare seulement le dossier de l'application (--no-packaging) : son propre
# appimagetool télécharge le runtime depuis GitHub et peut se figer. On assemble nous-mêmes plus
# bas, avec un runtime récupéré par curl et gardé en cache. On retente en cas de coupure réseau.
for essai in 1 2 3; do
    ( cd "$TRAVAIL" && "$PROJET/$VENV/bin/python" -m python_appimage build app --no-packaging -p "$PYVER" "$RECETTE" ) && break
    [ "$essai" = 3 ] && { echo "python-appimage a échoué 3 fois (réseau ?)" >&2; exit 1; }
    echo "   échec, nouvel essai dans 10 s" >&2; rm -rf "$TRAVAIL"/*; sleep 10
done
APPDIR="$TRAVAIL/IgOGS-$(uname -m)"
[ -x "$APPDIR/AppRun" ] || { echo "python-appimage n'a pas produit $APPDIR" >&2; exit 1; }
# Construite à part : l'AppImage en place (celle que lance install.sh) n'est remplacée qu'une
# fois la nouvelle terminée et vérifiée, jamais cassée par un build raté.
OUT="$TRAVAIL/IgOGS-x86_64.AppImage"
FINAL="./IgOGS-x86_64.AppImage"

telecharger() {  # url fichier
    [ -s "$2" ] || { echo "   téléchargement de $(basename "$2")"; curl -fsSL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"; }
}

echo "== allègement"
# Inutiles à l'exécution : tests et en-têtes de numpy, caches de compilation.
find "$APPDIR" -type d \( -name __pycache__ -o -name tests -path '*numpy*' \) -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$APPDIR"/opt/python*/lib/python*/site-packages/numpy/_core/include 2>/dev/null || true

echo "== assemblage de l'image"
telecharger "$APPIMAGETOOL_URL" "$CACHE/appimagetool"
telecharger "$RUNTIME_URL" "$CACHE/runtime-x86_64"
chmod +x "$CACHE/appimagetool"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --no-appstream \
    --runtime-file "$CACHE/runtime-x86_64" "$APPDIR" "$OUT" >/dev/null 2>"$TRAVAIL/appimagetool.log" \
    || { cat "$TRAVAIL/appimagetool.log" >&2; exit 1; }
chmod +x "$OUT"

echo
echo "== vérification de l'image"
if "$OUT" --appimage-extract-and-run --python -c "
import igogs.server, igogs.tray, cv2, numpy, websocket, dbus_fast
from igogs.simulate import Renderer, default_corners
from igogs.vision import Calibration, Detector
from igogs.goban import Position, B
p = Position(9); p[(4, 4)] = B
cal = Calibration(default_corners(), 9); det = Detector(9)
grid, _ = det.classify(det.features(cal.warp(Renderer(9).render(p))))
assert grid == p.grid, 'lecture du goban simulé incorrecte'
print('   IgOGS', igogs.__version__, '+ OpenCV', cv2.__version__, ': lecture du goban simulé OK')
"; then :
else
    echo "   ÉCHEC : IgOGS ne fonctionne pas dans l'image, l'AppImage en place est conservée" >&2
    exit 1
fi

mv -f "$OUT" "$FINAL"
OUT="$FINAL"
echo
echo "== terminé : $OUT ($(du -h "$OUT" | cut -f1))"
echo "   lancer        :  $OUT"
echo "   installer     :  ./install.sh $OUT"
echo
echo "Si le lancement répond « dlopen(): error loading libfuse.so.2 » : installer fuse2"
echo "(sudo pacman -S fuse2) ou lancer avec $OUT --appimage-extract-and-run"
