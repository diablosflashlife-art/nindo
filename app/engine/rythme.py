"""Le rythme de la narration.

LE DÉFAUT QU'ON CORRIGE. Chaque tour recevait exactement la même consigne :
« 150 à 280 mots, présent, deuxième personne, termine sur une situation
ouverte ». Au bout de vingt tours, tout se ressemble. Un échange de coups
s'étire comme une promenade, une conversation se coupe au moment où elle
devient intéressante, et la révélation d'une destinée arrive avec le même
souffle qu'un achat de rations.

Un maître du jeu humain ne fait jamais ça. Il accélère quand ça frappe, il
prend son temps quand on découvre, il laisse un silence après une nouvelle
grave, et il expédie ce qui n'est pas le sujet.

CE QUE FAIT CE MODULE. Il lit ce qui vient de se passer — le type d'action, le
résultat du dé, l'état du combat, les révélations du tour — et en déduit un
REGISTRE : une longueur, une allure, une façon de finir. Le narrateur reçoit
cette consigne en plus du reste.

CE QU'IL NE FAIT PAS. Il ne décide de rien de mécanique et ne touche à aucune
donnée. C'est une consigne de style, et rien d'autre. Si elle était retirée, le
jeu tournerait pareil — il serait seulement plus monotone.
"""
from __future__ import annotations

# Les registres, du plus tendu au plus posé. `mots` est une fourchette donnée
# au narrateur ; `allure` décrit la phrase ; `fin` dit comment sortir.
REGISTRES: dict[str, dict] = {
    "echange": {
        "nom": "ÉCHANGE",
        "mots": (110, 190),
        "allure": "Phrases courtes. Verbes d'action. Peu d'adjectifs, aucun "
                  "adverbe. On voit les corps bouger, pas ce qu'ils pensent.",
        "fin": "Coupe net, au milieu du mouvement. Pas de bilan, pas de "
               "respiration : le prochain échange part de là.",
    },
    "bascule": {
        "nom": "BASCULE",
        "mots": (130, 210),
        "allure": "Ralentis. Une seule chose arrive, et tout le paragraphe la "
                  "regarde. Un détail physique porte la nouvelle mieux qu'une "
                  "explication.",
        "fin": "Termine sur un silence, ou sur un geste suspendu. Ne commente "
               "pas ce qui vient d'arriver : laisse-le peser.",
    },
    "conversation": {
        "nom": "CONVERSATION",
        "mots": (170, 270),
        "allure": "Les répliques portent la scène. Deux ou trois échanges, pas "
                  "davantage. Entre eux, ce que les corps disent et que les "
                  "mots contredisent.",
        "fin": "Termine sur une réplique, ou sur ce qu'un personnage choisit "
               "de ne pas dire.",
    },
    "decouverte": {
        "nom": "DÉCOUVERTE",
        "mots": (190, 300),
        "allure": "Prends le temps du lieu. Trois sens au moins, dont un qui "
                  "n'est pas la vue. Ce que l'endroit dit de ceux qui y "
                  "vivent compte plus que sa géographie.",
        "fin": "Termine sur quelque chose qu'on remarque et qu'on ne comprend "
               "pas encore.",
    },
    "revers": {
        "nom": "REVERS",
        "mots": (130, 220),
        "allure": "Sec. L'échec a un coût immédiat et visible, et personne ne "
                  "vient le consoler. Ne cherche pas à sauver le personnage.",
        "fin": "Termine sur la conséquence, pas sur l'émotion.",
    },
    "respiration": {
        "nom": "RESPIRATION",
        "mots": (110, 190),
        "allure": "Rien d'urgent n'arrive. C'est le moment où l'on remarque "
                  "les gens autour, la fatigue, une habitude. Le ton se "
                  "détend sans devenir gentil.",
        "fin": "Termine sur un détail ordinaire, ou sur une phrase que "
               "quelqu'un lâche sans y penser.",
    },
    "ordinaire": {
        "nom": "SCÈNE",
        "mots": (150, 260),
        "allure": "Conséquences concrètes et observables. Ni précipitation, ni "
                  "complaisance.",
        "fin": "Termine sur une situation ouverte, sans poser de question.",
    },
}

# Au-delà de ce nombre de tours dans le même registre, on force l'alternance.
# Deux découvertes de suite passent ; six d'affilée endorment.
LASSITUDE = 3


