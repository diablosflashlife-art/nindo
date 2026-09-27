"""Les mises à jour de Nindō, par GitHub.

LE PRINCIPE. Publier une version, c'est pousser l'étiquette `vX.Y.Z` : GitHub
fabrique alors Nindō.exe et l'attache à la version (voir
.github/workflows/publier.yml). Au lancement, le lanceur demande à GitHub la
dernière version publiée ; si elle est plus récente, il la propose.

INSTALLER SANS RIEN CASSER. L'application empaquetée ne peut pas se remplacer
elle-même pendant qu'elle tourne. On télécharge donc l'archive, on la déballe à
côté, on écrit un petit script qui attend la fermeture de Nindō, recopie les
fichiers et relance. Les données du joueur (parties, clé) vivent dans
%APPDATA%\\Nindo et ne sont jamais touchées.

PRUDENCE. On ne télécharge que depuis les versions du dépôt déclaré dans
app/version.py, et rien ne s'installe sans un clic du joueur.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx

from app.config import DONNEES, EMPAQUETE, ROOT
from app.version import ARCHIVE, DEPOT, VERSION

_CACHE: dict = {"quand": 0.0, "versions": None}
DUREE_CACHE = 600          # secondes : GitHub limite les requêtes anonymes


def _tuple(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3]) or (0,)


def plus_recente(a: str, b: str) -> bool:
    """La version `a` est-elle strictement plus récente que `b` ?"""
    return _tuple(a) > _tuple(b)


def _en_html(ligne: str) -> str:
    """Une puce de NOUVEAUTES.md en HTML sûr : échappée, puis **gras**."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(ligne))


# --------------------------------------------------------------------------
# Les nouveautés
# --------------------------------------------------------------------------
def nouveautes_locales() -> list[dict]:
    """NOUVEAUTES.md, découpé par version. Sert hors ligne, et avant que le
    dépôt n'existe."""
    fichier = ROOT / "NOUVEAUTES.md"
    if not fichier.is_file():
        return []
    versions, courante = [], None
    for ligne in fichier.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(\S+)\s*(?:—|-)\s*(.*)$", ligne)
        if m:
            courante = {"version": m.group(1), "nom": m.group(2).strip(),
                        "points": [], "date": ""}
            versions.append(courante)
        elif courante and ligne.strip().startswith("- "):
            courante["points"].append(_en_html(ligne.strip()[2:]))
    return versions


def versions_distantes() -> list[dict] | None:
    """Les dernières versions publiées sur GitHub, ou None si on ne peut pas
    savoir (pas de dépôt, pas de réseau). Jamais une exception."""
    if not DEPOT:
        return None
    if _CACHE["versions"] is not None and time.time() - _CACHE["quand"] < DUREE_CACHE:
        return _CACHE["versions"]
    try:
        r = httpx.get(f"https://api.github.com/repos/{DEPOT}/releases",
                      params={"per_page": 6}, timeout=6.0,
                      headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    versions = []
    for rel in r.json():
        if rel.get("draft") or rel.get("prerelease"):
            continue
        archive = next((a for a in rel.get("assets") or [] if a.get("name") == ARCHIVE), None)
        points = [_en_html(l.strip()[2:]) for l in (rel.get("body") or "").splitlines()
                  if l.strip().startswith("- ")]
        versions.append({
            "version": (rel.get("tag_name") or "").lstrip("v"),
            "nom": re.sub(r"^v?[\d.]+\s*(—|-)\s*", "", rel.get("name") or ""),
            "date": (rel.get("published_at") or "")[:10],
            "points": points,
            "archive": archive.get("browser_download_url") if archive else "",
            "taille": archive.get("size", 0) if archive else 0,
        })
    _CACHE.update(quand=time.time(), versions=versions)
    return versions


def etat() -> dict:
    """Tout ce que le lanceur affiche sur la version."""
    distantes = versions_distantes()
    derniere = distantes[0] if distantes else None
    return {
        "version": VERSION,
        "empaquete": EMPAQUETE,
        "depot": DEPOT,
        "en_ligne": distantes is not None,
        "derniere": derniere,
        "maj": bool(derniere and derniere.get("archive")
                    and plus_recente(derniere["version"], VERSION)),
        "nouveautes": distantes or nouveautes_locales(),
    }


# --------------------------------------------------------------------------
# L'installation
# --------------------------------------------------------------------------
class MajImpossible(Exception):
    """Message à montrer au joueur tel quel."""


def lancer_en_fond(commande: list[str]) -> None:
    """Lance un programme qui doit SURVIVRE à Nindō, sans fenêtre.

    MESURÉ, PAS SUPPOSÉ. DETACHED_PROCESS — le réflexe pour « détacher » — empêche
    PowerShell de démarrer : les versions 1.0.0 et 1.0.1 téléchargeaient la
    mise à jour puis ne l'installaient jamais. Seule cette combinaison lance
    réellement le script : pas de fenêtre, un groupe de processus à part, et
    les trois flux standard fermés. On tente en plus de sortir de l'éventuel
    « job » Windows du parent, qui tuerait l'enfant à sa fermeture.
    """
    base = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    flux = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL}
    try:
        subprocess.Popen(commande, creationflags=base
                         | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **flux)
    except OSError:
        subprocess.Popen(commande, creationflags=base, **flux)


