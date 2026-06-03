import os
import json
import fitz
from openai import OpenAI

client = OpenAI()
ORDNER = "."

KATEGORIEN = [

 
    "Reparatur Dienstleistungen",
    "Jahressteuerbescheinigungen",
    "Rechnungen Büro Siersburg",
    "Zinsbescheinigungen",
    "Beiträge Verbände",
    "Photovoltaik Abrechnungen",
    "Haushaltscheck Minijob",
    "Beischeinigung Beiträge Krankenversicherung",
    "Spendenbescheinigungen",
    "Jahresverbrauchsabrechnungen",
    "Anlagen und Beteiligungen",
    "Versicherungen"
    "Sonstiges"

]

def pdf_text_lesen(pfad):
    doc = fitz.open(pfad)
    text = ""
    for i in range(min(2, len(doc))):
        text += doc[i].get_text()
    return text[:8000]

def dokument_analysieren(dateiname, text):
    prompt = f"""
Analysiere dieses deutsche Dokument.

Erzeuge einen sinnvollen Dateinamen und eine Kategorie.

Erlaubte Kategorien:
{", ".join(KATEGORIEN)}

Originaldateiname:
{dateiname}

Dokumenttext:
{text}
"""

    r = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "dokument_analyse",
                "schema": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": KATEGORIEN
                        },
                        "filename": {
                            "type": "string",
                            "description": "Dateiname ohne .pdf, Format YYYY-MM-DD_Absender_Dokumenttyp_Zusatz, keine Leerzeichen, keine Umlaute"
                        }
                    },
                    "required": ["category", "filename"],
                    "additionalProperties": False
                },
                "strict": True
            }
        }
    )

    print("RAW GPT:", r.output_text)
    return json.loads(r.output_text)

import csv
import shutil

ZIELORDNER = "_Sortiert"
vorschlaege = []

def eindeutiger_pfad(ordner, dateiname):
    basis, endung = os.path.splitext(dateiname)
    ziel = os.path.join(ordner, dateiname)
    nr = 1

    while os.path.exists(ziel):
        ziel = os.path.join(ordner, f"{basis}_{nr}{endung}")
        nr += 1

    return ziel

for root, dirs, files in os.walk(ORDNER):

    if ZIELORDNER in root:
        continue

    for datei in files:

        if not datei.lower().endswith(".pdf"):
            continue

        pfad = os.path.join(root, datei)

        print("\n" + "=" * 70)
        print("Alt:", pfad)

        text = pdf_text_lesen(pfad)

        if len(text.strip()) < 50:
            print("Zu wenig Text erkannt.")
            continue

        ergebnis = dokument_analysieren(datei, text)

        kategorie = ergebnis.get("category", "Sonstiges")
        filename = ergebnis.get("filename", "Unbekanntes_Dokument") + ".pdf"

        ziel_kategorie_ordner = os.path.join(ORDNER, ZIELORDNER, kategorie)
        os.makedirs(ziel_kategorie_ordner, exist_ok=True)

        zielpfad = eindeutiger_pfad(ziel_kategorie_ordner, filename)

        print("Kategorie:", kategorie)
        print("Neu:", zielpfad)

        shutil.move(pfad, zielpfad)

        vorschlaege.append([
            pfad,
            zielpfad,
            kategorie
        ])

with open(os.path.join(ORDNER, ZIELORDNER, "umbenennung.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["Alter Pfad", "Neuer Pfad", "Kategorie"])
    writer.writerows(vorschlaege)

print("\nFertig. Sortiert in:", os.path.join(ORDNER, ZIELORDNER))