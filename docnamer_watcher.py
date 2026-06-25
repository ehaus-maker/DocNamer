import os
import re
import sys
import json
import csv
import shutil
import base64
import time
import logging
import subprocess
import hashlib
import difflib
import urllib.request
import fitz
import anthropic

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from datetime import datetime

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

# Modus-Flags auswerten
EINMAL_MODUS = "--einmal" in sys.argv
VISION_MODUS = "--vision" in sys.argv   # nur _BrauchtVision über Cloud-Vision verarbeiten
args = [a for a in sys.argv[1:] if not a.startswith("--")]

if args:
    ORDNER = os.path.abspath(args[0])
elif os.environ.get("DOCNAMER_INBOX"):
    # Überwachter Ordner per Umgebungsvariable (z. B. WebDAV-Inbox)
    ORDNER = os.path.expanduser(os.environ["DOCNAMER_INBOX"])
else:
    # Watchdog meldet Pfade über den Documents-Symlink – wir verwenden denselben
    ORDNER = os.path.expanduser(
        "~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF/Documents"
    )
    if not os.path.exists(ORDNER):
        ORDNER = os.path.expanduser(
            "~/Library/Mobile Documents/iCloud~com~readdle~Scanner~PDF"
        )

# Ausgabe-Basis per Umgebungsvariable überschreibbar (Default: portabel)
AUSGABE_BASIS     = os.path.expanduser(os.environ.get("DOCNAMER_OUT", "~/Documents/DocNamer"))
ZIELORDNER        = os.path.join(AUSGABE_BASIS, "_Sortiert")
FEHLERORDNER      = os.path.join(AUSGABE_BASIS, "_Fehler")
DUPLIKAT_ORDNER   = os.path.join(AUSGABE_BASIS, "_Duplikate")
ERLEDIGT_BASIS    = os.path.join(AUSGABE_BASIS, "_Erledigt")
UNSORTIERT_ORDNER = os.path.join(AUSGABE_BASIS, "_Unsortiert")
BRAUCHT_VISION_ORDNER = os.path.join(AUSGABE_BASIS, "_BrauchtVision")
LOG_DATEI         = os.path.join(AUSGABE_BASIS, "docnamer.log")
CSV_DATEI         = os.path.join(AUSGABE_BASIS, "umbenennung.csv")
HASH_DATEI        = os.path.join(AUSGABE_BASIS, "hashes.json")

KATEGORIEN_JSON      = os.path.join(os.path.dirname(__file__), "kategorien.json")
# Poll-Intervall, bis die Dateigröße stabil ist. Über die WebDAV-Inbox kommen
# Dateien komplett an → kurz reicht; per Env feinjustierbar (iCloud ggf. höher).
STABILISIERUNGS_SECS = float(os.environ.get("DOCNAMER_STABILISIERUNG", "1.0"))
ICLOUD_TIMEOUT       = 60

# --- Ollama (lokale Modelle) ---
# Die Kategorie-Triage läuft IMMER lokal über Ollama. Nur Kategorien, die in
# kategorien.json ausdrücklich "verarbeitung": "cloud" tragen, werden danach
# zusätzlich über Anthropic analysiert. Default ist "lokal".
OLLAMA_HOST          = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODELL_TEXT   = os.environ.get("DOCNAMER_OLLAMA_TEXT",   "qwen2.5:7b")
OLLAMA_MODELL_VISION = os.environ.get("DOCNAMER_OLLAMA_VISION", "qwen2.5vl:7b")
OLLAMA_TIMEOUT       = int(os.environ.get("DOCNAMER_OLLAMA_TIMEOUT", "300"))
# Wie lange Ollama das Modell nach einem Aufruf geladen hält. "60s" lädt bei
# sporadischen Scans jedes Mal neu (Kaltstart-Strafe); länger = Modell bleibt resident.
OLLAMA_KEEPALIVE     = os.environ.get("DOCNAMER_OLLAMA_KEEPALIVE", "30m")

# --- Betriebsschalter ---
# Normalbetrieb ist rein lokal (OCR + Ollama-Text). Cloud-Vision wird NICHT
# automatisch genutzt – Scans ohne Text wandern nach _BrauchtVision und werden
# nur auf ausdrückliche Aktion (--vision) über die Cloud verarbeitet.
DEDUP_AKTIV  = os.environ.get("DOCNAMER_DEDUP", "1") != "0"   # DOCNAMER_DEDUP=0 → keine Duplikaterkennung
OCR_AKTIV    = os.environ.get("DOCNAMER_OCR",   "1") != "0"   # DOCNAMER_OCR=0   → keine OCR-Vorstufe
OCRMYPDF     = os.environ.get("DOCNAMER_OCRMYPDF", "/opt/homebrew/bin/ocrmypdf")

# Version aus VERSION-Datei (Single Source of Truth)
try:
    with open(os.path.join(os.path.dirname(__file__), "VERSION")) as _vf:
        VERSION = _vf.read().strip()
except Exception:
    VERSION = "?"

