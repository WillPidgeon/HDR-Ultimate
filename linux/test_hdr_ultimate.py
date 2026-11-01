"""Tests for the parts that don't need a Linux desktop. Run: python -m unittest test_hdr_ultimate"""
import unittest

import hdr_ultimate as h
from hdr_ultimate import Game, GameConfig, KdeBackend, Monitor, OledCare

# Shape of kscreen-doctor -o, from libkscreen's doctor.cpp, with its colour codes.
KDE_TEXT = (
    "\x1b[01;32mOutput: \x1b[0;0m1 DP-1 abc-uuid\n"
    "\tenabled\n\tconnected\n\tpriority 1\n"
    "\x1b[01;33m\tHDR: \x1b[0;0menabled\n"
    "\x1b[01;33m\tWide Color Gamut: \x1b[0;0menabled\n"
    "\x1b[01;32mOutput: \x1b[0;0m2 DP-2 def-uuid\n"
    "\x1b[01;33m\tHDR: \x1b[0;0mdisabled\n"
    "\x1b[01;32mOutput: \x1b[0;0m3 HDMI-A-1 ghi-uuid\n"
    "\x1b[01;33m\tHDR: \x1b[0;0mincapable\n"
)


def mode(mode_id, current):
    return (mode_id, 2560, 1440, 144.0, 1.0, [1.0], {"is-current": current} if current else {})


GNOME_STATE = (
    42,
    [(("DP-1", "ACR", "X27U", "1"), [mode("a60", False), mode("a144", True)],
      {"display-name": "X27U", "color-mode": 0, "supported-color-modes": [0, 1]}),
     (("DP-2", "ACR", "VG270U", "2"), [mode("b144", True)],
      {"display-name": "VG270U", "color-mode": 1, "supported-color-modes": [0, 1]}),
     (("HDMI-1", "OLD", "TV", "3"), [mode("c60", True)], {"display-name": "TV"})],
    [(0, 0, 1.0, 0, True, [("DP-1", "ACR", "X27U", "1")], {}),
     (2560, 0, 1.25, 0, False, [("DP-2", "ACR", "VG270U", "2")], {}),
     (5120, 0, 1.0, 1, False, [("HDMI-1", "OLD", "TV", "3")], {})],
    {"layout-mode": 1},
)


class Parsing(unittest.TestCase):
    def test_kde(self):
        m = KdeBackend.parse(KDE_TEXT)
        self.assertEqual([x.id for x in m], ["DP-1", "DP-2", "HDMI-A-1"])
        self.assertEqual([(x.hdr_supported, x.hdr_on) for x in m], [(True, True), (True, False), (False, False)])

    def test_gnome_read(self):
        m = h.gnome_monitors(GNOME_STATE)
        self.assertEqual([(x.id, x.hdr_supported, x.hdr_on) for x in m],
                         [("DP-1", True, False), ("DP-2", True, True), ("HDMI-1", False, False)])

    def test_gnome_keeps_layout(self):
        serial, method, logical, props = h.gnome_config(GNOME_STATE, "DP-1", True, lambda v: v)
        self.assertEqual((serial, method, props), (42, 1, {"layout-mode": 1}))
        self.assertEqual(logical, [
            (0, 0, 1.0, 0, True, [("DP-1", "a144", {"color-mode": 1})]),
            (2560, 0, 1.25, 0, False, [("DP-2", "b144", {"color-mode": 1})]),  # stays on
            (5120, 0, 1.0, 1, False, [("HDMI-1", "c60", {})]),
        ])

    def test_kde_modes(self):
        text = KDE_TEXT.replace("\tenabled\n", "\tenabled\n\x1b[01;33m\tModes: \x1b[0;0m 1:2560x1440@144*!  2:2560x1440@59.95  3:1920x1080@60  \n", 1)
        m = KdeBackend.parse(text)[0]
        self.assertEqual(m.current, (2560, 1440, 144))
        self.assertEqual(m.modes, [(2560, 1440, 144), (2560, 1440, 60), (1920, 1080, 60)])
        self.assertEqual(m.mode_ids[(2560, 1440, 60)], "2")

    def test_pick_mode(self):
        m = Monitor("DP-1", "X", modes=[(2560, 1440, 144), (2560, 1440, 60), (1920, 1080, 60)], current=(2560, 1440, 144))
        self.assertEqual(h.pick_mode(m, 1920, 1080, 0), (1920, 1080, 60))   # 144 not offered there: fastest it has
        self.assertEqual(h.pick_mode(m, 0, 0, 60), (2560, 1440, 60))        # refresh only
        self.assertIsNone(h.pick_mode(m, 1920, 1080, 144))

    def test_gnome_mode_change_moves_neighbours(self):
        first = h.gnome_monitors(GNOME_STATE)[0]  # two 144 Hz modes: the current one's id wins
        self.assertEqual((first.modes, first.current, first.mode_ids), ([(2560, 1440, 144)], (2560, 1440, 144), {(2560, 1440, 144): "a144"}))
        state = (GNOME_STATE[0], [(GNOME_STATE[1][0][0], GNOME_STATE[1][0][1] + [("a1080", 1920, 1080, 60.0, 1.0, [1.0], {})],
                                   GNOME_STATE[1][0][2])] + GNOME_STATE[1][1:], GNOME_STATE[2], GNOME_STATE[3])
        _, _, logical, _ = h.gnome_config(state, "DP-1", None, lambda v: v, "a1080")
        self.assertEqual([(lm[0], lm[5][0][1]) for lm in logical], [(0, "a1080"), (1920, "b144"), (4480, "c60")])
        self.assertEqual(logical[0][5][0][2], {"color-mode": 0})  # colour mode untouched

    def test_in_game_resolution(self):
        cases = [
            ('{ "name": "Resolution", "type": "string_list", "value": "2560x1440", "index": 20 }', '"value": "1920x1080"'),
            ('<screenWidth value="2560" />\n<screenHeight value="1440" />\n<resolutionIndexWindowed value="20" />',
             '<screenWidth value="1920" />\n<screenHeight value="1080" />\n<resolutionIndexWindowed value="20" />'),
            ("ResolutionSizeX=2560\nResolutionSizeY=1440\nWindowPosX=-1\nFrameRateLimit=240.000000",
             "ResolutionSizeX=1920\nResolutionSizeY=1080\nWindowPosX=-1\nFrameRateLimit=240.000000"),
        ]
        for text, expected in cases:
            out, found = GameConfig.replace_resolution(text, 1920, 1080)
            self.assertIn(expected, out)
            self.assertGreater(found, 0)
        self.assertEqual(GameConfig.replace_resolution("MaxFPS=240\nTextureWidth=2048", 1920, 1080)[1], 0)

    def test_paths(self):
        self.assertEqual(h.file_name(r"Z:\games\Foo.exe"), "Foo.exe")
        self.assertEqual(h.file_name("/games/foo/Foo.exe"), "Foo.exe")

    def test_game_settings(self):
        self.assertIs(GameConfig.parse('{ "name": "HDRModes", "type": "x", "value": "HDR10 PQ" }'), True)
        self.assertIs(GameConfig.parse('{ "name": "HDRModes", "value": "None" }'), False)
        self.assertIs(GameConfig.parse("bUseHDRDisplayOutput=True\nHDRBrightness=300"), True)
        self.assertIsNone(GameConfig.parse("HDRBrightness=300"))

    def test_process_match(self):
        g = Game(exe="/games/hl/HogwartsLegacy.exe")
        self.assertTrue(h.owns_process(g, ["python", r"Z:\games\hl\HogwartsLegacy.exe"]))
        self.assertTrue(h.owns_process(g, ["/games/hl/Phoenix/Binaries/Win64/Real.exe"]))
        self.assertFalse(h.owns_process(g, ["/usr/bin/firefox"]))


