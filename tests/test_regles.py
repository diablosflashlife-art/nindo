"""Le moteur de règles est déterministe et sans dépendance : c'est la seule
partie testable à 100 %. Garde ces tests verts — quand une partie déraille,
tu sauras que le problème vient de la couche IA."""
import random

import pytest

from app.rules.engine import roll_dice
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


def test_des(rs):
    total, jets = roll_dice("2d6+1", random.Random(42))
    assert len(jets) == 2
    assert total == sum(jets) + 1


def test_jet_suit_la_formule(rs):
    rs.rng = random.Random(1)
    r = rs.check({"taijutsu": 14}, "taijutsu", "normal")
    assert r.total == r.de + (14 - 10) // 2
    assert r.issue in {"echec", "echec_critique", "reussite",
                       "reussite_partielle", "reussite_critique"}


def test_arsenal_rendements_decroissants(rs):
    """Quarante techniques de rang E ne doivent PAS battre un Kage.
    C'est la pente naturelle des joueurs comme du modèle : il faut la bloquer."""
    collectionneur = rs.puissance(rs.stats_defaut(),
                                  [{"rang": "E", "maitrise": 100}] * 40, 1)
    specialiste = rs.puissance(rs.stats_defaut(),
                               [{"rang": "S", "maitrise": 90},
                                {"rang": "A", "maitrise": 80}], 1)
    assert specialiste["pe"] > collectionneur["pe"]
    assert collectionneur["tier"] <= 3


def test_maitrise_pese_dans_la_puissance(rs):
    faible = rs.puissance(rs.stats_defaut(), [{"rang": "A", "maitrise": 40}], 5)
    fort = rs.puissance(rs.stats_defaut(), [{"rang": "A", "maitrise": 95}], 5)
    assert fort["pe"] > faible["pe"]


def test_cout_persistant_fait_baisser_la_puissance(rs):
    """Le Mangekyô doit se payer vraiment, pas dans une phrase que le modèle oublie."""
    sain = rs.puissance(rs.stats_defaut(), [], 10)
    blesse = rs.puissance(rs.stats_defaut(), [], 10,
                          conditions=[{"effets": {"puissance": -25}}])
    assert blesse["pe"] < sain["pe"]


def test_tiers_ordonnes(rs):
    """Six ancres de puissance croissante doivent sortir dans le bon ordre."""
    base = rs.stats_defaut()
    ancres = []
    for boost, techs, niveau in [
        (0, [], 1),
        (2, [{"rang": "E", "maitrise": 50}], 2),
        (6, [{"rang": "D", "maitrise": 60}, {"rang": "C", "maitrise": 50}], 5),
        (12, [{"rang": "B", "maitrise": 70}, {"rang": "C", "maitrise": 80}], 10),
        (20, [{"rang": "A", "maitrise": 85}, {"rang": "B", "maitrise": 80}], 16),
        (30, [{"rang": "S", "maitrise": 95}, {"rang": "A", "maitrise": 90}], 24),
    ]:
        stats = {k: v + boost for k, v in base.items()}
        ancres.append(rs.puissance(stats, techs, niveau))
    pe = [a["pe"] for a in ancres]
    assert pe == sorted(pe), "les ancres ne sont plus ordonnées : formule cassée"
    tiers = [a["tier"] for a in ancres]
    assert tiers == sorted(tiers)
    assert tiers[0] < tiers[-1]


def test_regle_du_fosse(rs):
    """Au-delà d'un tier d'écart, le moteur ne lance PAS les dés : il impose
    les objectifs réellement ouverts."""
    assert rs.fosse(3, 3)["jet"] is True
    assert rs.fosse(4, 3)["jet"] is True          # difficile mais possible
    assert rs.fosse(4, 2)["jet"] is False         # plus un combat
    assert rs.fosse(6, 2)["objectifs"]


def test_cycle_elementaire(rs):
    """Une règle, pas un catalogue de vingt paires."""
    assert rs.avantage_element("katon", "futon") > 0
    assert rs.avantage_element("futon", "katon") < 0
    assert rs.avantage_element("katon", "raiton") == 0
    assert rs.avantage_element("katon", None) == 0


def test_progression(rs):
    niveau, xp, gagnes = rs.appliquer_xp(1, 0, 5000)
    assert niveau > 1 and gagnes > 0


def test_droits_de_grade(rs):
    """Le grade n'ajoute pas de dégâts : il ajoute des droits."""
    assert rs.droits("genin")["missions_max"] == "C"
    assert rs.droits("jonin")["missions_max"] == "S"
    assert rs.droits("genin")["escouade_max"] == 0
    assert rs.droits("commandant_jonin")["archives"] == "secretes"
