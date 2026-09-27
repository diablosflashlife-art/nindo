"""Cristallisation : une ligne devient un personnage.

Le vrai problème de la génération à la volée n'est pas de générer : c'est de
RÉGÉNÉRER DIFFÉREMMENT la fois suivante. Un aubergiste bourru au tour 30 et
timide au tour 90 détruit la crédibilité du monde plus sûrement qu'une fiche
absente.

D'où le gel : une fois écrite, la fiche est une entité comme une autre. Elle
est relue au contexte, jamais réinventée. Elle s'enrichit ou s'édite à la main
en console MJ — elle ne se régénère pas.
"""
from __future__ import annotations

import random

from sqlmodel import Session, select

from app.config import settings
from app.engine import francais
from app.engine.validators import valider_cristallisation
from app.engine.garde import decanoniser, nettoyer_nom, nom_canon, nom_propre, pudeur
from app.llm.prompts import CRISTALLISATION
from app.llm.provider import get_llm
from app.llm.schemas import CRISTALLISATION as SCHEMA
from app.lore.pack import LorePack
from app.models import (Campaign, Character, Crystallization, Event, Knowledge,
                        Location, MemoryFact, Relation, Secret)
from app.rules.engine import Ruleset


def compter_session(session: Session, camp: Campaign) -> int:
    """Cristallisations déjà faites récemment. Le plafond n'est pas une
    limitation subie : c'est une règle de mise en scène — un bon MJ ramène les
    mêmes visages au lieu d'en présenter quinze."""
    recentes = session.exec(select(Crystallization).where(
        Crystallization.campaign_id == camp.id,
        Crystallization.tour > camp.tour - 20)).all()
    return len(recentes)


def budget_disponible(session: Session, camp: Campaign) -> bool:
    return compter_session(session, camp) < settings.cristallisations_par_session


def reemployer(session: Session, camp: Campaign, lieu_id: int | None) -> Character | None:
    """Cherche un PNJ déjà connu qui pourrait tenir le rôle, plutôt que d'en
    créer un nouveau. C'est ce que fait un maître du jeu humain."""
    candidats = session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.is_pc == False,          # noqa: E712
        Character.vivant == True,          # noqa: E712
        Character.role_campagne == "figurant")).all()
    if lieu_id:
        proches = [c for c in candidats if c.location_id == lieu_id]
        if proches:
            return min(proches, key=lambda c: c.apparitions)
    return min(candidats, key=lambda c: c.apparitions) if candidats else None


# --------------------------------------------------------------------------
# Un visage croisé souvent finit par être quelqu'un
# --------------------------------------------------------------------------
# `enrichir()` était écrit, `settings.seuil_complet` déclaré, et rien n'appelait
# ni l'un ni l'autre. Un figurant recroisé dix fois restait « figurant » : pas
# de relation, donc absent du panneau de l'entourage ; pas d'objectif connu,
# donc invisible au narrateur ; pas d'histoire, donc rien à raconter.
#
# C'est pourtant de là que vient le sentiment d'un monde plutôt que d'un décor.
# L'aubergiste qu'on salue au tour 30 parce qu'on l'a vu au tour 8 vaut trois
# PNJ neufs.
#
# UNE PROMOTION PAR TOUR AU MAXIMUM. Elle coûte une génération, et promouvoir
# trois personnes dans la même scène les banalise toutes.
ROLE_PROMU = "connaissance"

# Ce que le PNJ pense du joueur quand il devient quelqu'un : positif mais
# faible. On s'est croisés, ce n'est pas encore une amitié — et la valeur
# bougera ensuite au rythme du jeu, comme toutes les autres.
ESTIME_INITIALE = 12

# Assez rare pour rester une bonne surprise, assez fréquent pour qu'on y croie.
CHANCE_RECROISEMENT = 0.22


