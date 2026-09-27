"""Les missions, et surtout leurs DÉLAIS.

`Quest.echeance_tour` était posé à la création de chaque mission — l'amorce
promet même qu'« une équipe jugée inapte est dissoute » sous douze tours — et
rien ne le comparait jamais à l'horloge de campagne. Une échéance qui ne tombe
jamais est pire qu'une absence d'échéance : elle apprend au joueur que rien de
ce qu'on lui annonce n'a de conséquence.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import missions
from app.models import Campaign, Event, MemoryFact, Quest
from app.rules.loader import charger


@pytest.fixture
def partie():
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=charger("naruto").data,
                        tour=10, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        yield session, camp


def _quete(session, camp, titre, echeance, statut="acceptée"):
    q = Quest(campaign_id=camp.id, titre=titre, statut=statut,
              echeance_tour=echeance, description="…")
    session.add(q)
    session.commit()
    session.refresh(q)
    return q


def test_une_echeance_depassee_fait_echouer_la_mission(partie):
    session, camp = partie
    q = _quete(session, camp, "Le sanglier du charbonnier", echeance=9)

    effets = missions.verifier_echeances(session, camp)
    session.commit()

    assert q.statut == "échouée"
    assert any("délai" in e.lower() for e in effets)
    # L'échec laisse une trace opposable : le monde s'en souvient.
    assert session.exec(select(Event).where(Event.campaign_id == camp.id)).first()
    assert session.exec(select(MemoryFact).where(
        MemoryFact.campaign_id == camp.id)).first()


def test_une_mission_dans_les_temps_n_est_pas_touchee(partie):
    session, camp = partie
    q = _quete(session, camp, "Escorte du convoi", echeance=14)
    assert missions.verifier_echeances(session, camp) == []
    assert q.statut == "acceptée"


def test_le_jour_meme_de_l_echeance_compte_encore(partie):
    """On échoue APRÈS le délai, pas pendant. Le tour de l'échéance est le
    dernier tour utile — c'est ce qu'annonce le décompte au joueur."""
    session, camp = partie
    q = _quete(session, camp, "Livraison", echeance=10)
    assert missions.verifier_echeances(session, camp) == []
    assert q.statut == "acceptée"

    camp.tour = 11
    missions.verifier_echeances(session, camp)
    assert q.statut == "échouée"


def test_une_mission_close_ne_retombe_pas(partie):
    session, camp = partie
    q = _quete(session, camp, "Déjà rendue", echeance=2, statut="réussie")
    assert missions.verifier_echeances(session, camp) == []
    assert q.statut == "réussie"


def test_une_mission_sans_echeance_ne_tombe_jamais(partie):
    session, camp = partie
    q = _quete(session, camp, "Enquête ouverte", echeance=None)
    camp.tour = 900
    assert missions.verifier_echeances(session, camp) == []
    assert q.statut == "acceptée"


def test_le_decompte_rendu_au_joueur(partie):
    session, camp = partie
    proche = _quete(session, camp, "Urgent", echeance=11)
    lointain = _quete(session, camp, "Tranquille", echeance=30)
    sans = _quete(session, camp, "Sans délai", echeance=None)

    assert missions.reste(proche, camp.tour) == 1
    assert missions.reste(lointain, camp.tour) == 20
    assert missions.reste(sans, camp.tour) is None
    assert {q.titre for q in missions.ouvertes(session, camp)} == {
        "Urgent", "Tranquille", "Sans délai"}
