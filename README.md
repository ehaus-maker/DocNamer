# 📄 DocNamer

Ein automatisches Dokumenten-Verwaltungssystem das gescannte PDFs erkennt, per KI analysiert, umbenennt und in Kategorien sortiert.

## Architektur
## Verwendete Pakete

| Paket | Zweck |
|---|---|
| `anthropic` | Claude API (Haiku + Sonnet) |
| `pymupdf (fitz)` | PDF lesen & als Bild rendern |
| `ocrmypdf` | OCR-Vorschaltung |
| `tesseract` | OCR-Engine (deu+eng) |
| `watchdog` | Ordner überwachen |
| `rumps` | macOS Menüleisten-App |
| `pyobjc` | macOS Framework |

## KI-Modelle

| Modell | Einsatz |
|---|---|
| `claude-haiku-4-5` | Textanalyse (schnell, günstig) |
| `claude-sonnet-4-6` | Vision-Analyse (Scans ohne Text) |

## Projektdateien

| Datei | Beschreibung |
|---|---|
| `docnamer_watcher.py` | Kern – OCR, Analyse, Sortierung |
| `docnamer_menubar.py` | macOS Menüleisten-App |
| `docnamer_korrektur.py` | CLI für manuelle Korrekturen |
| `docnamer.command` | Alter Shell-Launcher (legacy) |
| `kategorien.json` | Kategorie-Definitionen |
| `korrekturen.json` | Lernbasis (Few-Shot) |
| `hashes.json` | Duplikaterkennung |
| `umbenennung.csv` | Log aller Umbenennungen |
| `DocNamer.app` | macOS App-Bundle |

## Features

### Dokumentenverarbeitung
- ✅ Automatische OCR-Vorschaltung (ocrmypdf)
- ✅ Texterkennung Deutsch + Englisch
- ✅ KI-Analyse via Text oder Vision
- ✅ Automatisches Umbenennen (YYYY-MM-DD_Objekt_Typ)
- ✅ Kategorisierung in Ordnerstruktur
- ✅ Duplikaterkennung via SHA256-Hash
- ✅ iCloud-Download-Erzwingung

### Lernmechanismus
- ✅ Manuelle Korrekturen erfassbar
- ✅ Few-Shot-Learning in jedem Prompt
- ✅ Korrekturen-CLI mit Liste und Löschfunktion

### GUI
- ✅ macOS Menüleisten-App (rumps)
- ✅ Einmal-Scan per Klick
- ✅ Watcher starten/stoppen
- ✅ Korrektur direkt aus Menü
- ✅ Log und Sortiert-Ordner öffnen
- ✅ Weißes Template-Icon

## Installation

```bash
brew install ocrmypdf tesseract tesseract-lang
/opt/homebrew/bin/python3 -m pip install anthropic pymupdf watchdog rumps --break-system-packages
```

## Start

```bash
# Menüleisten-App
/opt/homebrew/bin/python3 docnamer_menubar.py

# Einmal-Scan direkt
/opt/homebrew/bin/python3 docnamer_watcher.py --einmal

# Korrektur erfassen
/opt/homebrew/bin/python3 docnamer_korrektur.py
```

## Geplant

- 🔜 Ollama statt Claude API (komplett lokal)
- 🔜 Stempelerkennung (fortlaufende Nummern)
- 🔜 Mac Mini M4 24GB als Server
- 🔜 Claude Code für Ollama-Umbau
- 🔜 Ubuntu-Version
