#!/bin/bash
# Lanzador de la interfaz en macOS: doble clic en Finder. Abre scripts/app.py con Python 3.12
cd "$(dirname "$0")" || exit 1

for candidate in python3.12 \
                 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
                 /opt/homebrew/bin/python3.12 \
                 /usr/local/bin/python3.12 \
                 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c "import sys, tkinter; sys.exit(sys.version_info[:2] < (3, 12))" >/dev/null 2>&1; then
        echo "Abriendo la interfaz. Esta ventana se puede minimizar; ciérrela al terminar."
        exec "$candidate" scripts/app.py
    fi
done

echo
echo "No se encontró Python 3.12 con tkinter en este equipo."
echo
echo "Instale Python 3.12 desde https://www.python.org/downloads/macos/ (incluye tkinter)."
echo "Si usa Homebrew: brew install python@3.12 python-tk@3.12"
echo "Luego vuelva a abrir Iniciar.command."
echo
read -r -p "Presione Enter para cerrar..."
exit 1
