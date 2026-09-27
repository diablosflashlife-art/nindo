"""Le mot de la fin — une campagne doit pouvoir s'ACHEVER.

`Campaign.phase` connaissait « terminee » et rien ne la déclenchait : une
campagne ne s'achevait jamais, elle s'arrêtait. Ces tests verrouillent les
trois choses qui comptent : on ne ferme pas trop tôt, on ne ferme jamais tout
seul, et le filtre de divulgation tient jusqu'au dernier texte.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import epilogue
from app.models import (Campaign, Character, Destiny, DestinyTrait, Event,
                        Location, Quest, Secret, Summary)
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="L'Ombre du Sceau", graine=1, ruleset=rs.data,
                        tour=30, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Tour du Kage")
        session.add(lieu)
        session.commit()
        session.refresh(lieu)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       clan="Sans clan", village="Konoha",
                       location_id=lieu.id, stats=rs.stats_defaut(),
                       ressources=rs.ressources_defaut())
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj


class Muet:
    def text(self, system, user, **kw):
        raise RuntimeError("Ollama est éteint")


class Faux:
    def __init__(self):
        self.vu = ""

    def text(self, system, user, **kw):
        self.vu = user
        return "[faux] Tu t'es arrêté là, et le reste a continué sans toi."


# ==========================================================================
# QUAND PEUT-ON CONCLURE
# ==========================================================================
def test_pas_d_epilogue_sur_une_campagne_qui_commence(rs, partie):
    session, camp, pj = partie
    camp.tour = 4
    ok, raison = epilogue.possible(session, camp, pj)
    assert ok is False and "matière" in raison


def test_la_duree_ouvre_la_fin(rs, partie):
    session, camp, pj = partie
    assert epilogue.possible(session, camp, pj)[0] is True


def test_une_destinee_accomplie_ouvre_la_fin_tot(rs, partie):
    session, camp, pj = partie
    camp.tour = 6
    d = Destiny(campaign_id=camp.id, character_id=pj.id)
    session.add(d)
    session.commit()
    session.refresh(d)
    session.add(DestinyTrait(campaign_id=camp.id, destiny_id=d.id,
                             trait_ref="sharingan", libelle="Un œil qui voit",
                             etat="eveille", rarete="rare"))
    session.commit()
    ok, raison = epilogue.possible(session, camp, pj)
    assert ok is True and "destinée" in raison.lower()


def test_un_petit_trait_eveille_n_est_pas_une_destinee(rs, partie):
    """Trouvé en partie réelle : une « seconde affinité » éveillée au
    deuxième tour permettait de clore la chronique au troisième."""
    session, camp, pj = partie
    camp.tour = 6
    d = Destiny(campaign_id=camp.id, character_id=pj.id)
    session.add(d)
    session.commit()
    session.refresh(d)
    session.add(DestinyTrait(campaign_id=camp.id, destiny_id=d.id,
                             trait_ref="seconde_affinite", libelle="Seconde affinité",
                             etat="eveille", rarete="peu_commun"))
    session.commit()
    assert epilogue.possible(session, camp, pj)[0] is False


def test_un_trait_donne_a_la_creation_n_accomplit_rien(rs, partie):
    """Un trait `actif_au_depart` naît déjà « eveille ». Sans ce filtre, un
    genin du premier tour avec une affinité de feu pouvait clore sa chronique
    en annonçant que sa destinée s'était accomplie."""
    session, camp, pj = partie
    camp.tour = 6
    d = Destiny(campaign_id=camp.id, character_id=pj.id)
    session.add(d)
    session.commit()
    session.refresh(d)
    session.add(DestinyTrait(campaign_id=camp.id, destiny_id=d.id,
                             trait_ref="affinite_katon", libelle="Feu",
                             etat="eveille", actif_au_depart=True))
    session.commit()
    assert epilogue.possible(session, camp, pj)[0] is False


def test_une_campagne_close_ne_se_referme_pas(rs, partie, monkeypatch):
    session, camp, pj = partie
    monkeypatch.setattr(epilogue, "get_llm", lambda: Faux())
    epilogue.clore(session, camp, pj, rs)
    assert epilogue.possible(session, camp, pj)[0] is False
    with pytest.raises(epilogue.FinRefusee):
        epilogue.clore(session, camp, pj, rs)


def test_on_ne_ferme_jamais_tout_seul(rs, partie):
    """Aucun appel de moteur ne doit clore une campagne : `possible` DIT que
    c'est possible, `clore` est déclenché par la table."""
    session, camp, pj = partie
    assert epilogue.possible(session, camp, pj)[0] is True
    assert camp.phase == "en_cours"


