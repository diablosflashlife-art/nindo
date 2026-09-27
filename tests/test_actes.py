"""La mission en actes et le débrief — chantier C de Nindō 2.0 (pilier 3).

Trois actes mesurés depuis l'engagement, la complication gardée pour l'acte
2, une note de S à D au débrief, des ryô versés à toute l'équipe, une
réputation qui bouge.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.engine import missions
from app.memory import context
from app.models import Campaign, Character, Condition, Quest
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
def partie(rs):
    bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(bd)
    with Session(bd) as session:
        camp = Campaign(nom="test", graine=5, ruleset=rs.data, tour=10, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        a = Character(campaign_id=camp.id, nom="Kaito", is_pc=True, ryo=500,
                      stats=rs.stats_defaut(), ressources={**rs.ressources_defaut(), "pv_max": 40})
        b = Character(campaign_id=camp.id, nom="Hana", is_pc=True, ryo=200,
                      stats=rs.stats_defaut(), ressources={**rs.ressources_defaut(), "pv_max": 40})
        session.add_all([a, b])
        session.commit()
        q = Quest(campaign_id=camp.id, titre="Le puits empoisonné", rang="C",
                  statut="acceptée", debut_tour=10, echeance_tour=26,
                  complication="Quelqu'un a versé le poison volontairement.")
        session.add(q)
        session.commit()
        session.refresh(q)
        yield session, camp, a, b, q


def test_les_trois_actes_se_suivent(partie):
    session, camp, a, b, q = partie
    assert missions.acte(q, 10)["nom"] == "Approche"
    assert missions.acte(q, 14)["nom"] == "Complication"
    assert "versé le poison" in missions.acte(q, 14)["consigne"]
    assert missions.acte(q, 18)["nom"] == "Dénouement"
    q.statut = "réussie"
    assert missions.acte(q, 18) is None


def test_la_complication_arrive_au_narrateur_a_l_acte_2(partie):
    session, camp, a, b, q = partie
    camp.tour = 14
    texte = context._s_missions(session, camp)
    assert "Acte 2 — Complication" in texte and "versé le poison" in texte
    camp.tour = 11
    assert "versé le poison" not in context._s_missions(session, camp)


def test_une_mission_vite_faite_et_propre_vaut_un_s(partie):
    session, camp, a, b, q = partie
    camp.tour = 14                      # 4 tours sur une fenêtre de 16 : vite
    assert missions.noter(session, camp, q, "réussie") == "S"


def test_quelqu_un_a_terre_fait_baisser_la_note(partie):
    session, camp, a, b, q = partie
    camp.tour = 24
    session.add(Condition(campaign_id=camp.id, character_id=b.id, code="hors_combat",
                          libelle="Relevé de justesse", depuis_tour=15))
    session.commit()
    assert missions.noter(session, camp, q, "réussie") == "C"
    assert missions.noter(session, camp, q, "échouée") == "D"


def test_le_debrief_paie_toute_l_equipe_et_note_la_reputation(partie, rs):
    session, camp, a, b, q = partie
    camp.tour = 14
    effets = missions.changer_statut(session, camp, a, rs, q, "réussie")
    session.commit()
    assert q.note == "S" and q.ryo == 1200          # 800 × 1,5
    assert a.ryo == 1700 and b.ryo == 1400, "chacun reçoit la paie"
    assert a.reputation == 4 and b.reputation == 4
    assert "note S" in q.rapport and "1200 ryô" in q.rapport
    assert any(e.startswith("Débrief : note S") for e in effets)
    # Une seconde clôture ne repaie pas.
    assert missions.changer_statut(session, camp, a, rs, q, "réussie") == []


def test_un_echec_coute_de_la_reputation(partie, rs):
    session, camp, a, b, q = partie
    missions.changer_statut(session, camp, a, rs, q, "échouée")
    assert q.note == "D" and q.ryo == 0
    assert a.reputation == -1 and a.ryo == 500
