@echo off
rem Double-clique ce fichier pour lancer Nindo.
rem Il demarre Ollama si besoin, le serveur du jeu, puis ouvre le navigateur.
chcp 65001 >nul
cd /d "%~dp0"
title Nindo

if not exist ".venv\Scripts\python.exe" (
  echo Premiere installation : preparation de Python, une minute...
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
  if not exist ".venv\Scripts\python.exe" (
    echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/
    echo en cochant "Add python.exe to PATH", puis relance ce fichier.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" scripts\lancer.py
if errorlevel 1 pause
