"""Le combat en rounds — chantier B de Nindō 2.0 (docs/NINDO-2.md, pilier 2).

Initiative et ordre des tours, cartes d'action sans arbitre, effets réels des
techniques (contrôle, garde, doublures, soin, zone), objets, et le duel entre
joueurs joué contre la garde de l'autre.
"""
import random

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import combat, techniques as tech
from app.engine import turn as moteur
from app.lore.pack import charger as charger_pack
from app.models import (Campaign, Character, CharacterTechnique, Condition,
                        Location)
from app.rules import engine as regles
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs, pack):
    bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(bd)
    with Session(bd) as session:
        camp = Campaign(nom="test", graine=11, ruleset=rs.data, tour=6, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Clairière", danger=3)
        session.add(lieu)
        session.commit()
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True, location_id=lieu.id,
                       stats={**rs.stats_defaut(), "ninjutsu": 16, "genjutsu": 14, "vitesse": 14},
                       ressources={**rs.ressources_defaut(), "chakra": 40},
                       inventaire=["5 kunai", "Parchemin explosif", "Pilule de sang"])
        session.add(pj)
        session.commit()
        session.refresh(pj)
        for ref, m in (("kanashibari", 70), ("kawarimi", 60), ("kage_bunshin", 60),
                       ("katon_goukakyuu", 60), ("iryo_base", 60)):
            session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                           technique_ref=ref, maitrise=m))
        session.commit()
        arch = next(a for a in pack.adversaires(tier_max=2) if a["id"] == "detrousseur_route")
        renc = combat.ouvrir(session, camp, pack, rs, pj, archetypes=[arch, arch])
        yield session, camp, pj, renc


def _ennemis(session, renc):
    return combat.adverses(session, renc)


# ==========================================================================
# L'INITIATIVE
# ==========================================================================
def test_l_initiative_est_tiree_et_ordonnee(partie):
    session, camp, pj, renc = partie
    assert len(renc.initiative) == 3 and len(renc.ordre) == 3
    valeurs = [renc.initiative[str(i)] for i in renc.ordre]
    assert valeurs == sorted(valeurs, reverse=True)
    assert pj.id in renc.ordre


def test_les_plus_rapides_frappent_avant_le_joueur(partie, rs, pack):
    session, camp, pj, renc = partie
    # On force : tous les ennemis plus rapides que Kaito.
    ennemis = _ennemis(session, renc)
    renc.initiative = {str(pj.id): 1, **{str(c.id): 15 for c in ennemis}}
    renc.ordre = [c.id for c in ennemis] + [pj.id]
    session.add(renc)
    session.commit()
    issue = combat.echanger(session, camp, pack, rs, pj, renc, {"posture": "mesuree"})
    lignes = issue["lignes"]
    i_avant = next(i for i, l in enumerate(lignes) if l.startswith(ennemis[0].nom))
    i_moi = next(i for i, l in enumerate(lignes) if l.startswith("Kaito attaque"))
    assert i_avant < i_moi, lignes


# ==========================================================================
# LES EFFETS DES TECHNIQUES
# ==========================================================================
def test_chaque_technique_a_un_effet_lisible(pack):
    for t in pack.techniques():
        e = tech.effets(t)
        assert e["type"] in tech.LIBELLES, t["id"]
        assert tech.resume(t)


def test_un_genjutsu_fige_sa_cible(partie, rs, pack, monkeypatch):
    session, camp, pj, renc = partie
    cible = _ennemis(session, renc)[0]
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (18, [18]))
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "technique", "technique": "kanashibari", "cible": cible.nom})
    assert any("paralysé" in l for l in issue["lignes"])
    cond = session.exec(select(Condition).where(Condition.character_id == cible.id)).first()
    assert cond is not None and cond.effets.get("saute")
    assert pj.ressources["chakra"] < 40, "le genjutsu a coûté du chakra"
    # Au round suivant, la cible ne riposte pas.
    issue2 = combat.echanger(session, camp, pack, rs, pj, renc, {"posture": "mesuree"})
    assert any(f"{cible.nom} ne peut pas agir" in l for l in issue2["lignes"])


def test_la_substitution_leve_une_garde_et_esquive(partie, rs, pack):
    session, camp, pj, renc = partie
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "technique", "technique": "kawarimi"})
    assert any("garde +4" in l for l in issue["lignes"])
    session.refresh(renc)
    e = renc.effets_actifs[str(pj.id)]
    assert e["garde"] == 4 and e["esquive"] and e["echange"] == renc.echange


