#! /bin/bash
# Point d'entrée de l'AppImage IgOGS (devient l'AppRun de l'image).
# La variable ci-dessous est remplacée à la construction par le Python embarqué.
#
#   ./IgOGS-x86_64.AppImage                -> lance IgOGS (serveur + navigateur + icône)
#   ./IgOGS-x86_64.AppImage --no-browser   -> sans ouvrir la page
#   ./IgOGS-x86_64.AppImage --stop         -> arrête l'instance en cours
#   ./IgOGS-x86_64.AppImage --python …     -> l'interpréteur embarqué

PY="{{ python-executable }}"
if [ ! -x "$PY" ]; then                       # filet, si la substitution rate
    PY="${APPDIR}/usr/bin/python3"
fi

if [ "${1:-}" = "--python" ]; then
    shift
    exec "$PY" -I "$@"
fi

# -I : mode isolé, ignore le dossier courant et les variables PYTHON* de l'hôte
exec "$PY" -I -m igogs "$@"
