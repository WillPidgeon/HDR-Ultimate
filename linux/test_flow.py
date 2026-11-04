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
h.ModeGuard.MONITOR_GAP = h.ModeGuard.ANY_GAP = 0.0  # instant for most checks; the guard section puts them back
h.Build.dir().mkdir(parents=True, exist_ok=True)
(h.Build.dir() / "config.json").write_text(json.dumps({"startup_asked": True, "oled_asked": True, "oled_enabled": True, "games": [
    {"exe": "/g/auto/Auto.exe", "name": "Auto Game", "ask": False, "oled_care": True, "monitors": {"DP-1": "Test OLED"}},
    {"exe": "/g/ask/Ask.exe", "name": "Ask Game", "monitors": {"DP-2": "Test LCD"}},
    {"exe": "/g/mode/Mode.exe", "name": "Mode Game", "ask": False, "monitors": {"HDMI-A-1": "Old TV"},
     "mode_monitor": "DP-2", "mode_width": 1920, "mode_height": 1080},
    {"exe": "/g/ingame/InGame.exe", "name": "In-game Res", "ask": False, "monitors": {"HDMI-A-1": "Old TV"},
     "config": str(h.Build.dir() / "GameUserSettings.ini"), "mode_monitor": "DP-1",
     "mode_width": 1920, "mode_height": 1080, "mode_hz": 60, "mode_in_game": True},
    {"exe": "/g/banner/Banner.exe", "name": "Banner Game", "ask": False, "monitors": {"DP-1": "Test OLED"},
     "mode_monitor": "DP-1", "mode_hz": 60}]}))