def test_les_doublures_encaissent(partie, rs, pack, monkeypatch):
    session, camp, pj, renc = partie
    pv = pj.ressources["pv"]
    combat.echanger(session, camp, pack, rs, pj, renc,
                    {"posture": "technique", "technique": "kage_bunshin"})
    session.refresh(renc)
    assert renc.effets_actifs[str(pj.id)]["clones"] >= 1, "posées, moins celles déjà consommées"
    # On remet deux doublures, et on mesure APRÈS le round où elles ont été
    # posées : les ripostes de ce round-là ont pu en consommer une.
    combat._poser_effet(renc, pj.id, clones=2)
    session.add(renc)
    session.commit()
    pv = pj.ressources["pv"]
    # Les ripostes touchent à coup sûr : un 20 pour tout le monde.
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (20, [20]))
    issue = combat.echanger(session, camp, pack, rs, pj, renc, {"posture": "defensive"})
    assert any("doublure" in l for l in issue["lignes"])
    assert pj.ressources["pv"] == pv, "les doublures ont pris les coups"


def test_une_technique_de_zone_frappe_tout_le_camp(partie, rs, pack, monkeypatch):
    session, camp, pj, renc = partie
    noms = [c.nom for c in _ennemis(session, renc)]
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (19, [19]))
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "technique", "technique": "katon_goukakyuu"})
    for nom in noms:
        assert any(l.startswith(f"Kaito attaque {nom}") for l in issue["lignes"]), nom


def test_un_soin_rend_des_pv(partie, rs, pack):
    session, camp, pj, renc = partie
    pj.ressources = {**pj.ressources, "pv": 20}
    session.add(pj)
    session.commit()
    combat.echanger(session, camp, pack, rs, pj, renc,
                    {"posture": "technique", "technique": "iryo_base"})
    assert pj.ressources["pv"] >= 20, "soigné, même si les ripostes ont suivi"


# ==========================================================================
# LES OBJETS
# ==========================================================================
def test_les_objets_utilisables_viennent_de_l_inventaire(partie, pack):
    _, _, pj, _ = partie
    refs = [o["ref"] for o in combat.objets_utilisables(pack, pj)]
    assert set(refs) == {"kunai", "parchemin_explosif", "pilule_sang"}


def test_le_parchemin_explosif_detone_et_se_consomme(partie, rs, pack, monkeypatch):
    session, camp, pj, renc = partie
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (19, [19]))
    issue = combat.echanger(session, camp, pack, rs, pj, renc,
                            {"posture": "mesuree", "objet": "parchemin_explosif"})
    assert any("Parchemin explosif" in l for l in issue["lignes"])
    assert "Parchemin explosif" not in pj.inventaire
    assert any(e.startswith("Parchemin explosif : utilisé") for e in issue["effets"])


def test_les_kunai_se_lancent_et_se_comptent(partie, rs, pack):
    session, camp, pj, renc = partie
    combat.echanger(session, camp, pack, rs, pj, renc, {"posture": "mesuree", "objet": "kunai"})
    assert "5 kunai" in pj.inventaire, "un kunai lancé se ramasse : pas consommé"


# ==========================================================================
# LES CARTES REMPLACENT L'ARBITRE
# ==========================================================================
def test_une_carte_prepare_le_round_sans_appel_au_modele(partie, rs, pack, monkeypatch):
    session, camp, pj, renc = partie
    from app.llm import provider
    appels = []
    vrai = provider.MockProvider.json
    monkeypatch.setattr(provider.MockProvider, "json",
                        lambda self, *a, **k: (appels.append(1), vrai(self, *a, **k))[1])
    cible = _ennemis(session, renc)[0].nom
    a = moteur.arbitrer(session, camp, pj, "", rs, pack,
                        choix={"action_code": "technique", "technique": "kanashibari", "cible": cible})
    assert not appels, "la carte fait foi : l'arbitre n'est pas appelé"
    assert a.en_combat and a.annonce
    assert a.intent["posture"] == "technique" and a.intent["technique"] == "kanashibari"
    assert a.combat["action"] == "Technique" and a.combat["technique"] == "Kanashibari no Jutsu"
    assert a.action.startswith("Technique — Kanashibari")
    assert [ct.id for ct in session.exec(select(CharacterTechnique).where(
        CharacterTechnique.technique_ref == "kanashibari")).all()] == a.liens_techniques


def test_sans_carte_l_arbitre_lit_le_texte(partie, rs, pack):
    session, camp, pj, renc = partie
    a = moteur.arbitrer(session, camp, pj, "Je frappe le premier.", rs, pack)
    assert a.en_combat and a.intent["posture"] == "offensive"


def test_le_round_se_resout_et_s_enregistre(partie, rs, pack):
    session, camp, pj, renc = partie
    a = moteur.arbitrer(session, camp, pj, "", rs, pack, choix={"action_code": "defendre"})
    prep = moteur.resoudre(session, camp, pj, a, rs, pack)
    assert prep.en_combat and prep.resolution["combat"]["echange"] == 1


