"""Le combat — une rencontre qui dure, des ressources qui descendent.

CE QUE CE MODULE CORRIGE. Avant lui, un affrontement était un jet de dé par
tour : rien ne baissait, aucun avantage ne se construisait, et la règle du
fossé — le cœur du genre — n'était branchée nulle part. Un genin et un jônin
se réglaient au même 1d20.

LES QUATRE IDÉES.

1. UNE RENCONTRE DURE. Elle s'ouvre, elle compte ses échanges, elle se ferme.
   Tant qu'elle est ouverte, chaque tour de jeu est un échange : le joueur
   déclare, le moteur oppose les jets, les PV et le chakra descendent, les
   blessures s'installent et pèsent sur la puissance.

2. LA POSTURE EST LE CHOIX. Six intentions, chacune un compromis chiffré dans
   le ruleset. Personne ne peut rester en offensive douze échanges : la
   défense s'effondre. C'est là que se trouve le jeu, pas dans le dé.

3. LES LEVIERS RÉDUISENT L'ÉCART, ILS NE DONNENT PAS DE BONUS. Un genin ne bat
   pas un nukenin de tier 4 en frappant mieux ; il le bat en préparant le
   terrain, en apprenant sa faiblesse, en acceptant d'y laisser quelque chose.
   Trois leviers ramènent « ce n'est plus un combat » à « difficile mais
   possible ». C'est la promesse du genre, rendue mécanique.

4. QUAND VAINCRE EST HORS DE PORTÉE, ON NE LANCE PAS LES DÉS POUR VAINCRE. Le
   moteur impose les objectifs réellement ouverts — fuir, retarder, protéger,
   obtenir une réponse — et compte les succès qui y mènent. Frapper un
   adversaire écrasant ne fait rien avancer, et le joueur l'apprend en une
   scène plutôt qu'en lisant une règle.

CE MODULE NE RACONTE RIEN. Il produit un bloc mécanique que le narrateur
habille, exactement comme le jet de dé d'un tour ordinaire. Aucune décision
chiffrée ne revient au modèle, y compris celles des adversaires : leur conduite
est écrite en Python, donc reproductible et déboguable.

AUCUN APPEL AU MODÈLE POUR FABRIQUER UN ADVERSAIRE. Les archétypes viennent de
`lore/naruto/adversaires.yaml` et du bestiaire ; une embuscade s'ouvre
instantanément. Un combat qui commence par trente secondes d'attente n'est pas
une embuscade.
"""
from __future__ import annotations

import random

from sqlmodel import Session, select

from app.models import (Campaign, Character, CharacterTechnique, Condition,
                        Encounter, Event, Location, MemoryFact, Relation)
from app.rules.engine import Ruleset

# Les postures qui ne cherchent pas à blesser. Elles ne font pas moins bien :
# elles font autre chose, et c'est ce qui rend un combat jouable au-delà du
# « je frappe encore ».
SANS_DEGATS = ("manoeuvre", "desengagement")

# Ce qu'une manœuvre peut établir, et avec quelle caractéristique. La clé est
# un levier du ruleset ; la valeur, la caractéristique qui l'obtient.
STAT_LEVIER = {
    "terrain_prepare": "intelligence",
    "renseignement": "perception",
    "contre_mesure": "intelligence",
    "embuscade": "vitesse",
    "adversaire_diminue": "perception",
    "nombre": "social",
    "enjeu_emotionnel": "social",
    "sacrifice": "endurance",
}

# Combien d'adversaires riposte dans un même échange. Au-delà, la narration
# devient une liste et le joueur ne suit plus rien.
RIPOSTES_MAX = 3

# Qui a quitté le combat. On le marque explicitement plutôt que de se fier au
# seul lieu : une rencontre sans lieu rendait « parti » indistinguable de
# « présent », et les adversaires restaient éternellement dans le combat.
MARQUE_RETRAIT = "a rompu le combat"


# ==========================================================================
# Lecture
# ==========================================================================
def active(session: Session, camp: Campaign) -> Encounter | None:
    return session.exec(select(Encounter).where(
        Encounter.campaign_id == camp.id,
        Encounter.statut == "en_cours").order_by(Encounter.id.desc())).first()


def adverses(session: Session, renc: Encounter) -> list[Character]:
    """Les adversaires encore dans le combat.

    Est hors du combat celui qui est tombé (PV à zéro), celui qui est mort, et
    celui qui a quitté le lieu — un adversaire qui rompt le contact s'en va
    vraiment, et pourra revenir.
    """
    out = []
    for cid in renc.camp_adverse:
        c = session.get(Character, cid)
        if c is None or not c.vivant:
            continue
        if int(c.ressources.get("pv", 0)) <= 0:
            continue
        if MARQUE_RETRAIT in (c.etats or []) or c.location_id != renc.location_id:
            continue
        out.append(c)
    return out


def retirer(session: Session, perso: Character) -> None:
    """Sort un combattant de la rencontre. Il n'est ni mort ni vaincu : il est
    parti, et il pourra revenir — ce qui vaut mieux pour la campagne."""
    perso.location_id = None
    if MARQUE_RETRAIT not in (perso.etats or []):
        perso.etats = list(perso.etats or []) + [MARQUE_RETRAIT]
    session.add(perso)


def allies(session: Session, renc: Encounter, pj: Character) -> list[Character]:
    """Les alliés qui se battent aux côtés du joueur, hors lui-même.

    C'était la pièce manquante la plus visible : trois brigands contre un genin
    seul est une exécution, contre une équipe de quatre c'est un combat. Le
    sensei et les coéquipiers sont là — les faire regarder était un contresens
    autant mécanique que narratif.
    """
    out = []
    for cid in renc.camp_joueur:
        if cid == pj.id:
            continue
        c = session.get(Character, cid)
        if c is None or not c.vivant:
            continue
        if int(c.ressources.get("pv", 0)) <= 0 or c.location_id != renc.location_id:
            continue
        out.append(c)
    return out


def _allies_presents(session: Session, camp: Campaign,
                     pj: Character) -> list[Character]:
    """Qui prêtera main-forte : les autres personnages joueurs présents, et les
    PNJ assez proches pour se mettre en travers."""
    if not pj.location_id:
        return []
    presents = session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.location_id == pj.location_id,
        Character.id != pj.id,
        Character.vivant == True)).all()  # noqa: E712
    out = []
    for c in presents:
        if c.role_campagne == "antagoniste":
            continue
        if c.is_pc:
            out.append(c)
            continue
        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id, Relation.source_id == c.id,
            Relation.cible_id == pj.id)).first()
        if c.role_campagne in ("sensei", "coequipier") and (
                rel is None or rel.valeur > -20):
            out.append(c)
        elif rel is not None and rel.valeur >= 20:
            out.append(c)
    return out


def tombes(session: Session, renc: Encounter) -> list[Character]:
    out = []
    for cid in renc.camp_adverse:
        c = session.get(Character, cid)
        if c is not None and int(c.ressources.get("pv", 0)) <= 0:
            out.append(c)
    return out


def fuis(session: Session, renc: Encounter) -> list[Character]:
    """Ceux qui ont rompu le contact : encore debout, mais partis.

    Ils comptent : mettre trois brigands en fuite est une victoire, et ne rien
    accorder pour ça apprenait au joueur qu'il fallait achever tout le monde.
    """
    out = []
    for cid in renc.camp_adverse:
        c = session.get(Character, cid)
        if c is None or int(c.ressources.get("pv", 0)) <= 0:
            continue
        if MARQUE_RETRAIT in (c.etats or []):
            out.append(c)
    return out


