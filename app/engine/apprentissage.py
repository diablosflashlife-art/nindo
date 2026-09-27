"""Apprendre une technique — la seule chose que le jeu ne savait pas faire.

CE QUI MANQUAIT. La maîtrise montait par l'usage, et c'était tout. Le
répertoire d'un personnage était figé à la création : trois techniques au tour
1, les mêmes trois au tour 80, un peu mieux maîtrisées. Dans ce genre, la
technique apprise au bon moment EST le récit — le parchemin volé, le maître qui
transmet, la semaine passée à rater le même geste.

TROIS CHEMINS, ET ILS NE VALENT PAS PAREIL.

  UN MAÎTRE. Quelqu'un de présent qui connaît réellement la technique et qui
  ne te déteste pas. C'est le chemin court, et c'est le seul qui donne un
  départ au-dessus du minimum : on apprend mieux de quelqu'un.

  UN PARCHEMIN. Dans ton inventaire. Il se consomme. C'est le chemin du butin
  et des récompenses de mission — ce qui donne une valeur à ce qu'on ramasse.

  LE TRAVAIL. Seul, dans ta spécialité, à plein tarif en temps. C'est toujours
  possible et ça ne dépend de personne : le filet qui garantit qu'un joueur
  isolé progresse quand même.

CE QUE ÇA COÛTE : DU TEMPS DE CAMPAGNE. Un rang C prend trois tours, et les
échéances de mission courent pendant qu'on s'entraîne. Sans ce prix, apprendre
serait un bouton gratuit qu'on presse jusqu'à tout connaître.

LE MOTEUR DÉCIDE, LE MODÈLE N'INTERVIENT PAS. L'éligibilité est entièrement
déterministe — rang, grade, caractéristique, clan, prérequis. Aucun appel au
modèle : apprendre doit marcher avec Ollama éteint, comme le repos.
"""
from __future__ import annotations

import random
import re
import unicodedata

from sqlmodel import Session, select

from app.engine import francais as fr
from app.lore.pack import LorePack
from app.models import (Campaign, Character, CharacterTechnique, Event,
                        MemoryFact, Relation, maintenant)
from app.rules.engine import Ruleset

RANGS = ["E", "D", "C", "B", "A", "S"]

# La spécialité de la fiche ne porte pas toujours le nom de la catégorie de
# technique. Même table que `creation._techniques_depart` : une seule vérité.
CATEGORIE_DE_SPECIALITE = {"medecine": "iryo"}

# Ce qu'on écrit dans l'inventaire. Le préfixe est la clé de reconnaissance :
# c'est lui qui distingue un parchemin d'apprentissage d'un objet quelconque.
PREFIXE_PARCHEMIN = "Parchemin —"


class ApprentissageRefuse(RuntimeError):
    """La technique ne peut pas être apprise, et le joueur doit savoir pourquoi."""


# --------------------------------------------------------------------------
# Les réglages, tous dans le ruleset
# --------------------------------------------------------------------------
def _cfg(rs: Ruleset) -> dict:
    return rs.data.get("apprentissage", {}) or {}


def rangs_ouverts(rs: Ruleset, grade: str) -> list[str]:
    table = _cfg(rs).get("rangs_par_grade", {}) or {}
    return list(table.get(grade) or table.get("genin") or ["E", "D"])


def tours_pour(rs: Ruleset, rang: str, *, avec_maitre: bool = False) -> int:
    base = int((_cfg(rs).get("tours_par_rang", {}) or {}).get(rang, 2))
    if avec_maitre:
        remise = float(_cfg(rs).get("remise_du_maitre", 0.5))
        base = round(base * (1.0 - max(0.0, min(0.9, remise))))
    return max(1, base)


def stat_minimale(rs: Ruleset, rang: str) -> int:
    return int((_cfg(rs).get("stat_minimale", {}) or {}).get(rang, 0))


