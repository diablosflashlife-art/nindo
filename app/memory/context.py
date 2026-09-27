"""Construction du contexte envoyé au modèle.

C'est LE module critique : la qualité du maître du jeu dépend moins du modèle
que de ce qu'on lui met sous les yeux.

Cinq couches, du plus fiable au plus flou, sous un budget de tokens fixe qui
ne grandit ni avec la taille du monde, ni avec la durée de la partie :

  LORE  MONDE      le lore de la scène — clans, techniques, armes, région
  L0    ÉTAT       lu en SQL, toujours à jour, toujours injecté
  L1    COURTE     les N derniers tours, mot à mot
  L2    CHRONIQUE  résumés hiérarchiques du passé
  L3    FAITS      faits atomiques rappelés par pertinence

UN CONTEXTE PAR RÔLE.
Les quatre appels d'un tour n'ont pas les mêmes besoins. Le narrateur a besoin
de tout ; l'arbitre a besoin de la fiche et du répertoire de techniques pour
choisir une caractéristique ; le simulateur de conséquences n'a besoin que de
la LISTE DES ENTITÉS auxquelles il a le droit de faire référence. Envoyer le
contexte complet aux quatre coûtait quatre fois le traitement du prompt pour
rien — et empêchait toute réutilisation du cache, puisque le prompt système
change à chaque appel.

LE BUDGET EST UNE RÉSERVATION, PAS UN PLAFOND MOU.
On construit d'abord l'état de jeu, qui est borné par nature (N tours, N
faits). On mesure. Puis on donne au dossier lore ce qui reste, dans la limite
de son propre plafond. Ainsi l'état de jeu ne peut jamais être évincé par du
lore, et le lore se réduit tout seul sur une longue partie.

FILTRE DE DIVULGATION — point de passage unique.
Ne sortent JAMAIS d'ici : Secret.verite, DestinyTrait.verite, les Knowledge
que les joueurs n'ont pas, les relations non publiques non découvertes, toute
valeur brute de puissance, les organisations marquées `secrete` dans le lore,
les faits de nature `secret` (graines d'intrigue semées par le moteur), et les
paliers de lignée non atteints. Un modèle qui voit une information la laisse
transparaître, même sans la nommer.
"""
from __future__ import annotations

import re

from sqlmodel import Session, select

from app.config import settings
from app.llm.embeddings import cosinus, get_embeddeur
from app.lore.brief import dossier as dossier_lore
from app.lore.pack import LorePack
from app.models import (Campaign, Character, CharacterCapacity, Faction,
                        Knowledge, Location, MemoryFact, Quest, Relation,
                        Summary, Turn)
from app.rules.engine import Ruleset

# Ce que chaque rôle reçoit. C'est la table qui décide du coût d'un tour.
SECTIONS_PAR_ROLE: dict[str, set[str]] = {
    "narrateur":    {"entete", "fiche", "lore", "lieu", "rencontre", "liens",
                     "missions", "fils", "factions", "secrets", "monde",
                     "chronique", "faits", "infos", "tours"},
    "arbitre":      {"entete", "stats", "fiche", "lore_reduit", "lieu",
                     "rencontre", "postures", "missions", "dernier_tour"},
    "consequences": {"entete", "entites", "missions", "fils_liste"},
    "propositions": {"entete", "fiche_courte", "lieu", "rencontre", "missions"},
}


def _label_relation(v: int) -> str:
    if v <= -60: return "hostile"
    if v <= -20: return "méfiant"
    if v < 20:   return "neutre"
    if v < 60:   return "amical"
    return "loyal"


def _mots(texte: str) -> set[str]:
    return set(re.findall(r"\w{4,}", (texte or "").lower()))


# Ce que pèse la proximité de SENS face au recouvrement de mots. À six, un
# fait qui parle exactement du même sujet sans partager un mot bat un fait qui
# partage deux mots par hasard — ce qui est précisément le but.
POIDS_SEMANTIQUE = 6.0