def pv_max(session: Session, rs: Ruleset, perso: Character) -> int:
    """Le maximum de référence pour les pourcentages de blessure.

    Il dépend du tier : un nukenin de tier 4 n'a pas les points de vie d'un
    genin, sinon il franchit tous ses seuils de blessure au premier échange.

    On le MÉMORISE dans les ressources, et monter d'un tier soigne la
    différence. Sans ça, un personnage qui progresse se retrouverait à trente
    points de vie sur un maximum de quarante-cinq : blessé en permanence pour
    avoir gagné un niveau.
    """
    actuel = int(perso.ressources.get("pv", 0) or 0)
    cible = max(int(rs.ressources_pour_tier(perso.tier).get("pv", 40)), actuel, 1)
    memorise = int(perso.ressources.get("pv_max") or 0)
    if cible > memorise:
        # Première mesure : le personnage est au complet. Le sensei de l'amorce
        # a été créé avec les ressources par défaut d'un genin ; le laisser à
        # quarante points sur un maximum de soixante le rendrait « entamé » dès
        # son premier combat, sans avoir reçu un seul coup.
        perso.ressources = {
            **perso.ressources, "pv_max": cible,
            "pv": cible if not memorise else actuel + (cible - memorise),
        }
        session.add(perso)
        return cible
    return max(1, memorise)


def _malus_blessures(session: Session, rs: Ruleset, perso: Character) -> int:
    codes = [c.code for c in session.exec(select(Condition).where(
        Condition.character_id == perso.id)).all()]
    return rs.malus_etats(codes)


def _lire_note(pnj: Character, cle: str) -> str:
    """Les consignes de conduite d'un adversaire, rangées dans ses notes.

    Les y mettre plutôt que dans une colonne dédiée a un avantage : elles
    restent lisibles par un humain qui ouvre la base, et un adversaire
    cristallisé plus tard les garde.
    """
    for ligne in (pnj.notes or "").splitlines():
        if ligne.startswith(f"[{cle}]"):
            return ligne.split("]", 1)[1].strip()
    return ""


# ==========================================================================
# Fabrication des adversaires — depuis le lore, sans modèle
# ==========================================================================
def _normaliser(arch: dict) -> dict:
    """Ramène un archétype d'adversaire ET une entrée de bestiaire au même
    format. Le moteur ne doit pas savoir s'il affronte un homme ou une bête."""
    est_bete = arch.get("_type") == "faune"
    return {
        "ref": arch.get("id", ""),
        "nom": arch.get("nom", "Inconnu"),
        "tier": int(arch.get("tier", 1)),
        "accents": list(arch.get("accents")
                        or (["endurance", "taijutsu"] if est_bete
                            else ["taijutsu", "vitesse"])),
        "groupe": list(arch.get("groupe") or ([1, 1] if est_bete else [1, 2])),
        "ia": arch.get("ia") or ("bete_enragee" if est_bete else "prudent"),
        "moral": arch.get("moral") or ("bete_enragee" if est_bete else ""),
        "comportement": arch.get("comportement", ""),
        "tactique": arch.get("tactique") or arch.get("comportement", ""),
        "armes": list(arch.get("armes") or []),
        "techniques": list(arch.get("techniques") or []),
        "butin": list(arch.get("butin") or arch.get("recolte") or []),
        "bete": est_bete,
    }


def _nommer(a: dict, pack, rng: random.Random, pris: set[str]) -> str:
    """Un adversaire nommé « Bandit 2 » n'est pas un adversaire.

    Les bêtes gardent leur nom d'espèce. Les humains reçoivent un prénom
    ordinaire et une épithète — ce que le joueur VOIT, c'est-à-dire exactement
    ce qu'on apprend d'un inconnu qui vous attaque.
    """
    if a["bete"]:
        base = a["nom"]
    else:
        prenoms = pack.prenoms_adversaire or ["Goro"]
        epithetes = pack.epithetes_adversaire or ["au bandeau rayé"]
        base = f"{rng.choice(prenoms)} {rng.choice(epithetes)}"
    nom, n = base, 2
    while nom.lower() in pris:
        nom = f"{base} ({n})"
        n += 1
    pris.add(nom.lower())
    return nom


def fabriquer(session: Session, camp: Campaign, pack, rs: Ruleset, arch: dict,
              lieu: Location | None, rng: random.Random, pris: set[str],
              cible_pj: Character | None = None) -> Character:
    """Un adversaire complet, instantanément, sans appel au modèle.

    Sa puissance n'est pas écrite à la main : `stats_pour_tier` monte les
    caractéristiques jusqu'à atteindre le tier demandé par le lore.
    Rééquilibrer la table des tiers rééquilibre donc tout le bestiaire du même
    geste — c'est la seule façon de garder un équilibre vrai après vingt
    retouches.
    """
    a = _normaliser(arch)
    stats = rs.stats_pour_tier(a["tier"], a["accents"])

    pnj = Character(
        campaign_id=camp.id, nom=_nommer(a, pack, rng, pris), is_pc=False,
        age=None if a["bete"] else rng.randint(17, 42),
        apparence=a["comportement"],
        clan="Sans clan", village="", village_ref="",
        personnalite=a["comportement"],
        parler="" if a["bete"] else "phrases brèves, ton dur",
        objectifs=[],
        grade="genin", niveau=rs.niveau_pour_tier(a["tier"]),
        stats=stats, ressources=rs.ressources_pour_tier(a["tier"]),
        inventaire=[(pack.get(r) or {}).get("nom", r) for r in a["armes"]],
        location_id=lieu.id if lieu else None,
        source="genere", lore_ref=a["ref"], role_campagne="antagoniste",
        notes=f"[tactique] {a['tactique']}\n[ia] {a['ia']}"
              + (f"\n[moral] {a['moral']}" if a["moral"] else ""),
    )
    session.add(pnj)
    session.commit()
    session.refresh(pnj)

    techniques = [{"rang": (pack.technique(t) or {}).get("rang", "E"),
                   "maitrise": 55} for t in a["techniques"]]
    res = rs.puissance(stats, techniques, pnj.niveau)
    pnj.pe, pnj.tier = res["pe"], res["tier"]
    session.add(pnj)

    # Hostile par construction : sans cette relation, la colonne de droite ne
    # dit pas au joueur à qui il a affaire.
    if cible_pj is not None:
        session.add(Relation(
            campaign_id=camp.id, source_id=pnj.id, cible_id=cible_pj.id,
            nature="hostilité", valeur=-60, note="Adversaire d'une rencontre."))
    session.commit()
    session.refresh(pnj)
    return pnj


def _milieu_de(lieu: Location | None) -> str:
    """Le terrain, traduit dans le vocabulaire des archétypes."""
    if lieu is None:
        return ""
    tags = [t.lower() for t in (lieu.tags or [])]
    nom = (lieu.nom or "").lower()
    for cle, mots in (
        ("frontiere", ("frontière", "frontiere", "poste", "militaire")),
        ("foret", ("forêt", "foret", "bois", "futaie", "exploration")),
        ("village", ("village", "quartier", "académie", "academie", "social")),
        ("montagne", ("montagne", "col", "mont")),
        ("route", ("route", "chemin", "pont")),
        ("ruines", ("ruine", "ruines", "temple")),
    ):
        if any(m in nom for m in mots) or any(m in tags for m in mots):
            return cle
    return ""


def choisir_archetypes(pack, rs: Ruleset, pj: Character,
                       lieu: Location | None, rng: random.Random,
                       tier_max: int | None = None) -> list[dict]:
    """Ce qui peut raisonnablement barrer la route ICI.

    Le plafond de tier est la seule protection du joueur contre une rencontre
    qui n'a aucun sens : par défaut un cran au-dessus de lui, et un lieu très
    dangereux en ajoute un. Une forêt frontalière est un endroit où l'on croise
    plus fort que soi — c'est écrit dans son danger, et le joueur peut le lire
    avant d'y aller.
    """
    plafond = tier_max if tier_max is not None else pj.tier + 1
    if lieu is not None and lieu.danger >= 6:
        plafond += 1

    pays = (pack.pays_du_village(pj.village_ref) or {}).get("id") \
        if pj.village_ref else None
    milieu = _milieu_de(lieu)

    candidats = pack.adversaires(tier_max=plafond, milieu=milieu, pays=pays)
    if not candidats:
        candidats = pack.adversaires(tier_max=plafond, pays=pays)
    if lieu is not None:
        candidats = candidats + [
            b for b in pack.faune(pays, danger_max=lieu.danger + 1)
            if int(b.get("tier", 1)) <= plafond]
    if not candidats:
        candidats = pack.adversaires(tier_max=max(1, plafond))
    if not candidats:
        return []

    a = _normaliser(rng.choice(candidats))
    arch = next(x for x in candidats if x.get("id") == a["ref"])
    bas, haut = (a["groupe"] + a["groupe"])[:2]
    return [arch] * max(1, rng.randint(int(bas), max(int(bas), int(haut))))


