"""Le générateur de destinée est déterministe par graine : on peut donc
verrouiller son comportement par des tests, y compris statistiquement."""
from collections import Counter

import pytest

from app.engine import destiny as dst
from app.lore.pack import charger as charger_pack
from app.rules.loader import charger as charger_ruleset

RARES = ("rare", "tres_rare", "legendaire")


@pytest.fixture(scope="module")
def monde():
    return charger_pack("naruto"), charger_ruleset("naruto")


def echantillon(monde, origine, clan, n=120):
    pack, rs = monde
    return [dst.generer(pack, rs, origine=origine, clan_id=clan,
                        village_id="konoha", specialisation="ninjutsu",
                        annee=0, graine=5000 + i) for i in range(n)]


def test_aucun_trait_rare_actif_a_la_creation(monde):
    """La règle qui interdit « Mangekyô + Rinnegan + bijû » dès le départ :
    un trait rare ou plus entre TOUJOURS en latent."""
    for origine, clan in (("clan_connu", "uchiha"), ("sans_clan", ""),
                          ("clan_mineur", "fuma")):
        for d in echantillon(monde, origine, clan, n=60):
            actifs_rares = [t for t in d.actifs if t.rarete in RARES]
            assert not actifs_rares, f"{origine} : {[t.libelle for t in actifs_rares]}"


def test_lignee_de_clan_reservee_au_clan(monde):
    """Appartenir au clan est une identité ; porter la lignée est un potentiel.
    Un sans-clan ne peut pas éveiller de Sharingan par hasard."""
    hors_clan = echantillon(monde, "sans_clan", "", n=120)
    noms = {t.ref for d in hors_clan for t in d.traits}
    assert "lignee_sharingan" not in noms
    assert "lignee_byakugan" not in noms


def test_le_clan_rend_sa_lignee_probable_sans_la_garantir(monde):
    """Chez un Uchiha, le Sharingan est le trait rare LE PLUS probable —
    pas un acquis. Le vrai verrou est dans les conditions d'éveil."""
    tirages = echantillon(monde, "clan_connu", "uchiha", n=120)
    avec = sum(1 for d in tirages if any(t.ref == "lignee_sharingan" for t in d.traits))
    assert avec > 0, "un Uchiha ne peut jamais l'obtenir : multiplicateur cassé"
    assert avec < len(tirages), "tous les Uchiha l'obtiennent : ce n'est plus un potentiel"


def test_budget_latent_respecte(monde):
    for d in echantillon(monde, "sans_clan", "", n=60):
        assert sum(t.cout for t in d.latents) <= d.budget_latent
        assert sum(t.cout for t in d.actifs) <= d.budget_actif


def test_au_plus_un_legendaire(monde):
    for d in echantillon(monde, "sans_clan", "", n=120):
        assert sum(1 for t in d.traits if t.rarete == "legendaire") <= 1


def test_sans_clan_a_le_plus_gros_potentiel(monde):
    """Un clan est un plafond autant qu'un socle : c'est voulu, et c'est
    ce qui rend les quatre origines réellement différentes."""
    pack, rs = monde
    assert rs.origines["sans_clan"]["budget_latent"] > \
           rs.origines["clan_connu"]["budget_latent"]


def test_prerequis_ajoutes(monde):
    """Un Hyôton sans affinité Suiton serait incohérent : le prérequis doit
    être ajouté automatiquement."""
    for d in echantillon(monde, "sans_clan", "", n=120):
        refs = {t.ref for t in d.traits}
        if "hyoton_heritage" in refs:
            assert "affinite_suiton" in refs


def test_profils_varient_selon_origine(monde):
    """Le profil donne la silhouette. Un enfant de clan et un orphelin ne
    doivent pas produire les mêmes."""
    clan = Counter(d.profil for d in echantillon(monde, "clan_connu", "uchiha", n=80))
    seul = Counter(d.profil for d in echantillon(monde, "sans_clan", "", n=80))
    assert "enfant_de_clan" in clan
    assert "sans_lignee" in seul
    assert clan != seul


def test_reproductible_par_graine(monde):
    pack, rs = monde
    kw = dict(origine="sans_clan", clan_id="", village_id="konoha",
              specialisation="ninjutsu", annee=0, graine=42)
    a = dst.generer(pack, rs, **kw)
    b = dst.generer(pack, rs, **kw)
    assert [t.ref for t in a.traits] == [t.ref for t in b.traits]
    assert a.profil == b.profil


def test_propositions_distinctes(monde):
    """Trois variantes de la même chose rendraient le choix factice."""
    pack, rs = monde
    props = dst.proposer(pack, rs, nombre=3, origine="sans_clan", clan_id="",
                         village_id="konoha", specialisation="ninjutsu",
                         annee=0, graine=77)
    assert len(props) == 3
    assert len({p.profil for p in props}) >= 2
    tres_rares = sum(1 for p in props
                     if any(t.rarete in ("tres_rare", "legendaire") for t in p.traits))
    assert tres_rares <= 1


def test_eligibilite_epoque(monde):
    """Otogakure n'existe qu'à partir de l'an -45 (lore Solve : les Raimei
    quittent Suna pour la fonder) : un trait qui en dépend ne doit pas
    apparaître avant."""
    pack, _ = monde
    oto = next(v for v in pack.liste("villages") if v["id"] == "oto")
    assert not pack.actif_a(oto, -60)
    assert pack.actif_a(oto, 0)
