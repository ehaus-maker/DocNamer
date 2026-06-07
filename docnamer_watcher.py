import os
import sys
import json
import csv
import shutil
import base64
import time
import logging
import subprocess
import hashlib
import fitz
import anthropic

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# --einmal Flag auswerten
EINMAL_MODUS = "--einmal" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]

if args:
    ORDNER = os.path.abspath(args[0])
else:
    # Watchdog meldet Pfade über den Documents-Symlink – wir verwenden denselben
    ORDNER = os.path.expanduser(
        "~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF/Documents"
    )
    # Fallback falls der Symlink nicht existiert
    if not os.path.exists(ORDNER):
        ORDNER = os.path.expanduser(
            "~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF"
        )

AUSGABE_BASIS   = os.path.expanduser("~/Documents/DocNamer")
ZIELORDNER      = os.path.join(AUSGABE_BASIS, "_Sortiert")
FEHLERORDNER    = os.path.join(AUSGABE_BASIS, "_Fehler")
DUPLIKAT_ORDNER = os.path.join(AUSGABE_BASIS, "_Duplikate")
ERLEDIGT_BASIS  = os.path.join(AUSGABE_BASIS, "_Erledigt")
LOG_DATEI       = os.path.join(AUSGABE_BASIS, "docnamer.log")
CSV_DATEI       = os.path.join(AUSGABE_BASIS, "umbenennung.csv")
HASH_DATEI      = os.path.join(AUSGABE_BASIS, "hashes.json")

KATEGORIEN_JSON      = os.path.join(os.path.dirname(__file__), "kategorien.json")
STABILISIERUNGS_SECS = 3
ICLOUD_TIMEOUT       = 60

# ---------------------------------------------------------------------------
# Ausgabe-Ordner anlegen (vor dem Logging!)
# ---------------------------------------------------------------------------

for d in (AUSGABE_BASIS, ZIELORDNER, FEHLERORDNER, DUPLIKAT_ORDNER, ERLEDIGT_BASIS):
    os.makedirs(d, exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DATEI, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("docnamer")

# ---------------------------------------------------------------------------
# Kategorien laden
# ---------------------------------------------------------------------------

with open(KATEGORIEN_JSON, "r", encoding="utf-8") as f:
    KATEGORIEN_INFO = json.load(f)


def kategorien_flatten(d, prefix=""):
    result = []
    for key, value in d.items():
        if isinstance(value, dict) and "beschreibung" not in value:
            result.extend(kategorien_flatten(value, f"{prefix}{key}/"))
        else:
            result.append(f"{prefix}{key}")
    return result


KATEGORIEN = kategorien_flatten(KATEGORIEN_INFO)

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

client = anthropic.Anthropic()


def parse_antwort(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text.strip())


def validiere_ergebnis(ergebnis):
    if "category" not in ergebnis or "filename" not in ergebnis:
        raise ValueError(f"Fehlende Felder: {ergebnis}")
    if ergebnis["category"] not in KATEGORIEN:
        raise ValueError(f"Ungültige Kategorie: {ergebnis['category']}")
    return ergebnis


def pdf_text_lesen(pfad):
    doc = fitz.open(pfad)
    text = ""
    for i in range(min(5, len(doc))):
        text += doc[i].get_text()
    return text[:20000]


def pdf_seiten_als_bilder(pfad, max_seiten=3):
    doc = fitz.open(pfad)
    bilder = []
    for i in range(min(max_seiten, len(doc))):
        page = doc[i]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        bildpfad = pfad.replace(".pdf", f"_seite_{i+1}.png")
        pix.save(bildpfad)
        bilder.append(bildpfad)
    return bilder


def dokument_analysieren_text(dateiname, text):
    prompt = f"""Analysiere dieses deutsche Dokument und antworte NUR mit einem JSON-Objekt.

Erzeuge:
- category: eine der erlaubten Kategorien (exakt so wie angegeben)
- filename: Dateiname im Format YYYY-MM-DD_Hauptobjekt_Dokumenttyp_Zusatz (keine Umlaute, keine Leerzeichen)

Erlaubte Kategorien:
{json.dumps(KATEGORIEN, ensure_ascii=False, indent=2)}

Kategorien mit Beschreibung:
{json.dumps(KATEGORIEN_INFO, ensure_ascii=False, indent=2)}

Originaldateiname: {dateiname}

Dokument:
{text}

Antworte NUR mit JSON, kein erklärender Text:
{{"category": "...", "filename": "..."}}"""

    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )
    return validiere_ergebnis(parse_antwort(r.content[0].text))