# ==========================================================================
# Ouverture d'une rencontre
# ==========================================================================
def _titre(archetypes: list[dict], ennemis: list[Character],
           lieu: Location | None, declencheur: str) -> str:
    if archetypes:
        quoi = _normaliser(archetypes[0])["nom"]
        nombre = f" ×{len(archetypes)}" if len(archetypes) > 1 else ""
    else:
        quoi = ennemis[0].nom if ennemis else "Inconnu"
        nombre = f" ×{len(ennemis)}" if len(ennemis) > 1 else ""
    ou = f" — {lieu.nom}" if lieu else ""
    prefixe = {"embuscade": "Embuscade", "engagement": "Affrontement",
               "patrouille": "Interception"}.get(declencheur, "Rencontre")
    return f"{prefixe} : {quoi}{nombre}{ou}"


def ouvrir(session: Session, camp: Campaign, pack, rs: Ruleset, pj: Character,
           *, declencheur: str = "engagement",
           archetypes: list[dict] | None = None,
           adversaires_existants: list[Character] | None = None,
           surprise: bool = False, titre: str = "") -> Encounter | None:
    """Ouvre une rencontre. Idempotent : s'il en existe déjà une, on la rend.

    Deux sources d'adversaires, et elles ne se mélangent pas : des PNJ qui
    existent déjà — le rival qui passe aux mains, la patrouille croisée hier —
    ou des archétypes fabriqués pour l'occasion. La première a toujours plus de
    valeur : elle donne un passé au combat.
    """
    deja = active(session, camp)
    if deja is not None:
        return deja

    lieu = session.get(Location, pj.location_id) if pj.location_id else None
    rng = random.Random(camp.graine + camp.tour * 977)

    ennemis = list(adversaires_existants or [])
    if not ennemis:
        archetypes = archetypes or choisir_archetypes(pack, rs, pj, lieu, rng)
        if not archetypes:
            return None
        pris = {c.nom.lower() for c in session.exec(select(Character).where(
            Character.campaign_id == camp.id)).all()}
        ennemis = [fabriquer(session, camp, pack, rs, a, lieu, rng, pris, pj)
                   for a in archetypes]

    for c in ennemis:
        pv_max(session, rs, c)
    # UNE BANDE N'EST PAS UNE ARMÉE DE HÉROS. Des sbires fabriqués pour
    # l'occasion, pas plus forts que le joueur, encaissent moins qu'un
    # duelliste. Mesuré en simulation : deux détrousseurs tenaient dix échanges
    # contre un genin seul, et un combat sur quatre finissait sans vainqueur.
    if not adversaires_existants and len(ennemis) >= 2:
        facteur = float((rs.combat.get("sbires") or {}).get("pv_facteur", 0.45))
        for c in ennemis:
            if c.tier <= pj.tier:
                pv = max(1, int(int(c.ressources.get("pv_max", c.ressources.get("pv", 1))) * facteur))
                c.ressources = {**c.ressources, "pv": pv, "pv_max": pv}
                session.add(c)

    # `surprise` veut dire « le joueur a été pris de court », jamais l'inverse :
    # une embuscade subie ne donne évidemment aucun avantage à celui qui la
    # subit. Elle coûte un malus au premier échange (voir `echanger`). Pour
    # frapper le premier, le joueur passe par une manœuvre `embuscade`.
    soutiens = _allies_presents(session, camp, pj)
    for c in soutiens:
        pv_max(session, rs, c)
    # LE FOSSÉ SE MESURE SUR LE PERSONNAGE QUI JOUE, pas sur son camp. Prendre
    # le tier du sensei aurait rendu la règle inopérante : un genin accompagné
    # d'un jônin n'aurait plus jamais rencontré d'adversaire hors de portée, et
    # la scène la plus mémorable du genre — tenir le temps que l'adulte arrive —
    # deviendrait injouable. Les alliés comptent, mais comme LEVIER.
    leviers = _levier_du_nombre(soutiens, ennemis)
    tier_adverse = max((c.tier for c in ennemis), default=1)
    fosse = rs.fosse_effectif(pj.tier, tier_adverse, leviers)

    renc = Encounter(
        campaign_id=camp.id, location_id=lieu.id if lieu else None,
        titre=titre or _titre(archetypes or [], ennemis, lieu, declencheur),
        declencheur=declencheur, surprise=surprise,
        tour_debut=camp.tour, echange=0,
        camp_joueur=[pj.id] + [c.id for c in soutiens],
        camp_adverse=[c.id for c in ennemis],
        leviers=leviers, fosse=fosse,
        objectifs=list(fosse.get("objectifs") or []),
        journal=[{"echange": 0,
                  "texte": f"La rencontre s'ouvre — {len(ennemis)} adversaire(s) "
                           f"contre {1 + len(soutiens)}, écart de tier "
                           f"{fosse.get('ecart', 0)}."}])
    session.add(renc)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="combat", resume=renc.titre,
        importance=4, entites=[pj.id] + [c.id for c in ennemis]))
    session.commit()
    session.refresh(renc)
    return renc


def peut_embusquer(session: Session, camp: Campaign, rs: Ruleset,
                   lieu: Location | None) -> bool:
    """Le monde n'attend pas que le joueur cherche les ennuis — mais il ne le
    harcèle pas non plus. Un plancher de danger, et une carence après chaque
    rencontre : sans elle, traverser une forêt devient un tunnel de combats."""
    cfg = rs.combat.get("embuscade", {})
    if lieu is None or lieu.danger < int(cfg.get("danger_min", 4)):
        return False
    if active(session, camp) is not None:
        return False
    derniere = session.exec(select(Encounter).where(
        Encounter.campaign_id == camp.id).order_by(Encounter.id.desc())).first()
    if derniere is not None:
        fin = derniere.tour_fin if derniere.tour_fin is not None else derniere.tour_debut
        if camp.tour - fin < int(cfg.get("cooldown_tours", 8)):
            return False
    return True


def tirer_embuscade(session: Session, camp: Campaign, pack, rs: Ruleset,
                    pj: Character) -> Encounter | None:
    """Tire au sort une embuscade selon le danger du lieu, et l'ouvre."""
    lieu = session.get(Location, pj.location_id) if pj.location_id else None
    if not peut_embusquer(session, camp, rs, lieu):
        return None
    cfg = rs.combat.get("embuscade", {})
    chance = float(cfg.get("chance_par_danger", 0.03)) * lieu.danger
    if random.Random(camp.graine + camp.tour * 6151).random() >= chance:
        return None
    return ouvrir(session, camp, pack, rs, pj,
                  declencheur="embuscade", surprise=True)


def _pnjs_du_lieu(session: Session, camp: Campaign,
                  pj: Character) -> list[Character]:
    if not pj.location_id:
        return []
    return list(session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.location_id == pj.location_id,
        Character.is_pc == False,          # noqa: E712
        Character.vivant == True)).all())  # noqa: E712


