"""Le combat, vérifié là où il peut réellement casser.

Deux niveaux, et ils ne se remplacent pas :

  RÈGLES     le moteur de règles, déterministe, sans base ni modèle. C'est là
             que se verrouillent le fossé, les leviers, les dégâts et la
             fabrique d'adversaires.
  RENCONTRE  la boucle complète sur une vraie base SQLite en mémoire, sans
             modèle de langage : l'échange, les blessures, la fuite, la
             clôture, et le fait qu'on ne meurt pas d'une rencontre perdue.

Les tests de règles tournent en millisecondes et disent exactement quelle
constante a bougé. Ceux de rencontre disent si le tout tient debout.
"""
import json
import random

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.engine import combat
from app.lore.pack import charger as charger_pack
from app.models import Campaign, Character, Condition
from app.rules.engine import Ruleset
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


# ==========================================================================
# RÈGLES
# ==========================================================================
def test_ruleset_survit_a_un_aller_retour_json():
    """Une campagne garde une COPIE de son ruleset dans une colonne JSON, et
    JSON n'a pas d'autre type de clé que le texte.

    Sans normalisation, `fosse[2]` et `tiers[4]` ne trouvaient plus rien sur
    toute partie rechargée : la règle du fossé était inopérante en jeu réel
    alors qu'elle passait tous les tests, qui chargeaient le YAML directement.
    """
    depuis_yaml = charger("naruto")
    recharge = Ruleset(data=json.loads(json.dumps(depuis_yaml.data)))
    assert recharge.fosse(2, 4)["jet"] is False
    assert recharge.fosse(2, 4)["objectifs"]
    assert recharge.tier_label(5) == depuis_yaml.tier_label(5)
    assert not recharge.tier_label(5).startswith("tier ")


def test_une_vieille_campagne_recupere_le_moteur_de_combat():
    """Une partie commencée avant le moteur de combat n'a ni postures, ni
    seuils de blessure : ses joueurs ne pourraient pas se battre du tout.

    On complète donc ce qui MANQUE — et seulement ça. Les valeurs que la
    campagne possède déjà restent les siennes, sinon on rééquilibrerait une
    partie en cours, ce qui est exactement la chose à ne jamais faire.
    """
    from app.rules.loader import charger_pour, lire

    ancien = lire("naruto")
    ancien["combat"] = {"attack_stat": "genjutsu", "defense_stat": "endurance"}
    ancien.pop("adversaires", None)
    ancien["resources"]["pv"]["default"] = 30      # l'équilibre de CETTE partie

    recupere = charger_pour(ancien, "naruto")
    assert len(recupere.postures) == 6, "les postures n'ont pas été récupérées"
    assert recupere.etats_combat(), "les seuils de blessure manquent toujours"
    assert recupere.ressources_pour_tier(3)["pv"] > 0
    assert recupere.combat["attack_stat"] == "genjutsu", \
        "une valeur de la campagne a été écrasée"
    assert recupere.ressources_defaut()["pv"] == 30, \
        "l'équilibre de la campagne a été écrasé"


def test_jet_oppose_donne_une_marge_et_une_issue(rs):
    rs.rng = random.Random(3)
    op = rs.oppose({"taijutsu": 16}, "taijutsu", {"endurance": 10}, "endurance")
    assert op.marge == op.total_a - op.total_b
    assert op.issue in {"touche_net", "touche", "effleure", "pare", "contre"}
    assert op.touche == (op.issue in ("touche_net", "touche", "effleure"))


def test_une_touche_franche_coute_plus_qu_une_egratignure(rs):
    """Sans le facteur par issue, effleurer coûtait presque aussi cher
    qu'ouvrir une garde, et un duel entre pairs durait deux échanges."""
    rs.rng = random.Random(11)
    nets = [rs.degats(12, 0, 0, "touche_net") for _ in range(60)]
    rs.rng = random.Random(11)
    effleures = [rs.degats(12, 0, 0, "effleure") for _ in range(60)]
    assert sum(nets) > sum(effleures)
    assert all(d >= 1 for d in nets + effleures)


def test_les_leviers_reduisent_l_ecart_sans_jamais_le_refermer(rs):
    """La promesse du genre, rendue mécanique — et sa limite.

    Trois leviers ramènent « ce n'est plus un combat » à « difficile mais
    possible ». Ils ne vont pas plus loin : sinon une équipe de genin abattrait
    un ANBU, ce qui viderait la règle de son sens.
    """
    seul = rs.fosse_effectif(2, 5, [])
    assert seul["jet"] is False and seul["ecart"] == 3

    prepare = rs.fosse_effectif(2, 5, ["terrain_prepare", "renseignement",
                                       "nombre"])
    assert prepare["ecart"] == 1
    assert prepare["jet"] is True

    trop = rs.fosse_effectif(2, 5, ["terrain_prepare", "renseignement",
                                    "nombre", "sacrifice", "contre_mesure"])
    assert trop["ecart"] == 1, "le plancher d'un cran doit tenir"


