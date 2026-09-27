"""La lecture à voix haute — découpage, attribution, prononciation.

CE QUI EXISTAIT DÉJÀ, ET DORMAIT. Le champ `Turn.segments`, le schéma
`SEGMENTS`, le prompt `SEGMENTEUR`, `Character.voix_ref`, les `prononciation`
du lore et le bloc `voix:` du ruleset : tout l'échafaudage était posé, et rien
ne lisait quoi que ce soit.

POURQUOI PAS LE MODÈLE. Le prompt `SEGMENTEUR` demandait au modèle de découper
la narration par locuteur et par émotion. C'était un appel de plus par tour —
plusieurs secondes — pour une tâche que le français rend déterministe : un
dialogue est entre guillemets, et le nom du locuteur est dans la phrase qui
l'entoure. Le moteur décide, le modèle habille : ici le moteur suffit.

POURQUOI PAS UN MOTEUR TTS EN PYTHON. Le navigateur en embarque un, branché
sur les voix du système, hors ligne et sans rien à installer. Une dépendance
Python de plus et un modèle vocal à télécharger auraient coûté davantage que
ce qu'ils apportaient. Ce module produit donc un PLAN DE LECTURE — quoi dire,
avec quelle hauteur, à quelle vitesse — et le navigateur le joue.

LA PRONONCIATION SE RÈGLE AVANT LE MOTEUR. Aucun synthétiseur francophone ne
sait lire « Mangekyô Sharingan » ni « Konohagakure ». Le lore porte déjà les
réécritures ; on les applique au texte lu, jamais au texte affiché.
"""
from __future__ import annotations

import re

from sqlmodel import Session, select

from app.models import Campaign, Character, Turn
from app.rules.engine import Ruleset

# Un dialogue français vit entre guillemets. Le prompt du narrateur les impose,
# et c'est ce qui rend le découpage fiable sans appeler un modèle.
DIALOGUE = re.compile(r"«\s*(.+?)\s*»", re.DOTALL)

# Ce qui, dans la phrase autour, désigne celui qui parle.
VERBES = ("dit", "répond", "lance", "murmure", "souffle", "crie", "grogne",
          "demande", "reprend", "ajoute", "coupe", "lâche", "articule",
          "glisse", "siffle", "rétorque", "observe", "conclut")

# --------------------------------------------------------------------------
# LE TON. `SEGMENTS.emotion` était déclaré dans le schéma et rempli par
# personne : une réplique criée et une réplique murmurée se lisaient
# exactement pareil, ce qui est le plus sûr moyen de rendre une voix de
# synthèse insupportable.
#
# Trois signaux, du plus fiable au moins fiable :
#   1. L'INCISE. « … », murmure-t-il. Le verbe de parole dit le ton, et le
#      français le place juste après la réplique. C'est la source la plus
#      sûre, parce qu'elle est explicite.
#   2. LA PONCTUATION. Un point d'exclamation, des capitales, trois points de
#      suspension : le narrateur les écrit déjà, il suffit de les lire.
#   3. LE REGISTRE DE LA SCÈNE, pour la narration. Il est choisi et stocké à
#      chaque tour depuis rythme.py, et ne servait qu'à écrire la consigne.
#
# Rien n'est deviné : à défaut de signal, on rend « neutre ».
# --------------------------------------------------------------------------
TONS_PAR_VERBE: dict[str, tuple[str, ...]] = {
    "cri": ("crie", "hurle", "rugit", "s'exclame", "beugle", "tonne",
            "vocifère", "s'écrie"),
    "murmure": ("murmure", "chuchote", "souffle", "glisse", "confie",
                "susurre"),
    "menace": ("gronde", "siffle", "menace", "crache", "assène", "avertit",
               "cingle"),
    "colere": ("aboie", "tranche", "coupe", "réplique", "rétorque", "jette",
               "s'emporte"),
    "hesitation": ("bredouille", "balbutie", "hésite", "bafouille",
                   "marmonne", "ânonne"),
    "lassitude": ("soupire", "lâche", "concède", "abandonne", "murmurait"),
    "rire": ("rit", "ricane", "pouffe", "s'esclaffe", "raille", "glousse"),
    "douleur": ("gémit", "halète", "râle", "grimace", "geint", "suffoque"),
    "question": ("demande", "interroge", "s'enquiert"),
}