def adversaires_designes(session: Session, camp: Campaign, pj: Character,
                         cible: str = "") -> list[Character]:
    """Contre QUI le joueur en vient aux mains, ici et maintenant.

    Deux cas, dans cet ordre : quelqu'un de nommément visé, ou les PNJ déjà
    hostiles. Si la réponse est vide, il n'y a PAS de combat — fabriquer des
    brigands parce que le joueur a dit « j'attaque » produisait des bandits
    surgis de nulle part au milieu de l'Académie.
    """
    presents = _pnjs_du_lieu(session, camp, pj)
    nom = (cible or "").strip().lower()
    if nom:
        vise = [c for c in presents
                if nom in c.nom.lower() or c.nom.lower().split()[0] == nom]
        if vise:
            return vise[:1]

    hostiles = []
    for c in presents:
        if c.role_campagne == "antagoniste":
            hostiles.append(c)
            continue
        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id, Relation.source_id == c.id,
            Relation.cible_id == pj.id)).first()
        if rel is not None and rel.valeur <= -40:
            hostiles.append(c)
    return hostiles


# ==========================================================================
# Un échange
# ==========================================================================
def _stat_offensive(rs: Ruleset, technique: dict | None, arme: dict | None,
                    arme_citee: bool = False) -> str:
    """Avec quoi on frappe. La technique décide, puis l'arme CITÉE, puis le
    défaut du ruleset."""
    if technique:
        if technique.get("stat") in rs.stats:
            return technique["stat"]
        cat = technique.get("categorie", "")
        table = rs.combat.get("stat_par_categorie", {})
        if table.get(cat) in rs.stats:
            return table[cat]
        if cat in rs.stats:
            return cat
    if arme_citee and arme and arme.get("stat") in rs.stats:
        return arme["stat"]
    defaut = rs.combat.get("attack_stat", "taijutsu")
    return defaut if defaut in rs.stats else next(iter(rs.stats))


def _arme_en_main(pack, perso: Character,
                  mention: str) -> tuple[dict | None, bool]:
    """L'arme du personnage la plus pertinente, et si l'action la CITE.

    -> (arme, citée)

    La distinction compte : un personnage armé bénéficie toujours de ses
    dégâts, mais seule une arme explicitement employée impose sa
    caractéristique. Sans elle, avoir des kunai dans son sac faisait résoudre
    tous les corps à corps en Vitesse.

    On ne fait jamais confiance à la mention seule : le matériel doit figurer
    dans l'inventaire, sinon le joueur se bat avec un katana imaginaire.
    """
    inv = " ".join(perso.inventaire or []).lower()
    texte = (mention or "").lower()
    meilleure, valeur, citee = None, -1, False
    for a in pack.materiel():
        noms = [n.lower() for n in (a.get("nom", ""), a.get("fr", ""),
                                    a.get("id", "")) if n]
        if not any(n in inv for n in noms):
            continue
        ici = any(n in texte for n in noms)
        score = int((a.get("effets") or {}).get("bonus_degats", 0)) + (100 if ici else 0)
        if score > valeur:
            meilleure, valeur, citee = a, score, ici
    return meilleure, citee


def _appliquer_degats(session: Session, camp: Campaign, rs: Ruleset,
                      cible: Character, degats: int) -> tuple[int, dict | None]:
    """Retire des PV, et installe la blessure si un seuil est franchi."""
    maximum = pv_max(session, rs, cible)
    avant = int(cible.ressources.get("pv", maximum))
    apres = max(0, avant - degats)
    cible.ressources = {**cible.ressources, "pv": apres}

    etat = rs.etat_franchi(avant, apres, maximum)
    if etat is not None:
        deja = session.exec(select(Condition).where(
            Condition.character_id == cible.id,
            Condition.code == etat["code"])).first()
        if deja is None:
            # Une blessure porte sa date de guérison. Le champ existait depuis
            # le premier jour et personne ne l'écrivait : les entailles se
            # gardaient jusqu'à la fin de la campagne, et seul un repos
            # déclaré les levait. Une égratignure se referme toute seule ; une
            # fracture, non — d'où le délai par sévérité, dans le ruleset.
            session.add(Condition(
                campaign_id=camp.id, character_id=cible.id, code=etat["code"],
                libelle=etat.get("libelle", etat["code"]),
                severite=int(etat.get("severite", 1)),
                effets=dict(etat.get("effets") or {}),
                origine="combat", depuis_tour=camp.tour,
                guerit_tour=_guerison(rs, camp.tour, int(etat.get("severite", 1)))))
            cible.etats = [e for e in (cible.etats or [])
                           if e != etat.get("libelle")] + [etat.get("libelle")]
        else:
            etat = None
    session.add(cible)
    return apres, etat


def _leviers_a_consommer(rs: Ruleset, renc: Encounter) -> tuple[int, list[str]]:
    """Les avantages qui ne valent QU'UNE FOIS, et ce qu'ils ajoutent au jet.

    Le ruleset distingue deux portées : `rencontre` (le terrain préparé le reste
    tout le combat) et `premier_echange` (une embuscade ne frappe avant d'être
    vue qu'une seule fois). Sans cette distinction, un joueur qui réussissait
    une embuscade la rejouait à chaque échange.
    """
    bonus, codes = 0, []
    for code in renc.leviers:
        if code in renc.leviers_consommes:
            continue
        levier = rs.levier(code) or {}
        if levier.get("portee") != "premier_echange":
            continue
        bonus += int(levier.get("ampleur", 1)) * 3
        codes.append(code)
    return bonus, codes


def _accorder_objectifs(renc: Encounter, lignes: list[str],
                        effets: list[str]) -> bool:
    """Met les objectifs imposés d'accord avec le fossé COURANT. -> écrasant ?

    TOUT LE SENS DES LEVIERS EST ICI : dès que l'écart est refermé, vaincre
    redevient possible et l'objectif imposé tombe. Sans cette remise à niveau,
    un joueur qui réussissait ses manœuvres restait prisonnier d'un « tu ne
    peux pas gagner » que la mécanique venait pourtant de démentir — et le
    panneau affichait les deux à la fois.

    Appelée à l'ouverture de l'échange ET juste après chaque levier acquis :
    le fossé change au milieu d'un échange, l'affichage ne doit jamais mentir
    entre les deux.
    """
    ecrasant = (not renc.fosse.get("jet", True)
                and renc.fosse.get("superieur") == "b")
    if ecrasant:
        renc.objectifs = list(renc.fosse.get("objectifs") or [])
    elif renc.objectifs or renc.objectif:
        lignes.append("L'écart est refermé : vaincre redevient possible.")
        effets.append("L'adversaire est désormais à ta portée.")
        renc.objectifs, renc.objectif, renc.progres = [], "", 0
    return ecrasant


def _levier_du_nombre(soutiens: list[Character],
                      ennemis: list[Character]) -> list[str]:
    """Être plus nombreux est un levier du ruleset : on le pose donc comme tel.

    C'est la bonne façon de faire compter les alliés sans leur faire remplacer
    le joueur. Un levier réduit l'écart d'un tier — exactement ce qu'apporte un
    camarade dans le dos d'un adversaire.
    """
    return ["nombre"] if soutiens and len(soutiens) >= len(ennemis) else []


def _bonus_fosse(renc: Encounter, camp_: str) -> int:
    """Le bonus que le fossé accorde au camp supérieur.

    Il était écrit dans le ruleset (`bonus_superieur: 4`) et appliqué nulle
    part : un écart d'un tier ne changeait donc absolument rien au combat,
    alors que c'est précisément le cas le plus fréquent.
    """
    if renc.fosse.get("superieur") != camp_:
        return 0
    return int(renc.fosse.get("bonus_superieur", 0))


def _encaisser(session: Session, camp: Campaign, rs: Ruleset,
               cible: Character, degats: int, effets: list[str]) -> list[str]:
    """Applique les dégâts et produit les lignes du journal mécanique."""
    pv, etat = _appliquer_degats(session, camp, rs, cible, degats)
    lignes = [f"Dégâts {degats} → {cible.nom} à {pv} PV."]
    effets.append(f"{cible.nom} : -{degats} PV")
    if etat is not None:
        effets.append(f"{cible.nom} : {etat.get('libelle')}")
    if pv <= 0:
        lignes.append(f"{cible.nom} est hors de combat.")
        effets.append(f"{cible.nom} est hors de combat.")
    return lignes


