# HDR Ultimate for Linux

Per-monitor HDR control with game detection, prompts, banners, OLED care, per-game resolution and refresh rate, and light/dark themes.

## Needs

- Wayland with **KDE Plasma 6** or **GNOME 48+**
- Python 3.9+ (the installer fetches Qt if it's missing)
- GNOME: `python3-gi`, and the AppIndicator extension if your distro doesn't include it, for the tray icon
- OLED care: `xprop` (`x11-utils` or `xorg-xprop`)

## Install

```bash
tar xzf HDRUltimate-Linux.tar.gz
cd HDRUltimate-Linux
sh install.sh
```

Remove it with `sh install.sh --uninstall`.

## Differences from Windows

- **Hotkeys:** Wayland doesn't let apps grab keys. Copy a command from Settings > Monitor hotkeys and add it as a custom shortcut in your desktop's keyboard settings.
- **OLED care:** only sees games running through XWayland, which covers Proton games. For native Wayland games it stays inactive rather than risk turning HDR off mid-game.
- **Game detection:** matches the exe file name, including inside Proton's process arguments.
- **Other builds:** `--dev` adds event triggers and `--playtest` keeps a log. Each has its own settings folder.

## Troubleshooting

Run `hdr-ultimate --list` to see what the app detects. To test the interface without HDR hardware, run `hdr-ultimate --dev --fake`.
