"""Les réglages que le joueur change depuis le lanceur, sans ouvrir de fichier.

La clé Mistral est le seul réglage qu'un joueur ne peut pas éviter : sans
elle, c'est Ollama qui raconte. On l'écrit dans SON fichier de réglages
(%APPDATA%\\Nindo\\.env pour l'application, le .env du projet depuis le code
source), et on la prend en compte immédiatement, sans redémarrer.
"""
from __future__ import annotations

import re

from app.config import ENV_FICHIER, settings


def _ecrire(cle: str, valeur: str) -> None:
    lignes = ENV_FICHIER.read_text(encoding="utf-8").splitlines() \
        if ENV_FICHIER.exists() else []
    motif = re.compile(rf"^\s*{re.escape(cle)}\s*=")
    for i, l in enumerate(lignes):
        if motif.match(l):
            lignes[i] = f"{cle}={valeur}"
            break
    else:
        lignes.append(f"{cle}={valeur}")
    ENV_FICHIER.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def enregistrer_cle_mistral(cle: str) -> None:
    """Enregistre la clé et bascule le jeu sur Mistral, Ollama en secours."""
    cle = (cle or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", cle):
        raise ValueError("Cette clé ne ressemble pas à une clé Mistral.")
    _ecrire("EN_LIGNE_CLE", cle)
    _ecrire("LLM_PROVIDER", "en_ligne")
    settings.en_ligne_cle = cle
    settings.llm_provider = "en_ligne"
    from app.llm import embeddings
    from app.llm.provider import reset_llm
    reset_llm()
    embeddings.reset_embeddeur()


def cle_masquee() -> str:
    cle = settings.en_ligne_cle or ""
    return f"…{cle[-4:]}" if len(cle) >= 8 else ""
