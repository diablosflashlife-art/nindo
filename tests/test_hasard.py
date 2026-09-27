"""« Je m'en remets au destin » doit toujours donner quelque chose.

Mesuré avant correction sur 2 000 tirages : un tiers des personnages tirés au
sort n'avaient aucun trait rare — trois orphelins sur quatre, presque toutes
les lames et les soignants. Et un personnage sur deux naissait avec trois
affinités élémentaires actives.
"""
import random

import pytest

from app.engine import destiny as dst
from app.engine.creation import Fiche, tirer_fiche, traits_de_l_archetype, valider
from app.lore.pack import charger as charger_pack
from app.rules.loader import charger

FORTS = {"rare", "tres_rare", "legendaire"}


@pytest.fixture(scope="module")
def tirages():
    pack, rs = charger_pack("naruto"), charger("naruto")
    out = []
    for g in range(300):
        fiche = tirer_fiche(pack, rs, Fiche(nom="Essai"), 0, graine=g * 7919 + 13)
        valider(pack, rs, fiche, 0)
        arch = pack.archetype(fiche.archetype) or {}
        forces = traits_de_l_archetype(pack, rs, fiche, random.Random(g))
        d = dst.generer(pack, rs, origine=fiche.origine, clan_id=fiche.clan_id,
                        village_id=fiche.village_id, specialisation=fiche.specialisation,
                        annee=0, graine=g, traits_forces=forces,
                        profil_force=arch.get("profil"))
        out.append((fiche, forces, d))
    return out


def test_chaque_tirage_porte_un_vrai_secret(tirages):
    vides = [f.archetype for f, _, d in tirages
             if not any(t.rarete in FORTS for t in d.traits)]
    assert not vides, f"{len(vides)} tirages sans rien de rare : {set(vides)}"


def test_le_trait_de_l_archetype_est_toujours_attribue(tirages):
    manques = [(f.archetype, f.village_id, forces) for f, forces, d in tirages
               if not all(r in {t.ref for t in d.traits} for r in forces)]
    assert not manques, manques[:5]


def test_une_seule_nature_de_chakra_au_depart(tirages):
    """Une affinité exigée par un héritage (le Hyôton demande Suiton) est un
    prérequis, pas une nature de plus : elle ne compte pas."""
    pack = charger_pack("naruto")
    for _, _, d in tirages:
        requis = {((pack.trait(t.ref) or {}).get("eligibilite") or {}).get("requiert_trait")
                  for t in d.traits}
        libres = [t for t in d.traits if t.ref.startswith("affinite_") and t.ref not in requis]
        actives = [t for t in libres if t.actif]
        assert len(actives) <= 1 and len(libres) <= 2, [t.ref for t in libres]


def test_un_joueur_qui_rejoint_garde_le_village_de_l_equipe():
    pack, rs = charger_pack("naruto"), charger("naruto")
    for g in range(40):
        fiche = tirer_fiche(pack, rs, Fiche(nom="Hana"), 0, graine=g, village_impose="kumo")
        valider(pack, rs, fiche, 0)
        assert fiche.village_id == "kumo"
