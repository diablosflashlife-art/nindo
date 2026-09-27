"""Générateur de missions.

Le problème
-----------
La campagne contenait exactement UNE quête, écrite en dur dans `campaign.py` :
« Premier rapport d'équipe ». Rien n'en produisait d'autre. Passé le tour dix,
il n'y avait plus d'objectif, et le joueur errait dans un village sans travail.

Pourquoi ne pas simplement demander une mission au modèle
---------------------------------------------------------
Parce qu'un modèle à qui l'on demande « invente une mission » produit trois fois
la même : escorter un marchand, livrer un message, chasser un bandit. Et il
invente des lieux et des personnages qui n'existent pas, que le narrateur
reprendra ensuite comme s'ils étaient canon.

La méthode
----------
Le MOTEUR tire l'ossature depuis le lore — un archétype, un commanditaire, un
lieu réel de la campagne, un objet de mission réel (une bête, une plante, un
minerai, une organisation, un déserteur du bingo book), une complication, et
une fois sur trois un revers. Le MODÈLE ne fait plus qu'habiller cette ossature.

Deux gains : la variété est garantie par la combinatoire, pas espérée du modèle ;
et aucune mission ne peut citer un lieu ou une faction qui n'existe pas.

La complication n'entre PAS dans la description
-----------------------------------------------
Le commanditaire ne la connaît pas — elle se découvre sur place. Elle est
stockée dans les notes de la mission, à l'usage du narrateur, et le prompt
interdit de l'écrire dans le texte remis au joueur.
"""
from __future__ import annotations

import random

from sqlmodel import Session, select

from app.llm.prompts import MISSION as PROMPT_MISSION
from app.llm.provider import get_llm
from app.llm.schemas import MISSION as SCHEMA_MISSION
from app.lore.pack import LorePack
from app.engine import francais as fr
from app.models import Campaign, Character, Event, Location, MemoryFact, Quest
from app.rules.engine import Ruleset

# Statuts d'une mission encore ouverte, donc encore soumise à son échéance.
EN_COURS = ("proposée", "acceptée", "en cours")

# Tours sans nouvelle offre après une offre expirée.
PAUSE_APRES_EXPIRATION = 5

# Rangs accessibles selon le grade, lus depuis les droits du ruleset.
ORDRE_RANGS = ["D", "C", "B", "A", "S"]


def ouvertes(session: Session, camp: Campaign) -> list[Quest]:
    return list(session.exec(select(Quest).where(
        Quest.campaign_id == camp.id, Quest.statut.in_(EN_COURS))).all())


def reste(quete: Quest, tour: int) -> int | None:
    """Combien de tours il reste. `None` quand la mission n'a pas d'échéance."""
    if quete.echeance_tour is None:
        return None
    return quete.echeance_tour - tour


def verifier_echeances(session: Session, camp: Campaign) -> list[str]:
    """Fait tomber les missions dont le délai est passé.

    POURQUOI C'EST IMPORTANT. `echeance_tour` était posé à la création de
    chaque mission — l'amorce promet même qu'« une équipe jugée inapte est
    dissoute » sous douze tours — et RIEN ne le comparait jamais à l'horloge de
    campagne. Le jeu faisait une promesse et ne la tenait pas.

    Une échéance qui ne tombe jamais est pire qu'une absence d'échéance : elle
    apprend au joueur que rien de ce qu'on lui annonce n'a de conséquence, et
    c'est exactement ce qui tue la crédibilité d'un maître du jeu.
    """
    effets: list[str] = []
    for q in ouvertes(session, camp):
        if q.echeance_tour is None or camp.tour <= q.echeance_tour:
            continue
        # Une offre que le joueur n'a jamais prise n'est pas un échec : le
        # bureau l'a confiée à une autre équipe. Mesuré sur une partie de 50
        # tours : huit offres ignorées devenaient huit « échecs », et
        # l'épilogue concluait « tu as échoué, à chaque fois ».
        if q.statut == "proposée":
            q.statut = "expirée"
            session.add(q)
            session.add(Event(
                campaign_id=camp.id, tour=camp.tour, type="mission",
                resume=f"« {q.titre} » a été confiée à une autre équipe.",
                importance=1))
            continue
        q.statut = "échouée"
        session.add(q)
        effets.append(f"Mission « {q.titre} » : délai dépassé — échouée.")
        session.add(Event(
            campaign_id=camp.id, tour=camp.tour, type="mission",
            resume=f"Le délai de « {q.titre} » est passé sans résultat.",
            importance=4))
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, importance=4, nature="fait",
            texte=f"La mission « {q.titre} » a échoué faute d'avoir été menée "
                  f"dans le délai fixé (tour {q.echeance_tour})."))
    return effets