def combo_disponible(session: Session, pack, rs: Ruleset, pj: Character,
                     technique_ref: str) -> dict | None:
    """Le combo que cette technique déclenche, si l'autre moitié suit.

    Les synergies étaient écrites dans le lore et lues par personne : deux
    techniques maîtrisées ensemble valaient exactement autant que prises
    séparément. Or c'est là que vit la profondeur tactique d'un jeu shinobi —
    immobiliser PUIS frapper, cloner PUIS enflammer.

    Exigeant par construction : il faut posséder TOUTES les techniques du
    combo, et chacune au-dessus du seuil de maîtrise. Un combo qu'on déclenche
    par accident n'est pas un combo.
    """
    if not technique_ref:
        return None
    liens = {ct.technique_ref: ct for ct in session.exec(
        select(CharacterTechnique).where(
            CharacterTechnique.character_id == pj.id)).all()}
    for s in pack.combos_de(list(liens)):
        requis = s.get("requiert") or []
        if technique_ref not in requis:
            continue
        seuil = int(s.get("maitrise_min", 0))
        if all(liens[r].maitrise >= seuil for r in requis):
            return s
    return None


def _meilleure_offensive(rs: Ruleset, perso: Character) -> str:
    """La voie par laquelle ce personnage frappe le mieux.

    Un jônin instructeur a du ninjutsu, de la perception et de la vitesse, pas
    du taijutsu : le faire cogner au poing comme un genin lui retirait tout ce
    qui en fait un jônin. On lui laisse sa meilleure voie de combat.
    """
    table = rs.combat.get("stat_par_categorie", {})
    candidates = {rs.combat.get("attack_stat", "taijutsu"),
                  *(v for v in table.values() if v in rs.stats)}
    candidates = [c for c in candidates if c in rs.stats]
    if not candidates:
        return next(iter(rs.stats))
    return max(candidates, key=lambda k: int(perso.stats.get(k, 0)))


def _frappes_alliees(session: Session, camp: Campaign, rs: Ruleset,
                     renc: Encounter, soutiens: list[Character],
                     rng: random.Random, effets: list[str]) -> list[str]:
    """Les alliés frappent, plus modestement que le joueur.

    Volontairement bridés (`facteur_degats`) : ils doivent équilibrer le nombre,
    pas remporter le combat à la place du joueur. Une équipe qui gagne toute
    seule est une équipe qui prive le joueur de sa scène.
    """
    cfg = rs.combat.get("allies", {})
    facteur = float(cfg.get("facteur_degats", 0.7))
    posture = rs.posture(cfg.get("posture", "mesuree"))
    lignes: list[str] = []

    for ami in soutiens[:int(cfg.get("max_agissants", 3))]:
        ennemis = adverses(session, renc)
        if not ennemis:
            break
        cible = min(ennemis, key=lambda c: int(c.ressources.get("pv", 99)))
        op = rs.oppose(ami.stats, _meilleure_offensive(rs, ami),
                       cible.stats, rs.combat.get("defense_stat", "endurance"),
                       int(posture.get("attaque", 0))
                       + _malus_blessures(session, rs, ami),
                       _malus_blessures(session, rs, cible))
        if not op.touche:
            lignes.append(f"{ami.nom} engage {cible.nom} sans le déborder.")
            continue
        degats = max(1, int(round(rs.degats(
            op.marge, 0, int(posture.get("degats", 0)), op.issue) * facteur)))
        lignes.append(f"{ami.nom} appuie sur {cible.nom}.")
        lignes += _encaisser(session, camp, rs, cible, degats, effets)
    return lignes


def _posture_adverse(rs: Ruleset, ia: str, pourcent_pv: float,
                     rng: random.Random) -> str:
    """La conduite d'un adversaire, en Python.

    Déléguer ce choix au modèle coûterait un appel par adversaire et par
    échange sans rien gagner : ce qu'on attend d'un adversaire, c'est qu'il
    soit lisible et constant, pas qu'il improvise.
    """
    if ia == "bete_enragee":
        return "offensive"
    if pourcent_pv <= 35:
        return "defensive" if rng.random() < 0.6 else "mesuree"
    if ia == "agressif":
        return "offensive" if rng.random() < 0.7 else "mesuree"
    if ia == "piegeur":
        return "manoeuvre" if rng.random() < 0.35 else "mesuree"
    return "offensive" if rng.random() < 0.4 else "mesuree"


