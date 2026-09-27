"""L'horloge du monde — ce qui bouge quand le joueur regarde ailleurs.

LA PROMESSE QUI N'ÉTAIT PAS TENUE. Le prompt du narrateur affirme depuis le
premier jour : « Chaque PNJ poursuit ses propres objectifs, même quand le
joueur n'est pas là. » Or rien, dans le moteur, ne le rendait vrai.
`Faction.objectifs` était écrit à l'amorce et jamais relu. Le monde était un
décor qui attendait, et le joueur finissait par le sentir : rien n'arrivait
jamais sans lui.

CE QUE CE MODULE FAIT. Tous les N tours, une faction ou un personnage avance
d'un cran sur un de ses objectifs. Ça produit un ÉVÉNEMENT — daté, attribué,
inscrit au journal — et parfois une RUMEUR, qui est la seule forme sous
laquelle le joueur peut l'apprendre s'il n'était pas là.

CE QU'IL NE FAIT PAS, ET C'EST ESSENTIEL.

  IL NE TOUCHE PAS AU JOUEUR. Aucune conséquence directe sur sa fiche, ses
  ressources ou ses relations. Un monde qui punit dans le dos est un monde
  injuste ; un monde qui BOUGE dans le dos est un monde vivant.

  IL N'INVENTE NI NOM NI LIEU. Il ne travaille que sur des entités déjà en
  base — les factions instanciées à l'amorce, les personnages cristallisés.
  C'est la même règle que partout ailleurs.

  IL NE DEMANDE RIEN AU MODÈLE. Un appel de plus par tour pour du bruit de
  fond ne se justifie pas. Les phrases viennent des objectifs déjà écrits.
"""
from __future__ import annotations

import random

from sqlmodel import Session, select

from app.models import Campaign, Character, Event, Faction, MemoryFact

# Tous les combien le monde avance. Assez rare pour qu'un événement compte,
# assez fréquent pour qu'on sente que ça respire.
CADENCE = 4

# Les étapes d'un objectif. Une faction ne réussit pas d'un coup : elle
# prépare, elle agit, elle obtient — et le joueur peut s'en mêler entre deux.
ETAPES = [
    ("prepare", "{qui} prépare quelque chose : {quoi}."),
    ("agit", "{qui} est passé à l'acte : {quoi}."),
    ("aboutit", "{qui} a obtenu ce qu'il cherchait : {quoi}."),
]

# Comment une nouvelle circule quand le joueur n'était pas là.
COLPORTAGE = [
    "On raconte au quartier marchand que {phrase}",
    "Un genin de retour de mission rapporte que {phrase}",
    "Le bureau des missions a reçu un rapport : {phrase}",
    "Personne ne le dit tout haut, mais {phrase}",
]


def _rng(camp: Campaign) -> random.Random:
    """Déterministe par campagne ET par tour : rejouer une partie depuis sa
    graine redonne exactement le même monde."""
    return random.Random(camp.graine * 7919 + camp.tour * 104729)


def _acteurs(session: Session, camp: Campaign) -> list[tuple[str, list[str]]]:
    """Qui a des objectifs, et lesquels. Factions d'abord — elles agissent à
    l'échelle du village ; puis les personnages marquants."""
    out: list[tuple[str, list[str]]] = []
    for f in session.exec(select(Faction).where(
            Faction.campaign_id == camp.id)).all():
        if f.objectifs:
            out.append((f.nom, list(f.objectifs)))
    for c in session.exec(select(Character).where(
            Character.campaign_id == camp.id,
            Character.is_pc == False,          # noqa: E712
            Character.vivant == True)).all():  # noqa: E712
        if c.objectifs and c.role_campagne in ("sensei", "rival", "antagoniste"):
            out.append((c.nom, list(c.objectifs)))
    return out


def _avancement(session: Session, camp: Campaign, qui: str) -> int:
    """Combien de crans cet acteur a déjà franchis. On compte ses événements
    plutôt que d'ajouter une table : le journal est déjà la mémoire du monde."""
    faits = session.exec(select(Event).where(
        Event.campaign_id == camp.id, Event.type == "monde")).all()
    return sum(1 for e in faits if e.resume.startswith(qui))


def avancer(session: Session, camp: Campaign) -> list[str]:
    """Fait avancer le monde d'un cran, si c'est l'heure.

    Rend les effets destinés au joueur — c'est-à-dire ce qu'il APPREND, pas ce
    qui s'est réellement produit. Le reste vit dans le journal et remontera au
    narrateur par la mémoire longue.
    """
    if camp.tour <= 1 or camp.tour % CADENCE:
        return []

    acteurs = _acteurs(session, camp)
    if not acteurs:
        return []

    rng = _rng(camp)
    qui, objectifs = rng.choice(acteurs)
    objectif = rng.choice(objectifs)
    cran = min(_avancement(session, camp, qui), len(ETAPES) - 1)
    _, gabarit = ETAPES[cran]
    phrase = gabarit.format(qui=qui, quoi=objectif.rstrip("."))

    session.add(Event(campaign_id=camp.id, tour=camp.tour, type="monde",
                      resume=phrase, importance=3, portee="prive"))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, nature="fait", importance=3,
        texte=f"(hors scène, tour {camp.tour}) {phrase}"))

    # Le joueur n'en apprend qu'une partie, et par ouï-dire. Une nouvelle qui
    # arrive toujours complète et toujours vraie n'est pas une rumeur.
    if rng.random() < 0.55:
        bruit = rng.choice(COLPORTAGE).format(
            phrase=phrase[0].lower() + phrase[1:])
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, nature="fait", importance=2,
            texte=bruit))
        return [f"Une rumeur circule : {bruit}"]
    return []


def rumeurs_recentes(session: Session, camp: Campaign,
                     limite: int = 3) -> list[Event]:
    """Ce que le monde a fait dernièrement, pour le contexte du narrateur.

    Le narrateur DOIT le savoir — sans quoi il ne peut pas y faire allusion,
    et la rumeur que le joueur a lue dans ses conséquences resterait sans
    suite.
    """
    return list(session.exec(select(Event).where(
        Event.campaign_id == camp.id, Event.type == "monde")
        .order_by(Event.tour.desc()).limit(limite)).all())
