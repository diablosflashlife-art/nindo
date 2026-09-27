"""Moteur de révélation — ce qui fait qu'une campagne s'ouvre.

Le problème que ce module résout
--------------------------------
La destinée du joueur était écrite intégralement à la création, figée, avec ses
paliers, ses conditions et ses indices. Puis rien ne la lisait jamais. Un
`DestinyTrait` naissait `latent` et restait `latent` au tour 200. Les
`DestinyClue` n'étaient jamais délivrés, les `Secret` gardaient
`niveau_revele = 0`, et aucun `Knowledge` n'était créé après le tour 0.

Conséquence en jeu : le joueur recevait son présage au premier tour et
n'apprenait plus jamais rien. Le critère du prompt PRÉSAGE — « relu au tour 200,
ce texte doit paraître avoir tout annoncé » — était inatteignable, faute d'un
module qui ouvre ce que la création a scellé.

Et un effet de bord plus vicieux : `context.py` n'injecte les objectifs d'un PNJ
que s'ils sont connus du groupe (`Knowledge.niveau >= 2`). Comme rien n'en
créait, aucun objectif de PNJ n'atteignait jamais le narrateur. Le filtre de
divulgation était parfait et fermé à double tour.

Les trois canaux
----------------
1. DESTINÉE — jugée par le modèle. Les conditions sont écrites en français
   (« un choc émotionnel violent vécu en jeu ») : seul un lecteur peut dire si
   la scène l'a réalisée. On ne lui envoie QUE la condition, jamais la vérité
   du trait.
2. SECRETS — déterministe. Un secret s'effrite à force de côtoyer celui qui le
   porte. Aucun appel au modèle : le nombre d'apparitions et la relation
   suffisent.
3. CONNAISSANCE — déterministe. À force de fréquenter un PNJ, on finit par
   savoir ce qu'il veut. C'est ce canal qui débloque les objectifs dans le
   contexte du narrateur.

Le rythme est bridé volontairement
----------------------------------
Une révélation par tour au maximum, et un délai de carence après chaque avancée.
Une campagne qui lâche tout en dix tours n'a plus rien à donner au trentième.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.llm.prompts import JUGE_DECLENCHEUR
from app.llm.provider import get_llm
from app.llm.schemas import REVELATION
from app.models import (Campaign, Character, CharacterCapacity,
                        CharacterTechnique, Destiny, DestinyClue, DestinyTrait,
                        Knowledge, MemoryFact, Relation, Secret)

# Tours de carence après une avancée de destinée. Empêche une scène intense de
# faire monter trois paliers d'affilée.
CARENCE_DESTINEE = 4

# Aucune destinée ne bouge avant ce tour. Mesuré en partie réelle : un juge en
# ligne a validé « un entraînement élémentaire poussé » au deuxième tour, sur
# une scène où le personnage examinait des fissures. Une campagne doit d'abord
# exister avant de commencer à se révéler.
PREMIER_EVEIL = 6

# Apparitions nécessaires pour faire tomber le palier N d'un secret. Un secret
# ne se perce pas en une rencontre.
# Mesuré sur une partie de 50 tours : un coéquipier apparaît une fois tous les
# cinq tours environ. À 13, le dernier palier n'était jamais atteint.
SEUILS_SECRET = {1: 3, 2: 6, 3: 10}

# À partir de quand on sait ce qu'un PNJ veut : assez de temps passé ensemble,
# ou une relation assez marquée dans un sens ou dans l'autre.
APPARITIONS_POUR_INTENTION = 3
RELATION_POUR_INTENTION = 35

ETATS = ["latent", "pressenti", "en_eveil", "eveille"]


def reveler(session: Session, camp: Campaign, pj: Character,
            narration: str, pnjs_presents: list[Character]) -> list[str]:
    """Étape 5bis du tour. Retourne les révélations en clair, pour affichage.

    Ne lève jamais : une révélation manquée n'a pas à interrompre un tour. Le
    journal reste la source de vérité, et le tour suivant réessaiera.
    """
    effets: list[str] = []
    try:
        effets += _destinee(session, camp, pj, narration)
    except Exception:  # noqa: BLE001
        pass
    try:
        effets += _secrets(session, camp, pj, pnjs_presents)
    except Exception:  # noqa: BLE001
        pass
    try:
        effets += _intentions(session, camp, pj, pnjs_presents)
    except Exception:  # noqa: BLE001
        pass
    return effets


# --------------------------------------------------------------------------
# 1. Destinée
# --------------------------------------------------------------------------
def _candidats(session: Session, camp: Campaign,
               pj: Character) -> list[tuple[DestinyTrait, dict]]:
    """Les traits dont le PROCHAIN palier est ouvert, avec sa condition.

    Un trait déjà éveillé n'a plus rien à donner ; un trait en carence attend.
    """
    destin = session.exec(select(Destiny).where(
        Destiny.character_id == pj.id)).first()
    if destin is None:
        return []
    traits = session.exec(select(DestinyTrait).where(
        DestinyTrait.destiny_id == destin.id)).all()

    out = []
    for t in traits:
        if t.etat == "eveille" or t.actif_au_depart:
            continue
        if camp.tour < (t.tour_min_prochain or 0):
            continue
        cible = t.palier_courant + 1
        cond = next((c for c in (t.conditions or [])
                     if isinstance(c, dict) and int(c.get("palier", 0)) == cible),
                    None)
        if cond is None:
            continue
        out.append((t, cond))
    return out


def _destinee(session: Session, camp: Campaign, pj: Character,
              narration: str) -> list[str]:
    if camp.tour < PREMIER_EVEIL:
        return []
    candidats = _candidats(session, camp, pj)
    if not candidats:
        return []

    # On n'expose que la condition. `verite` et `libelle` restent en base : dire
    # au modèle que la condition mène au Sharingan l'inciterait à la valider.
    lignes = ["### CONDITIONS EN ATTENTE"]
    for i, (_, cond) in enumerate(candidats):
        lignes.append(f"{i}. {cond.get('si', '')}")

    brut = get_llm().json(
        JUGE_DECLENCHEUR,
        "\n".join(lignes) + f"\n\n### SCÈNE QUI VIENT D'AVOIR LIEU\n{narration}",
        REVELATION, rapide=True)

    realises = [r for r in (brut.get("realises") or [])
                if isinstance(r, dict) and isinstance(r.get("id"), int)
                and 0 <= r["id"] < len(candidats)]
    if not realises:
        return []

    # Une seule par tour, quoi qu'en dise le modèle.
    trait, cond = candidats[realises[0]["id"]]
    pourquoi = (realises[0].get("pourquoi") or "").strip()
    if not cite_la_scene(pourquoi, narration):
        return []
    return _avancer(session, camp, pj, trait, cond, pourquoi[:200])


def cite_la_scene(pourquoi: str, narration: str) -> bool:
    """La consigne exige que `pourquoi` cite le fait de la scène qui réalise
    la condition. Un juge complaisant écrit une justification générale ; on
    vérifie qu'elle reprend au moins deux mots porteurs du récit."""
    import re
    mots = {m for m in re.findall(r"[\wÀ-ÿ]{5,}", (pourquoi or "").lower())}
    recit = (narration or "").lower()
    return sum(1 for m in mots if m in recit) >= 2


