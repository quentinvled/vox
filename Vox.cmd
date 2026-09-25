@echo off
rem Lance Vox sans fenetre console.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m vox
) else (
    echo Environnement introuvable. Lance d'abord : uv sync
    pause
)
