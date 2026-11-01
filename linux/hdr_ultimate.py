#!/usr/bin/env python3
"""HDR Ultimate for Linux: per-monitor HDR control for KDE Plasma and GNOME on Wayland.

Usage:
  hdr-ultimate                     start the app (or open Settings if it's already running)
  hdr-ultimate --toggle DP-1,DP-2  toggle HDR on these monitors; bind this to a key
  hdr-ultimate --list              list monitors
  hdr-ultimate --dev | --playtest  separate settings and log; --dev adds event triggers
  hdr-ultimate --fake              simulated monitors, for testing without HDR hardware
  hdr-ultimate --make-icon FILE    write the app icon as a PNG
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.2.0"
OLED_MIN_MINUTES, OLED_MAX_MINUTES = 1, 30
ICON_NAME = "hdr-ultimate"


class Build:
    channel = "release"
    fake = False

    @classmethod
    def suffix(cls):
        return "" if cls.channel == "release" else "-" + cls.channel

    @classmethod
    def name(cls):
        return "HDR Ultimate" + ("" if cls.channel == "release" else " " + cls.channel.capitalize())

    @classmethod
    def dev(cls):
        return cls.channel == "dev"

    @classmethod
    def dir(cls):
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return Path(base) / ("hdr-ultimate" + cls.suffix())


def log(message):
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "  " + message
    if Build.dev():
        print(line, flush=True)
    if Build.channel != "release":
        try:
            Build.dir().mkdir(parents=True, exist_ok=True)
            with open(Build.dir() / "log.txt", "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ------------------------------------------------------------------ Settings

@dataclass
class Game:
    exe: str = ""
    name: str = ""
    config: str = ""            # the game's own settings file (blank = auto-detect)
    restore: bool = True        # turn HDR back off when the game closes
    ask: bool = True            # ask before turning HDR on
    skip_if_off: bool = False   # don't offer HDR when the game's own HDR option is off
    oled_care: bool = True      # turn HDR off while tabbed out of the game
    monitors: dict = field(default_factory=dict)  # connector -> display name
    # Display mode while the game runs, on one monitor. Blank monitor = leave it; zero = keep that part.
    mode_monitor: str = ""
    mode_width: int = 0
    mode_height: int = 0
    mode_hz: int = 0
    mode_in_game: bool = False  # resolution goes in the game's settings file instead of the desktop

    @property
    def exe_file(self):
        return file_name(self.exe)

    @property
    def has_mode(self):
        return bool(self.mode_monitor) and (self.mode_width > 0 or self.mode_hz > 0)

    @property
    def desktop_size(self):
        return (0, 0) if self.mode_in_game else (self.mode_width, self.mode_height)


@dataclass
class Config:
    show_osd: bool = True
    games_enabled: bool = True
    theme: str = "system"       # system | light | dark
    startup_asked: bool = False
    oled_asked: bool = False
    oled_enabled: bool = False  # master switch, offered on first run; each game can still opt out
    oled_minutes: int = 5       # minutes tabbed out before OLED care turns HDR off
    usage_enabled: bool = True  # daily check-in for the active user count; opt-out offered on first run
    usage_asked: bool = False
    usage_day: str = ""         # UTC date of the last check-in
    games: list = field(default_factory=list)

    @staticmethod
    def path():
        return Build.dir() / "config.json"

    @classmethod
    def load(cls):
        c = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return c
        except (OSError, ValueError) as ex:
            log("Could not read config: " + str(ex))
            return c
        known = {f.name for f in fields(cls)} - {"games"}
        for key, value in data.items():
            if key in known:
                setattr(c, key, value)
        game_fields = {f.name for f in fields(Game)}
        c.games = [Game(**{k: v for k, v in g.items() if k in game_fields}) for g in data.get("games", [])]
        c.oled_minutes = max(OLED_MIN_MINUTES, min(OLED_MAX_MINUTES, int(c.oled_minutes)))
        return c

    def save(self):
        Build.dir().mkdir(parents=True, exist_ok=True)
        temp = self.path().with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")
        temp.replace(self.path())


class Usage:
    """Active user count: one empty check-in per day, with nothing that identifies the user or PC.
    The server (website/functions/ping.js) only adds one to that day's total."""
    ENDPOINT = "https://hdrultra.help/ping"  # blank = no check-ins, and nothing is offered
    TEST_VARIABLE = "HDRULTIMATE_USAGE_TEST_URL"  # test builds only check in to a test server named here

    def __init__(self):
        self.next_try, self.sending, self.sent_day = 0.0, False, None

    @classmethod
    def offered(cls):
        return Build.channel == "release" and bool(cls.ENDPOINT)

    @classmethod
    def url(cls):
        return cls.ENDPOINT if Build.channel == "release" else os.environ.get(cls.TEST_VARIABLE, "")

    def tick(self, config):
        """From the poll timer: at most one check-in per UTC day, retrying every 30 minutes while offline."""
        if self.sent_day:  # a check-in finished on its thread
            config.usage_day, self.sent_day = self.sent_day, None
            config.save()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not self.url() or not config.usage_enabled or not config.usage_asked or config.usage_day == today \
                or self.sending or time.monotonic() < self.next_try:
            return
        self.sending, self.next_try = True, time.monotonic() + 30 * 60
        threading.Thread(target=self.send, args=(self.url(), today), daemon=True).start()

    def send(self, url, today):
        try:
            request = urllib.request.Request(url, data=b"", method="POST", headers={"User-Agent": "HDRUltimate"})
            with urllib.request.urlopen(request, timeout=15) as reply:
                log("Usage check-in: %d" % reply.status)
                # Only the counter answers 204; a parking page or Wi-Fi sign-in page answering 200 doesn't count.
                if reply.status == 204:
                    self.sent_day = today
        except (OSError, ValueError) as ex:
            log("Usage check-in failed: " + str(ex))
        self.sending = False


def file_name(path):
    """Last path part, for both Unix paths and the Windows paths Proton uses."""
    return re.split(r"[\\/]", path or "")[-1]


def game_name_for(exe):
    stem = os.path.splitext(file_name(exe))[0]
    return re.sub(r"(-Win64-Shipping|-WinGDK-Shipping)$", "", stem, flags=re.I) or stem


# ------------------------------------------------------------------ Monitors

@dataclass
class Monitor:
    id: str                     # connector, e.g. DP-1
    name: str
    hdr_supported: bool = False
    hdr_on: bool = False
    modes: list = field(default_factory=list)     # (width, height, hz), largest and fastest first
    current: tuple = None                         # (width, height, hz)
    mode_ids: dict = field(default_factory=dict)  # (width, height, hz) -> the desktop's own mode id


def sort_modes(modes):
    return sorted(set(modes), key=lambda m: (m[0] * m[1], m[0], m[2]), reverse=True)


def pick_mode(monitor, width, height, hz):
    """The mode to switch to; zero keeps that part. A new resolution keeps the current refresh rate if it has it,
    otherwise its fastest. None if the monitor doesn't offer it."""
    if not monitor.current:
        return None
    w, h = (width, height) if width > 0 else monitor.current[:2]
    rates = [m[2] for m in monitor.modes if m[:2] == (w, h)]
    rate = hz if hz > 0 else monitor.current[2] if monitor.current[2] in rates else max(rates, default=0)
    return (w, h, rate) if rate in rates else None


def describe_mode(width, height, hz):
    return " @ ".join(p for p in ("%d × %d" % (width, height) if width > 0 else "", "%d Hz" % hz if hz > 0 else "") if p)


def edid_name(connector):
    """Model name from the monitor's EDID, or the connector name if there isn't one."""
    for edid in Path("/sys/class/drm").glob("card*-" + connector + "/edid"):
        try:
            data = edid.read_bytes()
        except OSError:
            continue
        for offset in (54, 72, 90, 108):
            block = data[offset:offset + 18]
            if len(block) == 18 and block[:3] == b"\0\0\0" and block[3] == 0xFC:
                return block[5:].split(b"\n")[0].decode("ascii", "ignore").strip() or connector
    return connector