def echanger(session: Session, camp: Campaign, pack, rs: Ruleset,
             pj: Character, renc: Encounter, intent: dict,
             bonus_technique: int = 0, maitrise: int = 50) -> dict:
    """Un échange complet. Rend le bloc mécanique que le narrateur habillera.

    -> {"bloc": str, "effets": [str], "lignes": [str], "reussi": bool,
        "statut": str}

    `bloc` est au narrateur ce que le résultat du dé est à un tour ordinaire :
    un fait acquis, non négociable, qu'il raconte sans le contredire.
    """
    rng = random.Random(camp.graine + camp.tour * 31 + renc.echange * 7)
    renc.echange += 1

    posture_nom = intent.get("posture") or "mesuree"
    if posture_nom not in rs.postures:
        posture_nom = "mesuree"
    posture = rs.posture(posture_nom)

    ennemis = adverses(session, renc)
    if not ennemis:
        return _clore(session, camp, rs, renc, "gagnee", pj, pack)

    # --- la cible : celle que le joueur nomme, sinon la plus entamée
    nomme = (intent.get("cible") or "").strip().lower()
    cible = next((c for c in ennemis if nomme and nomme in c.nom.lower()), None)
    if cible is None:
        cible = min(ennemis, key=lambda c: int(c.ressources.get("pv", 99)))

    technique = pack.resoudre_nom(intent.get("technique") or "") \
        if intent.get("technique") else None
    arme, arme_citee = _arme_en_main(
        pack, pj, f"{intent.get('arme', '')} {intent.get('resume', '')}")

    lignes: list[str] = []
    effets: list[str] = []
    soutiens = allies(session, renc, pj)

    # --- le fossé, réévalué à chaque échange : les leviers ont pu changer, les
    #     alliés ont pu tomber, et les blessures font baisser la puissance.
    tier_adverse = max(c.tier for c in ennemis)
    for code in _levier_du_nombre(soutiens, ennemis):
        if code not in renc.leviers:
            renc.leviers = renc.leviers + [code]
    renc.fosse = rs.fosse_effectif(pj.tier, tier_adverse, renc.leviers)
    ecrasant = _accorder_objectifs(renc, lignes, effets)

    if ecrasant and not renc.objectif:
        voulu = f"{intent.get('objectif', '')} {intent.get('resume', '')}".lower()
        renc.objectif = next(
            (o for o in renc.objectifs if o.split()[0].lower() in voulu),
            renc.objectifs[0] if renc.objectifs else "survivre")
        lignes.append(f"Vaincre est hors de portée. Objectif retenu : "
                      f"{renc.objectif}.")

    # --- le chakra : une posture technique se paie, et l'épuisement se voit
    cout = int(posture.get("chakra", 0))
    if cout and technique:
        cout = max(1, int(round(
            cout * float(rs.palier_maitrise(maitrise).get("cout", 1.0)))))
    if cout:
        dispo = int(pj.ressources.get("chakra", 0))
        if dispo < cout:
            lignes.append("Pas assez de chakra : la technique n'a pas pris.")
            posture_nom, posture, technique = "mesuree", rs.posture("mesuree"), None
        else:
            pj.ressources = {**pj.ressources, "chakra": dispo - cout}
            effets.append(f"chakra -{cout} → {pj.ressources['chakra']}")

    malus_pj = _malus_blessures(session, rs, pj)
    # Une embuscade subie se paie une fois, au premier échange : le temps de
    # comprendre d'où ça vient.
    if renc.surprise and renc.echange == 1:
        malus_pj -= 3
        lignes.append("Pris de court : le premier échange se joue en retard.")
    reussi = False

    # ------------------------------------------------------------- l'action
    if posture_nom == "desengagement":
        cfg = rs.combat.get("desengagement", {})
        check = rs.check(pj.stats, cfg.get("stat", "vitesse"),
                         cfg.get("difficulte", "difficile"),
                         bonus_technique + malus_pj + int(posture.get("attaque", 0)))
        reussi = check.reussi
        lignes.append(f"Désengagement — dé {check.de}, total {check.total} contre "
                      f"{check.difficulte} : {'rompu' if reussi else 'raté'}.")
        if reussi:
            session.add(renc)
            session.add(pj)
            session.commit()
            return _fin(session, camp, rs, renc, pj, pack, "rompue", lignes, effets)

    elif posture_nom == "manoeuvre":
        code = (intent.get("levier") or "").strip()
        if code not in rs.leviers():
            code = "terrain_prepare"
        plafond = int(rs.data.get("leviers_cumul_max", 3))
        if code in renc.leviers:
            lignes.append(f"Cet avantage est déjà acquis : la manœuvre n'ajoute "
                          f"rien.")
        elif rs.ampleur_leviers(renc.leviers) >= plafond:
            lignes.append("Les avantages accumulés plafonnent : en empiler un de "
                          "plus ne change plus rien au rapport de force.")
        else:
            check = rs.check(pj.stats, STAT_LEVIER.get(code, "intelligence"),
                             "normal", bonus_technique + malus_pj
                             + int(posture.get("attaque", 0)))
            reussi = check.reussi
            libelle = (rs.levier(code) or {}).get("libelle", code)
            lignes.append(f"Manœuvre ({libelle}) — dé {check.de}, total "
                          f"{check.total} contre {check.difficulte} : "
                          f"{'établie' if reussi else 'manquée'}.")
            if reussi:
                renc.leviers = renc.leviers + [code]
                avant = renc.fosse.get("ecart", 0)
                renc.fosse = rs.fosse_effectif(pj.tier, tier_adverse,
                                               renc.leviers)
                effets.append(f"Avantage acquis : {libelle}")
                if renc.fosse.get("ecart", 0) < avant:
                    effets.append(f"Écart ramené à {renc.fosse.get('ecart')} — "
                                  f"{renc.fosse.get('note', '')}")
                # Le fossé vient de changer au milieu de l'échange : l'objectif
                # imposé doit suivre immédiatement, sinon le panneau annonce
                # « hors de portée » sous une note « difficile mais possible ».
                ecrasant = _accorder_objectifs(renc, lignes, effets)

    elif ecrasant:
        # On ne lance pas les dés pour vaincre ce qu'on ne peut pas vaincre.
        lignes.append(f"{cible.nom} est hors de portée : le frapper ne mène à rien "
                      f"tant que l'écart n'est pas ramené à un cran.")

    else:
        stat = _stat_offensive(rs, technique, arme, arme_citee)
        pct_cible = int(cible.ressources.get("pv", 1)) * 100 \
            / pv_max(session, rs, cible)
        eclair, codes_eclair = _leviers_a_consommer(rs, renc)
        if eclair:
            renc.leviers_consommes = renc.leviers_consommes + codes_eclair
            lignes.append("Avantage joué : " + ", ".join(
                (rs.levier(c) or {}).get("libelle", c) for c in codes_eclair))
        bonus_a = (int(posture.get("attaque", 0)) + bonus_technique + malus_pj
                   + eclair + _bonus_fosse(renc, "a"))
        bonus_b = (int(rs.posture(_posture_adverse(
            rs, _lire_note(cible, "ia"), pct_cible, rng)).get("defense", 0))
            + _malus_blessures(session, rs, cible) + _bonus_fosse(renc, "b"))

        # La synergie, s'il y en a une. Elle ne facilite pas la touche : elle
        # aggrave ce qu'elle coûte. Frapper mieux est le fait de la posture ;
        # frapper au bon moment est le fait du répertoire.
        combo = combo_disponible(session, pack, rs, pj,
                                 (technique or {}).get("id", ""))
        if combo:
            lignes.append(f"Synergie — {combo.get('nom', combo.get('id'))}.")
            effets.append(f"Synergie : {combo.get('nom', combo.get('id'))}")

        if combo and (combo.get("effet") or {}).get("cible_sans_defense"):
            bonus_b = _malus_blessures(session, rs, cible) + _bonus_fosse(renc, "b")

        op = rs.oppose(pj.stats, stat, cible.stats,
                       rs.combat.get("defense_stat", "endurance"),
                       bonus_a, bonus_b)
        reussi = op.touche
        libelle = rs.stats.get(stat, {}).get("label", stat)
        lignes.append(f"{pj.nom} attaque {cible.nom} ({libelle}) — {op.total_a} "
                      f"contre {op.total_b}, marge {op.marge:+d} : "
                      f"{op.issue.replace('_', ' ')}.")
        if op.touche:
            bonus_arme = int((arme.get("effets") or {}).get("bonus_degats", 0)) \
                if arme else 0
            bonus_arme += int((combo.get("effet") or {}).get("bonus_degats", 0)) \
                if combo else 0
            degats = rs.degats(op.marge, bonus_arme,
                               int(posture.get("degats", 0)), op.issue)
            if op.critique:
                degats = int(degats * 1.5)
            lignes += _encaisser(session, camp, rs, cible, degats, effets)

    # ------------------------------------------------- les alliés se battent
    if posture_nom == "desengagement":
        pass
    elif ecrasant:
        # Ils ne peuvent pas l'entamer non plus : le laisser grignoter par les
        # dégâts alliés contredirait « vaincre est hors de portée ». Ils tiennent
        # la ligne, ce qui est déjà ce qui permet au joueur d'agir.
        if soutiens:
            lignes.append(", ".join(c.nom for c in soutiens)
                          + " tiennent la ligne sans pouvoir l'entamer.")
    else:
        lignes += _frappes_alliees(session, camp, rs, renc, soutiens, rng, effets)

    # --- progrès vers l'objectif imposé : frapper n'y sert à rien
    if ecrasant and reussi and posture_nom != "offensive":
        renc.progres += 1
        requis = int(rs.combat.get("objectif_progres_requis", 3))
        lignes.append(f"Progrès vers « {renc.objectif} » : {renc.progres}/{requis}.")
        effets.append(f"{renc.objectif} : {renc.progres}/{requis}")
        if renc.progres >= requis:
            session.add(renc)
            session.add(pj)
            session.commit()
            return _fin(session, camp, rs, renc, pj, pack, "objectif_atteint",
                        lignes, effets)

    # ---------------------------------------------------------- les ripostes
    # Elles se RÉPARTISSENT. Concentrer toutes les attaques sur le personnage
    # qui joue transformait chaque groupe d'adversaires en exécution, et rendait
    # les alliés purement décoratifs.
    part_joueur = float(rs.combat.get("part_attaques_sur_joueur", 0.55))
    defense_pj = int(posture.get("defense", 0)) + malus_pj
    for pnj in adverses(session, renc)[:RIPOSTES_MAX]:
        pct = int(pnj.ressources.get("pv", 1)) * 100 / pv_max(session, rs, pnj)
        sa_posture = _posture_adverse(rs, _lire_note(pnj, "ia"), pct, rng)
        if sa_posture in SANS_DEGATS:
            lignes.append(f"{pnj.nom} ne frappe pas : il manœuvre.")
            continue

        debout = allies(session, renc, pj)
        vise_pj = not debout or rng.random() < part_joueur
        victime = pj if vise_pj else rng.choice(debout)

        p = rs.posture(sa_posture)
        op = rs.oppose(pnj.stats, rs.combat.get("attack_stat", "taijutsu"),
                       victime.stats, rs.combat.get("defense_stat", "endurance"),
                       int(p.get("attaque", 0)) + _malus_blessures(session, rs, pnj)
                       + _bonus_fosse(renc, "b"),
                       (defense_pj if vise_pj else _malus_blessures(
                           session, rs, victime)) + _bonus_fosse(renc, "a"))
        if not op.touche:
            lignes.append(f"{pnj.nom} attaque {victime.nom} — "
                          f"{op.issue.replace('_', ' ')}.")
            continue
        degats = rs.degats(op.marge, 0, int(p.get("degats", 0)), op.issue)
        lignes.append(f"{pnj.nom} touche {victime.nom}.")
        lignes += _encaisser(session, camp, rs, victime, degats, effets)
        if int(pj.ressources.get("pv", 1)) <= 0:
            break

    # ------------------------------------------------------------- le moral
    # Voir un compagnon tomber ou détaler fait réfléchir les autres : c'est ce
    # qui termine la plupart des bagarres, bien avant le dernier point de vie.
    pertes = len(tombes(session, renc)) + len(fuis(session, renc))
    cfg_moral = rs.combat.get("moral", {}) or {}
    for pnj in adverses(session, renc):
        pct = int(pnj.ressources.get("pv", 1)) * 100 / pv_max(session, rs, pnj)
        moral = _lire_note(pnj, "moral")
        chance = rs.chance_rupture(pct, pj.tier - pnj.tier, moral)
        if pertes and moral not in (cfg_moral.get("jamais_si") or []) \
                and pnj.tier <= pj.tier:
            chance = max(chance, min(0.9, pertes * float(
                cfg_moral.get("chute_compagnon", 50)) / 100))
        if chance and rng.random() < chance:
            retirer(session, pnj)
            lignes.append(f"{pnj.nom} rompt le contact et disparaît.")
            effets.append(f"{pnj.nom} a fui.")

    renc.journal = renc.journal + [{"echange": renc.echange, "texte": t}
                                   for t in lignes]
    session.add(renc)
    session.add(pj)
    session.commit()

    # ------------------------------------------------------------- la fin ?
    if int(pj.ressources.get("pv", 1)) <= 0:
        return _fin(session, camp, rs, renc, pj, pack, "perdue", lignes, effets)
    if not adverses(session, renc):
        return _fin(session, camp, rs, renc, pj, pack, "gagnee", lignes, effets)
    if renc.echange >= int(rs.combat.get("echanges_max", 12)):
        return _fin(session, camp, rs, renc, pj, pack, "dispersee", lignes, effets)

    return {"bloc": _bloc(rs, renc, lignes), "effets": effets, "lignes": lignes,
            "reussi": reussi, "statut": renc.statut}