# Deux mots en capitales suffisent à faire un cri ; un seul peut être un nom
# propre mal saisi ou un sigle.
MAJUSCULES = re.compile(r"\b[A-ZÀ-Þ]{3,}\b")


def segmenter(texte: str) -> list[dict]:
    """Découpe une narration en segments de narration et de dialogue.

    Rend toujours au moins un segment : un texte sans guillemets est une
    narration d'un seul tenant, ce qui est le cas le plus fréquent.
    """
    segments: list[dict] = []
    position = 0
    for m in DIALOGUE.finditer(texte or ""):
        avant = texte[position:m.start()].strip()
        if avant:
            segments.append({"type": "narration", "texte": avant})
        segments.append({"type": "dialogue", "texte": m.group(1).strip()})
        position = m.end()
    reste = (texte or "")[position:].strip()
    if reste:
        segments.append({"type": "narration", "texte": reste})
    return segments or [{"type": "narration", "texte": (texte or "").strip()}]


def attribuer(segments: list[dict], noms: list[str]) -> list[dict]:
    """Cherche qui parle, dans ce qui entoure chaque dialogue.

    On regarde D'ABORD après — « … », dit Hiroshi — puis avant, parce que le
    français place le plus souvent l'incise derrière la réplique. À défaut, on
    reprend le dernier locuteur connu : deux répliques qui se suivent sont
    presque toujours un échange entre les deux mêmes personnes.
    """
    dernier = ""
    for i, seg in enumerate(segments):
        if seg["type"] != "dialogue":
            continue
        voisins = []
        if i + 1 < len(segments):
            voisins.append(segments[i + 1]["texte"][:140])
        if i > 0:
            voisins.append(segments[i - 1]["texte"][-140:])

        trouve = ""
        for v in voisins:
            trouve = _nom_cite(v, noms)
            if trouve:
                break
        seg["locuteur"] = trouve or dernier
        if trouve:
            dernier = trouve
    return segments


def _nom_cite(fragment: str, noms: list[str]) -> str:
    """Le personnage nommé dans ce fragment, de préférence près d'un verbe de
    parole. Les noms les plus longs d'abord : « Rika Hyûga » avant « Rika »."""
    bas = fragment.lower()
    candidats = sorted(noms, key=len, reverse=True)
    proches = [n for n in candidats
               if n.lower() in bas
               and any(v in bas for v in VERBES)]
    if proches:
        return proches[0]
    return next((n for n in candidats if n.lower() in bas), "")


def archetype(rs: Ruleset, perso: Character | None) -> dict:
    """Le grain de voix d'un personnage, d'après le ruleset.

    L'ordre de déclaration fait la règle : le premier archétype dont TOUTES
    les contraintes sont satisfaites l'emporte. C'est écrit dans le YAML, et
    c'est ce qui permet d'en ajouter un sans toucher à ce code.
    """
    cfg = rs.data.get("voix", {}).get("archetypes", {})
    defaut = {"pitch": 1.0, "vitesse": 1.0, "pauses": 1.0}
    if perso is None:
        return defaut

    for nom, regle in cfg.items():
        contraintes = 0
        for cle, valeur in regle.items():
            if cle in ("pitch", "vitesse", "pauses"):
                continue
            contraintes += 1
            if cle == "age_max" and (perso.age is None or perso.age > valeur):
                break
            if cle == "age_min" and (perso.age is None or perso.age < valeur):
                break
            if cle == "sexe" and (perso.sexe or "").lower() != str(valeur).lower():
                break
            if cle == "tier_min" and perso.tier < int(valeur):
                break
        else:
            if contraintes:
                return {"nom": nom,
                        "pitch": float(regle.get("pitch", 1.0)),
                        "vitesse": float(regle.get("vitesse", 1.0)),
                        "pauses": float(regle.get("pauses", 1.0))}
    return defaut


