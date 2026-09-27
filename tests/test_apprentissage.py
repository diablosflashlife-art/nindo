"""Apprendre une technique — la seule chose que le jeu ne savait pas faire.

Le répertoire d'un personnage était figé à la création : trois techniques au
tour 1, les mêmes trois au tour 80. Ces tests verrouillent les trois chemins
(maître, parchemin, travail), ce qu'ils coûtent, et surtout les REFUS — c'est
là qu'un système d'apprentissage devient un distributeur à techniques.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import apprentissage as appr
from app.lore.pack import charger as charger_pack
from app.models import (Campaign, Character, CharacterTechnique, Event,
                        Location, Quest, Relation)
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=7, ruleset=rs.data, tour=10,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        lieu = Location(campaign_id=camp.id, nom="Terrain d'entraînement 3")
        session.add(lieu)
        session.commit()
        session.refresh(lieu)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       specialisation="ninjutsu", grade="genin",
                       location_id=lieu.id, stats=rs.stats_defaut(),
                       ressources=rs.ressources_defaut())
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj, lieu


def _offre(session, camp, pack, rs, pj, tid):
    return next((o for o in appr.offres(session, camp, pack, rs, pj)
                 if o["id"] == tid), None)


# ==========================================================================
# LE TRAVAIL — le filet qui ne dépend de personne
# ==========================================================================
def test_on_travaille_dans_sa_specialite(rs, pack, partie):
    session, camp, pj, _ = partie
    offres = appr.offres(session, camp, pack, rs, pj)
    seuls = [o for o in offres if o["source"] == "travail"]
    assert seuls, "un ninjutsuka doit pouvoir travailler un ninjutsu"
    assert [o for o in seuls if o["categorie"] == "ninjutsu" and o["rang"] != "E"]
    assert all(o["categorie"] == "ninjutsu" or o["rang"] == "E" for o in seuls)


def test_on_ne_travaille_pas_seul_hors_de_sa_specialite(rs, pack, partie):
    session, camp, pj, _ = partie
    offres = appr.offres(session, camp, pack, rs, pj)
    assert not [o for o in offres
                if o["source"] == "travail" and o["categorie"] == "genjutsu"
                and o["rang"] != "E"]


def test_les_bases_de_l_academie_restent_toujours_travaillables(rs, pack, partie):
    """Le PLANCHER : une spécialité qui ne compte que deux entrées dans le pack
    laissait son spécialiste sans rien à travailler. Un diplômé de l'Académie
    peut toujours reprendre les bases au poteau d'entraînement."""
    session, camp, pj, _ = partie
    pj.specialisation = "fuinjutsu"
    session.commit()
    seuls = [o for o in appr.offres(session, camp, pack, rs, pj)
             if o["source"] == "travail"]
    assert seuls, "aucun chemin ouvert : un personnage ne doit jamais rester bloqué"
    assert any(o["rang"] == "E" for o in seuls)


def test_chaque_specialite_a_de_quoi_grandir(rs, pack):
    """Les voies autres que le ninjutsu n'avaient qu'une ou deux entrées : un
    spécialiste de fûinjutsu connaissait tout son art au tour 1."""
    ouverts = set(appr.rangs_ouverts(rs, "genin"))
    for spec in rs.specialisations:
        cat = appr.CATEGORIE_DE_SPECIALITE.get(spec, spec)
        dispo = [t for t in pack.techniques()
                 if t.get("categorie") == cat and t.get("rang") in ouverts
                 and not t.get("clan")]
        assert len(dispo) >= 3, f"{spec} n'offre que {len(dispo)} technique(s)"


def test_une_vieille_fiche_retrouve_sa_specialite(rs, pack, partie):
    """`specialisation` n'existait pas : on la retrouve dans les techniques
    de départ, qui ont justement été choisies depuis elle."""
    session, camp, pj, _ = partie
    pj.specialisation = ""
    session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                   technique_ref="kanashibari", maitrise=40))
    session.commit()
    assert appr.specialite(session, pack, pj) == "genjutsu"


# ==========================================================================
# LE GRADE, LA CARACTÉRISTIQUE, LES PRÉREQUIS
# ==========================================================================
def test_un_genin_ne_voit_pas_les_hauts_rangs(rs, pack, partie):
    session, camp, pj, _ = partie
    ouverts = set(appr.rangs_ouverts(rs, "genin"))
    assert "A" not in ouverts and "S" not in ouverts
    assert all(o["rang"] in ouverts
               for o in appr.offres(session, camp, pack, rs, pj))


def test_une_caracteristique_trop_basse_bloque_sans_cacher(rs, pack, partie):
    """On montre quand même : une technique qu'on ne voit pas ne sera jamais
    visée, et c'est ce qui donne un objectif d'entraînement."""
    session, camp, pj, _ = partie
    lointaines = [o for o in appr.offres(session, camp, pack, rs, pj)
                  if not o["pret"]]
    for o in lointaines:
        assert o["manque"] or o["stat_valeur"] < o["stat_requise"]