def _fin(session: Session, camp: Campaign, rs: Ruleset, renc: Encounter,
         pj: Character, pack, statut: str, lignes: list[str],
         effets: list[str]) -> dict:
    sortie = _clore(session, camp, rs, renc, statut, pj, pack)
    sortie["lignes"] = lignes + sortie["lignes"]
    sortie["effets"] = effets + sortie["effets"]
    sortie["bloc"] = _bloc(rs, renc, sortie["lignes"])
    return sortie


def _bloc(rs: Ruleset, renc: Encounter, lignes: list[str]) -> str:
    """Le fait acquis transmis au narrateur. Il l'habille, il ne le discute pas."""
    etat = [f"AFFRONTEMENT — échange {renc.echange} : {renc.titre}"]
    if renc.leviers:
        etat.append("Avantages en main : " + ", ".join(
            (rs.levier(c) or {}).get("libelle", c) for c in renc.leviers))
    if renc.objectif and renc.objectifs:
        etat.append(f"Vaincre est hors de portée. Objectif : {renc.objectif} "
                    f"({renc.progres}/"
                    f"{rs.combat.get('objectif_progres_requis', 3)}).")
    etat += lignes
    if renc.statut != "en_cours":
        etat.append("ISSUE : " + {
            "gagnee": "le camp adverse est hors de combat.",
            "perdue": "le personnage tombe. La scène doit dire ce qu'il en coûte.",
            "rompue": "le personnage a rompu le contact et s'éloigne.",
            "objectif_atteint": f"l'objectif « {renc.objectif} » est atteint.",
            "dispersee": "l'affrontement se délite sans vainqueur.",
        }.get(renc.statut, renc.statut))
    return "RÉSULTAT IMPOSÉ (non négociable) :\n" + "\n".join(etat)


# ==========================================================================
# Clôture
# ==========================================================================
def _clore(session: Session, camp: Campaign, rs: Ruleset, renc: Encounter,
           statut: str, pj: Character, pack) -> dict:
    """Ferme la rencontre et solde ses conséquences : butin, XP, blessures.

    UN PERSONNAGE JOUEUR NE MEURT PAS D'UNE RENCONTRE PERDUE, sauf en
    difficulté impitoyable. Ce n'est pas de la complaisance : une mort sur un
    mauvais jet au tour 30 détruit une campagne, et le genre a toujours préféré
    la capture, la dette et la cicatrice — qui font de meilleures scènes.
    """
    renc.statut = statut
    renc.tour_fin = camp.tour
    effets: list[str] = []

    if statut in ("gagnee", "objectif_atteint"):
        vaincus = tombes(session, renc)
        par_tier = int(rs.combat.get("xp_par_tier", 35))
        facteur_fuite = float(rs.combat.get("xp_fuite_facteur", 0.5))
        xp = sum(par_tier * max(1, c.tier) for c in vaincus)
        xp += int(sum(par_tier * max(1, c.tier) for c in fuis(session, renc))
                  * facteur_fuite)
        if statut == "objectif_atteint":
            # Tenir tête à un adversaire écrasant vaut ce qu'il vaut, LUI.
            xp = max(xp, par_tier * max(1, int(
                renc.fosse.get("tier_adverse_brut", 1))))
        if xp:
            from app.engine import progression

            niveau, reste, gagnes = rs.appliquer_xp(pj.niveau, pj.xp, xp)
            pj.niveau, pj.xp = niveau, reste
            mention = ""
            if gagnes:
                libres = progression.crediter(rs, pj, gagnes)
                mention = (f" — NIVEAU {niveau} atteint ! "
                           f"{libres} point(s) à placer.")
            effets.append(f"XP +{xp}" + mention)
            session.add(pj)

        butin: list[str] = []
        for c in vaincus:
            arch = pack.get(c.lore_ref) or {}
            butin += list(arch.get("butin") or arch.get("recolte") or [])
        if butin:
            renc.butin = butin
            pj.inventaire = list(pj.inventaire or []) + butin[:4]
            session.add(pj)
            effets.append("Butin : " + ", ".join(butin[:4]))

        # Un rouleau, parfois. C'est ce qui donne une valeur à ce qu'on ramasse
        # et ce qui ouvre une technique qu'on n'aurait pas pu travailler seul.
        try:
            from app.engine import apprentissage

            effets += apprentissage.tirer_parchemin(
                session, camp, pack, rs, pj, graine=renc.id or 0)
        except Exception:  # noqa: BLE001 — un butin manqué ne casse pas la clôture
            pass

    if statut == "perdue":
        if camp.difficulte == "impitoyable":
            pj.vivant = False
            effets.append(f"{pj.nom} est mort.")
        else:
            pj.ressources = {**pj.ressources, "pv": 1}
            pj.etats = [e for e in (pj.etats or []) if e != "relevé de justesse"] \
                + ["relevé de justesse"]
            session.add(Condition(
                campaign_id=camp.id, character_id=pj.id, code="hors_combat",
                libelle="Relevé de justesse", severite=3,
                effets={"puissance": -6}, origine="combat perdu",
                depuis_tour=camp.tour))
            effets.append(f"{pj.nom} tombe — il s'en tire, mais pas indemne.")
        session.add(pj)

    # Une rencontre close ne laisse jamais un ennemi en embuscade permanente
    # sur le lieu : ceux qui tiennent encore debout s'en vont.
    if statut in ("rompue", "dispersee", "perdue"):
        for c in adverses(session, renc):
            retirer(session, c)

    session.add(MemoryFact(
        campaign_id=camp.id, tour=camp.tour, importance=4, nature="fait",
        texte=f"{renc.titre} — issue : {statut.replace('_', ' ')} "
              f"(tours {renc.tour_debut} à {camp.tour}).",
        entites=[pj.id]))
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour, type="combat",
        resume=f"{renc.titre} : {statut.replace('_', ' ')}", importance=4,
        entites=[pj.id]))

    renc.journal = renc.journal + [{"echange": renc.echange,
                                    "texte": f"Issue : {statut}"}]
    session.add(renc)
    session.commit()

    from app.engine.creation import _recalculer_puissance
    _recalculer_puissance(session, camp, rs, pj)
    session.commit()

    return {"bloc": _bloc(rs, renc, []), "effets": effets, "lignes": [],
            "reussi": statut in ("gagnee", "objectif_atteint", "rompue"),
            "statut": statut}


