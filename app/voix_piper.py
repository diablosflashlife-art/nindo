"""La voix réaliste — Piper, une synthèse neuronale qui tourne sur le PC.

RETOUR DE PARTIE : « le TTS est mauvais, une voix vraiment robotisée ». La
lecture passait par le synthétiseur du navigateur, branché sur les voix du
système. Piper est libre, gratuit, hors ligne, et nettement plus naturel ;
sur un processeur ordinaire il produit l'audio six fois plus vite qu'on ne
l'écoute (mesuré : 15 s de récit en 2,6 s).

LA VOIX EST UN FICHIER DE ~60 Mo. Elle n'est pas livrée avec l'application :
le lanceur propose de l'installer, une fois, dans le dossier de données.
Sans elle, la lecture retombe sur la voix du navigateur — le jeu ne dépend
jamais de la voix.

CE MODULE NE LÈVE PAS. Une voix qui manque, un modèle illisible, une phrase
impossible à dire : on rend None et le navigateur prend le relais.
"""
from __future__ import annotations

import hashlib
import io
import threading
import wave
from pathlib import Path

from app.config import DONNEES

VOIX = "fr_FR-tom-medium"
DOSSIER = DONNEES / "voix"
CACHE = DOSSIER / "cache"

_charge = None
_verrou = threading.Lock()
_verrou_synthese = threading.Lock()
_installation = {"etat": "", "erreur": ""}      # "" | en_cours | fini | echec


def modele() -> Path:
    return DOSSIER / f"{VOIX}.onnx"


def disponible() -> bool:
    if not modele().is_file() or not (DOSSIER / f"{VOIX}.onnx.json").is_file():
        return False
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


def etat() -> dict:
    return {"piper": disponible(), "voix": VOIX, **_installation}


def _voix():
    global _charge
    with _verrou:
        if _charge is None:
            from piper import PiperVoice
            _charge = PiperVoice.load(str(modele()))
        return _charge


def dire(texte: str, vitesse: float = 1.0, volume: float = 1.0) -> bytes | None:
    """Le WAV de cette phrase, ou None. Mis en cache : un tour relu ne se
    resynthétise pas."""
    texte = " ".join((texte or "").split())[:1200]
    if not texte or not disponible():
        return None
    vitesse = max(0.6, min(1.6, float(vitesse or 1.0)))
    volume = max(0.2, min(1.2, float(volume or 1.0)))
    cle = hashlib.sha1(f"{VOIX}|{vitesse:.2f}|{volume:.2f}|{texte}".encode()).hexdigest()
    fichier = CACHE / f"{cle}.wav"
    if fichier.is_file():
        return fichier.read_bytes()
    try:
        from piper import SynthesisConfig
        tampon = io.BytesIO()
        # UNE PHRASE À LA FOIS. Le navigateur prépare la phrase suivante pendant
        # que la courante parle : deux synthèses simultanées sur la même voix
        # échouaient par moments, et le navigateur retombait sur sa voix
        # robotique au milieu du récit. Le verrou les met à la file.
        with _verrou_synthese:
            with wave.open(tampon, "wb") as w:
                _voix().synthesize_wav(texte, w, syn_config=SynthesisConfig(
                    length_scale=1.0 / vitesse, volume=volume))
        donnees = tampon.getvalue()
    except Exception as exc:  # noqa: BLE001 — la voix n'est jamais une raison d'échouer
        _installation["synthese"] = f"{exc.__class__.__name__}: {exc}"[:200]
        return None
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        fichier.write_bytes(donnees)
    except OSError:
        pass
    return donnees


def installer() -> None:
    """Télécharge la voix en fond (≈ 60 Mo, depuis le dépôt officiel des voix
    Piper). Le lanceur suit l'avancement par `etat()`."""
    if _installation["etat"] == "en_cours" or disponible():
        return

    def travail():
        _installation.update(etat="en_cours", erreur="")
        try:
            from piper.download_voices import download_voice
            DOSSIER.mkdir(parents=True, exist_ok=True)
            download_voice(VOIX, DOSSIER)
            _installation["etat"] = "fini" if disponible() else "echec"
        except Exception as exc:  # noqa: BLE001
            _installation.update(etat="echec", erreur=exc.__class__.__name__)

    threading.Thread(target=travail, daemon=True).start()
