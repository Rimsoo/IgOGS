# ruff: noqa: F821, F722  (annotations = signatures D-Bus de dbus-fast)
"""Icône dans la zone de notification (protocole StatusNotifierItem, comme Sunshine ou Steam).

Fonctionne avec Waybar, KDE, Xfce, et GNOME avec l'extension AppIndicator. Nécessite le
paquet Python `dbus-fast` ; sans lui, IgOGS tourne sans icône.
Clic sur l'icône : ouvrir IgOGS. Menu : Ouvrir, Quitter."""

import asyncio
import os
import struct
import threading
import zlib

from .i18n import tr

try:
    from dbus_fast import Variant
    from dbus_fast.aio import MessageBus
    from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method, signal
    AVAILABLE = True
except ImportError:  # pragma: no cover - dépend de l'installation
    AVAILABLE = False

ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


def _png_to_argb(path):
    """Décode un PNG RGBA 8 bits (non entrelacé) en pixmap ARGB32 big-endian (format du protocole)."""
    with open(path, "rb") as f:
        data = f.read()
    pos, idat, width, height = 8, b"", 0, 0
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", chunk[:10])
            if depth != 8 or color != 6:
                raise ValueError("PNG RGBA 8 bits attendu")
        elif ctype == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        ftype = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - 4] if x >= 4 else 0
            b = prev[x]
            c = prev[x - 4] if x >= 4 else 0
            if ftype == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ftype == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ftype == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif ftype == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[x] = (line[x] + pred) & 0xFF
        for x in range(0, stride, 4):
            r, g, bl, al = line[x:x + 4]
            out += bytes((al, r, g, bl))
        prev = line
    return width, height, bytes(out)


if AVAILABLE:
    class StatusNotifierItem(ServiceInterface):
        def __init__(self, tray):
            super().__init__("org.kde.StatusNotifierItem")
            self.tray = tray

        @dbus_property(access=PropertyAccess.READ)
        def Category(self) -> "s":
            return "ApplicationStatus"

        @dbus_property(access=PropertyAccess.READ)
        def Id(self) -> "s":
            return "igogs"

        @dbus_property(access=PropertyAccess.READ)
        def Title(self) -> "s":
            return "IgOGS"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "Active"

        @dbus_property(access=PropertyAccess.READ)
        def WindowId(self) -> "i":
            return 0

        @dbus_property(access=PropertyAccess.READ)
        def IconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconPixmap(self) -> "a(iiay)":
            return self.tray.pixmaps

        @dbus_property(access=PropertyAccess.READ)
        def OverlayIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def AttentionIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def ToolTip(self) -> "(sa(iiay)ss)":
            return ["", [], "IgOGS", tr("Clic : ouvrir IgOGS")]

        @dbus_property(access=PropertyAccess.READ)
        def ItemIsMenu(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Menu(self) -> "o":
            return MENU_PATH

        @method()
        def Activate(self, x: "i", y: "i"):
            self.tray.on_open()

        @method()
        def SecondaryActivate(self, x: "i", y: "i"):
            self.tray.on_open()

        @method()
        def ContextMenu(self, x: "i", y: "i"):
            pass

        @method()
        def Scroll(self, delta: "i", orientation: "s"):
            pass

        @signal()
        def NewIcon(self):
            pass

    class DBusMenu(ServiceInterface):
        """Menu minimal au format com.canonical.dbusmenu (celui des barres de notification)."""

        ITEMS = [(1, "Ouvrir IgOGS"), (2, None), (3, "Quitter IgOGS")]

        def __init__(self, tray):
            super().__init__("com.canonical.dbusmenu")
            self.tray = tray

        def _item(self, iid, label):
            props = {"type": Variant("s", "separator")} if label is None else {
                "label": Variant("s", tr(label)), "enabled": Variant("b", True), "visible": Variant("b", True)}
            return Variant("(ia{sv}av)", [iid, props, []])

        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> "u":
            return 3

        @dbus_property(access=PropertyAccess.READ)
        def TextDirection(self) -> "s":
            return "ltr"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "normal"

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "as":
            return []

        @method()
        def GetLayout(self, parent_id: "i", recursion_depth: "i", property_names: "as") -> "u(ia{sv}av)":
            root = [0, {"children-display": Variant("s", "submenu")},
                    [self._item(i, label) for i, label in self.ITEMS]]
            return [1, root]

        @method()
        def GetGroupProperties(self, ids: "ai", property_names: "as") -> "a(ia{sv})":
            out = []
            for iid, label in self.ITEMS:
                if not ids or iid in ids:
                    out.append([iid, self._item(iid, label).value[1]])
            return out

        @method()
        def GetProperty(self, iid: "i", name: "s") -> "v":
            for i, label in self.ITEMS:
                if i == iid and label is not None and name == "label":
                    return Variant("s", tr(label))
            return Variant("s", "")

        @method()
        def Event(self, iid: "i", event_id: "s", data: "v", timestamp: "u"):
            if event_id == "clicked":
                self.tray.on_menu(iid)

        @method()
        def EventGroup(self, events: "a(isvu)") -> "ai":
            for iid, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self.tray.on_menu(iid)
            return []

        @method()
        def AboutToShow(self, iid: "i") -> "b":
            return False

        @method()
        def AboutToShowGroup(self, ids: "ai") -> "aiai":
            return [[], []]

        @signal()
        def LayoutUpdated(self) -> "ui":
            return [1, 0]


class Tray:
    def __init__(self, on_open, on_quit, log):
        self.on_open = on_open
        self.on_quit = on_quit
        self.log = log
        self.loop = None
        self.stopping = False
        self.pixmaps = []
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "tray.png")
        try:
            self.pixmaps = [list(_png_to_argb(icon))]
        except (OSError, ValueError) as e:
            log(f"Icône : image illisible ({e})")

    def on_menu(self, iid):
        if iid == 1:
            self.on_open()
        elif iid == 3:
            self.on_quit()

    def start(self):
        if not AVAILABLE:
            self.log("Icône de notification indisponible (paquet dbus-fast absent)")
            return False
        threading.Thread(target=self._thread, daemon=True).start()
        return True

    def _thread(self):
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as e:
            if not self.stopping:  # l'arrêt de la boucle à la fermeture n'est pas une erreur
                self.log(f"Icône de notification indisponible ({e})")

    async def _serve(self):
        bus = await MessageBus().connect()
        bus.export(ITEM_PATH, StatusNotifierItem(self))
        bus.export(MENU_PATH, DBusMenu(self))
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        await bus.request_name(name)
        # La barre (Waybar, KDE…) peut démarrer APRÈS IgOGS, ou redémarrer : on vérifie
        # régulièrement que l'icône y est enregistrée, et on la (ré)enregistre sinon.
        shown = False
        while not self.stopping:
            try:
                intro = await bus.introspect("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher")
                watcher = bus.get_proxy_object("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", intro) \
                    .get_interface("org.kde.StatusNotifierWatcher")
                items = await watcher.get_registered_status_notifier_items()
                if not any(name in item for item in items):
                    await watcher.call_register_status_notifier_item(name)
                    self.log("Icône ajoutée à la zone de notification")
                    shown = True
            except Exception as e:
                if shown:
                    self.log("Zone de notification absente, l'icône sera remise dès son retour")
                    shown = False
                elif not getattr(self, "_waiting_logged", False):
                    self._waiting_logged = True
                    self.log(f"En attente de la zone de notification ({e})")
            await asyncio.sleep(3)

    def stop(self):
        self.stopping = True
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