def test_une_technique_deja_connue_ne_s_offre_plus(rs, pack, partie):
    session, camp, pj, _ = partie
    avant = {o["id"] for o in appr.offres(session, camp, pack, rs, pj)}
    cible = next(iter(avant))
    session.add(CharacterTechnique(campaign_id=camp.id, character_id=pj.id,
                                   technique_ref=cible, maitrise=40))
    session.commit()
    assert cible not in {o["id"] for o in appr.offres(session, camp, pack, rs, pj)}


def test_une_technique_de_clan_reste_au_clan(rs, pack, partie):
    session, camp, pj, _ = partie
    pj.clan_ref = ""
    session.commit()
    assert "kagemane" not in {o["id"]
                              for o in appr.offres(session, camp, pack, rs, pj)}


# ==========================================================================
# LE MAÎTRE
# ==========================================================================
def _sensei(session, camp, lieu, tid, maitrise=80):
    c = Character(campaign_id=camp.id, nom="Hiroshi", location_id=lieu.id,
                  role_campagne="sensei", grade="jonin")
    session.add(c)
    session.commit()
    session.refresh(c)
    session.add(CharacterTechnique(campaign_id=camp.id, character_id=c.id,
                                   technique_ref=tid, maitrise=maitrise))
    session.commit()
    return c


def test_un_maitre_present_ouvre_une_technique_hors_specialite(rs, pack, partie):
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari")
    o = _offre(session, camp, pack, rs, pj, "kanashibari")
    assert o and o["source"] == "maitre" and o["detail"] == "Hiroshi"


def test_un_maitre_raccourcit_l_entrainement(rs, pack, partie):
    session, camp, pj, lieu = partie
    seul = appr.tours_pour(rs, "D")
    _sensei(session, camp, lieu, "kanashibari")
    assert _offre(session, camp, pack, rs, pj, "kanashibari")["tours"] < seul


def test_on_n_apprend_pas_de_quelqu_un_qui_te_meprise(rs, pack, partie):
    session, camp, pj, lieu = partie
    c = _sensei(session, camp, lieu, "kanashibari")
    session.add(Relation(campaign_id=camp.id, source_id=c.id, cible_id=pj.id,
                         nature="rancune", valeur=-40))
    session.commit()
    assert _offre(session, camp, pack, rs, pj, "kanashibari") is None


def test_un_maitre_ailleurs_n_enseigne_rien(rs, pack, partie):
    session, camp, pj, lieu = partie
    c = _sensei(session, camp, lieu, "kanashibari")
    c.location_id = None
    session.commit()
    assert _offre(session, camp, pack, rs, pj, "kanashibari") is None


def test_un_maitre_qui_ne_maitrise_pas_n_enseigne_pas(rs, pack, partie):
    """En dessous du seuil on ne SAIT pas la technique : on la connaît."""
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari", maitrise=30)
    assert _offre(session, camp, pack, rs, pj, "kanashibari") is None


# ==========================================================================
# LE PARCHEMIN
# ==========================================================================
def test_un_parchemin_ouvre_et_se_consomme(rs, pack, partie):
    session, camp, pj, _ = partie
    pj.inventaire = [f"{appr.PREFIXE_PARCHEMIN} Kanashibari no Jutsu"]
    session.commit()
    o = _offre(session, camp, pack, rs, pj, "kanashibari")
    assert o and o["source"] == "parchemin"

    appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    assert not pj.inventaire, "le parchemin doit être consommé"
    assert "kanashibari" in appr.connues(session, pj)


def test_un_objet_ordinaire_n_est_pas_un_parchemin(rs, pack, partie):
    session, camp, pj, _ = partie
    pj.inventaire = ["Kanashibari no Jutsu"]      # pas le préfixe
    session.commit()
    assert _offre(session, camp, pack, rs, pj, "kanashibari") is None


def test_le_parchemin_se_reconnait_malgre_les_accents(rs, pack, partie):
    session, camp, pj, _ = partie
    pj.inventaire = [f"{appr.PREFIXE_PARCHEMIN} kanashibari no jutsu"]
    session.commit()
    assert _offre(session, camp, pack, rs, pj, "kanashibari") is not None


# ==========================================================================
# CE QUE ÇA COÛTE
# ==========================================================================
def test_apprendre_avance_l_horloge(rs, pack, partie):
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari")
    avant = camp.tour
    tours = _offre(session, camp, pack, rs, pj, "kanashibari")["tours"]
    appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    assert camp.tour == avant + tours, "l'entraînement doit coûter du temps"


def test_apprendre_laisse_une_trace(rs, pack, partie):
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari")
    appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    evs = session.exec(select(Event).where(Event.type == "apprentissage")).all()
    assert len(evs) == 1 and "Hiroshi" in evs[0].resume