def _url_sure(url: str) -> bool:
    return bool(DEPOT) and url.startswith(f"https://github.com/{DEPOT}/releases/download/")


def installer() -> str:
    """Télécharge et prépare la nouvelle version, puis lance le script qui la
    mettra en place dès que Nindō sera fermé. Rend la version installée."""
    if not EMPAQUETE:
        raise MajImpossible("Depuis le code source, mets à jour avec Git (git pull).")
    info = etat()
    derniere = info["derniere"]
    if not info["maj"] or not derniere:
        raise MajImpossible("Tu as déjà la dernière version.")
    url = derniere["archive"]
    if not _url_sure(url):
        raise MajImpossible("Adresse de mise à jour inattendue : installation refusée.")

    dossier = DONNEES / "maj"
    if dossier.exists():
        shutil.rmtree(dossier, ignore_errors=True)
    dossier.mkdir(parents=True)
    archive = dossier / ARCHIVE
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
            r.raise_for_status()
            with archive.open("wb") as f:
                for morceau in r.iter_bytes(1 << 16):
                    f.write(morceau)
    except httpx.HTTPError as exc:
        raise MajImpossible(f"Téléchargement interrompu ({exc.__class__.__name__}).") from exc

    deballe = dossier / "nouvelle"
    with zipfile.ZipFile(archive) as z:
        # Aucun chemin ne doit sortir du dossier de déballage.
        for nom in z.namelist():
            cible = (deballe / nom).resolve()
            if not str(cible).startswith(str(deballe.resolve())):
                raise MajImpossible("Archive de mise à jour invalide.")
        z.extractall(deballe)
    source = deballe / "Nindo" if (deballe / "Nindo").is_dir() else deballe
    if not (source / "Nindo.exe").is_file():
        raise MajImpossible("Archive de mise à jour incomplète.")

    installation = Path(sys.executable).resolve().parent
    script = dossier / "appliquer.ps1"
    script.write_text(
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        f"Start-Transcript -Path \"{dossier / 'journal.txt'}\" | Out-Null\n"
        f"Wait-Process -Id {os.getpid()} -Timeout 60\n"
        "Start-Sleep -Milliseconds 800\n"
        f"robocopy \"{source}\" \"{installation}\" /E /IS /IT /R:5 /W:1 /NFL /NDL /NJH /NJS\n"
        f"Start-Process \"{installation / 'Nindo.exe'}\"\n"
        "Stop-Transcript | Out-Null\n",
        encoding="utf-8-sig")
    lancer_en_fond(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script)])
    # On laisse la réponse partir, puis on s'efface : le script prend le relais.
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return derniere["version"]
