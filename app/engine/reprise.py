"""« Précédemment » — reprendre une campagne trois semaines plus tard.

LE PROBLÈME, ET IL EST ENTIER. Ce jeu se joue en local, à plusieurs, sur un
écran partagé, une soirée par semaine. Entre deux séances il passe sept jours.
Or en rouvrant la table on retrouvait : un mur de tours, une horloge à 34, et
rien qui dise ce qu'on était en train de faire. Le délai de mission qui courait,
la dette contractée au tour 22, le nom du type qu'on devait retrouver — tout
était en base, et personne ne s'en souvenait.

Le premier geste de la séance était donc de faire défiler l'historique en
disant « attends, on en était où ». Ce module est là pour que ce soit la
dernière fois.

DEUX ÉTAGES, ET LE SECOND EST FACULTATIF.

  LE RELEVÉ est déterministe et instantané : où l'on est, avec qui, ce que le
  dernier chapitre disait, les délais qui courent, les questions ouvertes, ce
  que le monde a fait sans nous, les points de niveau non placés. Il marche
  avec Ollama éteint, et c'est ce qui compte : on ne doit pas dépendre d'un
  modèle pour savoir de quoi on parle.

  LE RÉCIT est une seule génération courte, écrite par-dessus le relevé, et
  MISE EN CACHE dans `Summary` (niveau `reprise`). On la paie une fois par
  reprise, pas une fois par rechargement de page.

CE QUI N'Y ENTRE JAMAIS : `Secret.verite`, `DestinyTrait.verite`, les
`Knowledge` que le groupe ne possède pas. Le rappel de reprise est un texte lu
par le joueur — le filtre de divulgation y est encore plus strict qu'ailleurs.
"""
from __future__ import annotations

from datetime import timedelta

from sqlmodel import Session, select

from app.engine import francais as fr
from app.engine import secrets as horloge_secrets
from app.llm.prompts import REPRISE
from app.llm.provider import get_llm
from app.models import (Campaign, Character, Event, Location, Quest, Summary,
                        Turn, maintenant)
from app.rules.engine import Ruleset

# En dessous, on n'a rien oublié : on vient de faire une pause café.
HEURES_AVANT_RAPPEL = 10

# En dessous de trois tours, il n'y a pas encore d'histoire à rappeler.
TOURS_MINIMUM = 3

NIVEAU = "reprise"


# --------------------------------------------------------------------------
# Faut-il rappeler quoi que ce soit ?
# --------------------------------------------------------------------------
def ecart(camp: Campaign) -> timedelta:
    """Depuis combien de temps on a lâché. `joue_le` est écrit à chaque tour."""
    return maintenant() - (camp.joue_le or camp.cree_le)


def necessaire(session: Session, camp: Campaign) -> bool:
    if camp.phase != "en_cours" or camp.tour < TOURS_MINIMUM:
        return False
    return ecart(camp) >= timedelta(hours=HEURES_AVANT_RAPPEL)


def duree_lisible(camp: Campaign) -> str:
    """« trois jours », pas « 3 days, 4:12:07 »."""
    d = ecart(camp)
    jours = d.days
    if jours >= 14:
        return f"{jours // 7} semaines"
    if jours >= 7:
        return "une semaine" if jours < 10 else f"{jours} jours"
    if jours >= 1:
        return "un jour" if jours == 1 else f"{jours} jours"
    heures = int(d.total_seconds() // 3600)
    return "quelques heures" if heures < 2 else f"{heures} heures"


# --------------------------------------------------------------------------
# Le relevé — déterministe, instantané, sans modèle
# --------------------------------------------------------------------------
def releve(session: Session, camp: Campaign, pj: Character,
           rs: Ruleset) -> dict:
    """Tout ce dont on a besoin pour reprendre, pris en base et nulle part
    ailleurs. Aucune interprétation : c'est le matériau, pas le texte."""
    lieu = session.get(Location, pj.location_id) if pj.location_id else None

    derniers = session.exec(select(Turn).where(
        Turn.campaign_id == camp.id).order_by(Turn.index.desc()).limit(2)).all()
    chapitre = session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau != NIVEAU)
        .order_by(Summary.au_tour.desc())).first()

    # Les délais qui courent : l'information la plus sûrement oubliée.
    echeances = []
    for q in session.exec(select(Quest).where(
            Quest.campaign_id == camp.id)).all():
        if q.statut not in ("proposée", "acceptée", "en cours"):
            continue
        reste = (q.echeance_tour - camp.tour) if q.echeance_tour else None
        echeances.append({"titre": q.titre, "rang": q.rang, "statut": q.statut,
                          "reste": reste})
    echeances.sort(key=lambda e: (e["reste"] is None, e["reste"] or 0))

    compagnie = [c.nom for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.vivant == True,  # noqa: E712
        Character.location_id == pj.location_id)).all()
        if c.id != pj.id and c.role_campagne in
        ("sensei", "coequipier", "rival", "antagoniste")][:4]

    # Ce que le monde a fait pendant l'absence — déjà filtré à la création.
    bruits = session.exec(select(Event).where(
        Event.campaign_id == camp.id, Event.importance >= 3)
        .order_by(Event.tour.desc()).limit(5)).all()

    questions = [s.question.strip()
                 for s in horloge_secrets.entames(session, camp)]

    return {
        "tour": camp.tour,
        "depuis": duree_lisible(camp),
        "lieu": lieu.nom if lieu else "",
        "lieu_note": (lieu.description or "")[:180] if lieu else "",
        "compagnie": compagnie,
        "chapitre": (chapitre.texte or "").strip() if chapitre else "",
        "derniers": [{"index": t.index, "action": t.action,
                      "narration": (t.narration or "")[-420:]}
                     for t in reversed(derniers)],
        "echeances": echeances,
        "bruits": [f"(tour {e.tour}) {e.resume}" for e in reversed(bruits)],
        "questions": questions,
        "points_libres": pj.points_libres or 0,
        "etats": list(pj.etats or []),
    }