def test_les_leviers_ne_creusent_pas_un_ecart_favorable(rs):
    assert rs.fosse_effectif(5, 2, ["terrain_prepare"])["ecart"] == 3


def test_plafond_de_cumul_des_leviers(rs):
    assert rs.ampleur_leviers(["terrain_prepare"]) == 1
    assert rs.ampleur_leviers(["sacrifice"]) == 2
    assert rs.ampleur_leviers(
        ["terrain_prepare", "renseignement", "nombre", "sacrifice"]) == 3
    assert rs.ampleur_leviers(["terrain_prepare", "terrain_prepare"]) == 1


def test_un_adversaire_atteint_le_tier_demande(rs):
    """La puissance des adversaires n'est pas écrite à la main : rééquilibrer
    la table des tiers doit rééquilibrer tout le bestiaire du même geste."""
    for tier in (1, 2, 3, 4, 5):
        stats = rs.stats_pour_tier(tier, ["taijutsu", "vitesse"])
        obtenu = rs.puissance(stats, [], rs.niveau_pour_tier(tier))
        assert obtenu["tier"] >= tier, f"tier {tier} jamais atteint"
    assert (rs.ressources_pour_tier(5)["pv"]
            > rs.ressources_pour_tier(2)["pv"])


def test_les_blessures_se_posent_au_franchissement(rs):
    """Ce sont les FRANCHISSEMENTS qui blessent, pas le fait de rester sous le
    seuil : sinon chaque coup rouvrait la même plaie."""
    assert rs.etat_franchi(100, 60, 100)["code"] == "entaille"
    assert rs.etat_franchi(60, 55, 100) is None
    assert rs.etat_franchi(100, 10, 100)["code"] == "chancelant"
    assert rs.malus_etats(["blesse"]) < 0
    assert rs.malus_etats(["entaille", "blesse"]) < rs.malus_etats(["entaille"])


def test_moral_personne_ne_se_bat_jusqu_a_la_mort(rs):
    assert rs.chance_rupture(90, 0) == 0.0            # intact : il reste
    assert rs.chance_rupture(20, 0) > 0               # entamé : il peut rompre
    assert rs.chance_rupture(20, 2) > rs.chance_rupture(20, 0)
    assert rs.chance_rupture(5, 3, "bete_enragee") == 0.0


# ==========================================================================
# RENCONTRE — la boucle complète, sur base réelle, sans modèle
# ==========================================================================
@pytest.fixture
def partie(rs):
    """Une campagne minimale : un PJ de tier 2, aucun lieu, aucun allié.

    Volontairement dépouillée — on teste le moteur de combat, pas l'amorce.
    """
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=1,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       stats=rs.stats_pour_tier(2, ["taijutsu"]),
                       ressources=rs.ressources_defaut(), niveau=3, tier=2)
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj


def _ouvrir(session, camp, pack, rs, pj, ref="detrousseur_route"):
    return combat.ouvrir(session, camp, pack, rs, pj,
                         archetypes=[pack.adversaire(ref)])


def test_une_rencontre_s_ouvre_avec_des_adversaires_vivants(partie, pack, rs):
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    assert renc is not None
    ennemis = combat.adverses(session, renc)
    assert ennemis, "aucun adversaire fabriqué"
    assert all(c.nom and c.nom != "Inconnu" for c in ennemis), \
        "un adversaire sans nom n'est pas un adversaire"
    assert all(int(c.ressources["pv"]) > 0 for c in ennemis)
    assert combat.active(session, camp).id == renc.id


def test_une_seule_rencontre_a_la_fois(partie, pack, rs):
    session, camp, pj = partie
    a = _ouvrir(session, camp, pack, rs, pj)
    b = _ouvrir(session, camp, pack, rs, pj)
    assert a.id == b.id


def test_un_echange_fait_descendre_les_ressources(partie, pack, rs):
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    pv_avant = sum(int(c.ressources["pv"]) for c in combat.adverses(session, renc))

    touche = False
    for _ in range(8):
        if renc.statut != "en_cours":
            break
        combat.echanger(session, camp, pack, rs, pj, renc,
                        {"posture": "offensive", "resume": "je frappe"})
        camp.tour += 1
        restants = combat.adverses(session, renc)
        if sum(int(c.ressources["pv"]) for c in restants) < pv_avant:
            touche = True
            break
    assert touche, "huit échanges offensifs sans jamais entamer personne"
    assert renc.echange >= 1