def rappeler_faits(session: Session, camp: Campaign, requete: str,
                   limite: int = 8) -> list[MemoryFact]:
    """Rappel hybride : SENS + lettres + importance + récence.

    Le lexical seul ne retrouvait pas « a promis au vieux forgeron » quand le
    joueur écrivait « ce que j'ai juré à Tetsuo ». Le sémantique seul
    manquerait un nom propre ou un titre de mission, que les lettres
    retrouvent mieux que le sens. Les deux s'additionnent.

    DÉGRADATION SILENCIEUSE : sans modèle d'embedding installé, les vecteurs
    sont vides, le terme sémantique vaut zéro partout, et l'on retrouve
    exactement le classement d'avant.

    Les faits de nature `secret` sont EXCLUS. Le moteur en sème (les graines
    d'intrigue) et ils ne doivent ressortir qu'une fois découverts — sans ce
    filtre, la graine remontait au tour suivant dans « FAITS ÉTABLIS », ce qui
    contournait le filtre de divulgation depuis l'intérieur.
    """
    faits = session.exec(select(MemoryFact).where(
        MemoryFact.campaign_id == camp.id,
        MemoryFact.nature != "secret")).all()
    if not faits:
        return []
    cibles = _mots(requete)

    vecteur_requete: list[float] = []
    if any(f.vecteur for f in faits):
        embeddeur = get_embeddeur()
        if embeddeur.disponible:
            vecteur_requete = embeddeur.vecteurs([requete])[0]

    def score(f: MemoryFact) -> float:
        lexical = len(cibles & _mots(f.texte)) * 2
        semantique = (cosinus(vecteur_requete, f.vecteur) * POIDS_SEMANTIQUE
                      if vecteur_requete else 0.0)
        return lexical + semantique + f.importance + f.tour * 0.01

    return sorted(faits, key=score, reverse=True)[:limite]


def vectoriser(faits: list[MemoryFact]) -> None:
    """Pose le vecteur des faits qui n'en ont pas, EN UN SEUL APPEL.

    Appelée juste avant l'écriture. Un appel par fait aurait multiplié la
    latence par cinq pour rien : le modèle accepte une liste.
    """
    manquants = [f for f in faits if f.texte and not f.vecteur]
    if not manquants:
        return
    embeddeur = get_embeddeur()
    if not embeddeur.disponible:
        return
    for fait, vec in zip(manquants,
                         embeddeur.vecteurs([f.texte for f in manquants])):
        if vec:
            fait.vecteur = vec


def _connu_du_groupe(session: Session, camp: Campaign, sujet_id: int,
                     aspect: str) -> Knowledge | None:
    return session.exec(select(Knowledge).where(
        Knowledge.campaign_id == camp.id,
        Knowledge.sujet_type == "character",
        Knowledge.sujet_id == sujet_id,
        Knowledge.aspect == aspect,
        Knowledge.niveau >= 2)).first()


def _lignee_du_personnage(session: Session, camp: Campaign, pj: Character,
                          pack: LorePack | None) -> tuple[str, int]:
    """La lignée du personnage et le palier RÉELLEMENT atteint.

    Le palier est ce qui borne ce que le dossier lore a le droit de décrire :
    au-delà, les déclencheurs des stades supérieurs sont des spoilers.
    """
    if pack is None or not pj.clan_ref:
        return "", 0
    clan = pack.clan(pj.clan_ref) or {}
    ref = clan.get("lignee") or ""
    if not ref:
        return "", 0
    cap = session.exec(select(CharacterCapacity).where(
        CharacterCapacity.character_id == pj.id,
        CharacterCapacity.capacite_ref == ref)).first()
    return ref, int(cap.palier if cap else 0)


# --------------------------------------------------------------------------
# Sections d'état
# --------------------------------------------------------------------------
def _s_entete(camp: Campaign) -> str:
    return (f"### CAMPAGNE\n{camp.nom} — tour {camp.tour}\n"
            f"Époque : {camp.epoque}. Ton : {camp.ton}.")


def _s_stats(rs: Ruleset) -> str:
    return "### CARACTÉRISTIQUES DISPONIBLES\n" + ", ".join(rs.stats.keys())


