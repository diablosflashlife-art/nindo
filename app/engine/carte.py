"""La carte et le voyage.

CE QUI MANQUAIT. Les lieux avaient une région, un danger, un terrain et des
tags — mais aucune POSITION, et rien pour aller de l'un à l'autre. Le joueur
changeait d'endroit quand la narration le décidait, sans que cela coûte jamais
rien. Un monde dont on traverse la moitié sans y penser n'a pas de géographie :
il n'a qu'un décor qui change.

CE QUE LE VOYAGE DOIT COÛTER.

  DU TEMPS. La distance se paie en tours de campagne, et ces tours-là comptent
  pour les échéances de mission. Aller chercher une plante à deux jours de
  marche quand il reste trois tours est désormais une décision.

  DU RISQUE. Arriver dans un lieu dangereux, c'est parfois y arriver mal. On
  laisse le moteur d'embuscade décider, avec les mêmes règles qu'en jeu —
  plancher de danger et carence compris.

CE QUE LE VOYAGE NE FAIT PAS. Il ne raconte rien. Le déplacement produit un
fait et un événement ; c'est le tour suivant, narré normalement, qui dira à
quoi ressemblait la route.
"""
from __future__ import annotations

import math

from sqlmodel import Session, select

from app.engine import francais as fr
from app.models import (Campaign, Character, Event, Location, MemoryFact,
                        maintenant)
from app.rules.engine import Ruleset


class VoyageImpossible(RuntimeError):
    """Le déplacement est refusé, et la raison est destinée au joueur."""


# Les lieux de l'amorce, par nom. Sert à replacer les campagnes commencées
# avant que les lieux aient des coordonnées : sans ça, leur carte serait vide
# pour toujours, et une sauvegarde doit survivre aux mises à jour du jeu.
#
# Ce sont les noms de l'ANCIENNE amorce, commune à tous les villages. Les
# campagnes récentes reçoivent leurs lieux et leurs coordonnées de
# `lore/naruto/lieux.yaml`, un jeu par village : cette table ne sert plus
# qu'aux sauvegardes d'avant.
PLACES_CONNUES = {
    "académie": (44, 58),
    "tour du kage": (53, 49),
    "quartier marchand": (35, 66),
    "terrain d'entraînement 3": (64, 70),
    "forêt frontalière": (76, 27),
    "poste frontière du nord": (58, 13),
}

# Où poser un lieu dont on ne sait rien. Une spirale douce autour du centre,
# indexée par l'ordre d'apparition : déterministe, donc stable d'un
# rechargement à l'autre, et jamais deux lieux au même endroit.
def _place_par_defaut(rang: int) -> tuple[int, int]:
    angle = 2.399963 * rang            # l'angle d'or, qui étale sans motif
    rayon = 12 + 5.2 * math.sqrt(rang)
    return (int(50 + rayon * math.cos(angle)),
            int(50 + rayon * math.sin(angle)))


def placer(session: Session, camp: Campaign) -> None:
    """Donne une position aux lieux qui n'en ont pas.

    Les campagnes d'avant la carte ont des lieux sans coordonnées. On les pose
    ici plutôt que dans une migration : le nom suffit à retrouver la
    géographie du village, et ce qu'on ne reconnaît pas prend une place
    déterministe. Écrit une fois, puis plus jamais.
    """
    tous = list(session.exec(select(Location).where(
        Location.campaign_id == camp.id).order_by(Location.id)).all())
    occupees = {(l.x, l.y) for l in tous if l.x or l.y}
    change = False

    for rang, l in enumerate(tous):
        if l.x or l.y:
            continue
        pos = PLACES_CONNUES.get((l.nom or "").strip().lower())
        if pos is None or pos in occupees:
            pos = _place_par_defaut(rang)
            while pos in occupees:
                rang += 1
                pos = _place_par_defaut(rang)
        l.x, l.y = pos
        occupees.add(pos)
        session.add(l)
        change = True

    if change:
        session.commit()


def decouvrir(session: Session, camp: Campaign, lieu: Location,
              raison: str) -> str:
    """Fait entrer un lieu sur la carte du joueur.

    Le champ `connu` existait et valait vrai partout : le monde entier était
    donné d'emblée, et la carte n'avait aucune raison de grandir. Un endroit
    se découvre maintenant parce qu'on y est envoyé, parce qu'on en a entendu
    parler, ou parce qu'on y est allé.

    Rend la phrase destinée au joueur, ou une chaîne vide s'il connaissait
    déjà l'endroit — appeler cette fonction deux fois ne doit rien produire.
    """
    if lieu.connu:
        return ""
    lieu.connu = True
    session.add(lieu)
    nomme = fr.determine(lieu.nom)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="decouverte",
        resume=f"{fr.majuscule(nomme)} entre sur la carte : {raison}.",
        importance=3))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, nature="lieu", importance=3,
        texte=f"{fr.majuscule(nomme)} est désormais un lieu connu du groupe "
              f"({raison}, tour {camp.tour})."))
    return f"Nouveau lieu connu : {lieu.nom}."


