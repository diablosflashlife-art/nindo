"""Le monde n'a pas de centre : chaque village jouable doit être complet.

Le jeu a longtemps été pensé depuis Konoha : toute campagne, quel que soit son
village, naissait avec « Terrain d'entraînement 3 » et « Forêt frontalière »,
placés sur le plan de Konoha ; seules les institutions de Konoha existaient ;
le narrateur disait « le Hokage » partout. Ces tests passent chacun des six
villages au même crible.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import francais as fr
from app.engine.campaign import amorcer_monde
from app.lore import brief
from app.lore.pack import charger as charger_pack
from app.models import Campaign, Faction, Location
from app.rules.loader import charger

VILLAGES = ["konoha", "suna", "kiri", "kumo", "iwa", "oto"]
NOMS_DE_KONOHA = {"Terrain d'entraînement 3", "Forêt frontalière",
                  "Poste frontière du Nord", "Tour du Hokage"}


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


def _amorcer(rs, pack, village):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    session = Session(moteur)
    camp = Campaign(nom="t", graine=1, ruleset=rs.data, tour=0, phase="amorce")
    session.add(camp)
    session.commit()
    session.refresh(camp)
    amorcer_monde(session, camp, pack, rs, village)
    return session, camp


@pytest.mark.parametrize("village", VILLAGES)
def test_chaque_village_est_jouable(pack, village):
    v = pack.village(village)
    assert v and v.get("rang") == "majeur"
    assert v.get("culture") and v.get("parler") and v.get("dirigeant")


@pytest.mark.parametrize("village", VILLAGES)
def test_chaque_village_a_ses_propres_lieux(pack, rs, village):
    session, camp = _amorcer(rs, pack, village)
    lieux = session.exec(select(Location)).all()
    noms = {l.nom for l in lieux}
    assert len(lieux) >= 5
    if village != "konoha":
        assert not noms & NOMS_DE_KONOHA, f"{village} hérite des lieux de Konoha : {noms & NOMS_DE_KONOHA}"
    # un lieu sûr pour commencer, un lieu de frontière et un lieu dangereux
    assert min(l.danger for l in lieux) == 1
    assert any("frontière" in (l.tags or []) for l in lieux)
    assert any(l.danger >= 6 and not l.connu for l in lieux), "rien à découvrir"
    # des coordonnées dans le cadre du plan
    assert all(0 <= l.x <= 100 and 0 <= l.y <= 100 for l in lieux)
    assert len({(l.x, l.y) for l in lieux}) == len(lieux), "deux lieux au même endroit"


@pytest.mark.parametrize("village", VILLAGES)
def test_chaque_village_a_ses_institutions(pack, rs, village):
    session, camp = _amorcer(rs, pack, village)
    locales = [f for f in session.exec(select(Faction)).all()
               if (pack.get(f.lore_ref) or {}).get("village") == village]
    assert locales, f"aucune faction propre à {village}"


@pytest.mark.parametrize("village", VILLAGES)
def test_chaque_village_a_des_voisins(pack, village):
    assert any(village in t.get("entre", []) for t in pack.tensions()), \
        f"{village} n'a aucune relation avec les autres villages"


@pytest.mark.parametrize("village,titre", [
    ("konoha", "Hokage"), ("suna", "Kazekage"), ("kiri", "Mizukage"),
    ("kumo", "Raikage"), ("iwa", "Tsuchikage"), ("oto", "Tetsukage")])
def test_le_narrateur_connait_le_titre_du_kage(pack, village, titre):
    texte = brief._section_village(pack, village)
    assert titre in texte


@pytest.mark.parametrize("village", VILLAGES)
def test_les_lieux_se_disent_en_francais(pack, village):
    """« à Réseau des ponts » ou « à Dôme du Kazekage » ne sont pas du
    français. Chaque lieu de départ doit être reconnu par la grammaire."""
    for l in pack.lieux_de_depart(village):
        nom = l["nom"]
        if nom.split()[0].lower() in ("la", "le", "les", "l'"):
            continue
        assert fr.genre(nom) != fr.AUCUN, f"{nom} : article inconnu"


def test_oto_a_sa_faune(pack):
    assert [b for b in pack.liste("faune") if "son" in (b.get("pays") or [])]
