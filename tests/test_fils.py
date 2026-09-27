"""Les fils du récit, et les missions qui lui donnent sa forme.

Mesuré sur une partie réelle de cinquante tours : une énigme ouverte presque à
chaque scène, aucune refermée ; huit offres de mission ignorées comptées comme
huit échecs ; un épilogue qui concluait « tu as échoué, à chaque fois ».
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import fils, missions
from app.models import Campaign, Character, Fil, MemoryFact, Quest, Relation
from app.rules.loader import charger


@pytest.fixture
def rs():
    return charger("naruto")


@pytest.fixture
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=5, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        pj = Character(campaign_id=camp.id, nom="Sayuri", is_pc=True, niveau=1, xp=0,
                       stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
        sensei = Character(campaign_id=camp.id, nom="Rin", is_pc=False, role_campagne="sensei")
        session.add_all([pj, sensei])
        session.commit()
        session.refresh(pj)
        session.refresh(sensei)
        session.add(Relation(campaign_id=camp.id, source_id=sensei.id, cible_id=pj.id, valeur=10))
        session.commit()
        yield session, camp, pj, sensei


# --------------------------------------------------------------------- fils
def test_une_question_posee_devient_un_fil(partie):
    session, camp, *_ = partie
    fils.appliquer(session, camp, {"mystere_nouveau": "Qui a creusé la fissure ?"})
    session.commit()
    assert [f.question for f in fils.ouverts(session, camp)] == ["Qui a creusé la fissure ?"]


def test_une_reponse_referme_le_fil_et_se_dit(partie):
    session, camp, *_ = partie
    fils.appliquer(session, camp, {"mystere_nouveau": "Qui a creusé la fissure ?"})
    session.commit()
    effets = fils.appliquer(session, camp, {"mysteres_resolus": [
        {"numero": 0, "reponse": "Kaito, pour cacher les plans volés."}]})
    session.commit()
    assert not fils.ouverts(session, camp)
    assert "Kaito" in effets[0]
    assert session.exec(select(MemoryFact)).first() is not None


def test_pas_plus_de_quatre_fils_ouverts(partie):
    session, camp, *_ = partie
    for i in range(7):
        fils.appliquer(session, camp, {"mystere_nouveau": f"Question numéro {i} ?"})
        session.commit()
    assert len(fils.ouverts(session, camp)) == fils.OUVERTS_MAX + 1


def test_trop_de_fils_interdit_d_en_ouvrir(partie):
    session, camp, *_ = partie
    for i in range(3):
        session.add(Fil(campaign_id=camp.id, question=f"Q{i} ?", ouvert_au_tour=camp.tour))
    session.commit()
    assert "N'OUVRE AUCUN NOUVEAU MYSTÈRE" in fils.consigne(session, camp)


def test_un_fil_qui_attend_doit_etre_paye(partie):
    session, camp, *_ = partie
    session.add(Fil(campaign_id=camp.id, question="Que cache Rin ?", ouvert_au_tour=1))
    session.commit()
    camp.tour = 1 + fils.AGE_REPONSE
    assert "CETTE SCÈNE RÉPOND à « Que cache Rin ? »" in fils.consigne(session, camp)


def test_une_mission_engagee_monte_puis_se_denoue(partie, rs):
    session, camp, pj, _ = partie
    q = Quest(campaign_id=camp.id, titre="Premier rapport d'équipe", statut="acceptée",
              debut_tour=1)
    session.add(q)
    session.commit()
    camp.tour = 1 + fils.ARC_MONTEE
    assert "L'ARC MONTE" in fils.consigne(session, camp)
    camp.tour = 1 + fils.ARC_DENOUEMENT
    assert "DÉNOUEMENT" in fils.consigne(session, camp)


# ----------------------------------------------------------------- missions
def test_une_offre_ignoree_expire_sans_echec(partie):
    session, camp, *_ = partie
    q = Quest(campaign_id=camp.id, titre="Faucon égaré", statut="proposée", echeance_tour=4)
    session.add(q)
    session.commit()
    effets = missions.verifier_echeances(session, camp)
    session.commit()
    assert q.statut == "expirée"
    assert not effets, "une offre jamais prise n'est pas un échec à annoncer"
    assert session.exec(select(MemoryFact)).first() is None


def test_accepter_lance_l_horloge_de_l_arc(partie, rs):
    session, camp, pj, _ = partie
    q = Quest(campaign_id=camp.id, titre="Faucon égaré", statut="proposée")
    session.add(q)
    session.commit()
    missions.changer_statut(session, camp, pj, rs, q, "acceptée")
    assert q.debut_tour == camp.tour


def test_une_mission_reussie_rapporte_une_fois(partie, rs):
    session, camp, pj, sensei = partie
    q = Quest(campaign_id=camp.id, titre="Premier rapport", statut="acceptée", rang="D",
              donneur_id=sensei.id)
    session.add(q)
    session.commit()
    effets = missions.changer_statut(session, camp, pj, rs, q, "réussie")
    session.commit()
    assert any("réussie" in e and "XP" in e for e in effets)
    xp_apres = (pj.niveau, pj.xp)
    rel = session.exec(select(Relation).where(Relation.source_id == sensei.id)).first()
    assert rel.valeur == 10 + missions.CONFIANCE_DONNEUR
    # le récit la redit « réussie » : pas de seconde récompense
    q.statut = "en cours"
    missions.changer_statut(session, camp, pj, rs, q, "réussie")
    assert (pj.niveau, pj.xp) == xp_apres


# ------------------------------------------- trouvé en partie à deux joueurs
def test_le_recit_ne_ressuscite_pas_une_mission_echouee(partie, rs):
    session, camp, pj, _ = partie
    q = Quest(campaign_id=camp.id, titre="Premier rapport", statut="échouée")
    session.add(q)
    session.commit()
    assert missions.changer_statut(session, camp, pj, rs, q, "en cours") == []
    assert q.statut == "échouée"


def test_le_recit_ne_fait_pas_echouer_une_offre(partie, rs):
    session, camp, pj, _ = partie
    q = Quest(campaign_id=camp.id, titre="Livraison scellée", statut="proposée")
    session.add(q)
    session.commit()
    missions.changer_statut(session, camp, pj, rs, q, "échouée")
    assert q.statut == "proposée"
    # le joueur, lui, peut la refuser
    missions.changer_statut(session, camp, pj, rs, q, "refusée", par_le_joueur=True)
    assert q.statut == "refusée"


def test_accepter_laisse_le_temps_de_mener_la_mission(partie, rs):
    session, camp, pj, _ = partie
    q = Quest(campaign_id=camp.id, titre="Livraison", statut="proposée",
              echeance_tour=camp.tour + 2)
    session.add(q)
    session.commit()
    missions.changer_statut(session, camp, pj, rs, q, "acceptée", par_le_joueur=True)
    assert q.echeance_tour >= camp.tour + missions.DELAI_MINIMAL_ACCEPTEE


def test_une_question_a_rallonge_est_coupee():
    from app.engine.validators import valider_consequences

    class _Vide:
        def exec(self, *_):
            class R:
                def all(self):
                    return []
            return R()
    net, _ = valider_consequences(_Vide(), Campaign(id=1, nom="x"), {
        "mystere_nouveau": "Qui ou quoi a laissé cette substance noire sur l'aiguille "
                           "rocheuse, et quel est son lien avec la créature ?"})
    assert net["mystere_nouveau"].endswith("?")
    assert len(net["mystere_nouveau"]) <= 80
    assert "créature" not in net["mystere_nouveau"]


def test_le_narrateur_est_rappele_a_l_ordre_pour_l_autre_joueur():
    from app.engine.turn import Preparation
    prep = Preparation(action="a", intent={}, bloc="b", resolution={}, systeme="s",
                       contexte_narrateur="c", effets_combat=[], liens_techniques=[],
                       en_combat=False, autres_pj=["Raiden Kaze"])
    invite = prep.invite()
    assert "Raiden Kaze appartient à un AUTRE joueur humain" in invite
    assert invite.index("AUTRE joueur") > invite.index("RÉSULTAT MÉCANIQUE")
    assert prep.max_tokens < 600