def lieux(session: Session, camp: Campaign) -> list[Location]:
    """Les lieux connus, tous placés. Un lieu ignoré du joueur reste hors de
    la carte : c'est ce qui permet d'en instancier sans les révéler."""
    placer(session, camp)
    return [l for l in session.exec(select(Location).where(
        Location.campaign_id == camp.id).order_by(Location.id)).all()
        if l.connu and (l.x or l.y)]


def distance(a: Location, b: Location) -> float:
    return math.hypot((a.x or 0) - (b.x or 0), (a.y or 0) - (b.y or 0))


def cout(rs: Ruleset, depart: Location | None, arrivee: Location) -> int:
    """Le prix du trajet, en tours de campagne.

    Borné des deux côtés : un pas de côté coûte au moins un tour — le temps
    existe — et la traversée la plus longue ne doit pas engloutir une échéance
    entière d'un seul coup.
    """
    cfg = rs.data.get("voyage", {})
    if depart is None:
        return int(cfg.get("tours_min", 1))
    brut = distance(depart, arrivee) * float(cfg.get("tours_par_unite", 0.08))
    return max(int(cfg.get("tours_min", 1)),
               min(int(cfg.get("tours_max", 6)), round(brut)))


def itineraire(session: Session, camp: Campaign, rs: Ruleset,
               pj: Character) -> list[dict]:
    """La carte telle que le joueur la voit : chaque lieu, son danger, et ce
    que coûterait d'y aller depuis là où il est."""
    ici = session.get(Location, pj.location_id) if pj.location_id else None
    out = []
    for l in lieux(session, camp):
        out.append({
            "lieu": l,
            "ici": ici is not None and l.id == ici.id,
            "cout": 0 if (ici is not None and l.id == ici.id)
                    else cout(rs, ici, l),
        })
    return out


def voyager(session: Session, camp: Campaign, pack, rs: Ruleset,
            pj: Character, destination_id: int) -> list[str]:
    """Déplace le personnage, avance l'horloge, et voit ce qui l'attend.

    L'ordre compte : on arrive D'ABORD, puis on tire l'embuscade. Un guet-apens
    se subit à l'arrivée, sur le danger du lieu où l'on met les pieds, pas sur
    celui d'où l'on vient.
    """
    from app.engine import combat
    from app.engine import missions as gen_missions

    if combat.active(session, camp) is not None:
        raise VoyageImpossible(
            "On ne quitte pas un affrontement en marchant. Romps le contact "
            "d'abord.")

    arrivee = session.get(Location, destination_id)
    if arrivee is None or arrivee.campaign_id != camp.id:
        raise VoyageImpossible("Ce lieu n'existe pas dans cette campagne.")
    if not arrivee.connu:
        raise VoyageImpossible("Tu ne connais pas encore cet endroit.")
    if pj.location_id == arrivee.id:
        raise VoyageImpossible("Tu y es déjà.")

    depart = session.get(Location, pj.location_id) if pj.location_id else None
    tours = cout(rs, depart, arrivee)

    pj.location_id = arrivee.id
    camp.tour += tours
    # Voyager, c'est jouer : sans ça, une session passée à traverser le pays ne
    # touchait pas `joue_le` et l'accueil reléguait la campagne en bas de liste.
    camp.joue_le = maintenant()
    session.add(pj)
    session.add(camp)

    # « à la Tour du Kage », pas « à Tour du Kage ». Voir francais.py : un nom
    # de lieu est un nom commun déterminé, et l'article se contracte.
    effets = [f"Voyage {fr.vers(arrivee.nom)} — {fr.accorde(tours, 'tour')}."]
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="deplacement",
        resume=f"{pj.nom} s'est rendu {fr.a(arrivee.nom)}"
               + (f" {fr.depuis(depart.nom)}." if depart else "."),
        importance=2, entites=[pj.id]))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, importance=2, nature="lieu",
        texte=f"{pj.nom} s'est rendu {fr.a(arrivee.nom)}"
              + (f" {fr.depuis(depart.nom)}" if depart else "")
              + f" (tour {camp.tour}).",
        entites=[pj.id]))

    # Le monde est petit : une connaissance peut se trouver là où on arrive.
    # Seules les personnes déjà promues sont candidates — un figurant reste où
    # il est, et c'est ce qui distingue un décor d'un personnage.
    from app.engine import crystallize

    effets += crystallize.recroiser(session, camp, pj, arrivee.id)

    # Le temps a passé : les délais promis se vérifient, les blessures se
    # referment. Un trajet de six tours n'est pas un trajet gratuit.
    effets += gen_missions.verifier_echeances(session, camp)
    effets += combat.soigner_le_temps(session, camp, pj, rs)
    for _ in range(tours):
        effets += combat.recuperer(session, camp, rs, pj)
    session.commit()

    guet = combat.tirer_embuscade(session, camp, pack, rs, pj)
    if guet is not None:
        effets.append(f"Tu n'arrives pas seul : {guet.titre}")
    session.commit()
    return effets
