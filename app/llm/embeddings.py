"""Le rappel par le SENS, pas par les mots.

LE PROBLÈME. `rappeler_faits()` classait la mémoire longue par recouvrement
lexical. Le joueur écrit « ce que j'ai juré à Tetsuo » ; le fait enregistré dit
« Kaito a promis au vieux forgeron de ne jamais révéler l'origine de la lame ».
Aucun mot commun au-delà du prénom. Le fait ne remontait pas, le maître du jeu
ne s'en souvenait pas, et la campagne perdait exactement ce qui fait sa valeur.

LA MÉTHODE. Chaque fait reçoit un vecteur à l'écriture. Au rappel, on compare
le vecteur de la question à ceux des faits : deux phrases qui parlent de la
même chose se ressemblent, même sans partager un mot.

TROIS PRINCIPES, ET ILS COMPTENT PLUS QUE LE MODÈLE CHOISI.

1. LE LEXICAL NE DISPARAÎT PAS. Un nom propre, une date, un titre de mission
   se retrouvent mieux par les lettres que par le sens. Les deux scores
   s'additionnent — on ne remplace pas, on complète.

2. ÇA NE DOIT JAMAIS CASSER UN TOUR. Si le modèle d'embedding n'est pas
   installé, si Ollama ne répond pas, si le champ est vide : on retombe
   silencieusement sur le classement lexical d'avant. Une mémoire un peu moins
   fine vaut infiniment mieux qu'une partie interrompue.

3. UNE SEULE REQUÊTE PAR TOUR. Les faits d'un tour sont vectorisés ensemble,
   la question l'est en même temps que le rappel. Deux appels à un modèle de
   270 Mo : quelques millisecondes, invisibles à côté de la narration.

À INSTALLER : `ollama pull nomic-embed-text`. Sans ça, tout fonctionne comme
avant — voir /sante, qui le dit.
"""
from __future__ import annotations

import math
from typing import Protocol

import httpx

from app.config import settings


class Embeddeur(Protocol):
    def vecteurs(self, textes: list[str]) -> list[list[float]]: ...
    @property
    def disponible(self) -> bool: ...


class AbsentEmbeddeur:
    """Ne vectorise rien. C'est le comportement d'avant, et il reste correct."""

    disponible = False

    def vecteurs(self, textes: list[str]) -> list[list[float]]:
        return [[] for _ in textes]


class OllamaEmbeddeur:
    """Vectorise via Ollama, et se tait quand il ne peut pas.

    La disponibilité est sondée UNE fois : sans ce cache, chaque tour paierait
    le délai d'expiration d'un service absent, et le jeu ramerait pour une
    fonction d'agrément.
    """

    def __init__(self) -> None:
        self.base = settings.ollama_base_url.rstrip("/")
        self.modele = settings.embed_model
        self.client = httpx.Client(timeout=20.0)
        self._etat: bool | None = None

    @property
    def disponible(self) -> bool:
        if self._etat is None:
            self._etat = bool(self._appeler(["test"]))
        return self._etat

    def _appeler(self, textes: list[str]) -> list[list[float]]:
        """Deux API selon l'âge d'Ollama : `/api/embed` prend une liste,
        `/api/embeddings` un seul texte. On essaie la récente, puis l'autre."""
        try:
            r = self.client.post(f"{self.base}/api/embed",
                                 json={"model": self.modele, "input": textes})
            if r.status_code == 200:
                vecs = r.json().get("embeddings") or []
                if len(vecs) == len(textes):
                    return vecs
            sortie = []
            for t in textes:
                r = self.client.post(f"{self.base}/api/embeddings",
                                     json={"model": self.modele, "prompt": t})
                r.raise_for_status()
                sortie.append(r.json().get("embedding") or [])
            return sortie if any(sortie) else []
        except Exception:  # noqa: BLE001 — l'absence d'embeddings n'est pas une panne
            return []

    def vecteurs(self, textes: list[str]) -> list[list[float]]:
        if not textes or not self.disponible:
            return [[] for _ in textes]
        vecs = self._appeler(textes)
        return vecs if len(vecs) == len(textes) else [[] for _ in textes]


def cosinus(a: list[float], b: list[float]) -> float:
    """Proximité de sens, entre -1 et 1. Zéro dès qu'un vecteur manque : un
    fait sans vecteur ne doit ni être favorisé ni être puni."""
    if not a or not b or len(a) != len(b):
        return 0.0
    produit = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return produit / (na * nb) if na and nb else 0.0


_INSTANCE: Embeddeur | None = None


def get_embeddeur() -> Embeddeur:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = (OllamaEmbeddeur()
                     if settings.embeddings and (
                         settings.llm_provider == "ollama"
                         or (settings.llm_provider == "en_ligne"
                             and settings.llm_secours == "ollama"))
                     else AbsentEmbeddeur())
    return _INSTANCE


def reset_embeddeur() -> None:
    """Utile aux tests, pour injecter un embeddeur ou rebasculer."""
    global _INSTANCE
    _INSTANCE = None


def poser(embeddeur: Embeddeur) -> None:
    """Injecte un embeddeur — les tests en ont besoin, le jeu jamais."""
    global _INSTANCE
    _INSTANCE = embeddeur