def test_on_apprend_mieux_de_quelqu_un(rs, pack, partie):
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari")
    appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    ct = appr.connues(session, pj)["kanashibari"]
    assert ct.maitrise > int(rs.data["apprentissage"]["maitrise_initiale"])


def test_les_echeances_courent_pendant_l_entrainement(rs, pack, partie):
    session, camp, pj, lieu = partie
    _sensei(session, camp, lieu, "kanashibari")
    session.add(Quest(campaign_id=camp.id, titre="Escorte", rang="D",
                      statut="acceptée", echeance_tour=camp.tour))
    session.commit()
    appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    q = session.exec(select(Quest)).first()
    assert q.statut == "échouée", \
        "s'entraîner ne doit pas mettre le monde en pause"


# ==========================================================================
# LES REFUS
# ==========================================================================
def test_une_technique_hors_de_portee_est_refusee(rs, pack, partie):
    session, camp, pj, _ = partie
    with pytest.raises(appr.ApprentissageRefuse):
        appr.apprendre(session, camp, pack, rs, pj, "kanashibari")


def test_une_technique_inventee_est_refusee(rs, pack, partie):
    session, camp, pj, _ = partie
    with pytest.raises(appr.ApprentissageRefuse):
        appr.apprendre(session, camp, pack, rs, pj, "vol_plane_du_heron")


def test_un_refus_ne_change_rien(rs, pack, partie):
    session, camp, pj, _ = partie
    avant = camp.tour
    with pytest.raises(appr.ApprentissageRefuse):
        appr.apprendre(session, camp, pack, rs, pj, "kanashibari")
    assert camp.tour == avant and "kanashibari" not in appr.connues(session, pj)


# ==========================================================================
# LES PARCHEMINS QU'ON TROUVE
# ==========================================================================
def test_un_parchemin_trouve_est_toujours_lisible(rs, pack, partie):
    """Un rouleau qu'on ne peut pas lire n'est pas une récompense, c'est une
    ligne d'inventaire."""
    session, camp, pj, _ = partie
    trouve = False
    for tour in range(60):
        camp.tour = tour
        if appr.tirer_parchemin(session, camp, pack, rs, pj):
            trouve = True
            break
    assert trouve, "aucun parchemin en soixante tirages"
    nom = pj.inventaire[-1].replace(appr.PREFIXE_PARCHEMIN, "").strip()
    cible = next(t for t in pack.techniques() if t["nom"] == nom)
    assert cible["rang"] in appr.rangs_ouverts(rs, pj.grade)
    assert _offre(session, camp, pack, rs, pj, cible["id"]) is not None


def test_le_tirage_est_deterministe(rs, pack, partie):
    session, camp, pj, _ = partie
    camp.tour = 12
    a = appr.tirer_parchemin(session, camp, pack, rs, pj, graine=3)
    pj.inventaire = []
    b = appr.tirer_parchemin(session, camp, pack, rs, pj, graine=3)
    assert a == b, "recharger une sauvegarde ne doit pas rejouer les dés"


# ==========================================================================
# LES SÉANCES JOUÉES — retour de partie : « on apprend sans scène, sans dé »
# ==========================================================================
def test_une_seance_reussie_fait_avancer_sans_tout_donner(rs, pack, partie):
    session, camp, pj, _ = partie
    o = _offre(session, camp, pack, rs, pj, "shunshin")      # rang D : 2 séances
    bloc, effets = appr.seance(session, camp, pack, rs, pj, o, "reussite")
    assert "SÉANCE D'ENTRAÎNEMENT" in bloc and "pas encore acquise" in bloc.lower()
    assert pj.entrainements["shunshin"] == 50
    assert "shunshin" not in appr.connues(session, pj)


def test_un_echec_apprend_un_peu(rs, pack, partie):
    session, camp, pj, _ = partie
    o = _offre(session, camp, pack, rs, pj, "shunshin")
    appr.seance(session, camp, pack, rs, pj, o, "echec")
    assert 0 < pj.entrainements["shunshin"] < 50


def test_a_cent_pour_cent_la_technique_est_acquise(rs, pack, partie):
    session, camp, pj, _ = partie
    o = _offre(session, camp, pack, rs, pj, "shunshin")
    appr.seance(session, camp, pack, rs, pj, o, "reussite")
    o = _offre(session, camp, pack, rs, pj, "shunshin")
    assert o["progression"] == 50
    bloc, effets = appr.seance(session, camp, pack, rs, pj, o, "reussite")
    session.commit()
    assert "TECHNIQUE ACQUISE" in bloc
    assert any("Technique apprise" in e for e in effets)
    assert "shunshin" in appr.connues(session, pj)
    assert "shunshin" not in (pj.entrainements or {})


def test_une_seance_impossible_dit_pourquoi(rs, pack, partie):
    session, camp, pj, _ = partie
    with pytest.raises(appr.ApprentissageRefuse):
        appr.verifier_seance(session, camp, pack, rs, pj, "amaterasu")