# Ce que rapporte une mission réussie. Sans récompense visible, réussir et
# laisser tomber se ressemblaient : le joueur n'avait aucune raison de suivre
# le bureau plutôt que le premier mystère venu.
XP_PAR_RANG = {"D": 40, "C": 80, "B": 150, "A": 250, "S": 400}
CONFIANCE_DONNEUR = 10


# Une mission terminée ne revient pas. Mesuré en partie réelle : le récit
# « ressuscitait » une mission échouée, puis la faisait échouer une seconde fois.
TERMINES = ("réussie", "échouée", "refusée", "expirée")
# Ce que le RÉCIT a le droit de décider, selon l'état de la mission. Une offre
# qu'on n'a pas prise ne peut ni réussir ni échouer : elle se prend, ou elle
# expire. Les boutons du joueur, eux, passent par `par_le_joueur=True`.
TRANSITIONS_DU_RECIT = {
    "proposée": ("acceptée", "en cours"),
    "acceptée": ("en cours", "réussie", "échouée"),
    "en cours": ("réussie", "échouée"),
}
# Le temps qu'on laisse à une mission acceptée : de quoi monter et se dénouer.
DELAI_MINIMAL_ACCEPTEE = 15


def changer_statut(session: Session, camp: Campaign, pj: Character, rs: Ruleset,
                   quete: Quest, statut: str, *, par_le_joueur: bool = False) -> list[str]:
    """Le seul chemin pour changer le statut d'une mission — bouton du joueur
    ou constat du récit. Il date l'engagement (l'horloge de l'arc, voir
    engine/fils.py) et verse la récompense une fois, et une seule."""
    if not statut or quete.statut == statut or quete.statut in TERMINES:
        return []
    if not par_le_joueur and statut not in TRANSITIONS_DU_RECIT.get(quete.statut, ()):
        return []
    avant = quete.statut
    quete.statut = statut
    if statut in ("acceptée", "en cours") and avant == "proposée":
        # Accepter une mission à deux tours de son échéance la condamnait :
        # mesuré en partie réelle, acceptée au tour 19, échouée au tour 22.
        quete.echeance_tour = max(quete.echeance_tour or 0,
                                  camp.tour + DELAI_MINIMAL_ACCEPTEE)
    effets = [f"Mission « {quete.titre} » : {avant} → {statut}"]
    if statut in ("acceptée", "en cours") and quete.debut_tour is None:
        quete.debut_tour = camp.tour
    if statut == "échouée" and not quete.note:
        effets += debriefer(session, camp, rs, quete, pj)
    if statut == "réussie" and not quete.recompense_donnee:
        quete.recompense_donnee = True
        from app.engine import progression
        xp = XP_PAR_RANG.get(quete.rang, 40)
        effets.append(f"Mission réussie ! {progression.gagner_xp(session, camp, rs, pj, xp)}")
        effets += debriefer(session, camp, rs, quete, pj)
        if quete.donneur_id:
            from app.models import Relation
            rel = session.exec(select(Relation).where(
                Relation.campaign_id == camp.id, Relation.source_id == quete.donneur_id,
                Relation.cible_id == pj.id)).first()
            if rel is not None:
                rel.valeur = min(100, rel.valeur + CONFIANCE_DONNEUR)
                session.add(rel)
                donneur = session.get(Character, quete.donneur_id)
                if donneur:
                    effets.append(f"{donneur.nom} te fait davantage confiance (+{CONFIANCE_DONNEUR}).")
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, nature="fait", importance=4,
            texte=f"L'équipe de {pj.nom} a mené à bien la mission « {quete.titre} » "
                  f"(rang {quete.rang}) au tour {camp.tour}."))
    session.add(Event(campaign_id=camp.id, tour=camp.tour, type="mission",
                      resume=f"« {quete.titre} » : {statut}.",
                      importance=4 if statut in ("réussie", "échouée") else 2))
    session.add(quete)
    return effets