def dokument_analysieren_bild(dateiname, bildpfade):
    prompt = f"""Analysiere alle Seiten dieses Dokuments und antworte NUR mit einem JSON-Objekt.

Bestimme:
- category: eine der erlaubten Kategorien (exakt so wie angegeben)
- filename: Dateiname im Format YYYY-MM-DD_Hauptobjekt_Dokumenttyp_Zusatz
  (keine Umlaute, keine Leerzeichen, Objekt des Dokuments, nicht Empfänger)

Erlaubte Kategorien:
{json.dumps(KATEGORIEN, ensure_ascii=False, indent=2)}

Kategorien mit Beschreibung:
{json.dumps(KATEGORIEN_INFO, ensure_ascii=False, indent=2)}

Originaldateiname: {dateiname}

Antworte NUR mit JSON, kein erklärender Text:
{{"category": "...", "filename": "..."}}"""

    content = [{"type": "text", "text": prompt}]
    for bildpfad in bildpfade:
        with open(bildpfad, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64}
        })

    r = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": content}]
    )
    return validiere_ergebnis(parse_antwort(r.content[0].text))


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def eindeutiger_pfad(ordner, dateiname):
    basis, endung = os.path.splitext(dateiname)
    ziel = os.path.join(ordner, dateiname)
    nr = 1
    while os.path.exists(ziel):
        ziel = os.path.join(ordner, f"{basis}_{nr}{endung}")
        nr += 1
    return ziel


