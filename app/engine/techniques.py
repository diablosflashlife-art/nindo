"""Ce qu'une technique FAIT en combat — chantier B de Nindō 2.0.

Jusqu'ici une technique n'était qu'un bonus au jet et un coût en chakra :
Kanashibari et Gôkakyû se jouaient pareil. Ici chaque technique reçoit un
EFFET MÉCANIQUE lisible, appliqué par le moteur de combat et affiché au
joueur avant qu'il choisisse :

    attaque    des dégâts en plus, parfois sur tout le camp adverse (zone)
    controle   la cible perd son prochain tour (genjutsu, ombre, paralysie)
    garde      une défense renforcée ce round, parfois une esquive parfaite
    clones     des doublures qui encaissent les prochains coups
    soin       des points de vie rendus, à soi ou à un allié
    mobilite   rompre le contact devient facile, et l'on se couvre
    affaiblir  des dégâts légers, et la cible frappe moins bien un moment

Le lore peut écrire `effets:` à la main ; sinon l'effet se DÉDUIT de la
catégorie et du rang, pour qu'aucune technique n'arrive sans effet. C'est ce
qui permet d'en ajouter une au lore sans toucher au moteur.
"""
from __future__ import annotations

DEGATS_PAR_RANG = {"E": 1, "D": 2, "C": 4, "B": 6, "A": 9, "S": 14}
SOIN_PAR_RANG = {"E": 4, "D": 8, "C": 12, "B": 18, "A": 26, "S": 40}
GARDE_PAR_RANG = {"E": 3, "D": 4, "C": 5, "B": 7, "A": 9, "S": 12}

LIBELLES = {
    "attaque": "Attaque", "controle": "Contrôle", "garde": "Garde",
    "clones": "Clones", "soin": "Soin", "mobilite": "Mobilité",
    "affaiblir": "Affaiblissement",
}


def effets(t: dict) -> dict:
    """L'effet mécanique de cette technique, explicite ou déduit."""
    if t.get("effets"):
        e = dict(t["effets"])
        e.setdefault("type", "attaque")
        return e
    rang = str(t.get("rang", "E"))
    cat = t.get("categorie", "")
    if cat == "genjutsu":
        return {"type": "controle", "tours": 1,
                "etat": "sous genjutsu", "stat_defense": "genjutsu"}
    if cat == "iryo":
        return {"type": "soin", "pv": SOIN_PAR_RANG.get(rang, 8)}
    if cat == "fuinjutsu":
        return {"type": "garde", "garde": GARDE_PAR_RANG.get(rang, 4)}
    if cat == "espionnage":
        return {"type": "garde", "garde": 2}
    if cat in ("taijutsu", "kenjutsu", "ninjutsu", "kugutsu", "arme") or t.get("degats"):
        return {"type": "attaque", "degats": DEGATS_PAR_RANG.get(rang, 1)}
    return {"type": "attaque", "degats": DEGATS_PAR_RANG.get(rang, 1)}


def resume(t: dict) -> str:
    """Une ligne pour le joueur : « Attaque · +4 dégâts · zone »."""
    e = effets(t)
    morceaux = [LIBELLES.get(e["type"], e["type"])]
    if e["type"] == "attaque":
        if e.get("degats"):
            morceaux.append(f"+{e['degats']} dégâts")
        if e.get("zone"):
            morceaux.append("tout le camp adverse")
    elif e["type"] == "controle":
        morceaux.append(f"la cible perd {e.get('tours', 1)} tour"
                        + ("s" if e.get("tours", 1) > 1 else ""))
    elif e["type"] == "garde":
        morceaux.append(f"défense +{e.get('garde', 4)} ce round")
        if e.get("esquive"):
            morceaux.append("annule le prochain coup")
    elif e["type"] == "clones":
        morceaux.append(f"{e.get('clones', 2)} doublures encaissent")
    elif e["type"] == "soin":
        morceaux.append(f"rend {e.get('pv', 8)} PV")
    elif e["type"] == "mobilite":
        morceaux.append("rompre le contact devient facile")
    elif e["type"] == "affaiblir":
        morceaux.append(f"+{e.get('degats', 1)} dégâts, la cible frappe à "
                        f"{e.get('jet', -2)} pendant {e.get('tours', 2)} tours")
    return " · ".join(morceaux)
