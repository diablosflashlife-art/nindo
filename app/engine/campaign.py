"""Amorce de campagne : le monde et la distribution.

C'EST LE CŒUR DE L'EXPÉRIENCE. Le joueur ne doit pas écrire son sensei, ses
coéquipiers ni son rival — il les découvre. Ce module les fait naître à partir
du lore, du personnage créé et d'une table de détails distinctifs, puis les
fige définitivement.

Les secrets des PNJ sont écrits ici et n'entrent JAMAIS dans le contexte du
narrateur tant que les joueurs ne les ont pas découverts.
"""
from __future__ import annotations

import random

from sqlmodel import Session, select

from app.engine.garde import decanoniser, nettoyer_nom, nom_canon, pudeur
from app.llm.prompts import DISTRIBUTION, OUVERTURE
from app.llm.provider import get_llm
from app.llm.schemas import DISTRIBUTION as SCHEMA_DISTRIBUTION
from app.lore.pack import LorePack
from app.models import (Campaign, Character, Event, Faction, Knowledge, Location,
                        MemoryFact, Office, Quest, Relation, Secret, Turn)
from app.rules.engine import Ruleset

# Rôles générés à l'ouverture. Le joueur ne les écrit jamais.
ROLES = [
    ("sensei", "Le jônin instructeur de l'équipe. Plus âgé, compétent, marqué par la guerre."),
    ("coequipier", "Un coéquipier du même âge que le joueur, complémentaire de lui."),
    ("coequipier", "Un second coéquipier du même âge, différent du premier."),
    ("rival", "Un rival du même âge : même promotion, ambition concurrente, raison précise d'en vouloir au joueur ou de le jauger."),
]


# --------------------------------------------------------------------------
# 1. Le décor : lieux, factions, sièges
# --------------------------------------------------------------------------
def amorcer_monde(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                  village_id: str) -> None:
    """Instancie ce dont la campagne a besoin, et rien de plus.

    Instanciation paresseuse : on ne copie pas le lore en base au démarrage.
    Le reste du monde existe dans le pack et se cristallise à la rencontre.
    """
    village = pack.village(village_id) or {}
    vnom = village.get("nom_fr") or village.get("nom", "Village")

    # LES LIEUX DU VILLAGE, et pas ceux de Konoha. Cette liste était écrite ici
    # en dur : toute campagne, quel que soit le village, naissait avec un
    # « Terrain d'entraînement 3 » et une « Forêt frontalière » placés pour le
    # plan de Konoha. Ils vivent maintenant dans le lore (lieux.yaml), un jeu
    # par village, et leurs coordonnées suivent le plan de CE village.
    #
    # Le BROUILLARD. Un genin du premier jour connaît son village, pas la
    # frontière. Les lieux dangereux entrent sur la carte quand le bureau des
    # missions y envoie l'équipe — voir `carte.decouvrir`.
    for l in pack.lieux_de_depart(village_id):
        danger = int(l.get("danger", 1))
        session.add(Location(
            campaign_id=camp.id, nom=l["nom"], description=l.get("description", ""),
            danger=danger, region=vnom, village_ref=village_id,
            terrain=dict(l.get("terrain") or {}), tags=list(l.get("tags") or []),
            x=int(l.get("x", 50)), y=int(l.get("y", 50)), connu=danger <= 3))

    for org in pack.liste("organisations"):
        if org.get("village") not in (village_id, None):
            continue
        session.add(Faction(
            campaign_id=camp.id, lore_ref=org["id"], nom=org["nom"],
            objectifs=list(org.get("objectifs", [])),
            puissance=int(org.get("puissance", 50)), village_ref=village_id))

    # Sièges de grade, créés Y COMPRIS VACANTS : c'est ce NULL qui rend les
    # arcs de succession possibles.
    for g in rs.grades:
        if g.get("nature") != "poste":
            continue
        for n in range(1, int(g.get("places_par_village", 1)) + 1):
            session.add(Office(campaign_id=camp.id, village_ref=village_id,
                               grade=g["id"], siege_no=n, statut="vacant"))
    session.commit()


