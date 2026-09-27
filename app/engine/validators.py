"""Garde-fou entre le modèle et la base.

Aucune sortie de modèle n'atteint la base sans passer ici. On n'écrit que ce
qui référence des entités RÉELLES et reste dans des bornes RAISONNABLES.
C'est ce qui empêche la dérive : le modèle peut halluciner, il ne peut pas
corrompre la partie.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import Campaign, Character, Quest

DELTA_RELATION_MAX = 25
XP_PAR_TOUR_MAX = 200
DELTA_RESSOURCE_MAX = 50


def resoudre_personnage(session: Session, camp: Campaign, nom: str) -> Character | None:
    """Résolution tolérante : le modèle écrira « Hiroshi » pour « Hiroshi Tanaka »."""
    if not nom:
        return None
    nom = nom.strip()
    persos = session.exec(select(Character).where(
        Character.campaign_id == camp.id)).all()
    for c in persos:
        if c.nom.lower() == nom.lower():
            return c
    for c in persos:
        prenom = c.nom.lower().split()[0] if c.nom else ""
        if nom.lower() in c.nom.lower() or prenom == nom.lower():
            return c
    return None


def valider_consequences(session: Session, camp: Campaign, brut: dict) -> tuple[dict, list[str]]:
    """-> (conséquences nettoyées, rejets). Les rejets sont conservés dans le
    tour : ils disent quel prompt corriger."""
    rejets: list[str] = []
    net: dict = {"faits": [], "relations": [], "quetes": [], "ressources": [],
                 "xp": 0, "graine_intrigue": (brut.get("graine_intrigue") or "").strip(),
                 "rencontres": [], "propositions": [],
                 "mystere_nouveau": "", "mysteres_resolus": []}

    question = (brut.get("mystere_nouveau") or "").strip()
    if len(question) >= 8:
        net["mystere_nouveau"] = question[:200] if question.endswith("?") \
            else question[:198].rstrip(" .") + " ?"
    for r in (brut.get("mysteres_resolus") or [])[:2]:
        if isinstance(r, dict) and isinstance(r.get("numero"), int) \
                and len((r.get("reponse") or "").strip()) >= 6:
            net["mysteres_resolus"].append(
                {"numero": r["numero"], "reponse": r["reponse"].strip()[:300]})

    # Les pistes offertes au joueur. Bornées comme le reste : un modèle local
    # qui part en roue libre produit parfois douze propositions de trois
    # lignes, et la zone d'action devient illisible.
    for p in (brut.get("propositions") or [])[:4]:
        texte = (p.get("texte") or "").strip()
        if len(texte) < 4:
            continue
        net["propositions"].append({
            "texte": texte[:160],
            "risque": p.get("risque") if p.get("risque") in
                      ("faible", "moyen", "eleve") else "moyen"})

    for f in brut.get("faits") or []:
        texte = (f.get("texte") or "").strip()
        if len(texte) < 10:
            rejets.append(f"fait trop court : {texte!r}")
            continue
        net["faits"].append({
            "texte": texte,
            "importance": max(1, min(5, int(f.get("importance", 3) or 3))),
        })

    for r in brut.get("relations") or []:
        cible = resoudre_personnage(session, camp, r.get("personnage", ""))
        if cible is None:
            rejets.append(f"personnage inconnu : {r.get('personnage')!r}")
            continue
        delta = int(r.get("delta", 0) or 0)
        if abs(delta) > DELTA_RELATION_MAX:
            rejets.append(f"delta relation écrêté ({delta}) pour {cible.nom}")
            delta = max(-DELTA_RELATION_MAX, min(DELTA_RELATION_MAX, delta))
        if delta == 0:
            continue
        net["relations"].append({"character_id": cible.id, "delta": delta,
                                 "raison": (r.get("raison") or "")[:200]})

    quetes = session.exec(select(Quest).where(Quest.campaign_id == camp.id)).all()
    for q in brut.get("quetes") or []:
        titre = (q.get("titre") or "").strip().lower()
        match = next((x for x in quetes if x.titre.strip().lower() == titre), None)
        if match is None:
            rejets.append(f"quête inconnue : {q.get('titre')!r}")
            continue
        net["quetes"].append({"quest_id": match.id, "statut": q.get("statut")})

    for r in brut.get("ressources") or []:
        nom = (r.get("nom") or "").strip().lower()
        if not nom:
            continue
        delta = max(-DELTA_RESSOURCE_MAX,
                    min(DELTA_RESSOURCE_MAX, int(r.get("delta", 0) or 0)))
        net["ressources"].append({"nom": nom, "delta": delta})

    net["xp"] = max(0, min(XP_PAR_TOUR_MAX, int(brut.get("xp") or 0)))

    # Rencontres : une seule par tour, et jamais quelqu'un qui existe déjà —
    # sinon la cristallisation créerait un doublon de personnage connu.
    for r in (brut.get("rencontres") or [])[:2]:
        role = (r.get("role") or "").strip()
        if len(role) < 3:
            rejets.append(f"rencontre sans rôle exploitable : {role!r}")
            continue
        nom = (r.get("nom") or "").strip()
        if nom and resoudre_personnage(session, camp, nom) is not None:
            rejets.append(f"rencontre ignorée, {nom!r} existe déjà")
            continue
        importance = r.get("importance")
        net["rencontres"].append({
            "role": role[:60], "nom": nom[:60],
            "importance": importance if importance in ("figurant", "notable")
                          else "figurant",
            "scene": (r.get("scene") or "").strip()[:200]})
        break

    return net, rejets


def valider_cristallisation(germe: dict, brut: dict) -> tuple[dict, list[str]]:
    """Le germe reste canon : la génération ne peut rien y contredire."""
    rejets: list[str] = []
    net = {
        "nom": (brut.get("nom") or germe.get("nom") or "Inconnu").strip()[:60],
        "personnalite": (brut.get("personnalite") or "").strip()[:400],
        "parler": (brut.get("parler") or "").strip()[:300],
        "apparence": (brut.get("apparence") or "").strip()[:300],
        "objectif": (brut.get("objectif") or "").strip()[:200],
        "secret": (brut.get("secret") or "").strip()[:300],
    }
    if germe.get("nom") and net["nom"].lower() != germe["nom"].lower():
        rejets.append(f"nom du germe imposé ({germe['nom']})")
        net["nom"] = germe["nom"]
    if len(net["personnalite"]) < 10:
        rejets.append("personnalité trop pauvre")
        net["personnalite"] = "réservé, difficile à cerner"
    if not net["parler"]:
        net["parler"] = "phrases courtes, ton neutre"
    return net, rejets
