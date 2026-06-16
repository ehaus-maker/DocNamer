#!/usr/bin/env python3
"""DocNamer Fernsteuerung – Menüleisten-App fürs MacBook.

Gleiche Bedienung wie docnamer_menubar.py, aber die Verarbeitung läuft auf dem
Mac Mini. Prozess-Steuerung (Einmal-Scan, Watcher starten/stoppen) geht per SSH
an den Mini; Datei-Aktionen (Kategorien, Log, Sortiert) arbeiten lokal auf den
über iCloud synchronisierten Dateien.

Voraussetzung: passwortloser SSH-Zugang zum Mini (Schlüssel ist eingerichtet)
und das Steuerskript ~/docnamer_ctl.sh auf dem Mini.
"""
import os, subprocess, threading, rumps

# --- Mini-Zugang ---------------------------------------------------------
MINI_HOST = os.environ.get("DOCNAMER_MINI_HOST", "ehaus@MacMini-M4.local")
CTL       = "~/docnamer_ctl.sh"

SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
# Feste iCloud-Pfade: das Skript darf lokal liegen (TCC-Beschränkung für Apps
# aus /Applications), greift aber per "open" auf die synchronisierten Dateien zu.
REPO            = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Documents/GitHub/DocNamer")
KATEGORIEN_JSON = os.path.join(REPO, "kategorien.json")

_ICLOUD_DOCS  = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/Documents")
AUSGABE_BASIS = os.path.join(_ICLOUD_DOCS, "DocNamer") if os.path.isdir(_ICLOUD_DOCS) else os.path.expanduser("~/Documents/DocNamer")
LOG_DATEI     = os.path.join(AUSGABE_BASIS, "docnamer.log")
HASH_JSON     = os.path.join(AUSGABE_BASIS, "hashes.json")
SORTIERT      = os.path.join(AUSGABE_BASIS, "_Sortiert")

try:
    with open(os.path.join(REPO, "VERSION")) as _vf:
        VERSION = _vf.read().strip()
except Exception:
    VERSION = "?"

# Icon nur setzen wenn vorhanden (läuft sonst auch ohne, nur mit Titel).
_ICON = os.path.join(SCRIPT_DIR, "icon_template.png")
if not os.path.exists(_ICON):
    _ICON = None


def ssh(*args):
    """Führt das Steuerskript auf dem Mini aus und liefert (ok, ausgabe)."""
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=8", MINI_HOST, f"{CTL} {' '.join(args)}"],
        capture_output=True, text=True
    )
    if r.returncode != 0:
        return False, (r.stderr.strip() or "Mini nicht erreichbar")
    return True, r.stdout.strip()


class DocNamerRemote(rumps.App):

    def __init__(self):
        super().__init__("DN▸", icon=_ICON, template=True, quit_button=None)
        self.status_zeile = rumps.MenuItem("Status: unbekannt")
        self.menu = [
            rumps.MenuItem(f"DocNamer v{VERSION} (Mini-Fernsteuerung)"),
            self.status_zeile,
            rumps.separator,
            rumps.MenuItem("Einmal-Scan", callback=self.einmal_scan),
            rumps.MenuItem("Watcher starten", callback=self.watcher_starten),
            rumps.MenuItem("Watcher stoppen", callback=self.watcher_stoppen),
            rumps.MenuItem("Status pruefen", callback=self.status),
            rumps.separator,
            rumps.MenuItem("Kategorien editieren", callback=self.kategorien_editieren),
            rumps.MenuItem("Hash editieren", callback=self.hash_editieren),
            rumps.separator,
            rumps.MenuItem("Log anzeigen", callback=self.log_anzeigen),
            rumps.MenuItem("Sortiert oeffnen", callback=self.sortiert_oeffnen),
            rumps.separator,
            rumps.MenuItem("Beenden", callback=self.beenden),
        ]

    # --- Prozess-Steuerung über den Mini ---------------------------------
    def einmal_scan(self, _):
        self.status_zeile.title = "Status: Einmal-Scan läuft…"
        def lauf():
            ok, out = ssh("einmal")
            if ok:
                verarbeitet = out.count("Kategorie")
                self.status_zeile.title = f"Status: Scan fertig – {verarbeitet} Dok."
            else:
                self.status_zeile.title = f"Status: Fehler – {out}"
        threading.Thread(target=lauf, daemon=True).start()
        rumps.alert("DocNamer", "Einmal-Scan auf dem Mini gestartet.\n"
                                 "Ergebnis erscheint danach in der Statuszeile im Menü.")

    def watcher_starten(self, _):
        ok, out = ssh("start")
        self.title = "DN👁" if ok else "DN▸"
        self.status_zeile.title = f"Status: {out}" if ok else "Status: Fehler"
        rumps.alert("Watcher starten", out if ok else f"Fehler: {out}")

    def watcher_stoppen(self, _):
        ok, out = ssh("stop")
        self.title = "DN▸"
        self.status_zeile.title = f"Status: {out}" if ok else "Status: Fehler"
        rumps.alert("Watcher stoppen", out if ok else f"Fehler: {out}")

    def status(self, _):
        ok, out = ssh("status")
        self.status_zeile.title = f"Status: {out}" if ok else "Status: Fehler"
        rumps.alert("Status", out if ok else f"Fehler: {out}")

    # --- Datei-Aktionen, lokal auf den iCloud-synchronisierten Dateien ----
    def kategorien_editieren(self, _):
        subprocess.run(["open", "-a", "TextEdit", KATEGORIEN_JSON])
        rumps.notification("DocNamer", "Hinweis",
                           "Neue Kategorien greifen erst nach Watcher-Neustart auf dem Mini.")

    def hash_editieren(self, _):
        if not os.path.exists(HASH_JSON):
            rumps.alert("Hash-Datei", "Noch keine hashes.json synchronisiert.")
            return
        subprocess.run(["open", "-a", "TextEdit", HASH_JSON])

    def log_anzeigen(self, _):
        if os.path.exists(LOG_DATEI):
            subprocess.run(["open", "-a", "Console", LOG_DATEI])
        else:
            rumps.alert("Log", "Noch kein Log synchronisiert.")

    def sortiert_oeffnen(self, _):
        os.makedirs(SORTIERT, exist_ok=True)
        subprocess.run(["open", SORTIERT])

    def beenden(self, _):
        rumps.quit_application()


if __name__ == "__main__":
    DocNamerRemote().run()