# API-Key: Umgebungsvariable hat Vorrang, Fallback auf ~/.docnamer_config
_CONFIG_DATEI = os.path.expanduser("~/.docnamer_config")
if not os.environ.get("ANTHROPIC_API_KEY") and os.path.exists(_CONFIG_DATEI):
    with open(_CONFIG_DATEI) as _f:
        for _zeile in _f:
            _zeile = _zeile.strip()
            if _zeile.startswith("ANTHROPIC_API_KEY="):
                os.environ["ANTHROPIC_API_KEY"] = _zeile.split("=", 1)[1].strip('"\'')
                break

# ---------------------------------------------------------------------------
# Ausgabe-Ordner anlegen (vor dem Logging!)
# ---------------------------------------------------------------------------

for d in (AUSGABE_BASIS, ZIELORDNER, FEHLERORDNER, DUPLIKAT_ORDNER,
          ERLEDIGT_BASIS, UNSORTIERT_ORDNER, BRAUCHT_VISION_ORDNER):
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

# Schlüssel, die Metadaten einer Kategorie sind – keine Unterkategorien.
_META_KEYS = {"beschreibung", "verarbeitung"}


def kategorien_flatten(d, prefix=""):
    """Wandelt den (verschachtelten) Kategorienbaum in Slash-Pfade um.

    Ein dict gilt als Blatt-Kategorie, sobald es "beschreibung" enthält.
    Skalare Metafelder (z.B. "verarbeitung": "lokal" auf Knotenebene) werden
    übersprungen und nie als Kategorie ausgegeben."""
    result = []
    for key, value in d.items():
        if key in _META_KEYS:
            continue
        if not isinstance(value, dict):
            continue  # Metadaten-Skalar, keine Kategorie
        if "beschreibung" in value:
            result.append(f"{prefix}{key}")
        else:
            result.extend(kategorien_flatten(value, f"{prefix}{key}/"))
    return result


KATEGORIEN = kategorien_flatten(KATEGORIEN_INFO)


def verarbeitung_fuer(kategorie):
    """Ermittelt die Datenschutz-Einstellung ('lokal' oder 'cloud') für einen
    Kategorie-Slash-Pfad. Das verarbeitung-Feld wird entlang des Pfads vererbt
    (Elternknoten wirken auf Kinder), das tiefste gewinnt. Default: 'lokal'."""
    setting = "lokal"
    knoten = KATEGORIEN_INFO
    for teil in kategorie.split("/"):
        if not isinstance(knoten, dict) or teil not in knoten:
            break
        knoten = knoten[teil]
        if isinstance(knoten, dict) and isinstance(knoten.get("verarbeitung"), str):
            setting = knoten["verarbeitung"]
    return setting.strip().lower()


# ---------------------------------------------------------------------------
# Antwort-Parsing & Validierung
# ---------------------------------------------------------------------------

def parse_antwort(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text.strip())


def naechste_kategorie(cat):
    """Ordnet einen (evtl. abgewandelten) Modell-Output der nächstliegenden gültigen
    Kategorie zu. Bewusst konservativ – lieber None (→ _Unsortiert) als Fehlablage.
    Reihenfolge: exakt (case-insensitiv) → Teilstring langer Namen → difflib-Ähnlichkeit."""
    c = cat.strip().lower()
    kand = {}
    for k in KATEGORIEN:
        kand[k.lower()] = k
        kand[k.split("/")[-1].lower()] = k
    if c in kand:
        return kand[c]
    if len(c) >= 8:
        for kl, kanon in kand.items():
            if len(kl) >= 8 and (c in kl or kl in c):
                return kanon
    treffer = difflib.get_close_matches(c, list(kand.keys()), n=1, cutoff=0.82)
    return kand[treffer[0]] if treffer else None


def validiere_ergebnis(ergebnis):
    if "category" not in ergebnis or "filename" not in ergebnis:
        raise ValueError(f"Fehlende Felder: {ergebnis}")
    cat = str(ergebnis["category"]).strip()
    if cat not in KATEGORIEN:
        zuordnung = naechste_kategorie(cat)
        if zuordnung:
            log.info(f"  ↳ Kategorie '{cat}' → '{zuordnung}' (Nächste-Treffer-Zuordnung)")
            cat = zuordnung
        else:
            raise ValueError(f"Ungültige Kategorie: {ergebnis['category']}")
    ergebnis["category"] = cat
    # Dateinamen säubern: evtl. mitgeliefertes .pdf entfernen (Code hängt es selbst an)
    name = str(ergebnis["filename"]).strip()
    while name.lower().endswith(".pdf"):
        name = name[:-4].rstrip()
    ergebnis["filename"] = name
    return ergebnis


def pdf_text_lesen(pfad):
    doc = fitz.open(pfad)
    text = ""
    for i in range(min(5, len(doc))):
        text += doc[i].get_text()
    return text[:20000]


