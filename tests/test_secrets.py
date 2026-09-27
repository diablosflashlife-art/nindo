"""Les secrets qui pressent — et qui finissent par se refermer.

Ce qui peut casser ici, et que ces tests verrouillent :

  LE FILTRE     `Secret.verite` ne doit JAMAIS sortir, et un secret intact ne
                doit pas s'annoncer : sa question seule suffirait à trahir
                qu'il y a quelque chose à trouver.
  LES HORLOGES  une relance espacée, bornée, qui s'éteint ; une fenêtre qui se
                referme et qui laisse une trace.
  LES ANCIENNES CAMPAGNES  `tour_progres` valait zéro partout avant ce module.
                Sans garde-fou, le premier tour joué déclarait vingt tours
                d'inertie et refermait tous les secrets d'un coup.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import secrets
from app.models import Campaign, Character, Event, MemoryFact, Secret
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
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
        pnj = Character(campaign_id=camp.id, nom="Tetsuo le forgeron")
        session.add(pj)
        session.add(pnj)
        session.commit()
        session.refresh(pj)
        session.refresh(pnj)
        yield session, camp, pj, pnj


def _secret(session, camp, pnj, **kw):
    s = Secret(campaign_id=camp.id, sujet_id=pnj.id,
               question="Qui a brûlé la forge ?",
               verite="Le chef de la garde, pour effacer une dette.",
               indices=[{"palier": 1, "texte": "Il évite de parler du feu."}],
               **kw)
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


# ==========================================================================
# LE FILTRE DE DIVULGATION
# ==========================================================================
def test_un_secret_intact_ne_s_annonce_pas(rs, partie):
    """Sa question seule dirait au joueur qu'il y a quelque chose à trouver."""
    session, camp, _, pnj = partie
    _secret(session, camp, pnj, niveau_revele=0, tour_progres=1)
    assert secrets.entames(session, camp) == []
    assert secrets.pressions(session, camp, rs) == []


def test_la_verite_ne_sort_jamais(rs, partie):
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=1)
    camp.tour = 40
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    tout = " ".join(secrets.pressions(session, camp, rs))
    tout += " ".join(e.resume for e in session.exec(select(Event)).all())
    tout += " ".join(f.texte for f in session.exec(select(MemoryFact)).all())
    assert "chef de la garde" not in tout.lower()
    assert s.verite not in tout


# ==========================================================================
# LE RAPPEL
# ==========================================================================
def test_un_secret_frais_ne_relance_rien(rs, partie):
    session, camp, pj, pnj = partie
    _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    assert secrets.verifier_echeances(session, camp, rs, pj) == []
    assert secrets.pressions(session, camp, rs) == []


def test_le_monde_y_revient_apres_le_seuil(rs, partie):
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.rappel_apres(rs)
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert s.rappels == 1
    consignes = secrets.pressions(session, camp, rs)
    assert consignes and "Qui a brûlé la forge" in consignes[0]
    assert "AUCUNE réponse" in consignes[0], \
        "la consigne doit interdire au narrateur d'inventer la réponse"


def test_les_relances_sont_espacees(rs, partie):
    """Trois relances trois tours de suite, ce n'est plus une intrigue."""
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    seuil = secrets.rappel_apres(rs)
    camp.tour += seuil
    secrets.verifier_echeances(session, camp, rs, pj)
    camp.tour += 1
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert s.rappels == 1, "la deuxième relance est tombée un tour après"
    camp.tour = s.tour_progres + seuil * 2
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert s.rappels == 2


def test_une_relance_s_eteint(rs, partie):
    """Une pression permanente collée au contexte n'est plus une pression."""
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.rappel_apres(rs)
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert secrets.pressions(session, camp, rs)
    camp.tour += secrets.fenetre_rappel(rs) + 1
    assert secrets.pressions(session, camp, rs) == [], \
        "la consigne est restée active au-delà de sa fenêtre"


def test_on_ne_harcele_pas(rs, partie):
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    depart = camp.tour
    for k in range(1, 12):
        camp.tour = depart + k
        secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert s.rappels <= secrets.rappels_max(rs)


def test_un_progres_rend_les_relances(rs, partie):
    """Le joueur a montré qu'il cherchait : le monde a le droit de
    l'accompagner de nouveau plus tard."""
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.rappel_apres(rs)
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    assert s.rappels == 1

    secrets.marquer_progres(s, camp.tour)
    session.add(s)
    session.commit()
    assert s.rappels == 0 and s.tour_progres == camp.tour
    assert secrets.pressions(session, camp, rs) == []


# ==========================================================================
# LA FENÊTRE
# ==========================================================================
def test_la_fenetre_se_referme_et_laisse_une_trace(rs, partie):
    session, camp, pj, pnj = partie
    s = _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.fenetre(rs)
    effets = secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()

    assert s.perdu is True and s.resolu is True
    assert effets and "refermée" in effets[0]
    ev = session.exec(select(Event).where(Event.type == "secret_perdu")).all()
    assert len(ev) == 1 and ev[0].importance >= 4
    assert pnj.nom in ev[0].resume, "on doit savoir autour de qui c'est perdu"
    assert session.exec(select(MemoryFact)).all(), \
        "la perte doit entrer en mémoire longue, sinon le narrateur l'ignore"


def test_un_secret_perdu_sort_des_pressions_apres_le_deuil(rs, partie):
    session, camp, pj, pnj = partie
    _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.fenetre(rs)
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    consignes = secrets.pressions(session, camp, rs)
    assert consignes and "TROP TARD" in consignes[0]

    camp.tour += secrets.fenetre_deuil(rs) + 1
    assert secrets.pressions(session, camp, rs) == []


def test_un_secret_perdu_ne_se_referme_pas_deux_fois(rs, partie):
    session, camp, pj, pnj = partie
    _secret(session, camp, pnj, niveau_revele=1, tour_progres=camp.tour)
    camp.tour += secrets.fenetre(rs)
    secrets.verifier_echeances(session, camp, rs, pj)
    session.commit()
    camp.tour += 5
    assert secrets.verifier_echeances(session, camp, rs, pj) == []
    assert len(session.exec(select(Event)).all()) == 1


def test_une_vieille_campagne_ne_perd_pas_tout_au_premier_tour(rs, partie):
    """`tour_progres` valait zéro sur toutes les parties d'avant ce module.
    Sans garde-fou, le premier tour joué déclarait vingt tours d'inertie."""
    session, camp, pj, pnj = partie
    camp.tour = 57
    s = _secret(session, camp, pnj, niveau_revele=2, tour_progres=0)
    assert secrets.verifier_echeances(session, camp, rs, pj) == []
    assert s.perdu is False
    assert s.tour_progres == camp.tour, \
        "l'horloge doit s'amorcer au tour courant, pas rester à zéro"