(h.Build.dir() / "GameUserSettings.ini").write_bytes(b"[/Script/Engine.GameUserSettings]\r\nResolutionSizeX=2560\r\nResolutionSizeY=1440\r\nWindowPosX=-1\r\n")
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

    # Display modes.
    def mode(mon):
        return next(m.current for m in a.backend.monitors() if m.id == mon)

    ini = h.Build.dir() / "GameUserSettings.ini"
    running.update({"mode.exe", "ingame.exe"})
    a.tick()
    check("desktop resolution switches, keeping the refresh rate", mode("DP-2") == (1920, 1080, 144))
    check("in-game resolution leaves the desktop size, sets refresh", mode("DP-1") == (2560, 1440, 60))
    check("in-game resolution waits for the game to close", "ResolutionSizeX=2560" in ini.read_text())
    # Changed while the game runs: applied straight away.
    edited = a.running["mode.exe"]  # the same object as in the settings
    edited.mode_hz = 60
    a.tick()
    check("refresh rate changed while running applies now", mode("DP-2") == (1920, 1080, 60))
    edited.mode_width, edited.mode_height, edited.mode_hz = 0, 0, 0
    a.tick()
    check("display mode removed while running restores the monitor", mode("DP-2") == (2560, 1440, 144))
    edited.mode_width, edited.mode_height, edited.mode_hz = 1920, 1080, 0
    a.tick()
    check("display mode set again while running applies", mode("DP-2")[:2] == (1920, 1080))
    edited.mode_width, edited.mode_height, edited.mode_hz = 0, 0, 60
    a.tick()
    check("resolution back to don't change while running: own resolution, new rate", mode("DP-2") == (2560, 1440, 60))
    edited.mode_width, edited.mode_height, edited.mode_hz = 1920, 1080, 0  # back to how the test config has it
    running.clear()
    a.tick()
    check("display modes restored when games close", mode("DP-2") == mode("DP-1") == (2560, 1440, 144))
    text = ini.read_bytes().decode()
    check("in-game resolution written after close", "ResolutionSizeX=1920\r\nResolutionSizeY=1080\r\nWindowPosX=-1" in text)
    check("settings file backed up once", (h.Build.dir() / "GameUserSettings.ini.hdrultimate.bak").exists())
    pump(4)

    # HDR banner with the game's display mode.
    from PySide6.QtWidgets import QLabel
    running.add("banner.exe")
    a.tick()
    pump(1.8)
    texts = [[l.text() for l in w.findChildren(QLabel)] for w in popups()]
    check("one popup: the HDR banner, no separate mode notice", len(texts) == 1)
    check("banner shows the resolution and refresh rate", any("2560 × 1440  ·  60 Hz" in t for t in texts[0]) if texts else False)
    if popups():
        popups()[0].grab().save(os.path.join(folder, "banner-mode.png"))
    running.clear()
    a.tick()
    pump(4)

    # Safety guard: HDR first, then the mode once the monitor has settled, and never two changes back to back.
    h.ModeGuard.MONITOR_GAP, h.ModeGuard.ANY_GAP = 4.0, 1.5
    clock = [1000.0]
    a.guard.clock = lambda: clock[0]
    a.guard.last.clear()
    a.guard.last_any = float("-inf")
    # Danger detector: HDR switching while a game's mode is active, or two changes on a monitor < 4 s apart.
    dangers, last_change = [], {}
    real_set_hdr, real_set_mode = a.backend.set_hdr, a.backend.set_mode

    def noted(mon_id, what):
        if clock[0] - last_change.get(mon_id, -99) < 3.9:
            dangers.append("back to back: " + what)
        last_change[mon_id] = clock[0]

    def watched_hdr(m, on):
        if mode(m.id) != (2560, 1440, 144):
            dangers.append("HDR %s while in a game's mode %s" % ("on" if on else "off", mode(m.id)))
        noted(m.id, "HDR %s" % on)
        return real_set_hdr(m, on)

    def watched_mode(m, md):
        noted(m.id, "mode %s" % (md,))
        return real_set_mode(m, md)

    a.backend.set_hdr, a.backend.set_mode = watched_hdr, watched_mode
    running.add("banner.exe")
    a.tick()
    check("guard: HDR turns on first", hdr("DP-1"))
    check("guard: the mode waits for the monitor to settle", mode("DP-1") == (2560, 1440, 144))
    pump(1.8)
    texts = [[l.text() for l in w.findChildren(QLabel)] for w in popups()]
    check("guard: the banner shows the mode it's switching to", any("2560 × 1440  ·  60 Hz" in t for w in texts for t in w))
    clock[0] += 2
    a.tick()
    check("guard: still waiting 2 s later", mode("DP-1") == (2560, 1440, 144))
    clock[0] += 2.1
    a.tick()
    check("guard: then switches, once", mode("DP-1") == (2560, 1440, 60))
    # Tray toggle while the game's mode is active: the mode goes back first, HDR switches after, then the mode returns.
    clock[0] += 10
    a.toggle(["DP-1"])
    check("guard: toggle puts the monitor back first", mode("DP-1") == (2560, 1440, 144) and hdr("DP-1"))
    clock[0] += 4.1
    a.tick()
    check("guard: then HDR goes off", not hdr("DP-1"))
    clock[0] += 4.1
    a.tick()
    check("guard: then the game's mode returns", mode("DP-1") == (2560, 1440, 60))
    clock[0] += 10
    a.toggle(["DP-1"])
    clock[0] += 4.1
    a.tick()
    clock[0] += 4.1
    a.tick()
    check("guard: toggling back on works the same way", hdr("DP-1") and mode("DP-1") == (2560, 1440, 60))

    running.clear()
    clock[0] += 10
    a.tick()
    check("guard: on close the mode goes back first", mode("DP-1") == (2560, 1440, 144) and hdr("DP-1"))
    clock[0] += 4.1
    a.tick()
    check("guard: then HDR turns off", not hdr("DP-1"))
    check("guard: no HDR switch in a game's mode, no back-to-back changes " + "; ".join(dangers), not dangers)
    a.backend.set_hdr, a.backend.set_mode = real_set_hdr, real_set_mode
    h.ModeGuard.MONITOR_GAP = h.ModeGuard.ANY_GAP = 0.0
    a.guard.clock = time.monotonic
    pump(4)

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

    dialogs = []

    def answer_dialog():
        dlg = app.activeModalWidget()
        if dlg is None:
            QTimer.singleShot(20, answer_dialog)
            return
        dialogs.append(dlg.windowTitle())
        dlg.reject()

    for col, title in enumerate(s.columns):
        if title == "Display mode":  # opens the game's full options instead of a dropdown
            QTimer.singleShot(20, answer_dialog)
        else:
            QTimer.singleShot(20, answer_menu)
        s.cell_menu(1, col)
    check("every column opens a dropdown", len(seen) == len(s.columns) - 1)
    check("display mode column opens the game's options", len(dialogs) == 1)
    check("display mode column shows the mode", s.table.item(2, s.columns.index("Display mode")).text() == "1920×1080"
          and s.table.item(3, s.columns.index("Display mode")).text() == "1920×1080 60Hz in-game")
    check("dropdown edits are saved", json.loads(cfg_file.read_text())["games"][1]["ask"] is False)

    picker = []

    def read_picker():
        dlg = app.activeModalWidget()
        picker.extend(dlg.refresh.itemText(i) for i in range(dlg.refresh.count()))
        dlg.reject()

    QTimer.singleShot(50, read_picker)
    s.edit_game(3)
    check("refresh picker goes up to 480 Hz", len(picker) > 1 and picker[1].startswith("480 Hz  (uses "))
    check("refresh picker keeps the monitor's own rates", any(t.startswith("144 Hz") and "uses" not in t for t in picker))

    QTimer.singleShot(50, lambda: app.activeModalWidget().accept_game())
    s.edit_game(0)
    check("edit dialog saves", json.loads(cfg_file.read_text())["games"][0]["oled_care"] is True)
    for index in (2, 3):
        before = json.loads(cfg_file.read_text())["games"][index]
        QTimer.singleShot(50, lambda: app.activeModalWidget().accept_game())
        s.edit_game(index)
        after = json.loads(cfg_file.read_text())["games"][index]
        keys = ("mode_monitor", "mode_width", "mode_height", "mode_hz", "mode_in_game")
        check("edit dialog keeps display mode (%s)" % before["name"], all(before[k] == after[k] for k in keys))
    # Dev tab triggers for the display mode and update notices.
    pump(4)
    dev = [b for b in s.findChildren(QPushButton)]
    for text in ("Display mode notice", "Display mode failed notice", "Display mode restored notice", "Updated notice"):
        b = next((b for b in dev if b.text() == text), None)
        check("dev trigger: " + text, b is not None and (b.click() or True) and len(popups()) >= 1)
        pump(0.1)
    update = next((b for b in dev if b.text() == "Update notice"), None)
    check("dev trigger: Update notice", update is not None)
    if update:
        update.click()
        a.menu.aboutToShow.emit()
        check("pretend update is the next minor version", a.updates.latest == "%d.%d.0" % (h.parse_version(h.VERSION)[0], h.parse_version(h.VERSION)[1] + 1))
        check("tray menu shows the update", any(x.text().startswith("Update available") for x in a.menu.actions()))
    pump(7.5)
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
