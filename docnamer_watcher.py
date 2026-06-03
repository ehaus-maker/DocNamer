import os
import sys
import json
import csv
import shutil
import base64
import time
import logging
import subprocess
import fitz
import anthropic

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

if len(sys.argv) > 1:
    ORDNER = os.path.abspath(sys.argv[1])
else:
    ORDNER = os.path.expanduser(
        "~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF"
    )

AUSGABE_BASIS = os.path.expanduser("~/Documents/DocNamer")
ZIELORDNER   = os.path.join(AUSGABE_BASIS, "_Sortiert")
FEHLERORDNER = os.path.join(AUSGABE_BASIS, "_Fehler")
LOG_DATEI    = os.path.join(AUSGABE_BASIS, "docnamer.log")
CSV_DATEI    = os.path.join(AUSGABE_BASIS, "umbenennung.csv")

KATEGORIEN_JSON = os.path.join(os.path.dirname(__file__), "kategorien.json")

# Wartezeit: PDF muss X Sekunden stabil sein bevor Analyse startet
# (verhindert Verarbeitung halb-geschriebener Dateien)
STABILISIERUNGS_SECS = 3

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


def eindeutiger_pfad(ordner, dateiname):
    basis, endung = os.path.splitext(dateiname)
    ziel = os.path.join(ordner, dateiname)
    nr = 1
    while os.path.exists(ziel):
        ziel = os.path.join(ordner, f"{basis}_{nr}{endung}")
        nr += 1
    return ziel


def csv_zeile_schreiben(alt, neu, kategorie):
    os.makedirs(ZIELORDNER, exist_ok=True)
    neu_datei = not os.path.exists(CSV_DATEI)
    with open(CSV_DATEI, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if neu_datei:
            writer.writerow(["Zeitstempel", "Alter Pfad", "Neuer Pfad", "Kategorie"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), alt, neu, kategorie])

# ---------------------------------------------------------------------------
# Dokument verarbeiten
# ---------------------------------------------------------------------------

ICLOUD_DOWNLOAD_TIMEOUT = 60  # Sekunden


def icloud_download_erzwingen(pfad):
    """Falls die Datei nur ein iCloud-Platzhalter ist, Download erzwingen und warten."""
    verzeichnis = os.path.dirname(pfad)
    dateiname   = os.path.basename(pfad)
    platzhalter = os.path.join(verzeichnis, "." + dateiname + ".icloud")

    if not os.path.exists(platzhalter):
        return  # Datei ist bereits lokal verfügbar

    log.info(f"  → iCloud-Platzhalter erkannt, erzwinge Download...")
    try:
        subprocess.run(["brctl", "download", pfad], check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"brctl download fehlgeschlagen: {e}")

    for _ in range(ICLOUD_DOWNLOAD_TIMEOUT):
        if os.path.exists(pfad) and not os.path.exists(platzhalter):
            log.info(f"  → iCloud-Download abgeschlossen.")
            return
        time.sleep(1)

    raise TimeoutError(f"iCloud-Download Timeout nach {ICLOUD_DOWNLOAD_TIMEOUT}s: {dateiname}")


def verarbeite_pdf(pfad):
    """Analysiert eine PDF und verschiebt sie in den Ziel- oder Fehlerordner."""

    datei = os.path.basename(pfad)
    log.info(f"Neue Datei erkannt: {datei}")

    # iCloud: Download sicherstellen bevor wir die Größe prüfen
    try:
        icloud_download_erzwingen(pfad)
    except Exception as e:
        log.error(f"  ✗ iCloud-Fehler bei {datei}: {e}")
        # Weiter versuchen – vielleicht ist die Datei trotzdem da

    # Warten bis Datei stabil ist (vollständig geschrieben)
    groesse_alt = -1
    while True:
        try:
            groesse_neu = os.path.getsize(pfad)
        except FileNotFoundError:
            log.warning(f"Datei verschwunden vor Verarbeitung: {datei}")
            return
        if groesse_neu == groesse_alt:
            break
        groesse_alt = groesse_neu
        time.sleep(STABILISIERUNGS_SECS)

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

        zielordner = os.path.join(ZIELORDNER, kategorie)
        os.makedirs(zielordner, exist_ok=True)
        zielpfad = eindeutiger_pfad(zielordner, filename)

        shutil.move(pfad, zielpfad)
        csv_zeile_schreiben(pfad, zielpfad, kategorie)

        log.info(f"  ✓ Kategorie : {kategorie}")
        log.info(f"  ✓ Neu       : {zielpfad}")

    except Exception as e:
        log.error(f"  ✗ Fehler bei {datei}: {e}")
        os.makedirs(FEHLERORDNER, exist_ok=True)
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
# Watchdog Handler
# ---------------------------------------------------------------------------

