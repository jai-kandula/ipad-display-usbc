"""An icon next to the Windows clock while the host runs: shows whether the
iPad is connected, switches profiles, opens the log, and quits.

Needs the optional pystray package (start.bat installs it). Without it the
host runs exactly as before, just without the icon."""
import os
import threading
import time

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:            # not installed, or not on a desktop
    pystray = None

AVAILABLE = pystray is not None


def _image(connected):
    """A small tablet: blue while the iPad is connected, grey otherwise."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    body = (79, 140, 255, 255) if connected else (140, 146, 158, 255)
    d.rounded_rectangle((6, 12, 58, 52), radius=7, fill=body)
    d.rounded_rectangle((11, 17, 53, 47), radius=3, fill=(16, 18, 24, 255))
    if connected:
        d.rectangle((15, 21, 49, 43), fill=(79, 140, 255, 120))
    return img


class Tray:
    def __init__(self, status, deck_ctl, log_path, folder, quit_cb):
        """status() -> (connected: bool, text: str); deck_ctl is the host's
        profile switcher; quit_cb() stops the host."""
        self.status, self.deck, self.log_path, self.folder, self.quit_cb = \
            status, deck_ctl, log_path, folder, quit_cb
        self.icon = None
        self._last = None

    # -- menu
    def _profile_items(self):
        import deck
        data = deck.load()

        def make(name):
            def act(icon, item):
                threading.Thread(target=self._switch, args=(name,), daemon=True).start()

            def checked(item):
                return deck.load().get("active") == name
            return pystray.MenuItem(name, act, checked=checked, radio=True)
        return [make(p["name"]) for p in data["profiles"]]

    def _switch(self, name):
        try:
            self.deck.switch(name)
        except Exception as e:
            print("[tray] couldn't switch profile:", e, flush=True)

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: self.status()[1], None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Profile", pystray.Menu(lambda: self._profile_items())),
            pystray.MenuItem("Open log", lambda icon, item: self._open(self.log_path),
                             visible=lambda item: os.path.exists(self.log_path)),
            pystray.MenuItem("Open folder", lambda icon, item: self._open(self.folder)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit iPad Display", self._quit),
        )

    @staticmethod
    def _open(path):
        try:
            os.startfile(path)
        except Exception as e:
            print("[tray] couldn't open", path, e, flush=True)

    def _quit(self, icon, item):
        icon.stop()
        self.quit_cb()

    # -- life
    def start(self):
        if not AVAILABLE:
            return False
        connected, text = self.status()
        self.icon = pystray.Icon("iPadDisplay", _image(connected), f"iPad Display: {text}", self._menu())
        threading.Thread(target=self.icon.run, name="tray", daemon=True).start()
        threading.Thread(target=self._follow, name="tray-status", daemon=True).start()
        return True

    def _follow(self):
        while True:
            time.sleep(2.0)
            try:
                connected, text = self.status()
                if (connected, text) != self._last:
                    self._last = (connected, text)
                    self.icon.icon = _image(connected)
                    self.icon.title = f"iPad Display: {text}"
                    self.icon.update_menu()
            except Exception:
                pass