def _s_fiche(session: Session, camp: Campaign, pj: Character, rs: Ruleset,
             pack: LorePack | None, court: bool = False) -> str:
    stats_cfg = rs.stats
    fiche = [f"{pj.nom}, {pj.age or '?'} ans — {rs.titre_grade(pj.grade)}, "
             f"niveau {pj.niveau}",
             f"Clan : {pj.clan or 'aucun'} | Village : {pj.village or 'inconnu'}"]
    if not court:
        stats = ", ".join(f"{stats_cfg.get(k, {}).get('label', k)} {v}"
                          for k, v in pj.stats.items())
        fiche.append(f"Caractéristiques : {stats}")
    fiche.append("Ressources : " + ", ".join(f"{k} {v}"
                                             for k, v in pj.ressources.items()))
    techs = _techniques_de(session, camp, pj, pack)
    fiche.append(f"Techniques : {', '.join(techs) if techs else 'aucune'}")
    fiche.append(f"Inventaire : "
                 f"{', '.join(pj.inventaire) if pj.inventaire else 'vide'}")
    if pj.etats:
        fiche.append(f"États : {', '.join(pj.etats)}")
    return "### PERSONNAGE QUI AGIT CE TOUR-CI\n" + "\n".join(fiche)


def _presents(session: Session, camp: Campaign, pj: Character,
              lieu: Location) -> list[Character]:
    return session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.location_id == lieu.id,
        Character.id != pj.id,
        Character.vivant == True)).all()  # noqa: E712


def _s_lieu(session: Session, camp: Campaign, pj: Character, rs: Ruleset,
            lieu: Location | None) -> list[str]:
    if lieu is None:
        return []
    # Le nom DÉTERMINÉ — « la Tour du Kage », pas l'étiquette brute. Le
    # narrateur recopie ce qu'on lui donne : lui donner « Tour du Kage »
    # produisait « Kaito entre dans Tour du Kage ». Voir engine/francais.py.
    from app.engine import francais as fr

    parts = [f"### LIEU\n{fr.majuscule(fr.determine(lieu.nom))} "
             f"(danger {lieu.danger}/10)\n{lieu.description}"]
    presents = _presents(session, camp, pj, lieu)

    # Les autres PJ sont listés à part, très explicitement : c'est ce qui
    # empêche le modèle de décider à leur place.
    autres_pj = [c for c in presents if c.is_pc]
    if autres_pj:
        lignes = [f"- {c.nom} (joué par {c.joueur}), {c.clan}, "
                  f"{rs.titre_grade(c.grade)} — "
                  f"{c.personnalite or 'tempérament non défini'}"
                  for c in autres_pj]
        parts.append(
            "### AUTRES PERSONNAGES JOUEURS PRÉSENTS\n" + "\n".join(lignes) +
            "\n" + AVERTISSEMENT_AUTRES_PJ)

    pnjs = [c for c in presents if not c.is_pc]
    if pnjs:
        lignes = []
        for pnj in pnjs:
            rel = session.exec(select(Relation).where(
                Relation.campaign_id == camp.id,
                Relation.source_id == pnj.id,
                Relation.cible_id == pj.id)).first()
            v = rel.valeur if rel else 0
            l = [f"- {pnj.nom}"]
            if (qui := role_de(pnj)):
                l.append(f", {qui}")
            if pnj.clan and pnj.clan != "Sans clan":
                l.append(f" ({pnj.clan})")
            l.append(f" — {pnj.personnalite or 'tempérament inconnu'}")
            lignes.append("".join(l))
            if pnj.parler:
                lignes.append(f"  Parle ainsi : {pnj.parler}")
            lignes.append(f"  Attitude envers {pj.nom} : {_label_relation(v)} ({v})")
            # Les objectifs ne sont injectés que s'ils sont connus du groupe
            if pnj.objectifs and _connu_du_groupe(session, camp, pnj.id, "intention"):
                lignes.append(f"  Poursuit : {pnj.objectifs[0]}")
        parts.append("### PERSONNAGES PRÉSENTS\n" + "\n".join(lignes))
    return parts