# --------------------------------------------------------------------------
# Ce que le personnage peut apprendre
# --------------------------------------------------------------------------
def _cle(texte: str) -> str:
    """Comparaison tolérante : sans accents, sans casse, sans ponctuation.

    Un parchemin ramassé s'écrit « Parchemin — Shunshin no Jutsu » ; le joueur
    ne doit pas perdre sa récompense parce qu'une apostrophe diffère.
    """
    nu = "".join(c for c in unicodedata.normalize("NFD", texte or "")
                 if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", nu.lower()).strip()


def specialite(session: Session, pack: LorePack, pj: Character) -> str:
    """La catégorie de technique qu'on sait travailler seul.

    Les campagnes d'avant cette colonne n'ont rien de stocké : on la retrouve
    alors dans les techniques de départ, qui ont justement été choisies depuis
    la spécialité. Une sauvegarde doit survivre aux versions.
    """
    brute = (pj.specialisation or "").strip().lower()
    if brute:
        return _cle(CATEGORIE_DE_SPECIALITE.get(brute, brute))

    comptes: dict[str, int] = {}
    for ref in connues(session, pj):
        cat = (pack.technique(ref) or {}).get("categorie", "")
        if cat and cat != "taijutsu":   # tout le monde sort de l'Académie
            comptes[cat] = comptes.get(cat, 0) + 1
    return _cle(max(comptes, key=comptes.get)) if comptes else ""


def connues(session: Session, perso: Character) -> dict[str, CharacterTechnique]:
    return {ct.technique_ref: ct for ct in session.exec(
        select(CharacterTechnique).where(
            CharacterTechnique.character_id == perso.id)).all()}


def _prerequis_manquants(pack: LorePack, tid: str,
                         deja: dict[str, CharacterTechnique]) -> list[str]:
    """Les techniques qu'il faut connaître AVANT. Écrites dans `synergies`
    depuis le premier jour, et opposables ici pour la première fois."""
    manque = []
    for s in pack.synergies("prerequis"):
        if s.get("debloque") != tid:
            continue
        seuil = int(s.get("maitrise_min", 0))
        for r in s.get("requiert") or []:
            ct = deja.get(r)
            if ct is None or ct.maitrise < seuil:
                manque.append((pack.technique(r) or {}).get("nom", r))
    return manque


def _maitres(session: Session, camp: Campaign, rs: Ruleset, pj: Character,
             tid: str) -> list[Character]:
    """Qui, ici et maintenant, sait réellement cette technique et l'enseignerait.

    Trois conditions, et aucune n'est décorative : être là, la maîtriser
    vraiment, et ne pas détester l'élève.
    """
    if not pj.location_id:
        return []
    seuil = int(_cfg(rs).get("maitrise_pour_enseigner", 60))
    out = []
    for c in session.exec(select(Character).where(
            Character.campaign_id == camp.id,
            Character.location_id == pj.location_id,
            Character.is_pc == False,          # noqa: E712
            Character.vivant == True)).all():  # noqa: E712
        ct = session.exec(select(CharacterTechnique).where(
            CharacterTechnique.character_id == c.id,
            CharacterTechnique.technique_ref == tid)).first()
        if ct is None or ct.maitrise < seuil:
            continue
        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id, Relation.source_id == c.id,
            Relation.cible_id == pj.id)).first()
        if rel is not None and rel.valeur < 0:
            continue          # on n'apprend pas de quelqu'un qui te méprise
        out.append(c)
    return out


def _parchemin(pj: Character, tech: dict) -> str:
    """L'entrée d'inventaire qui ouvre cette technique, ou une chaîne vide."""
    cibles = {_cle(tech.get("nom", "")), _cle(tech.get("fr", "")),
              _cle(tech.get("id", ""))} - {""}
    for objet in (pj.inventaire or []):
        c = _cle(objet)
        if not c.startswith(_cle(PREFIXE_PARCHEMIN)):
            continue
        if any(x and x in c for x in cibles):
            return objet
    return ""