class PDFHandler(FileSystemEventHandler):

    def on_created(self, event):
        if event.is_directory:
            return
        pfad = event.src_path

        # iCloud-Platzhalter: .dateiname.pdf.icloud → echten Pfad ableiten
        if os.path.basename(pfad).startswith(".") and pfad.endswith(".icloud"):
            echter_name = os.path.basename(pfad)[1:].removesuffix(".icloud")
            pfad = os.path.join(os.path.dirname(pfad), echter_name)

        if not pfad.lower().endswith(".pdf"):
            return
        if ZIELORDNER in pfad or FEHLERORDNER in pfad:
            return
        verarbeite_pdf(pfad)

    def on_moved(self, event):
        """Reagiert auch auf Dateien die per drag & drop / mv hineinkommen."""
        if event.is_directory:
            return
        pfad = event.dest_path
        if not pfad.lower().endswith(".pdf"):
            return
        if ZIELORDNER in pfad or FEHLERORDNER in pfad:
            return
        verarbeite_pdf(pfad)

# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------

def fehler_zurueckholen():
    """Verschiebt PDFs aus _Fehler zurück in den Scan-Ordner zum erneuten Versuch."""
    if not os.path.exists(FEHLERORDNER):
        return
    zurueck = 0
    for datei in os.listdir(FEHLERORDNER):
        if datei.lower().endswith(".pdf"):
            quelle  = os.path.join(FEHLERORDNER, datei)
            ziel    = eindeutiger_pfad(ORDNER, datei)
            shutil.move(quelle, ziel)
            log.info(f"  → Zurück in Scan-Ordner: {datei}")
            zurueck += 1
    if zurueck > 0:
        log.info(f"Retry: {zurueck} PDF(s) zurück in Scan-Ordner verschoben.")


def startup_scan():
    """Verarbeitet alle PDFs die beim Start bereits im Ordner liegen."""
    fehler_zurueckholen()
    log.info("Startup-Scan: suche vorhandene PDFs...")
    gefunden = 0
    for root, dirs, files in os.walk(ORDNER):
        # _Sortiert und _Fehler überspringen
        dirs[:] = [d for d in dirs if os.path.join(root, d) not in (ZIELORDNER, FEHLERORDNER)]
        for datei in files:
            if datei.lower().endswith(".pdf"):
                gefunden += 1
                verarbeite_pdf(os.path.join(root, datei))
    if gefunden == 0:
        log.info("Startup-Scan: keine PDFs gefunden.")
    else:
        log.info(f"Startup-Scan: {gefunden} PDF(s) verarbeitet.")


if __name__ == "__main__":
    os.makedirs(AUSGABE_BASIS, exist_ok=True)
    os.makedirs(ZIELORDNER, exist_ok=True)
    os.makedirs(FEHLERORDNER, exist_ok=True)

    log.info("=" * 60)
    log.info(f"docnamer Watcher gestartet")
    log.info(f"Überwachter Ordner : {ORDNER}")
    log.info(f"Zielordner         : {ZIELORDNER}")
    log.info(f"Fehlerordner       : {FEHLERORDNER}")
    log.info(f"Log                : {LOG_DATEI}")
    log.info("=" * 60)

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