def text_mit_ocr(pfad):
    """Liest den eingebetteten Text. Ist er zu dünn (< 50 Zeichen) und OCR aktiv,
    legt ocrmypdf (deu+eng) eine Textebene an und liest erneut.
    Rückgabe: (text, ocr_genutzt)."""
    text = pdf_text_lesen(pfad)
    if len(text.strip()) >= 50 or not OCR_AKTIV or not os.path.exists(OCRMYPDF):
        return text, False
    tmp = pfad.replace(".pdf", ".ocr.pdf")
    try:
        subprocess.run([OCRMYPDF, "--skip-text", "-l", "deu+eng", pfad, tmp],
                       check=True, capture_output=True, timeout=300)
        neu = pdf_text_lesen(tmp)
        return (neu, True) if len(neu.strip()) > len(text.strip()) else (text, False)
    except Exception as e:
        log.warning(f"  → OCR fehlgeschlagen: {e}")
        return text, False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


BILD_MAX_BYTES = 4 * 1024 * 1024   # 4 MB pro Seite, sicher unter API-Limit


def pdf_seiten_als_bilder(pfad, max_seiten=3):
    """Rendert PDF-Seiten als JPEG für die Vision-Analyse.
    Zoom startet bei 1.5× und wird halbiert bis das Bild unter BILD_MAX_BYTES liegt."""
    doc = fitz.open(pfad)
    bilder = []
    for i in range(min(max_seiten, len(doc))):
        page = doc[i]
        zoom = 1.5
        while zoom >= 0.5:
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            jpeg_bytes = pix.tobytes(output="jpeg", jpg_quality=85)
            if len(jpeg_bytes) <= BILD_MAX_BYTES:
                break
            zoom -= 0.25
        bildpfad = pfad.replace(".pdf", f"_seite_{i+1}.jpg")
        with open(bildpfad, "wb") as f:
            f.write(jpeg_bytes)
        bilder.append(bildpfad)
    return bilder


def _bilder_base64(bildpfade):
    out = []
    for bildpfad in bildpfade:
        with open(bildpfad, "rb") as f:
            out.append(base64.b64encode(f.read()).decode("utf-8"))
    return out


def _kategorie_knoten(pfad):
    """Liefert das Blatt-dict einer Kategorie über ihren Slash-Pfad."""
    knoten = KATEGORIEN_INFO
    for teil in pfad.split("/"):
        knoten = knoten[teil]
    return knoten


def _kb_block():
    """Kompakter Kategorie-Block für den Prompt: pro Kategorie nur Beschreibung und
    Abgrenzung (die negativen Hinweise). Absender/Stichwörter bleiben bewusst DRAUSSEN –
    die nutzt der deterministische Absender-Lookup VOR dem Modell (schnell + exakt),
    nicht der Prompt (das würde ihn aufblähen und das Modell verwirren)."""
    bloecke = []
    for pfad in KATEGORIEN:
        k = _kategorie_knoten(pfad)
        zeilen = [f"### {pfad}"]
        if k.get("beschreibung"):
            zeilen.append(k["beschreibung"])
        if k.get("abgrenzung"):
            zeilen.append(f"Abgrenzung: {k['abgrenzung']}")
        bloecke.append("\n".join(zeilen))
    return "\n\n".join(bloecke)


# ---------------------------------------------------------------------------
# Absender-Lookup: bekannte Firmen → Kategorie (deterministisch, ohne LLM)
# ---------------------------------------------------------------------------

def unternehmen_index(info=None):
    """Baut {firma_lower: kategorie_pfad} aus den 'unternehmen'-Listen der KB."""
    idx = {}
    for pfad in kategorien_flatten(info or KATEGORIEN_INFO):
        knoten = info or KATEGORIEN_INFO
        for teil in pfad.split("/"):
            knoten = knoten[teil]
        for firma in knoten.get("unternehmen", []):
            f = firma.strip().lower()
            if len(f) >= 3:
                idx.setdefault(f, pfad)
    return idx


_UNTERNEHMEN_INDEX = unternehmen_index()


def lookup_kategorie(text, dateiname=""):
    """Sucht bekannte Absender in Text+Dateiname. Nur wenn GENAU EINE Kategorie
    eindeutig matcht, wird sie zurückgegeben – sonst None (dann entscheidet das Modell)."""
    heu = f"{dateiname}\n{text}".lower()
    treffer = {kat for firma, kat in _UNTERNEHMEN_INDEX.items() if firma in heu}
    return treffer.pop() if len(treffer) == 1 else None


# Pfade, die das System selbst in _Sortiert geschrieben hat – die werden NICHT gelernt.
_SELBST_EINSORTIERT = set()


def kategorien_neu_laden():
    """Liest kategorien.json neu ein und baut Kategorienliste + Absender-Index neu auf."""
    global KATEGORIEN_INFO, KATEGORIEN, _UNTERNEHMEN_INDEX
    with open(KATEGORIEN_JSON, encoding="utf-8") as f:
        KATEGORIEN_INFO = json.load(f)
    KATEGORIEN = kategorien_flatten(KATEGORIEN_INFO)
    _UNTERNEHMEN_INDEX = unternehmen_index()