def test_la_technique_coute_du_chakra(partie, pack, rs):
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    avant = int(pj.ressources["chakra"])
    combat.echanger(session, camp, pack, rs, pj, renc,
                    {"posture": "technique", "resume": "je forme les signes"})
    assert int(pj.ressources["chakra"]) < avant


def test_le_chakra_manquant_annule_la_technique(partie, pack, rs):
    session, camp, pj = partie
    pj.ressources = {**pj.ressources, "chakra": 0}
    session.add(pj)
    session.commit()
    renc = _ouvrir(session, camp, pack, rs, pj)
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "technique", "resume": "je forme les signes"})
    assert any("chakra" in l.lower() for l in issue["lignes"])
    assert int(pj.ressources["chakra"]) == 0


def test_une_manoeuvre_reussie_etablit_un_levier(partie, pack, rs):
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    for _ in range(12):
        if renc.leviers or renc.statut != "en_cours":
            break
        combat.echanger(session, camp, pack, rs, pj, renc,
                        {"posture": "manoeuvre", "levier": "terrain_prepare",
                         "resume": "je tends un fil entre deux poteaux"})
        camp.tour += 1
    assert "terrain_prepare" in renc.leviers


def test_face_a_un_adversaire_ecrasant_frapper_ne_mene_a_rien(partie, pack, rs):
    """La règle du fossé, vue du joueur : le moteur impose les objectifs
    réellement ouverts et frapper n'y fait rien avancer."""
    session, camp, pj = partie
    renc = combat.ouvrir(session, camp, pack, rs, pj,
                         archetypes=[pack.adversaire("chasseur_anbu")])
    assert renc.objectifs, "aucun objectif imposé face à un tier bien supérieur"

    pv_avant = int(combat.adverses(session, renc)[0].ressources["pv"])
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "offensive", "objectif": "retarder",
                             "resume": "je l'attaque"})
    assert int(combat.adverses(session, renc)[0].ressources["pv"]) == pv_avant
    assert renc.progres == 0, "frapper ne doit rien faire avancer"
    assert any("hors de portée" in l for l in issue["lignes"])


def test_l_objectif_tombe_des_que_l_ecart_est_referme(partie, pack, rs):
    """Tout le sens des leviers. Sans cette remise à niveau, le joueur restait
    prisonnier d'un « tu ne peux pas gagner » que la mécanique démentait."""
    session, camp, pj = partie
    renc = combat.ouvrir(session, camp, pack, rs, pj,
                         archetypes=[pack.adversaire("nukenin_confirme")])
    assert renc.objectifs

    renc.leviers = ["terrain_prepare", "renseignement"]
    session.add(renc)
    session.commit()
    combat.echanger(session, camp, pack, rs, pj, renc,
                    {"posture": "mesuree", "resume": "je l'attaque"})
    assert renc.fosse["jet"] is True
    assert not renc.objectifs and not renc.objectif


def test_un_pj_ne_meurt_pas_d_une_rencontre_perdue(partie, pack, rs):
    """Une mort sur un mauvais jet au tour 30 détruit une campagne. Le genre a
    toujours préféré la capture, la dette et la cicatrice."""
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    pj.ressources = {**pj.ressources, "pv": 1}
    session.add(pj)
    session.commit()

    for _ in range(14):
        if renc.statut != "en_cours":
            break
        combat.echanger(session, camp, pack, rs, pj, renc,
                        {"posture": "offensive", "resume": "je frappe"})
        camp.tour += 1

    assert renc.statut != "en_cours", "la rencontre ne s'est jamais close"
    if renc.statut == "perdue":
        assert pj.vivant is True
        assert int(pj.ressources["pv"]) >= 1
        assert pj.etats


def test_un_pj_meurt_en_difficulte_impitoyable(partie, pack, rs):
    session, camp, pj = partie
    camp.difficulte = "impitoyable"
    renc = _ouvrir(session, camp, pack, rs, pj)
    pj.ressources = {**pj.ressources, "pv": 1}
    session.add(pj)
    session.commit()
    for _ in range(14):
        if renc.statut != "en_cours":
            break
        combat.echanger(session, camp, pack, rs, pj, renc,
                        {"posture": "offensive", "resume": "je frappe"})
        camp.tour += 1
    if renc.statut == "perdue":
        assert pj.vivant is False


def test_le_repos_repare_ce_que_le_combat_a_cassé(partie, pack, rs):
    """Sans ce chemin, la première rencontre perdue condamne la campagne."""
    session, camp, pj = partie
    combat.pv_max(session, rs, pj)
    pj.ressources = {**pj.ressources, "pv": 3, "chakra": 1}
    session.add(pj)
    session.add(Condition(campaign_id=camp.id, character_id=pj.id,
                          code="blesse", libelle="Blessé", severite=2,
                          effets={"puissance": -4}))
    session.commit()

    combat.recuperer(session, camp, rs, pj, repos=True)
    assert int(pj.ressources["pv"]) > 3
    assert int(pj.ressources["chakra"]) > 1
    assert session.exec(
        __import__("sqlmodel").select(Condition).where(
            Condition.character_id == pj.id)).all() == []


