#!/usr/bin/env python3
import os, sys, json, threading, subprocess, rumps

SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
WATCHER_SCRIPT  = os.path.join(SCRIPT_DIR, "docnamer_watcher.py")
AUSGABE_BASIS   = os.path.expanduser("~/Documents/DocNamer")
LOG_DATEI       = os.path.join(AUSGABE_BASIS, "docnamer.log")
KATEGORIEN_JSON = os.path.join(SCRIPT_DIR, "kategorien.json")
HASH_JSON       = os.path.join(AUSGABE_BASIS, "hashes.json")

try:
    with open(os.path.join(SCRIPT_DIR, "VERSION")) as _vf:
        VERSION = _vf.read().strip()
except Exception:
    VERSION = "?"

STANDARD_ORDNER = os.path.expanduser("~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF/Documents")
if not os.path.exists(STANDARD_ORDNER):
    STANDARD_ORDNER = os.path.expanduser("~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF")

PYTHON = "/opt/homebrew/bin/python3"

class DocNamerApp(rumps.App):

    def __init__(self):
        super().__init__("DN", icon=os.path.join(SCRIPT_DIR, "icon_template.png"), template=True, quit_button=None)
        self.watcher_prozess = None
        self.scan_ordner = STANDARD_ORDNER
        self.menu = [
            rumps.MenuItem(f"DocNamer v{VERSION}"),
            rumps.separator,
            rumps.MenuItem("📂 Ordner waehlen", callback=self.ordner_waehlen),
            rumps.separator,
            rumps.MenuItem("Einmal-Scan", callback=self.einmal_scan),
            rumps.MenuItem("Watcher starten", callback=self.watcher_starten),
            rumps.MenuItem("Watcher stoppen", callback=self.watcher_stoppen),
            rumps.separator,
            rumps.MenuItem("Kategorien editieren", callback=self.kategorien_editieren),
            rumps.MenuItem("Hash editieren", callback=self.hash_editieren),
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

    def kategorien_editieren(self, _):
        subprocess.run(["open", "-a", "TextEdit", KATEGORIEN_JSON])

    def hash_editieren(self, _):
        if not os.path.exists(HASH_JSON):
            rumps.alert("Hash-Datei", "Noch keine hashes.json vorhanden.")
            return
        subprocess.run(["open", "-a", "TextEdit", HASH_JSON])

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
