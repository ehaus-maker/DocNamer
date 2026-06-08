#!/bin/bash
# =============================================================================
#  DocNamer Installer
#  Doppelklick im Finder genügt – kein Terminal-Wissen erforderlich.
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Hilfsfunktion: Dialogfenster anzeigen
dialog() {
    osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"DocNamer Installer\""
}

dialog_frage() {  # gibt "true" oder "false" zurück
    osascript -e "display dialog \"$1\" buttons {\"Abbrechen\", \"Ja\"} default button \"Ja\" with title \"DocNamer Installer\"" \
        > /dev/null 2>&1 && echo "true" || echo "false"
}

eingabe() {  # Texteingabe, gibt eingegebenen Wert zurück
    osascript -e "set r to text returned of (display dialog \"$1\" default answer \"$2\" with title \"DocNamer Installer\")" \
        -e "return r"
}

fortschritt() {
    echo ""
    echo "──────────────────────────────────────"
    echo "  $1"
    echo "──────────────────────────────────────"
}

# =============================================================================
# Willkommen
# =============================================================================
osascript -e 'display dialog "Willkommen beim DocNamer Installer!\n\nDieses Skript installiert alle nötigen Abhängigkeiten und richtet DocNamer auf diesem Mac ein.\n\nDauer: ca. 2–5 Minuten." buttons {"Abbrechen", "Weiter"} default button "Weiter" with title "DocNamer Installer"' > /dev/null 2>&1 || {
    echo "Installation abgebrochen."
    exit 0
}

# =============================================================================
# 1. API-Key abfragen
# =============================================================================
fortschritt "Schritt 1/4: Anthropic API-Key"