def test_le_goutte_a_goutte_ne_depasse_pas_le_maximum(partie, pack, rs):
    session, camp, pj = partie
    maximum = combat.pv_max(session, rs, pj)
    combat.recuperer(session, camp, rs, pj)
    assert int(pj.ressources["pv"]) <= maximum


def test_une_rencontre_close_ne_laisse_personne_sur_le_lieu(partie, pack, rs):
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    combat._clore(session, camp, rs, renc, "rompue", pj, pack)
    assert renc.statut == "rompue"
    assert renc.tour_fin == camp.tour
    assert combat.adverses(session, renc) == []


def test_le_contexte_du_mj_ne_livre_jamais_les_points_de_vie(partie, pack, rs):
    """Un MJ qui connaît les PV annonce les PV, et le joueur se met à jouer
    contre une barre de vie au lieu d'une scène."""
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    ennemi = combat.adverses(session, renc)[0]
    texte = combat.etat_pour_contexte(session, camp, rs, renc, pj)
    assert ennemi.nom in texte
    assert str(int(ennemi.ressources["pv"])) not in texte
    assert any(mot in texte for mot in
               ("intact", "entamé", "blessé", "il tient à peine"))


def test_les_blessures_legeres_se_referment_avec_le_temps(partie, pack, rs):
    """`Condition.guerit_tour` était déclaré depuis le premier jour et lu par
    personne : une entaille se gardait jusqu'à la fin de la campagne."""
    session, camp, pj = partie
    renc = _ouvrir(session, camp, pack, rs, pj)
    combat._appliquer_degats(session, camp, rs, pj, 20)
    session.commit()

    blessures = session.exec(__import__("sqlmodel").select(Condition).where(
        Condition.character_id == pj.id)).all()
    assert blessures, "aucune blessure posée par un coup de vingt points"
    legere = min(blessures, key=lambda c: c.severite)
    assert legere.guerit_tour is not None, "la blessure n'a pas de date de fin"

    camp.tour = legere.guerit_tour
    effets = combat.soigner_le_temps(session, camp, pj, rs)
    assert any("refermé" in e for e in effets)
    restantes = {c.code for c in session.exec(
        __import__("sqlmodel").select(Condition).where(
            Condition.character_id == pj.id)).all()}
    assert legere.code not in restantes


def test_un_combo_exige_tout_le_repertoire_et_la_maitrise(partie, pack, rs):
    """Les synergies du lore n'étaient lues par personne. Elles le sont — et
    elles restent exigeantes : un combo qu'on déclenche par accident n'en est
    pas un."""
    from app.models import CharacterTechnique

    session, camp, pj = partie
    combo = pack.synergies("combo")[0]
    a, b = combo["requiert"][:2]
    seuil = int(combo.get("maitrise_min", 0))

    session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                   technique_ref=a, maitrise=seuil))
    session.commit()
    assert combat.combo_disponible(session, pack, rs, pj, a) is None, \
        "une seule moitié du combo suffisait"

    session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                   technique_ref=b, maitrise=max(0, seuil - 20)))
    session.commit()
    assert combat.combo_disponible(session, pack, rs, pj, a) is None, \
        "le seuil de maîtrise n'était pas opposé"

    lien = session.exec(__import__("sqlmodel").select(CharacterTechnique).where(
        CharacterTechnique.character_id == pj.id,
        CharacterTechnique.technique_ref == b)).first()
    lien.maitrise = seuil
    session.add(lien)
    session.commit()
    trouve = combat.combo_disponible(session, pack, rs, pj, a)
    assert trouve is not None and trouve["id"] == combo["id"]


def test_l_embuscade_respecte_le_plancher_de_danger(partie, pack, rs):
    """Le monde a des dents, mais ne harcèle pas : une place de village n'est
    pas un endroit où l'on se fait embusquer."""
    from app.models import Location

    session, camp, pj = partie
    sur = Location(campaign_id=camp.id, nom="Académie", danger=1)
    dangereux = Location(campaign_id=camp.id, nom="Forêt frontalière", danger=7)
    session.add(sur)
    session.add(dangereux)
    session.commit()
    assert combat.peut_embusquer(session, camp, rs, sur) is False
    assert combat.peut_embusquer(session, camp, rs, dangereux) is True
    assert combat.peut_embusquer(session, camp, rs, None) is False
