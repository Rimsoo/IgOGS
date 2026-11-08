#!/usr/bin/env bash
# Construit IgOGS-x86_64.AppImage à partir de ce dossier (même méthode que Go Sensei).
#
#   ./build-appimage.sh
#
# Résultat : un fichier unique et exécutable qui embarque Python, IgOGS et ses dépendances
# (OpenCV minimal compilé pour l'occasion, numpy, websocket-client, dbus-fast). Rien à installer.
# Il faut cmake et un compilateur C++ (OpenCV). La construction télécharge une image Python
# manylinux, les sources d'OpenCV, appimagetool et le runtime AppImage
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

SP="$(echo "$APPDIR"/opt/python*/lib/python*/site-packages)"
PYBIN="$(echo "$APPDIR"/opt/python*/bin/python3.*)"
[ -d "$SP" ] && [ -x "$PYBIN" ] || { echo "Python introuvable dans $APPDIR" >&2; exit 1; }

echo "== OpenCV minimal (à la place d'opencv-python-headless)"
# L'interpréteur de l'image, avec ses bibliothèques, pour compiler la liaison à son ABI ; un
# petit lanceur évite d'imposer ces bibliothèques à cmake et au compilateur.
cat > "$TRAVAIL/python" <<LANCEUR
#!/bin/sh
LD_LIBRARY_PATH="$APPDIR/usr/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" exec "$PYBIN" "\$@"
LANCEUR
chmod +x "$TRAVAIL/python"
appimage/build-opencv.sh "$TRAVAIL/python" "$TRAVAIL/opencv"
rm -rf "$SP"/cv2 "$SP"/opencv_python_headless*
cp -a "$TRAVAIL/opencv/cv2" "$SP/"

echo "== numpy sans BLAS (à la place de sa copie d'OpenBLAS, 25 Mo)"
# IgOGS ne fait d'algèbre linéaire que sur de toutes petites matrices : la version interne
# (lapack_lite) suffit. Même version de numpy, recompilée ; la roue est gardée en cache.
NPVER="$("$TRAVAIL/python" -c 'import numpy; print(numpy.__version__)')"
PYTAG="$("$TRAVAIL/python" -c 'import sys; print(f"cp{sys.version_info[0]}{sys.version_info[1]}")')"
ROUES="$CACHE/numpy-$NPVER-$PYTAG-noblas-nodispatch"
if ! ls "$ROUES"/numpy-*.whl >/dev/null 2>&1; then
    echo "   compilation de numpy $NPVER, quelques minutes"
    "$TRAVAIL/python" -m pip wheel --quiet --no-deps --no-binary numpy "numpy==$NPVER" -w "$ROUES.tmp" \
        -Csetup-args=-Dallow-noblas=true -Csetup-args=-Dblas=none -Csetup-args=-Dlapack=none \
        -Csetup-args=-Dcpp_link_args=-static-libstdc++ -Csetup-args=-Dcpu-dispatch=none \
        || { echo "compilation de numpy impossible" >&2; exit 1; }
    rm -rf "$ROUES" && mv "$ROUES.tmp" "$ROUES"
fi
rm -rf "$SP"/numpy "$SP"/numpy.libs "$SP"/numpy-*.dist-info
"$TRAVAIL/python" -m pip install --quiet --no-deps --no-index --target "$SP" "$ROUES"/numpy-*.whl
"$TRAVAIL/python" -c 'import numpy; numpy.linalg.lstsq(numpy.eye(3), numpy.ones(3), rcond=None); print("   numpy", numpy.__version__, "sans BLAS : OK")'

echo "== allègement"
PYLIB="$(dirname "$SP")"
# Outils d'installation et de développement : inutiles une fois l'image construite.
rm -rf "$SP"/pip "$SP"/pip-* "$SP"/setuptools "$SP"/setuptools-* "$SP"/_distutils_hack \
       "$SP"/numpy/_core/include "$SP"/numpy/f2py "$SP"/numpy/_pyinstaller "$SP"/numpy/typing/tests \
       "$APPDIR"/opt/python*/include "$APPDIR"/opt/python*/bin/{pip*,f2py,numpy-config,wsdump} \
       "$PYLIB"/{ensurepip,idlelib,tkinter,turtledemo,turtle.py,lib2to3,pydoc_data,test,unittest/test} \
       "$PYLIB"/config-*-linux-gnu "$PYLIB"/lib-dynload/_tkinter* \
       "$APPDIR"/usr/lib/libtk* "$APPDIR"/usr/lib/libtcl* "$APPDIR"/usr/share/tcltk "$APPDIR"/usr/share/tcl* \
       "$APPDIR"/usr/share/tk*
# Modules inutilisés : sqlite, terminal (curses, readline), modules de test de CPython et numpy.
rm -rf "$PYLIB"/sqlite3 "$PYLIB"/curses "$PYLIB"/lib-dynload/{_sqlite3,_curses,_curses_panel,readline}* \
       "$PYLIB"/lib-dynload/{_test,_xxtest,_ctypes_test,xxlimited,xxsubtype}* \
       "$APPDIR"/usr/lib/{libsqlite3,libncurses,libpanel,libform,libmenu,libreadline,libhistory}* \
       "$SP"/numpy/_core/_simd*.so "$SP"/numpy/_core/_*tests*.so "$SP"/numpy/_core/_operand_flag_tests*.so
find "$SP" -type d -name tests -prune -exec rm -rf {} +
find "$APPDIR" -name '*.pyi' -delete
find "$APPDIR" -name '*.opt-1.pyc' -delete -o -name '*.opt-2.pyc' -delete
# Bibliothèques natives sans leurs symboles de débogage.
find "$APPDIR" -type f \( -name '*.so' -o -name '*.so.*' \) -exec strip --strip-unneeded {} + 2>/dev/null || true
echo "   contenu de l'image : $(du -sh "$APPDIR" | cut -f1)"

echo "== assemblage de l'image"
telecharger "$APPIMAGETOOL_URL" "$CACHE/appimagetool"
telecharger "$RUNTIME_URL" "$CACHE/runtime-x86_64"
chmod +x "$CACHE/appimagetool"
# zstd au niveau maximal + gros blocs : l'image la plus petite possible (le runtime AppImage ne
# lit que zstd ; la décompression reste rapide au lancement).
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --no-appstream --comp zstd \
    --mksquashfs-opt -b --mksquashfs-opt 1M --mksquashfs-opt -Xcompression-level --mksquashfs-opt 22 \
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