def _sans_modele(pj: Character, d: dict) -> str:
    """Le rappel quand il n'y a pas de modèle — ou qu'il n'a pas répondu.

    Volontairement sec et utile plutôt que joli. Un joueur qui reprend a besoin
    de savoir où il est et ce qui le presse ; le style est un bonus.
    """
    bouts = []
    if d["lieu"]:
        # « est à la Tour du Kage », pas « est à Tour du Kage ». Voir francais.
        bouts.append(f"{pj.nom} est {fr.a(d['lieu'])}")
    else:
        bouts.append(f"{pj.nom} reprend sa route")
    if d["compagnie"]:
        bouts.append("avec " + fr.enumerer(d["compagnie"]))
    phrase = " ".join(bouts) + f", au tour {d['tour']}."

    suite = []
    if d["etats"]:
        suite.append("Tu portes encore : " + fr.enumerer(d["etats"]) + ".")
    pressants = [e for e in d["echeances"]
                 if e["reste"] is not None and e["reste"] <= 3]
    if pressants:
        e = pressants[0]
        reste = max(0, e["reste"])
        suite.append(f"« {e['titre']} » arrive à échéance dans "
                     f"{fr.accorde(reste, 'tour')}."
                     if reste else f"« {e['titre']} » arrive à échéance.")
    elif d["echeances"]:
        suite.append("En cours : " + fr.enumerer(
            [f"« {e['titre']} »" for e in d["echeances"][:2]]) + ".")
    if d["questions"]:
        suite.append("Sans réponse : " + fr.enumerer(
            [f"« {q} »" for q in d["questions"][:2]]) + ".")
    if d["chapitre"]:
        suite.append(d["chapitre"])
    return " ".join([phrase] + suite).strip()


def _invite(pj: Character, d: dict) -> str:
    """Le relevé mis en forme pour le modèle. Dense, étiqueté, sans fioriture."""
    lignes = [f"### JOUEUR\n{pj.nom}, tour {d['tour']}, "
              f"absent depuis {d['depuis']}."]
    if d["lieu"]:
        # On donne au modèle le nom DÉTERMINÉ : c'est ce qui lui fait écrire
        # « la Tour du Kage » au lieu de recopier l'étiquette brute.
        lignes.append(f"### OÙ\n{fr.majuscule(fr.determine(d['lieu']))}. "
                      f"{d['lieu_note']}".strip())
    if d["compagnie"]:
        lignes.append("### AVEC\n" + fr.enumerer(d["compagnie"]))
    if d["etats"]:
        lignes.append("### ÉTAT\n" + ", ".join(d["etats"]))
    if d["chapitre"]:
        lignes.append("### CE QUE DIT LE DERNIER CHAPITRE\n" + d["chapitre"])
    for t in d["derniers"]:
        lignes.append(f"### TOUR {t['index']}\nIl a tenté : {t['action']}\n"
                      f"Ce qui s'est passé : {t['narration']}")
    if d["echeances"]:
        lignes.append("### CE QUI COURT\n" + "\n".join(
            f"- « {e['titre']} » ({e['rang']}, {e['statut']})"
            + (f" — {e['reste']} tour(s) restants" if e["reste"] is not None
               else "")
            for e in d["echeances"][:4]))
    if d["questions"]:
        lignes.append("### CE QUI RESTE SANS RÉPONSE\n" + "\n".join(
            f"- « {q} »" for q in d["questions"][:3])
            + "\nTu n'en connais pas la réponse et tu ne l'inventes pas.")
    if d["bruits"]:
        lignes.append("### CE QUI A BOUGÉ\n" + "\n".join(
            f"- {b}" for b in d["bruits"]))
    return "\n\n".join(lignes)


# --------------------------------------------------------------------------
# Le récit, et son cache
# --------------------------------------------------------------------------
def _en_cache(session: Session, camp: Campaign) -> Summary | None:
    """Le rappel déjà écrit pour CE tour. Au tour suivant il est périmé."""
    return session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau == NIVEAU,
        Summary.au_tour == camp.tour)).first()


def rappeler(session: Session, camp: Campaign, pj: Character, rs: Ruleset,
             *, ecrire: bool = True) -> dict:
    """Le rappel de reprise, prêt à afficher.

    `ecrire=False` rend le relevé et le texte de secours sans toucher au
    modèle : c'est ce que demande une page qui doit répondre tout de suite.

    NE LÈVE JAMAIS. Un rappel raté ne doit pas empêcher d'ouvrir sa table :
    on retombe sur le texte déterministe, qui dit déjà l'essentiel.
    """
    d = releve(session, camp, pj, rs)
    secours = _sans_modele(pj, d)

    cache = _en_cache(session, camp)
    if cache is not None and (cache.texte or "").strip():
        return {"texte": cache.texte.strip(), "releve": d, "du_modele": True}
    if not ecrire:
        return {"texte": secours, "releve": d, "du_modele": False}

    texte = ""
    try:
        texte = (get_llm().text(REPRISE, _invite(pj, d), rapide=True) or "").strip()
    except Exception:  # noqa: BLE001 — jamais au prix de la page
        texte = ""
    if not texte:
        return {"texte": secours, "releve": d, "du_modele": False}

    session.add(Summary(campaign_id=camp.id, niveau=NIVEAU,
                        du_tour=max(0, camp.tour - 3), au_tour=camp.tour,
                        texte=texte))
    session.commit()
    return {"texte": texte, "releve": d, "du_modele": True}