# --------------------------------------------------------------------------
# 2. La distribution : générée, jamais écrite par le joueur
# --------------------------------------------------------------------------
def generer_distribution(session: Session, camp: Campaign, pack: LorePack,
                         rs: Ruleset, pj: Character,
                         coequipiers: int = 2) -> list[Character]:
    """L'entourage. Une équipe genin compte trois élèves : avec deux joueurs,
    il ne reste qu'une place de coéquipier à remplir ; avec trois, aucune."""
    rng = random.Random(camp.graine + 31)
    places = max(0, min(2, coequipiers))
    roles, vus = [], 0
    for role, desc in ROLES:
        if role == "coequipier":
            vus += 1
            if vus > places:
                continue
        roles.append((role, desc))
    village = pack.village(pj.village_ref) or {}
    sel = pack.sel or ["porte un objet sans valeur apparente"]

    # Le germe : culture du village, contexte du joueur, un détail distinctif
    # imposé par rôle. Sans ce détail, un modèle produit des figurants lisses.
    details = rng.sample(sel, min(len(roles), len(sel)))
    lignes = [
        f"### VILLAGE\n{village.get('nom_fr', village.get('nom'))}",
        f"Culture : {village.get('culture', '').strip()}",
        f"Manière de parler locale : {village.get('parler', '')}",
        f"\n### PERSONNAGE JOUEUR\n{pj.nom}, {pj.age} ans, {pj.sexe or 'non précisé'}, "
        f"clan {pj.clan}, grade {pj.grade}.",
        f"Apparence : {pj.apparence or 'non précisée'}",
        f"\n### ÉPOQUE\n{camp.epoque} — ton de campagne : {camp.ton}",
        f"\n### PERSONNAGES À CRÉER (dans cet ordre, exactement {len(roles)})",
    ]
    for i, (role, desc) in enumerate(roles):
        lignes.append(f"{i+1}. [{role}] {desc}")
        lignes.append(f"   Détail distinctif imposé : {details[i]}")

    brut = get_llm().json(DISTRIBUTION, "\n".join(lignes), SCHEMA_DISTRIBUTION, rapide=False)

    lieu = session.get(Location, pj.location_id) if pj.location_id else None
    crees: list[Character] = []
    for i, (role, _) in enumerate(roles):
        data = (brut.get("personnages") or [{}] * len(roles))[i] if i < len(
            brut.get("personnages") or []) else {}
        nom = nettoyer_nom(data.get("nom") or "") or f"Inconnu {i+1}"
        if nom_canon(nom):
            nom = decanoniser(nom, rng, pack.prenoms_adversaire)
        # Un doublon de nom casserait la résolution des personnages cités
        if any(c.nom.lower() == nom.lower() for c in crees) or nom.lower() == pj.nom.lower():
            nom = f"{nom} ({role})"

        age = int(data.get("age") or (32 if role == "sensei" else (pj.age or 13)))
        if role != "sensei":
            # « du même âge que le joueur » : un modèle qui en fait un adulte
            # changerait le sens de toute la description.
            age = max(10, min(age, 16))
        grade = "jonin" if role == "sensei" else "genin"
        niveau = 12 if role == "sensei" else rng.randint(1, 3)

        stats = rs.stats_defaut()
        if role == "sensei":
            for k in ("ninjutsu", "intelligence", "perception", "vitesse"):
                stats[k] += rng.randint(10, 16)
        elif role == "rival":
            for k in rng.sample(list(stats), 3):
                stats[k] += rng.randint(2, 5)
        else:
            for k in rng.sample(list(stats), 2):
                stats[k] += rng.randint(1, 4)

        pnj = Character(
            campaign_id=camp.id, nom=nom, is_pc=False,
            sexe=data.get("sexe", ""), age=age,
            apparence=pudeur((data.get("apparence") or "").strip(), age),
            origine=(data.get("histoire") or "").strip(),
            clan="Sans clan", village=pj.village, village_ref=pj.village_ref,
            personnalite=pudeur((data.get("personnalite") or "").strip(), age),
            parler=(data.get("parler") or "").strip(),
            objectifs=list(data.get("objectifs") or []),
            grade=grade, niveau=niveau, stats=stats,
            ressources=rs.ressources_defaut(),
            location_id=lieu.id if lieu else None,
            source="genere", role_campagne=role,
            notes=f"[détail imposé] {details[i]}",
        )
        session.add(pnj)
        session.commit()
        session.refresh(pnj)

        res = rs.puissance(stats, [], niveau)
        pnj.pe, pnj.tier = res["pe"], res["tier"]
        session.add(pnj)

        # --- relation dans les deux sens : une relation est orientée
        valeur = int(data.get("relation_valeur") or 0)
        valeur = max(-40, min(40, valeur))
        nature = (data.get("relation_nature") or role).strip()
        session.add(Relation(campaign_id=camp.id, source_id=pnj.id, cible_id=pj.id,
                             nature=nature, valeur=valeur, note="Relation initiale."))
        session.add(Relation(campaign_id=camp.id, source_id=pj.id, cible_id=pnj.id,
                             nature=nature, valeur=max(0, valeur // 2),
                             note="Relation initiale."))

        # --- le secret : vrai dès maintenant, jamais exposé au narrateur
        secret = (data.get("secret") or "").strip()
        if secret:
            session.add(Secret(
                campaign_id=camp.id,
                question=f"Que cache {nom} ?",
                verite=secret, sujet_id=pnj.id,
                indices=[
                    {"palier": 1, "texte": f"{nom} évite un sujet précis."},
                    {"palier": 2, "texte": f"Quelqu'un fait allusion au passé de {nom}."},
                    {"palier": 3, "texte": f"Une preuve matérielle apparaît."},
                ]))

        # --- ce que le joueur sait d'emblée : l'identité, rien d'autre
        session.add(Knowledge(
            campaign_id=camp.id, groupe=True, sujet_type="character", sujet_id=pnj.id,
            aspect="identite", niveau=2,
            contenu=f"{nom} est {'ton instructeur' if role == 'sensei' else 'de ta promotion'}.",
            source="presentation", tour=0))

        crees.append(pnj)

    session.commit()
    return crees


# --------------------------------------------------------------------------
# 3. La première mission et la scène d'ouverture
# --------------------------------------------------------------------------
def amorcer_recit(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                  pj: Character, distribution: list[Character],
                  autres: list[Character] | None = None) -> Turn:
    sensei = next((c for c in distribution if c.role_campagne == "sensei"), None)
    lieu = session.get(Location, pj.location_id) if pj.location_id else None

    quete = Quest(
        campaign_id=camp.id,
        titre="Premier rapport d'équipe",
        rang="D",
        # Un ordre de l'instructeur, pas une offre : c'est l'arc d'ouverture.
        # Il monte au tour 9 et se dénoue au tour 13 (voir engine/fils.py).
        statut="acceptée",
        debut_tour=1,
        donneur_id=sensei.id if sensei else None,
        description="Votre instructeur veut évaluer l'équipe avant de lui confier "
                    "quoi que ce soit de sérieux.",
        enjeu="Une équipe jugée inapte est dissoute, et ses membres reversés ailleurs.",
        echeance_tour=16,
    )
    session.add(quete)

    lignes = [
        f"### LIEU\n{lieu.nom if lieu else 'le village'} — {lieu.description if lieu else ''}",
        ("\n### PERSONNAGES JOUEURS (chacun joué par une personne réelle)\n"
         + "\n".join(f"- {p.nom}, {p.age} ans, {p.clan}, {p.grade}"
                     + (f" — joué par {p.joueur}" if p.joueur else "")
                     for p in [pj] + list(autres or [])))
        if autres else
        f"\n### PERSONNAGE JOUEUR\n{pj.nom}, {pj.age} ans, {pj.clan}, {pj.grade}.",
        "\n### PERSONNAGES PRÉSENTS",
    ]
    for c in distribution:
        lignes.append(f"- {c.nom} ({c.role_campagne}) — {c.personnalite}")
        lignes.append(f"  Parle ainsi : {c.parler}")
    lignes.append(f"\n### SITUATION\nPremière réunion de l'équipe. "
                  f"{sensei.nom if sensei else 'L’instructeur'} doit les évaluer.")
    lignes.append(f"Ton : {camp.ton}")

    narration = get_llm().text(OUVERTURE, "\n".join(lignes), temperature=0.9)

    camp.tour = 1
    camp.phase = "en_cours"
    camp.resume_ouverture = narration[:400]
    tour = Turn(campaign_id=camp.id, index=1, character_id=pj.id,
                action="[Ouverture de la campagne]", narration=narration,
                resolution={"ouverture": True})
    session.add(tour)
    session.add(Event(campaign_id=camp.id, tour=1, type="narratif",
                      resume="La campagne commence : première réunion de l'équipe.",
                      importance=4, entites=[pj.id]))
    session.add(MemoryFact(
        campaign_id=camp.id, tour=1, importance=4,
        texte=f"{', '.join(p.nom for p in [pj] + list(autres or []))} "
              f"{'ont' if autres else 'a'} rejoint l'équipe de "
              f"{sensei.nom if sensei else 'son instructeur'} au premier jour.",
        entites=[pj.id] + [p.id for p in (autres or [])] + [c.id for c in distribution]))
    session.add(camp)
    session.commit()
    session.refresh(tour)
    return tour
