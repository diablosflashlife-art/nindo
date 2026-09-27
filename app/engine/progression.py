"""Monter de niveau, et que ça change quelque chose.

LA PROMESSE QUI N'ÉTAIT PAS TENUE. `progression.points_par_niveau: 2` est
écrit dans le ruleset depuis le premier jour. Personne ne les distribuait
jamais. Monter de niveau incrémentait un compteur, recalculait une puissance
qui ne bougeait pas — puisque aucune caractéristique n'avait changé — et le
joueur n'avait rien à décider.

Or c'est la seule boucle où le joueur CHOISIT ce que son personnage devient.
Tout le reste lui arrive : la destinée est tirée, l'entourage est généré, les
techniques s'ouvrent quand le récit le veut. Les points de niveau sont la
part qui lui appartient.

DEUX GARDE-FOUS.

  UN PLAFOND PAR CARACTÉRISTIQUE. Sans lui, la pente optimale est de tout
  mettre dans la même case, et un genin de niveau vingt frappe comme un Kage
  en étant incapable de tout le reste.

  ON NE DÉPENSE PAS À LA PLACE DU JOUEUR. Les points s'accumulent tant qu'il
  ne choisit pas. Un personnage qui garde trois points en réserve est un
  personnage dont le joueur attend de savoir ce dont il aura besoin.
"""
from __future__ import annotations

from sqlmodel import Session

from app.models import Campaign, Character, Event
from app.rules.engine import Ruleset


class ProgressionRefusee(RuntimeError):
    """Le point ne peut pas être placé là, et le joueur doit savoir pourquoi."""


def par_niveau(rs: Ruleset) -> int:
    return int(rs.data.get("progression", {}).get("points_par_niveau", 2))


def plafond(rs: Ruleset) -> int:
    """La valeur au-delà de laquelle une caractéristique ne monte plus.

    Sans plafond, la pente optimale est de tout mettre dans la même case.
    """
    return int(rs.data.get("progression", {}).get("stat_max", 30))


def gagner_xp(session, camp, rs: Ruleset, pj: Character, xp: int) -> str:
    """Crédite l'XP, gère le passage de niveau, et rend la ligne d'effet.
    Utilisé par le tour et par la récompense des missions."""
    niveau, reste, gagnes = rs.appliquer_xp(pj.niveau, pj.xp, xp)
    pj.niveau, pj.xp = niveau, reste
    mention = ""
    if gagnes:
        libres = crediter(rs, pj, gagnes)
        mention = f" — NIVEAU {niveau} atteint ! {libres} point(s) à placer, en haut de la colonne de droite."
        from app.engine.creation import _recalculer_puissance
        _recalculer_puissance(session, camp, rs, pj)
    session.add(pj)
    return f"XP {xp:+d}{mention}"


def crediter(rs: Ruleset, pj: Character, niveaux: int) -> int:
    """Ajoute les points des niveaux gagnés. Rend le total en attente."""
    if niveaux > 0:
        pj.points_libres = (pj.points_libres or 0) + niveaux * par_niveau(rs)
    return pj.points_libres or 0


def depenser(session: Session, camp: Campaign, rs: Ruleset, pj: Character,
             stat: str, points: int = 1) -> list[str]:
    """Place des points sur une caractéristique.

    Écrit immédiatement et recalcule la puissance : le joueur doit voir son
    tier bouger au moment où il décide, pas au tour suivant.
    """
    if stat not in rs.stats:
        raise ProgressionRefusee("Cette caractéristique n'existe pas.")
    points = max(1, int(points))
    if (pj.points_libres or 0) < points:
        raise ProgressionRefusee(
            "Tu n'as pas assez de points. On en gagne en montant de niveau.")

    avant = int(pj.stats.get(stat, 10))
    maxi = plafond(rs)
    if avant >= maxi:
        raise ProgressionRefusee(
            f"{rs.stats[stat].get('label', stat)} est déjà à son maximum "
            f"({maxi}). Ce qui te reste à gagner est ailleurs.")
    points = min(points, maxi - avant)

    pj.stats = {**pj.stats, stat: avant + points}
    pj.points_libres = (pj.points_libres or 0) - points
    session.add(pj)

    libelle = rs.stats[stat].get("label", stat)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="progression",
        resume=f"{pj.nom} a travaillé son {libelle.lower()} "
               f"({avant} → {avant + points}).",
        importance=2, entites=[pj.id]))
    session.commit()

    from app.engine.creation import _recalculer_puissance
    tier_avant = pj.tier
    _recalculer_puissance(session, camp, rs, pj)
    session.commit()

    effets = [f"{libelle} {avant} → {avant + points}"]
    if pj.tier > tier_avant:
        effets.append(f"Tu changes de rang de puissance : {rs.tier_label(pj.tier)}.")
    return effets


def offres(rs: Ruleset, pj: Character) -> list[dict]:
    """Ce que le joueur peut choisir, groupé comme sur sa fiche.

    Le regroupement se fait ICI et pas dans le gabarit : la table et la fiche
    affichent la même chose, et le jour où une caractéristique change de
    famille il n'y a qu'un endroit à relire.

    On rend TOUTES les caractéristiques, y compris celles au plafond : cacher
    une option qu'on a déjà maximisée ferait croire qu'elle a disparu.
    """
    libelles = rs.data.get("groupes_stats", {})
    maxi = plafond(rs)
    groupes: list[dict] = []
    index: dict[str, dict] = {}

    for cle, cfg in rs.stats.items():
        nom = cfg.get("groupe", "") or "autres"
        if nom not in index:
            index[nom] = {"cle": nom,
                          "label": libelles.get(nom, {}).get("label", nom),
                          "stats": []}
            groupes.append(index[nom])
        valeur = int(pj.stats.get(cle, cfg.get("default", 10)))
        index[nom]["stats"].append({
            "cle": cle,
            "label": cfg.get("label", cle),
            "valeur": valeur,
            "maxi": maxi,
            "plein": valeur >= maxi,
        })
    return groupes