def ton_du_dialogue(texte: str, incise: str = "") -> str:
    """Comment cette réplique se dit. Déterministe, sans modèle.

    L'incise l'emporte sur la ponctuation : « Va-t'en », murmure-t-il, est un
    murmure même avec un point d'exclamation — c'est l'auteur qui a tranché.
    """
    bas = (incise or "").lower()
    for ton, verbes in TONS_PAR_VERBE.items():
        if any(v in bas for v in verbes):
            return ton

    brut = (texte or "").strip()
    if not brut:
        return "neutre"
    if len(MAJUSCULES.findall(brut)) >= 2:
        return "cri"
    if brut.endswith("!") or brut.endswith(" !"):
        return "cri" if brut.isupper() else "colere"
    if brut.endswith("…") or brut.endswith("..."):
        return "hesitation"
    if brut.endswith("?") or brut.endswith(" ?"):
        return "question"
    return "neutre"


def ton_de_narration(rs: Ruleset, registre: str) -> str:
    """Le souffle de la scène, relu à voix haute.

    Le registre est déjà choisi et stocké à chaque tour. Il ne servait qu'à
    écrire la consigne du narrateur ; il sert maintenant aussi à la lire.
    """
    table = rs.data.get("voix", {}).get("ton_par_registre", {}) or {}
    return table.get(registre or "ordinaire", "neutre")


def modulation(rs: Ruleset, ton: str) -> dict:
    """Les facteurs du ton, tels que le ruleset les déclare."""
    tons = rs.data.get("voix", {}).get("tons", {}) or {}
    base = {"pitch": 1.0, "vitesse": 1.0, "volume": 1.0, "pause": 1.0}
    return {**base, **{k: float(v) for k, v in (tons.get(ton) or {}).items()
                       if k in base}}


def prononcer(texte: str, pack) -> str:
    """Réécrit ce qui ne se prononce pas tout seul.

    S'applique UNIQUEMENT au texte lu. Le joueur continue de voir
    « Konohagakure » à l'écran — on ne corrige pas son orthographe, on aide
    le synthétiseur.
    """
    sortie = texte or ""
    for terme, fr in getattr(pack, "lexique", []):
        if terme and terme in sortie:
            sortie = sortie.replace(terme, fr)
    return sortie


def plan_de_lecture(session: Session, camp: Campaign, tour: Turn,
                    rs: Ruleset, pack) -> list[dict]:
    """Ce que le navigateur doit dire, et comment.

    Un segment = un texte prononçable, une hauteur, une vitesse. Le narrateur
    garde une voix neutre : c'est aux personnages de se distinguer de lui, pas
    l'inverse.
    """
    persos = {c.nom: c for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id)).all()}
    segments = attribuer(segmenter(tour.narration), list(persos))

    # Le registre de CETTE scène, déjà choisi au tour joué. Voir rythme.py.
    registre = (tour.resolution or {}).get("registre", "ordinaire")
    ton_fond = ton_de_narration(rs, registre)

    plan = []
    for i, seg in enumerate(segments):
        perso = persos.get(seg.get("locuteur", ""))
        dialogue = seg["type"] == "dialogue"
        grain = archetype(rs, perso) if dialogue else {
            "pitch": 1.0, "vitesse": 1.0, "pauses": 1.0}

        # L'incise est ce qui SUIT la réplique — « … », murmure-t-il. C'est là
        # que le français met le verbe de parole, et c'est le signal le plus
        # sûr de la façon dont la phrase se dit.
        if dialogue:
            incise = segments[i + 1]["texte"][:140] if i + 1 < len(segments) else ""
            ton = ton_du_dialogue(seg["texte"], incise)
        else:
            ton = ton_fond
        mod = modulation(rs, ton)

        texte = prononcer(seg["texte"], pack)
        if not texte:
            continue
        # Le ton MULTIPLIE l'archétype : le grain de voix reste celui du
        # personnage, seul le moment change. Et on borne — un synthétiseur
        # au-delà de ces valeurs devient inaudible, pas expressif.
        plan.append({
            "texte": texte,
            "type": seg["type"],
            "locuteur": seg.get("locuteur", ""),
            "ton": ton,
            "pitch": round(min(2.0, max(0.5, grain["pitch"] * mod["pitch"])), 3),
            "vitesse": round(min(1.6, max(0.6, grain["vitesse"] * mod["vitesse"])), 3),
            "volume": round(min(1.0, max(0.15, mod["volume"])), 3),
            "pause": round(min(3.0, max(0.4,
                           grain.get("pauses", 1.0) * mod["pause"])), 3),
        })
    return plan