def _kategorie_aus_ordner(ordnerpfad):
    """Kategorie-Slash-Pfad aus einem _Sortiert-Unterordner; ein Scanner-Datums-Subordner
    wird übersprungen. None, wenn außerhalb von _Sortiert oder keine bekannte Kategorie."""
    try:
        rel = os.path.relpath(os.path.realpath(ordnerpfad), os.path.realpath(ZIELORDNER))
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    teile = [t for t in rel.split(os.sep) if t and t != "."]
    if teile and DATE_MUSTER.match(teile[0]):
        teile = teile[1:]
    kat = "/".join(teile)
    return kat if kat in KATEGORIEN else None


def _absender_aus_dateiname(dateiname):
    """Feld 2 (Absender) aus 'YYYY-MM-DD_Absender_Typ_Jahr.pdf'. None, wenn Schema nicht passt."""
    name = os.path.splitext(os.path.basename(dateiname))[0]
    teile = name.split("_")
    if len(teile) >= 2 and DATE_MUSTER.match(teile[0]) and len(teile[1]) >= 2:
        return teile[1]
    return None


def lerne_absender(dest_pfad):
    """Lernt aus einer MANUELL in eine _Sortiert-Kategorie gelegten Datei den Absender und
    trägt ihn in 'unternehmen' der Zielkategorie ein. Eigene Schreibvorgänge des Systems
    werden übersprungen – so lernt DocNamer nur aus echten Nutzer-Korrekturen."""
    real = os.path.realpath(dest_pfad)
    if real in _SELBST_EINSORTIERT:
        _SELBST_EINSORTIERT.discard(real)
        return
    if not dest_pfad.lower().endswith(".pdf"):
        return
    kategorie = _kategorie_aus_ordner(os.path.dirname(dest_pfad))
    absender  = _absender_aus_dateiname(dest_pfad)
    if not kategorie or not absender:
        return
    if _UNTERNEHMEN_INDEX.get(absender.lower()) == kategorie:
        return  # schon bekannt
    try:
        with open(KATEGORIEN_JSON, encoding="utf-8") as f:
            daten = json.load(f)
        knoten = daten
        for teil in kategorie.split("/"):
            knoten = knoten[teil]
        liste = knoten.setdefault("unternehmen", [])
        if absender not in liste:
            liste.append(absender)
            with open(KATEGORIEN_JSON, "w", encoding="utf-8") as f:
                json.dump(daten, f, ensure_ascii=False, indent=2)
            kategorien_neu_laden()
            log.info(f"  🧠 Gelernt: Absender '{absender}' → {kategorie}")
    except Exception as e:
        log.warning(f"  → Lernen fehlgeschlagen: {e}")


def analyse_prompt(dateiname, text=None, fehler_hinweis=None):
    """Strenger Prompt für Kategorie + Dateiname – verhindert erfundene Kategorien.
    fehler_hinweis: bei einem Retry der Korrekturtext zur vorigen ungültigen Antwort."""
    teil_text = f"\n\nDokument:\n{text}" if text else ""
    hinweis_block = f"\n\n‼️ KORREKTUR: {fehler_hinweis}\n" if fehler_hinweis else ""
    return f"""Du bist ein präziser Dokumenten-Sortierer. Antworte AUSSCHLIESSLICH mit einem JSON-Objekt.{hinweis_block}

REGELN für "category" – sehr wichtig:
- Wähle GENAU EINE Kategorie aus der Liste unten.
- Übernimm sie ZEICHENGENAU (gleiche Groß-/Kleinschreibung, gleicher Singular/Plural, gleiche Schrägstriche).
- Du darfst Kategorien NIEMALS erfinden, übersetzen, kürzen, kombinieren oder umformulieren.
- Gibt es keine eindeutig passende Kategorie, verwende exakt: "Sonstiges".

ERLAUBTE KATEGORIEN – die "category" ist GENAU der Pfad aus der ###-Überschrift
(bei Unterkategorien inkl. Schrägstrich, z.B. "Anlagen und Beteiligungen/DLF").
Nutze "Typische Absender", "Dokumenttypen", "Stichwörter" und "Abgrenzung" zum Einordnen:

{_kb_block()}

REGELN für "filename":
- Format: YYYY-MM-DD_Absender_Dokumenttyp_Abrechnungsjahr
- YYYY-MM-DD = Belegdatum des Dokuments.
- Absender = ausstellende Firma/Organisation, kurz (z.B. Allianz, Sparkasse, Finanzamt) – NICHT der Empfänger.
- Dokumenttyp = Art des Dokuments (z.B. Rechnung, Beitragsrechnung, Zinsbescheinigung, Einkommensteuerbescheid).
- Abrechnungsjahr = Jahr, auf das sich das Dokument bezieht (z.B. 2025); weglassen, wenn nicht erkennbar.
- keine Umlaute, keine Leerzeichen (mehrteilige Felder als CamelCase), Felder mit _ trennen, kurz halten.

Originaldateiname: {dateiname}{teil_text}

Antworte NUR mit JSON, kein weiterer Text:
{{"category": "...", "filename": "..."}}"""