# ==========================================================================
# LA MISSION EN ACTES, ET LE DÉBRIEF — chantier C de Nindō 2.0
# ==========================================================================
# Trois actes, mesurés en tours depuis l'engagement : l'approche (on part, on
# se prépare, on arrive), la complication (ce que le commanditaire ignorait
# se révèle), le dénouement (voir engine/fils.py pour la montée et la clôture).
ACTES = [
    {"numero": 1, "nom": "Approche", "depuis": 0,
     "consigne": "L'équipe part, se prépare, arrive sur place. Pose le décor de "
                 "la mission et ce qu'on y trouve d'abord — pas encore la surprise."},
    {"numero": 2, "nom": "Complication", "depuis": 4,
     "consigne": "CE QUE LE COMMANDITAIRE IGNORAIT SE RÉVÈLE MAINTENANT (si ce "
                 "n'est pas déjà fait) : {complication}. Fais-le découvrir par "
                 "l'équipe, dans la scène, sans le nommer comme une règle."},
    {"numero": 3, "nom": "Dénouement", "depuis": 8,
     "consigne": "La mission touche à sa fin : fais converger vers ce qui la "
                 "conclura, succès ou échec."},
]
NOTES = ["S", "A", "B", "C", "D"]


def acte(quete: Quest, tour: int) -> dict | None:
    """L'acte courant d'une mission engagée, avec son nom et sa consigne."""
    if quete.statut not in ("acceptée", "en cours") or quete.debut_tour is None:
        return None
    age = tour - quete.debut_tour
    courant = ACTES[0]
    for a in ACTES:
        if age >= a["depuis"]:
            courant = a
    return {**courant, "age": age,
            "consigne": courant["consigne"].format(
                complication=quete.complication or "un imprévu que personne n'avait annoncé")}


def noter(session: Session, camp: Campaign, quete: Quest, statut: str) -> str:
    """La note du débrief, de S à D. Déterministe et lisible : vite fait,
    personne à terre, tout le monde debout — c'est ce que le bureau regarde."""
    if statut != "réussie":
        return "D"
    from app.models import Condition
    rang = NOTES.index("B")
    debut = quete.debut_tour if quete.debut_tour is not None else camp.tour
    fenetre = max(1, (quete.echeance_tour or camp.tour) - debut)
    if camp.tour - debut <= fenetre / 2:
        rang -= 1                                    # vite fait
    equipe = session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.is_pc == True)).all()  # noqa: E712
    tombes = session.exec(select(Condition).where(
        Condition.campaign_id == camp.id, Condition.code == "hors_combat",
        Condition.depuis_tour >= debut)).all()
    if tombes and any(c.character_id in {p.id for p in equipe} for c in tombes):
        rang += 1                                    # quelqu'un est tombé
    elif all(int(p.ressources.get("pv", 0)) * 100
             >= 60 * int(p.ressources.get("pv_max") or 40) for p in equipe):
        rang -= 1                                    # tout le monde debout
    return NOTES[max(0, min(len(NOTES) - 1, rang))]


