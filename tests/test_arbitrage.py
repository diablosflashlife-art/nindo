"""L'annonce du jet — chantier A de Nindō 2.0 (docs/NINDO-2.md, pilier 1).

Le joueur déclare ; le maître du jeu annonce la caractéristique, la
difficulté, le bonus détaillé, les chances et le prix de l'échec ; le joueur
choisit une technique, force ou reformule ; PUIS le dé roule. Ces tests
verrouillent que l'annonce ne dépense rien, que les choix se paient au dé,
et que forcer reste un pari.
"""
import random

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.engine import turn as moteur
from app.lore.pack import charger as charger_pack
from app.models import Campaign, Character, CharacterTechnique, Location
from app.rules import engine as regles
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs):
    moteur_bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur_bd)
    with Session(moteur_bd) as session:
        camp = Campaign(nom="test", graine=3, ruleset=rs.data, tour=4, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Cour de l'Académie", danger=1)
        session.add(lieu)
        session.commit()
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       specialisation="taijutsu", location_id=lieu.id,
                       stats={**rs.stats_defaut(), "taijutsu": 14},
                       ressources={**rs.ressources_defaut(), "chakra": 10})
        session.add(pj)
        session.commit()
        session.refresh(pj)
        # Deux techniques de taijutsu : une assurée, une à peine apprise.
        session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                       technique_ref="konoha_senpuu", maitrise=60))
        session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                       technique_ref="taijutsu_base", maitrise=10))
        session.commit()
        yield session, camp, pj


# ==========================================================================
# LES CHANCES — la même lecture que le jet réel
# ==========================================================================
def test_les_chances_enumerent_les_faces(rs):
    c = rs.chances({"taijutsu": 10}, "taijutsu", "normal")
    # DC 12, sans bonus : 12 à 20 réussissent (9 faces), 9 à 11 « oui, mais »
    # (3 faces), 1 à 8 échouent — le 1 toujours.
    assert c == {"reussite": 45, "partielle": 15, "echec": 40}
    assert sum(c.values()) == 100


def test_un_bonus_deplace_les_chances_et_le_vingt_reussit_toujours(rs):
    assert rs.chances({"taijutsu": 10}, "taijutsu", "legendaire")["reussite"] == 5
    assert rs.chances({"taijutsu": 10}, "taijutsu", "triviale", 10)["echec"] == 5


# ==========================================================================
# L'ANNONCE — rien n'est dépensé, tout est dit
# ==========================================================================
def test_l_annonce_dit_tout_et_ne_depense_rien(rs, pack, partie):
    session, camp, pj = partie
    chakra = pj.ressources["chakra"]
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur de la cour.", rs, pack)
    assert a.annonce and a.stat == "taijutsu" and a.dc == 12
    assert a.difficulte_label == "Normale"
    assert a.modificateur == 2 and a.bonus == [], "la caractéristique est dans la formule, pas dans le bonus"
    assert a.chances["reussite"] > 45
    assert a.enjeu_echec.startswith("[mock]")
    assert pj.ressources["chakra"] == chakra
    refs = [t["ref"] for t in a.techniques]
    assert refs == ["konoha_senpuu", "taijutsu_base"], "les plus utiles d'abord"
    senpuu = a.techniques[0]
    assert senpuu["bonus"] == 2 and senpuu["cout"] == 3 and senpuu["dispo"]
    assert a.techniques[1]["bonus"] < 0, "une technique à peine apprise gêne"
    assert a.forcer == {"bonus": 3, "chakra": 2, "dispo": True}


def test_sans_chakra_les_options_se_ferment(rs, pack, partie):
    session, camp, pj = partie
    pj.ressources = {**pj.ressources, "chakra": 1}
    session.add(pj)
    session.commit()
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    assert not a.forcer["dispo"]
    assert not a.techniques[0]["dispo"]
    assert a.techniques[1]["dispo"], "les kata de base ne coûtent rien"


def test_l_annonce_se_relit_dans_le_tour(rs, pack, partie):
    session, camp, pj = partie
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    prep = moteur.resoudre(session, camp, pj, a, rs, pack)
    r = prep.resolution
    assert r["check"]["bonus"] == 0 and r["arbitrage"]["modificateur"] == 2
    assert r["arbitrage"]["stat_label"] == "Taijutsu" and r["arbitrage"]["dc"] == 12
    assert "chances" in r["arbitrage"]


# ==========================================================================
# LES CHOIX SE PAIENT AU DÉ
# ==========================================================================
def test_la_technique_choisie_ajoute_son_bonus_et_coute_son_chakra(rs, pack, partie):
    session, camp, pj = partie
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    prep = moteur.resoudre(session, camp, pj, a, rs, pack, technique="konoha_senpuu")
    assert prep.resolution["check"]["bonus"] == 2
    # Le solde remonte ensuite de 2 (récupération hors combat) : on lit l'effet.
    assert any(e.startswith("Chakra -3 (reste 7)") for e in prep.effets_combat)
    assert [ct.technique_ref for ct in prep.liens_techniques] == ["konoha_senpuu"]
    assert prep.resolution["arbitrage"]["technique"] == "Konoha Senpû"


