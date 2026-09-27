"""Le souffle de la narration.

Chaque tour recevait exactement la même consigne : « 150 à 280 mots, présent,
deuxième personne, termine sur une situation ouverte ». Au bout de vingt tours
tout se ressemble — un échange de coups s'étire comme une promenade, et la
révélation d'une destinée arrive avec le même souffle qu'un achat de rations.
"""
from app.engine import rythme


def _intent(action="autre"):
    return {"action_type": action, "resume": "…"}


def _check(issue="reussite", reussi=True):
    return {"check": {"issue": issue, "reussi": reussi}}


def test_un_echange_de_coups_est_court_et_sec():
    r = rythme.choisir(_intent("combat"), {"combat": {"statut": "en_cours"}},
                       en_combat=True)
    assert r == "echange"
    bas, haut = rythme.REGISTRES[r]["mots"]
    assert haut < rythme.REGISTRES["decouverte"]["mots"][1], \
        "un échange doit être plus court qu'une découverte"


def test_la_fin_d_un_affrontement_n_est_pas_un_echange_de_plus():
    """C'est le moment où l'on compte ce qu'il reste. Ça se raconte
    autrement."""
    assert rythme.choisir(_intent("combat"), {"combat": {"statut": "gagnee"}},
                          en_combat=True) == "bascule"
    assert rythme.choisir(_intent("combat"), {"combat": {"statut": "perdue"}},
                          en_combat=True) == "bascule"


def test_une_destinee_qui_s_ouvre_fait_respirer_le_tour_suivant():
    r = rythme.choisir(_intent("dialogue"), {}, en_combat=False,
                       revelation_recente=True)
    assert r == "bascule"


def test_chaque_nature_d_action_a_son_souffle():
    assert rythme.choisir(_intent("dialogue"), _check(), False) == "conversation"
    assert rythme.choisir(_intent("social"), _check(), False) == "conversation"
    assert rythme.choisir(_intent("exploration"), _check(), False) == "decouverte"
    assert rythme.choisir(_intent("repos"), _check(), False) == "respiration"


def test_un_echec_critique_se_raconte_sec():
    r = rythme.choisir(_intent("autre"), _check("echec_critique", False), False)
    assert r == "revers"


def test_trois_tours_du_meme_souffle_forcent_le_changement():
    """Un maître du jeu humain change d'allure sans qu'on le lui demande.
    Six découvertes d'affilée endorment."""
    suite = ["decouverte"] * rythme.LASSITUDE
    r = rythme.choisir(_intent("exploration"), _check(), False, recents=suite)
    assert r != "decouverte"


def test_l_anti_monotonie_ne_prime_jamais_sur_l_urgence():
    """On ne rend pas un combat « paisible » sous prétexte qu'il dure."""
    suite = ["echange"] * 6
    r = rythme.choisir(_intent("combat"), {"combat": {"statut": "en_cours"}},
                       en_combat=True, recents=suite)
    assert r == "echange"


def test_la_consigne_porte_une_longueur_et_une_sortie():
    for cle in rythme.REGISTRES:
        texte = rythme.consigne(cle)
        assert "Longueur" in texte and "Sortie" in texte and "Allure" in texte
        bas, haut = rythme.fourchette(cle)
        assert f"{bas} à {haut} mots" in texte
        assert bas < haut


def test_l_allure_de_la_table_raccourcit_ou_allonge():
    """Mesuré en partie réelle : 320 mots par tour, deux minutes de lecture à
    voix haute. La table choisit ; « court » est le défaut."""
    for cle in rythme.REGISTRES:
        court, normal, long_ = (rythme.fourchette(cle, a) for a in ("court", "normal", "long"))
        assert court[1] < normal[1] < long_[1]
        assert normal == rythme.REGISTRES[cle]["mots"]
    assert rythme.fourchette("ordinaire")[1] <= 180


def test_un_registre_inconnu_retombe_sur_l_ordinaire():
    assert "SCÈNE" in rythme.consigne("n'existe pas")


def test_un_creux_appelle_une_scene_de_groupe():
    """Sans mission ni combat, le village se racontait comme un couloir
    d'attente. Ce sont pourtant les scènes d'entre-deux dont on se souvient."""
    sans = rythme.consigne("ordinaire")
    avec = rythme.consigne("ordinaire", entre_deux=True)
    assert "AUCUNE URGENCE" not in sans
    assert "AUCUNE URGENCE" in avec
    assert "le calme est le sujet" in avec, \
        "le narrateur doit savoir qu'il ne faut PAS inventer un enjeu"
