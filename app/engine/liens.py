"""Les relations qui agissent.

LE DÉFAUT. Le graphe social était tenu à jour avec soin — chaque tour pouvait
faire bouger une valeur de -25 à +25, le filtre de divulgation la respectait,
la colonne de droite l'affichait — et il n'était JAMAIS lu comme un
déclencheur. Une relation à +80 ne produisait pas plus qu'une relation à +10.
Personne ne venait jamais te trouver, te couvrir, ni te trahir.

Un chiffre qui ne produit rien n'est pas une relation : c'est une décoration.

CE QUE FAIT CE MODULE. Il surveille les FRANCHISSEMENTS de palier. Passer de
« neutre » à « proche », ou de « méfiant » à « hostile », est un événement
daté : il s'inscrit au journal, il devient un fait mémorisé, et il pose sur le
narrateur une consigne — « fais-le venir à toi », « prépare l'occasion ».

TROIS CHOIX QUI COMPTENT.

  ON NE RÉAGIT QU'AU FRANCHISSEMENT. Une relation stable à +80 ne doit pas
  faire surgir un dévouement à chaque tour. La consigne vit quelques tours,
  puis s'éteint : elle a eu sa chance.

  LE MOTEUR DÉTECTE, LE NARRATEUR DRAMATISE. On ne décide ni de la scène, ni
  des mots. On dit ce qui doit arriver ; le comment appartient au récit.

  AUCUN APPEL AU MODÈLE. Tout se lit dans le ruleset et dans la base.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import Campaign, Character, Event, MemoryFact, Relation
from app.rules.engine import Ruleset


def paliers(rs: Ruleset) -> list[dict]:
    """Tous les paliers déclarés, dans l'ordre du ruleset."""
    return list(rs.data.get("relations", {}).get("paliers", []))


def palier_de(rs: Ruleset, valeur: int) -> dict | None:
    """Le palier atteint par cette valeur, ou None si la relation est tiède.

    ON TESTE CHAQUE CÔTÉ DEPUIS SON EXTRÊME. Les parcourir dans un seul ordre
    faisait tomber une valeur de -90 dans « méfiant » (seuil -25) avant
    d'atteindre « hostile » (seuil -60) : la relation la plus sombre du jeu
    produisait la consigne la plus tiède.

    Le neutre n'a pas de palier, et c'est voulu : la plupart des gens que
    croise un genin ne pensent rien de lui.
    """
    table = paliers(rs)
    if valeur >= 0:
        hauts = sorted((p for p in table if int(p.get("seuil", 0)) >= 0),
                       key=lambda p: -int(p.get("seuil", 0)))
        return next((p for p in hauts if valeur >= int(p.get("seuil", 0))), None)
    bas = sorted((p for p in table if int(p.get("seuil", 0)) < 0),
                 key=lambda p: int(p.get("seuil", 0)))
    return next((p for p in bas if valeur <= int(p.get("seuil", 0))), None)


def verifier(session: Session, camp: Campaign, pj: Character,
             rs: Ruleset) -> list[str]:
    """Constate les franchissements et les inscrit. Rend les effets du tour.

    Ne regarde que les relations ORIENTÉES VERS le personnage qui joue : ce
    que les autres pensent de lui est ce qui peut lui arriver. Ce qu'il pense
    d'eux ne regarde que lui.
    """
    effets: list[str] = []
    liens = session.exec(select(Relation).where(
        Relation.campaign_id == camp.id,
        Relation.cible_id == pj.id)).all()

    for rel in liens:
        palier = palier_de(rs, rel.valeur)
        niveau = int(palier.get("niveau", 0)) if palier else 0
        if niveau == (rel.palier_vu or 0):
            continue

        source = session.get(Character, rel.source_id)
        if source is None or not source.vivant or source.is_pc:
            # On note tout de même le passage, pour ne pas le redéclencher.
            rel.palier_vu, rel.palier_tour = niveau, camp.tour
            session.add(rel)
            continue

        ancien = rel.palier_vu or 0
        rel.palier_vu, rel.palier_tour = niveau, camp.tour
        session.add(rel)

        if palier is None:
            # Retour au tiède : ça compte aussi, et ça se raconte.
            effets.append(f"{source.nom} ne sait plus quoi penser de toi.")
            session.add(Event(
                campaign_id=camp.id, tour=camp.tour, type="relation",
                resume=f"{source.nom} est redevenu neutre envers {pj.nom}.",
                importance=2, entites=[source.id, pj.id]))
            continue

        sens = "se rapproche" if niveau > ancien else "s'éloigne"
        nom = palier.get("nom", "")
        effets.append(f"{source.nom} {sens} — il te tient pour {nom}.")
        session.add(Event(
            campaign_id=camp.id, tour=camp.tour, type="relation",
            resume=f"{source.nom} tient désormais {pj.nom} pour {nom} "
                   f"({rel.valeur:+d}).",
            importance=4 if abs(niveau) >= 2 else 3,
            entites=[source.id, pj.id]))
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, nature="relation",
            importance=4 if abs(niveau) >= 2 else 3,
            texte=f"Au tour {camp.tour}, {source.nom} en est venu à tenir "
                  f"{pj.nom} pour {nom}. {rel.note}".strip(),
            entites=[source.id, pj.id]))
    return effets


def pressions(session: Session, camp: Campaign, pj: Character,
              rs: Ruleset) -> list[str]:
    """Les consignes encore actives, pour le contexte du narrateur.

    Uniquement les franchissements RÉCENTS. Une relation stable à +80 ne doit
    pas faire surgir un dévouement à chaque tour : la consigne vit quelques
    tours, puis s'éteint. Une pression permanente n'est plus une pression.
    """
    fenetre = int(rs.data.get("relations", {}).get("fenetre_pression", 3))
    table = {int(p["niveau"]): p for p in paliers(rs) if "niveau" in p}

    out: list[str] = []
    for rel in session.exec(select(Relation).where(
            Relation.campaign_id == camp.id,
            Relation.cible_id == pj.id)).all():
        niveau = rel.palier_vu or 0
        if not niveau or camp.tour - (rel.palier_tour or 0) > fenetre:
            continue
        palier = table.get(niveau)
        source = session.get(Character, rel.source_id)
        if palier is None or source is None or not source.vivant:
            continue
        out.append(" ".join(
            palier.get("intention", "").format(qui=source.nom).split()))
    return out
