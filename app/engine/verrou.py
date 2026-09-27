"""Un tour à la fois par campagne.

LE PROBLÈME. Deux requêtes qui jouent en même temps sur la même partie lisent
toutes les deux `camp.tour`, l'incrémentent toutes les deux, et écrivent la
même valeur : deux tours joués, l'horloge avancée d'un seul. Pire, elles se
partagent une rencontre en cours et s'écrasent mutuellement les points de vie.

Ça n'arrivait pas tant qu'un seul navigateur jouait — le flux SSE désactive
déjà le bouton pendant qu'une scène s'écrit. Mais le hot-seat se joue à
plusieurs autour d'un écran, et rien n'empêche d'ouvrir la table sur deux
onglets, ni de jouer à trois sur le réseau local.

LA PORTÉE, ET SES LIMITES. Un verrou en mémoire de processus suffit ici :
le jeu tourne sur un seul uvicorn, pour une table de trois personnes au plus.
Le jour où l'on voudra plusieurs workers, il faudra un verrou en base — une
ligne posée et relâchée dans une transaction. La frontière est ce module, et
c'est pour ça qu'il existe plutôt que trois `Lock` éparpillés.

ON N'ATTEND PAS INDÉFINIMENT. Un tour peut durer trente secondes ; empiler les
requêtes derrière lui rendrait l'interface incompréhensible. Au-delà du délai,
on refuse poliment, et l'interface le dit.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

# Un verrou par campagne, créés à la demande. Le dictionnaire lui-même est
# protégé : deux requêtes simultanées sur une campagne jamais vue créeraient
# sinon deux verrous différents, ce qui n'en fait aucun.
_VERROUS: dict[int, threading.Lock] = {}
_GARDE = threading.Lock()

DELAI = 0.5          # secondes d'attente avant de renoncer


class CampagneOccupee(RuntimeError):
    """Un tour est déjà en train de se jouer sur cette campagne."""


def verrou_de(cid: int) -> threading.Lock:
    with _GARDE:
        if cid not in _VERROUS:
            _VERROUS[cid] = threading.Lock()
        return _VERROUS[cid]


@contextmanager
def tour_exclusif(cid: int, delai: float = DELAI):
    """Garantit qu'un seul tour s'écrit à la fois sur cette campagne.

    Lève `CampagneOccupee` plutôt que d'attendre : l'appelant sait quoi
    répondre au joueur, ce module non.
    """
    verrou = verrou_de(cid)
    if not verrou.acquire(timeout=delai):
        raise CampagneOccupee(
            "Un tour est déjà en train de se jouer sur cette campagne. "
            "Attends qu'il se termine avant d'agir.")
    try:
        yield
    finally:
        verrou.release()


def occupee(cid: int) -> bool:
    """Utile aux tests et au diagnostic ; jamais pour décider d'agir — entre
    la question et l'action, l'état peut changer."""
    return verrou_de(cid).locked()