def _avancer(session: Session, camp: Campaign, pj: Character,
             trait: DestinyTrait, cond: dict, pourquoi: str) -> list[str]:
    trait.palier_courant += 1
    trait.tour_min_prochain = camp.tour + CARENCE_DESTINEE

    palier_max = max(1, int(trait.palier_max or 1))
    if trait.palier_courant >= palier_max:
        trait.etat = "eveille"
    else:
        trait.etat = ETATS[min(trait.palier_courant, len(ETATS) - 2)]
    session.add(trait)

    # L'indice est ce que le JOUEUR reçoit. Il est vrai, incomplet, et ne
    # nomme jamais ce qu'il annonce.
    indice = (cond.get("indice") or "").strip()
    clue = session.exec(select(DestinyClue).where(
        DestinyClue.trait_id == trait.id,
        DestinyClue.palier == trait.palier_courant)).first()
    if clue is not None:
        clue.delivre_tour = camp.tour
        if indice and not clue.texte:
            clue.texte = indice
        indice = clue.texte or indice
        session.add(clue)
    elif indice:
        session.add(DestinyClue(
            campaign_id=camp.id, trait_id=trait.id,
            palier=trait.palier_courant, texte=indice, delivre_tour=camp.tour))

    if indice:
        # nature « destinee » : lisible par le joueur, et distinct des faits
        # ordinaires pour que l'interface puisse le mettre en avant.
        session.add(MemoryFact(
            campaign_id=camp.id, texte=indice, nature="destinee",
            importance=4, tour=camp.tour, entites=[pj.id]))

    gagne = ""
    if trait.etat == "eveille":
        gagne = _accorder(session, camp, pj, trait)

    etiquette = ("Quelque chose s'est éveillé." if trait.etat == "eveille"
                 else "Quelque chose a bougé.")
    if gagne:
        etiquette += " " + gagne
    return [f"{etiquette} {indice}".strip()
            + (f" (déclencheur : {pourquoi})" if pourquoi else "")]


