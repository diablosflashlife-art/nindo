"""Le tour de table — plusieurs joueurs déclarent, une seule scène résout.

RETOUR DE PARTIE. À deux, le jeu faisait jouer les personnages l'un après
l'autre, chacun déclenchant sa propre narration : la scène avançait deux fois,
chaque joueur attendait l'autre, et le récit ne mettait jamais les deux
personnages en présence. Autour d'une vraie table, ça ne se passe pas comme
ça : chacun dit ce que fait son personnage, puis le maître du jeu raconte ce
qui arrive À TOUS.

COMMENT. Chaque joueur passe par le MÊME chemin qu'en solo (`turn.arbitrer`
puis `turn.resoudre`) : son action est lue par l'arbitre, son jet est annoncé
puis lancé, sa séance d'entraînement avance. Les préparations sont ensuite
fusionnées en une seule, que le narrateur raconte en une scène, et `conclure`
écrit un seul tour. Rien n'est dupliqué : le tour de table n'est qu'une façon
de rassembler des tours solo.

LE GROUPE, C'EST LE LIEU. Deux personnages au même endroit partagent la scène ;
s'ils se séparent, chacun retrouve la sienne.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.engine import turn as moteur
from app.llm.prompts import NARRATEUR, NARRATEUR_COMBAT, pour_le_groupe
from app.lore.pack import LorePack
from app.models import Campaign, Character, Turn
from app.rules.engine import Ruleset


def groupe(session: Session, camp: Campaign, pj: Character) -> list[Character]:
    """Les personnages joueurs qui partagent la scène de `pj` (lui compris)."""
    pjs = session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.is_pc == True)  # noqa: E712
        .order_by(Character.id)).all()
    return [p for p in pjs if p.id == pj.id
            or (pj.location_id and p.location_id == pj.location_id)]


def dans_la_scene(tour: Turn, ids: set[int]) -> bool:
    """Ce tour concerne-t-il ce groupe ? L'ouverture concerne tout le monde."""
    if tour.index <= 1 or tour.character_id in ids:
        return True
    return any(j.get("id") in ids for j in (tour.resolution or {}).get("joueurs", []))


def fusionner(preps: list[tuple[Character, moteur.Preparation]]) -> moteur.Preparation:
    """Une seule préparation pour tous : chaque action, chaque résultat."""
    meneur, base = preps[0]
    en_combat = any(p.en_combat for _, p in preps)
    resolution = dict(base.resolution)
    resolution["joueurs"] = [
        {"id": pj.id, "nom": pj.nom, "joueur": pj.joueur or "", "action": p.action,
         "resolution": {k: v for k, v in p.resolution.items()
                        if k in ("check", "combat", "impossible", "entrainement",
                                 "arbitrage")}}
        for pj, p in preps]
    from app.memory.context import AVERTISSEMENT_AUTRES_PJ, AVERTISSEMENT_TOUR_DE_TABLE
    return moteur.Preparation(
        action="\n".join(f"{pj.nom} : {' '.join(p.action.split())}" for pj, p in preps),
        intent=base.intent,
        bloc="\n\n".join(f"— {pj.nom} —\n{p.bloc}" for pj, p in preps),
        resolution=resolution,
        systeme=pour_le_groupe(NARRATEUR_COMBAT if en_combat else NARRATEUR),
        # Le contexte est celui du meneur, où les autres joueurs sont présentés
        # comme « à ne pas faire agir ». Ce tour-ci, ils ont déclaré : on le dit.
        contexte_narrateur=base.contexte_narrateur.replace(
            AVERTISSEMENT_AUTRES_PJ, AVERTISSEMENT_TOUR_DE_TABLE),
        effets_combat=[f"{pj.nom} — {e}" for pj, p in preps for e in p.effets_combat],
        liens_techniques=[lt for _, p in preps for lt in p.liens_techniques],
        en_combat=en_combat,
        registre=base.registre,
        entre_deux=base.entre_deux and not en_combat,
        allure=base.allure,
        autres_pj=[],
        groupe=True,
        facteur_longueur=1.0 + 0.5 * (len(preps) - 1),
        participants=[pj.id for pj, _ in preps],
    )


def arbitrer(session: Session, camp: Campaign, entrees: list[dict], rs: Ruleset,
             pack: LorePack) -> list[tuple[int, moteur.Arbitrage]]:
    """L'annonce de chaque joueur, dans l'ordre des déclarations.

    `entrees` : [{"pj": Character, "action", "posture", "levier",
    "entrainement"}]. Rend des identifiants de personnage, pas des objets :
    l'annonce attend un clic entre deux requêtes."""
    out = []
    for e in entrees:
        pj = e["pj"]
        out.append((pj.id, moteur.arbitrer(
            session, camp, pj, e["action"], rs, pack,
            posture=e.get("posture", ""), levier=e.get("levier", ""),
            entrainement=e.get("entrainement", ""))))
    return out


def resoudre(session: Session, camp: Campaign,
             arbitrages: list[tuple[int, moteur.Arbitrage]], rs: Ruleset,
             pack: LorePack,
             choix: dict[int, dict] | None = None) -> tuple[Character, moteur.Preparation]:
    """Les dés de tous, puis une seule préparation. Rend (le personnage qui
    mène, la préparation fusionnée). `choix` : {id du personnage: {"technique",
    "forcer"}}, ce que chacun a décidé sur son annonce."""
    choix = choix or {}
    preps = []
    for pid, arb in arbitrages:
        pj = session.get(Character, pid)
        if pj is None:
            continue
        c = choix.get(pid) or {}
        preps.append((pj, moteur.resoudre(
            session, camp, pj, arb, rs, pack,
            technique=c.get("technique", ""), forcer=bool(c.get("forcer")))))
    if len(preps) == 1:
        return preps[0]
    return preps[0][0], fusionner(preps)


def preparer(session: Session, camp: Campaign, entrees: list[dict], rs: Ruleset,
             pack: LorePack) -> tuple[Character, moteur.Preparation]:
    """Annonce et dés d'un trait — le chemin « lancer tout seul »."""
    return resoudre(session, camp, arbitrer(session, camp, entrees, rs, pack), rs, pack)
