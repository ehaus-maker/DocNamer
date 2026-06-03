#!/bin/bash
# docnamer LaunchAgent Setup
# Ausführen mit: bash setup.sh

set -e

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     docnamer LaunchAgent Setup       ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ---------------------------------------------------------------------------
# Konfiguration abfragen
# ---------------------------------------------------------------------------

read -p "Pfad zum docnamer-Ordner (z.B. /Users/max/docnamer): " SKRIPT_ORDNER
SKRIPT_ORDNER="${SKRIPT_ORDNER%/}"

read -p "Pfad zum Scan-Ordner (z.B. /Users/max/Scans): " SCAN_ORDNER
SCAN_ORDNER="${SCAN_ORDNER%/}"

read -p "Anthropic API Key (sk-ant-...): " API_KEY

PYTHON=$(which python3)
PLIST_NAME="com.docnamer.watcher"
PLIST_ZIEL="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

# ---------------------------------------------------------------------------
# Abhängigkeiten installieren
# ---------------------------------------------------------------------------

echo ""
echo "→ Installiere Python-Abhängigkeiten..."
pip3 install --quiet anthropic pymupdf watchdog

# ---------------------------------------------------------------------------
# Plist generieren
# ---------------------------------------------------------------------------

echo "→ Erstelle LaunchAgent Plist..."

cat > "$PLIST_ZIEL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SKRIPT_ORDNER}/docnamer_watcher.py</string>
        <string>${SCAN_ORDNER}</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>${API_KEY}</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${SKRIPT_ORDNER}/launchagent_out.log</string>
    <key>StandardErrorPath</key>
    <string>${SKRIPT_ORDNER}/launchagent_err.log</string>
</dict>
</plist>
EOF

# ---------------------------------------------------------------------------
# LaunchAgent laden
# ---------------------------------------------------------------------------

echo "→ Registriere LaunchAgent..."

# Bereits geladen? Erst entladen.
launchctl unload "$PLIST_ZIEL" 2>/dev/null || true
launchctl load -w "$PLIST_ZIEL"

# ---------------------------------------------------------------------------
# Fertig
# ---------------------------------------------------------------------------

echo ""
echo "✓ docnamer läuft jetzt als Hintergrunddienst."
echo ""
echo "  Überwachter Ordner : ${SCAN_ORDNER}"
echo "  Sortiert nach      : ${SCAN_ORDNER}/_Sortiert"
echo "  Fehler nach        : ${SCAN_ORDNER}/_Fehler"
echo "  Log                : ${SKRIPT_ORDNER}/launchagent_out.log"
echo ""
echo "Nützliche Befehle:"
echo "  Status prüfen  : launchctl list | grep docnamer"
echo "  Stoppen        : launchctl unload ~/Library/LaunchAgents/${PLIST_NAME}.plist"
echo "  Neu starten    : launchctl unload ~/Library/LaunchAgents/${PLIST_NAME}.plist && launchctl load -w ~/Library/LaunchAgents/${PLIST_NAME}.plist"
echo "  Log anzeigen   : tail -f ${SKRIPT_ORDNER}/launchagent_out.log"
echo ""
