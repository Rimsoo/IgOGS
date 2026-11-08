"""IgOGS : driver caméra pour goban physique (OGS, Go Sensei, enregistrement SGF)."""

import os

# Les échecs d'ouverture de caméra sont déjà expliqués dans l'interface (« occupée par … ») :
# inutile qu'OpenCV les répète dans le terminal à chaque nouvelle tentative.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

__version__ = "0.1.0"