def soigner(session: Session, camp: Campaign, rs: Ruleset, perso: Character,
            pv: int = 0, chakra: int = 0,
            lever_etats: bool = False) -> list[str]:
    """Rend des ressources, et éventuellement lève les blessures réversibles.

    Sans ce chemin, la première rencontre perdue condamne la campagne : les
    blessures s'accumulent, la puissance ne remonte jamais, et chaque rencontre
    suivante est plus dure que la précédente.
    """
    effets: list[str] = []
    if pv:
        maximum = pv_max(session, rs, perso)
        avant = int(perso.ressources.get("pv", 0))
        perso.ressources = {**perso.ressources, "pv": min(maximum, avant + pv)}
        if perso.ressources["pv"] > avant:
            effets.append(f"PV +{perso.ressources['pv'] - avant}")
    if chakra:
        maximum = int(rs.ressources_pour_tier(perso.tier).get("chakra", 30))
        avant = int(perso.ressources.get("chakra", 0))
        perso.ressources = {**perso.ressources,
                            "chakra": min(max(maximum, avant), avant + chakra)}
        if perso.ressources["chakra"] > avant:
            effets.append(f"chakra +{perso.ressources['chakra'] - avant}")
    if lever_etats:
        for c in session.exec(select(Condition).where(
                Condition.character_id == perso.id,
                Condition.reversible == True)).all():  # noqa: E712
            session.delete(c)
            effets.append(f"{c.libelle} : soigné")
        perso.etats = []
    session.add(perso)
    session.commit()
    from app.engine.creation import _recalculer_puissance
    _recalculer_puissance(session, camp, rs, perso)
    session.commit()
    return effets


def _guerison(rs: Ruleset, tour: int, severite: int) -> int | None:
    """À quel tour cette blessure se sera refermée d'elle-même.

    `None` pour ce qui ne guérit pas tout seul : au-delà du dernier délai
    déclaré, il faut un repos ou des soins.
    """
    delais = rs.combat.get("recuperation", {}).get("guerison_par_severite", [])
    if not delais or severite < 1 or severite > len(delais):
        return None
    duree = delais[severite - 1]
    return tour + int(duree) if duree else None


def soigner_le_temps(session: Session, camp: Campaign, perso: Character,
                     rs: Ruleset) -> list[str]:
    """Referme les blessures dont l'heure est venue.

    Appelée à chaque tour hors combat comme en combat : le temps passe aussi
    pendant qu'on se bat, et une entaille du premier échange n'a pas à durer
    jusqu'au dernier.
    """
    effets: list[str] = []
    for c in session.exec(select(Condition).where(
            Condition.character_id == perso.id,
            Condition.reversible == True)).all():  # noqa: E712
        if c.guerit_tour is None or camp.tour < c.guerit_tour:
            continue
        session.delete(c)
        perso.etats = [e for e in (perso.etats or []) if e != c.libelle]
        effets.append(f"{c.libelle} : refermé")
    if effets:
        session.add(perso)
        session.commit()
        from app.engine.creation import _recalculer_puissance
        _recalculer_puissance(session, camp, rs, perso)
        session.commit()
    return effets


def recuperer(session: Session, camp: Campaign, rs: Ruleset, perso: Character,
              repos: bool = False) -> list[str]:
    """La récupération hors combat : lente à chaque tour, franche sur un repos.

    Le goutte-à-goutte par tour existe pour que traverser trois jours de route
    referme les écorchures ; le repos déclaré est ce qui permet de repartir
    vraiment, et c'est la seule chose qui rende un combat perdu jouable.
    """
    cfg = rs.combat.get("recuperation", {})
    if not repos:
        return soigner(session, camp, rs, perso,
                       pv=int(cfg.get("pv_par_tour", 1)),
                       chakra=int(cfg.get("chakra_par_tour", 2)))
    maximum = pv_max(session, rs, perso)
    chakra_max = int(rs.ressources_pour_tier(perso.tier).get("chakra", 30))
    return soigner(
        session, camp, rs, perso,
        pv=max(1, int(maximum * float(cfg.get("repos_pv", 0.45)))),
        chakra=max(1, int(chakra_max * float(cfg.get("repos_chakra", 0.6)))),
        lever_etats=True)


# ==========================================================================
# Ce que le MJ a le droit de savoir de la rencontre
# ==========================================================================
def etat_pour_contexte(session: Session, camp: Campaign, rs: Ruleset,
                       renc: Encounter, pj: Character) -> str:
    """L'état du combat, sans les chiffres qui n'appartiennent pas au MJ.

    Le narrateur reçoit l'état APPARENT des adversaires — « blessé », « il tient
    à peine » — jamais leurs points de vie exacts : un MJ qui connaît les PV
    annonce les PV, et le joueur se met à jouer contre une barre de vie.
    """
    lignes = [f"### AFFRONTEMENT EN COURS\n{renc.titre} — échange "
              f"{renc.echange + 1}."]
    if renc.surprise and renc.echange <= 1:
        lignes.append("Le camp du joueur a été pris de court.")

    for c in adverses(session, renc):
        pct = int(c.ressources.get("pv", 1)) * 100 / pv_max(session, rs, c)
        apparent = ("intact" if pct > 80 else "entamé" if pct > 60
                    else "blessé" if pct > 35 else "il tient à peine")
        lignes.append(f"- {c.nom} — {apparent}")
        tactique = _lire_note(c, "tactique")
        if tactique:
            lignes.append(f"  Se bat ainsi : {tactique}")

    hors = [c.nom for c in tombes(session, renc)]
    if hors:
        lignes.append("Hors de combat : " + ", ".join(hors))

    if renc.leviers:
        lignes.append("Avantages acquis par le joueur : " + ", ".join(
            (rs.levier(c) or {}).get("libelle", c) for c in renc.leviers))

    amis = allies(session, renc, pj)
    if amis:
        lignes.append("Se battent aux côtés du joueur : " + ", ".join(
            c.nom for c in amis))

    if renc.objectif and renc.objectifs:
        lignes.append(
            f"Vaincre cet adversaire est HORS DE PORTÉE. La scène ne peut se "
            f"dénouer que par : {renc.objectif}. Avancement {renc.progres}/"
            f"{rs.combat.get('objectif_progres_requis', 3)}. Ne laisse jamais "
            f"entendre que le joueur pourrait l'emporter de force.")
    elif renc.objectifs:
        lignes.append(
            "Vaincre est HORS DE PORTÉE. Les seules issues ouvertes : "
            + ", ".join(renc.objectifs)
            + ". Frapper cet adversaire ne mène nulle part.")
    elif renc.fosse.get("ecart", 0) >= 1:
        lignes.append(f"Rapport de force : {renc.fosse.get('note', '')}.")

    return "\n".join(lignes)


def postures_offertes(rs: Ruleset) -> list[dict]:
    """Ce que l'interface propose au joueur. La liste vient du ruleset : en
    ajouter une ne demande pas de toucher au gabarit."""
    return [{"code": code, "label": p.get("label", code),
             "aide": p.get("aide", ""), "chakra": int(p.get("chakra", 0))}
            for code, p in rs.postures.items()]
