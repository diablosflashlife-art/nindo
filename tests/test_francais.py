"""Les petits mots qui font qu'une phrase est française.

Le défaut d'origine se lisait à chaque voyage : « Kaito s'est rendu à Tour du
Kage ». On écrivait `f"à {lieu.nom}"` partout, ce qui ne marche que pour un nom
propre. Ces tests verrouillent les deux choses qui comptent : les contractions,
et le fait que ce qu'on ne reconnaît PAS reste sans article.
"""
import pytest

from app.engine import francais as fr


# ==========================================================================
# LES LIEUX DE L'AMORCE — les six noms que toute campagne porte
# ==========================================================================
@pytest.mark.parametrize("nom,attendu", [
    ("Académie", "à l'Académie"),
    ("Tour du Kage", "à la Tour du Kage"),
    ("Quartier marchand", "au Quartier marchand"),
    ("Terrain d'entraînement 3", "au Terrain d'entraînement 3"),
    ("Forêt frontalière", "à la Forêt frontalière"),
    ("Poste frontière du Nord", "au Poste frontière du Nord"),
])
def test_les_lieux_de_l_amorce_se_disent_bien(nom, attendu):
    assert fr.a(nom) == attendu


@pytest.mark.parametrize("nom,attendu", [
    ("Académie", "de l'Académie"),
    ("Tour du Kage", "de la Tour du Kage"),
    ("Quartier marchand", "du Quartier marchand"),
    ("Poste frontière du Nord", "du Poste frontière du Nord"),
    ("Konoha", "de Konoha"),
])
def test_le_complement_se_contracte_aussi(nom, attendu):
    assert fr.de(nom) == attendu


# ==========================================================================
# LE DÉFAUT SÛR
# ==========================================================================
def test_un_nom_propre_ne_prend_pas_d_article():
    """« à la Konoha » serait une faute grossière ; « à la Tour » manquante
    n'est qu'une maladresse. On se trompe donc du bon côté."""
    for nom in ("Konoha", "Ichiraku", "Suna", "Kirigakure"):
        assert fr.a(nom) == f"à {nom}"
        assert fr.determine(nom) == nom


def test_un_nom_vide_ne_produit_rien():
    for f in (fr.a, fr.de, fr.dans, fr.vers, fr.depuis, fr.determine):
        assert f("") == ""
        assert f("   ") == ""


def test_le_pluriel_prend_aux_et_des():
    assert fr.a("Portes du Sud") == "aux Portes du Sud"
    assert fr.de("Ruines de l'Est") == "des Ruines de l'Est"


def test_marais_reste_masculin_singulier():
    """Une règle sur le « s » final y verrait un pluriel : « aux Marais »."""
    assert fr.a("Marais des brumes") == "au Marais des brumes"


def test_l_accent_ne_fait_pas_rater_la_reconnaissance():
    assert fr.a("Foret frontaliere") == "à la Foret frontaliere"
    assert fr.a("Marche aux poissons") == "au Marche aux poissons"


# ==========================================================================
# LES AUTRES FORMES
# ==========================================================================
def test_vers_et_depuis_gardent_l_article():
    assert fr.vers("Tour du Kage") == "vers la Tour du Kage"
    assert fr.depuis("Académie") == "depuis l'Académie"
    assert fr.vers("Konoha") == "vers Konoha"


def test_dans_veut_l_article_et_ne_se_contracte_pas():
    assert fr.dans("Forêt frontalière") == "dans la Forêt frontalière"
    assert fr.dans("Quartier marchand") == "dans le Quartier marchand"
    assert fr.dans("Konoha") == "à Konoha", \
        "on n'entre pas « dans Konoha », on y est"


# ==========================================================================
# LES ACCORDS QU'ON ÉCRIVAIT À LA MAIN
# ==========================================================================
def test_le_nombre_accorde_le_nom():
    assert fr.accorde(1, "tour") == "1 tour"
    assert fr.accorde(3, "tour") == "3 tours"
    assert fr.accorde(0, "tour") == "0 tour"
    assert fr.accorde(2, "cheval", "chevaux") == "2 chevaux"


def test_l_enumeration_se_termine_par_et():
    assert fr.enumerer(["a"]) == "a"
    assert fr.enumerer(["a", "b"]) == "a et b"
    assert fr.enumerer(["a", "b", "c"]) == "a, b et c"
    assert fr.enumerer(["a", "", "  ", "c"]) == "a et c"
    assert fr.enumerer([]) == ""


def test_la_majuscule_ne_detruit_pas_le_reste():
    """`str.capitalize()` transformait « la Tour du Kage » en « La tour du
    kage » : une correction silencieuse qui abîme un nom propre."""
    assert fr.majuscule("la Tour du Kage") == "La Tour du Kage"
    assert fr.majuscule("l'Académie") == "L'Académie"
    assert fr.majuscule("") == ""