def debriefer(session: Session, camp: Campaign, rs: Ruleset, quete: Quest,
              pj: Character) -> list[str]:
    """Le rapport au bureau : la note, la paie de l'équipe, la réputation.

    Sans lui, réussir vite et proprement ou de justesse se valaient. Le
    joueur voit maintenant ce que le village retient de sa mission.
    """
    statut = quete.statut
    note = noter(session, camp, quete, statut)
    cfg = rs.data.get("debrief", {}) or {}
    paie = float((cfg.get("paie_par_note") or {}).get(note, 1.0))
    reput = int((cfg.get("reputation_par_note") or {}).get(note, 0))
    base = int((rs.data.get("rangs_mission", {}).get(quete.rang) or {}).get("ryo", 300))
    ryo = int(round(base * paie / 10) * 10) if statut == "réussie" else 0

    equipe = session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.is_pc == True)).all()  # noqa: E712
    for p in equipe:
        p.ryo = int(p.ryo or 0) + ryo
        p.reputation = int(p.reputation or 0) + reput
        session.add(p)

    quete.note, quete.ryo = note, ryo
    quete.rapport = (f"Rapport au bureau des missions — note {note}. "
                     + (f"{ryo} ryô versés à chaque membre de l'équipe. " if ryo else "Aucune paie. ")
                     + f"Réputation {reput:+d}.")
    session.add(quete)
    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, nature="fait", importance=3,
        texte=f"Débrief de « {quete.titre} » : note {note}, réputation {reput:+d}."))
    effets = [f"Débrief : note {note}"]
    if ryo:
        effets.append(f"ryô +{ryo} (chacun)")
    effets.append(f"réputation {reput:+d}")
    return effets


def rangs_autorises(rs: Ruleset, grade: str) -> list[str]:
    """Un genin ne reçoit pas de mission de rang A. Le ruleset le dit déjà dans
    `droits.missions_max` — il suffisait de le lire."""
    plafond = (rs.droits(grade) or {}).get("missions_max", "D")
    if plafond not in ORDRE_RANGS:
        plafond = "D"
    return ORDRE_RANGS[: ORDRE_RANGS.index(plafond) + 1]


def _poids_rang(rang: str) -> float:
    """Les missions faciles sont les plus fréquentes — c'est le quotidien d'une
    équipe, et ça rend les missions difficiles remarquables."""
    return {"D": 100, "C": 55, "B": 22, "A": 7, "S": 1}.get(rang, 10)


