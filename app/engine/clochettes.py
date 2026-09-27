"""L'épreuve des clochettes — la première mission, qui apprend le jeu.

Nindō 2.0, chantier E. Un manuel n'apprend rien à personne ; une scène, si.
L'instructeur porte deux clochettes, l'équipe doit en prendre une avant
midi. Ce que le joueur découvre en la jouant :

  1. la boucle du tour — déclarer, lire l'annonce, lancer ;
  2. le combat en rounds, sur le plateau, avec les cartes ;
  3. la RÈGLE DU FOSSÉ — un jônin est hors de portée d'un genin, le frapper
     ne mène à rien, et le panneau le dit en clair ;
  4. les LEVIERS et le nombre — c'est ensemble, par la ruse et le terrain,
     que l'écart se referme et que la clochette devient prenable.

TROIS ACTES, comme toute mission : l'explication, l'affrontement contre
l'instructeur (ouvert par le moteur, avec ses objectifs à lui), le verdict.
La réussite ne tient pas aux clochettes prises : elle tient à l'équipe.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import Campaign, Character, Encounter, Quest

ARCHETYPE = "clochettes"
OBJECTIFS = ["prendre une clochette", "faire équipe", "gagner du temps"]

ACTES = [
    {"numero": 1, "nom": "Les clochettes", "depuis": 0,
     "consigne": "L'instructeur explique l'épreuve, montre les clochettes, fixe "
                 "midi. Il provoque, il jauge, il laisse l'équipe se disperser. "
                 "Ne raconte AUCUN échange de coups avant qu'il n'attaque : ce "
                 "sera le moteur qui l'ouvrira."},
    {"numero": 2, "nom": "Seuls contre lui", "depuis": 2,
     "consigne": "L'ÉPREUVE EST UN AFFRONTEMENT contre l'instructeur. Il est très "
                 "au-dessus d'eux et le montre sans forcer : il pare d'une main, "
                 "il commente, il lit un livre. Chaque fois que l'équipe se "
                 "coordonne (une diversion, un piège, un appât), il est un peu "
                 "moins insaisissable — et c'est ça qu'il regarde. {complication}"},
    {"numero": 3, "nom": "Le verdict", "depuis": 7,
     "consigne": "Midi sonne. L'instructeur rend son verdict : ce qui compte "
                 "n'était pas la clochette mais l'équipe. S'ils ont fait équipe, "
                 "il les garde (la mission est réussie) ; sinon il leur laisse "
                 "une dernière chance, et le dit durement."},
]


def quete(session: Session, camp: Campaign) -> Quest | None:
    return session.exec(select(Quest).where(
        Quest.campaign_id == camp.id, Quest.archetype == ARCHETYPE,
        Quest.statut.in_(["acceptée", "en cours"]))).first()


def declencher(session: Session, camp: Campaign, pack, rs, pj: Character) -> Encounter | None:
    """À l'acte 2, l'instructeur attaque : la rencontre s'ouvre contre lui,
    avec les objectifs de l'épreuve. Une fois, si personne ne se bat déjà."""
    from app.engine import combat
    q = quete(session, camp)
    if q is None or q.debut_tour is None or camp.tour - q.debut_tour < ACTES[1]["depuis"]:
        return None
    if combat.active(session, camp) is not None:
        return None
    deja = session.exec(select(Encounter).where(
        Encounter.campaign_id == camp.id, Encounter.declencheur == ARCHETYPE)).first()
    if deja is not None:
        return None
    sensei = session.get(Character, q.donneur_id) if q.donneur_id else None
    if sensei is None or not sensei.vivant or sensei.location_id != pj.location_id:
        return None
    renc = combat.ouvrir(session, camp, pack, rs, pj, declencheur=ARCHETYPE,
                         adversaires_existants=[sensei],
                         titre=f"L'épreuve des clochettes — {sensei.nom}")
    if renc is not None:
        # Les objectifs de l'épreuve remplacent ceux du fossé : on ne vainc
        # pas son instructeur, on lui prend une clochette — ensemble.
        # L'objectif est fixé d'emblée — « faire équipe » — et chaque action
        # réussie le fait avancer, même si l'instructeur n'est qu'un cran
        # au-dessus : c'est le nombre qui referme l'écart, c'est la leçon.
        renc.objectifs = list(OBJECTIFS)
        renc.objectif = OBJECTIFS[1]
        session.add(renc)
        session.commit()
    return renc


def solder(session: Session, camp: Campaign, rs, renc: Encounter, statut: str,
           pj: Character) -> list[str]:
    """La rencontre de l'épreuve se referme : la mission se conclut avec
    elle. Ce qui compte, c'est d'avoir fait équipe — c'est-à-dire d'avoir
    fait progresser l'objectif, pas d'avoir « gagné »."""
    from app.engine import missions
    q = quete(session, camp)
    if q is None or renc.declencheur != ARCHETYPE:
        return []
    reussie = statut in ("objectif_atteint", "gagnee") or renc.progres >= 2 \
        or "nombre" in (renc.leviers or []) and renc.progres >= 1
    return missions.changer_statut(session, camp, pj, rs, q,
                                   "réussie" if reussie else "échouée")
