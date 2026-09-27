"""« Précédemment » — le rappel qu'on lit en rouvrant sa campagne.

Ce qui doit tenir, et que ces tests verrouillent :

  LE SEUIL      pas de rappel après une pause café, ni sur une campagne qui
                n'a pas encore d'histoire.
  SANS MODÈLE   le relevé déterministe doit suffire. C'est la garantie qu'on
                peut reprendre sa partie avec Ollama éteint.
  LE CACHE      le rappel est écrit UNE fois par tour, pas à chaque
                rechargement de page.
  LA TABLE      `Summary` sert aussi aux chapitres. Un rappel rangé là ne doit
                ni passer pour un chapitre, ni arrêter la compression.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import reprise
from app.models import (Campaign, Character, Location, Quest, Summary,
                        maintenant)
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=12,
                        phase="en_cours",
                        joue_le=maintenant() - timedelta(days=9))
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Konoha",
                        description="Le village caché de la Feuille.")
        session.add(lieu)
        session.commit()
        session.refresh(lieu)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       location_id=lieu.id, etats=["entaille au bras"],
                       points_libres=2)
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj


# ==========================================================================
# LE SEUIL
# ==========================================================================
def test_pas_de_rappel_apres_une_pause_cafe(rs, partie):
    session, camp, _ = partie
    camp.joue_le = maintenant() - timedelta(minutes=20)
    assert reprise.necessaire(session, camp) is False


def test_pas_de_rappel_sur_une_campagne_qui_commence(rs, partie):
    session, camp, _ = partie
    camp.tour = 1
    assert reprise.necessaire(session, camp) is False


def test_pas_de_rappel_hors_partie_en_cours(rs, partie):
    session, camp, _ = partie
    camp.phase = "creation"
    assert reprise.necessaire(session, camp) is False


def test_rappel_apres_une_semaine(rs, partie):
    session, camp, _ = partie
    assert reprise.necessaire(session, camp) is True
    assert "semaine" in reprise.duree_lisible(camp)


def test_la_duree_se_lit_en_francais(rs, partie):
    session, camp, _ = partie
    for jours, attendu in ((0, "heures"), (1, "un jour"), (3, "3 jours"),
                           (21, "3 semaines")):
        camp.joue_le = maintenant() - timedelta(days=jours, hours=3)
        assert attendu in reprise.duree_lisible(camp), f"{jours} jours"


# ==========================================================================
# LE RELEVÉ, SANS MODÈLE
# ==========================================================================
def test_le_releve_dit_ou_l_on_est_et_ce_qui_presse(rs, partie):
    session, camp, pj = partie
    session.add(Quest(campaign_id=camp.id, titre="Escorter le marchand",
                      rang="C", statut="acceptée", echeance_tour=camp.tour + 2))
    session.add(Quest(campaign_id=camp.id, titre="Retrouver le rouleau",
                      rang="B", statut="acceptée", echeance_tour=camp.tour + 30))
    session.commit()

    d = reprise.releve(session, camp, pj, rs)
    assert d["lieu"] == "Konoha"
    assert d["tour"] == camp.tour
    assert d["etats"] == ["entaille au bras"]
    assert d["points_libres"] == 2
    assert [e["titre"] for e in d["echeances"]][0] == "Escorter le marchand", \
        "le délai le plus court doit passer devant"


def test_le_rappel_marche_sans_modele(rs, partie):
    """C'est la garantie qu'on peut reprendre sa partie avec Ollama éteint."""
    session, camp, pj = partie
    session.add(Quest(campaign_id=camp.id, titre="Escorter le marchand",
                      rang="C", statut="acceptée", echeance_tour=camp.tour + 1))
    session.commit()

    out = reprise.rappeler(session, camp, pj, rs, ecrire=False)
    assert out["du_modele"] is False
    assert pj.nom in out["texte"] and "Konoha" in out["texte"]
    assert "Escorter le marchand" in out["texte"], \
        "un délai imminent ne doit pas passer à la trappe"
    assert session.exec(select(Summary)).all() == [], \
        "le chemin sans modèle ne doit rien écrire en base"


def test_le_rappel_ne_plante_pas_sans_lieu_ni_rien(rs, partie):
    session, camp, pj = partie
    pj.location_id, pj.etats, pj.points_libres = None, [], 0
    session.add(pj)
    session.commit()
    out = reprise.rappeler(session, camp, pj, rs, ecrire=False)
    assert out["texte"].strip()


# ==========================================================================
# LE CACHE
# ==========================================================================
def test_le_recit_est_ecrit_une_fois_par_tour(rs, partie, monkeypatch):
    session, camp, pj = partie
    appels = {"n": 0}

    class Faux:
        def text(self, system, user, **kw):
            appels["n"] += 1
            return "[faux] Tu reprends là où tu avais laissé."

    monkeypatch.setattr(reprise, "get_llm", lambda: Faux())
    premier = reprise.rappeler(session, camp, pj, rs)
    second = reprise.rappeler(session, camp, pj, rs)
    assert appels["n"] == 1, "le rappel a été régénéré au rechargement"
    assert premier["texte"] == second["texte"]
    assert premier["du_modele"] is True

    camp.tour += 1
    reprise.rappeler(session, camp, pj, rs)
    assert appels["n"] == 2, "un nouveau tour doit périmer le rappel"


def test_un_modele_muet_retombe_sur_le_releve(rs, partie, monkeypatch):
    session, camp, pj = partie

    class Casse:
        def text(self, system, user, **kw):
            raise RuntimeError("Ollama ne répond pas")

    monkeypatch.setattr(reprise, "get_llm", lambda: Casse())
    out = reprise.rappeler(session, camp, pj, rs)
    assert out["du_modele"] is False and pj.nom in out["texte"]


# ==========================================================================
# LA TABLE PARTAGÉE
# ==========================================================================
def test_un_rappel_n_est_pas_un_chapitre(rs, partie, monkeypatch):
    """`Summary` sert aussi à la compression hiérarchique. Sans filtre, le
    dernier rappel écrit ferait croire que l'historique est compressé jusqu'au
    tour courant — et plus aucun résumé de scène ne serait produit."""
    session, camp, pj = partie
    session.add(Summary(campaign_id=camp.id, niveau="scene", du_tour=1,
                        au_tour=6, texte="Le vrai chapitre."))
    session.commit()

    class Faux:
        def text(self, system, user, **kw):
            return "[faux] Précédemment."

    monkeypatch.setattr(reprise, "get_llm", lambda: Faux())
    reprise.rappeler(session, camp, pj, rs)

    chapitres = session.exec(select(Summary).where(
        Summary.niveau != reprise.NIVEAU)).all()
    assert len(chapitres) == 1 and chapitres[0].au_tour == 6

    d = reprise.releve(session, camp, pj, rs)
    assert d["chapitre"] == "Le vrai chapitre.", \
        "le relevé doit citer le chapitre, pas le rappel précédent"
