"""Ce qui a été trouvé en partie réelle, et ne doit plus revenir."""
import random

from app.engine.garde import decanoniser, nettoyer_nom, nom_canon, nom_propre, pudeur
from app.llm.provider import achever, epurer


def test_un_nom_de_la_serie_est_repere_et_remplace():
    assert nom_canon("Kakuzu Hoshino")
    assert nom_canon("Kakashi")
    assert not nom_canon("Kiyomi Tachibana")
    nouveau = decanoniser("Kakuzu Hoshino", random.Random(1), ["Goro", "Tetsu"])
    assert nouveau.endswith("Hoshino") and not nom_canon(nouveau)


def test_un_nom_de_clan_reste_permis():
    assert not nom_canon("Mizuki Hozuki".replace("Mizuki", "Ren"))
    assert not nom_canon("Aoi Uchiha")


def test_une_enfant_n_est_jamais_decrite_sensuelle():
    texte = ("Sensuelle et impulsive, mais aussi calme et réfléchie. Elle s'exprime "
             "souvent en rires, mais ses actes sont précis. Elle a un côté masochiste "
             "qu'elle ne reconnaît pas.")
    propre = pudeur(texte, 13)
    assert "ensuel" not in propre and "masochiste" not in propre
    assert "précis" in propre


def test_un_adulte_garde_sa_description():
    assert pudeur("Séducteur et menteur.", 34) == "Séducteur et menteur."


def test_un_recit_coupe_revient_a_sa_derniere_phrase():
    coupe = ("Kiyomi s'appuie contre la rambarde. « Tu sens ça ? » Un rire lui échappe. "
             "Plus loin, près d'un")
    assert achever(coupe) == ("Kiyomi s'appuie contre la rambarde. « Tu sens ça ? » "
                              "Un rire lui échappe.")


def test_un_recit_complet_n_est_pas_touche():
    assert achever("La Brume ne pardonne pas. « Personne. »") == \
        "La Brume ne pardonne pas. « Personne. »"


def test_la_mise_en_forme_disparait():
    assert epurer("*« Tu sens ça ? »* Elle rit. **Avant**.") == "« Tu sens ça ? » Elle rit. Avant."


def test_l_etiquette_ne_fait_pas_partie_du_nom():
    assert nettoyer_nom("Famille : Shiraishi") == "Shiraishi"
    assert nettoyer_nom("Nom: « Ren Himuro »") == "Ren Himuro"
    assert nettoyer_nom("Kiyomi Tachibana") == "Kiyomi Tachibana"


def test_le_juge_doit_citer_la_scene():
    from app.engine.reveal import cite_la_scene
    recit = "Tu concentres le chakra du vent dans tes paumes jusqu'à ce que la roche se fende."
    assert cite_la_scene("Elle concentre le chakra du vent jusqu'à fendre la roche.", recit)
    assert not cite_la_scene("Le personnage a montré un grand potentiel élémentaire.", recit)


def test_une_etiquette_n_est_pas_un_nom():
    assert not nom_propre("Inconnu")
    assert not nom_propre("présence furtive")
    assert not nom_propre("Silhouette encapuchonnée")
    assert nom_propre("Yoshio")
    assert nom_propre("Take au masque de bois")