class Oled(unittest.TestCase):
    def setUp(self):
        self.care = OledCare()
        self.game = Game(exe="/g/a.exe", oled_care=True, monitors={"DP-1": "X"})
        self.on = [Monitor("DP-1", "X", True, True)]
        self.off = [Monitor("DP-1", "X", True, False)]

    def tick(self, now, mons, focused, running=True, enabled=True):
        return self.care.tick(now, mons, [self.game] if running else [], self.game if focused else None, enabled, 60)

    def test_off_after_limit_then_back(self):
        self.tick(0, self.on, True)
        self.assertEqual(self.tick(59, self.on, False), ([], []))
        self.assertEqual(self.tick(61, self.on, False)[1], self.on)
        self.assertEqual(self.tick(63, self.off, False), ([], []))
        self.assertEqual(self.tick(65, self.off, True)[0], self.off)

    def test_native_wayland_game_never_seen_focused_is_left_alone(self):
        for t in range(0, 600, 30):
            self.assertEqual(self.tick(t, self.on, False), ([], []))

    def test_opted_out_game_is_ignored(self):
        self.game.oled_care = False
        self.tick(0, self.on, True)
        self.assertEqual(self.tick(600, self.on, False), ([], []))

    def test_turned_on_by_hand_stays_on_while_tabbed_out(self):
        self.tick(0, self.on, True)
        self.tick(61, self.on, False)
        self.assertEqual(self.tick(200, self.on, False), ([], []))

    def test_master_switch_off_restores(self):
        self.tick(0, self.on, True)
        self.tick(61, self.on, False)
        self.assertEqual(self.tick(63, self.off, False, enabled=False)[0], self.off)

    def test_game_closed_leaves_hdr_off(self):
        self.tick(0, self.on, True)
        self.tick(61, self.on, False)
        self.assertEqual(self.tick(63, self.off, False, running=False), ([], []))
        self.assertEqual(self.care.off, set())


class UsageCheckIn(unittest.TestCase):
    def test_once_a_day_nothing_else_and_opt_out(self):
        import os
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"] or 0))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        os.environ[h.Usage.TEST_VARIABLE] = "http://127.0.0.1:%d/ping" % server.server_port
        channel, h.Build.channel = h.Build.channel, "dev"
        try:
            def run(config):
                usage = h.Usage()
                config.save = lambda: None
                usage.tick(config)
                while usage.sending:
                    time.sleep(0.01)
                usage.tick(config)  # picks up the finished check-in
                usage.next_try = 0
                usage.tick(config)  # same day: nothing more
                time.sleep(0.2)
                return config

            config = run(h.Config(usage_asked=True))
            self.assertEqual(len(seen), 1)
            path, headers, body = seen[0]
            self.assertEqual((path, headers["User-Agent"], body), ("/ping", "HDRUltimate", b""))
            self.assertTrue(config.usage_day)
            run(h.Config(usage_asked=True, usage_enabled=False))
            run(h.Config(usage_asked=False))
            self.assertEqual(len(seen), 1)
        finally:
            h.Build.channel = channel
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