def test_une_technique_non_proposee_est_ignoree(rs, pack, partie):
    session, camp, pj = partie
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    prep = moteur.resoudre(session, camp, pj, a, rs, pack, technique="chidori")
    assert prep.resolution["check"]["bonus"] == 0
    assert not any("chakra -" in e for e in prep.effets_combat)


def test_forcer_est_un_pari(rs, pack, partie, monkeypatch):
    session, camp, pj = partie
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    # Un 3 sur le dé : 3 + 2 + 3 = 8 contre 12, raté malgré le bonus.
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (3, [3]))
    prep = moteur.resoudre(session, camp, pj, a, rs, pack, forcer=True)
    c = prep.resolution["check"]
    assert c["bonus"] == 3 and c["issue"] == "echec_critique"
    assert any(e.startswith("Forcé : chakra -2 (reste 8)") for e in prep.effets_combat)
    assert "FORCÉ" in prep.bloc and prep.resolution["arbitrage"]["forcer"]


def test_forcer_qui_reussit_ne_coute_que_le_chakra(rs, pack, partie, monkeypatch):
    session, camp, pj = partie
    a = moteur.arbitrer(session, camp, pj, "Je saute le mur.", rs, pack)
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (15, [15]))
    prep = moteur.resoudre(session, camp, pj, a, rs, pack, forcer=True)
    assert prep.resolution["check"]["issue"] == "reussite"
    assert any(e.startswith("Forcé : chakra -2 (reste 8)") for e in prep.effets_combat)


def test_preparer_reste_le_chemin_d_un_seul_trait(rs, pack, partie):
    session, camp, pj = partie
    prep = moteur.preparer(session, camp, pj, "Je saute le mur.", rs, pack)
    assert "check" in prep.resolution and "arbitrage" in prep.resolution


def test_une_action_impossible_n_a_pas_d_annonce(rs, pack, partie, monkeypatch):
    session, camp, pj = partie
    from app.llm import provider
    vrai = provider.MockProvider.json

    def json_impossible(self, system, user, schema, **kw):
        out = vrai(self, system, user, schema, **kw)
        if "action_type" in schema.get("properties", {}):
            out.update(faisable="non", obstacle="Madara n'est pas ici.")
        return out
    monkeypatch.setattr(provider.MockProvider, "json", json_impossible)
    a = moteur.arbitrer(session, camp, pj, "Je tue Madara.", rs, pack)
    assert not a.annonce and a.impossible == "Madara n'est pas ici."
    prep = moteur.resoudre(session, camp, pj, a, rs, pack)
    assert prep.resolution["impossible"] and "check" not in prep.resolution


def test_un_coequipier_present_est_une_cible_qui_existe(rs, pack, partie, monkeypatch):
    """Mesuré en partie à deux : « je lis ses appuis pour le faire trébucher »
    visait Raiden, et l'arbitre répondait « Raiden (appuis et équilibre) n'est
    pas ici ». Un duel amical entre joueurs se joue au jet, il ne se refuse pas."""
    session, camp, pj = partie
    autre = Character(campaign_id=camp.id, nom="Hana", is_pc=True,
                      location_id=pj.location_id, stats=rs.stats_defaut(),
                      ressources=rs.ressources_defaut())
    # Un rival hostile est là aussi : c'est lui que le moteur prenait pour
    # cible quand le nom ne correspondait à aucun PNJ.
    rival = Character(campaign_id=camp.id, nom="Kazuki", is_pc=False,
                      role_campagne="antagoniste", location_id=pj.location_id,
                      stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
    session.add_all([autre, rival])
    session.commit()
    from app.llm import provider
    vrai = provider.MockProvider.json

    def json_duel(self, system, user, schema, **kw):
        out = vrai(self, system, user, schema, **kw)
        if "action_type" in schema.get("properties", {}):
            out.update(action_type="combat", cible="Hana (appuis et équilibre)")
        return out
    monkeypatch.setattr(provider.MockProvider, "json", json_duel)
    a = moteur.arbitrer(session, camp, pj, "Je fais trébucher Hana.", rs, pack)
    assert a.annonce and not a.impossible
    assert a.intent["cible"] == "Hana"
    prep = moteur.resoudre(session, camp, pj, a, rs, pack)
    assert "check" in prep.resolution and "combat" not in prep.resolution, \
        "un duel entre joueurs n'ouvre pas de rencontre"


def test_une_cible_absente_reste_impossible(rs, pack, partie, monkeypatch):
    session, camp, pj = partie
    from app.llm import provider
    vrai = provider.MockProvider.json

    def json_absent(self, system, user, schema, **kw):
        out = vrai(self, system, user, schema, **kw)
        if "action_type" in schema.get("properties", {}):
            out.update(action_type="combat", cible="Madara")
        return out
    monkeypatch.setattr(provider.MockProvider, "json", json_absent)
    a = moteur.arbitrer(session, camp, pj, "J'attaque Madara.", rs, pack)
    assert a.impossible == "Madara n'est pas ici."