# ==========================================================================
# CE QUE ÇA ÉCRIT
# ==========================================================================
def test_clore_ferme_et_laisse_un_texte(rs, partie, monkeypatch):
    session, camp, pj = partie
    monkeypatch.setattr(epilogue, "get_llm", lambda: Faux())
    out = epilogue.clore(session, camp, pj, rs)

    assert camp.phase == "terminee"
    assert out["du_modele"] is True and out["texte"].strip()
    s = session.exec(select(Summary).where(
        Summary.niveau == epilogue.NIVEAU)).first()
    assert s is not None and s.au_tour == camp.tour
    ev = session.exec(select(Event).where(Event.type == "fin")).all()
    assert len(ev) == 1 and ev[0].importance == 5


def test_un_modele_muet_n_empeche_pas_de_conclure(rs, partie, monkeypatch):
    """C'est le dernier texte de la campagne : il ne peut pas manquer."""
    session, camp, pj = partie
    monkeypatch.setattr(epilogue, "get_llm", lambda: Muet())
    out = epilogue.clore(session, camp, pj, rs)
    assert camp.phase == "terminee"
    assert out["du_modele"] is False
    assert "Kaito" in out["texte"] and "Tour du Kage" in out["texte"]


def test_le_texte_de_secours_dit_les_lieux_en_francais(rs, partie, monkeypatch):
    session, camp, pj = partie
    monkeypatch.setattr(epilogue, "get_llm", lambda: Muet())
    texte = epilogue.clore(session, camp, pj, rs)["texte"]
    assert "à la Tour du Kage" in texte
    assert "à Tour du Kage" not in texte


# ==========================================================================
# LE FILTRE TIENT JUSQU'AU BOUT
# ==========================================================================
def test_aucune_verite_de_secret_n_entre_dans_l_epilogue(rs, partie, monkeypatch):
    session, camp, pj = partie
    autre = Character(campaign_id=camp.id, nom="Tetsuo")
    session.add(autre)
    session.commit()
    session.refresh(autre)
    session.add(Secret(campaign_id=camp.id, sujet_id=autre.id,
                       question="Qui a brûlé la forge ?",
                       verite="Le chef de la garde, pour effacer une dette.",
                       niveau_revele=1, tour_progres=camp.tour))
    session.commit()

    faux = Faux()
    monkeypatch.setattr(epilogue, "get_llm", lambda: faux)
    out = epilogue.clore(session, camp, pj, rs)

    assert "Qui a brûlé la forge" in faux.vu, "la question doit être rappelée"
    assert "chef de la garde" not in faux.vu.lower()
    assert "chef de la garde" not in out["texte"].lower()


def test_aucune_verite_de_destinee_non_plus(rs, partie, monkeypatch):
    session, camp, pj = partie
    d = Destiny(campaign_id=camp.id, character_id=pj.id)
    session.add(d)
    session.commit()
    session.refresh(d)
    session.add(DestinyTrait(
        campaign_id=camp.id, destiny_id=d.id, trait_ref="sceau",
        libelle="Une marque sous l'épaule", etat="eveille",
        verite="Le sceau du démon renard, posé par le Quatrième."))
    session.commit()

    faux = Faux()
    monkeypatch.setattr(epilogue, "get_llm", lambda: faux)
    epilogue.clore(session, camp, pj, rs)
    assert "démon renard" not in faux.vu.lower()


# ==========================================================================
# ROUVRIR
# ==========================================================================
def test_rouvrir_rend_la_main_sans_rien_effacer(rs, partie, monkeypatch):
    session, camp, pj = partie
    monkeypatch.setattr(epilogue, "get_llm", lambda: Faux())
    epilogue.clore(session, camp, pj, rs)

    epilogue.rouvrir(session, camp)
    assert camp.phase == "en_cours"
    assert epilogue.deja_clos(session, camp) is not None, \
        "l'épilogue reste dans la chronique"
    assert len(session.exec(select(Event).where(Event.type == "fin")).all()) == 2


def test_rouvrir_une_campagne_en_cours_ne_fait_rien(rs, partie):
    session, camp, pj = partie
    epilogue.rouvrir(session, camp)
    assert camp.phase == "en_cours"
    assert session.exec(select(Event).where(Event.type == "fin")).all() == []


# ==========================================================================
# LE RELEVÉ
# ==========================================================================
def test_le_releve_trie_les_missions_par_issue(rs, partie):
    session, camp, pj = partie
    session.add(Quest(campaign_id=camp.id, titre="Escorte", rang="D",
                      statut="réussie"))
    session.add(Quest(campaign_id=camp.id, titre="Rouleau", rang="C",
                      statut="échouée"))
    session.add(Quest(campaign_id=camp.id, titre="Patrouille", rang="D",
                      statut="acceptée"))
    session.commit()
    d = epilogue.releve(session, camp, pj, rs)
    assert d["reussies"] == ["Escorte"]
    assert d["echouees"] == ["Rouleau"]
    assert d["en_cours"] == ["Patrouille"]
