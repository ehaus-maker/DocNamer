#!/usr/bin/env python3
import os, sys, json, threading, subprocess, rumps

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
WATCHER_SCRIPT   = os.path.join(SCRIPT_DIR, "docnamer_watcher.py")
KORREKTUR_SCRIPT = os.path.join(SCRIPT_DIR, "docnamer_korrektur.py")
AUSGABE_BASIS    = os.path.expanduser("~/Documents/DocNamer")
LOG_DATEI        = os.path.join(AUSGABE_BASIS, "docnamer.log")
KORREKTUREN_JSON = os.path.join(SCRIPT_DIR, "korrekturen.json")
KATEGORIEN_JSON  = os.path.join(SCRIPT_DIR, "kategorien.json")

STANDARD_ORDNER = os.path.expanduser("~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF/Documents")
if not os.path.exists(STANDARD_ORDNER):
    STANDARD_ORDNER = os.path.expanduser("~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF")

PYTHON = "/opt/homebrew/bin/python3"

class DocNamerApp(rumps.App):

    def __init__(self):
        super().__init__("DN", icon="/Users/ehaus/Documents/GitHub/DocNamer/icon_template.png", template=True, quit_button=None)
        self.watcher_prozess = None
        self.scan_ordner = STANDARD_ORDNER
        self.menu = [
            rumps.MenuItem("📂 Ordner waehlen", callback=self.ordner_waehlen),
            rumps.separator,
            rumps.MenuItem("Einmal-Scan", callback=self.einmal_scan),
            rumps.MenuItem("Watcher starten", callback=self.watcher_starten),
            rumps.MenuItem("Watcher stoppen", callback=self.watcher_stoppen),
            rumps.separator,
            rumps.MenuItem("Korrektur erfassen", callback=self.korrektur_erfassen),
            rumps.MenuItem("Korrekturen anzeigen", callback=self.korrekturen_anzeigen),
            rumps.MenuItem("Kategorien editieren", callback=self.kategorien_editieren),
            rumps.separator,
            rumps.MenuItem("Log anzeigen", callback=self.log_anzeigen),
            rumps.MenuItem("Sortiert oeffnen", callback=self.sortiert_oeffnen),
            rumps.separator,
            rumps.MenuItem("Beenden", callback=self.beenden),
        ]

    def ordner_waehlen(self, _):
        result = subprocess.run(["osascript", "-e", 'tell application "Finder"\nset f to choose folder\nreturn POSIX path of f\nend tell'], capture_output=True, text=True)
        pfad = result.stdout.strip().rstrip("/")
        if pfad:
            self.scan_ordner = pfad
            rumps.notification("DocNamer", "Ordner geaendert", pfad)

    def einmal_scan(self, _):
        rumps.notification("DocNamer", "Einmal-Scan gestartet", "")
        def scan():
            result = subprocess.run([PYTHON, WATCHER_SCRIPT, self.scan_ordner, "--einmal"], capture_output=True, text=True)
            verarbeitet = result.stdout.count("Kategorie")
            rumps.notification("DocNamer", "Scan abgeschlossen", f"{verarbeitet} Dokument(e)")
        threading.Thread(target=scan, daemon=True).start()

    def watcher_starten(self, _):
        if self.watcher_prozess and self.watcher_prozess.poll() is None:
            rumps.notification("DocNamer", "Watcher laeuft bereits", "")
            return
        self.watcher_prozess = subprocess.Popen([PYTHON, WATCHER_SCRIPT, self.scan_ordner], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.title = "👁"
        rumps.notification("DocNamer", "Watcher gestartet", "")

    def watcher_stoppen(self, _):
        if self.watcher_prozess:
            self.watcher_prozess.terminate()
            self.watcher_prozess = None
        self.title = "🗂"
        rumps.notification("DocNamer", "Watcher gestoppt", "")

    def korrektur_erfassen(self, _):
        subprocess.run(["osascript", "-e", f'tell application "Terminal"\nactivate\ndo script "{PYTHON} {KORREKTUR_SCRIPT}"\nend tell'])

    def korrekturen_anzeigen(self, _):
        if not os.path.exists(KORREKTUREN_JSON):
            rumps.alert("Korrekturen", "Noch keine vorhanden.")
            return
        with open(KORREKTUREN_JSON) as f:
            k = json.load(f)
        text = "\n".join(f"{i+1}. {x['original_filename']} → {x['korrekte_kategorie']}" for i, x in enumerate(k[-10:]))
        rumps.alert(f"Korrekturen ({len(k)})", text)

    def kategorien_editieren(self, _):
        subprocess.run(["open", "-a", "TextEdit", KATEGORIEN_JSON])

    def log_anzeigen(self, _):
        if os.path.exists(LOG_DATEI):
            subprocess.run(["open", "-a", "Console", LOG_DATEI])
        else:
            rumps.alert("Log", "Noch kein Log vorhanden.")

    def sortiert_oeffnen(self, _):
        sortiert = os.path.join(AUSGABE_BASIS, "_Sortiert")
        os.makedirs(sortiert, exist_ok=True)
        subprocess.run(["open", sortiert])

    def beenden(self, _):
        self.watcher_stoppen(None)
        rumps.quit_application()

if __name__ == "__main__":
    DocNamerApp().run()