# ---------------------------------------------------------------------------
# Analyse – lokal (Ollama) und Cloud (Anthropic)
# ---------------------------------------------------------------------------

def _ollama_chat(modell, prompt, bilder_b64=None):
    """Ruft die lokale Ollama-Chat-API auf und liefert den Antworttext.
    format=json erzwingt valides JSON, temperature=0 macht die Kategorie stabil."""
    nachricht = {"role": "user", "content": prompt}
    if bilder_b64:
        nachricht["images"] = bilder_b64
    payload = json.dumps({
        "model": modell,
        "messages": [nachricht],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "keep_alive": OLLAMA_KEEPALIVE,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))["message"]["content"]


def _analyse_lokal(modell, dateiname, text=None, bildpfade=None):
    """Ollama-Analyse mit einem Retry: scheitert die Validierung (ungültige/erfundene
    Kategorie, fehlende Felder), wird einmal mit Korrekturhinweis nachgefragt, der das
    Modell auf die erlaubte Liste bzw. "Sonstiges" zwingt. Erst dann schlägt es fehl."""
    bilder = _bilder_base64(bildpfade) if bildpfade else None
    inhalt = _ollama_chat(modell, analyse_prompt(dateiname, text), bilder_b64=bilder)
    try:
        return validiere_ergebnis(parse_antwort(inhalt))
    except (ValueError, json.JSONDecodeError) as e:
        log.info(f"  ↻ Ungültige Antwort ({e}) – Retry mit Korrekturhinweis")
        hinweis = (f'Deine letzte Antwort war ungültig ({e}). Wähle JETZT GENAU EINE '
                   f'Kategorie ZEICHENGENAU aus der erlaubten Liste oben – oder, wenn '
                   f'keine eindeutig passt, exakt "Sonstiges". Erfinde keine Kategorie.')
        inhalt = _ollama_chat(modell, analyse_prompt(dateiname, text, hinweis), bilder_b64=bilder)
        return validiere_ergebnis(parse_antwort(inhalt))


def analyse_lokal_text(dateiname, text):
    return _analyse_lokal(OLLAMA_MODELL_TEXT, dateiname, text=text)


def analyse_lokal_bild(dateiname, bildpfade):
    return _analyse_lokal(OLLAMA_MODELL_VISION, dateiname, bildpfade=bildpfade)


client = anthropic.Anthropic()


def analyse_cloud_text(dateiname, text):
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": analyse_prompt(dateiname, text)}],
    )
    return validiere_ergebnis(parse_antwort(r.content[0].text))


def analyse_cloud_bild(dateiname, bildpfade):
    content = [{"type": "text", "text": analyse_prompt(dateiname)}]
    for b64 in _bilder_base64(bildpfade):
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    r = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )
    return validiere_ergebnis(parse_antwort(r.content[0].text))


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def macos_notification(titel, untertitel, text=""):
    """Sendet eine macOS-Systembenachrichtigung via osascript."""
    try:
        skript = (
            f'display notification "{text}" '
            f'with title "{titel}" '
            f'subtitle "{untertitel}"'
        )
        subprocess.run(["osascript", "-e", skript], check=False)
    except Exception:
        pass


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


def pdf_dhash(pfad, hash_groesse=8):
    """Perceptueller dHash der ersten PDF-Seite.

    Die Seite wird auf (hash_groesse+1) × hash_groesse Pixel in Graustufen
    gerendert. Für jedes Pixel-Paar nebeneinander wird verglichen ob links
    heller als rechts ist → 64-Bit-Fingerabdruck als Hex-String.
    Fallback auf SHA-256 des Dateiinhalts wenn fitz die Seite nicht rendern kann."""
    try:
        doc   = fitz.open(pfad)
        seite = doc[0]
        rect  = seite.rect
        breite, hoehe = hash_groesse + 1, hash_groesse
        sx = breite / rect.width
        sy = hoehe  / rect.height
        pix = seite.get_pixmap(matrix=fitz.Matrix(sx, sy), colorspace=fitz.csGRAY)
        doc.close()
        samples = pix.samples  # ein Byte pro Pixel, Graustufen
        bits = []
        for y in range(hoehe):
            for x in range(hash_groesse):
                links  = samples[y * breite + x]
                rechts = samples[y * breite + x + 1]
                bits.append(1 if links > rechts else 0)
        n = 0
        for bit in bits:
            n = (n << 1) | bit
        return format(n, f'0{hash_groesse * hash_groesse // 4}x')
    except Exception:
        pass
    # Fallback: SHA-256 des rohen Dateiinhalts (text-basierte PDFs)
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


DHASH_SCHWELLE = 15  # max. Hamming-Distanz für "gleiche" Dokumente (von 64 Bits)


