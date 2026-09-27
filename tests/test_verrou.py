"""Un tour à la fois par campagne.

Deux requêtes simultanées lisaient toutes les deux `camp.tour`, l'incrémentaient
toutes les deux et écrivaient la même valeur : deux tours joués, l'horloge
avancée d'un seul. Le hot-seat se joue à plusieurs autour d'un écran, et rien
n'empêche d'ouvrir la table sur deux onglets.
"""
import threading
import time

import pytest

from app.engine.verrou import CampagneOccupee, occupee, tour_exclusif


def test_un_seul_tour_a_la_fois():
    entre = threading.Event()
    relache = threading.Event()
    refus = []

    def premier():
        with tour_exclusif(1):
            entre.set()
            relache.wait(timeout=2)

    fil = threading.Thread(target=premier)
    fil.start()
    entre.wait(timeout=2)
    try:
        with pytest.raises(CampagneOccupee):
            with tour_exclusif(1, delai=0.05):
                refus.append("passé")
    finally:
        relache.set()
        fil.join(timeout=2)
    assert refus == [], "le second tour est passé malgré le verrou"


def test_deux_campagnes_ne_se_bloquent_pas():
    """Le verrou est PAR PARTIE. Une table qui joue ne doit pas figer les
    autres — c'est tout l'intérêt de ne pas avoir mis un verrou global."""
    with tour_exclusif(10):
        with tour_exclusif(11, delai=0.05):
            assert occupee(10) and occupee(11)
    assert not occupee(10) and not occupee(11)


def test_le_verrou_est_rendu_meme_si_le_tour_echoue():
    with pytest.raises(ValueError):
        with tour_exclusif(20):
            raise ValueError("le modèle n'a pas répondu")
    assert not occupee(20), "un tour raté gardait la campagne bloquée"
    with tour_exclusif(20, delai=0.05):
        pass


def test_le_refus_est_rapide():
    """On refuse, on n'empile pas : au-delà du délai l'interface doit pouvoir
    répondre quelque chose au joueur."""
    with tour_exclusif(30):
        depart = time.monotonic()
        with pytest.raises(CampagneOccupee):
            with tour_exclusif(30, delai=0.1):
                pass
        assert time.monotonic() - depart < 1.0
