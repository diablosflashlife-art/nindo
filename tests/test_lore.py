"""Le lore, le dossier de scène et le générateur de missions.

Ces tests ne touchent ni la base, ni le réseau, ni le modèle : le pack, le
dossier et l'ossature des missions sont du calcul pur sur des données. C'est
volontaire — ce sont les parties qu'on modifie le plus souvent, donc celles
qui doivent se vérifier en une seconde.
"""
from __future__ import annotations

import pytest
import yaml

from app.config import ROOT
from app.lore import brief
from app.lore.pack import charger
from app.rules.engine import Ruleset


@pytest.fixture(scope="module")
def pack():
    return charger("naruto")


@pytest.fixture(scope="module")
def rs():
    data = yaml.safe_load((ROOT / "rulesets" / "naruto.yaml").read_text(encoding="utf-8"))
    return Ruleset(data=data)


class Bidon:
    """Un personnage minimal : le dossier lore n'a pas besoin de la base."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _pj(**kw):
    base = dict(clan_ref="uchiha", grade="genin", affinites=["katon"],
                inventaire=["10 shuriken", "5 kunai", "bandeau frontal"])
    base.update(kw)
    return Bidon(**base)


# ==========================================================================
# Le pack
# ==========================================================================
def test_les_nouvelles_collections_sont_chargees(pack):
    assert pack.armes(), "armes.yaml n'est pas chargé"
    assert pack.consommables()
    assert pack.faune("feu")
    assert pack.flore("feu")
    assert pack.mineraux("terre")
    assert pack.archetypes_mission("D")
    assert pack.bingo_book()
    assert pack.rumeurs()
    assert pack.sites_invocation()


def test_chaque_arme_a_un_emploi_tactique(pack):
    """`tactique` est le champ que le MJ lit vraiment. Une arme sans lui est
    une ligne de catalogue, pas un outil de scène."""
    muettes = [a["id"] for a in pack.armes() + pack.consommables()
               if not (a.get("tactique") or a.get("particularite"))]
    assert not muettes, f"sans emploi décrit : {muettes}"


def test_chaque_entree_du_bestiaire_justifie_une_mission(pack):
    sans = [x["id"] for cle in ("faune", "flore", "mineraux")
            for x in pack.liste(cle) if not x.get("accroche")]
    assert not sans, f"sans accroche : {sans}"


def test_les_identifiants_du_lore_sont_uniques():
    """Le pack indexe par identifiant : un doublon écrase silencieusement."""
    vus: dict[str, str] = {}
    doublons = []
    for fichier in sorted((ROOT / "lore" / "naruto").glob("*.yaml")):
        contenu = yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}
        for cle, valeur in contenu.items():
            if not isinstance(valeur, list):
                continue
            for item in valeur:
                if isinstance(item, dict) and "id" in item:
                    if item["id"] in vus:
                        doublons.append(f"{item['id']} ({vus[item['id']]} / {fichier.name})")
                    vus[item["id"]] = fichier.name
    assert not doublons, f"identifiants en double : {doublons}"


def test_les_archetypes_couvrent_tous_les_rangs(pack):
    for rang in ("D", "C", "B", "A", "S"):
        assert pack.archetypes_mission(rang), f"aucun archétype de rang {rang}"


def test_les_organisations_secretes_sont_ecartees_par_defaut(pack):
    publiques = {o["id"] for o in pack.organisations()}
    assert "racine" not in publiques and "akatsuki" not in publiques
    assert "anbu" in publiques
    assert pack.est_secrete("racine") and not pack.est_secrete("anbu")


# ==========================================================================
# Le dossier lore
# ==========================================================================
def test_le_dossier_parle_du_clan_present(pack):
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), pnjs=[Bidon(clan_ref="hyuga", grade="jonin")],
                      lieu=Bidon(danger=3))
    assert "Uchiha" in d and "Hyûga" in d
    # ce qui travaille le clan est ce qui rend un PNJ jouable
    assert "regarde avec méfiance" in d


def test_le_dossier_donne_les_techniques_et_le_materiel(pack):
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), lieu=Bidon(danger=2))
    assert "TECHNIQUES QU'ON PEUT VOIR ICI" in d
    assert "ÉQUIPEMENT EN MAIN" in d
    assert "Shuriken" in d and "Kunai" in d


def test_un_genin_seul_ne_voit_pas_de_technique_de_rang_eleve(pack):
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), lieu=Bidon(danger=1))
    assert "[A]" not in d and "[S]" not in d


def test_un_jonin_present_ouvre_les_rangs_superieurs(pack):
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), pnjs=[Bidon(clan_ref=None, grade="jonin")],
                      lieu=Bidon(danger=1))
    assert "[B]" in d


def test_le_dossier_ne_revele_pas_les_paliers_non_atteints(pack):
    """Le point le plus sensible du module.

    Les paliers supérieurs d'une lignée portent leurs déclencheurs — « la perte
    d'un être cher, par sa propre faute ». Un modèle qui les lit arrange la
    scène pour y arriver.
    """
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), lignee_ref="sharingan", lignee_palier=1,
                      lieu=Bidon(danger=2))
    assert "Un tomoe" in d, "le palier atteint doit être décrit"
    lignee = pack.lignee("sharingan")
    for palier in lignee["paliers"][1:]:
        assert palier["nom"] not in d, f"fuite du palier {palier['nom']}"
        if palier.get("declencheur"):
            assert palier["declencheur"] not in d


def test_sans_lignee_eveillee_rien_nest_dit(pack):
    d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                      pj=_pj(), lignee_ref="sharingan", lignee_palier=0)
    assert "LIGNÉE DU PERSONNAGE" not in d


def test_le_dossier_tient_son_budget(pack):
    for budget in (200, 600, 1400, 3000):
        d = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1",
                          annee=0, pj=_pj(), lieu=Bidon(danger=5),
                          budget_tokens=budget)
        # une section entière passe ou ne passe pas : on tolère le dépassement
        # d'une seule section, jamais un dossier qui ignore le budget
        assert brief.taille_estimee(d) <= budget + 400, \
            f"budget {budget} dépassé : {brief.taille_estimee(d)}"


def test_le_dossier_suit_le_village_et_la_region(pack):
    feu = brief.dossier(pack, village_ref="konoha", epoque_id="naruto_p1", annee=0,
                        pj=_pj(clan_ref=None), lieu=Bidon(danger=4))
    vent = brief.dossier(pack, village_ref="suna", epoque_id="naruto_p1", annee=0,
                         pj=_pj(clan_ref=None), lieu=Bidon(danger=4))
    assert "Volonté du Feu" in feu and "Volonté du Feu" not in vent
    assert "Cactus-outre" in vent and "Cactus-outre" not in feu


def test_le_dossier_suit_lepoque(pack):
    tot = brief.dossier(pack, village_ref="konoha", epoque_id="hiruzen",
                        annee=pack.annee_de("hiruzen"), pj=_pj(clan_ref=None))
    tard = brief.dossier(pack, village_ref="konoha", epoque_id="shippuden",
                         annee=pack.annee_de("shippuden"), pj=_pj(clan_ref=None))
    assert "Démon-Renard" in tard, "un événement passé doit être cité"
    assert "Démon-Renard" not in tot, "il n'a pas encore eu lieu"


# ==========================================================================
# Le générateur de missions
# ==========================================================================
def test_le_grade_plafonne_le_rang_de_mission(pack, rs):
    from app.engine.missions import ossature, rangs_autorises

    assert rangs_autorises(rs, "genin") == ["D", "C"]
    assert "S" in rangs_autorises(rs, "kage")

    pj = Bidon(id=1, nom="K", grade="genin", tier=2, village_ref="konoha")
    lieux = [Bidon(id=1, nom="Académie"), Bidon(id=2, nom="Forêt frontalière")]
    for tour in range(1, 60):
        camp = Bidon(id=1, graine=7, tour=tour, epoque="naruto_p1", ton="sombre")
        assert ossature(pack, rs, camp, pj, lieux)["rang"] in ("D", "C")


def test_les_missions_sont_variees(pack, rs):
    from app.engine.missions import ossature

    pj = Bidon(id=1, nom="K", grade="jonin", tier=4, village_ref="konoha")
    lieux = [Bidon(id=i, nom=n) for i, n in enumerate(
        ["Académie", "Tour du Kage", "Quartier marchand", "Forêt frontalière"], 1)]
    vues = set()
    for tour in range(1, 51):
        camp = Bidon(id=1, graine=999, tour=tour, epoque="naruto_p1", ton="sombre")
        o = ossature(pack, rs, camp, pj, lieux)
        vues.add((o["archetype"], o["complication"]))
    assert len(vues) >= 20, f"seulement {len(vues)} missions distinctes sur 50"


def test_une_extermination_ne_cible_pas_un_animal_inoffensif(pack, rs):
    """Les cerfs sacrés des Nara ne sont pas des nuisibles."""
    from app.engine.missions import ossature

    pj = Bidon(id=1, nom="K", grade="chunin", tier=3, village_ref="konoha")
    lieux = [Bidon(id=1, nom="Forêt frontalière")]
    for tour in range(1, 200):
        camp = Bidon(id=1, graine=3, tour=tour, epoque="naruto_p1", ton="sombre")
        o = ossature(pack, rs, camp, pj, lieux)
        if o["categorie"] == "extermination" and o["objet"]:
            bete = pack.get(o["objet"]["ref"]) or {}
            assert int(bete.get("danger", 9)) >= 2, f"{bete.get('nom')} est inoffensif"


def test_une_ossature_ne_cite_que_des_lieux_reels(pack, rs):
    from app.engine.missions import ossature

    pj = Bidon(id=1, nom="K", grade="chunin", tier=3, village_ref="konoha")
    noms = ["Académie", "Tour du Kage"]
    lieux = [Bidon(id=i, nom=n) for i, n in enumerate(noms, 1)]
    for tour in range(1, 30):
        camp = Bidon(id=1, graine=11, tour=tour, epoque="naruto_p1", ton="sombre")
        assert ossature(pack, rs, camp, pj, lieux)["lieu_nom"] in noms
