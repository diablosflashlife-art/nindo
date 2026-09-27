"""Nindō, l'application.

Ce fichier est le point d'entrée de Nindō.exe (voir nindo.spec) — et se lance
aussi depuis le code source : `python nindo.py`.

Il fait trois choses :
1. réveille Ollama s'il sert de secours et qu'il dort ;
2. démarre le serveur du jeu, en local, sur un port libre ;
3. ouvre une FENÊTRE Nindō (et non un onglet de navigateur) sur le lanceur.

Fermer la fenêtre ferme le jeu. Chaque tour est enregistré à mesure : rien ne
se perd.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx


def _port_libre(prefere: int = 47823) -> int:
    for port in (prefere, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("aucun port libre")


def _reveiller_ollama(settings) -> None:
    """En fond : la fenêtre n'attend pas Ollama pour s'ouvrir."""
    if not (settings.llm_provider == "ollama" or
            (settings.llm_provider == "en_ligne" and settings.llm_secours == "ollama")):
        return
    base = settings.ollama_base_url.rstrip("/")
    try:
        httpx.get(f"{base}/api/tags", timeout=1.5)
        return
    except httpx.HTTPError:
        pass
    exe = shutil.which("ollama") or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    if Path(exe).is_file():
        from app.maj import lancer_en_fond
        lancer_en_fond([exe, "serve"])


def main() -> int:
    # Une application sans console n'a ni sortie ni erreur standard : ce sont
    # des `None`, et le premier module qui y écrit — uvicorn configurant ses
    # journaux — fait tomber l'application avant la fenêtre. On les redirige
    # vers le néant.
    for flux in ("stdout", "stderr"):
        if getattr(sys, flux) is None:
            setattr(sys, flux, open(os.devnull, "w", encoding="utf-8"))

    # Import ici : la configuration crée le dossier de données au chargement.
    import uvicorn

    from app.config import settings
    from app.main import app

    port = _port_libre()
    adresse = f"http://127.0.0.1:{port}"
    serveur = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                            log_level="warning", log_config=None))
    threading.Thread(target=serveur.run, daemon=True).start()
    threading.Thread(target=_reveiller_ollama, args=(settings,), daemon=True).start()

    for _ in range(200):
        try:
            httpx.get(f"{adresse}/static/favicon.svg", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.05)

    try:
        import webview
    except ImportError:
        # Sans pywebview (depuis le code source), on retombe sur le navigateur.
        import webbrowser
        webbrowser.open(f"{adresse}/lanceur")
        print(f"Nindō tourne sur {adresse} — Ctrl+C pour arrêter.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    webview.create_window("Nindō", f"{adresse}/lanceur", width=1360, height=880,
                          min_size=(980, 640), background_color="#0b0d12",
                          text_select=True)
    webview.start(private_mode=False)
    serveur.should_exit = True
    return 0


def _journal_erreur() -> None:
    """L'application n'a pas de console : une erreur au démarrage disparaîtrait
    sans trace. On l'écrit dans le dossier de données, où un joueur peut la
    retrouver et l'envoyer."""
    import traceback
    try:
        from app.config import DONNEES
        dossier = DONNEES
    except Exception:  # noqa: BLE001 — la configuration elle-même a pu échouer
        dossier = Path(os.environ.get("APPDATA") or Path.home()) / "Nindo"
    dossier.mkdir(parents=True, exist_ok=True)
    with (dossier / "nindo-erreur.log").open("a", encoding="utf-8") as f:
        f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        traceback.print_exc(file=f)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        _journal_erreur()
        raise
