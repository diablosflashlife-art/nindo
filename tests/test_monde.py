"""L'horloge du monde.

Le prompt du narrateur affirme depuis le premier jour que « chaque PNJ
poursuit ses propres objectifs, même quand le joueur n'est pas là ». Rien, dans
le moteur, ne le rendait vrai : `Faction.objectifs` était écrit à l'amorce et
jamais relu. Le monde était un décor qui attendait.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import monde
from app.models import Campaign, Character, Event, Faction, MemoryFact
from app.rules.loader import charger


@pytest.fixture
def partie():
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=42, ruleset=charger("naruto").data,
                        tour=0, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        session.add(Faction(campaign_id=camp.id, nom="La Racine",
                            objectifs=["placer ses agents au bureau des missions"]))
        session.add(Character(
            campaign_id=camp.id, nom="Rin Kurosawa", is_pc=False,
            role_campagne="rival",
            objectifs=["être choisie pour l'examen avant tout le monde"]))
        session.add(Character(
            campaign_id=camp.id, nom="Kaito", is_pc=True,
            objectifs=["retrouver son père"]))
        session.commit()
        yield session, camp


def _avancer_jusqu_a(session, camp, tour):
    camp.tour = tour
    return monde.avancer(session, camp)


def test_le_monde_avance_a_sa_cadence(partie):
    session, camp = partie
    for t in range(2, monde.CADENCE):
        _avancer_jusqu_a(session, camp, t)
    assert session.exec(select(Event).where(Event.type == "monde")).all() == []

    _avancer_jusqu_a(session, camp, monde.CADENCE)
    session.commit()
    assert session.exec(select(Event).where(Event.type == "monde")).all()


def test_le_premier_tour_ne_declenche_rien(partie):
    """Une campagne ne s'ouvre pas sur une rumeur : le joueur n'a encore rien
    vu du monde."""
    session, camp = partie
    assert _avancer_jusqu_a(session, camp, 0) == []
    assert _avancer_jusqu_a(session, camp, 1) == []


def test_un_acteur_progresse_par_crans(partie):
    """Une faction ne réussit pas d'un coup : elle prépare, elle agit, elle
    obtient — et le joueur peut s'en mêler entre deux."""
    session, camp = partie
    vus = []
    for i in range(1, 7):
        _avancer_jusqu_a(session, camp, monde.CADENCE * i)
        session.commit()
    evts = session.exec(select(Event).where(Event.type == "monde")
                        .order_by(Event.tour)).all()
    assert len(evts) >= 3
    for e in evts:
        vus.append(e.resume)
    assert any("prépare" in r for r in vus)
    assert any("passé à l'acte" in r for r in vus), \
        "aucun acteur n'a jamais dépassé la préparation"


def test_le_monde_ne_touche_jamais_au_joueur(partie):
    """Un monde qui punit dans le dos est injuste ; un monde qui BOUGE dans
    le dos est vivant."""
    session, camp = partie
    pj = session.exec(select(Character).where(
        Character.is_pc == True)).first()  # noqa: E712
    avant = (pj.xp, pj.niveau, dict(pj.ressources), list(pj.etats))

    for i in range(1, 6):
        _avancer_jusqu_a(session, camp, monde.CADENCE * i)
    session.commit()
    session.refresh(pj)
    assert (pj.xp, pj.niveau, dict(pj.ressources), list(pj.etats)) == avant


def test_le_monde_n_invente_aucun_nom(partie):
    """Même règle que partout : on ne travaille que sur des entités déjà en
    base."""
    session, camp = partie
    connus = {"La Racine", "Rin Kurosawa"}
    for i in range(1, 8):
        _avancer_jusqu_a(session, camp, monde.CADENCE * i)
    session.commit()
    for e in session.exec(select(Event).where(Event.type == "monde")).all():
        assert any(e.resume.startswith(n) for n in connus), e.resume


def test_le_personnage_joueur_n_est_jamais_un_acteur(partie):
    """Le joueur poursuit ses objectifs en jouant, pas dans le dos du
    joueur."""
    session, camp = partie
    for i in range(1, 8):
        _avancer_jusqu_a(session, camp, monde.CADENCE * i)
    session.commit()
    for e in session.exec(select(Event).where(Event.type == "monde")).all():
        assert not e.resume.startswith("Kaito")


def test_le_monde_est_reproductible(partie):
    """Rejouer une campagne depuis sa graine doit redonner le même monde."""
    session, camp = partie
    camp.tour = monde.CADENCE
    a = monde._rng(camp).random()
    b = monde._rng(camp).random()
    assert a == b
    camp.tour = monde.CADENCE * 2
    assert monde._rng(camp).random() != a


def test_ce_qui_a_bouge_remonte_au_narrateur(partie):
    session, camp = partie
    _avancer_jusqu_a(session, camp, monde.CADENCE)
    session.commit()
    assert monde.rumeurs_recentes(session, camp)

    from app.memory.context import _s_monde
    texte = _s_monde(session, camp)
    assert "CE QUI A BOUGÉ" in texte
    assert "n'était pas là" in texte, \
        "le narrateur doit savoir que le joueur n'a pas assisté à ça"


def test_une_campagne_sans_objectifs_ne_produit_rien(partie):
    session, camp = partie
    for f in session.exec(select(Faction)).all():
        f.objectifs = []
        session.add(f)
    for c in session.exec(select(Character)).all():
        c.objectifs = []
        session.add(c)
    session.commit()
    assert _avancer_jusqu_a(session, camp, monde.CADENCE) == []
    assert session.exec(select(MemoryFact)).all() == []