def promouvoir(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
               pj: Character, presents: list[Character]) -> list[str]:
    """Le premier figurant assez vu devient une connaissance.

    Ne lève jamais : rater une promotion ne doit pas interrompre un tour, et
    le tour suivant réessaiera — le compteur d'apparitions, lui, est déjà écrit.
    """
    seuil = max(2, int(settings.seuil_complet))
    for pnj in presents:
        if pnj.is_pc or not pnj.vivant:
            continue
        if pnj.role_campagne != "figurant" or pnj.source != "genere":
            continue
        if (pnj.apparitions or 0) < seuil:
            continue

        try:
            enrichir(session, camp, pack, rs, pnj)
        except Exception:  # noqa: BLE001 — la fiche reste utilisable telle quelle
            session.rollback()

        pnj.role_campagne = ROLE_PROMU
        session.add(pnj)

        # La relation est ce qui le fait ENTRER dans le panneau de l'entourage
        # et dans les pressions de `liens.py`. Sans elle, la promotion ne se
        # verrait nulle part.
        deja = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id,
            Relation.source_id == pnj.id,
            Relation.cible_id == pj.id)).first()
        if deja is None:
            session.add(Relation(
                campaign_id=camp.id, source_id=pnj.id, cible_id=pj.id,
                nature="connaissance", valeur=ESTIME_INITIALE,
                note=f"Se sont croisés {francais.accorde(pnj.apparitions, 'fois', 'fois')}."))

        session.add(Knowledge(
            campaign_id=camp.id, groupe=True, sujet_type="character",
            sujet_id=pnj.id, aspect="identite", niveau=3,
            contenu=f"{pnj.nom} n'est plus un inconnu : on se salue.",
            source="habitude", tour=camp.tour))
        session.add(Event(
            campaign_id=camp.id, tour=camp.tour, type="entourage",
            resume=f"{pnj.nom} n'est plus un visage croisé : "
                   f"{pj.nom} le connaît maintenant.",
            importance=3, entites=[pj.id, pnj.id]))
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, nature="relation",
            importance=3,
            texte=f"Au tour {camp.tour}, {pnj.nom} est devenu une "
                  f"connaissance de {pj.nom} à force de se croiser.",
            entites=[pj.id, pnj.id]))
        session.commit()
        return [f"{pnj.nom} n'est plus un inconnu."]
    return []


def recroiser(session: Session, camp: Campaign, pj: Character,
              arrivee_id: int) -> list[str]:
    """Le monde est petit : une connaissance peut se trouver là où on arrive.

    DÉTERMINISTE. Tiré sur la graine de campagne et le tour, donc reproductible
    — recharger une sauvegarde ne rejoue pas les dés autrement.

    Ce n'est pas un hasard gratuit : seules les personnes DÉJÀ promues sont
    candidates, et elles ont des raisons de bouger. Un figurant, lui, reste où
    il est — c'est ce qui distingue un décor d'un personnage.
    """
    candidats = [c for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.is_pc == False,          # noqa: E712
        Character.vivant == True,          # noqa: E712
        Character.role_campagne == ROLE_PROMU)).all()
        if c.location_id != arrivee_id]
    if not candidats:
        return []

    rng = random.Random(camp.graine * 7919 + camp.tour)
    if rng.random() > CHANCE_RECROISEMENT:
        return []

    qui = rng.choice(candidats)
    qui.location_id = arrivee_id
    qui.apparitions = (qui.apparitions or 0) + 1
    session.add(qui)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="entourage",
        resume=f"{qui.nom} se trouvait là, sans l'avoir prévu.",
        importance=2, entites=[pj.id, qui.id]))
    return [f"Tu tombes sur {qui.nom}."]