# Remplacé en tour de table (voir engine/table.py) : là, les autres joueurs ont
# DÉCLARÉ leur action, et elle doit être racontée.
AVERTISSEMENT_AUTRES_PJ = ("Ces personnages appartiennent à d'autres joueurs humains. Tu "
                           "peux décrire ce qui leur arrive, jamais ce qu'ils "
                           "choisissent, disent ou tentent.")
AVERTISSEMENT_TOUR_DE_TABLE = ("Ces personnages appartiennent à d'autres joueurs humains. "
                               "CE TOUR-CI, chacun a déclaré son action (voir plus bas) : "
                               "raconte-la avec son résultat, sans rien y ajouter.")


# Le rôle d'un PNJ dans la vie du joueur. Mesuré sur une partie de 50 tours :
# sans lui, le narrateur faisait répondre « mon rival » par un marchand, et
# l'instructrice parlait de « ton instructeur » comme d'un tiers.
ROLES_LIBELLES = {"sensei": ("ton instructeur", "ton instructrice"),
                  "coequipier": ("ton coéquipier", "ta coéquipière"),
                  "rival": ("ton rival", "ta rivale")}


def role_de(pnj: Character) -> str:
    libelles = ROLES_LIBELLES.get(pnj.role_campagne or "")
    if not libelles:
        return ""
    return libelles[1] if (pnj.sexe or "").lower().startswith("f") else libelles[0]


def _s_equipe(session: Session, camp: Campaign, pj: Character,
              lieu: Location | None) -> str:
    """Qui est qui, même hors de la scène. « Je demande à mon rival » doit
    viser le rival, qu'il soit là ou non."""
    equipe = [c for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.is_pc == False)).all()  # noqa: E712
        if role_de(c)]
    if not equipe:
        return ""
    ordre = list(ROLES_LIBELLES)
    equipe.sort(key=lambda c: ordre.index(c.role_campagne))
    lignes = [f"- {c.nom} : {role_de(c)}"
              + ("" if lieu and c.location_id == lieu.id else " (pas dans la scène)")
              for c in equipe]
    return (f"### L'ENTOURAGE DE {pj.nom.upper()}\n" + "\n".join(lignes)
            + "\nQuand le joueur parle de son instructeur, de ses coéquipiers ou "
              "de son rival, c'est d'eux qu'il s'agit — et de personne d'autre.")


def _s_rencontre(session: Session, camp: Campaign, pj: Character,
                 rs: Ruleset) -> str:
    """L'affrontement en cours, s'il y en a un.

    Import local : `combat` importe `creation`, qui importe ce module. Le faire
    en tête créerait un cycle, et ce module doit pouvoir se charger seul.
    """
    from app.engine import combat

    renc = combat.active(session, camp)
    if renc is None:
        return ""
    return combat.etat_pour_contexte(session, camp, rs, renc, pj)


def _s_postures(session: Session, camp: Campaign, rs: Ruleset) -> str:
    """Le vocabulaire de l'arbitre de combat. Inutile hors rencontre, donc
    injecté seulement quand il y en a une."""
    from app.engine import combat

    if combat.active(session, camp) is None:
        return ""
    return "### POSTURES DISPONIBLES\n" + "\n".join(
        f"- {p['code']} ({p['label']}) — {p['aide']}"
        for p in combat.postures_offertes(rs))


