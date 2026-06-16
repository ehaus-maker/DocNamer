#!/bin/bash
# DocNamer Fernsteuerung – wird vom MacBook per SSH aufgerufen und verwaltet
# den Watcher-Prozess auf dem Mac Mini. Bewusst manuell (kein Autostart),
# damit zwischen Einmalscan und Dauer-Watcher umgeschaltet werden kann.
#
#   docnamer_ctl.sh einmal   – einmaliger Scan, Ausgabe wird zurückgegeben
#   docnamer_ctl.sh start    – Dauer-Watcher im Hintergrund starten
#   docnamer_ctl.sh stop     – Dauer-Watcher stoppen
#   docnamer_ctl.sh status   – läuft der Watcher?
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="/opt/homebrew/bin/python3"
WATCHER="$SCRIPT_DIR/docnamer_watcher.py"
BASIS="$HOME/Documents/DocNamer"
PIDFILE="$BASIS/watcher.pid"
LOG="$BASIS/docnamer.log"

mkdir -p "$BASIS"

laeuft() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-status}" in
    einmal)
        echo "→ Einmal-Scan startet..."
        "$PYTHON" "$WATCHER" --einmal
        echo "→ Einmal-Scan fertig."
        ;;
    start)
        if laeuft; then
            echo "Watcher läuft bereits (PID $(cat "$PIDFILE"))."
            exit 0
        fi
        nohup "$PYTHON" "$WATCHER" >> "$LOG" 2>&1 &
        echo $! > "$PIDFILE"
        echo "Watcher gestartet (PID $(cat "$PIDFILE"))."
        ;;
    stop)
        if laeuft; then
            kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
            echo "Watcher gestoppt."
        else
            echo "Watcher läuft nicht."
            rm -f "$PIDFILE" 2>/dev/null || true
        fi
        ;;
    status)
        if laeuft; then
            echo "Watcher läuft (PID $(cat "$PIDFILE"))."
        else
            echo "Watcher gestoppt."
        fi
        ;;
    *)
        echo "Verwendung: docnamer_ctl.sh {einmal|start|stop|status}"
        exit 1
        ;;
esac
