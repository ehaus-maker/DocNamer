#!/usr/bin/env python3
"""
docnamer_korrektur.py – Manuelle Korrekturen zur Lernbasis hinzufügen

Verwendung:
  python docnamer_korrektur.py                         # Interaktiver Modus
  python docnamer_korrektur.py --liste                 # Alle Korrekturen anzeigen
  python docnamer_korrektur.py --loesche <dateiname>   # Korrektur entfernen
"""

import os
import sys
import json
import fitz
from datetime import datetime

KORREKTUREN_JSON = os.path.join(os.path.dirname(__file__), "korrekturen.json")
KATEGORIEN_JSON  = os.path.join(os.path.dirname(__file__), "kategorien.json")

def kategorien_flatten(d, prefix=""):
    result = []
    for key, value in d.items():
        if isinstance(value, dict) and "beschreibung" not in value:
            result.extend(kategorien_flatten(value, f"{prefix}{key}/"))
        else:
            result.append(f"{prefix}{key}")
    return result

def kategorien_laden():
    with open(KATEGORIEN_JSON, "r", encoding="utf-8") as f:
        info = json.load(f)
    return kategorien_flatten(info)

def korrekturen_laden():
    if not os.path.exists(KORREKTUREN_JSON):
        return []
    with open(KORREKTUREN_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def korrekturen_speichern(korrekturen):
    with open(KORREKTUREN_JSON, "w", encoding="utf-8") as f:
        json.dump(korrekturen, f, ensure_ascii=False, indent=2)

def pdf_text_snippet(pfad):
    try:
        doc = fitz.open(pfad)
        text = ""
        for i in range(min(2, len(doc))):
            text += doc[i].get_text()
        return text.strip()[:300]
    except Exception:
        return ""

def liste_anzeigen():
    korrekturen = korrekturen_laden()
    if not korrekturen:
        print("Keine Korrekturen vorhanden.")
        return
    print(f"\n{'='*70}")
    print(f"  {len(korrekturen)} gespeicherte Korrektur(en)")
    print(f"{'='*70}")
    for i, k in enumerate(korrekturen, 1):
        print(f"\n[{i}] {k.get('zeitstempel', '?')}")
        print(f"    Datei    : {k['original_filename']}")
        print(f"    KI hatte : {k['ki_kategorie']}")
        print(f"    Richtig  : {k['korrekte_kategorie']} / {k['korrekter_filename']}")
        snippet = k.get('ocr_text_snippet', '')
        if snippet:
            print(f"    Text     : {snippet[:80]}...")
    print()

def korrektur_loeschen(dateiname):
    korrekturen = korrekturen_laden()
    vorher = len(korrekturen)
    korrekturen = [k for k in korrekturen if k.get("original_filename") != dateiname]
    if len(korrekturen) < vorher:
        korrekturen_speichern(korrekturen)
        print(f"✓ Korrektur für '{dateiname}' gelöscht.")
    else:
        print(f"✗ Keine Korrektur für '{dateiname}' gefunden.")

def interaktiv():
    kategorien = kategorien_laden()
    korrekturen = korrekturen_laden()

    print("\n" + "="*60)
    print("  DocNamer – Korrektur erfassen")
    print("="*60)

    pfad_input = input("\nPfad zur PDF-Datei (oder Enter zum Abbrechen): ").strip()
    if not pfad_input:
        print("Abgebrochen.")
        return

    pfad = os.path.expanduser(pfad_input)
    if not os.path.exists(pfad):
        print(f"✗ Datei nicht gefunden: {pfad}")
        return

    dateiname = os.path.basename(pfad)
    snippet   = pdf_text_snippet(pfad)

    print(f"\nDatei    : {dateiname}")
    if snippet:
        print(f"Textauszug: {snippet[:120]}...")

    ki_kat = input("\nWelche Kategorie hat die KI vergeben? (Enter = unbekannt): ").strip()
    if not ki_kat:
        ki_kat = "unbekannt"

    print(f"\nVerfügbare Kategorien:")
    for i, k in enumerate(kategorien, 1):
        print(f"  {i:3}. {k}")

    while True:
        auswahl = input("\nNummer der korrekten Kategorie: ").strip()
        if auswahl.isdigit() and 1 <= int(auswahl) <= len(kategorien):
            korrekte_kat = kategorien[int(auswahl) - 1]
            break
        print("Ungültige Eingabe, bitte Nummer eingeben.")

    vorschlag = os.path.splitext(dateiname)[0]
    korrekter_name = input(f"\nKorrekter Dateiname (ohne .pdf) [{vorschlag}]: ").strip()
    if not korrekter_name:
        korrekter_name = vorschlag

    korrekturen = [k for k in korrekturen if k.get("original_filename") != dateiname]
    korrekturen.append({
        "zeitstempel": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "original_filename": dateiname,
        "ocr_text_snippet": snippet,
        "ki_kategorie": ki_kat,
        "korrekte_kategorie": korrekte_kat,
        "korrekter_filename": korrekter_name
    })
    korrekturen_speichern(korrekturen)

    print(f"\n✓ Korrektur gespeichert!")
    print(f"  {dateiname} → {korrekte_kat} / {korrekter_name}")
    print(f"  Gesamt: {len(korrekturen)} Korrektur(en) in der Lernbasis.\n")

if __name__ == "__main__":
    if "--liste" in sys.argv:
        liste_anzeigen()
    elif "--loesche" in sys.argv:
        idx = sys.argv.index("--loesche")
        if idx + 1 < len(sys.argv):
            korrektur_loeschen(sys.argv[idx + 1])
        else:
            print("Verwendung: python docnamer_korrektur.py --loesche <dateiname>")
    else:
        interaktiv()