def _accorder(session: Session, camp: Campaign, pj: Character,
              trait: DestinyTrait) -> str:
    """Rend l'éveil mécaniquement réel.

    Un trait qui s'éveille sans rien accorder est une ligne de texte. Ici il
    monte un palier de capacité ou ajoute une technique — donc il change les
    jets, donc le joueur le SENT.
    """
    ref = (trait.accorde or "").strip()
    if not ref:
        return ""

    cap = session.exec(select(CharacterCapacity).where(
        CharacterCapacity.character_id == pj.id,
        CharacterCapacity.capacite_ref == ref)).first()
    if cap is not None:
        cap.palier = max(1, cap.palier + 1)
        cap.palier_max_atteint = max(cap.palier_max_atteint, cap.palier)
        cap.active = True
        if cap.eveil_tour is None:
            cap.eveil_tour = camp.tour
        session.add(cap)
        return f"Capacité éveillée (palier {cap.palier})."

    tech = session.exec(select(CharacterTechnique).where(
        CharacterTechnique.character_id == pj.id,
        CharacterTechnique.technique_ref == ref)).first()
    if tech is None:
        # Le prérequis, enfin opposable. Le lore déclare que le Chidori
        # demande l'armure de foudre ; tant que personne ne lisait cette
        # ligne, une destinée pouvait l'accorder à un genin qui n'avait
        # jamais tenu un raiton. Un palier qu'on saute n'en est pas un.
        manquants = _prerequis_manquants(session, pj, ref)
        if manquants:
            return (f"Quelque chose a failli s'ouvrir, et n'a pas pu : "
                    f"il y manque {', '.join(manquants)}.")
        session.add(CharacterTechnique(
            campaign_id=camp.id, character_id=pj.id, technique_ref=ref,
            maitrise=15, appris_tour=camp.tour))
        return "Une technique s'est ouverte à toi."

    # Ni capacité déjà suivie, ni technique connue : on crée la capacité.
    session.add(CharacterCapacity(
        campaign_id=camp.id, character_id=pj.id, capacite_ref=ref,
        palier=1, palier_max_atteint=1, active=True, eveil_tour=camp.tour))
    return "Capacité éveillée (palier 1)."


def _prerequis_manquants(session: Session, pj: Character, ref: str) -> list[str]:
    """Ce qui manque au personnage pour qu'une technique puisse s'ouvrir.

    Rend des NOMS lisibles, pas des identifiants : le message revient au
    joueur, et « il y manque Raiton : armure de foudre » lui apprend quelque
    chose, quand « raiton_armure » ne lui apprend rien.
    """
    from app.lore.pack import charger

    try:
        pack = charger()
    except Exception:  # noqa: BLE001 — sans lore, on n'oppose rien
        return []

    connues = {ct.technique_ref for ct in session.exec(
        select(CharacterTechnique).where(
            CharacterTechnique.character_id == pj.id)).all()}
    manquants: list[str] = []
    for s in pack.prerequis_de(ref):
        for r in s.get("requiert") or []:
            if r not in connues:
                manquants.append((pack.technique(r) or {}).get("nom", r))
    return manquants