def hamming_distanz(a, b):
    """Anzahl unterschiedlicher Bits zwischen zwei Hex-Strings gleicher Länge."""
    if len(a) != len(b):
        return 999
    diff = int(a, 16) ^ int(b, 16)
    return bin(diff).count("1")


def icloud_download_erzwingen(pfad):
    verzeichnis = os.path.dirname(pfad)
    dateiname   = os.path.basename(pfad)
    platzhalter = os.path.join(verzeichnis, "." + dateiname + ".icloud")
    if not os.path.exists(platzhalter):
        return
    log.info("  → iCloud-Platzhalter erkannt, erzwinge Download...")
    try:
        subprocess.run(["brctl", "download", pfad], check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"brctl download fehlgeschlagen: {e}")
    for _ in range(ICLOUD_TIMEOUT):
        if os.path.exists(pfad) and not os.path.exists(platzhalter):
            log.info("  → iCloud-Download abgeschlossen.")
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
        if not os.path.isdir(pfad):
            continue
        if os.path.islink(pfad):
            continue
        if name.startswith(".") or name.startswith("_"):
            continue
        if name == "Documents":
            continue
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
# PDF verarbeiten – datenschutz-basiertes Routing
# ---------------------------------------------------------------------------

def verarbeite_pdf(pfad):
    """Analysiert eine PDF und verschiebt sie in Ziel-, Fehler- oder Unsortiert-Ordner.

    Ablauf:
      1. Kategorie-Triage IMMER lokal (Ollama): Text → qwen2.5, sonst Vision-Modell.
      2. verarbeitung-Feld der Triage-Kategorie bestimmt das Routing (Default lokal):
         - lokal: das lokale Ergebnis ist final (kein Cloud-Aufruf).
         - cloud: finale Analyse über Anthropic (Haiku/Sonnet).
      3. Scheitert schon die lokale Triage → _Unsortiert (kein Cloud-Vision, kein _Fehler).
    """
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

    # Duplikatprüfung (per DOCNAMER_DEDUP=0 abschaltbar)
    datei_hash = None
    hashes = {}
    if DEDUP_AKTIV:
        try:
            datei_hash = pdf_dhash(pfad)
            hashes = hash_laden()
            treffer_hash = None
            if datei_hash in hashes:
                treffer_hash = datei_hash
            else:
                for bekannter_hash in hashes:
                    if hamming_distanz(datei_hash, bekannter_hash) <= DHASH_SCHWELLE:
                        treffer_hash = bekannter_hash
                        break
            if treffer_hash:
                eintrag  = hashes[treffer_hash]
                datum    = eintrag["datum"]     if isinstance(eintrag, dict) else eintrag
                original = eintrag["dateiname"] if isinstance(eintrag, dict) else datei
                log.info(f"  → Duplikat (verarbeitet am {datum}), verschiebe nach _Duplikate.")
                dup_pfad = eindeutiger_pfad(DUPLIKAT_ORDNER, datei)
                shutil.move(pfad, dup_pfad)
                leere_ordner_archivieren()
                macos_notification(
                    "⚠️ DocNamer – Duplikat erkannt",
                    f"{datei}",
                    f"Bereits sortiert als: {original} – bitte _Duplikate prüfen."
                )
                return
        except Exception as e:
            log.warning(f"  → Hash-Prüfung fehlgeschlagen: {e}")

    qname = quellordner_name(pfad)
    try:
        text, ocr_genutzt = text_mit_ocr(pfad)
        if ocr_genutzt:
            log.info("  → OCR-Textebene erzeugt (ocrmypdf)")

        # Kein verwertbarer Text (auch nach OCR) → braucht Vision → parken.
        # NICHT automatisch zur Cloud; der Nutzer entscheidet später (--vision).
        if len(text.strip()) < 50:
            log.info("  → kein Text (auch nach OCR) → geparkt in _BrauchtVision")
            ziel = eindeutiger_pfad(BRAUCHT_VISION_ORDNER, datei)
            shutil.move(pfad, ziel)
            macos_notification(
                "👁 DocNamer – braucht Vision", datei,
                "Kein Text erkannt – liegt in _BrauchtVision (du entscheidest über Vision)."
            )
            leere_ordner_archivieren()
            return

        # Lokale Textanalyse (Ollama) – rein lokal, keine Cloud
        try:
            log.info("  → Lokale Analyse (Ollama Text)")
            ergebnis = analyse_lokal_text(datei, text)
        except Exception as e:
            log.warning(f"  → Lokale Analyse fehlgeschlagen ({e}) → _Unsortiert")
            ziel = eindeutiger_pfad(UNSORTIERT_ORDNER, datei)
            shutil.move(pfad, ziel)
            macos_notification(
                "📂 DocNamer – nicht einsortiert", datei,
                "Lokale Kategorie-Erkennung fehlgeschlagen – liegt in _Unsortiert."
            )
            leere_ordner_archivieren()
            return

        # Absender-Lookup hat Vorrang vor dem Modell: kennt die KB die Firma eindeutig,
        # gewinnt der deterministische Treffer (schnell + exakt für Stammabsender).
        treffer = lookup_kategorie(text, datei)
        if treffer and treffer != ergebnis["category"]:
            log.info(f"  ↪ Absender-Lookup: {ergebnis['category']} → {treffer}")
            ergebnis["category"] = treffer

        kategorie = ergebnis["category"]
        filename  = ergebnis["filename"] + ".pdf"

        if qname:
            zielordner = os.path.join(ZIELORDNER, qname, kategorie)
        else:
            zielordner = os.path.join(ZIELORDNER, kategorie)
        os.makedirs(zielordner, exist_ok=True)
        zielpfad = eindeutiger_pfad(zielordner, filename)

        _SELBST_EINSORTIERT.add(os.path.realpath(zielpfad))  # eigener Schreibvorgang, nicht lernen
        shutil.move(pfad, zielpfad)
        csv_zeile_schreiben(pfad, zielpfad, kategorie)
        log.info(f"  ✓ Kategorie : {kategorie}  (lokal)")
        log.info(f"  ✓ Neu       : {zielpfad}")

        if DEDUP_AKTIV and datei_hash:
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


