"""L'épreuve des clochettes — chantier E de Nindō 2.0.

La première mission apprend le jeu : à l'acte 2 l'instructeur attaque, la
rencontre porte les objectifs de l'épreuve (pas ceux du fossé), l'instructeur
ne « disparaît » pas quand elle se referme, et faire équipe conclut la
mission.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import clochettes, combat, missions
from app.engine import campaign as amorce
from app.lore.pack import charger as charger_pack
from app.models import Campaign, Character, Encounter, Location, Quest
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs, pack):
    bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(bd)
    with Session(bd) as session:
        camp = Campaign(nom="test", graine=4, ruleset=rs.data, tour=0, phase="amorce")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Terrain 7", danger=1)
        session.add(lieu)
        session.commit()
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True, location_id=lieu.id,
                       stats=rs.stats_defaut(), ressources=rs.ressources_defaut(), tier=2)
        sensei = Character(campaign_id=camp.id, nom="Gorōmu", is_pc=False, grade="jonin",
                           role_campagne="sensei", location_id=lieu.id, niveau=12,
                           stats={k: v + 12 for k, v in rs.stats_defaut().items()},
                           ressources=rs.ressources_defaut())
        session.add_all([pj, sensei])
        session.commit()
        res = rs.puissance(sensei.stats, [], sensei.niveau)
        sensei.pe, sensei.tier = res["pe"], res["tier"]
        session.add(sensei)
        session.commit()
        amorce.amorcer_recit(session, camp, pack, rs, pj, [sensei])
        yield session, camp, pj, sensei


def test_la_premiere_mission_est_l_epreuve(partie):
    session, camp, pj, sensei = partie
    q = clochettes.quete(session, camp)
    assert q is not None and q.titre == "L'épreuve des clochettes"
    assert q.donneur_id == sensei.id and q.archetype == "clochettes"
    assert missions.acte(q, 1)["nom"] == "Les clochettes"
    assert missions.acte(q, 3)["nom"] == "Seuls contre lui"
    assert missions.acte(q, 8)["nom"] == "Le verdict"


def test_l_instructeur_attaque_a_l_acte_2(partie, rs, pack):
    session, camp, pj, sensei = partie
    assert clochettes.declencher(session, camp, pack, rs, pj) is None, "pas avant l'acte 2"
    camp.tour = 3
    renc = clochettes.declencher(session, camp, pack, rs, pj)
    assert renc is not None and renc.camp_adverse == [sensei.id]
    assert sensei.id not in renc.camp_joueur, "pas des deux côtés"
    assert renc.objectifs == clochettes.OBJECTIFS and renc.objectif == "faire équipe"
    assert sensei.tier > pj.tier and not renc.fosse.get("jet", True), \
        "l'instructeur est hors de portée : c'est la leçon"
    assert clochettes.declencher(session, camp, pack, rs, pj) is None, "une seule fois"


def test_les_objectifs_de_l_epreuve_tiennent_pendant_le_round(partie, rs, pack):
    session, camp, pj, sensei = partie
    camp.tour = 3
    renc = clochettes.declencher(session, camp, pack, rs, pj)
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "manoeuvre", "levier": "terrain_prepare",
                             "objectif": "faire équipe"})
    session.refresh(renc)
    assert renc.objectifs == clochettes.OBJECTIFS
    assert renc.objectif in clochettes.OBJECTIFS
    assert any("hors de portée" in l for l in issue["lignes"]) or renc.objectif


def test_faire_equipe_conclut_la_mission_et_garde_l_instructeur(partie, rs, pack):
    session, camp, pj, sensei = partie
    camp.tour = 3
    renc = clochettes.declencher(session, camp, pack, rs, pj)
    renc.progres = 2
    session.add(renc)
    session.commit()
    sortie = combat._clore(session, camp, rs, renc, "dispersee", pj, pack)
    q = session.exec(select(Quest).where(Quest.archetype == "clochettes")).first()
    assert q.statut == "réussie" and q.note
    assert any("réussie" in e for e in sortie["effets"])
    session.refresh(sensei)
    assert sensei.location_id == pj.location_id, "l'instructeur reste au village"


def test_sans_faire_equipe_l_epreuve_echoue(partie, rs, pack):
    session, camp, pj, sensei = partie
    camp.tour = 3
    renc = clochettes.declencher(session, camp, pack, rs, pj)
    combat._clore(session, camp, rs, renc, "perdue", pj, pack)
    q = session.exec(select(Quest).where(Quest.archetype == "clochettes")).first()
    assert q.statut == "échouée"