def _s_missions(session: Session, camp: Campaign) -> str:
    """Les missions ouvertes, ET LE TEMPS QUI RESTE.

    Sans le délai, le narrateur n'avait aucune raison de presser qui que ce
    soit : une mission de douze tours se racontait au tour onze comme au tour
    deux. C'est le compte à rebours qui fait exister l'enjeu.
    """
    from app.engine.missions import ouvertes, reste

    quetes = ouvertes(session, camp)
    if not quetes:
        return ""
    lignes = []
    for q in quetes:
        r = reste(q, camp.tour)
        if r is None:
            delai = ""
        elif r <= 0:
            delai = " — DÉLAI DÉPASSÉ"
        elif r <= 2:
            delai = f" — plus que {r} tour(s) : l'urgence doit s'entendre"
        else:
            delai = f" — {r} tours restants"
        offre = (" — simple offre du bureau : l'équipe ne l'a pas prise"
                 if q.statut == "proposée" else "")
        from app.engine.fils import ARC_DENOUEMENT
        if q.statut in ("acceptée", "en cours") and q.debut_tour is not None \
                and camp.tour - q.debut_tour >= ARC_DENOUEMENT:
            offre += (" — ELLE SE CONCLUT MAINTENANT : si la scène l'a menée à "
                      "son terme, son statut est « réussie » ; si elle l'a "
                      "perdue, « échouée ».")
        lignes.append(f"- [{q.statut}] {q.titre} (rang {q.rang}){delai}{offre}\n"
                      f"  {q.description[:160]}")
    return "### MISSIONS EN COURS\n" + "\n".join(lignes)


def _s_entites(session: Session, camp: Campaign) -> str:
    """Le strict nécessaire du simulateur de conséquences : les noms qu'il a le
    droit de citer. Tout le reste de son contexte était du gaspillage."""
    persos = session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.vivant == True)).all()  # noqa: E712
    quetes = session.exec(select(Quest).where(
        Quest.campaign_id == camp.id)).all()
    lignes = ["Personnages existants (n'en cite AUCUN autre) :",
              ", ".join(c.nom for c in persos) or "aucun"]
    if quetes:
        lignes += ["Missions existantes (n'en cite AUCUNE autre) :",
                   ", ".join(q.titre for q in quetes)]
    return "### ENTITÉS RÉELLES\n" + "\n".join(lignes)


def _s_factions(session: Session, camp: Campaign, pack: LorePack | None) -> str:
    """Seules les factions PUBLIQUES.

    `amorcer_monde` instancie toutes les organisations du lore, y compris
    celles marquées `secrete` — La Racine, l'Akatsuki, les cultes. Les lister
    ici révélait leur existence au joueur dès le premier tour.
    """
    factions = session.exec(select(Faction).where(
        Faction.campaign_id == camp.id)).all()
    publiques = [f for f in factions
                 if pack is None or not pack.est_secrete(f.lore_ref)]
    if not publiques:
        return ""
    return "### FACTIONS CONNUES\n" + "\n".join(
        f"- {f.nom} — {', '.join(f.objectifs) or 'objectifs inconnus'}"
        for f in publiques[:5])


def _s_liens(session: Session, camp: Campaign, pj: Character,
             rs: Ruleset) -> str:
    """Ce qui pèse entre le joueur et les autres, et qui demande à arriver.

    Uniquement les paliers FRANCHIS récemment : une relation stable ne doit
    pas faire surgir la même scène à chaque tour. Voir liens.pressions.
    """
    from app.engine.liens import pressions

    consignes = pressions(session, camp, pj, rs)
    if not consignes:
        return ""
    return ("### CE QUI PÈSE ENTRE VOUS\n"
            + "\n".join(f"- {c}" for c in consignes)
            + "\nFais-en quelque chose dans cette scène ou dans la suivante, "
              "sans jamais l'annoncer : on ne dit pas à un joueur qu'un "
              "personnage a changé d'avis, on le lui montre.")


def _s_secrets(session: Session, camp: Campaign, rs: Ruleset) -> str:
    """Les questions ouvertes qui demandent à revenir, ou qui se sont refermées.

    LE FILTRE DE DIVULGATION TIENT ICI. Ne sort que la `question` d'un secret
    dont le joueur a DÉJÀ vu un palier — jamais la `verite`, jamais un secret
    intact, dont la question seule annoncerait qu'il y a quelque chose à
    trouver. Voir app/engine/secrets.py.
    """
    from app.engine.secrets import pressions

    consignes = pressions(session, camp, rs)
    if not consignes:
        return ""
    return ("### CE QUI RESTE SANS RÉPONSE\n"
            + "\n".join(f"- {c}" for c in consignes)
            + "\nTu ne SAIS PAS la réponse et tu ne l'inventes pas : ton rôle "
              "est de faire exister la question dans la scène.")