def csv_zeile_schreiben(alt, neu, kategorie):
    neu_datei = not os.path.exists(CSV_DATEI)
    with open(CSV_DATEI, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if neu_datei:
            writer.writerow(["Zeitstempel", "Alter Pfad", "Neuer Pfad", "Kategorie"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alt, neu, kategorie])


def hash_laden():
    if not os.path.exists(HASH_DATEI):
        return {}
    with open(HASH_DATEI, "r", encoding="utf-8") as f:
        return json.load(f)


def hash_speichern(hashes):
    with open(HASH_DATEI, "w", encoding="utf-8") as f:
        json.dump(hashes, f, ensure_ascii=False, indent=2)


def pdf_hash(pfad):
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def icloud_download_erzwingen(pfad):
    verzeichnis = os.path.dirname(pfad)
    dateiname   = os.path.basename(pfad)
    platzhalter = os.path.join(verzeichnis, "." + dateiname + ".icloud")
    if not os.path.exists(platzhalter):
        return
    log.info(f"  → iCloud-Platzhalter erkannt, erzwinge Download...")
    try:
        subprocess.run(["brctl", "download", pfad], check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"brctl download fehlgeschlagen: {e}")
    for _ in range(ICLOUD_TIMEOUT):
        if os.path.exists(pfad) and not os.path.exists(platzhalter):
            log.info(f"  → iCloud-Download abgeschlossen.")
            return
        time.sleep(1)
    raise TimeoutError(f"iCloud-Download Timeout: {dateiname}")


def quellordner_name(pfad):
    """Gibt den Namen des direkten Elternordners zurück, falls er nicht der Scan-Ordner selbst ist."""
    ordner_real = os.path.realpath(ORDNER)
    eltern_real = os.path.realpath(os.path.dirname(pfad))
    if eltern_real != ordner_real:
        return os.path.basename(eltern_real)
    return ""


def leere_ordner_archivieren():
    """Verschiebt leere Quellordner im Scanner-Ordner nach _Erledigt."""
    erledigt = 0
    for name in os.listdir(ORDNER):
        pfad = os.path.join(ORDNER, name)
        # Nur echte, nicht-versteckte, nicht-System-Ordner
        if not os.path.isdir(pfad):
            continue
        if os.path.islink(pfad):
            continue
        if name.startswith(".") or name.startswith("_"):
            continue
        if name == "Documents":
            continue
        # Prüfen ob noch PDFs vorhanden
        pdfs_vorhanden = any(
            f.lower().endswith(".pdf")
            for _, _, files in os.walk(pfad)
            for f in files
        )
        if not pdfs_vorhanden:
            ziel = eindeutiger_pfad(ERLEDIGT_BASIS, name)
            shutil.move(pfad, ziel)
            log.info(f"  → Leerer Ordner archiviert: {name} → _Erledigt/")
            erledigt += 1
    if erledigt > 0:
        log.info(f"Aufräumen: {erledigt} Ordner nach _Erledigt verschoben.")


# ---------------------------------------------------------------------------
# PDF verarbeiten
# ---------------------------------------------------------------------------

def verarbeite_pdf(pfad):
    """Analysiert eine PDF und verschiebt sie in den Ziel- oder Fehlerordner."""

    datei = os.path.basename(pfad)

    if not os.path.exists(pfad):
        log.warning(f"Datei nicht gefunden, übersprungen: {datei}")
        return

    log.info(f"Neue Datei erkannt: {datei}")

    # iCloud-Download sicherstellen
    try:
        icloud_download_erzwingen(pfad)
    except Exception as e:
        log.error(f"  ✗ iCloud-Fehler bei {datei}: {e}")

    # Warten bis Datei stabil ist
    groesse_alt = -1
    while True:
        try:
            groesse_neu = os.path.getsize(pfad)
        except FileNotFoundError:
            log.warning(f"Datei verschwunden: {datei}")
            return
        if groesse_neu == groesse_alt:
            break
        groesse_alt = groesse_neu
        time.sleep(STABILISIERUNGS_SECS)

    # Duplikatprüfung
    datei_hash = None
    hashes = {}
    try:
        datei_hash = pdf_hash(pfad)
        hashes = hash_laden()
        if datei_hash in hashes:
            eintrag = hashes[datei_hash]
            datum   = eintrag["datum"] if isinstance(eintrag, dict) else eintrag
            log.info(f"  → Duplikat (verarbeitet am {datum}), verschiebe nach _Duplikate.")
            dup_pfad = eindeutiger_pfad(DUPLIKAT_ORDNER, datei)
            shutil.move(pfad, dup_pfad)
            leere_ordner_archivieren()
            return
    except Exception as e:
        log.warning(f"  → Hash-Prüfung fehlgeschlagen: {e}")

    # Analyse & Sortierung
    qname    = quellordner_name(pfad)
    bildpfade = []
    try:
        text = pdf_text_lesen(pfad)

        if len(text.strip()) >= 50:
            log.info(f"  → Textanalyse (Haiku)")
            ergebnis = dokument_analysieren_text(datei, text)
        else:
            log.info(f"  → Scan erkannt, Vision-Analyse (Sonnet)")
            bildpfade = pdf_seiten_als_bilder(pfad, max_seiten=3)
            ergebnis = dokument_analysieren_bild(datei, bildpfade)

        kategorie = ergebnis["category"]
        filename  = ergebnis["filename"] + ".pdf"

        if qname:
            zielordner = os.path.join(ZIELORDNER, qname, kategorie)
        else:
            zielordner = os.path.join(ZIELORDNER, kategorie)
        os.makedirs(zielordner, exist_ok=True)
        zielpfad = eindeutiger_pfad(zielordner, filename)

        shutil.move(pfad, zielpfad)
        csv_zeile_schreiben(pfad, zielpfad, kategorie)

        log.info(f"  ✓ Kategorie : {kategorie}")
        log.info(f"  ✓ Neu       : {zielpfad}")

        if datei_hash:
            hashes[datei_hash] = {
                "datum":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "dateiname": os.path.basename(zielpfad),
            }
            hash_speichern(hashes)

        leere_ordner_archivieren()

    except Exception as e:
        log.error(f"  ✗ Fehler bei {datei}: {e}")
        fehlerpfad = eindeutiger_pfad(FEHLERORDNER, datei)
        try:
            shutil.move(pfad, fehlerpfad)
            log.error(f"  ✗ Verschoben nach _Fehler: {fehlerpfad}")
        except Exception as e2:
            log.error(f"  ✗ Konnte Datei nicht verschieben: {e2}")

    finally:
        for bild in bildpfade:
            if os.path.exists(bild):
                os.remove(bild)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class PDFHandler(FileSystemEventHandler):

    def _verarbeiten(self, pfad):
        # iCloud-Platzhalter auflösen
        if os.path.basename(pfad).startswith(".") and pfad.endswith(".icloud"):
            echter_name = os.path.basename(pfad)[1:].removesuffix(".icloud")
            pfad = os.path.join(os.path.dirname(pfad), echter_name)
        if not pfad.lower().endswith(".pdf"):
            return
        # Ausgabe-Ordner ignorieren
        pfad_real = os.path.realpath(pfad)
        for ignore in (ZIELORDNER, FEHLERORDNER, DUPLIKAT_ORDNER, ERLEDIGT_BASIS):
            if pfad_real.startswith(os.path.realpath(ignore)):
                return
        verarbeite_pdf(pfad)

    def on_created(self, event):
        if not event.is_directory:
            self._verarbeiten(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._verarbeiten(event.dest_path)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def fehler_zurueckholen():
    if not os.path.exists(FEHLERORDNER):
        return
    zurueck = 0
    for datei in os.listdir(FEHLERORDNER):
        if datei.lower().endswith(".pdf"):
            quelle = os.path.join(FEHLERORDNER, datei)
            ziel   = eindeutiger_pfad(ORDNER, datei)
            shutil.move(quelle, ziel)
            log.info(f"  → Retry: {datei} zurück in Scan-Ordner")
            zurueck += 1
    if zurueck > 0:
        log.info(f"Retry: {zurueck} PDF(s) zurück verschoben.")


def startup_scan():
    fehler_zurueckholen()
    log.info("Startup-Scan: suche vorhandene PDFs...")
    gefunden = 0
    ordner_real = os.path.realpath(ORDNER)
    for root, dirs, files in os.walk(ORDNER):
        # Ausgabe-Ordner überspringen
        dirs[:] = [
            d for d in dirs
            if not os.path.realpath(os.path.join(root, d)).startswith(
                os.path.realpath(AUSGABE_BASIS)
            )
            and not os.path.islink(os.path.join(root, d))
        ]
        for datei in files:
            if datei.lower().endswith(".pdf"):
                gefunden += 1
                verarbeite_pdf(os.path.join(root, datei))
    log.info(f"Startup-Scan: {gefunden} PDF(s) gefunden.")
    leere_ordner_archivieren()


if __name__ == "__main__":
    log.info("=" * 60)
    modus = "Einmal-Scan" if EINMAL_MODUS else "Watcher"
    log.info(f"docnamer gestartet  [{modus}]")
    log.info(f"Überwachter Ordner : {ORDNER}")
    log.info(f"Zielordner         : {ZIELORDNER}")
    log.info(f"Fehlerordner       : {FEHLERORDNER}")
    log.info(f"Log                : {LOG_DATEI}")
    log.info("=" * 60)

    if EINMAL_MODUS:
        # Einmal-Scan: durchlaufen und beenden
        startup_scan()
        log.info("Einmal-Scan abgeschlossen.")
    else:
        # Watcher: dauerhaft laufen
        startup_scan()

        handler  = PDFHandler()
        observer = Observer()
        observer.schedule(handler, ORDNER, recursive=True)
        observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Watcher wird beendet...")
            observer.stop()

        observer.join()
        log.info("Watcher beendet.")
