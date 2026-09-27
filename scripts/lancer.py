"""Lancer le jeu en un geste : `Lancer Nindo.bat` appelle ce script.

Il fait, dans l'ordre, ce qu'on tapait à la main avant chaque séance :
1. démarre Ollama s'il en a besoin et qu'il dort ;
2. démarre le serveur du jeu ;
3. dit en clair qui racontera (Mistral, Ollama, ou personne) ;
4. ouvre le navigateur sur l'accueil.

Si le jeu tourne déjà (un double-clic de trop), il ouvre seulement le
navigateur. Fermer la fenêtre arrête le jeu ; les parties sont enregistrées
à chaque tour, rien n'est perdu.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx

RACINE = Path(__file__).resolve().parent.parent
os.chdir(RACINE)
sys.path.insert(0, str(RACINE))

# Premier lancement : la configuration se lit à l'import, .env doit donc
# exister AVANT.
PREMIER = not (RACINE / ".env").is_file() and (RACINE / ".env.example").is_file()
if PREMIER:
    shutil.copy(RACINE / ".env.example", RACINE / ".env")

from app.config import settings  # noqa: E402

HOTE, PORT = "127.0.0.1", int(os.environ.get("PORT", "8000"))
ADRESSE = f"http://localhost:{PORT}"

V, J, R, G, Z = "\033[32m", "\033[33m", "\033[31m", "\033[90m", "\033[0m"


def dire(texte: str = "") -> None:
    print(texte, flush=True)


def repond(url: str, delai: float = 2.0) -> httpx.Response | None:
    try:
        return httpx.get(url, timeout=delai)
    except httpx.HTTPError:
        return None


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------
def ollama_requis() -> bool:
    return settings.llm_provider == "ollama" or (
        settings.llm_provider == "en_ligne" and settings.llm_secours == "ollama")


def trouver_ollama() -> str | None:
    chemin = shutil.which("ollama")
    if chemin:
        return chemin
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(local) if local.is_file() else None


def reveiller_ollama() -> bool:
    base = settings.ollama_base_url.rstrip("/")
    if repond(f"{base}/api/tags"):
        dire(f"  {V}✓{Z} Ollama est déjà éveillé.")
        return True
    exe = trouver_ollama()
    if not exe:
        dire(f"  {J}!{Z} Ollama n'est pas installé : https://ollama.com/download")
        return False
    dire(f"  {G}… Ollama dort, je le réveille.{Z}")
    options = {}
    if os.name == "nt":
        options["creationflags"] = (subprocess.CREATE_NO_WINDOW
                                    | subprocess.DETACHED_PROCESS)
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **options)
    for _ in range(30):
        time.sleep(0.5)
        if repond(f"{base}/api/tags"):
            dire(f"  {V}✓{Z} Ollama est prêt.")
            return True
    dire(f"  {J}!{Z} Ollama ne répond pas. Lance-le à la main, puis relance le jeu.")
    return False


def prechauffer_ollama() -> None:
    """Quand Ollama raconte seul, on charge le modèle en mémoire dès
    maintenant : le premier tour n'attend plus trente secondes. En relais, on
    s'en abstient — la carte graphique reste libre pour le stream tant que
    Mistral raconte."""
    try:
        httpx.post(f"{settings.ollama_base_url.rstrip('/')}/api/generate",
                   json={"model": settings.llm_model, "keep_alive": settings.llm_keep_alive},
                   timeout=180)
    except httpx.HTTPError:
        pass


# --------------------------------------------------------------------------
# Le serveur
# --------------------------------------------------------------------------
def bilan() -> None:
    """Attend que le jeu réponde, dit qui raconte, ouvre le navigateur."""
    for _ in range(120):
        if repond(f"{ADRESSE}/", 1.0):
            break
        time.sleep(0.5)
    else:
        dire(f"{R}Le jeu ne démarre pas. Regarde les messages au-dessus.{Z}")
        return

    r = repond(f"{ADRESSE}/sante", 30.0)
    info = r.json() if r is not None else {}
    dire()
    if settings.llm_provider == "en_ligne":
        en_ligne = info.get("en_ligne", {})
        ok = en_ligne.get("etat") == "joignable"
        dire(f"  {V + '✓' if ok else J + '!'}{Z} Conteur : {en_ligne.get('service', '?')} "
             f"({en_ligne.get('modele', '?')}) — {en_ligne.get('etat', 'inconnu')}")
        sec = info.get("secours_ollama") or {}
        if sec:
            ok2 = sec.get("pret")
            dire(f"  {V + '✓' if ok2 else J + '!'}{Z} Secours : Ollama — "
                 f"{'prêt' if ok2 else sec.get('ollama', 'indisponible')}")
    elif settings.llm_provider == "ollama":
        dire(f"  {V + '✓' if info.get('pret') else J + '!'}{Z} Conteur : Ollama "
             f"({settings.llm_model}) — {'prêt' if info.get('pret') else 'pas prêt'}")
    else:
        dire(f"  {J}!{Z} Mode mock : réponses factices (LLM_PROVIDER dans .env).")
    for action in info.get("action") or []:
        dire(f"    {J}→ {action}{Z}")
    if not info.get("pret", True):
        dire(f"  {R}Aucun conteur n'est prêt : les tours échoueront tant que ce "
             f"n'est pas réglé.{Z}")

    dire(f"\n  {V}Le jeu est ouvert : {ADRESSE}{Z}")
    dire(f"  {G}Garde cette fenêtre ouverte pendant la partie. La fermer arrête le jeu ;")
    dire(f"  chaque tour est déjà enregistré, rien ne se perd.{Z}\n")
    ouvrir()


def ouvrir() -> None:
    if not os.environ.get("SANS_NAVIGATEUR"):   # pour les essais automatiques
        webbrowser.open(ADRESSE)


def main() -> int:
    if os.name == "nt":
        os.system("")  # active les couleurs de la console Windows
    dire(f"\n  {J}忍道  Nindō — le jeu de rôle ninja{Z}\n")

    deja = repond(f"{ADRESSE}/sante", 5.0)
    if deja is not None and deja.status_code == 200 and "fournisseur" in deja.text:
        dire(f"  {V}✓{Z} Le jeu tourne déjà : j'ouvre le navigateur.")
        ouvrir()
        return 0

    if PREMIER:
        dire(f"  {J}!{Z} Premier lancement : .env créé depuis .env.example "
             f"(mode mock). Voir DEMARRER.md pour brancher l'IA.")

    if ollama_requis() and reveiller_ollama() and settings.llm_provider == "ollama":
        threading.Thread(target=prechauffer_ollama, daemon=True).start()

    threading.Thread(target=bilan, daemon=True).start()
    import uvicorn
    try:
        uvicorn.run("app.main:app", host=HOTE, port=PORT, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