CONFIG_DATEI="$HOME/.docnamer_config"
VORHANDENER_KEY=""
if [[ -f "$CONFIG_DATEI" ]]; then
    VORHANDENER_KEY=$(grep "ANTHROPIC_API_KEY=" "$CONFIG_DATEI" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"' || true)
fi

if [[ -n "$VORHANDENER_KEY" ]]; then
    echo "  → Vorhandener API-Key gefunden, wird beibehalten."
    API_KEY="$VORHANDENER_KEY"
else
    # Schritt 1: Erklärung – eigener Account erforderlich
    osascript -e 'display dialog "DocNamer benötigt einen eigenen Anthropic API-Key.\n\n⚠️  Wichtig: Verwende niemals den Key einer anderen Person – du hättest keine Kontrolle über Kosten und Datenschutz.\n\nSo bekommst du deinen eigenen Key (ca. 2 Minuten):\n\n1. console.anthropic.com im Browser öffnen\n2. Kostenlosen Account anlegen\n3. Guthaben aufladen (z.B. 5 €)\n4. Links im Menü: \"API Keys\" → \"Create Key\"\n5. Key kopieren (beginnt mit sk-ant-...)\n\nDanach auf \"Weiter\" klicken und den Key einfügen." buttons {"Abbrechen", "Weiter"} default button "Weiter" with title "DocNamer – API-Key einrichten"' > /dev/null 2>&1 || {
        echo "Installation abgebrochen."
        exit 0
    }

    # Schritt 2: Sicherheitshinweis
    osascript -e 'display dialog "🔐  Sicherheitshinweis zum API-Key\n\nDein API-Key ist wie ein Passwort:\n\n• Gib ihn niemals an andere Personen weiter\n• Sende ihn nicht per E-Mail oder Chat\n• Du kannst ihn jederzeit unter console.anthropic.com sperren\n\nDocNamer speichert den Key ausschließlich lokal auf diesem Mac (~/.docnamer_config, nur für dich lesbar). Er wird nie ins Netzwerk übertragen, außer direkt an die Anthropic API." buttons {"Verstanden – Weiter"} default button "Verstanden – Weiter" with title "DocNamer – Sicherheitshinweis"' > /dev/null 2>&1 || {
        echo "Installation abgebrochen."
        exit 0
    }

    # Schritt 3: Key per Copy/Paste einfügen
    API_KEY=$(osascript \
        -e 'set r to text returned of (display dialog "API-Key hier einfügen (⌘V):\n\nDer Key beginnt mit sk-ant- und ist ca. 100 Zeichen lang.\nDie Eingabe wird aus Sicherheitsgründen nicht angezeigt." default answer "" with title "DocNamer – API-Key einfügen" with hidden answer)' \
        -e 'return r' 2>/dev/null || true)

    if [[ -z "$API_KEY" || "$API_KEY" != sk-ant-* ]]; then
        dialog "Kein gültiger API-Key erkannt.\n\nDer Key muss mit sk-ant- beginnen.\nBitte Installation erneut starten und Key aus console.anthropic.com kopieren."
        exit 1
    fi

    echo "ANTHROPIC_API_KEY=\"$API_KEY\"" > "$CONFIG_DATEI"
    chmod 600 "$CONFIG_DATEI"
    echo "  → API-Key sicher gespeichert in $CONFIG_DATEI (Zugriffsrechte: nur du)"
fi

# =============================================================================
# 2. Homebrew prüfen / installieren
# =============================================================================
fortschritt "Schritt 2/4: Homebrew & System-Abhängigkeiten"

if ! command -v brew &>/dev/null; then
    echo "  → Homebrew nicht gefunden – wird installiert..."
    osascript -e 'display notification "Homebrew wird installiert, das dauert einige Minuten..." with title "DocNamer Installer"'
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Homebrew zum PATH hinzufügen (Apple Silicon vs Intel)
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
    echo "  → Homebrew installiert."
else
    echo "  → Homebrew bereits vorhanden: $(brew --version | head -1)"
fi

# Systempakete installieren
echo "  → Installiere ocrmypdf, tesseract, tesseract-lang..."
brew install ocrmypdf tesseract tesseract-lang 2>&1 | grep -E "Installing|Already|==>|Error" || true
echo "  → Systempakete OK."

# =============================================================================
# 3. Python & Pip-Pakete
# =============================================================================
fortschritt "Schritt 3/4: Python-Pakete"

PYTHON=""
for P in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$P" &>/dev/null; then
        PYTHON="$P"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    dialog "Python 3 wurde nicht gefunden. Bitte Homebrew neu starten und erneut versuchen."
    exit 1
fi

echo "  → Python: $($PYTHON --version)"
echo "  → Installiere anthropic, pymupdf, watchdog, rumps..."
"$PYTHON" -m pip install --quiet --break-system-packages anthropic pymupdf watchdog rumps 2>&1 | grep -v "^$" || true
echo "  → Python-Pakete OK."

# =============================================================================
# 4. DocNamer.app einrichten
# =============================================================================
fortschritt "Schritt 4/4: DocNamer App einrichten"

# Icon-Pfad im Menubar-Skript auf aktuelles Verzeichnis setzen
MENUBAR_SKRIPT="$SCRIPT_DIR/docnamer_menubar.py"
if grep -q '/Users/ehaus' "$MENUBAR_SKRIPT" 2>/dev/null; then
    sed -i '' "s|/Users/ehaus/[^\"']*icon_template\.png|$SCRIPT_DIR/icon_template.png|g" "$MENUBAR_SKRIPT"
    echo "  → Icon-Pfad in docnamer_menubar.py angepasst."
fi

# In Applications verlinken?
APP_QUELLE="$SCRIPT_DIR/DocNamer.app"
APP_ZIEL="/Applications/DocNamer.app"

if [[ -d "$APP_QUELLE" ]]; then
    ANTWORT=$(dialog_frage "DocNamer.app in den Applications-Ordner kopieren?\n\n(Empfohlen – dann erscheint DocNamer im Launchpad)")
    if [[ "$ANTWORT" == "true" ]]; then
        cp -R "$APP_QUELLE" "$APP_ZIEL"
        echo "  → DocNamer.app → /Applications/"
    fi
fi

# =============================================================================
# Fertig
# =============================================================================
echo ""
echo "  ✓ Installation abgeschlossen!"
echo ""

osascript -e 'display dialog "✅ DocNamer wurde erfolgreich installiert!\n\nSo starten:\n• DocNamer.app im Finder doppelklicken, oder\n• DocNamer im Launchpad öffnen\n\nDas Menüleistensymbol erscheint oben rechts." buttons {"DocNamer starten", "Schließen"} default button "DocNamer starten" with title "DocNamer – Installation abgeschlossen"' 2>/dev/null | grep -q "DocNamer starten" && \
    open "$SCRIPT_DIR/docnamer_menubar.py" 2>/dev/null || \
    "$PYTHON" "$SCRIPT_DIR/docnamer_menubar.py" &

exit 0
