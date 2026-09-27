"""Chargement des rulesets YAML."""
import copy
import functools
from pathlib import Path

import yaml

from app.config import ROOT
from app.rules.engine import Ruleset

RULESETS = ROOT / "rulesets"


def lister() -> list[dict]:
    out = []
    for p in sorted(RULESETS.glob("*.yaml")):
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        out.append({"slug": p.stem, "nom": d.get("name", p.stem), "data": d})
    return out


@functools.lru_cache(maxsize=8)
def _lu(slug: str) -> dict:
    p = RULESETS / f"{slug}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"Ruleset introuvable : {slug}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def lire(slug: str) -> dict:
    """Le fichier, analysé une fois et rendu en copie profonde.

    En copie parce que les appelants le modifient : `Ruleset` normalise les
    clés entières, et une campagne en garde un exemplaire figé. Rendre le
    dictionnaire du cache les ferait tous se marcher dessus.
    """
    return copy.deepcopy(_lu(slug))


def charger(slug: str) -> Ruleset:
    return Ruleset(data=lire(slug))


def completer(copie: dict, defauts: dict) -> dict:
    """Ajoute à `copie` les clés ABSENTES de `defauts`, sans rien écraser.

    À quoi ça sert. Une campagne conserve une copie de son ruleset : modifier
    le YAML ne doit pas rééquilibrer une partie en cours, et c'est une règle à
    laquelle on ne touche pas.

    Mais une partie commencée avant l'arrivée d'un SOUS-SYSTÈME n'en possède
    aucune trace — une campagne d'avant le moteur de combat n'a ni postures, ni
    seuils de blessure, ni table de récupération. Ses joueurs ne pourraient
    simplement pas se battre. Ce n'est pas un rééquilibrage, c'est une absence.

    D'où la distinction : on complète ce qui MANQUE, on ne remplace jamais ce
    qui existe. Une valeur déjà présente dans la copie reste celle de la
    campagne, pour toujours.
    """
    for cle, valeur in defauts.items():
        if cle not in copie:
            copie[cle] = valeur
        elif isinstance(valeur, dict) and isinstance(copie.get(cle), dict):
            completer(copie[cle], valeur)
    return copie


def charger_pour(copie: dict, slug: str) -> Ruleset:
    """Les règles d'une campagne : sa copie, complétée de ce qui n'existait pas
    encore quand elle a été créée."""
    if not copie:
        return charger(slug)
    try:
        # Copie PROFONDE : `completer` descend dans les sous-tables, et une
        # copie de surface les partagerait avec la ligne de la campagne — on
        # modifierait alors sa copie figée en croyant travailler sur la nôtre.
        return Ruleset(data=completer(copy.deepcopy(copie), lire(slug)))
    except FileNotFoundError:
        # Le ruleset a disparu du disque : la copie de la campagne suffit à
        # continuer à jouer, et c'est précisément pour ça qu'elle existe.
        return Ruleset(data=copie)