# --------------------------------------------------------------------------
# 2. Secrets
# --------------------------------------------------------------------------
def _secrets(session: Session, camp: Campaign, pj: Character,
             pnjs: list[Character]) -> list[str]:
    """Un secret s'effrite à force de côtoyer celui qui le porte.

    Déterministe par choix : faire juger ça par un modèle produirait des
    révélations en rafale les tours intenses et rien du tout ensuite.
    """
    effets: list[str] = []
    for pnj in pnjs:
        secret = session.exec(select(Secret).where(
            Secret.campaign_id == camp.id,
            Secret.sujet_id == pnj.id,
            Secret.resolu == False)).first()  # noqa: E712
        if secret is None:
            continue
        # Côtoyer le porteur, c'est garder la question vivante : la fenêtre de
        # vingt tours ne se referme que sur un secret qu'on a DÉLAISSÉ. Sans
        # ceci, sept secrets sur neuf se perdaient en 57 tours alors que le
        # joueur voyait leurs porteurs régulièrement.
        if secret.niveau_revele >= 1:
            from app.engine import secrets as horloge_secrets
            horloge_secrets.garder_vivant(secret, camp.tour)
            session.add(secret)
        prochain = secret.niveau_revele + 1
        seuil = SEUILS_SECRET.get(prochain)
        if seuil is None or pnj.apparitions < seuil:
            continue

        indice = next((i for i in (secret.indices or [])
                       if int(i.get("palier", 0)) == prochain), None)
        if indice is None:
            continue

        secret.niveau_revele = prochain
        # Le joueur a montré qu'il cherchait : les deux horloges de pression
        # repartent de zéro, et les relances consommées lui sont rendues.
        from app.engine import secrets as horloge_secrets

        horloge_secrets.marquer_progres(secret, camp.tour)
        session.add(secret)
        texte = (indice.get("texte") or "").strip()
        if texte:
            session.add(Knowledge(
                campaign_id=camp.id, groupe=True, sujet_type="character",
                sujet_id=pnj.id, aspect="faiblesse", niveau=2, contenu=texte,
                fiable=False, source="observation", tour=camp.tour))
            session.add(MemoryFact(
                campaign_id=camp.id, texte=texte, nature="fait",
                importance=3, tour=camp.tour, entites=[pj.id, pnj.id]))
            effets.append(f"À propos de {pnj.nom} : {texte}")
        # Le dernier palier ne « résout » pas le secret : c'est au jeu de le
        # faire. Il rend seulement la vérité atteignable.
        return effets  # un secret par tour suffit
    return effets


# --------------------------------------------------------------------------
# 3. Ce que l'on finit par savoir des gens
# --------------------------------------------------------------------------
def _intentions(session: Session, camp: Campaign, pj: Character,
                pnjs: list[Character]) -> list[str]:
    """Rend connu l'objectif avouable d'un PNJ suffisamment fréquenté.

    C'est ce canal qui débloque « Poursuit : … » dans le contexte du narrateur.
    Sans lui, le filtre de divulgation garde éternellement fermé quelque chose
    qui devrait s'ouvrir en trois scènes.
    """
    effets: list[str] = []
    for pnj in pnjs:
        if not pnj.objectifs:
            continue
        deja = session.exec(select(Knowledge).where(
            Knowledge.campaign_id == camp.id,
            Knowledge.sujet_type == "character",
            Knowledge.sujet_id == pnj.id,
            Knowledge.aspect == "intention")).first()
        if deja is not None:
            continue

        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id,
            Relation.source_id == pnj.id,
            Relation.cible_id == pj.id)).first()
        proche = abs(rel.valeur) >= RELATION_POUR_INTENTION if rel else False
        if pnj.apparitions < APPARITIONS_POUR_INTENTION and not proche:
            continue

        session.add(Knowledge(
            campaign_id=camp.id, groupe=True, sujet_type="character",
            sujet_id=pnj.id, aspect="intention", niveau=2,
            contenu=f"{pnj.nom} cherche à {pnj.objectifs[0]}",
            source="fréquentation", tour=camp.tour))
        effets.append(f"Tu as compris ce que {pnj.nom} cherche.")
        return effets
    return effets


# --------------------------------------------------------------------------
def compter_apparitions(session: Session, pnjs: list[Character]) -> None:
    """Incrémente le compteur d'apparitions des PNJ présents.

    Rien ne le faisait. `Character.apparitions` est documenté comme « pilote la
    cristallisation » et valait 0 pour tout le monde : ni la cristallisation, ni
    l'enrichissement, ni les seuils de secret ne pouvaient se déclencher.
    """
    for pnj in pnjs:
        pnj.apparitions = (pnj.apparitions or 0) + 1
        session.add(pnj)
