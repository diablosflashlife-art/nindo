"""Nindō, liens, épuisement, examen chûnin — chantier D de Nindō 2.0 (pilier 4).

Le jeu de rôle lui-même rapporte : tenir son nindō, faire avancer un lien.
Le chakra bas pèse sur les jets. L'examen s'ouvre quand il est mérité, a ses
trois épreuves, et promeut toute l'équipe.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import combat, missions
from app.engine import turn as moteur
from app.engine.validators import valider_consequences
from app.lore.pack import charger as charger_pack
from app.memory import context
from app.models import Campaign, Character, Event, Location, Quest, Relation
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs):
    bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(bd)
    with Session(bd) as session:
        camp = Campaign(nom="test", graine=9, ruleset=rs.data, tour=12, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Village")
        session.add(lieu)
        session.commit()
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True, niveau=3,
                       location_id=lieu.id, nindo="Personne ne reste derrière, jamais.",
                       stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
        sensei = Character(campaign_id=camp.id, nom="Gorōmu", is_pc=False,
                           role_campagne="sensei", location_id=lieu.id,
                           stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
        session.add_all([pj, sensei])
        session.commit()
        session.add(Relation(campaign_id=camp.id, source_id=sensei.id, cible_id=pj.id,
                             nature="sensei", valeur=10, lien=True))
        session.commit()
        yield session, camp, pj, sensei


def _net(session, camp, **extra):
    brut = {"faits": [], "relations": [], "quetes": [], "ressources": [], "xp": 0,
            "propositions": [], **extra}
    net, _ = valider_consequences(session, camp, brut)
    return net


# ==========================================================================
# LE JEU DE RÔLE RAPPORTE
# ==========================================================================
def test_tenir_son_nindo_rapporte_une_fois_par_fenetre(partie, rs):
    session, camp, pj, _ = partie
    e1 = moteur._recompenser_le_jeu(session, camp, pj, _net(session, camp, nindo_joue=True), rs)
    assert any(e.startswith("Nindō tenu") for e in e1) and pj.xp == 5
    session.commit()
    e2 = moteur._recompenser_le_jeu(session, camp, pj, _net(session, camp, nindo_joue=True), rs)
    assert e2 == [] and pj.xp == 5, "pas deux fois dans la même fenêtre"
    camp.tour += 3
    e3 = moteur._recompenser_le_jeu(session, camp, pj, _net(session, camp, nindo_joue=True), rs)
    assert e3 and pj.xp == 10


def test_un_lien_avance_rapporte_et_resserre(partie, rs):
    session, camp, pj, sensei = partie
    net = _net(session, camp, lien_joue="Gorōmu")
    assert net["lien_joue"] == sensei.id
    effets = moteur._recompenser_le_jeu(session, camp, pj, net, rs)
    assert any("Lien avec Gorōmu" in e for e in effets) and pj.xp == 5
    rel = session.exec(select(Relation).where(Relation.source_id == sensei.id)).first()
    assert rel.valeur == 13


def test_un_lien_inconnu_ne_rapporte_rien(partie, rs):
    session, camp, pj, _ = partie
    net = _net(session, camp, lien_joue="Madara")
    assert net["lien_joue"] is None
    assert moteur._recompenser_le_jeu(session, camp, pj, net, rs) == []


def test_le_narrateur_connait_le_nindo_et_les_liens(partie, rs, pack):
    session, camp, pj, _ = partie
    fiche = context._s_fiche(session, camp, pj, rs, pack)
    assert "Nindō (sa règle) : « Personne ne reste derrière, jamais. »" in fiche
    assert "Liens : Gorōmu" in fiche


# ==========================================================================
# L'ÉPUISEMENT
# ==========================================================================
def test_le_chakra_bas_pese_sur_les_jets(partie, rs, pack):
    session, camp, pj, _ = partie
    assert combat.malus_blessures(session, rs, pj) == 0
    pj.ressources = {**pj.ressources, "chakra": 3}
    session.add(pj)
    session.commit()
    assert combat.epuise(rs, pj)
    assert combat.malus_blessures(session, rs, pj) == combat.MALUS_EPUISEMENT
    a = moteur.arbitrer(session, camp, pj, "J'escalade le mur.", rs, pack)
    assert {"libelle": "épuisé", "valeur": -2} in a.bonus


# ==========================================================================
# L'EXAMEN CHÛNIN
# ==========================================================================
def test_l_examen_s_ouvre_quand_il_est_merite(partie, rs):
    session, camp, pj, _ = partie
    assert not missions.examen_ouvert(session, camp, rs, pj), "aucune mission réussie"
    for i in range(2):
        session.add(Quest(campaign_id=camp.id, titre=f"Mission {i}", statut="réussie"))
    session.commit()
    assert missions.examen_ouvert(session, camp, rs, pj)
    pj.niveau = 1
    assert not missions.examen_ouvert(session, camp, rs, pj), "trop jeune"
    pj.niveau = 3
    pj.grade = "chunin"
    assert not missions.examen_ouvert(session, camp, rs, pj), "déjà chûnin"


def test_l_examen_a_ses_trois_epreuves_et_promeut(partie, rs, pack):
    session, camp, pj, _ = partie
    lieux = session.exec(select(Location)).all()
    oss = missions.ossature(pack, rs, camp, pj, list(lieux), archetype=missions.EXAMEN)
    assert oss["archetype"] == missions.EXAMEN and oss["rang"] == "C"
    assert oss["complication"]
    assert missions.EXAMEN not in [a["id"] for a in pack.archetypes_mission("C")], \
        "jamais tiré au hasard"
    q = Quest(campaign_id=camp.id, titre="Examen chûnin", rang="C", statut="acceptée",
              debut_tour=camp.tour, echeance_tour=camp.tour + 16,
              archetype=missions.EXAMEN, complication=oss["complication"])
    session.add(q)
    session.commit()
    assert missions.acte(q, camp.tour)["nom"] == "Épreuve écrite"
    assert missions.acte(q, camp.tour + 5)["nom"] == "Forêt de la mort"
    assert missions.acte(q, camp.tour + 10)["nom"] == "Tournoi"
    assert missions.acte(q, camp.tour)["noms"] == ["Épreuve écrite", "Forêt de la mort", "Tournoi"]
    camp.tour += 11
    effets = missions.changer_statut(session, camp, pj, rs, q, "réussie")
    assert any("promu Chûnin" in e for e in effets)
    assert pj.grade == "chunin"
    assert session.exec(select(Event).where(Event.type == "promotion")).first() is not None
