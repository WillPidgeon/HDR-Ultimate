# HDR Ultimate

A lightweight system-tray app for per-monitor HDR control on Windows 10/11 and Linux, with hotkeys and automatic game detection.

## Features

- **Per-monitor hotkeys** to switch HDR on or off for each display
- **Game detection:** when a game you've added starts, HDR Ultimate offers to turn HDR on (or does it automatically with "Always for this game")
- **Reads the game's own HDR setting** and skips the offer when HDR is off inside the game
- **Turns HDR back off** when the game closes, unless another HDR game still needs it
- **OLED care:** turns HDR off after you've been tabbed out of a game for a set number of minutes, and back on when you return
- **Tray icon** showing live HDR status for every monitor, kept in sync with changes made in Windows Settings
- **Banner and on-screen confirmations** that never steal focus
- **Light and dark themes**

## Contact

- You can contact me through this discord server for any bug reports or requests [here.](https://discord.gg/CsxstrtyVS)

## Support the project

HDR Ultimate is a personal project. If it's useful to you, donations are appreciated but never expected.

| Coin | Address |
| --- | --- |
| Bitcoin (BTC) | `bc1qeaxzwgxluv7cqd0vycfvev2djm233m8nk9f7yx` |
| Ethereum (ETH) | `0xBFB1fac92565B9B2A995D8bAb9B677fE8d9AB94F` |
| XRP | `rKXSrHt2i6n4qPqgkQwWMhJVhW9A9PDX6R` |

Crypto transactions can't be reversed. Double-check the address and the network before you send.

## Download

Grab the latest build from the **Releases** page. Windows needs no installer, just run the `.exe`. Linux ships as a `.tar.gz`.

## Windows

Requires Windows 10 or 11. Run `HDRUltimate.exe`, then look for the icon in the system tray. Open Settings from the tray menu to add games and set hotkeys.

### Build from source

No SDK needed. `build.cmd` uses the C# compiler that ships with Windows (.NET Framework 4.x).

```
cd windows
build.cmd            :: release build
build.cmd dev        :: console log and a Dev tab with event triggers
build.cmd playtest   :: log file and crash reports
build.cmd all        :: all three
```

Each build has its own settings folder and startup entry, so they never touch each other's data.

## Linux

Wayland with **KDE Plasma 6** or **GNOME 48+**, and Python 3.9+. See [linux/README.md](linux/README.md) for install steps, differences from Windows and troubleshooting.

```
tar xzf HDRUltimate-Linux.tar.gz
cd HDRUltimate-Linux
sh install.sh
```

## Project layout

```
windows/   C# source, icon and build script
linux/     Python source, installer and tests
assets/    screenshots
```

## License

HDR Ultimate is free software, licensed under the [GNU General Public License v3.0](LICENSE). You can use, study, share and modify it, but any version you distribute must also be released under the GPL with its source code.

Copyright (c) 2026 William Pidgeon

## Credits

The interface uses the [Orbitron](https://fonts.google.com/specimen/Orbitron) font, licensed under the SIL Open Font License.