# ==========================================================================
# LE DUEL ENTRE JOUEURS
# ==========================================================================
def test_un_duel_entre_joueurs_se_joue_contre_la_garde(rs, pack, monkeypatch):
    bd = create_engine("sqlite://")
    SQLModel.metadata.create_all(bd)
    with Session(bd) as session:
        camp = Campaign(nom="duel", graine=1, ruleset=rs.data, tour=3, phase="en_cours")
        session.add(camp)
        session.commit()
        lieu = Location(campaign_id=camp.id, nom="Dôjô")
        session.add(lieu)
        session.commit()
        a = Character(campaign_id=camp.id, nom="Raiden", is_pc=True, location_id=lieu.id,
                      stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
        b = Character(campaign_id=camp.id, nom="Hana", is_pc=True, location_id=lieu.id,
                      stats={**rs.stats_defaut(), "endurance": 16}, ressources=rs.ressources_defaut())
        session.add_all([a, b])
        session.commit()
        from app.llm import provider
        vrai = provider.MockProvider.json

        def json_duel(self, system, user, schema, **kw):
            out = vrai(self, system, user, schema, **kw)
            if "action_type" in schema.get("properties", {}):
                out.update(action_type="combat", cible="Hana")
            return out
        monkeypatch.setattr(provider.MockProvider, "json", json_duel)
        arb = moteur.arbitrer(session, camp, a, "Je balaie Hana.", rs, pack)
        assert arb.dc == 15 and arb.difficulte == "garde de Hana"
        assert arb.difficulte_label == "Garde de Hana"
        assert arb.chances["reussite"] < 45


def test_le_tour_de_table_joue_un_seul_round(partie, rs, pack):
    """Mesuré en partie à deux : chaque joueur déclenchait son propre round,
    les adversaires ripostaient deux fois et le compteur avançait de deux."""
    session, camp, pj, renc = partie
    from app.engine import table
    autre = Character(campaign_id=camp.id, nom="Hana", is_pc=True, location_id=pj.location_id,
                      stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
    session.add(autre)
    session.commit()
    renc.camp_joueur = renc.camp_joueur + [autre.id]
    renc.initiative = {**renc.initiative, str(autre.id): 9}
    renc.ordre = sorted(renc.initiative, key=lambda k: -renc.initiative[k])
    renc.ordre = [int(k) for k in renc.ordre]
    session.add(renc)
    session.commit()
    cible = _ennemis(session, renc)[0].nom
    entrees = [{"pj": pj, "action": "", "choix": {"action_code": "attaquer", "cible": cible}},
               {"pj": autre, "action": "", "choix": {"action_code": "defendre"}}]
    meneur, prep = table.preparer(session, camp, entrees, rs, pack)
    session.refresh(renc)
    assert renc.echange == 1
    assert prep.groupe and prep.resolution["combat"]["echange"] == 1
    assert prep.bloc.count("RÉSULTAT IMPOSÉ") == 1
    assert any(l.startswith("Kaito attaque") for l in prep.resolution["combat"]["lignes"])
    assert [j["nom"] for j in prep.resolution["joueurs"]] == ["Kaito", "Hana"]


def test_un_round_partage_qui_clot_la_rencontre_reste_un_round(partie, rs, pack, monkeypatch):
    """Mesuré en partie réelle : le dernier adversaire tombait pendant le
    round partagé, la rencontre se refermait, et chaque joueur se retrouvait
    rejoué comme un jet ordinaire (« Kazuki n'est pas présent »)."""
    session, camp, pj, renc = partie
    from app.engine import table
    autre = Character(campaign_id=camp.id, nom="Hana", is_pc=True, location_id=pj.location_id,
                      stats=rs.stats_defaut(), ressources=rs.ressources_defaut())
    session.add(autre)
    session.commit()
    renc.camp_joueur = renc.camp_joueur + [autre.id]
    renc.ordre = [pj.id, autre.id] + [c.id for c in _ennemis(session, renc)]
    for c in _ennemis(session, renc):
        c.ressources = {**c.ressources, "pv": 1}
        session.add(c)
    session.add(renc)
    session.commit()
    monkeypatch.setattr(regles, "roll_dice", lambda expr, rng=None: (19, [19]))
    entrees = [{"pj": pj, "action": "", "choix": {"action_code": "technique", "technique": "katon_goukakyuu"}},
               {"pj": autre, "action": "", "choix": {"action_code": "attaquer"}}]
    _, prep = table.preparer(session, camp, entrees, rs, pack)
    session.refresh(renc)
    assert renc.statut == "gagnee"
    assert prep.en_combat and prep.resolution["combat"]["statut"] == "gagnee"
    assert all("combat" in j["resolution"] for j in prep.resolution["joueurs"])
