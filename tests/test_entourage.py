"""Les PNJ qui reviennent — un visage croisé souvent finit par être quelqu'un.

`enrichir()` était écrit, `settings.seuil_complet` déclaré, et rien n'appelait
ni l'un ni l'autre : un figurant recroisé dix fois restait « figurant ». Pas de
relation, donc absent du panneau de l'entourage ; pas d'objectif connu, donc
invisible au narrateur. C'est pourtant de là que vient le sentiment d'un monde
plutôt que d'un décor.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings
from app.engine import crystallize
from app.lore.pack import charger as charger_pack
from app.models import (Campaign, Character, Event, Knowledge, Location,
                        MemoryFact, Relation)
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=3, ruleset=rs.data, tour=12,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        ici = Location(campaign_id=camp.id, nom="Quartier marchand")
        ailleurs = Location(campaign_id=camp.id, nom="Académie")
        session.add(ici)
        session.add(ailleurs)
        session.commit()
        session.refresh(ici)
        session.refresh(ailleurs)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       location_id=ici.id)
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj, ici, ailleurs


def _figurant(session, camp, lieu, nom="Tetsuo", apparitions=1):
    c = Character(campaign_id=camp.id, nom=nom, location_id=lieu.id,
                  source="genere", role_campagne="figurant",
                  apparitions=apparitions)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


@pytest.fixture(autouse=True)
def sans_modele(monkeypatch):
    """La promotion appelle `enrichir`, qui appelle le modèle. Ici on teste ce
    que la promotion ÉCRIT, pas ce que le modèle raconte."""
    monkeypatch.setattr(crystallize, "enrichir",
                        lambda session, camp, pack, rs, pnj: pnj)


# ==========================================================================
# LA PROMOTION
# ==========================================================================
def test_un_figurant_a_peine_croise_reste_un_figurant(rs, pack, partie):
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=1)
    assert crystallize.promouvoir(session, camp, pack, rs, pj, [c]) == []
    assert c.role_campagne == "figurant"


def test_au_bout_de_trois_fois_il_devient_quelqu_un(rs, pack, partie):
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=settings.seuil_complet)
    effets = crystallize.promouvoir(session, camp, pack, rs, pj, [c])

    assert effets and "Tetsuo" in effets[0]
    assert c.role_campagne == crystallize.ROLE_PROMU


def test_la_promotion_cree_la_relation_qui_le_rend_visible(rs, pack, partie):
    """Sans relation, la promotion ne se verrait nulle part : c'est elle qui
    fait entrer le personnage dans le panneau de l'entourage."""
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])

    rel = session.exec(select(Relation).where(
        Relation.source_id == c.id, Relation.cible_id == pj.id)).first()
    assert rel is not None and rel.valeur > 0
    assert session.exec(select(Knowledge)).all()
    assert session.exec(select(MemoryFact)).all(), \
        "sans fait mémorisé, le narrateur ignore qu'il le connaît"
    assert session.exec(select(Event).where(Event.type == "entourage")).all()


def test_une_seule_promotion_par_tour(rs, pack, partie):
    """Promouvoir trois personnes dans la même scène les banalise toutes."""
    session, camp, pj, ici, _ = partie
    a = _figurant(session, camp, ici, "Tetsuo", 5)
    b = _figurant(session, camp, ici, "Mio", 5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [a, b])
    promus = [x for x in (a, b) if x.role_campagne == crystallize.ROLE_PROMU]
    assert len(promus) == 1


def test_on_ne_promeut_pas_deux_fois(rs, pack, partie):
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])
    assert crystallize.promouvoir(session, camp, pack, rs, pj, [c]) == []
    assert len(session.exec(select(Relation)).all()) == 1


def test_un_personnage_ecrit_a_la_main_n_est_pas_promu(rs, pack, partie):
    """Le sensei et les coéquipiers ont déjà leur place : la promotion ne
    concerne que ce que le jeu a généré en chemin."""
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=9)
    c.source = "partie"
    c.role_campagne = "sensei"
    session.commit()
    assert crystallize.promouvoir(session, camp, pack, rs, pj, [c]) == []


def test_un_promu_n_est_plus_reemploye_comme_silhouette(rs, pack, partie):
    """`reemployer` sert à recycler des figurants. Quelqu'un qu'on connaît
    n'est plus une silhouette interchangeable."""
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])
    assert crystallize.reemployer(session, camp, ici.id) is None


# ==========================================================================
# LE RECROISEMENT
# ==========================================================================
def test_un_figurant_ne_suit_jamais_le_joueur(rs, pack, partie):
    session, camp, pj, ici, ailleurs = partie
    c = _figurant(session, camp, ici, apparitions=1)
    for tour in range(40):
        camp.tour = tour
        assert crystallize.recroiser(session, camp, pj, ailleurs.id) == []
    assert c.location_id == ici.id


def test_une_connaissance_peut_se_trouver_la_ou_on_arrive(rs, pack, partie):
    session, camp, pj, ici, ailleurs = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])

    vu = False
    for tour in range(40):
        camp.tour = tour
        if crystallize.recroiser(session, camp, pj, ailleurs.id):
            vu = True
            break
    assert vu, "en quarante voyages, on n'a jamais recroisé personne"
    assert c.location_id == ailleurs.id


def test_le_recroisement_est_deterministe(rs, pack, partie):
    session, camp, pj, ici, ailleurs = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])
    camp.tour = 17
    a = crystallize.recroiser(session, camp, pj, ailleurs.id)
    c.location_id = ici.id
    b = crystallize.recroiser(session, camp, pj, ailleurs.id)
    assert a == b, "recharger une sauvegarde ne doit pas rejouer les dés"


def test_on_ne_recroise_pas_quelqu_un_deja_sur_place(rs, pack, partie):
    session, camp, pj, ici, _ = partie
    c = _figurant(session, camp, ici, apparitions=5)
    crystallize.promouvoir(session, camp, pack, rs, pj, [c])
    for tour in range(40):
        camp.tour = tour
        assert crystallize.recroiser(session, camp, pj, ici.id) == []