def ossature(pack: LorePack, rs: Ruleset, camp: Campaign, pj: Character,
             lieux: list[Location], *, rang: str | None = None,
             graine: int | None = None) -> dict | None:
    """Tire l'ossature d'une mission. Aucun appel au modèle ici.

    Retourne None si le lore ne permet rien de cohérent — mieux vaut pas de
    mission qu'une mission qui parle d'un endroit inexistant.
    """
    rng = random.Random(graine if graine is not None
                        else camp.graine + camp.tour * 7919)

    dispo = rangs_autorises(rs, pj.grade)
    if rang and rang in dispo:
        rangs = [rang]
    else:
        rangs = dispo
    rang_choisi = rng.choices(rangs, weights=[_poids_rang(r) for r in rangs])[0]

    archetypes = pack.archetypes_mission(rang_choisi)
    if not archetypes:
        return None
    arch = rng.choice(archetypes)

    pays = pack.pays_du_village(pj.village_ref or "") or {}
    pays_ref = pays.get("id", "")

    # L'objet de la mission vient d'une collection réelle du lore.
    ancres = list(arch.get("ancre") or [])
    objet = None
    rng.shuffle(ancres)
    for cle in ancres:
        if cle == "bingo_book":
            cibles = pack.bingo_book(tier_max=max(2, pj.tier + 2))
        elif cle == "organisations":
            cibles = pack.organisations(pj.village_ref, inclure_secretes=False)
        elif cle == "sites_invocation":
            cibles = pack.sites_invocation()
        elif cle == "faune":
            cibles = pack.faune(pays_ref)
            # Une mission d'extermination ne se lance pas contre un animal
            # inoffensif : les cerfs sacrés des Nara ne sont pas des nuisibles.
            if arch.get("categorie") == "extermination":
                cibles = [c for c in cibles if int(c.get("danger", 0)) >= 2]
        else:
            cibles = {"flore": pack.flore,
                      "mineraux": pack.mineraux}.get(cle, lambda _p: [])(pays_ref)
        cibles = [c for c in cibles if c.get("accroche")]
        if cibles:
            choix = rng.choice(cibles)
            objet = {"source": cle, "ref": choix.get("id", ""),
                     "nom": choix.get("nom", ""),
                     "accroche": choix.get("accroche", ""),
                     "detail": (choix.get("comportement") or choix.get("effet")
                                or choix.get("motif") or choix.get("note") or "")}
            break

    commanditaires = [c for c in (arch.get("commanditaire") or ["village"])
                      if c in pack.commanditaires() or c == "village"]
    com_ref = rng.choice(commanditaires or ["village"])
    com = pack.commanditaires().get(com_ref, {})

    lieu = rng.choice(lieux) if lieux else None

    # Second générateur, décorrélé du premier : sans lui, deux missions du même
    # archétype tombaient souvent sur la même complication, parce que le tirage
    # se faisait au même rang dans la séquence aléatoire.
    rng2 = random.Random(((graine if graine is not None else camp.graine)
                          + camp.tour * 31 + sum(map(ord, arch["id"]))) * 7919)
    complication = rng2.choice(arch.get("complications") or [""]) or ""
    revers = rng2.choice(pack.revers()) if pack.revers() and rng2.random() < 0.34 else ""
    clause = arch.get("clause", "") if rng2.random() < 0.4 else ""

    duree = arch.get("duree_tours") or [5, 10]
    echeance = camp.tour + rng.randint(int(duree[0]), int(duree[-1]))

    return {
        "archetype": arch["id"],
        "categorie": arch.get("categorie", ""),
        "rang": rang_choisi,
        "objectif": arch.get("objectif", ""),
        "enjeu_type": arch.get("enjeu", ""),
        "commanditaire": com_ref,
        "commanditaire_label": com.get("label", com_ref),
        "commanditaire_note": com.get("note", ""),
        "lieu_id": lieu.id if lieu else None,
        "lieu_nom": lieu.nom if lieu else "",
        "objet": objet,
        "complication": complication,
        "revers": revers,
        "clause": clause,
        "echeance_tour": echeance,
    }


def _brief(pack: LorePack, camp: Campaign, pj: Character, oss: dict) -> str:
    village = pack.village(pj.village_ref or "") or {}
    lignes = [
        "### OSSATURE (non négociable)",
        f"Rang : {oss['rang']} — catégorie : {oss['categorie']}",
        f"Objectif : {oss['objectif']}",
        f"Type d'enjeu : {oss['enjeu_type']}",
        f"Commanditaire : {oss['commanditaire_label']} — {oss['commanditaire_note']}",
        # Le nom déterminé : le modèle recopie ce qu'on lui donne, et « part
        # pour Poste frontière du Nord » n'est pas une phrase française.
        f"Point de départ : "
        f"{fr.majuscule(fr.determine(oss['lieu_nom'])) or 'le village'}",
        f"\n### CONTEXTE\nVillage : {village.get('nom_fr', village.get('nom', ''))}",
        f"Manière de parler locale : {village.get('parler', '')}",
        f"Époque : {camp.epoque} — ton de campagne : {camp.ton}",
        f"Équipe : {pj.nom}, {pj.grade}.",
    ]
    if oss["objet"]:
        o = oss["objet"]
        lignes += [f"\n### OBJET DE LA MISSION\n{o['nom']}",
                   f"Ce qui la justifie : {o['accroche']}"]
        if o["detail"]:
            lignes.append(f"À savoir : {o['detail']}")
    lignes.append("\n### CE QUE LE COMMANDITAIRE IGNORE\n"
                  "Ne l'écris PAS dans la description — il ne le sait pas.\n"
                  f"{oss['complication']}")
    return "\n".join(lignes)