def _s_monde(session: Session, camp: Campaign) -> str:
    """Ce que le monde a fait pendant que le joueur regardait ailleurs.

    Sans cette section, la rumeur lue dans les conséquences d'un tour
    n'arrivait jamais jusqu'au narrateur : il ne pouvait ni y faire allusion,
    ni la faire remonter, et le monde retombait au décor qui attend.
    """
    from app.engine.monde import rumeurs_recentes

    faits = rumeurs_recentes(session, camp)
    if not faits:
        return ""
    return ("### CE QUI A BOUGÉ SANS LE JOUEUR\n"
            + "\n".join(f"- (tour {e.tour}) {e.resume}" for e in reversed(faits))
            + "\nTu peux y faire allusion — une remarque entendue, un "
              "attroupement, un PNJ pressé. N'annonce jamais cela comme un "
              "fait établi : le joueur n'était pas là.")


def _s_chronique(session: Session, camp: Campaign) -> str:
    # Les rappels de reprise partagent cette table sans être des chapitres :
    # les injecter ici ferait relire au narrateur son propre « Précédemment ».
    from app.engine.reprise import NIVEAU as NIVEAU_REPRISE

    resumes = session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau != NIVEAU_REPRISE)
        .order_by(Summary.au_tour.desc()).limit(3)).all()
    if not resumes:
        return ""
    return "### CHRONIQUE (résumé du passé)\n" + "\n".join(
        f"[tours {s.du_tour}-{s.au_tour}] {s.texte}" for s in reversed(resumes))


def _s_faits(session: Session, camp: Campaign, action: str) -> str:
    faits = rappeler_faits(session, camp, action)
    if not faits:
        return ""
    return "### FAITS ÉTABLIS (vrais, opposables)\n" + "\n".join(
        f"- (tour {f.tour}) {f.texte}" for f in faits)


def _s_infos(session: Session, camp: Campaign) -> str:
    infos = session.exec(select(Knowledge).where(
        Knowledge.campaign_id == camp.id, Knowledge.niveau >= 2,
        Knowledge.aspect != "identite").order_by(Knowledge.tour.desc())
        .limit(6)).all()
    if not infos:
        return ""
    return "### INFORMATIONS EN POSSESSION DU GROUPE\n" + "\n".join(
        f"- {i.contenu}" + ("" if i.fiable else " (non confirmé)") for i in infos)