def offres(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
           pj: Character) -> list[dict]:
    """Tout ce que ce personnage pourrait apprendre, avec le chemin et le prix.

    On rend la liste COMPLÈTE de ce qui est ouvert, triée du plus accessible au
    plus lointain. Une technique qu'on ne voit pas ne sera jamais visée, et
    c'est précisément ce qui donne un objectif d'entraînement.
    """
    deja = connues(session, pj)
    ouverts = rangs_ouverts(rs, pj.grade)
    spec = specialite(session, pack, pj)

    out = []
    for t in pack.techniques(clan=pj.clan_ref or None):
        tid = t.get("id")
        if not tid or tid in deja:
            continue
        rang = t.get("rang", "E")
        if rang not in ouverts:
            continue

        cle_stat = t.get("stat") or ""
        valeur = int(pj.stats.get(cle_stat, 0)) if cle_stat else 99
        besoin = stat_minimale(rs, rang)
        manque_prereq = _prerequis_manquants(pack, tid, deja)

        maitres = _maitres(session, camp, rs, pj, tid)
        rouleau = _parchemin(pj, t)
        # LE TRAVAIL SOLITAIRE. Dans sa propre spécialité — sans ce garde-fou,
        # un spécialiste de fûinjutsu apprendrait le genjutsu tout seul — ET
        # dans les bases de l'Académie, que tout diplômé sait reprendre au
        # poteau d'entraînement. Ce second cas est le PLANCHER : il garantit
        # qu'aucun personnage ne se retrouve sans rien à travailler, même quand
        # sa spécialité ne compte que deux entrées dans le pack.
        seul = (bool(spec) and _cle(t.get("categorie", "")) == spec) or rang == "E"

        if maitres:
            source, detail = "maitre", maitres[0].nom
        elif rouleau:
            source, detail = "parchemin", rouleau
        elif seul:
            source, detail = "travail", "ta spécialité"
        else:
            continue

        pret = valeur >= besoin and not manque_prereq
        out.append({
            "id": tid,
            "nom": t.get("nom", tid),
            "fr": t.get("fr", ""),
            "rang": rang,
            "categorie": t.get("categorie", ""),
            "effet": t.get("effet", ""),
            "source": source,
            "detail": detail,
            "maitre_id": maitres[0].id if maitres else None,
            "tours": tours_pour(rs, rang, avec_maitre=bool(maitres)),
            "stat": cle_stat,
            "stat_label": (rs.stats.get(cle_stat, {}) or {}).get("label", cle_stat),
            "stat_valeur": valeur,
            "stat_requise": besoin,
            "manque": manque_prereq,
            "pret": pret,
            "progression": int((pj.entrainements or {}).get(tid, 0)),
        })

    ordre = {"maitre": 0, "parchemin": 1, "travail": 2}
    # Ce qu'on a déjà commencé d'abord : on reprend ce qu'on travaillait.
    return sorted(out, key=lambda o: (not o["pret"], -o["progression"], ordre[o["source"]],
                                      RANGS.index(o["rang"]), o["nom"]))


# --------------------------------------------------------------------------
# S'entraîner : une séance JOUÉE
# --------------------------------------------------------------------------
# Retour de partie : « on apprend des techniques comme ça, sans scène, sans
# dé, sans entraînement ». Un bouton inscrivait la technique d'un coup. Chaque
# séance est désormais un tour de jeu : un geste raconté, un jet, une
# progression. Il faut autant de séances réussies que de « tours » du rang.
DIFFICULTE_PAR_RANG = {"E": "facile", "D": "normal", "C": "difficile",
                       "B": "ardue", "A": "ardue", "S": "legendaire"}
FACTEUR_PAR_ISSUE = {"reussite_critique": 1.5, "reussite": 1.0, "reussite_partielle": 0.6,
                     "echec": 0.35, "echec_critique": 0.0}
BONUS_DU_MAITRE = 1.5


def offre(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
          pj: Character, tid: str) -> dict | None:
    return next((o for o in offres(session, camp, pack, rs, pj) if o["id"] == tid), None)


def verifier_seance(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                    pj: Character, tid: str) -> dict:
    """L'offre à travailler, ou ApprentissageRefuse avec la raison."""
    o = offre(session, camp, pack, rs, pj, tid)
    if o is None:
        raise ApprentissageRefuse(
            "Rien ne t'ouvre cette technique pour le moment : il te faut un "
            "maître présent, un parchemin, ou qu'elle relève de ta spécialité.")
    if o["manque"]:
        raise ApprentissageRefuse("Il te manque d'abord " + fr.enumerer(o["manque"]) + ".")
    if o["stat_valeur"] < o["stat_requise"]:
        raise ApprentissageRefuse(
            f"{o['stat_label']} {o['stat_valeur']} ne suffit pas pour un rang "
            f"{o['rang']} : il en faut {o['stat_requise']}.")
    return o