def vision_ordner_verarbeiten():
    """Verarbeitet die in _BrauchtVision geparkten Dokumente über die Cloud-Vision
    (Sonnet) und sortiert sie ein. Wird NUR auf ausdrückliche Aktion (--vision)
    aufgerufen – der Nutzer kuratiert _BrauchtVision vorher (Unerwünschtes entfernen)."""
    pdfs = sorted(f for f in os.listdir(BRAUCHT_VISION_ORDNER) if f.lower().endswith(".pdf"))
    log.info(f"Vision-Verarbeitung (Cloud/Sonnet): {len(pdfs)} Dokument(e) in _BrauchtVision")
    for datei in pdfs:
        pfad = os.path.join(BRAUCHT_VISION_ORDNER, datei)
        bildpfade = []
        try:
            log.info(f"Vision: {datei}")
            bildpfade = pdf_seiten_als_bilder(pfad, max_seiten=3)
            ergebnis  = analyse_cloud_bild(datei, bildpfade)
            kategorie = ergebnis["category"]
            filename  = ergebnis["filename"] + ".pdf"
            zielordner = os.path.join(ZIELORDNER, kategorie)
            os.makedirs(zielordner, exist_ok=True)
            zielpfad = eindeutiger_pfad(zielordner, filename)
            shutil.move(pfad, zielpfad)
            csv_zeile_schreiben(pfad, zielpfad, kategorie)
            log.info(f"  ✓ Kategorie : {kategorie}  (cloud-vision)")
            log.info(f"  ✓ Neu       : {zielpfad}")
        except Exception as e:
            log.error(f"  ✗ Vision-Fehler bei {datei}: {e}")
            try:
                shutil.move(pfad, eindeutiger_pfad(FEHLERORDNER, datei))
            except Exception:
                pass
        finally:
            for bild in bildpfade:
                if os.path.exists(bild):
                    os.remove(bild)
    log.info("Vision-Verarbeitung abgeschlossen.")


# ---------------------------------------------------------------------------
# Watchdog – Kategorie-Ordner in kategorien.json pflegen
# ---------------------------------------------------------------------------

DATE_MUSTER = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def neue_kategorie_eintragen(ordner_pfad, still=False):
    """Trägt einen neu im Finder angelegten Ordner als Kategorie in kategorien.json ein."""
    ziel_real = os.path.realpath(ZIELORDNER)
    pfad_real = os.path.realpath(ordner_pfad)
    try:
        rel = os.path.relpath(pfad_real, ziel_real)
    except ValueError:
        return

    teile = [t for t in rel.split(os.sep) if t]
    if not teile:
        return

    if DATE_MUSTER.match(teile[0]):
        teile = teile[1:]
    if not teile:
        return

    try:
        with open(KATEGORIEN_JSON, "r", encoding="utf-8") as f:
            daten = json.load(f)

        knoten = daten
        for teil in teile[:-1]:
            if teil not in knoten or not isinstance(knoten[teil], dict):
                log.warning(f"  → Elternknoten '{teil}' nicht in kategorien.json – Kategorie nicht eingetragen.")
                return
            knoten = knoten[teil]

        name = teile[-1]
        if name in knoten:
            return  # bereits vorhanden

        knoten[name] = {
            "beschreibung": f"Im Finder angelegt am {datetime.now().strftime('%Y-%m-%d')} – bitte Beschreibung ergänzen"
        }

        with open(KATEGORIEN_JSON, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=2)

        kategorie_pfad = "/".join(teile)
        log.info(f"  ✓ Neue Kategorie in kategorien.json eingetragen: {kategorie_pfad}")
        if not still:
            macos_notification(
                "📁 DocNamer – Neue Kategorie erkannt",
                kategorie_pfad,
                "Kategorie wurde automatisch eingetragen. Beschreibung optional ergänzen."
            )

    except Exception as e:
        log.warning(f"  → kategorien.json konnte nicht aktualisiert werden: {e}")


