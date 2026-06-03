import os
import sys
import json
import csv
import shutil
import base64
import fitz
from openai import OpenAI

client = OpenAI()

if len(sys.argv) > 1:
    ORDNER = sys.argv[1]
else:
    ORDNER = "."

ZIELORDNER = "_Sortiert"

with open(
    "kategorien.json",
    "r",
    encoding="utf-8"
) as f:
    KATEGORIEN_INFO = json.load(f)

def kategorien_flatten(d, prefix=""):
    result = []

    for key, value in d.items():

        if isinstance(value, dict) and "beschreibung" not in value:
            result.extend(
                kategorien_flatten(
                    value,
                    f"{prefix}{key}/"
                )
            )
        else:
            result.append(f"{prefix}{key}")

    return result

KATEGORIEN = kategorien_flatten(KATEGORIEN_INFO)
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

SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": KATEGORIEN},
        "filename": {"type": "string"}
    },
    "required": ["category", "filename"],
    "additionalProperties": False
}

def dokument_analysieren_text(dateiname, text):
    prompt = f"""
Analysiere dieses deutsche Dokument.

Erzeuge:
- Kategorie
- Dateiname

Dateinamenformat:
YYYY-MM-DD_Hauptobjekt_Dokumenttyp_Zusatz

Keine Umlaute.
Keine Leerzeichen.

Kategorien mit Beschreibung:
{json.dumps(KATEGORIEN_INFO, ensure_ascii=False, indent=2)}

Dateiname: {dateiname}

Dokument:
{text}
"""
    r = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "analyse",
                "schema": SCHEMA,
                "strict": True
            }
        }
    )
    return json.loads(r.output_text)

def dokument_analysieren_bild(dateiname, bildpfade):
    prompt = f"""
Analysiere alle Seiten dieses Dokuments.

Bestimme:
- Kategorie
- Datum
- Hauptobjekt (z.B. Porsche911, Sparkasse, Finanzamt)
- Dokumenttyp

Dateinamenformat:
YYYY-MM-DD_Hauptobjekt_Dokumenttyp_Zusatz

Keine Umlaute.
Keine Leerzeichen.
Verwende möglichst das Objekt des Dokuments, nicht den Empfänger.

Kategorien mit Beschreibung:
{json.dumps(KATEGORIEN_INFO, ensure_ascii=False, indent=2)}

Originaldatei:
{dateiname}
"""
    content = [{"type": "input_text", "text": prompt}]

    for bildpfad in bildpfade:
        with open(bildpfad, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        content.append({
            "type": "input_image",
            "image_url": f"data:image/png;base64,{b64}"
        })

    r = client.responses.create(
        model="gpt-4.1",
        input=[{"role": "user", "content": content}],
        text={
            "format": {
                "type": "json_schema",
                "name": "analyse",
                "schema": SCHEMA,
                "strict": True
            }
        }
    )
    return json.loads(r.output_text)

def eindeutiger_pfad(ordner, dateiname):
    basis, endung = os.path.splitext(dateiname)
    ziel = os.path.join(ordner, dateiname)
    nr = 1
    while os.path.exists(ziel):
        ziel = os.path.join(ordner, f"{basis}_{nr}{endung}")
        nr += 1
    return ziel

vorschlaege = []

for root, dirs, files in os.walk(ORDNER):

    if ZIELORDNER in root:
        continue

    for datei in files:

        if not datei.lower().endswith(".pdf"):
            continue

        pfad = os.path.join(root, datei)

        print("\\n" + "=" * 70)
        print("Alt:", pfad)

        try:
            text = pdf_text_lesen(pfad)

            if len(text.strip()) >= 50:
                print("Analyse über Text")
                ergebnis = dokument_analysieren_text(datei, text)
            else:
                print("Scan erkannt -> Vision Analyse")

                bildpfade = pdf_seiten_als_bilder(pfad, max_seiten=3)

                ergebnis = dokument_analysieren_bild(datei, bildpfade)

                for bild in bildpfade:
                    if os.path.exists(bild):
                        os.remove(bild)

            kategorie = ergebnis["category"]
            filename = ergebnis["filename"] + ".pdf"

            zielordner = os.path.join(ORDNER, ZIELORDNER, kategorie)
            os.makedirs(zielordner, exist_ok=True)

            zielpfad = eindeutiger_pfad(zielordner, filename)

            print("Kategorie:", kategorie)
            print("Neu:", zielpfad)

            shutil.move(pfad, zielpfad)

            vorschlaege.append([pfad, zielpfad, kategorie])

        except Exception as e:
            print(f"Fehler bei {datei}: {e}")

os.makedirs(os.path.join(ORDNER, ZIELORDNER), exist_ok=True)

with open(
    os.path.join(ORDNER, ZIELORDNER, "umbenennung.csv"),
    "w",
    newline="",
    encoding="utf-8"
) as f:
    writer = csv.writer(f)
    writer.writerow(["Alter Pfad", "Neuer Pfad", "Kategorie"])
    writer.writerows(vorschlaege)

print("\\nFertig. Sortiert in:", os.path.join(ORDNER, ZIELORDNER))
