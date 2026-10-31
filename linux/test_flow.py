"""Drives the real app on simulated monitors, offscreen. Run: python test_flow.py <empty temp folder>"""
import json
import os
import sys
import time
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = sys.argv[1]
import hdr_ultimate as h

h.Build.channel, h.Build.fake = "dev", True
h.Build.dir().mkdir(parents=True, exist_ok=True)
(h.Build.dir() / "config.json").write_text(json.dumps({"startup_asked": True, "oled_asked": True, "oled_enabled": True, "games": [
    {"exe": "/g/auto/Auto.exe", "name": "Auto Game", "ask": False, "oled_care": True, "monitors": {"DP-1": "Test OLED"}},
    {"exe": "/g/ask/Ask.exe", "name": "Ask Game", "monitors": {"DP-2": "Test LCD"}}]}))
failures = []
running = set()
h.running_exes = lambda games: set(running)


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


def drive(app, a, folder, theme, QTimer):
    from PySide6.QtWidgets import QPushButton

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def hdr(mon):
        return next(m.hdr_on for m in a.backend.monitors() if m.id == mon)

    def popups():
        return [w for w in app.topLevelWidgets() if w.isVisible() and w.objectName() == "popup"]

    def press(text):
        for w in popups():
            for b in w.findChildren(QPushButton):
                if b.text() == text:
                    b.click()
                    return True
        return False

    # Hand-off: the toggle path a key binding uses.
    a.toggle(["DP-1"])
    check("toggle turns HDR on", hdr("DP-1"))
    pump(1.8)
    check("banner after toggle", len(popups()) == 1)
    a.toggle(["DP-1"])
    check("toggle turns HDR off", not hdr("DP-1"))
    pump(3.6)
    check("popups close by themselves", not popups())

    # Automatic game.
    running.add("auto.exe")
    a.tick()
    check("automatic game turns HDR on", hdr("DP-1"))
    pump(1.8)
    check("banner for game", len(popups()) == 1)
    popups()[0].grab().save(os.path.join(folder, "banner.png"))
    pump(3.3)

    # Prompted game.
    running.add("ask.exe")
    a.tick()
    check("prompt leaves HDR off until answered", not hdr("DP-2"))
    check("prompt shown", any(b.text() == "Turn on HDR" for w in popups() for b in w.findChildren(QPushButton)))
    popups()[0].grab().save(os.path.join(folder, "prompt.png"))
    check("prompt button works", press("Turn on HDR"))
    check("answering turns HDR on", hdr("DP-2"))
    pump(0.1)

    # OLED care.
    focus = [a.running["auto.exe"]]
    a.focus = SimpleNamespace(available=True, game=lambda games: focus[0])
    a.tick()
    focus[0] = None
    a.tick()
    a.oled.last_focused["DP-1"] -= 301
    a.tick()
    check("OLED care turns HDR off after tabbing out", not hdr("DP-1"))
    focus[0] = a.running["auto.exe"]
    a.tick()
    check("HDR back on returning to game", hdr("DP-1"))

    # Closing.
    running.clear()
    a.tick()
    check("HDR off when games close", not hdr("DP-1") and not hdr("DP-2"))
    pump(0.1)

    # Settings: every cell dropdown, and edit dialog.
    a.open_settings()
    s = a.settings
    cfg_file = h.Build.dir() / "config.json"
    seen = {}

    def answer_menu():
        menu = app.activePopupWidget()
        if menu is None:
            QTimer.singleShot(20, answer_menu)
            return
        seen[len(seen)] = [x.text() for x in menu.actions()]
        pick = next((x for x in menu.actions() if x.text() == "Turn HDR on automatically"), None)
        if pick:
            pick.trigger()
        menu.close()

    for col in range(len(s.columns)):
        QTimer.singleShot(20, answer_menu)
        s.cell_menu(1, col)
    check("every column opens a dropdown", len(seen) == len(s.columns))
    check("dropdown edits are saved", json.loads(cfg_file.read_text())["games"][1]["ask"] is False)

    QTimer.singleShot(50, lambda: app.activeModalWidget().accept_game())
    s.edit_game(0)
    check("edit dialog saves", json.loads(cfg_file.read_text())["games"][0]["oled_care"] is True)
    theme.set("light")
    theme.set("dark")
    a.menu.aboutToShow.emit()
    check("tray menu builds", len(a.menu.actions()) >= 4)
    s.close()
    check("no crash logged", "CRASH" not in (h.Build.dir() / "log.txt").read_text())
    return len(failures)


h.take_screenshots = drive
sys.excepthook = lambda kind, value, tb: (failures.append(str(value)), sys.__excepthook__(kind, value, tb))
code = h.run_ui(SimpleNamespace(toggle=None, make_icon=None, keep=False, screenshot=sys.argv[1]))
print("FAILED" if code else "ALL PASSED", failures)
sys.exit(code)
