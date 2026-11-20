@echo off
rem Lanzador de la interfaz en Windows: doble clic. Abre scripts\app.py con Python 3.12
cd /d "%~dp0"

rem 1. Lanzador py del instalador de python.org
py -3.12 -c "import tkinter" >nul 2>nul && goto launcher
rem 2. Python del PATH
python -c "import sys, tkinter; sys.exit(sys.version_info[:2] < (3, 12))" >nul 2>nul && goto path
goto missing

:launcher
start "" pyw -3.12 scripts\app.py
exit /b 0

:path
start "" pythonw scripts\app.py
exit /b 0

:missing
echo.
echo No se encontro Python 3.12 con tkinter en este equipo.
echo.
echo Instale Python 3.12 desde https://www.python.org/downloads/ y, en el instalador,
echo marque "Add python.exe to PATH" y deje marcada la opcion "tcl/tk and IDLE".
echo Luego vuelva a abrir Iniciar.bat.
echo.
pause
exit /b 1