_TEMP_ORDNER_NAMEN = {"neuer ordner", "untitled folder", "unbenannter ordner"}


def _ist_temp_name(pfad):
    return os.path.basename(pfad).strip().lower() in _TEMP_ORDNER_NAMEN


def kategorie_umbenennen(alter_pfad, neuer_pfad):
    """Benennt einen Kategorie-Eintrag in kategorien.json um."""
    ziel_real = os.path.realpath(ZIELORDNER)

    def teile_aus_pfad(pfad):
        try:
            rel = os.path.relpath(os.path.realpath(pfad), ziel_real)
        except ValueError:
            return []
        teile = [t for t in rel.split(os.sep) if t]
        if teile and DATE_MUSTER.match(teile[0]):
            teile = teile[1:]
        return teile

    alte_teile = teile_aus_pfad(alter_pfad)
    neue_teile = teile_aus_pfad(neuer_pfad)

    if not alte_teile or not neue_teile:
        return

    try:
        with open(KATEGORIEN_JSON, "r", encoding="utf-8") as f:
            daten = json.load(f)

        knoten = daten
        for teil in alte_teile[:-1]:
            if teil not in knoten:
                break
            knoten = knoten[teil]
        else:
            alter_name = alte_teile[-1]
            neuer_name = neue_teile[-1]
            if alter_name in knoten:
                wert = knoten.pop(alter_name)
                knoten[neuer_name] = wert
                with open(KATEGORIEN_JSON, "w", encoding="utf-8") as f:
                    json.dump(daten, f, ensure_ascii=False, indent=2)
                log.info(f"  ✓ Kategorie umbenannt: {alter_name} → {neuer_name}")
                return

    except Exception as e:
        log.warning(f"  → kategorien.json umbenennen fehlgeschlagen: {e}")

    neue_kategorie_eintragen(neuer_pfad)


class SortierOrdnerHandler(FileSystemEventHandler):
    """Überwacht _Sortiert/ auf neue und umbenannte Ordner → kategorien.json."""

    def on_created(self, event):
        if event.is_directory:
            if not _ist_temp_name(event.src_path):
                neue_kategorie_eintragen(event.src_path)
        else:
            lerne_absender(event.src_path)          # manuell hineingelegte Datei → lernen

    def on_moved(self, event):
        if not event.is_directory:
            lerne_absender(event.dest_path)          # manuell verschobene Datei → lernen
            return
        if _ist_temp_name(event.src_path):
            neue_kategorie_eintragen(event.dest_path)
        else:
            kategorie_umbenennen(event.src_path, event.dest_path)


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
        for ignore in (ZIELORDNER, FEHLERORDNER, DUPLIKAT_ORDNER,
                       ERLEDIGT_BASIS, UNSORTIERT_ORDNER, BRAUCHT_VISION_ORDNER):
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


def sortiert_ordner_abgleichen():
    """Vergleicht beim Start alle Ordner in _Sortiert/ mit kategorien.json."""
    if not os.path.exists(ZIELORDNER):
        return
    neu = 0
    for root, dirs, _ in os.walk(ZIELORDNER):
        for d in dirs:
            pfad = os.path.join(root, d)
            neue_kategorie_eintragen(pfad, still=True)
            neu += 1
    if neu:
        log.info(f"Ordner-Abgleich: {neu} Ordner geprüft.")


def startup_scan():
    fehler_zurueckholen()
    sortiert_ordner_abgleichen()
    log.info("Startup-Scan: suche vorhandene PDFs...")
    gefunden = 0
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
    modus = "Vision-Cloud" if VISION_MODUS else ("Einmal-Scan" if EINMAL_MODUS else "Watcher")
    log.info(f"DocNamer {VERSION} gestartet  [{modus}]  (Lab: lokal-first + OCR)")
    log.info(f"Überwachter Ordner : {ORDNER}")
    log.info(f"Zielordner         : {ZIELORDNER}")
    log.info(f"Braucht Vision     : {BRAUCHT_VISION_ORDNER}")
    log.info(f"Ollama Text        : {OLLAMA_MODELL_TEXT}")
    log.info(f"OCR / Dedup        : ocr={OCR_AKTIV}  dedup={DEDUP_AKTIV}")
    log.info("=" * 60)

    if VISION_MODUS:
        # Nur die geparkten _BrauchtVision-Dokumente über die Cloud verarbeiten
        vision_ordner_verarbeiten()
    elif EINMAL_MODUS:
        startup_scan()
        log.info("Einmal-Scan abgeschlossen.")
    else:
        startup_scan()

        handler  = PDFHandler()
        observer = Observer()
        observer.schedule(handler, ORDNER, recursive=True)

        sortier_handler = SortierOrdnerHandler()
        observer.schedule(sortier_handler, ZIELORDNER, recursive=True)

        observer.start()
        log.info(f"Kategorie-Erkennung aktiv: {ZIELORDNER}")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("Watcher wird beendet...")
            observer.stop()

        observer.join()
        log.info("Watcher beendet.")
