#!/usr/bin/env bash
# Compile un OpenCV minimal pour l'AppImage : seulement ce qu'IgOGS utilise (core, imgproc,
# imgcodecs en JPEG seul, videoio en V4L2 seul, liaison Python). Le paquet PyPI
# opencv-python-headless pèse ~150 Mo (FFmpeg, AV1, OpenBLAS, dnn…) ; celui-ci ~10 Mo.
#
#   appimage/build-opencv.sh PYTHON DEST
#     PYTHON : l'interpréteur de l'AppImage (numpy installé), pour compiler la liaison à son ABI
#     DEST   : dossier où déposer le paquet « cv2 » prêt à copier dans site-packages
#
# libstdc++ est liée en statique : l'image ne dépend pas de la version du système.
# Le résultat est gardé dans .build-cache/ (clé : version d'OpenCV + de Python).

set -euo pipefail
PY="$1"
DEST="$2"
OPENCV_VERSION="${OPENCV_VERSION:-4.12.0}"
ICI="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
CACHE="$ICI/.build-cache"
PYTAG="$("$PY" -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}")')"
SORTIE="$CACHE/opencv-$OPENCV_VERSION-$PYTAG-min3"

if [ ! -f "$SORTIE/cv2/__init__.py" ]; then
    mkdir -p "$CACHE"
    SRC="$CACHE/opencv-$OPENCV_VERSION.tar.gz"
    [ -s "$SRC" ] || { echo "   téléchargement d'OpenCV $OPENCV_VERSION"
        curl -fsSL --retry 3 -o "$SRC.part" "https://github.com/opencv/opencv/archive/refs/tags/$OPENCV_VERSION.tar.gz"
        mv "$SRC.part" "$SRC"; }
    W="$(mktemp -d)"
    trap 'rm -rf "$W"' EXIT
    tar -xzf "$SRC" -C "$W"
    PYINC="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
    NPINC="$("$PY" -c 'import numpy; print(numpy.get_include())')"
    # OpenCV veut connaître libpython, sans la lier sous Linux (le module la trouve dans le
    # processus) : un fichier vide suffit si l'interpréteur n'en fournit pas.
    PYLIBF="$("$PY" -c 'import os, sysconfig as s; print(os.path.join(s.get_config_var("LIBDIR") or "", s.get_config_var("LDLIBRARY") or ""))')"
    [ -f "$PYLIBF" ] || { PYLIBF="$W/libpython-vide.so"; : > "$PYLIBF"; }
    GEN=(); command -v ninja >/dev/null 2>&1 && GEN=(-G Ninja)
    echo "   compilation d'OpenCV $OPENCV_VERSION minimal ($PYTAG), quelques minutes"
    cmake -S "$W/opencv-$OPENCV_VERSION" -B "$W/build" "${GEN[@]}" -Wno-dev \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$W/prefix" \
        -DBUILD_LIST=core,imgproc,imgcodecs,videoio,python3,python_bindings_generator \
        -DBUILD_SHARED_LIBS=OFF -DBUILD_opencv_python3=ON -DBUILD_opencv_python2=OFF \
        -DPYTHON3_EXECUTABLE="$PY" -DPYTHON3_LIBRARY="$PYLIBF" -DPYTHON3_INCLUDE_DIR="$PYINC" -DPYTHON3_NUMPY_INCLUDE_DIRS="$NPINC" \
        -DOPENCV_PYTHON3_INSTALL_PATH=python -DOPENCV_SKIP_PYTHON_LOADER=OFF \
        -DWITH_V4L=ON -DWITH_FFMPEG=OFF -DWITH_GSTREAMER=OFF -DWITH_1394=OFF -DWITH_OBSENSOR=OFF \
        -DWITH_JPEG=ON -DBUILD_JPEG=ON -DBUILD_ZLIB=ON -DWITH_PNG=OFF -DWITH_TIFF=OFF -DWITH_WEBP=OFF -DWITH_AVIF=OFF \
        -DWITH_OPENJPEG=OFF -DWITH_JASPER=OFF -DWITH_OPENEXR=OFF -DWITH_IMGCODEC_HDR=OFF \
        -DWITH_IMGCODEC_SUNRASTER=OFF -DWITH_IMGCODEC_PXM=OFF -DWITH_IMGCODEC_PFM=OFF -DWITH_SPNG=OFF \
        -DWITH_IPP=OFF -DWITH_OPENCL=OFF -DWITH_LAPACK=OFF -DWITH_EIGEN=OFF -DWITH_TBB=OFF \
        -DWITH_OPENMP=OFF -DWITH_PROTOBUF=OFF -DWITH_QT=OFF -DWITH_GTK=OFF -DWITH_VA=OFF \
        -DWITH_VA_INTEL=OFF -DWITH_CUDA=OFF -DWITH_ITT=OFF -DCV_TRACE=OFF -DWITH_ADE=OFF \
        -DWITH_KLEIDICV=OFF -DWITH_ZLIB_NG=OFF \
        -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_DOCS=OFF \
        -DBUILD_opencv_apps=OFF -DBUILD_JAVA=OFF -DINSTALL_PYTHON_EXAMPLES=OFF \
        -DOPENCV_GENERATE_PKGCONFIG=OFF -DENABLE_PRECOMPILED_HEADERS=OFF \
        -DCPU_DISPATCH= -DCMAKE_SHARED_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
        -DCMAKE_MODULE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" >"$W/cmake.log" \
        || { tail -40 "$W/cmake.log" >&2; exit 1; }
    cmake --build "$W/build" --parallel "$(nproc)" >"$W/build.log" 2>&1 || { tail -40 "$W/build.log" >&2; exit 1; }
    cmake --install "$W/build" >/dev/null
    INIT="$(find "$W/prefix" -path '*/cv2/__init__.py' 2>/dev/null | head -1)"
    [ -n "$INIT" ] || { echo "liaison Python d'OpenCV introuvable (voir la configuration cmake)" >&2;
                        grep -i -A3 "python 3\|unavailable" "$W/cmake.log" >&2; exit 1; }
    PKG="$(dirname "$INIT")"
    find "$PKG" -name '*.so' -exec strip --strip-unneeded {} +
    rm -rf "$SORTIE.tmp" && mkdir -p "$SORTIE.tmp" && cp -a "$PKG" "$SORTIE.tmp/cv2"
    cp "$W/opencv-$OPENCV_VERSION/LICENSE" "$SORTIE.tmp/cv2/LICENSE-OpenCV.txt"
    rm -rf "$SORTIE" && mv "$SORTIE.tmp" "$SORTIE"
fi
mkdir -p "$DEST"
rm -rf "$DEST/cv2"
cp -a "$SORTIE/cv2" "$DEST/cv2"
echo "   OpenCV $OPENCV_VERSION minimal : $(du -sh "$DEST/cv2" | cut -f1)"