class KdeBackend:
    """Plasma 6: kscreen-doctor, which reports and sets HDR per output."""
    desktop = "KDE Plasma"
    ANSI = re.compile(r"\x1b\[[0-9;]*m")

    def __init__(self):
        self.last = []

    @classmethod
    def parse(cls, text):
        found, current = [], None
        for line in cls.ANSI.sub("", text).splitlines():
            head = re.match(r"\s*Output:\s+\S+\s+(\S+)", line)
            if head:
                current = Monitor(head.group(1), edid_name(head.group(1)))
                found.append(current)
                continue
            state = re.match(r"\s*HDR:\s*(\w+)", line)
            if state and current:
                current.hdr_supported = state.group(1) != "incapable"
                current.hdr_on = state.group(1) == "enabled"
            # Modes:  1:2560x1440@144*!  2:1920x1080@60   (* = current, ! = preferred)
            if current and re.match(r"\s*Modes:", line):
                for mode_id, w, h, hz, flags in re.findall(r"(\S+?):(\d+)x(\d+)@([\d.]+)([*!]*)", line):
                    mode = (int(w), int(h), round(float(hz)))
                    current.mode_ids.setdefault(mode, mode_id)
                    if "*" in flags:
                        current.current, current.mode_ids[mode] = mode, mode_id
                current.modes = sort_modes(current.mode_ids)
        return found

    def monitors(self):
        try:
            done = subprocess.run(["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=3)
            self.last = self.parse(done.stdout + done.stderr)
        except (OSError, subprocess.SubprocessError) as ex:
            log("kscreen-doctor failed: " + str(ex))
        return self.last

    def set_hdr(self, monitor, on):
        word = "enable" if on else "disable"
        # Same as the HDR checkbox in System Settings, which sets wide colour too. Not waited on:
        # kscreen-doctor can stall until the screen next redraws. Callers re-read the real state.
        try:
            subprocess.Popen(["kscreen-doctor", "output.%s.hdr.%s" % (monitor.id, word), "output.%s.wcg.%s" % (monitor.id, word)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError as ex:
            log("kscreen-doctor failed: " + str(ex))
            return False

    def set_mode(self, monitor, mode):
        try:
            done = subprocess.run(["kscreen-doctor", "output.%s.mode.%s" % (monitor.id, monitor.mode_ids.get(mode, "%dx%d@%d" % mode))],
                                  capture_output=True, text=True, timeout=10)
            return done.returncode == 0
        except (OSError, subprocess.SubprocessError) as ex:
            log("kscreen-doctor failed: " + str(ex))
            return False


COLOR_DEFAULT, COLOR_BT2100 = 0, 1


def gnome_monitors(state):
    """Monitors from Mutter's GetCurrentState reply."""
    found = []
    for spec, mode_list, props in state[1]:
        modes = props.get("supported-color-modes") or []
        m = Monitor(spec[0], props.get("display-name") or spec[0], COLOR_BT2100 in modes,
                    props.get("color-mode") == COLOR_BT2100)
        for mode_id, w, h, refresh, _scale, _scales, mode_props in mode_list:
            mode = (w, h, round(refresh))
            m.mode_ids.setdefault(mode, mode_id)
            if mode_props.get("is-current"):
                m.current, m.mode_ids[mode] = mode, mode_id
        m.modes = sort_modes(m.mode_ids)
        found.append(m)
    return found


def gnome_config(state, connector, on, wrap_uint, mode_id=None):
    """Arguments for ApplyMonitorsConfig that keep the whole layout and change one monitor's colour mode
    (on = None keeps it) and/or its mode.

    Mutter replaces the entire configuration, so every monitor has to be listed with its current settings.
    It also rejects gaps between monitors, so monitors right of or below a resized one move with its edge.
    """
    by_connector = {m[0][0]: m for m in state[1]}
    physical = state[3].get("layout-mode") == 2

    def size(mode, scale, transform):
        w, h = (mode[2], mode[1]) if transform % 2 else (mode[1], mode[2])  # odd transforms are rotated 90°
        return (w, h) if physical else (round(w / scale), round(h / scale))

    logical, resized = [], None
    for x, y, scale, transform, primary, specs, _props in state[2]:
        members = []
        for spec in specs:
            mon = by_connector[spec[0]]
            mode = current = next((m for m in mon[1] if m[6].get("is-current")), None)
            if current is None:
                raise ValueError("no current mode on " + spec[0])
            if spec[0] == connector and mode_id is not None and mode_id != current[0]:
                mode = next(m for m in mon[1] if m[0] == mode_id)
                old = size(current, scale, transform)
                if mode[5] and not any(abs(s - scale) < 0.01 for s in mode[5]):
                    scale = min(mode[5], key=lambda s: abs(s - scale))
                resized = (x, y, old, size(mode, scale, transform))
            options = {}
            for key in ("color-mode", "rgb-range"):
                if key in mon[2]:
                    options[key] = wrap_uint(mon[2][key])
            if spec[0] == connector and on is not None:
                options["color-mode"] = wrap_uint(COLOR_BT2100 if on else COLOR_DEFAULT)
            members.append((spec[0], mode[0], options))
        logical.append([x, y, scale, transform, primary, members])
    if resized:
        x0, y0, (ow, oh), (nw, nh) = resized
        for lm in logical:
            if lm[0] >= x0 + ow:
                lm[0] += nw - ow
            if lm[1] >= y0 + oh:
                lm[1] += nh - oh
    props = {"layout-mode": wrap_uint(state[3]["layout-mode"])} if "layout-mode" in state[3] else {}
    return state[0], 1, [tuple(lm) for lm in logical], props  # 1 = temporary: nothing to confirm, resets if monitors change


class GnomeBackend:
    """GNOME 48+: Mutter's DisplayConfig D-Bus interface, the same one gdctl uses."""
    desktop = "GNOME"

    def __init__(self):
        from gi.repository import Gio, GLib
        self.GLib = GLib
        self.Gio = Gio
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, method, params=None):
        reply = self.bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                                   "org.gnome.Mutter.DisplayConfig", method, params, None,
                                   self.Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack()

    def monitors(self):
        try:
            return gnome_monitors(self.call("GetCurrentState"))
        except Exception as ex:
            log("Mutter failed: " + str(ex))
            return []

    def apply(self, monitor, on=None, mode_id=None):
        try:
            config = gnome_config(self.call("GetCurrentState"), monitor.id, on, lambda v: self.GLib.Variant("u", v), mode_id)
            self.call("ApplyMonitorsConfig", self.GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", config))
            return True
        except Exception as ex:
            log("Mutter failed: " + str(ex))
            return False

    def set_hdr(self, monitor, on):
        return self.apply(monitor, on=on)

    def set_mode(self, monitor, mode):
        return mode in monitor.mode_ids and self.apply(monitor, mode_id=monitor.mode_ids[mode])


class FakeBackend:
    desktop = "Simulated"

    def __init__(self):
        modes = [(3840, 2160, 60), (2560, 1440, 144), (2560, 1440, 60), (1920, 1080, 144), (1920, 1080, 60)]
        self.list = [Monitor("DP-1", "Test OLED", True, False), Monitor("DP-2", "Test LCD", True, False),
                     Monitor("HDMI-A-1", "Old TV", False, False)]
        for m in self.list:
            m.modes, m.current = sort_modes(modes), (2560, 1440, 144)
            m.mode_ids = {mode: "%dx%d@%d" % mode for mode in modes}

    def monitors(self):
        return [Monitor(m.id, m.name, m.hdr_supported, m.hdr_on, list(m.modes), m.current, dict(m.mode_ids)) for m in self.list]

    def set_hdr(self, monitor, on):
        for m in self.list:
            if m.id == monitor.id:
                m.hdr_on = on
        log("Set HDR %s on %s" % ("on" if on else "off", monitor.name))
        return True

    def set_mode(self, monitor, mode):
        for m in self.list:
            if m.id == monitor.id and mode in m.modes:
                m.current = mode
                return True
        return False


class NoBackend:
    desktop = "Unsupported desktop"

    def monitors(self):
        return []

    def set_hdr(self, monitor, on):
        return False

    def set_mode(self, monitor, mode):
        return False


def pick_backend():
    if Build.fake:
        return FakeBackend()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" in desktop and shutil.which("kscreen-doctor"):
        return KdeBackend()
    if "gnome" in desktop or "ubuntu" in desktop:
        try:
            return GnomeBackend()
        except Exception as ex:
            log("GNOME support unavailable (python3-gi needed): " + str(ex))
    return NoBackend()


# ------------------------------------------------------------------ Game detection

def running_exes(games):
    """Exe file names (lower case) of the games that are running. Proton games show up in process arguments."""
    wanted = {g.exe_file.lower() for g in games}
    found = set()
    if not wanted:
        return found
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                args = f.read().decode("utf-8", "ignore").split("\0")
        except OSError:
            continue
        found |= {file_name(a).lower() for a in args} & wanted
    return found


def owns_process(game, args):
    """Whether a process belongs to the game: its exe, or anything from the game's folder."""
    folder = os.path.dirname(game.exe)
    return any(file_name(a).lower() == game.exe_file.lower() or (folder and a.startswith(folder + "/")) for a in args)


class Focus:
    """Finds the focused game. Wayland hides this, but Proton games run through XWayland, where it's readable.

    available is False when that can't work (xprop missing); callers must then leave HDR alone.
    """

    def __init__(self):
        self.available = bool(shutil.which("xprop") and os.environ.get("DISPLAY"))

    @staticmethod
    def xprop(*args):
        return subprocess.run(["xprop"] + list(args), capture_output=True, text=True, timeout=2).stdout

    def game(self, games):
        if not self.available or not games:
            return None
        try:
            active = re.search(r"window id # (0x[0-9a-f]+)", self.xprop("-root", "_NET_ACTIVE_WINDOW"))
            if not active or int(active.group(1), 16) == 0:
                return None  # a native Wayland window has focus
            info = self.xprop("-id", active.group(1), "_NET_WM_PID", "WM_CLASS")
        except (OSError, subprocess.SubprocessError):
            return None
        pid = re.search(r"_NET_WM_PID\(CARDINAL\) = (\d+)", info)
        args = []
        if pid:
            try:
                with open("/proc/%s/cmdline" % pid.group(1), "rb") as f:
                    args = f.read().decode("utf-8", "ignore").split("\0")
            except OSError:
                pass
        classes = re.findall(r'"([^"]*)"', info)  # Wine sets WM_CLASS to the exe name
        for g in games:
            if owns_process(g, args) or g.exe_file.lower() in [c.lower() for c in classes]:
                return g
        return None


class OledCare:
    """Decides when to turn HDR off on a tabbed-out game's monitors and back on when it regains focus."""

    def __init__(self):
        self.last_focused = {}
        self.off = set()      # monitors OLED care has turned off
        self.seen = set()     # monitors whose game has been seen with focus

    def tick(self, now, monitors, running, focused_game, enabled, limit_seconds):
        """Returns (monitors to turn back on, monitors to turn off)."""
        watched = {path for g in running if enabled and g.oled_care for path in g.monitors}
        held = {path for g in running for path in g.monitors}
        for path in [p for p in self.last_focused if p not in watched]:
            del self.last_focused[path]
        self.seen &= watched
        is_focused = lambda path: focused_game is not None and path in focused_game.monitors
        by_id = {m.id: m for m in monitors}

        restore, turn_off = [], []
        for path in list(self.off):
            if path in held and path in watched and not is_focused(path):
                continue
            self.off.discard(path)
            if path in held and path in by_id and not by_id[path].hdr_on:  # game closed: nothing to come back to
                restore.append(by_id[path])

        for path in watched:
            if is_focused(path):
                self.seen.add(path)
            if path in self.off:
                continue  # HDR switched back on by hand stays on
            m = by_id.get(path)
            # The clock only runs while HDR is actually on, so turning it on by hand gets a full wait.
            if is_focused(path) or path not in self.last_focused or m is None or not m.hdr_on:
                self.last_focused[path] = now
                continue
            # A native Wayland game looks tabbed out all the time, so never act until focus has been seen.
            if path in self.seen and now - self.last_focused[path] >= limit_seconds:
                self.off.add(path)
                turn_off.append(m)
        return restore, turn_off


# ------------------------------------------------------------------ A game's own settings

class GameConfig:
    """Finds a game's settings file (native or inside its Proton prefix) and reads its HDR option."""
    KNOWN = {
        "cyberpunk2077.exe": ["AppData/Local/CD Projekt Red/Cyberpunk 2077/UserSettings.json"],
        "rdr2.exe": ["Documents/Rockstar Games/Red Dead Redemption 2/Settings/system.xml"],
        "gta5.exe": ["Documents/Rockstar Games/GTA V/settings.xml"],
        "witcher3.exe": ["Documents/The Witcher 3/user.settings", "Documents/The Witcher 3/dx12user.settings"],
        "hogwartslegacy.exe": ["AppData/Local/Hogwarts Legacy/Saved/Config/WindowsNoEditor/GameUserSettings.ini",
                               "AppData/Local/Hogwarts Legacy/Saved/Config/Windows/GameUserSettings.ini"],
    }
    EXTS = {".ini", ".json", ".xml", ".cfg", ".txt", ".config", ".settings", ".lua", ".yaml", ".yml"}

    @staticmethod
    def proton_user(exe):
        """The steamuser folder of a Steam game's Proton prefix, found through its app manifest."""
        m = re.match(r"(.*/steamapps)/common/([^/]+)/", exe or "")
        if not m:
            return None
        for manifest in Path(m.group(1)).glob("appmanifest_*.acf"):
            try:
                if '"installdir"' in (text := manifest.read_text(errors="ignore")) and '"%s"' % m.group(2) in text:
                    user = Path(m.group(1)) / "compatdata" / manifest.stem.split("_")[1] / "pfx/drive_c/users/steamuser"
                    return user if user.is_dir() else None
            except OSError:
                pass
        return None

    @staticmethod
    def candidates(game):
        names = []
        stem = re.sub(r"(-Win64-Shipping|-WinGDK-Shipping|_x64|x64|_dx12|_dx11|DX12|DX11)$", "",
                      os.path.splitext(game.exe_file)[0], flags=re.I)
        for s in (game.name, stem):
            s = (s or "").strip()
            for v in (s, s.replace(" ", ""), re.sub(r"[^\w ]", "", s).strip()):
                if len(v) > 1 and v.lower() not in [n.lower() for n in names]:
                    names.append(v)
        return names

    @classmethod
    def scan(cls, folder, depth=4):
        best, best_score, seen = None, -1, 0
        for root, dirs, files in os.walk(folder):
            if root[len(str(folder)):].count(os.sep) >= depth:
                dirs[:] = []
            for f in files:
                path = os.path.join(root, f)
                seen += 1
                if seen > 400:
                    return best
                if os.path.splitext(f)[1].lower() not in cls.EXTS or cls.read(path) is None or cls.parse(cls.read(path)) is None:
                    continue
                score = 2 if re.search("graphic|video|display|setting|config|option|system|user", f.lower()) else 1
                if score > best_score:
                    best, best_score = path, score
        return best

    @classmethod
    def find(cls, game):
        if game.config and os.path.isfile(game.config):
            return game.config
        user = cls.proton_user(game.exe)
        names = cls.candidates(game)
        roots = []
        if user:
            for rel in cls.KNOWN.get(game.exe_file.lower(), []):
                if (user / rel).is_file():
                    return str(user / rel)
            for n in names:
                for ini in (user / "AppData/Local" / n / "Saved/Config").glob("*/GameUserSettings.ini"):
                    return str(ini)
            roots += [user / r for r in ("AppData/Local", "AppData/Roaming", "AppData/LocalLow", "Documents", "Documents/My Games", "Saved Games")]
        roots += [Path.home() / ".config", Path.home() / ".local/share"]
        for root in roots:
            for n in names:
                if (root / n).is_dir():
                    hit = cls.scan(root / n)
                    if hit:
                        return hit
        return cls.scan(os.path.dirname(game.exe)) if game.exe and os.path.isdir(os.path.dirname(game.exe)) else None

    @staticmethod
    def read(path):
        try:
            if os.path.getsize(path) >= 1024 * 1024:
                return None
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return None

    @classmethod
    def enabled_in(cls, path):
        text = cls.read(path) if path and os.path.isfile(path) else None
        return None if text is None else cls.parse(text)

    @classmethod
    def parse(cls, text):
        result = None
        patterns = [
            r'"name"\s*:\s*"([^"]*hdr[^"]*)"[^{}]*?"value"\s*:\s*"?([^",}\r\n]+)',  # Cyberpunk-style JSON
            r'^[ \t]*"?([\w.]*hdr[\w.]*)"?[ \t]*[:=][ \t]*"?([^"\r\n,;}]+)',        # key = value
            r'<([\w.]*hdr[\w.]*)[^>]*?value\s*=\s*"([^"]*)"',                       # <hdr value="true" />
            r'<([\w.]*hdr[\w.]*)>([^<]*)</',                                        # <hdr>true</hdr>
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, text, re.I | re.M):
                value = cls.classify(m.group(1), m.group(2))
                if value is True or result is True:
                    result = True
                elif value is False:
                    result = False
        return result

    @staticmethod
    def classify(key, value):
        key, value = key.lower(), value.strip().lower()
        # Calibration values (HDRBrightness, HDRPaperWhite...) aren't the on/off switch.
        if re.search("bright|nits|white|lum|gamma|contrast|calib|peak|black|saturat|tonemap|exposure|paper|ui|hud", key):
            return None
        if value in ("true", "on", "enabled", "enable", "yes"):
            return True
        if value in ("false", "off", "disabled", "disable", "no", "none", "sdr"):
            return False
        if "hdr" in value:
            return True  # e.g. "HDR10 PQ"
        if re.fullmatch(r"-?\d+", value) and re.search("enable|use|output|mode|on$|toggle|active|^b?hdr$|hdr$", key):
            return int(value) != 0
        return None

    # ---- In-game resolution

    # "2560x1440" under a resolution setting: Cyberpunk's { "name": "Resolution", ..., "value": "2560x1440" }, or key = 2560x1440.
    PAIRS = [re.compile(r'("name"\s*:\s*"[^"]*resolution[^"]*"[^{}]*?"value"\s*:\s*")(\d{3,5})(\s*[xX]\s*)(\d{3,5})', re.I),
             re.compile(r'^([ \t]*"?[\w.]*resolution[\w.]*"?[ \t]*[:=][ \t]*"?)(\d{3,5})(\s*[xX]\s*)(\d{3,5})', re.I | re.M)]
    # Separate width and height settings, group 2 = name, group 3 = number: RDR2's <screenWidth value="2560" />,
    # Unreal's ResolutionSizeX=2560, and "name"/"value" JSON.
    NUMBERS = [re.compile(r'(<([\w.]+)\b[^>]*?value\s*=\s*")(\d{3,5})(?=")', re.I),
               re.compile(r'^([ \t]*"?([\w.]+)"?[ \t]*[:=][ \t]*"?)(\d{3,5})(?![\d.xX])', re.I | re.M),
               re.compile(r'("name"\s*:\s*"([^"]+)"[^{}]*?"value"\s*:\s*)(\d{3,5})(?![\d.])', re.I)]

    @staticmethod
    def dimension(key):
        """'w' or 'h' for a screen width/height setting, else None. Window positions, limits and render sizes don't count."""
        key = key.lower()
        if not re.search("screen|resolution|desired|display|fullscreen", key) or \
                re.search("pos|min|max|hud|border|texture|shadow|render|scale|percent|index|offset|ratio|aspect|safe|fov", key):
            return None
        if re.search("width|sizex$|resx$|resolutionx$", key):
            return "w"
        if re.search("height|sizey$|resy$|resolutiony$", key):
            return "h"
        return None

    @classmethod
    def replace_resolution(cls, text, width, height):
        """The text with every resolution setting rewritten, and how many there were (0 = none found)."""
        found = 0

        def pair(m):
            nonlocal found
            found += 1
            return m.group(1) + str(width) + m.group(3) + str(height)

        def number(m):
            nonlocal found
            d = cls.dimension(m.group(2))
            if d is None or int(m.group(3)) < 320:
                return m.group(0)
            found += 1
            return m.group(1) + str(width if d == "w" else height)

        for pattern in cls.PAIRS:
            text = pattern.sub(pair, text)
        for pattern in cls.NUMBERS:
            text = pattern.sub(number, text)
        return text, found

    @classmethod
    def has_resolution(cls, path):
        text = cls.read(path) if path and os.path.isfile(path) else None
        return text is not None and cls.replace_resolution(text, 1, 1)[1] > 0

    @classmethod
    def set_resolution(cls, path, width, height):
        """Writes the resolution into the game's settings file, keeping its encoding. The first write keeps a backup
        next to it. Only call this while the game is closed: games rewrite their settings when they exit."""
        try:
            data = Path(path).read_bytes()
            encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig" if data[:3] == b"\xef\xbb\xbf" else "utf-8"
            before = data.decode(encoding)
            after, found = cls.replace_resolution(before, width, height)
            if not found:
                log("No resolution setting in " + path)
                return False
            if after != before:
                backup = path + ".hdrultimate.bak"
                if not os.path.exists(backup):
                    shutil.copy2(path, backup)
                Path(path).write_bytes(after.encode(encoding))
                log("Set in-game resolution %dx%d in %s" % (width, height, path))
            return True
        except (OSError, UnicodeError) as ex:
            log("Couldn't set in-game resolution in %s: %s" % (path, ex))
            return False

    @classmethod
    def apply_resolution(cls, game):
        """Writes a game's chosen in-game resolution, if it has one. False when there's nothing to write or it failed."""
        if not game.mode_in_game or game.mode_width <= 0 or not game.config or not os.path.isfile(game.config):
            return False
        return cls.set_resolution(game.config, game.mode_width, game.mode_height)


# ------------------------------------------------------------------ Start at login

class Startup:
    @staticmethod
    def path():
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        return Path(base) / "autostart" / ("hdr-ultimate%s.desktop" % Build.suffix())

    @classmethod
    def enabled(cls):
        return cls.path().exists()

    @classmethod
    def set(cls, on):
        if not on:
            cls.path().unlink(missing_ok=True)
            return
        cls.path().parent.mkdir(parents=True, exist_ok=True)
        launcher = shutil.which("hdr-ultimate") or " ".join(shlex.quote(a) for a in (sys.executable, os.path.abspath(__file__)))
        args = {"dev": " --dev", "playtest": " --playtest"}.get(Build.channel, "")
        cls.path().write_text("[Desktop Entry]\nType=Application\nName=%s\nExec=%s%s\nIcon=%s\nX-GNOME-Autostart-enabled=true\n"
                              % (Build.name(), launcher, args, ICON_NAME), encoding="utf-8")


# ------------------------------------------------------------------ Interface

def run_ui(args):
    from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import (QAction, QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QLinearGradient, QPainter,
                               QPainterPath, QPalette, QPen, QPixmap)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                                   QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea,
                                   QSlider, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

    socket_name = "hdr-ultimate" + Build.suffix()

    # Second launch: hand over to the running app.
    probe = QLocalSocket()
    probe.connectToServer(socket_name)
    if probe.waitForConnected(300):
        probe.write(((args.toggle and "toggle " + args.toggle) or "show").encode())
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return 0

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(Build.name())
    app.setDesktopFileName("hdr-ultimate")

    # ---------------------------------------------------------------- Theme and logo
    LIGHT = dict(bg="#ffffff", surface="#f6f6f8", border="#dedee4", text="#18181b", muted="#6c6c74",
                 accent="#dc2634", hover="#b91c28", accent_text="#ffffff")
    DARK = dict(bg="#000000", surface="#141418", border="#32323a", text="#f0f0f5", muted="#9696a0",
                accent="#9358ff", hover="#7c3aed", accent_text="#ffffff")
    RAINBOW = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#5856d6", "#af52de"]

    font_id = QFontDatabase.addApplicationFont(str(Path(__file__).resolve().parent / "Orbitron.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    orbitron = families[0] if families else app.font().family()

    def header_font(points):
        f = QFont(orbitron, 1)
        f.setPointSizeF(points)
        f.setWeight(QFont.Weight.Bold)
        return f

    body = QFont(app.font())
    body.setFamilies(["Noto Sans", "Cantarell", "Ubuntu", "DejaVu Sans", "Segoe UI"])  # never Orbitron, which is for headers
    body.setPointSizeF(11)
    body.setWeight(QFont.Weight.DemiBold)
    app.setFont(body)

    class Theme(QObject):
        changed = Signal()

        def __init__(self, mode):
            super().__init__()
            self.mode = mode
            self.p = self.resolve()
            app.styleHints().colorSchemeChanged.connect(lambda _s: self.set(self.mode))

        def resolve(self):
            if self.mode == "light":
                return LIGHT
            if self.mode == "dark":
                return DARK
            return DARK if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT

        def set(self, mode):
            self.mode = mode
            self.p = self.resolve()
            self.apply()
            self.changed.emit()

        def apply(self):
            p = self.p
            pal = QPalette()
            for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "bg"), (QPalette.ColorRole.Text, "text"),
                              (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.ButtonText, "text"),
                              (QPalette.ColorRole.Button, "surface"), (QPalette.ColorRole.Highlight, "accent")):
                pal.setColor(role, QColor(p[key]))
            app.setPalette(pal)
            app.setStyleSheet("""
              QWidget {{ background: {bg}; color: {text}; }}
              QLabel[muted="true"] {{ color: {muted}; }}
              QPushButton {{ background: {surface}; border: none; padding: 7px 16px; min-height: 18px; }}
              QPushButton:hover {{ background: {border}; }}
              QPushButton[primary="true"] {{ background: {accent}; color: {accent_text}; }}
              QPushButton[primary="true"]:hover {{ background: {hover}; }}
              QLineEdit, QComboBox {{ background: {surface}; border: none; padding: 6px 10px; min-height: 20px; }}
              QLineEdit:focus {{ border-bottom: 2px solid {accent}; }}
              QComboBox::drop-down {{ border: none; width: 22px; }}
              QComboBox QAbstractItemView {{ background: {surface}; selection-background-color: {accent}; selection-color: {accent_text}; outline: none; }}
              QTabWidget::pane {{ border: none; }}
              QTabBar::tab {{ background: {bg}; color: {muted}; padding: 8px 14px; border: none; font-family: "{font}"; font-weight: bold; }}
              QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
              QTableWidget {{ border: none; gridline-color: {border}; outline: none; }}
              QTableWidget::item {{ padding: 4px 6px; border-bottom: 1px solid {border}; }}
              QTableWidget::item:selected {{ background: {surface}; color: {text}; }}
              QHeaderView::section {{ background: {bg}; color: {text}; border: none; border-bottom: 1px solid {accent}; padding: 6px; font-family: "{font}"; font-weight: bold; }}
              QMenu {{ background: {surface}; border: 1px solid {border}; }}
              QMenu::item {{ padding: 7px 22px; }}
              QMenu::item:selected {{ background: {accent}; color: {accent_text}; }}
              QMenu::item:disabled {{ color: {muted}; }}
              QSlider::groove:horizontal {{ height: 4px; background: {border}; }}
              QSlider::sub-page:horizontal {{ background: {accent}; }}
              QSlider::sub-page:horizontal:disabled {{ background: {muted}; }}
              QSlider::handle:horizontal {{ background: {accent}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
              QSlider::handle:horizontal:disabled {{ background: {muted}; }}
              QScrollArea {{ border: none; }}
            """.format(font=orbitron, **p))

    cfg = Config.load()
    theme = Theme(cfg.theme)
    theme.apply()

    def render_logo(size, states):
        """A rainbow H and outline on a white or black tile, with a bar per HDR monitor. Grey when all are off."""
        p = theme.p
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        g = QPainter(pix)
        g.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        for i, c in enumerate(RAINBOW):
            grad.setColorAt(i / 6, QColor(c))
        main = grad if any(states) else QColor(p["muted"])
        line = max(1.0, size / 16)
        tile = QRectF(line / 2, line / 2, size - line, size - line)
        g.setBrush(QColor(p["bg"]))
        g.setPen(QPen(main, line))
        g.drawRoundedRect(tile, size * 0.15, size * 0.15)
        pad = int(line + 0.999) + max(1, size // 16)
        bar = max(2, size // 7) if states else 0
        gap = max(1, size // 16) if states else 0
        area = QRectF(pad, pad, size - 2 * pad, size - 2 * pad - bar - gap)
        path = QPainterPath()
        path.addText(0, 0, header_font(72), "H")
        b = path.boundingRect()
        scale = min(area.width() / b.width(), area.height() / b.height())
        g.translate(area.center())
        g.scale(scale, scale)
        g.translate(-b.center())
        g.fillPath(path, main)
        g.resetTransform()
        if states:
            n, seg_gap = len(states), max(1, size // 16)
            width = max(1, (size - 2 * pad - seg_gap * (n - 1)) // n)
            g.setPen(Qt.PenStyle.NoPen)
            for i, on in enumerate(states):
                g.setBrush(grad if on else QColor(p["border"]))
                g.drawRect(pad + i * (width + seg_gap), size - pad - bar, width, bar)
        g.end()
        return pix

    def app_icon(states=(True, True)):
        icon = QIcon()
        for s in (16, 22, 24, 32, 48, 64, 128):
            icon.addPixmap(render_logo(s, list(states)))
        return icon

    if args.make_icon:
        render_logo(256, [True, True]).save(args.make_icon)
        return 0

    # ---------------------------------------------------------------- Small controls
    def heading(text, points):
        label = QLabel(text)
        label.setFont(header_font(points))
        return label

    def muted(text):
        label = QLabel(text)
        label.setProperty("muted", True)
        label.setWordWrap(True)
        return label

    def button(text, handler, primary=False):
        b = QPushButton(text)
        b.setProperty("primary", primary)
        b.clicked.connect(lambda _=False: handler())
        return b

    class Toggle(QCheckBox):
        def __init__(self, on, changed=None):
            super().__init__()
            self.setChecked(on)
            self.setFixedSize(38, 20)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if changed:
                self.toggled.connect(changed)

        def hitButton(self, _pos):
            return True

        def paintEvent(self, _event):
            p = theme.p
            g = QPainter(self)
            g.setRenderHint(QPainter.RenderHint.Antialiasing)
            g.setPen(Qt.PenStyle.NoPen)
            on = self.isChecked()
            g.setBrush(QColor(p["accent"] if on and self.isEnabled() else p["border"]))
            g.drawRoundedRect(self.rect(), 10, 10)
            g.setBrush(QColor(p["accent_text"] if on else p["muted"]))
            g.drawEllipse(QRectF(self.width() - 17 if on else 3, 3, 14, 14))

    def line(text, control):
        row = QWidget()
        row.setFixedHeight(48)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(text))
        lay.addStretch(1)
        lay.addWidget(control)
        return row

    # ---------------------------------------------------------------- Notices
    class Popup(QWidget):
        """Top-right notice that never takes focus from a game."""
        open_ones = []

        def __init__(self, ms):
            super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                             | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.ms = ms
            self.setObjectName("popup")
            self.setStyleSheet("#popup {{ background: {surface}; border: 1px solid {border}; border-left: 3px solid {accent}; }}"
                               "QLabel {{ background: transparent; }}".format(**theme.p))

        def present(self):
            Popup.open_ones.append(self)
            self.adjustSize()
            area = QGuiApplication.primaryScreen().availableGeometry()
            y = area.top() + 16 + sum(w.height() + 8 for w in Popup.open_ones[:-1])
            self.move(area.right() - self.width() - 16, y)
            self.show()
            QTimer.singleShot(self.ms, self.close)

        def closeEvent(self, event):
            if self in Popup.open_ones:
                Popup.open_ones.remove(self)
            super().closeEvent(event)

    def show_notice(text, ms=1800):
        w = Popup(ms)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 12, 18, 12)
        lay.addWidget(QLabel(text))
        w.present()

    def show_banner(monitor_name, detail):
        w = Popup(3000)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 10, 22, 10)
        mark = QLabel()
        mark.setPixmap(render_logo(40, [True, True]))
        lay.addWidget(mark)
        text = QVBoxLayout()
        text.addWidget(heading("HDR ON", 13))
        text.addWidget(muted(monitor_name + ("  ·  " + detail if detail else "")))
        lay.addLayout(text)
        w.present()

    class GamePrompt(Popup):
        NO, YES, ALWAYS = 0, 1, 2
        answered = Signal(int)

        def __init__(self, game, names, in_game):
            super().__init__(25000)
            self.done = False
            lay = QVBoxLayout(self)
            lay.setContentsMargins(16, 12, 16, 12)
            lay.addWidget(heading("Turn on HDR for %s?" % game.name, 12.5))
            mons = ", ".join(names)
            lay.addWidget(muted(("On in the game, off on " if in_game is True else "HDR is off on ") + mons))
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(button("Not now", lambda: self.finish(self.NO)))
            row.addWidget(button("Always for this game", lambda: self.finish(self.ALWAYS)))
            row.addWidget(button("Turn on HDR", lambda: self.finish(self.YES), True))
            lay.addLayout(row)

        def finish(self, answer):
            if not self.done:
                self.done = True
                self.answered.emit(answer)
            self.close()

        def dismiss(self):
            self.done = True
            self.close()

        def closeEvent(self, event):
            if not self.done:
                self.done = True
                self.answered.emit(self.NO)  # timed out
            super().closeEvent(event)

    # ---------------------------------------------------------------- Dialogs
    def pick_exe(parent, current=""):
        path, _ = QFileDialog.getOpenFileName(parent, "Select the game's executable", os.path.dirname(current) if current else "",
                                              "Windows or Linux games (*.exe *);;All files (*)")
        return path or None

    class MonitorMenu(QPushButton):
        """Dropdown with a checkbox per monitor."""

        def __init__(self, selected, connected):
            super().__init__()
            self.chosen = dict(selected)
            self.options = {m.id: m.name + ("" if m.hdr_supported else "  — no HDR") for m in connected}
            for key, name in selected.items():
                self.options.setdefault(key, name + "  — not connected")
            self.names = {m.id: m.name for m in connected}
            self.clicked.connect(self.open_menu)
            self.refresh()

        def refresh(self):
            self.setText(", ".join(self.chosen.values()) or "Pick monitors")

        def open_menu(self):
            menu = QMenu(self)
            for key, label in self.options.items():
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(key in self.chosen)
                act.toggled.connect(lambda on, k=key: self.pick(k, on))
            menu.exec(self.mapToGlobal(QPoint(0, self.height())))

        def pick(self, key, on):
            if on:
                self.chosen[key] = self.names.get(key, key)
            else:
                self.chosen.pop(key, None)
            self.refresh()

    class GameDialog(QDialog):
        def __init__(self, parent, game, is_new, connected):
            super().__init__(parent)
            self.setWindowTitle("Add HDR game" if is_new else "Edit HDR game")
            self.game = Game(**asdict(game))
            self.is_new = is_new
            form = QFormLayout(self)
            form.setHorizontalSpacing(24)
            form.setVerticalSpacing(10)
            self.name = QLineEdit(game.name)
            self.exe = button(game.exe_file or "Choose…", self.choose_exe)
            selected = dict(game.monitors)
            if is_new and not selected:
                selected = {m.id: m.name for m in connected if m.hdr_supported}
            self.monitors = MonitorMenu(selected, connected)
            self.config = button("", self.choose_config)
            self.show_config()
            self.on_start = self.choice(0 if game.ask else 1, "Ask me first", "Turn HDR on automatically")
            self.if_off = self.choice(0 if game.skip_if_off else 1, "Leave HDR off", "Offer HDR anyway")
            self.on_close = self.choice(0 if game.restore else 1, "Turn HDR back off", "Leave HDR on")
            self.tabbed = self.choice(1 if game.oled_care else 0, "Keep HDR on", "Turn HDR off (OLED care)")
            # Display mode: the monitor, then the resolutions and refresh rates it offers.
            self.connected = connected
            self.mode_ids = [m.id for m in connected]
            labels = [m.name for m in connected]
            if game.mode_monitor and game.mode_monitor not in self.mode_ids:
                self.mode_ids.append(game.mode_monitor)
                labels.append(game.monitors.get(game.mode_monitor, game.mode_monitor) + "  — not connected")
            pick = next((i for i, k in enumerate(self.mode_ids) if k == game.mode_monitor or (not game.mode_monitor and k in selected)), 0)
            self.mode_monitor = self.choice(0, *(labels or ["No monitors found"]))
            self.mode_monitor.setCurrentIndex(pick)
            self.resolution, self.refresh = QComboBox(), QComboBox()
            self.apply_to = self.choice(1 if game.mode_in_game else 0, "Desktop", "In-game")
            self.sizes, self.rates, self.modes, self.current = [], [], [], None
            self.fill_sizes(game.mode_width, game.mode_height, game.mode_hz)
            self.show_config()
            self.mode_monitor.currentIndexChanged.connect(lambda _: self.fill_sizes(*self.selected_size(), self.selected_hz()))
            self.resolution.currentIndexChanged.connect(lambda _: self.fill_rates(self.selected_hz()))
            for label, box in (("Name", self.name), ("Executable", self.exe), ("Monitors", self.monitors),
                               ("Game settings file", self.config), ("When the game starts", self.on_start),
                               ("If HDR is off in the game", self.if_off), ("When the game closes", self.on_close),
                               ("When tabbed out", self.tabbed), ("Display mode on", self.mode_monitor),
                               ("Resolution", self.resolution), ("Resolution applies to", self.apply_to),
                               ("Refresh rate", self.refresh)):
                form.addRow(label, box)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(button("Save", self.accept_game, True))
            row.addWidget(button("Cancel", self.reject))
            form.addRow(row)
            self.setMinimumWidth(500)

        @staticmethod
        def choice(selected, *options):
            box = QComboBox()
            box.addItems(options)
            box.setCurrentIndex(selected)
            return box

        def show_config(self):
            self.game.config = GameConfig.find(self.game) or ""
            on = GameConfig.enabled_in(self.game.config)
            self.config.setText((file_name(self.game.config) or "None found") + {True: "  ·  HDR on", False: "  ·  HDR off"}.get(on, ""))
            if hasattr(self, "apply_to"):
                self.apply_to.setItemText(1, "In-game" if GameConfig.has_resolution(self.game.config) else "In-game (not in settings file)")

        def fill_sizes(self, width, height, hz):
            """Keeps the given choices where the monitor offers them. A disconnected monitor just shows what was saved."""
            key = self.mode_ids[self.mode_monitor.currentIndex()] if self.mode_ids else None
            mon = next((m for m in self.connected if m.id == key), None)
            self.modes = list(mon.modes) if mon else ([(width, height, hz)] if width > 0 or hz > 0 else [])
            self.current = mon.current if mon else None
            self.sizes = list(dict.fromkeys(m[:2] for m in self.modes if m[0] > 0))
            self.resolution.blockSignals(True)
            self.resolution.clear()
            self.resolution.addItems(["Don't change"] + ["%d × %d" % s + ("  (current)" if self.current and s == self.current[:2] else "")
                                                         for s in self.sizes])
            self.resolution.setCurrentIndex(self.sizes.index((width, height)) + 1 if (width, height) in self.sizes else 0)
            self.resolution.blockSignals(False)
            self.fill_rates(hz)

        def fill_rates(self, hz):
            """The rates for the chosen resolution, or for the current one when it isn't changing."""
            size = self.sizes[self.resolution.currentIndex() - 1] if self.resolution.currentIndex() > 0 else \
                (self.current[:2] if self.current else None)
            self.rates = sorted({m[2] for m in self.modes if m[2] > 0 and (size is None or m[:2] == size)}, reverse=True)
            same = self.current is not None and size == self.current[:2]
            self.refresh.clear()
            self.refresh.addItems(["Don't change"] + ["%d Hz" % r + ("  (current)" if same and r == self.current[2] else "") for r in self.rates])
            self.refresh.setCurrentIndex(self.rates.index(hz) + 1 if hz in self.rates else 0)

        def selected_size(self):
            return self.sizes[self.resolution.currentIndex() - 1] if self.resolution.currentIndex() > 0 else (0, 0)

        def selected_hz(self):
            return self.rates[self.refresh.currentIndex() - 1] if self.refresh.currentIndex() > 0 else 0

        def choose_exe(self):
            path = pick_exe(self, self.game.exe)
            if not path:
                return
            if self.name.text() == game_name_for(self.game.exe):
                self.name.setText(game_name_for(path))
            self.game.exe, self.game.config = path, ""
            self.exe.setText(self.game.exe_file)
            self.show_config()

        def choose_config(self):
            path, _ = QFileDialog.getOpenFileName(self, "Select the game's settings file", os.path.dirname(self.game.config))
            if path:
                self.game.config = path
                self.show_config()

        def accept_game(self):
            if not self.game.exe:
                QMessageBox.information(self, self.windowTitle(), "Pick the game's executable first.")
            elif not self.monitors.chosen:
                QMessageBox.information(self, self.windowTitle(), "Pick at least one monitor.")
            elif self.apply_to.currentIndex() == 1 and self.resolution.currentIndex() > 0 and not GameConfig.has_resolution(self.game.config):
                QMessageBox.information(self, self.windowTitle(), "The game's settings file has no resolution setting HDR Ultimate "
                                        "can change. Choose Desktop, or pick the right file under Game settings file.")
            else:
                g = self.game
                g.name = self.name.text().strip() or game_name_for(g.exe)
                g.monitors = dict(self.monitors.chosen)
                g.ask = self.on_start.currentIndex() == 0
                g.skip_if_off = self.if_off.currentIndex() == 0
                g.restore = self.on_close.currentIndex() == 0
                g.oled_care = self.tabbed.currentIndex() == 1
                (g.mode_width, g.mode_height), g.mode_hz = self.selected_size(), self.selected_hz()
                g.mode_in_game = self.apply_to.currentIndex() == 1
                g.mode_monitor = self.mode_ids[self.mode_monitor.currentIndex()] if g.mode_width or g.mode_hz else ""
                self.accept()

    # ---------------------------------------------------------------- Settings
    class Settings(QWidget):
        closed = Signal()

        def __init__(self, tray_app):
            super().__init__()
            self.a = tray_app
            self.setWindowTitle(Build.name())
            self.setWindowIcon(app_icon())
            self.resize(740, 590)
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            brand = QHBoxLayout()
            brand.setContentsMargins(18, 14, 18, 6)
            self.logo = QLabel()
            brand.addWidget(self.logo)
            brand.addSpacing(10)
            brand.addWidget(heading(Build.name(), 16))
            brand.addStretch(1)
            root.addLayout(brand)
            theme.changed.connect(self.repaint_logo)
            self.repaint_logo()

            self.columns = ["Game", "Exe", "Monitors"] + (["In-game HDR"] if Build.dev() else []) + ["On launch"]
            self.table = QTableWidget(0, len(self.columns))
            self.table.setHorizontalHeaderLabels(self.columns)
            self.table.verticalHeader().hide()
            self.table.setShowGrid(False)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.verticalHeader().setDefaultSectionSize(36)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            header.setHighlightSections(False)
            self.table.cellDoubleClicked.connect(self.cell_menu)

            tabs = QTabWidget()
            tabs.addTab(self.games_page(), "HDR games")
            sub = QTabWidget()
            sub.addTab(self.general_page(), "General")
            sub.addTab(self.hotkeys_page(), "Monitor hotkeys")
            sub.addTab(self.oled_page(), "OLED care")
            if Build.dev():
                sub.addTab(self.dev_page(), "Dev")
            tabs.addTab(sub, "Settings")
            root.addWidget(tabs, 1)
            self.tabs, self.sub = tabs, sub
            self.refill()

        def repaint_logo(self):
            self.logo.setPixmap(render_logo(40, [True, True]))

        def closeEvent(self, event):
            self.closed.emit()
            super().closeEvent(event)

        def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape:
                self.close()

        def games_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(18, 12, 18, 16)
            top = QHBoxLayout()
            top.addWidget(muted("Double-click a cell to change it"), 1)
            top.addWidget(button("Add game", self.add_game, True))
            lay.addLayout(top)
            lay.addWidget(self.table, 1)
            return page

        @staticmethod
        def stack(*rows):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(18, 8, 18, 8)
            lay.setSpacing(0)
            for r in rows:
                lay.addWidget(r)
            lay.addStretch(1)
            return page

        def general_page(self):
            modes = ["system", "light", "dark"]
            box = QComboBox()
            box.addItems(["Match system", "Light", "Dark"])
            box.setCurrentIndex(max(0, modes.index(cfg.theme) if cfg.theme in modes else 0))
            box.setFixedWidth(200)

            def set_theme(i):
                cfg.theme = modes[i]
                cfg.save()
                theme.set(cfg.theme)

            box.currentIndexChanged.connect(set_theme)

            def flag(attr):
                def changed(on):
                    setattr(cfg, attr, on)
                    cfg.save()
                return changed

            rows = [line("Theme", box),
                    line("Start at login", Toggle(Startup.enabled(), Startup.set)),
                    line("On-screen notice", Toggle(cfg.show_osd, flag("show_osd"))),
                    line("Auto HDR for games", Toggle(cfg.games_enabled, flag("games_enabled"))),
                    muted("Desktop: " + self.a.backend.desktop)]
            if Usage.offered():
                rows.insert(4, line("Anonymous active user count", Toggle(cfg.usage_enabled, flag("usage_enabled"))))
            if Build.channel != "release":
                rows.insert(4, line("Logs  (%s %s)" % (Build.channel, VERSION), button(
                    "Open folder", lambda: subprocess.Popen(["xdg-open", str(Build.dir())]))))
            return self.stack(*rows)

        def hotkeys_page(self):
            page = QWidget()
            self.hot = QVBoxLayout(page)
            self.hot.setContentsMargins(18, 12, 18, 12)
            self.hot.setSpacing(2)
            self.hot.addWidget(muted("Wayland doesn't let apps grab keys themselves. Copy a command here and add it as a custom "
                                     "shortcut in your desktop's keyboard settings."))
            self.hot_rows = QVBoxLayout()
            self.hot.addLayout(self.hot_rows)
            self.hot.addStretch(1)
            return page

        def fill_hotkeys(self):
            while self.hot_rows.count():
                self.hot_rows.takeAt(0).widget().deleteLater()
            mons = [m for m in self.a.backend.monitors() if m.hdr_supported]
            entries = [(m.name, m.id) for m in mons]
            if len(mons) > 1:
                entries.append(("All HDR monitors", ",".join(m.id for m in mons)))
            launcher = "hdr-ultimate" + {"dev": " --dev", "playtest": " --playtest"}.get(Build.channel, "")
            for label, ids in entries:
                command = "%s --toggle %s" % (launcher, ids)
                copy = button("Copy command", lambda c=command: (QGuiApplication.clipboard().setText(c), show_notice("Copied  ·  " + c)))
                self.hot_rows.addWidget(line(label, copy))

        def oled_page(self):
            time_row = QWidget()
            lay = QHBoxLayout(time_row)
            lay.setContentsMargins(0, 0, 0, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(OLED_MIN_MINUTES, OLED_MAX_MINUTES)
            slider.setValue(cfg.oled_minutes)
            slider.setFixedWidth(210)
            value = QLabel("%d min" % cfg.oled_minutes)
            value.setFixedWidth(60)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(slider)
            lay.addWidget(value)

            def minutes(v):
                cfg.oled_minutes = v
                value.setText("%d min" % v)
                cfg.save()

            slider.valueChanged.connect(minutes)
            self.oled_games = QWidget()
            self.oled_lay = QVBoxLayout(self.oled_games)
            self.oled_lay.setContentsMargins(0, 0, 0, 0)
            self.oled_lay.setSpacing(0)
            self.oled_hint = muted("")

            def master(on):
                cfg.oled_enabled = on
                cfg.save()
                slider.setEnabled(on)
                value.setEnabled(on)
                self.oled_games.setEnabled(on)

            rows = [line("OLED care", Toggle(cfg.oled_enabled, master)), line("Turn HDR off when tabbed out for", time_row), self.oled_hint, self.oled_games]
            if not self.a.focus.available and not Build.fake:
                rows.insert(2, muted("xprop isn't installed, so OLED care can't see which window has focus and won't do anything."))
            master(cfg.oled_enabled)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.stack(*rows))
            return scroll

        def fill_oled_games(self):
            self.oled_hint.setText("Games" if cfg.games else "Add a game in HDR games first")
            while self.oled_lay.count():
                self.oled_lay.takeAt(0).widget().deleteLater()
            for g in cfg.games:
                def changed(on, game=g):
                    game.oled_care = on
                    cfg.save()
                self.oled_lay.addWidget(line(g.name, Toggle(g.oled_care, changed)))

        def dev_page(self):
            test = Game(exe="/games/TestGame.exe", name="Test Game")
            first = lambda: self.a.backend.monitors()[0].name if self.a.backend.monitors() else "Test monitor"
            page = QWidget()
            grid = QVBoxLayout(page)
            grid.setContentsMargins(18, 12, 18, 12)
            events = [("HDR banner", lambda: show_banner(first(), "")),
                      ("HDR banner: game", lambda: show_banner(first(), test.name)),
                      ("HDR off notice", lambda: show_notice("HDR OFF  ·  " + first())),
                      ("HDR failed notice", lambda: show_notice("HDR didn't turn on  ·  " + first())),
                      ("Game closed notice", lambda: show_notice(test.name + " closed  ·  HDR OFF  ·  " + first())),
                      ("OLED care notice", lambda: show_notice("OLED care  ·  HDR OFF  ·  " + first())),
                      ("Game prompt", lambda: GamePrompt(test, [first()], True).present()),
                      ("Crash report", lambda: log("Test crash from the Dev tab"))]
            for i in range(0, len(events), 2):
                row = QHBoxLayout()
                for text, action in events[i:i + 2]:
                    row.addWidget(button(text, lambda t=text, a=action: (log("Dev trigger: " + t), a())))
                grid.addLayout(row)
            grid.addStretch(1)
            return page

        def refill(self):
            self.table.setRowCount(len(cfg.games))
            for row, g in enumerate(cfg.games):
                cells = [g.name, g.exe_file, ", ".join(g.monitors.values())]
                if Build.dev():
                    cells.append({True: "On", False: "Off"}.get(GameConfig.enabled_in(g.config), "—"))
                cells.append("Ask" if g.ask else "Automatic")
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setToolTip(g.exe)
                    self.table.setItem(row, col, item)
            self.fill_oled_games()
            self.fill_hotkeys()

        def saved(self):
            cfg.save()
            self.refill()

        def add_game(self):
            exe = pick_exe(self)
            if not exe:
                return
            index = next((i for i, g in enumerate(cfg.games) if g.exe == exe), -1)
            self.edit_game(index, Game(exe=exe, name=game_name_for(exe)))

        def edit_game(self, index, fresh=None):
            dlg = GameDialog(self, fresh if index < 0 else cfg.games[index], index < 0, self.a.backend.monitors())
            if dlg.exec():
                if index < 0:
                    cfg.games.append(dlg.game)
                else:
                    cfg.games[index] = dlg.game
                # A running game would overwrite it on exit; it's written when the game closes instead.
                if dlg.game.exe_file.lower() not in running_exes([dlg.game]):
                    GameConfig.apply_resolution(dlg.game)
                self.saved()

        def cell_menu(self, row, col):
            """Double-clicking a cell opens that cell's options as a dropdown."""
            g = cfg.games[row]
            title = self.columns[col]
            menu = QMenu(self)
            if title == "Game":
                menu.addAction("Rename / edit all options…", lambda: self.edit_game(row))
                menu.addAction("Remove game", lambda: (cfg.games.pop(row), self.saved()))
            elif title == "Exe":
                def change():
                    exe = pick_exe(self, g.exe)
                    if exe:
                        if g.name == game_name_for(g.exe):
                            g.name = game_name_for(exe)
                        g.exe, g.config = exe, ""
                        g.config = GameConfig.find(g) or ""
                        self.saved()
                menu.addAction("Choose a different exe…", change)
                menu.addAction("Open file location", lambda: subprocess.Popen(["xdg-open", os.path.dirname(g.exe)]))
            elif title == "Monitors":
                chooser = MonitorMenu(g.monitors, self.a.backend.monitors())
                menu.deleteLater()
                chooser.open_menu()
                if chooser.chosen:  # a game always needs at least one monitor
                    g.monitors = chooser.chosen
                    self.saved()
                return
            elif title == "In-game HDR":
                def detect():
                    g.config = ""
                    g.config = GameConfig.find(g) or ""
                    self.saved()
                menu.addAction("Detect again", detect)
            else:
                def ask(value):
                    g.ask = value
                    self.saved()
                menu.addAction("Ask me first", lambda: ask(True))
                menu.addAction("Turn HDR on automatically", lambda: ask(False))
            rect = self.table.visualRect(self.table.model().index(row, col))
            menu.exec(self.table.viewport().mapToGlobal(rect.bottomLeft()))

    # ---------------------------------------------------------------- App
    class TrayApp(QObject):
        def __init__(self):
            super().__init__()
            self.backend = pick_backend()
            self.focus = Focus()
            self.oled = OledCare()
            self.usage = Usage()
            self.settings = None
            self.running = {}     # exe file name -> Game
            self.auto_on = {}     # monitor -> exe names holding it on
            self.mode_set = {}    # monitor -> (mode to go back to, exe names holding the change)
            self.prompts = {}
            self.last_indicator = None
            for m in self.backend.monitors():
                log("Monitor: %s (%s) HDR supported=%s on=%s" % (m.name, m.id, m.hdr_supported, m.hdr_on))
            log("--- %s %s on %s" % (Build.name(), VERSION, self.backend.desktop))

            self.tray = QSystemTrayIcon(app_icon())
            self.menu = QMenu()
            self.menu.aboutToShow.connect(self.build_menu)
            self.build_menu()
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self.tray_click)
            self.tray.show()
            theme.changed.connect(self.force_indicator)

            self.server = QLocalServer(self)
            QLocalServer.removeServer(socket_name)  # left behind by a crash
            self.server.listen(socket_name)
            self.server.newConnection.connect(self.command)

            app.aboutToQuit.connect(lambda: self.restore_modes(None))  # don't leave a game's display mode behind
            self.check_games()
            self.update_indicator()
            self.timer = QTimer(self)
            self.timer.setInterval(2000)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

            if not cfg.startup_asked and not Build.dev():
                self.ask_startup()
            if not cfg.oled_asked and not Build.dev():
                self.ask_oled()
            if not cfg.usage_asked and Usage.offered():
                self.ask_usage()
            # Without a tray (stock GNOME has none) Settings is the only way in.
            if not QSystemTrayIcon.isSystemTrayAvailable() or not cfg.games:
                self.open_settings()
            if isinstance(self.backend, NoBackend):
                show_notice("HDR needs Plasma 6 or GNOME 48+ on Wayland", 7000)

        def ask_startup(self):
            answer = QMessageBox.question(None, Build.name(), "Start %s when you log in?" % Build.name())
            if answer == QMessageBox.StandardButton.Yes:
                Startup.set(True)
            cfg.startup_asked = True
            cfg.save()

        def ask_oled(self):
            answer = QMessageBox.question(
                None, Build.name(),
                "Turn on OLED care?\n\nIt turns HDR off on a game's monitors after you've been tabbed out of it "
                "for a few minutes, and back on when you return, to help prevent burn-in. "
                "You can change this any time under Settings > OLED care.")
            cfg.oled_enabled = answer == QMessageBox.StandardButton.Yes
            cfg.oled_asked = True
            cfg.save()

        def ask_usage(self):
            """On by default; this is the chance to opt out. No check-in goes out until it's answered."""
            box = QMessageBox(QMessageBox.Icon.Question, Build.name(), "Anonymous active user count")
            box.setInformativeText("HDR Ultimate checks in once a day, only to count how many people are using it. "
                                   "Nothing about you, your PC, your games or your settings is sent. "
                                   "You can turn this off any time under Settings.")
            keep = box.addButton("Keep it on", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Opt out", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            cfg.usage_enabled = box.clickedButton() is keep
            cfg.usage_asked = True
            cfg.save()

        def tick(self):
            self.check_games()
            self.check_oled()
            self.update_indicator()
            self.usage.tick(cfg)

        def command(self):
            sock = self.server.nextPendingConnection()

            def read():
                text = bytes(sock.readAll()).decode()
                if text.startswith("toggle "):
                    self.toggle(text.split(" ", 1)[1].split(","))
                elif text == "show":
                    self.open_settings()

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)

        def tray_click(self, reason):
            if reason == QSystemTrayIcon.ActivationReason.Trigger:
                self.open_settings()

        def build_menu(self):
            self.menu.clear()
            for m in self.backend.monitors():
                act = self.menu.addAction(m.name + ("" if m.hdr_supported else "  — no HDR"))
                act.setCheckable(True)
                act.setChecked(m.hdr_on)
                act.setEnabled(m.hdr_supported)
                act.triggered.connect(lambda _=False, mon=m: self.toggle_one(mon))
            self.menu.addSeparator()
            for g in self.running.values():
                self.menu.addAction("Detected: " + g.name).setEnabled(False)
            self.menu.addAction("Settings", self.open_settings)
            self.menu.addAction("Exit", app.quit)

        def force_indicator(self):
            self.last_indicator = None
            self.update_indicator()

        def update_indicator(self):
            mons = [m for m in self.backend.monitors() if m.hdr_supported]
            tip = "\n".join("%s: HDR %s" % (m.name, "ON" if m.hdr_on else "off") for m in mons) or "HDR Ultimate - no HDR monitors"
            if tip != self.last_indicator:
                self.last_indicator = tip
                self.tray.setIcon(app_icon([m.hdr_on for m in mons]))
                self.tray.setToolTip(tip)

        def notify(self, text, ms=1800):
            if cfg.show_osd:
                show_notice(text, ms)

        def announce_on(self, monitors, detail):
            """Waits for the display to settle, re-reads the real state, and only then shows the banner."""
            if not monitors:
                return
            ids = {m.id for m in monitors}

            def check():
                now = [m for m in self.backend.monitors() if m.id in ids]
                for m in now:
                    if m.hdr_on and cfg.show_osd:
                        show_banner(m.name, detail)
                missed = [m.name for m in now if not m.hdr_on]
                if missed:
                    self.notify("HDR didn't turn on  ·  " + ", ".join(missed))
                self.update_indicator()

            QTimer.singleShot(1500, check)

        def toggle(self, ids):
            targets = [m for m in self.backend.monitors() if m.id in ids and m.hdr_supported]
            if not targets:
                self.notify("No HDR monitor from this hotkey is connected")
                return
            turn_on = any(not m.hdr_on for m in targets)  # mixed -> all on; all on -> all off
            failed = [m.name for m in targets if not self.backend.set_hdr(m, turn_on)]
            if turn_on:
                self.announce_on([m for m in targets if m.name not in failed], "")
            else:
                self.notify("HDR OFF  ·  " + ", ".join(m.name for m in targets))
            if failed:
                self.notify("Failed: " + ", ".join(failed))
            self.update_indicator()

        def toggle_one(self, m):
            self.toggle([m.id])

        def check_games(self):
            games = cfg.games if cfg.games_enabled else []
            found = running_exes(games)
            for g in games:
                key = g.exe_file.lower()
                if key in found and key not in self.running:
                    self.running[key] = g
                    self.game_started(g, key)
            for key, g in list(self.running.items()):
                if key not in found:
                    del self.running[key]
                    self.game_stopped(g, key)

        def game_started(self, g, key):
            log("Game started: %s (%s)" % (g.name, g.exe))
            self.apply_mode(g, key)
            for path in g.monitors:
                if path in self.auto_on:
                    self.auto_on[path].add(key)
            off = [m for m in self.backend.monitors() if m.hdr_supported and m.id in g.monitors and not m.hdr_on]
            if not off:
                return  # HDR already on everywhere this game wants it
            g.config = GameConfig.find(g) or ""
            in_game = GameConfig.enabled_in(g.config)
            log("In-game HDR for %s: %s (%s)" % (g.name, in_game, g.config or "no settings file"))
            if in_game is False and g.skip_if_off:
                self.notify(g.name + ": HDR is off in the game's settings  ·  HDR left off", 7000)
                return
            if not g.ask:
                self.enable_for(g, key)
                return
            prompt = GamePrompt(g, [m.name for m in off], in_game)

            def answered(answer):
                self.prompts.pop(key, None)
                if answer == GamePrompt.ALWAYS:
                    g.ask = False
                    cfg.save()
                if answer != GamePrompt.NO and key in self.running:
                    self.enable_for(g, key)

            prompt.answered.connect(answered)
            self.prompts[key] = prompt
            prompt.present()

        def apply_mode(self, g, key):
            """Switches the game's monitor to its resolution and refresh rate. A monitor already in that mode
            is left alone afterwards too, the same as HDR."""
            width, height = g.desktop_size
            if not g.has_mode or (width == 0 and g.mode_hz == 0):
                return  # in-game resolution only: written when the game closes
            m = next((x for x in self.backend.monitors() if x.id == g.mode_monitor), None)
            if not m:
                log("Display mode for %s: monitor not connected" % g.name)
                return
            mode = pick_mode(m, width, height, g.mode_hz)
            if mode is None or (mode != m.current and not self.backend.set_mode(m, mode)):
                self.notify("%s  ·  couldn't switch %s to %s" % (g.name, m.name, describe_mode(width, height, g.mode_hz)), 7000)
                return
            if m.id not in self.mode_set:
                if mode == m.current:
                    return
                self.mode_set[m.id] = (m.current, set())
            self.mode_set[m.id][1].add(key)
            if mode != m.current:
                log("Set display mode %s on %s" % (describe_mode(*mode), m.name))
                self.notify("%s  ·  %s  ·  %s" % (g.name, describe_mode(*mode), m.name))

        def restore_modes(self, key):
            """Puts back each monitor's mode from before the game once no running game holds it (key None = all)."""
            restored = []
            mons = {m.id: m for m in self.backend.monitors()} if self.mode_set else {}
            for mon_id, (original, holders) in list(self.mode_set.items()):
                if key is not None:
                    if key not in holders:
                        continue
                    holders.discard(key)
                    if holders:
                        continue
                del self.mode_set[mon_id]
                m = mons.get(mon_id)
                if m and original and (m.current == original or self.backend.set_mode(m, original)):
                    log("Restored display mode on " + m.name)
                    restored.append(m.name)
            return restored

        def enable_for(self, g, key):
            changed = []
            for m in self.backend.monitors():
                if not m.hdr_supported or m.id not in g.monitors:
                    continue
                if m.id in self.auto_on:
                    self.auto_on[m.id].add(key)
                elif not m.hdr_on and self.backend.set_hdr(m, True):  # already on: leave it on afterwards too
                    self.auto_on[m.id] = {key}
                    changed.append(m)
            self.announce_on(changed, g.name)
            self.update_indicator()

        def game_stopped(self, g, key):
            log("Game stopped: " + g.name)
            if key in self.prompts:
                self.prompts.pop(key).dismiss()
            mode_restored = bool(self.restore_modes(key))
            # The game saved its own settings as it exited, so put the chosen resolution back for next time.
            # cfg.games, because editing a running game replaces its object.
            GameConfig.apply_resolution(next((x for x in cfg.games if x.exe == g.exe), g))
            mons = {m.id: m for m in self.backend.monitors()}
            changed = []
            for path, holders in list(self.auto_on.items()):
                if key not in holders:
                    continue
                holders.discard(key)
                if holders:
                    continue  # another HDR game still needs it
                del self.auto_on[path]
                m = mons.get(path)
                if g.restore and m and m.hdr_on and self.backend.set_hdr(m, False):
                    changed.append(m.name)
            parts = (["HDR OFF  ·  " + ", ".join(changed)] if changed else []) + (["display mode restored"] if mode_restored else [])
            if parts:
                self.notify("%s closed  ·  %s" % (g.name, "  ·  ".join(parts)))
            self.update_indicator()

        def check_oled(self):
            if not self.focus.available:
                return
            # cfg.games, because editing a running game replaces its object.
            running = [g for g in cfg.games if g.exe_file.lower() in self.running]
            if not self.oled.off and not any(g.oled_care for g in running):
                return
            restore, turn_off = self.oled.tick(time.monotonic(), self.backend.monitors(), running, self.focus.game(running),
                                               cfg.oled_enabled, cfg.oled_minutes * 60)
            turned = [m for m in restore if self.backend.set_hdr(m, True)]
            if turned:
                log("OLED care restored HDR: " + ", ".join(m.name for m in turned))
                self.announce_on(turned, "")
            names = [m.name for m in turn_off if self.backend.set_hdr(m, False)]
            if names:
                log("OLED care turned HDR off: " + ", ".join(names))
                self.notify("OLED care  ·  HDR OFF  ·  " + ", ".join(names))

        def open_settings(self):
            if self.settings:
                self.settings.raise_()
                self.settings.activateWindow()
                return
            self.settings = Settings(self)
            self.settings.closed.connect(self.settings_closed)
            self.settings.show()

        def settings_closed(self):
            self.settings = None
            if not QSystemTrayIcon.isSystemTrayAvailable():
                app.quit()

    tray_app = TrayApp()
    if args.toggle:
        tray_app.toggle(args.toggle.split(","))
        if not (args.keep or args.screenshot):
            QTimer.singleShot(2500, app.quit)
    if args.screenshot:
        return take_screenshots(app, tray_app, args.screenshot, theme, QTimer)
    return app.exec()


def take_screenshots(app, tray_app, folder, theme, QTimer):
    """Test aid: saves each page of Settings to images, then exits."""
    tray_app.open_settings()
    s = tray_app.settings
    pages = [("games", 0, None), ("general", 1, 0), ("hotkeys", 1, 1), ("oled", 1, 2)]
    for mode in ("dark", "light"):
        theme.set(mode)
        for name, top, sub in pages:
            s.tabs.setCurrentIndex(top)
            if sub is not None:
                s.sub.setCurrentIndex(sub)
            app.processEvents()
            s.grab().save(os.path.join(folder, "%s-%s.png" % (mode, name)))
    return 0


def main():
    import argparse
    parser = argparse.ArgumentParser(add_help=True, description="HDR Ultimate for Linux")
    parser.add_argument("--toggle", help="comma-separated monitors to toggle")
    parser.add_argument("--list", action="store_true", help="list monitors")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--playtest", action="store_true")
    parser.add_argument("--fake", action="store_true", help="simulated monitors")
    parser.add_argument("--make-icon", dest="make_icon")
    parser.add_argument("--keep", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", help=argparse.SUPPRESS)
    args = parser.parse_args()
    Build.channel = "dev" if args.dev else "playtest" if args.playtest else "release"
    Build.fake = args.fake

    if args.list:
        for m in pick_backend().monitors():
            print("%-12s %-20s %s" % (m.id, m.name, ("HDR on" if m.hdr_on else "HDR off") if m.hdr_supported else "no HDR"))
        return 0

    # Under Wayland an app can't place its own windows, which the top-right notices rely on,
    # so run through XWayland when it's there.
    if sys.platform.startswith("linux"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb;wayland")

    sys.excepthook = lambda kind, value, tb: log("CRASH\n" + "".join(traceback.format_exception(kind, value, tb)))
    return run_ui(args)


if __name__ == "__main__":
    sys.exit(main())
