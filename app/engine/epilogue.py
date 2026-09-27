"""Le mot de la fin — une campagne doit pouvoir s'ACHEVER.

CE QUI MANQUAIT. `Campaign.phase` connaît « terminee » depuis le premier jour
et rien ne la déclenchait. Une campagne ne s'achevait donc jamais : elle
s'arrêtait. Un soir, on ne rouvrait plus l'onglet, et quarante tours d'histoire
restaient dans une base sans que rien ne dise ce qu'ils avaient valu.

Or la fin n'est pas une formalité administrative. C'est le moment où tout ce
qui a été semé se relit d'un coup : ce que le joueur a décidé, ce qu'il a
perdu, qui est resté, et ce qu'il n'a jamais su. C'est aussi la seule chose
qu'on montre à quelqu'un qui n'a pas joué.

COMMENT ON FERME.

  JAMAIS TOUT SEUL. Aucune condition ne clôt une campagne dans le dos du
  joueur. Le jeu dit qu'on PEUT conclure ; c'est la table qui décide.

  PAS AVANT QU'IL Y AIT QUELQUE CHOSE À DIRE. Un épilogue au tour trois est un
  texte sur rien. Il faut de la durée, ou une destinée qui s'est ouverte.

  ET ÇA SE ROUVRE. `rouvrir()` existe et n'est pas une concession : on se
  trompe, on veut jouer une scène de plus. Rien n'est effacé, l'épilogue reste
  dans la chronique, et la campagne reprend là où elle était.

LE FILTRE DE DIVULGATION TIENT JUSQU'AU BOUT. Ni `Secret.verite`, ni
`DestinyTrait.verite` n'entrent dans le relevé. Une question restée ouverte
reste ouverte — l'épilogue peut dire qu'elle pèse, jamais ce qu'elle cachait.
C'est le dernier endroit où l'on serait tenté de tout dire, et c'est
précisément celui où il ne faut pas.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.engine import francais as fr
from app.engine import secrets as horloge_secrets
from app.llm.prompts import EPILOGUE
from app.llm.provider import get_llm
from app.models import (Campaign, Character, Destiny, DestinyTrait, Encounter,
                        Event, Location, Quest, Relation, Secret, Summary,
                        Turn, maintenant)
from app.rules.engine import Ruleset

NIVEAU = "epilogue"

# En dessous, il n'y a pas encore d'histoire à conclure.
TOURS_MINIMUM = 25


class FinRefusee(RuntimeError):
    """On ne peut pas clore, et le joueur doit savoir pourquoi."""


# --------------------------------------------------------------------------
# Peut-on conclure ?
# --------------------------------------------------------------------------
RARETES_ACCOMPLIES = ("rare", "tres_rare", "legendaire")


def _eveils(session: Session, pj: Character) -> list[DestinyTrait]:
    """Ce qui s'est RÉELLEMENT ouvert en jeu.

    `actif_au_depart` est exclu : un trait donné à la création naît déjà
    « eveille » et n'a rien accompli. Sans ce filtre, un genin du premier tour
    qui avait une affinité de feu pouvait clore sa chronique en annonçant que
    sa destinée s'était accomplie. Même règle que `reveal._candidats`.
    """
    destin = session.exec(select(Destiny).where(
        Destiny.character_id == pj.id)).first()
    if destin is None:
        return []
    # Et seulement ce qui compte : une « seconde affinité » éveillée est un
    # palier, pas une destinée accomplie. Sans ce filtre, la partie réelle a
    # pu être close au troisième tour.
    return [t for t in session.exec(select(DestinyTrait).where(
        DestinyTrait.destiny_id == destin.id)).all()
        if t.etat == "eveille" and not t.actif_au_depart
        and t.rarete in RARETES_ACCOMPLIES]


def possible(session: Session, camp: Campaign, pj: Character) -> tuple[bool, str]:
    """Dit si la campagne peut être close, et ce qui manque sinon.

    Deux portes, et une seule suffit : la DURÉE — on a joué assez longtemps
    pour qu'il y ait quelque chose à raconter — ou l'ACCOMPLISSEMENT, une
    destinée arrivée à son terme.
    """
    if camp.phase == "terminee":
        return False, "Cette chronique est déjà close."
    if camp.phase != "en_cours":
        return False, "La campagne n'a pas encore commencé."
    if _eveils(session, pj):
        return True, "Ta destinée s'est accomplie."
    if camp.tour >= TOURS_MINIMUM:
        return True, f"{fr.accorde(camp.tour, 'tour')} joués."
    reste = TOURS_MINIMUM - camp.tour
    return False, (f"Encore {fr.accorde(reste, 'tour')} — ou une destinée "
                   f"menée à son terme. Un épilogue a besoin de matière.")


def deja_clos(session: Session, camp: Campaign) -> Summary | None:
    return session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau == NIVEAU)
        .order_by(Summary.au_tour.desc())).first()


# --------------------------------------------------------------------------
# Le relevé de toute une campagne
# --------------------------------------------------------------------------
def releve(session: Session, camp: Campaign, pj: Character,
           rs: Ruleset) -> dict:
    """Ce qui a eu lieu, pris en base et nulle part ailleurs.

    Aucune vérité de secret, aucune vérité de destinée : on ne sort d'ici que
    ce que le joueur a réellement vu.
    """
    lieu = session.get(Location, pj.location_id) if pj.location_id else None

    chapitres = [s.texte.strip() for s in session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau == "scene")
        .order_by(Summary.du_tour)).all() if (s.texte or "").strip()]

    jalons = session.exec(select(Event).where(
        Event.campaign_id == camp.id, Event.importance >= 4)
        .order_by(Event.tour)).all()

    quetes = session.exec(select(Quest).where(
        Quest.campaign_id == camp.id)).all()
    rencontres = session.exec(select(Encounter).where(
        Encounter.campaign_id == camp.id)).all()

    proches = []
    for rel in session.exec(select(Relation).where(
            Relation.campaign_id == camp.id, Relation.cible_id == pj.id)).all():
        autre = session.get(Character, rel.source_id)
        if autre is None:
            continue
        proches.append({"nom": autre.nom, "valeur": rel.valeur,
                        "nature": rel.nature, "vivant": autre.vivant,
                        "role": autre.role_campagne})
    proches.sort(key=lambda r: -abs(r["valeur"]))

    # Les questions, JAMAIS leurs réponses.
    ouvertes = [s.question.strip()
                for s in horloge_secrets.entames(session, camp)]
    perdues = [s.question.strip() for s in session.exec(select(Secret).where(
        Secret.campaign_id == camp.id, Secret.perdu == True)).all()]  # noqa: E712

    eveils = [{"libelle": t.libelle or t.trait_ref,
               "revelation": t.revelation_initiale or ""}
              for t in _eveils(session, pj)]

    dernier = session.exec(select(Turn).where(
        Turn.campaign_id == camp.id).order_by(Turn.index.desc())).first()

    return {
        "tour": camp.tour,
        "nom": pj.nom,
        "grade": rs.titre_grade(pj.grade),
        "niveau": pj.niveau,
        "tier": rs.tier_label(pj.tier),
        "clan": pj.clan,
        "village": pj.village,
        "lieu": lieu.nom if lieu else "",
        "vivant": pj.vivant,
        "chapitres": chapitres,
        "jalons": [f"(tour {e.tour}) {e.resume}" for e in jalons],
        "reussies": [q.titre for q in quetes if q.statut == "réussie"],
        "echouees": [q.titre for q in quetes if q.statut == "échouée"],
        "en_cours": [q.titre for q in quetes
                     if q.statut in ("proposée", "acceptée", "en cours")],
        "combats": len(rencontres),
        "proches": proches[:6],
        "questions": ouvertes,
        "perdues": perdues,
        "eveils": eveils,
        "derniere_scene": (dernier.narration or "")[-500:] if dernier else "",
    }


def _sans_modele(d: dict) -> str:
    """L'épilogue quand il n'y a pas de modèle — ou qu'il n'a pas répondu.

    Sec, factuel, et complet. Mieux vaut un relevé honnête qu'une page vide :
    c'est le dernier texte de la campagne, il ne peut pas manquer.
    """
    p = [f"{d['nom']}, {d['grade'].lower()} {fr.de(d['village'])}, s'est arrêté "
         f"{fr.a(d['lieu']) if d['lieu'] else 'en chemin'} au tour {d['tour']}."]
    if not d["vivant"]:
        p.append("Il n'en est pas revenu.")
    if d["eveils"]:
        p.append("Ce qui dormait s'est éveillé : "
                 + fr.enumerer([e["libelle"] for e in d["eveils"]]) + ".")
    if d["reussies"]:
        p.append("Menées à bien : "
                 + fr.enumerer([f"« {t} »" for t in d["reussies"]]) + ".")
    if d["echouees"]:
        p.append("Manquées : "
                 + fr.enumerer([f"« {t} »" for t in d["echouees"]]) + ".")
    vivants = [r["nom"] for r in d["proches"] if r["vivant"]]
    if vivants:
        p.append("Sont restés : " + fr.enumerer(vivants) + ".")
    if d["questions"] or d["perdues"]:
        p.append("Sans réponse, et pour toujours : " + fr.enumerer(
            [f"« {q} »" for q in (d["questions"] + d["perdues"])[:3]]) + ".")
    return " ".join(p)


def _invite(d: dict) -> str:
    lignes = [
        f"### LE PERSONNAGE\n{d['nom']}, {d['grade']}, niveau {d['niveau']} "
        f"({d['tier']}), {d['clan']}, {d['village']}."
        + ("" if d["vivant"] else " IL EST MORT."),
        f"### OÙ ÇA S'ARRÊTE\nTour {d['tour']}"
        + (f", {fr.a(d['lieu'])}." if d["lieu"] else "."),
    ]
    if d["eveils"]:
        lignes.append("### CE QUI S'EST ÉVEILLÉ EN LUI\n" + "\n".join(
            f"- {e['libelle']}" + (f" — {e['revelation']}" if e["revelation"] else "")
            for e in d["eveils"]))
    if d["chapitres"]:
        lignes.append("### L'HISTOIRE, CHAPITRE PAR CHAPITRE\n"
                      + "\n\n".join(d["chapitres"][-8:]))
    if d["jalons"]:
        lignes.append("### CE QUI A COMPTÉ\n"
                      + "\n".join(f"- {j}" for j in d["jalons"][-12:]))
    if d["reussies"] or d["echouees"]:
        lignes.append("### MISSIONS\n"
                      + "\n".join(f"- réussie : {t}" for t in d["reussies"])
                      + "\n"
                      + "\n".join(f"- échouée : {t}" for t in d["echouees"]))
    if d["proches"]:
        lignes.append("### CEUX QUI COMPTAIENT\n" + "\n".join(
            f"- {r['nom']} ({r['nature']}, {r['valeur']:+d})"
            + ("" if r["vivant"] else " — mort")
            for r in d["proches"]))
    if d["questions"] or d["perdues"]:
        lignes.append(
            "### CE QUI RESTE SANS RÉPONSE\n"
            + "\n".join(f"- « {q} »" for q in d["questions"])
            + "\n"
            + "\n".join(f"- « {q} » (il est trop tard)" for q in d["perdues"])
            + "\nTu n'en connais pas les réponses et tu ne les inventes pas.")
    if d["derniere_scene"]:
        lignes.append("### LA DERNIÈRE SCÈNE JOUÉE\n" + d["derniere_scene"])
    return "\n\n".join(lignes)


# --------------------------------------------------------------------------
# Clore, et rouvrir
# --------------------------------------------------------------------------
def clore(session: Session, camp: Campaign, pj: Character,
          rs: Ruleset) -> dict:
    """Écrit l'épilogue et ferme la campagne.

    NE LÈVE QUE SI C'EST REFUSÉ. Un modèle muet ne doit pas empêcher de
    conclure : on retombe sur le relevé, qui dit déjà tout ce qui a eu lieu.
    """
    ok, raison = possible(session, camp, pj)
    if not ok:
        raise FinRefusee(raison)

    d = releve(session, camp, pj, rs)
    texte = ""
    try:
        texte = (get_llm().text(EPILOGUE, _invite(d)) or "").strip()
    except Exception:  # noqa: BLE001 — on conclut quand même
        texte = ""
    du_modele = bool(texte)
    if not texte:
        texte = _sans_modele(d)

    session.add(Summary(campaign_id=camp.id, niveau=NIVEAU,
                        du_tour=1, au_tour=camp.tour, texte=texte))
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="fin",
        resume=f"La chronique de {pj.nom} est close au tour {camp.tour}.",
        importance=5, entites=[pj.id]))
    camp.phase = "terminee"
    camp.joue_le = maintenant()
    session.add(camp)
    session.commit()
    return {"texte": texte, "releve": d, "du_modele": du_modele}


def rouvrir(session: Session, camp: Campaign) -> None:
    """On se trompe, on veut jouer une scène de plus.

    Rien n'est effacé : l'épilogue reste dans la chronique, daté du tour où on
    croyait avoir fini. S'il y en a un second, les deux seront là — et c'est
    honnête, puisque la campagne a bel et bien failli s'arrêter deux fois.
    """
    if camp.phase != "terminee":
        return
    camp.phase = "en_cours"
    camp.joue_le = maintenant()
    session.add(camp)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="fin",
        resume="La chronique est rouverte : il restait quelque chose à jouer.",
        importance=3))
    session.commit()
