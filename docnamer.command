#!/bin/bash
# DocNamer Launcher
# Doppelklick im Finder startet diesen Dialog.

# ---------------------------------------------------------------------------
# Pfad zum Python-Skript (liegt im selben Ordner wie diese .command-Datei)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/docnamer_watcher.py"

# ---------------------------------------------------------------------------
# Standard-Ordner auslesen (derselbe Fallback wie im Python-Skript)
# ---------------------------------------------------------------------------
STANDARD_ORDNER="$HOME/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF/Documents"
if [ ! -d "$STANDARD_ORDNER" ]; then
    STANDARD_ORDNER="$HOME/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF"
fi

# Für den Dialog: ~ kürzen wenn möglich
ANZEIGE_ORDNER="${STANDARD_ORDNER/#$HOME/~}"

# ---------------------------------------------------------------------------
# AppleScript-Dialog
# ---------------------------------------------------------------------------
AUSWAHL=$(osascript <<EOF
set dialogResult to button returned of (display dialog "Überwachter Ordner:
${ANZEIGE_ORDNER}

Wie möchtest du DocNamer starten?" ¬
    with title "DocNamer" ¬
    buttons {"Ordner wählen …", "Watcher", "Einmal-Scan"} ¬
    default button "Einmal-Scan")
return dialogResult
EOF
)

# Abbruch (Escape / Fenster geschlossen)
if [ -z "$AUSWAHL" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Ordner wählen
# ---------------------------------------------------------------------------
if [ "$AUSWAHL" = "Ordner wählen …" ]; then
    GEWAEHLTER_ORDNER=$(osascript <<EOF
set chosenFolder to choose folder with prompt "Ordner für DocNamer auswählen:"
return POSIX path of chosenFolder
EOF
    )
    if [ -z "$GEWAEHLTER_ORDNER" ]; then
        exit 0
    fi
    # Trailing Slash entfernen
    GEWAEHLTER_ORDNER="${GEWAEHLTER_ORDNER%/}"

    # Nochmal fragen: Einmal-Scan oder Watcher mit dem gewählten Ordner
    ANZEIGE_GEWAEHLT="${GEWAEHLTER_ORDNER/#$HOME/~}"
    AUSWAHL2=$(osascript <<EOF
set dialogResult to button returned of (display dialog "Gewählter Ordner:
${ANZEIGE_GEWAEHLT}

Wie möchtest du starten?" ¬
    with title "DocNamer" ¬
    buttons {"Abbrechen", "Watcher", "Einmal-Scan"} ¬
    default button "Einmal-Scan")
return dialogResult
EOF
    )
    if [ -z "$AUSWAHL2" ] || [ "$AUSWAHL2" = "Abbrechen" ]; then
        exit 0
    fi
    AUSWAHL="$AUSWAHL2"
    STANDARD_ORDNER="$GEWAEHLTER_ORDNER"
fi

# ---------------------------------------------------------------------------
# Python-Umgebung ermitteln (conda / venv / system)
# ---------------------------------------------------------------------------
if command -v conda &>/dev/null && conda info --envs 2>/dev/null | grep -q "docnamer"; then
    PYTHON=$(conda run -n docnamer which python 2>/dev/null)
    RUN_PREFIX="conda run -n docnamer"
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
    RUN_PREFIX=""
elif [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/venv/bin/python"
    RUN_PREFIX=""
else
    PYTHON=$(command -v python3)
    RUN_PREFIX=""
fi

# ---------------------------------------------------------------------------
# Starten
# ---------------------------------------------------------------------------
echo "=============================="
echo "  DocNamer"
echo "=============================="
echo "  Ordner : $STANDARD_ORDNER"
echo "  Modus  : $AUSWAHL"
echo "  Python : $PYTHON"
echo "=============================="
echo ""

if [ "$AUSWAHL" = "Einmal-Scan" ]; then
    $RUN_PREFIX "$PYTHON" "$PYTHON_SCRIPT" "$STANDARD_ORDNER" --einmal
    echo ""
    echo "✓ Einmal-Scan abgeschlossen."
    # Kurz warten damit man das Ergebnis lesen kann, dann Fenster schließen
    sleep 5
else
    echo "Watcher läuft ... (Fenster schließen zum Beenden)"
    echo ""
    $RUN_PREFIX "$PYTHON" "$PYTHON_SCRIPT" "$STANDARD_ORDNER"
fi
