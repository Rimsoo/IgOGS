import argparse

from . import __version__


def main():
    p = argparse.ArgumentParser(prog="igogs", description="Driver caméra pour goban physique")
    p.add_argument("--port", type=int, help="port HTTP local (défaut : 8742)")
    p.add_argument("--no-browser", action="store_true", help="ne pas ouvrir l'interface")
    p.add_argument("--no-tray", action="store_true", help="pas d'icône dans la zone de notification")
    p.add_argument("--stop", action="store_true", help="arrêter l'instance en cours")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args()
    from .config import Config
    from .server import run, stop_instance
    if a.stop:
        print("IgOGS arrêté." if stop_instance(a.port or Config()["port"]) else "IgOGS ne tournait pas.")
        return
    run(port=a.port, open_browser=not a.no_browser, tray=not a.no_tray)


if __name__ == "__main__":
    main()
