"""Les relations qui agissent.

Le graphe social était tenu à jour avec soin et n'était jamais lu comme un
déclencheur. Une relation à +80 ne produisait pas plus qu'une relation à +10 :
personne ne venait jamais te trouver, te couvrir, ni te trahir. Un chiffre qui
ne produit rien n'est pas une relation, c'est une décoration.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import liens
from app.models import Campaign, Character, Event, MemoryFact, Relation
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=10,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True)
        pnj = Character(campaign_id=camp.id, nom="Hiroshi Tanaka", is_pc=False,
                        role_campagne="sensei")
        session.add(pj)
        session.add(pnj)
        session.commit()
        session.refresh(pj)
        session.refresh(pnj)
        rel = Relation(campaign_id=camp.id, source_id=pnj.id, cible_id=pj.id,
                       nature="mentor", valeur=0)
        session.add(rel)
        session.commit()
        session.refresh(rel)
        yield session, camp, pj, pnj, rel


def test_une_relation_tiede_ne_declenche_rien(partie, rs):
    session, camp, pj, _, rel = partie
    rel.valeur = 12
    session.add(rel)
    assert liens.verifier(session, camp, pj, rs) == []


def test_franchir_un_palier_est_un_evenement(partie, rs):
    session, camp, pj, pnj, rel = partie
    rel.valeur = 70
    session.add(rel)

    effets = liens.verifier(session, camp, pj, rs)
    session.commit()

    assert effets and pnj.nom in effets[0]
    assert rel.palier_vu == 2 and rel.palier_tour == camp.tour
    assert session.exec(select(Event).where(Event.type == "relation")).first()
    assert session.exec(select(MemoryFact).where(
        MemoryFact.nature == "relation")).first()


def test_une_relation_stable_ne_redeclenche_pas(partie, rs):
    """C'est tout l'enjeu : une relation à +80 ne doit pas faire surgir un
    dévouement à chaque tour."""
    session, camp, pj, _, rel = partie
    rel.valeur = 70
    session.add(rel)
    assert liens.verifier(session, camp, pj, rs)
    session.commit()

    camp.tour += 1
    assert liens.verifier(session, camp, pj, rs) == []
    camp.tour += 1
    rel.valeur = 85                       # toujours le même palier
    assert liens.verifier(session, camp, pj, rs) == []


def test_la_consigne_s_eteint_apres_quelques_tours(partie, rs):
    """Une pression permanente n'est plus une pression : elle a eu sa
    chance."""
    session, camp, pj, pnj, rel = partie
    rel.valeur = -70
    session.add(rel)
    liens.verifier(session, camp, pj, rs)
    session.commit()

    actives = liens.pressions(session, camp, pj, rs)
    assert actives and pnj.nom in actives[0]

    fenetre = rs.data["relations"]["fenetre_pression"]
    camp.tour += fenetre + 1
    assert liens.pressions(session, camp, pj, rs) == []


def test_la_consigne_dit_quoi_faire_arriver_pas_quoi_raconter(partie, rs):
    session, camp, pj, pnj, rel = partie
    rel.valeur = 70
    session.add(rel)
    liens.verifier(session, camp, pj, rs)
    session.commit()
    texte = liens.pressions(session, camp, pj, rs)[0]
    assert pnj.nom in texte
    assert "{qui}" not in texte, "le gabarit n'a pas été rempli"


def test_redescendre_au_tiede_compte_aussi(partie, rs):
    session, camp, pj, _, rel = partie
    rel.valeur = 70
    session.add(rel)
    liens.verifier(session, camp, pj, rs)
    session.commit()

    camp.tour += 1
    rel.valeur = 5
    session.add(rel)
    effets = liens.verifier(session, camp, pj, rs)
    assert effets and rel.palier_vu == 0
    assert liens.pressions(session, camp, pj, rs) == []


def test_les_paliers_suivent_les_seuils_du_ruleset(rs):
    assert liens.palier_de(rs, 0) is None
    assert liens.palier_de(rs, 30)["niveau"] == 1
    assert liens.palier_de(rs, 90)["niveau"] == 2
    assert liens.palier_de(rs, -30)["niveau"] == -1
    assert liens.palier_de(rs, -90)["niveau"] == -2


def test_ce_qui_pese_remonte_au_narrateur(partie, rs):
    from app.memory.context import _s_liens

    session, camp, pj, pnj, rel = partie
    rel.valeur = 70
    session.add(rel)
    liens.verifier(session, camp, pj, rs)
    session.commit()

    texte = _s_liens(session, camp, pj, rs)
    assert "CE QUI PÈSE" in texte and pnj.nom in texte
    assert "on le lui montre" in texte, \
        "le narrateur doit savoir qu'il ne faut pas l'annoncer"