def _s_tours(session: Session, camp: Campaign, limite: int) -> str:
    tours = session.exec(select(Turn).where(
        Turn.campaign_id == camp.id).order_by(Turn.index.desc())
        .limit(limite)).all()
    if not tours:
        return ""
    noms = {c.id: c.nom for c in session.exec(select(Character).where(
        Character.campaign_id == camp.id)).all()}
    bloc = []
    for t in reversed(tours):
        acteur = noms.get(t.character_id, "Le joueur")
        if t.action and not t.action.startswith("["):
            bloc.append(f"[{acteur}] {t.action}")
        bloc.append(f"[MJ] {t.narration}")
    return "### TOURS RÉCENTS\n" + "\n".join(bloc)


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------
def construire(session: Session, camp: Campaign, pj: Character, action: str,
               rs: Ruleset, pack: LorePack | None = None,
               role: str = "narrateur") -> str:
    """Contexte du tour, dimensionné pour le rôle demandé.

    `role` vaut narrateur, arbitre, consequences ou propositions. Un rôle
    inconnu reçoit le contexte complet — mieux vaut payer que mentir.
    """
    voulu = SECTIONS_PAR_ROLE.get(role, SECTIONS_PAR_ROLE["narrateur"])
    lieu = session.get(Location, pj.location_id) if pj.location_id else None

    parts: list[str] = [_s_entete(camp)]
    if "stats" in voulu:
        parts.append(_s_stats(rs))
    if "fiche" in voulu:
        parts.append(_s_fiche(session, camp, pj, rs, pack))
    elif "fiche_courte" in voulu:
        parts.append(_s_fiche(session, camp, pj, rs, pack, court=True))
    if "entites" in voulu:
        parts.append(_s_entites(session, camp))
    if "lieu" in voulu:
        parts.extend(_s_lieu(session, camp, pj, rs, lieu))
        parts.append(_s_equipe(session, camp, pj, lieu))
    # La rencontre passe AVANT les missions : quand les coups pleuvent, c'est
    # l'information la plus urgente du contexte.
    if "rencontre" in voulu:
        parts.append(_s_rencontre(session, camp, pj, rs))
    if "liens" in voulu:
        parts.append(_s_liens(session, camp, pj, rs))
    if "postures" in voulu:
        parts.append(_s_postures(session, camp, rs))
    if "missions" in voulu:
        parts.append(_s_missions(session, camp))
    # Les fils du récit : ce qu'on doit au joueur (voir engine/fils.py).
    if "fils" in voulu or "fils_liste" in voulu:
        from app.engine import fils
        parts.append(fils.consigne(session, camp) if "fils" in voulu
                     else fils.liste_numerotee(session, camp))
    if "factions" in voulu:
        parts.append(_s_factions(session, camp, pack))
    if "secrets" in voulu:
        parts.append(_s_secrets(session, camp, rs))
    if "monde" in voulu:
        parts.append(_s_monde(session, camp))
    if "chronique" in voulu:
        parts.append(_s_chronique(session, camp))
    if "faits" in voulu:
        parts.append(_s_faits(session, camp, action))
    if "infos" in voulu:
        parts.append(_s_infos(session, camp))
    if "tours" in voulu:
        parts.append(_s_tours(session, camp, settings.tours_recents))
    elif "dernier_tour" in voulu:
        parts.append(_s_tours(session, camp, 1))

    etat = "\n\n".join(p for p in parts if p)

    # Le lore prend ce qui reste, dans la limite de son propre plafond. L'état
    # de jeu ne peut donc jamais être évincé par du lore.
    besoin_lore = "lore" in voulu or "lore_reduit" in voulu
    if not besoin_lore or pack is None:
        return etat

    reste = settings.budget_contexte - taille_estimee(etat)
    plafond = min(settings.budget_lore, max(0, reste))
    if plafond < 120:
        return etat

    pnjs = [c for c in _presents(session, camp, pj, lieu) if not c.is_pc] if lieu else []
    lignee_ref, palier = _lignee_du_personnage(session, camp, pj, pack)
    lore = dossier_lore(
        pack,
        village_ref=pj.village_ref or "",
        epoque_id=camp.epoque,
        annee=pack.annee_de(camp.epoque),
        pj=pj, pnjs=pnjs, lieu=lieu,
        lignee_ref=lignee_ref, lignee_palier=palier,
        budget_tokens=plafond,
        complet="lore" in voulu,
    )
    if not lore:
        return etat
    # Le lore vient AVANT l'état : il est stable d'un tour à l'autre, donc le
    # placer en tête maximise la réutilisation du cache de préfixe du moteur.
    return f"{lore}\n\n{etat}"


def _techniques_de(session: Session, camp: Campaign, perso: Character,
                   pack: LorePack | None) -> list[str]:
    from app.models import CharacterTechnique

    liens = session.exec(select(CharacterTechnique).where(
        CharacterTechnique.character_id == perso.id)).all()
    out = []
    for ct in liens:
        t = pack.technique(ct.technique_ref) if pack else None
        nom = (t or {}).get("nom", ct.technique_ref)
        fr = (t or {}).get("fr")
        libelle = f"{nom} ({fr})" if fr else nom
        out.append(f"{libelle} [maîtrise {ct.maitrise}]")
    return out


def taille_estimee(contexte: str) -> int:
    """Estimation grossière en tokens. Sert à tenir le budget et à surveiller
    le coût réel d'un tour en console MJ."""
    return len(contexte) // 4