def seance(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
           pj: Character, o: dict, issue: str) -> tuple[str, list[str]]:
    """Fait avancer l'apprentissage selon l'issue du jet. Rend (bloc pour le
    narrateur, effets pour le joueur). À 100 %, la technique est acquise."""
    tours = tours_pour(rs, o["rang"])
    gain = 100.0 / max(1, tours) * FACTEUR_PAR_ISSUE.get(issue, 0.35)
    if o["source"] == "maitre":
        gain *= BONUS_DU_MAITRE
    avant = int((pj.entrainements or {}).get(o["id"], 0))
    apres = min(100, int(round(avant + gain)))
    etat = dict(pj.entrainements or {})
    effets: list[str] = []
    if apres >= 100:
        etat.pop(o["id"], None)
        pj.entrainements = etat
        effets += _acquerir(session, camp, rs, pj, o)
        fin = ("TECHNIQUE ACQUISE : raconte le moment où elle sort enfin, "
               "entière, pour la première fois.")
    else:
        etat[o["id"]] = apres
        pj.entrainements = etat
        effets.append(f"Entraînement — {o['nom']} : {avant} % → {apres} %")
        fin = ("La technique n'est PAS encore acquise : raconte une séance "
               "de travail, ses gestes, ses ratés, et ce qui a progressé.")
    session.add(pj)
    avec = {"maitre": f", sous la conduite de {o['detail']}",
            "parchemin": ", en suivant un parchemin",
            "travail": ", seul"}[o["source"]]
    bloc = (f"SÉANCE D'ENTRAÎNEMENT — {o['nom']}"
            f"{' (' + o['fr'] + ')' if o.get('fr') else ''}{avec}.\n"
            f"Progression : {avant} % → {apres} %.\n{fin}")
    return bloc, effets


def _acquerir(session: Session, camp: Campaign, rs: Ruleset, pj: Character,
              o: dict) -> list[str]:
    maitrise = int(_cfg(rs).get(
        "maitrise_du_maitre" if o["source"] == "maitre" else "maitrise_initiale", 40))
    session.add(CharacterTechnique(
        campaign_id=camp.id, character_id=pj.id, technique_ref=o["id"],
        maitrise=maitrise, appris_tour=camp.tour))
    if o["source"] == "parchemin":
        pj.inventaire = [x for x in (pj.inventaire or []) if x != o["detail"]]
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="apprentissage",
        resume=f"{pj.nom} maîtrise désormais {o['nom']}.",
        importance=4, entites=[i for i in (pj.id, o["maitre_id"]) if i]))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, nature="fait", importance=4,
        texte=f"Au tour {camp.tour}, {pj.nom} a appris {o['nom']} à force d'entraînement.",
        entites=[i for i in (pj.id, o["maitre_id"]) if i]))
    return [f"Technique apprise : {o['nom']} !"]


