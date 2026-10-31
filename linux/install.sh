#!/bin/sh
# Installs HDR Ultimate for the current user. ./install.sh --uninstall removes it.
set -e
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP="$HOME/.local/share/hdr-ultimate"
BIN="$HOME/.local/bin/hdr-ultimate"
DESKTOP="$HOME/.local/share/applications/hdr-ultimate.desktop"
ICON="$HOME/.local/share/icons/hicolor/256x256/apps/hdr-ultimate.png"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$APP"
    rm -f "$BIN" "$DESKTOP" "$ICON" "$HOME/.config/autostart/hdr-ultimate.desktop"
    echo "Removed HDR Ultimate. Settings are still in ~/.config/hdr-ultimate"
    exit 0
fi

command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }
mkdir -p "$APP" "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
cp "$SRC/hdr_ultimate.py" "$SRC/Orbitron.ttf" "$APP/"

# System packages when present; otherwise a private copy of Qt. System site packages
# stay visible because GNOME support needs the distro's python3-gi.
PYTHON=python3
if ! python3 -c "import PySide6" 2>/dev/null; then
    echo "Installing Qt (PySide6) for HDR Ultimate..."
    python3 -m venv --system-site-packages "$APP/venv" ||
        { echo "Could not create a Python environment. Install python3-venv, then run this again."; exit 1; }
    "$APP/venv/bin/pip" install --quiet PySide6-Essentials
    PYTHON="$APP/venv/bin/python"
fi

cat > "$BIN" <<EOF
#!/bin/sh
exec "$PYTHON" "$APP/hdr_ultimate.py" "\$@"
EOF
chmod +x "$BIN"

QT_QPA_PLATFORM=offscreen "$BIN" --make-icon "$ICON" || true

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=HDR Ultimate
Comment=Per-monitor HDR control
Exec=$BIN
Icon=hdr-ultimate
Categories=Settings;Utility;
EOF

echo "Installed. Start HDR Ultimate from your app menu, or run: $BIN"
command -v xprop >/dev/null || echo "Note: install xprop (x11-utils or xorg-xprop) for OLED care."
case "${XDG_CURRENT_DESKTOP:-}" in
    *GNOME*) python3 -c "import gi" 2>/dev/null || echo "Note: install python3-gi (python3-gobject on some distros) for GNOME." ;;
esac
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: add ~/.local/bin to your PATH so key bindings can find hdr-ultimate." ;; esac