def generer(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
            pj: Character, *, rang: str | None = None,
            statut: str = "proposée") -> Quest | None:
    """Ossature tirée du lore, habillage par le modèle, écriture en base."""
    lieux = session.exec(select(Location).where(
        Location.campaign_id == camp.id)).all()
    oss = ossature(pack, rs, camp, pj, list(lieux), rang=rang)
    if oss is None:
        return None

    try:
        brut = get_llm().json(PROMPT_MISSION, _brief(pack, camp, pj, oss),
                              SCHEMA_MISSION, rapide=True)
    except Exception:  # noqa: BLE001 — une mission manquée ne casse pas un tour
        return None

    titre = (brut.get("titre") or "").strip()[:120]
    if not titre:
        return None
    # Un titre en double casserait la résolution des quêtes citées par le modèle
    existants = {q.titre.strip().lower() for q in session.exec(
        select(Quest).where(Quest.campaign_id == camp.id)).all()}
    if titre.lower() in existants:
        # La même mission reproposée (« Scorpion des dunes » trois fois dans la
        # partie de 50 tours) : on passe, le prochain tour tirera autre chose.
        return None

    donneur = None
    if oss["commanditaire"] in ("village", "conseil"):
        donneur = session.exec(select(Character).where(
            Character.campaign_id == camp.id,
            Character.role_campagne == "sensei")).first()

    quete = Quest(
        campaign_id=camp.id,
        titre=titre,
        rang=oss["rang"],
        statut=statut,
        donneur_id=donneur.id if donneur else None,
        description=(brut.get("description") or "").strip()[:1200],
        enjeu=(brut.get("enjeu") or oss["enjeu_type"]).strip()[:600],
        echeance_tour=oss["echeance_tour"],
        # Gardée pour l'acte 2 : c'est ce que le commanditaire ignorait.
        complication=" ".join(filter(None, [oss.get("complication", ""),
                                            oss.get("revers", "")]))[:400],
    )
    session.add(quete)

    # Une mission qui envoie quelque part fait découvrir l'endroit. C'est ce
    # qui donne à la carte une raison de grandir : le joueur n'apprend pas le
    # monde en le lisant, il l'apprend en y étant envoyé.
    if oss.get("lieu_id"):
        from app.engine.carte import decouvrir
        lieu = session.get(Location, oss["lieu_id"])
        if lieu is not None:
            decouvrir(session, camp, lieu,
                      f"le bureau des missions t'y envoie — « {titre} »")

    session.commit()
    session.refresh(quete)
    return quete


def proposer_si_vide(session: Session, camp: Campaign, pack: LorePack,
                     rs: Ruleset, pj: Character) -> Quest | None:
    """Garantit qu'il y a toujours quelque chose à faire.

    Appelé à la fin d'un tour : si aucune mission n'est ouverte, le bureau des
    missions en affiche une nouvelle. C'est ce qui évite les campagnes qui
    s'arrêtent faute d'objectif.
    """
    ouvertes = session.exec(select(Quest).where(
        Quest.campaign_id == camp.id,
        Quest.statut.in_(["proposée", "acceptée", "en cours"]))).all()
    if ouvertes:
        return None
    # Le bureau n'affiche pas une offre par tour : après une offre expirée,
    # il laisse respirer. Sinon le tableau se remplit d'offres que personne ne
    # prend, et le joueur apprend à ne plus le regarder.
    recente = session.exec(select(Event).where(
        Event.campaign_id == camp.id, Event.type == "mission",
        Event.tour > camp.tour - PAUSE_APRES_EXPIRATION)).first()
    if recente is not None and "autre équipe" in (recente.resume or ""):
        return None
    return generer(session, camp, pack, rs, pj)
