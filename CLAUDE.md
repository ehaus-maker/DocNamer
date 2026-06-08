# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

DocNamer watches an iCloud Scanner-Pro folder for new PDFs, classifies each one via Claude, then renames and files it into a category folder under `~/Documents/DocNamer/_Sortiert/`. Code, comments, log output, and user-facing strings are in **German** — keep new contributions in German to match.

## Run / Develop

The project has no build step, no test suite, no virtualenv checked in. Everything runs against system Python at `/opt/homebrew/bin/python3` and assumes `ANTHROPIC_API_KEY` is set in the environment.

```bash
# One-shot scan of the default Scanner-Pro folder
/opt/homebrew/bin/python3 docnamer_watcher.py --einmal

# Scan a specific folder once
/opt/homebrew/bin/python3 docnamer_watcher.py /path/to/folder --einmal

# Run as a long-lived watcher (default mode, no flag)
/opt/homebrew/bin/python3 docnamer_watcher.py

# Menubar app (wraps the watcher in subprocesses)
/opt/homebrew/bin/python3 docnamer_menubar.py

# Manage the correction database
/opt/homebrew/bin/python3 docnamer_korrektur.py            # interactive
/opt/homebrew/bin/python3 docnamer_korrektur.py --liste
/opt/homebrew/bin/python3 docnamer_korrektur.py --loesche <dateiname>
```

System dependencies are listed in **`Brewfile`** (`ocrmypdf`, `tesseract`, `tesseract-lang`). Python deps are listed in **`requirements.txt`** (`anthropic`, `pymupdf`, `watchdog`, `rumps`). **`DocNamer Installieren.command`** reads both files automatically — add new dependencies there, not hard-coded in the installer.

## Architecture

Three Python entry points share state through files on disk; there is no shared module — each script is standalone and re-implements helpers like `kategorien_flatten` independently.

**`docnamer_watcher.py`** is the core. It runs in two modes (`--einmal` vs. watcher) but the per-file pipeline is identical:

1. `icloud_download_erzwingen` — detect `.NAME.icloud` placeholders and force `brctl download`, then poll up to `ICLOUD_TIMEOUT` seconds for the real file.
2. Wait `STABILISIERUNGS_SECS` until the file size stops changing (PDFs from the scanner stream in over iCloud).
3. Perceptual dHash (first page rendered at 9×8 px) compared against `hashes.json` with Hamming-distance ≤ 15 → duplicates go to `_Duplikate/`. Fallback to SHA-256 for text-only PDFs without embedded images.
4. Extract text with `fitz` (first 5 pages, 20k chars). **Branching rule:** if extracted text ≥ 50 chars → `claude-haiku-4-5-20251001` (text path); otherwise render up to 3 pages at 2× zoom and send to `claude-sonnet-4-6` as base64 PNGs (vision path). The model IDs are hard-coded — if you change them, update the README table too.
5. Both prompts demand a strict `{"category": "...", "filename": "..."}` JSON response. `parse_antwort` strips a possible ```json fence; `validiere_ergebnis` rejects categories that aren't in the flattened category list.
6. On success, move into `_Sortiert/[<quellordner>/]<kategorie>/<filename>.pdf`, append to `umbenennung.csv`, record the hash. On failure, move to `_Fehler/`.
7. After each successful move, `leere_ordner_archivieren` sweeps any now-empty subfolders of the watched directory into `_Erledigt/`.
8. On startup, `fehler_zurueckholen` moves everything from `_Fehler/` back into the watched folder for a retry.

**Categories live in `kategorien.json`** as a (possibly nested) tree. `kategorien_flatten` walks the tree and emits slash-paths (e.g. `Anlagen und Beteiligungen/DLF`). A subtree is treated as a leaf category once it has a `beschreibung` key — that's the recursion's terminating condition, so any new nested category MUST include `beschreibung` at the level meant to be selectable.

**Output tree (created on first run, lives outside the repo):**
```
~/Documents/DocNamer/
  _Sortiert/      target tree, mirrors kategorien.json
  _Fehler/        failed analyses (retried on next startup)
  _Duplikate/     files whose hash is already in hashes.json
  _Erledigt/      empty source subfolders archived after sweep
  docnamer.log, umbenennung.csv, hashes.json
```

**`docnamer_menubar.py`** is a `rumps` menu-bar wrapper that launches `docnamer_watcher.py` as a subprocess (one-shot or persistent). It owns a `self.watcher_prozess` handle for stop/start. The "documents processed" count in the post-scan notification is computed by counting occurrences of `"Kategorie"` in stdout — fragile, so don't change that log string lightly. The icon path is patched to a relative path by the installer.

**`docnamer_korrektur.py`** edits `korrekturen.json`, an append-only list of `{original_filename, ocr_text_snippet, ki_kategorie, korrekte_kategorie, korrekter_filename}` records. **Important caveat:** despite what the README implies, `docnamer_watcher.py` does **not** currently read `korrekturen.json` — corrections are captured but not fed back into the prompt as few-shot examples. Wiring this in is an obvious next feature.

**`DocNamer.app`** is a minimal `.app` bundle whose `Contents/MacOS/docnamer_launcher` is a shell script that runs `docnamer.command`. The `.command` file is the legacy AppleScript-dialog launcher — the menubar app is now the primary entry point.

**`DocNamer Installieren.command`** is the clickable installer for new Macs. It reads `Brewfile` and `requirements.txt` for dependencies, asks for the API key via GUI dialogs, and stores it in `~/.docnamer_config`.

**`com.docnamer.watcher.plist`** is a launchd template (not installed) with placeholder paths. It documents the optional "run as a launch agent" path; treat it as a config sample, not active code.

**`docnamer_watcher.py`** reads `ANTHROPIC_API_KEY` from the environment if set, otherwise from `~/.docnamer_config`.

## Conventions

- File-naming output format is enforced by the prompt, not validated in code: `YYYY-MM-DD_Hauptobjekt_Dokumenttyp_Zusatz` with no umlauts and no spaces. The "Hauptobjekt" is the document's subject, **not** the recipient — this distinction is explicit in the vision prompt and matters for things like insurance documents.
- The watcher avoids re-entering its own output tree by `realpath`-checking every event against `ZIELORDNER`/`FEHLERORDNER`/`DUPLIKAT_ORDNER`/`ERLEDIGT_BASIS` — preserve this when adding new output directories.
- Scanner Pro syncs files into per-day subfolders; `quellordner_name` preserves that subfolder name as an extra prefix in the target path (`_Sortiert/<scanner-subfolder>/<category>/...`). Removing this would silently flatten user-organized batches.