def choisir(intent: dict, resolution: dict, en_combat: bool,
            revelation_recente: bool = False,
            recents: list[str] | None = None) -> str:
    """Le registre de CE tour.

    L'ordre des tests est l'ordre des priorités : ce qui bascule une campagne
    prime sur ce qui la fait avancer, qui prime sur le train-train.
    """
    # Une destinée qui s'est ouverte au tour précédent : la scène qui suit
    # doit respirer autour, au lieu d'enchaîner comme si de rien n'était.
    if revelation_recente:
        return "bascule"

    if en_combat:
        # La fin d'un affrontement n'est pas un échange de plus. C'est le
        # moment où l'on compte ce qu'il reste, et ça se raconte autrement.
        statut = (resolution.get("combat") or {}).get("statut", "en_cours")
        return "echange" if statut == "en_cours" else "bascule"

    check = resolution.get("check") or {}
    if check.get("issue") == "echec_critique":
        return "revers"

    action = intent.get("action_type", "")
    registre = {
        "dialogue": "conversation",
        "social": "conversation",
        "exploration": "decouverte",
        "deplacement": "decouverte",
        "repos": "respiration",
        "combat": "echange",
    }.get(action, "ordinaire")

    if not check.get("reussi", True) and registre == "ordinaire":
        registre = "revers"

    # Anti-monotonie : si les derniers tours ont tous le même registre et que
    # rien d'urgent ne l'impose, on change de souffle. C'est ce qu'un maître
    # du jeu humain fait sans y penser.
    suite = recents or []
    if (len(suite) >= LASSITUDE
            and all(r == registre for r in suite[-LASSITUDE:])
            and registre in ("ordinaire", "decouverte", "conversation")):
        registre = "respiration" if registre != "respiration" else "ordinaire"
    return registre


# Ce qu'on dit au narrateur quand plus rien ne presse. Sans cette consigne, un
# creux entre deux missions se racontait comme un couloir d'attente : le
# joueur tourne en rond dans un village où il ne se passe rien. Or ce sont les
# scènes d'entre-deux — un repas, un entraînement, une dispute qui traîne —
# dont on se souvient à la fin d'une campagne.
ENTRE_DEUX = (
    "### AUCUNE URGENCE NE PRESSE\n"
    "Pas de mission en cours, pas d'affrontement. C'est un creux, et un creux "
    "n'est pas un vide : c'est le moment où la vie de groupe existe.\n"
    "Fais arriver une scène ordinaire et située — un repas, un entraînement, "
    "une réparation de matériel, une file d'attente, une corvée. Quelqu'un y "
    "dit quelque chose qu'il ne dirait pas en mission.\n"
    "N'invente aucun enjeu pour meubler : le calme est le sujet."
)


# L'ALLURE DE LA TABLE. Mesuré en partie réelle : un modèle en ligne écrit
# environ 320 mots par tour, soit près de deux minutes de lecture à voix
# haute. À plusieurs devant un stream, c'est trop. La table choisit : court
# (le défaut), normal, ou long pour qui joue seul et aime lire.
ALLURES = {"court": 0.62, "normal": 1.0, "long": 1.3}
ALLURE_DEFAUT = "court"


def fourchette(registre: str, allure: str = ALLURE_DEFAUT) -> tuple[int, int]:
    r = REGISTRES.get(registre) or REGISTRES["ordinaire"]
    f = ALLURES.get(allure, ALLURES[ALLURE_DEFAUT])
    bas, haut = r["mots"]
    return int(round(bas * f / 10) * 10), int(round(haut * f / 10) * 10)


def consigne(registre: str, entre_deux: bool = False,
             allure: str = ALLURE_DEFAUT) -> str:
    """La consigne de style, telle que le narrateur la reçoit."""
    r = REGISTRES.get(registre) or REGISTRES["ordinaire"]
    bas, haut = fourchette(registre, allure)
    texte = (f"### REGISTRE DE CETTE SCÈNE — {r['nom']}\n"
             f"Longueur : {bas} à {haut} mots. Cette fourchette remplace celle "
             f"de tes règles de style.\n"
             f"Allure : {r['allure']}\n"
             f"Sortie : {r['fin']}")
    return f"{texte}\n\n{ENTRE_DEUX}" if entre_deux else texte


def derniers(tours) -> list[str]:
    """Les registres des tours récents, du plus ancien au plus récent."""
    return [t.resolution.get("registre", "") for t in tours
            if t.resolution.get("registre")]
