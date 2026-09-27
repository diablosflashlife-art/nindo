"""« Je m'en remets au destin » doit faire arriver quelque chose.

Avant les archétypes, le tirage donnait ce que la loi des grands nombres
donne : un sans-clan de Konoha en ninjutsu, huit fois sur dix. Ces tests
verrouillent que le sort tire un archétype, qu'il impose ses traits latents
sans passer par le budget, et qu'il ne contourne jamais l'éligibilité.
"""
import random

import pytest

from app.engine import destiny as dst
from app.engine.creation import Fiche, tirer_fiche, traits_de_l_archetype, valider
from app.lore.pack import charger as charger_pack
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


def _tirage(pack, rs, graine):
    annee = pack.annee_de("naruto_p1")
    fiche = tirer_fiche(pack, rs, Fiche(nom="Tenma"), annee, graine=graine)
    valider(pack, rs, fiche, annee)
    forces = traits_de_l_archetype(pack, rs, fiche, random.Random(graine))
    arch = pack.archetype(fiche.archetype) or {}
    d = dst.generer(pack, rs, origine=fiche.origine, clan_id=fiche.clan_id,
                    village_id=fiche.village_id, specialisation=fiche.specialisation,
                    annee=annee, graine=graine, traits_forces=forces,
                    profil_force=arch.get("profil"))
    return fiche, arch, d


def test_le_sort_tire_toujours_un_archetype(pack, rs):
    for g in range(30):
        fiche, arch, _ = _tirage(pack, rs, g)
        assert fiche.archetype and arch, f"graine {g} : aucun archétype"


def test_les_archetypes_rares_arrivent_vraiment(pack, rs):
    """C'est tout le point : un réceptacle, un marqué, un ermite doivent
    sortir sur une centaine de tirages, pas une fois par siècle."""
    vus = {_tirage(pack, rs, g)[1]["id"] for g in range(120)}
    assert {"receptacle", "marque", "ermite"} & vus, vus


def test_le_trait_impose_est_bien_dans_la_destinee(pack, rs):
    trouve = False
    for g in range(80):
        fiche, arch, d = _tirage(pack, rs, g)
        if arch["id"] != "receptacle":
            continue
        trouve = True
        refs = {t.ref for t in d.traits}
        assert "jinchuriki_latent" in refs, "le réceptacle n'en est pas un"
        assert not next(t for t in d.traits if t.ref == "jinchuriki_latent").actif, \
            "un trait légendaire n'est jamais actif à la création"
    assert trouve


def test_l_archetype_borne_l_origine(pack, rs):
    for g in range(80):
        fiche, arch, _ = _tirage(pack, rs, g)
        if arch.get("origines"):
            assert fiche.origine in arch["origines"], (arch["id"], fiche.origine)


def test_l_archetype_ne_contourne_pas_l_eligibilite(pack, rs):
    """Un sans-clan ne reçoit pas de Sharingan, archétype ou pas."""
    annee = pack.annee_de("naruto_p1")
    d = dst.generer(pack, rs, origine="sans_clan", clan_id="", village_id="konoha",
                    specialisation="ninjutsu", annee=annee, graine=3,
                    traits_forces=["lignee_sharingan"])
    assert "lignee_sharingan" not in {t.ref for t in d.traits}


def test_le_prerequis_d_un_trait_impose_vient_avec(pack, rs):
    """Hyôton demande Suiton : imposer l'un amène l'autre."""
    annee = pack.annee_de("naruto_p1")
    d = dst.generer(pack, rs, origine="sans_clan", clan_id="", village_id="kiri",
                    specialisation="ninjutsu", annee=annee, graine=5,
                    traits_forces=["hyoton_heritage"])
    refs = {t.ref for t in d.traits}
    assert {"hyoton_heritage", "affinite_suiton"} <= refs


def test_un_ninja_de_suna_ne_tire_pas_uzumaki_s_il_a_ses_propres_clans(pack, rs):
    annee = pack.annee_de("naruto_p1")
    for g in range(200):
        fiche = tirer_fiche(pack, rs, Fiche(nom="X"), annee, graine=g)
        if fiche.village_id == "suna" and fiche.clan_id:
            clan = pack.clan(fiche.clan_id)
            assert clan.get("village") == "suna", (g, fiche.clan_id)


def test_la_specialisation_de_l_archetype_est_respectee(pack, rs):
    for g in range(120):
        fiche, arch, _ = _tirage(pack, rs, g)
        if arch.get("specialisation"):
            assert fiche.specialisation == arch["specialisation"]