# --------------------------------------------------------------------------
# Apprendre d'un coup (ancien chemin, gardé pour l'API)
# --------------------------------------------------------------------------
def apprendre(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
              pj: Character, tid: str) -> list[str]:
    """Passe le temps qu'il faut, et inscrit la technique.

    Avance l'horloge de campagne comme un voyage : les échéances se vérifient,
    les blessures se referment, les ressources remontent. C'est le même prix,
    parce que c'est le même temps.
    """
    from app.engine import combat
    from app.engine import missions as gen_missions

    if combat.active(session, camp) is not None:
        raise ApprentissageRefuse(
            "On n'apprend pas une technique au milieu d'un échange de coups.")

    offre = next((o for o in offres(session, camp, pack, rs, pj)
                  if o["id"] == tid), None)
    if offre is None:
        raise ApprentissageRefuse(
            "Rien ne t'ouvre cette technique pour le moment : il te faut un "
            "maître présent, un parchemin, ou qu'elle relève de ta spécialité.")
    if offre["manque"]:
        raise ApprentissageRefuse(
            "Il te manque d'abord " + fr.enumerer(offre["manque"]) + ".")
    if offre["stat_valeur"] < offre["stat_requise"]:
        raise ApprentissageRefuse(
            f"{offre['stat_label']} {offre['stat_valeur']} ne suffit pas pour "
            f"un rang {offre['rang']} : il en faut {offre['stat_requise']}.")

    tours = offre["tours"]
    maitrise = int(_cfg(rs).get(
        "maitrise_du_maitre" if offre["source"] == "maitre"
        else "maitrise_initiale", 40))

    session.add(CharacterTechnique(
        campaign_id=camp.id, character_id=pj.id, technique_ref=tid,
        maitrise=maitrise, appris_tour=camp.tour))

    # Le parchemin se consomme. Sans ça, un seul rouleau servirait à toute
    # l'équipe et pour toujours.
    if offre["source"] == "parchemin":
        pj.inventaire = [o for o in (pj.inventaire or [])
                         if o != offre["detail"]]

    comment = {
        "maitre": f"sous la conduite de {offre['detail']}",
        "parchemin": "à partir d'un parchemin",
        "travail": "seul, à force de recommencer",
    }[offre["source"]]

    camp.tour += tours
    camp.joue_le = maintenant()
    session.add(pj)
    session.add(camp)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="apprentissage",
        resume=f"{pj.nom} a appris {offre['nom']} {comment} "
               f"({fr.accorde(tours, 'tour')} d'entraînement).",
        importance=4, entites=[i for i in (pj.id, offre["maitre_id"]) if i]))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, nature="fait", importance=4,
        texte=f"Au tour {camp.tour}, {pj.nom} a appris {offre['nom']} "
              f"{comment}.",
        entites=[i for i in (pj.id, offre["maitre_id"]) if i]))

    effets = [f"Technique apprise : {offre['nom']} "
              f"({fr.accorde(tours, 'tour')} d'entraînement)."]
    session.commit()

    effets += gen_missions.verifier_echeances(session, camp)
    effets += combat.soigner_le_temps(session, camp, pj, rs)
    for _ in range(tours):
        effets += combat.recuperer(session, camp, rs, pj)

    from app.engine.creation import _recalculer_puissance
    _recalculer_puissance(session, camp, rs, pj)
    session.commit()
    return effets


# --------------------------------------------------------------------------
# Les parchemins, et d'où ils viennent
# --------------------------------------------------------------------------
# Assez rare pour qu'un parchemin reste une trouvaille. Un butin sur cinq.
CHANCE_PARCHEMIN = 0.2


def tirer_parchemin(session: Session, camp: Campaign, pack: LorePack,
                    rs: Ruleset, pj: Character, *, graine: int = 0) -> list[str]:
    """Une victoire peut laisser un rouleau. DÉTERMINISTE sur la graine.

    On ne tire que dans ce qui est RÉELLEMENT utile au personnage : son grade,
    ses prérequis, son clan. Un parchemin qu'on ne peut pas lire n'est pas une
    récompense, c'est une ligne d'inventaire.
    """
    deja = connues(session, pj)
    ouverts = rangs_ouverts(rs, pj.grade)
    candidats = [
        t for t in pack.techniques(clan=pj.clan_ref or None)
        if t.get("id") and t["id"] not in deja
        and t.get("rang") in ouverts
        and not _prerequis_manquants(pack, t["id"], deja)
        and int(pj.stats.get(t.get("stat") or "", 99))
        >= stat_minimale(rs, t.get("rang", "E"))
        and not _parchemin(pj, t)
    ]
    if not candidats:
        return []

    rng = random.Random(camp.graine * 104729 + camp.tour * 31 + graine)
    if rng.random() > CHANCE_PARCHEMIN:
        return []

    # Le plus haut rang ouvert d'abord : un rouleau doit valoir le détour.
    candidats.sort(key=lambda t: -RANGS.index(t.get("rang", "E")))
    choix = rng.choice(candidats[:4])
    objet = f"{PREFIXE_PARCHEMIN} {choix['nom']}"
    pj.inventaire = list(pj.inventaire or []) + [objet]
    session.add(pj)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="butin",
        resume=f"{pj.nom} a mis la main sur un parchemin : {choix['nom']}.",
        importance=3, entites=[pj.id]))
    return [f"Parchemin trouvé : {choix['nom']}."]
