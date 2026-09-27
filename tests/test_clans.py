"""Les clans voulus, village par village — et rien qui tombe à côté.

La liste est celle de la table (d'après le wiki Solve). Ces tests
verrouillent qu'elle est bien celle que propose la création, que chaque clan
de sang a une lignée réellement atteignable, et que chaque référence — lignée,
technique, trait — pointe sur quelque chose qui existe.
"""
import pytest

from app.engine import destiny as dst
from app.lore.pack import charger as charger_pack
from app.rules.loader import charger

VOULUS = {
    "kiri": {"hozuki", "yokushin", "karatachi", "hoshigaki"},
    "suna": {"jishaku", "ensho", "ayatsuri", "mugen"},
    "konoha": {"hyuga", "uchiha", "senju", "aburame"},
    "oto": {"raimei", "kaguya", "fuma", "salamandre"},
    "kumo": {"yotsuki", "chinoike", "narukami"},
    "iwa": {"kamizuru", "clan_bakuton"},
}


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


def test_chaque_village_a_exactement_ses_clans(pack):
    for village, attendus in VOULUS.items():
        presents = {c["id"] for c in pack.clans(village=village)}
        assert presents == attendus, (village, presents ^ attendus)


def test_aucun_clan_hors_liste(pack):
    tous = set().union(*VOULUS.values())
    assert {c["id"] for c in pack.liste("clans")} == tous


def test_aucun_clan_disperse(pack):
    """Un ninja de Suna ne doit plus se voir proposer un clan d'ailleurs."""
    assert not [c["id"] for c in pack.liste("clans") if c.get("disperse")]


def test_oto_est_jouable(pack):
    oto = pack.village("oto")
    assert oto.get("rang") == "majeur", "Oto doit apparaître à la création"
    assert oto.get("culture") and oto.get("parler")
    assert pack.actif_a(oto, pack.annee_de("naruto_p1"))


def test_chaque_clan_a_son_histoire_et_son_epithete(pack):
    for c in pack.liste("clans"):
        assert c.get("histoire") and c.get("epithete"), c["id"]
        assert c.get("bonus"), c["id"]


def test_chaque_lignee_citee_existe(pack):
    for c in pack.liste("clans"):
        if c.get("lignee"):
            assert pack.lignee(c["lignee"]), (c["id"], c["lignee"])


def test_chaque_technique_de_clan_existe(pack):
    for c in pack.liste("clans"):
        for t in c.get("techniques") or []:
            assert pack.technique(t), (c["id"], t)


def test_chaque_clan_de_sang_peut_eveiller_sa_lignee(pack, rs):
    """Un clan de sang dont la lignée n'a aucun trait de destinée est une
    promesse morte : l'héritage serait affiché, et jamais atteignable."""
    for c in pack.liste("clans"):
        if c.get("rang") != "majeur" or not c.get("lignee"):
            continue
        traits = [t for t in pack.traits()
                  if (t.get("accorde") or {}).get("capacite") == c["lignee"]
                  and c["id"] in ((t.get("eligibilite") or {}).get("clans") or [])]
        assert traits, f"{c['id']} : aucun trait n'accorde {c['lignee']}"


def test_la_lignee_sort_vraiment_pour_le_clan(pack, rs):
    """Sur quarante enfants Karatachi, le Kirisôgan doit apparaître."""
    annee = pack.annee_de("naruto_p1")
    vus = 0
    for g in range(40):
        d = dst.generer(pack, rs, origine="clan_connu", clan_id="karatachi",
                        village_id="kiri", specialisation="genjutsu",
                        annee=annee, graine=g)
        vus += any(t.ref == "lignee_kirisogan" for t in d.traits)
    assert vus >= 4, vus


def test_la_lignee_d_un_clan_ne_sort_jamais_ailleurs(pack, rs):
    annee = pack.annee_de("naruto_p1")
    for g in range(60):
        d = dst.generer(pack, rs, origine="clan_connu", clan_id="uchiha",
                        village_id="konoha", specialisation="ninjutsu",
                        annee=annee, graine=g)
        refs = {t.ref for t in d.traits}
        assert not refs & {"lignee_kirisogan", "lignee_sakin", "lignee_ketsuryugan"}