def cristalliser(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                 germe: dict, *, declencheur: str, lieu: Location | None = None,
                 niveau: str = "leger") -> Character:
    """Crée un personnage à partir d'un germe minimal.

    Le germe (nom, rôle, village, affiliation) reste CANON : la génération ne
    peut rien y contredire. C'est le validateur qui l'impose.
    """
    rng = random.Random(camp.graine + camp.tour * 977 + len(germe.get("nom", "")))
    village = pack.village(germe.get("village_ref") or "konoha") or {}
    detail = rng.choice(pack.sel) if pack.sel else "porte un objet sans valeur apparente"

    lignes = [
        "### GERME (non négociable)",
        f"Nom : {germe.get('nom') or 'à inventer, plausible pour ce village'}",
        f"Rôle : {germe.get('role', 'habitant')}",
        f"Village : {village.get('nom_fr', village.get('nom', ''))}",
        f"Affiliation : {germe.get('affiliation', 'aucune')}",
        f"Importance : {germe.get('importance', 'figurant')}",
        f"\n### CULTURE DU VILLAGE\n{village.get('culture', '').strip()}",
        f"Manière de parler locale : {village.get('parler', '')}",
        f"\n### SCÈNE\n{germe.get('scene', 'rencontre ordinaire')}",
        f"Lieu : {lieu.nom if lieu else 'non précisé'}",
        f"\n### DÉTAIL DISTINCTIF IMPOSÉ\n{detail}",
    ]

    brut = get_llm().json(CRISTALLISATION, "\n".join(lignes), SCHEMA, rapide=True)
    net, rejets = valider_cristallisation(germe, brut)

    # Un nom en double casserait la résolution des personnages cités
    existants = {c.nom.lower() for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id)).all()}
    nom = nettoyer_nom(net["nom"]) or net["nom"]
    # « Inconnu », « présence furtive » : une étiquette ou une description, pas
    # un nom. Un personnage qu'on croise six fois mérite d'en avoir un.
    if not nom_propre(nom):
        prenoms = [p for p in pack.prenoms_adversaire if not nom_canon(p)] or ["Goro"]
        nom = rng.choice(prenoms)
        rejets.append("nom inventé")
    # Un nom venu du récit est canon pour la partie ; un nom inventé ici ne
    # doit pas être celui d'un personnage de la série.
    if not germe.get("nom") and nom_canon(nom):
        nom = decanoniser(nom, rng, pack.prenoms_adversaire)
        rejets.append("nom de la série remplacé")
    if nom.lower() in existants:
        nom = f"{nom} le {germe.get('role', 'passant')}"
        rejets.append("nom dédoublonné")

    stats = rs.stats_defaut()
    importance = germe.get("importance", "figurant")
    if importance == "notable":
        for k in rng.sample(list(stats), 3):
            stats[k] += rng.randint(2, 6)
    elif importance != "figurant":
        for k in rng.sample(list(stats), 2):
            stats[k] += rng.randint(1, 3)

    pnj = Character(
        campaign_id=camp.id, nom=nom, is_pc=False,
        age=germe.get("age"), apparence=pudeur(net["apparence"], germe.get("age")),
        personnalite=pudeur(net["personnalite"], germe.get("age")), parler=net["parler"],
        objectifs=[net["objectif"]] if net["objectif"] else [],
        clan="Sans clan", village=village.get("nom_fr", village.get("nom", "")),
        village_ref=germe.get("village_ref", ""),
        grade=germe.get("grade", "genin"), niveau=germe.get("niveau", 1),
        stats=stats, ressources=rs.ressources_defaut(),
        location_id=lieu.id if lieu else None,
        source="genere", role_campagne=germe.get("role_campagne", "figurant"),
        apparitions=1, notes=f"[détail imposé] {detail}",
    )
    session.add(pnj)
    session.commit()
    session.refresh(pnj)

    res = rs.puissance(stats, [], pnj.niveau)
    pnj.pe, pnj.tier = res["pe"], res["tier"]
    session.add(pnj)

    if net["secret"]:
        session.add(Secret(
            campaign_id=camp.id, question=f"Que cache {nom} ?",
            verite=net["secret"], sujet_id=pnj.id,
            indices=[{"palier": 1, "texte": f"{nom} élude une question."}]))

    session.add(Knowledge(
        campaign_id=camp.id, groupe=True, sujet_type="character", sujet_id=pnj.id,
        aspect="identite", niveau=2, contenu=f"{nom} — {germe.get('role', 'habitant')}.",
        source="rencontre", tour=camp.tour))

    session.add(Crystallization(
        campaign_id=camp.id, character_id=pnj.id, tour=camp.tour,
        niveau=niveau, declencheur=declencheur, germe=germe, rejets=rejets))
    session.commit()
    session.refresh(pnj)
    return pnj


def enrichir(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
             pnj: Character) -> Character:
    """Passage de `leger` à `complet` : on AJOUTE des champs vides, on ne
    réécrit jamais ceux déjà remplis. Le gel n'est pas négociable."""
    if pnj.source != "genere":
        return pnj
    germe = {"nom": pnj.nom, "role": pnj.role_campagne, "village_ref": pnj.village_ref,
             "importance": "notable", "scene": "personnage devenu récurrent"}
    lieu = session.get(Location, pnj.location_id) if pnj.location_id else None

    brut = get_llm().json(CRISTALLISATION, _germe_texte(pack, germe, lieu), SCHEMA, rapide=True)
    net, rejets = valider_cristallisation(germe, brut)

    if not pnj.apparence:
        pnj.apparence = net["apparence"]
    if not pnj.origine:
        pnj.origine = net.get("histoire", "") or ""
    if net["objectif"] and net["objectif"] not in pnj.objectifs:
        pnj.objectifs = [*pnj.objectifs, net["objectif"]][:3]
    session.add(pnj)
    session.add(Crystallization(
        campaign_id=camp.id, character_id=pnj.id, tour=camp.tour,
        niveau="complet", declencheur="recurrence", germe=germe, rejets=rejets))
    session.commit()
    session.refresh(pnj)
    return pnj


def _germe_texte(pack: LorePack, germe: dict, lieu: Location | None) -> str:
    village = pack.village(germe.get("village_ref") or "konoha") or {}
    return "\n".join([
        "### GERME (non négociable)",
        f"Nom : {germe.get('nom')}",
        f"Rôle : {germe.get('role')}",
        f"Village : {village.get('nom_fr', village.get('nom', ''))}",
        f"\n### CULTURE\n{village.get('culture', '').strip()}",
        f"\n### SCÈNE\n{germe.get('scene', '')}",
        f"Lieu : {lieu.nom if lieu else 'non précisé'}",
    ])
