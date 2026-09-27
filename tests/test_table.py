"""Le tour de table, et la voix réaliste.

RETOUR DE PARTIE À DEUX : une seule scène racontait l'action du meneur et
laissait l'autre joueur en figurant. Ces tests verrouillent ce qui a corrigé
ça — qui partage la scène, et une préparation fusionnée où CHAQUE action
figure, dans l'ordre, à la troisième personne.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app import voix_piper
from app.engine import table
from app.engine.turn import Preparation
from app.memory.context import AVERTISSEMENT_AUTRES_PJ, AVERTISSEMENT_TOUR_DE_TABLE
from app.models import Campaign, Character, Location, Turn
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=7, ruleset=rs.data, tour=5,
                        phase="en_cours", nb_joueurs=3)
        session.add(camp)
        session.commit()
        session.refresh(camp)
        ici = Location(campaign_id=camp.id, nom="Pont")
        ailleurs = Location(campaign_id=camp.id, nom="Forêt")
        session.add_all([ici, ailleurs])
        session.commit()
        pjs = []
        for nom, lieu in (("Kaito", ici), ("Hana", ici), ("Ren", ailleurs)):
            pj = Character(campaign_id=camp.id, nom=nom, is_pc=True,
                           location_id=lieu.id, stats=rs.stats_defaut(),
                           ressources=rs.ressources_defaut())
            session.add(pj)
            pjs.append(pj)
        session.commit()
        for pj in pjs:
            session.refresh(pj)
        yield session, camp, pjs


def _prep(action, bloc, en_combat=False):
    return Preparation(
        action=action, intent={"type": "social"}, bloc=bloc,
        resolution={"check": {"issue": "succes"}, "divers": 1},
        systeme="", contexte_narrateur=f"CONTEXTE\n{AVERTISSEMENT_AUTRES_PJ}\nFIN",
        effets_combat=["coup porté"] if en_combat else [], liens_techniques=[],
        en_combat=en_combat, autres_pj=["Hana"])


# ==========================================================================
# QUI PARTAGE LA SCÈNE
# ==========================================================================
def test_le_groupe_c_est_le_lieu(partie):
    session, camp, (kaito, hana, ren) = partie
    assert [p.nom for p in table.groupe(session, camp, kaito)] == ["Kaito", "Hana"]
    assert [p.nom for p in table.groupe(session, camp, ren)] == ["Ren"]


def test_un_tour_appartient_a_ceux_qui_l_ont_joue():
    tour = Turn(campaign_id=1, index=4, character_id=1,
                resolution={"joueurs": [{"id": 1}, {"id": 2}]})
    assert table.dans_la_scene(tour, {2})
    assert not table.dans_la_scene(tour, {3})
    # L'ouverture concerne tout le monde.
    assert table.dans_la_scene(Turn(campaign_id=1, index=1, character_id=1), {3})


# ==========================================================================
# LA FUSION — aucune action ne peut manquer
# ==========================================================================
def test_la_fusion_garde_chaque_action_dans_l_ordre(partie):
    _, _, (kaito, hana, _) = partie
    p = table.fusionner([(kaito, _prep("J'interroge l'instructeur.", "Jet : succès")),
                         (hana, _prep("Je  lance un kunaï\nsur la cible.", "Jet : échec"))])
    assert p.groupe and p.participants == [kaito.id, hana.id]
    assert p.action.splitlines() == ["Kaito : J'interroge l'instructeur.",
                                     "Hana : Je lance un kunaï sur la cible."]
    assert "— Kaito —" in p.bloc and "— Hana —" in p.bloc
    assert [j["nom"] for j in p.resolution["joueurs"]] == ["Kaito", "Hana"]
    assert "divers" not in p.resolution["joueurs"][0]["resolution"]
    consigne = p.consigne_action
    assert "1. Kaito" in consigne and "2. Hana" in consigne
    assert "jamais « tu »" in consigne


def test_en_tour_de_table_les_autres_joueurs_ont_declare(partie):
    _, _, (kaito, hana, _) = partie
    p = table.fusionner([(kaito, _prep("a", "b")), (hana, _prep("c", "d"))])
    assert AVERTISSEMENT_AUTRES_PJ not in p.contexte_narrateur
    assert AVERTISSEMENT_TOUR_DE_TABLE in p.contexte_narrateur
    assert p.rappel_joueurs == ""          # personne n'est « à ne pas faire agir »


def test_une_scene_a_plusieurs_a_plus_de_place(partie):
    _, _, (kaito, hana, _) = partie
    seul = _prep("a", "b")
    deux = table.fusionner([(kaito, _prep("a", "b")), (hana, _prep("c", "d"))])
    assert deux.facteur_longueur == 1.5
    assert deux.max_tokens > seul.max_tokens


def test_un_seul_combattant_met_la_scene_en_combat(partie):
    _, _, (kaito, hana, _) = partie
    p = table.fusionner([(kaito, _prep("a", "b")),
                         (hana, _prep("c", "d", en_combat=True))])
    assert p.en_combat and not p.entre_deux
    assert p.effets_combat == ["Hana — coup porté"]


# ==========================================================================
# LA VOIX — jamais une raison d'échouer
# ==========================================================================
def test_sans_voix_installee_on_rend_la_main_au_navigateur(tmp_path, monkeypatch):
    monkeypatch.setattr(voix_piper, "DOSSIER", tmp_path)
    monkeypatch.setattr(voix_piper, "CACHE", tmp_path / "cache")
    assert not voix_piper.disponible()
    assert voix_piper.dire("Bonjour.") is None
    assert voix_piper.etat()["piper"] is False


def test_une_phrase_vide_ne_se_dit_pas():
    assert voix_piper.dire("   ") is None


@pytest.mark.skipif(not voix_piper.disponible(), reason="voix Piper non installée")
def test_la_voix_installee_parle_et_se_souvient(tmp_path, monkeypatch):
    monkeypatch.setattr(voix_piper, "CACHE", tmp_path)
    wav = voix_piper.dire("Le vent se lève sur le village caché.", vitesse=1.2)
    assert wav and wav[:4] == b"RIFF"
    assert len(list(tmp_path.glob("*.wav"))) == 1
    assert voix_piper.dire("Le vent se lève sur le village caché.", vitesse=1.2) == wav
