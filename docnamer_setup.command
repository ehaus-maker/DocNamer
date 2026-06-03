#!/bin/bash
# DocNamer App-Setup
# Einmalig ausführen: erstellt DocNamer.app mit Icon im selben Ordner.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="DocNamer"
APP_PATH="$SCRIPT_DIR/$APP_NAME.app"

echo "=============================="
echo "  DocNamer Setup"
echo "=============================="
echo ""

# ---------------------------------------------------------------------------
# 1. Icon als PNG erzeugen (Python, keine externen Abhängigkeiten)
# ---------------------------------------------------------------------------
echo "→ Erzeuge App-Icon ..."

ICON_PNG="$SCRIPT_DIR/docnamer_icon.png"

python3 - "$ICON_PNG" <<'PYEOF'
import sys, struct, zlib, math

def make_png(size=1024):
    pixels = []
    cx = cy = size / 2
    r_outer = size * 0.44

    for y in range(size):
        row = []
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = math.sqrt(dx*dx + dy*dy)

            if dist > r_outer:
                row.extend([0, 0, 0, 0]); continue

            bg = (26, 26, 46, 255)

            # Dokument
            dx1, dx2 = size*.32, size*.62
            dy1, dy2 = size*.28, size*.66
            dr = size*.025
            fold = size*.09
            fx, fy = dx2 - fold, dy1 + fold

            in_doc = ((dx1+dr <= x <= dx2-dr and dy1 <= y <= dy2) or
                      (dx1 <= x <= dx2 and dy1+dr <= y <= dy2-dr))
            in_fold = x > fx and y < fy and (x-fx)+(fy-y) < fold

            if in_doc and not in_fold:
                # Überschrift-Balken
                if dx1 <= x <= dx2-fold and dy1 <= y <= dy1+size*.06:
                    row.extend([57, 73, 171, 255]); continue
                # Linien
                on_line = False
                for lf, xf in [(.38,size*.58),(.44,size*.58),(.49,size*.58),(.54,size*.58),(.59,size*.54)]:
                    if abs(y - size*lf) < size*.008 and size*.37 <= x <= xf:
                        on_line = True; break
                if on_line:
                    row.extend([159, 168, 218, 255])
                else:
                    row.extend([232, 234, 246, 255])
                continue

            # Stift
            angle = math.pi / 4
            pcx, pcy = size*.60, size*.62
            rdx = (x-pcx)*math.cos(-angle) - (y-pcy)*math.sin(-angle)
            rdy = (x-pcx)*math.sin(-angle) + (y-pcy)*math.cos(-angle)
            pw, ph = size*.038, size*.18

            if abs(rdx) < pw and -ph/2 < rdy < ph*.35:
                row.extend([255, 111, 0, 255])
            elif abs(rdx) < pw and ph*.35 <= rdy < ph/2 and abs(rdx) < (ph/2-rdy)*1.5:
                row.extend([255, 200, 100, 255])
            elif abs(rdx) < pw and -ph/2-size*.03 < rdy < -ph/2:
                row.extend([230, 81, 0, 255])
            else:
                row.extend(bg)
        pixels.append(row)
    return size, pixels

def write_png(path, size=1024):
    w, pixels = make_png(size)

    def chunk(name, data):
        crc = zlib.crc32(name + data) & 0xffffffff
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', crc)

    ihdr = struct.pack('>IIBBBBB', w, w, 8, 6, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes(row) for row in pixels)
    out = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', ihdr)
           + chunk(b'IDAT', zlib.compress(raw, 6))
           + chunk(b'IEND', b''))
    with open(path, 'wb') as f:
        f.write(out)

write_png(sys.argv[1], 1024)
print("  Icon-PNG erstellt.")
PYEOF

# ---------------------------------------------------------------------------
# 2. icns erzeugen
# ---------------------------------------------------------------------------
echo "→ Erzeuge .icns ..."

ICONSET="$SCRIPT_DIR/docnamer.iconset"
mkdir -p "$ICONSET"

# Alle benötigten Größen aus dem 1024er PNG erzeugen
for SIZE in 16 32 64 128 256 512 1024; do
    sips -z $SIZE $SIZE "$ICON_PNG" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" > /dev/null 2>&1
done

# @2x Varianten
for SIZE in 16 32 64 128 256 512; do
    DOUBLE=$((SIZE * 2))
    cp "$ICONSET/icon_${DOUBLE}x${DOUBLE}.png" "$ICONSET/icon_${SIZE}x${SIZE}@2x.png"
done

iconutil -c icns "$ICONSET" -o "$SCRIPT_DIR/docnamer.icns"
rm -rf "$ICONSET"
echo "  .icns erstellt."

# ---------------------------------------------------------------------------
# 3. .app-Struktur anlegen
# ---------------------------------------------------------------------------
echo "→ Erstelle $APP_NAME.app ..."

rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

# Icon kopieren
cp "$SCRIPT_DIR/docnamer.icns" "$APP_PATH/Contents/Resources/docnamer.icns"

# Info.plist
cat > "$APP_PATH/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>             <string>DocNamer</string>
    <key>CFBundleDisplayName</key>      <string>DocNamer</string>
    <key>CFBundleIdentifier</key>       <string>de.ehaus.docnamer</string>
    <key>CFBundleVersion</key>          <string>1.0</string>
    <key>CFBundleIconFile</key>         <string>docnamer</string>
    <key>CFBundleExecutable</key>       <string>docnamer_launcher</string>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>LSMinimumSystemVersion</key>   <string>12.0</string>
    <key>NSHighResolutionCapable</key>  <true/>
</dict>
</plist>
PLIST

# Launcher-Skript (ruft docnamer.command auf)
cat > "$APP_PATH/Contents/MacOS/docnamer_launcher" <<LAUNCHER
#!/bin/bash
SCRIPT_DIR="\$(cd "\$(dirname "\$0")/../../.." && pwd)"
open -a Terminal "\$SCRIPT_DIR/docnamer.command"
LAUNCHER

chmod +x "$APP_PATH/Contents/MacOS/docnamer_launcher"

# ---------------------------------------------------------------------------
# 4. Temporäre Dateien aufräumen
# ---------------------------------------------------------------------------
rm -f "$SCRIPT_DIR/docnamer_icon.png"
rm -f "$SCRIPT_DIR/docnamer.icns"

echo ""
echo "=============================="
echo "  ✓ $APP_NAME.app erstellt!"
echo "  → $APP_PATH"
echo "=============================="
echo ""
echo "Du kannst DocNamer.app jetzt:"
echo "  • per Doppelklick starten"
echo "  • ins Dock ziehen"
echo "  • in /Applications verschieben"
echo ""
sleep 5
