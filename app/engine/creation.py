"""Création de personnage.

Le joueur choisit QUI est son personnage — nom, sexe, âge, village, origine,
spécialisation. Le système écrit CE QUI SOMMEILLE en lui, et ne lui en rend
qu'un présage.

Appartenir à un clan est une IDENTITÉ : libre, sans coût en points.
Porter sa lignée est un POTENTIEL : latent, conditionnel, jamais acquis à la
création. Tous les Uchiha n'éveillent pas le Sharingan.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.engine import destiny as dst
from app.llm.prompts import PRESAGE
from app.llm.provider import get_llm
from app.lore.pack import LorePack
from app.models import (Campaign, Character, CharacterCapacity,
                        CharacterTechnique, Destiny, DestinyClue, DestinyTrait,
                        Location)
from app.rules.engine import Ruleset, fiche_hash

MAX_PJ = 3


class ErreurCreation(ValueError):
    """Message destiné à être affiché au joueur tel quel."""


@dataclass
class Fiche:
    nom: str
    sexe: str = ""
    age: int | None = None
    apparence: str = ""
    joueur: str = ""
    village_id: str = "konoha"
    origine: str = "sans_clan"          # clan_connu|clan_mineur|clan_invente|sans_clan
    clan_id: str = ""
    clan_invente: dict = field(default_factory=dict)
    specialisation: str = "ninjutsu"
    stats_libres: dict = field(default_factory=dict)
    mode_destinee: str = "proposee"
    graine_destinee: int | None = None
    archetype: str = ""                 # tiré par « je m'en remets au destin »


def traits_de_l_archetype(pack: LorePack, rs: Ruleset, fiche: Fiche,
                          rng: random.Random) -> list[str]:
    """Les traits latents que l'archétype impose au tirage de destinée.

    `traits` sont tous imposés ; `traits_parmi` en tire UN ; `lignee_du_clan`
    prend la lignée propre au clan tiré, s'il en a une. L'éligibilité est
    vérifiée ensuite par le générateur, jamais ici.
    """
    arch = pack.archetype(fiche.archetype) or {}
    forces = list(arch.get("traits") or [])
    if arch.get("traits_parmi"):
        # Seulement ce que ce personnage peut porter : un Hyôton hors de Kiri
        # et de Konoha était tiré, puis refusé — et le « dernier d'une
        # lignée » n'héritait de rien une fois sur trois.
        def possible(ref: str) -> bool:
            el = ((pack.trait(ref) or {}).get("eligibilite") or {})
            return (not el.get("villages") or fiche.village_id in el["villages"]) and \
                   (not el.get("clans") or (fiche.clan_id or "") in el["clans"])
        parmi = [r for r in arch["traits_parmi"] if possible(r)] or list(arch["traits_parmi"])
        forces.append(rng.choice(parmi))
    if arch.get("lignee_du_clan") and fiche.clan_id:
        for t in pack.traits():
            if t.get("famille") == "lignee_clan" and \
                    fiche.clan_id in ((t.get("eligibilite", {}) or {}).get("clans") or []):
                forces.append(t["id"])
                break
    return forces


# --------------------------------------------------------------------------
# « Je m'en remets au destin »
# --------------------------------------------------------------------------
def tirer_fiche(pack: LorePack, rs: Ruleset, fiche: Fiche, annee: int,
                graine: int | None = None) -> Fiche:
    """Remplit village, origine, clan, voie et points quand le joueur refuse
    de les choisir.

    Le joueur garde son nom, son âge et son apparence : ce qu'il abandonne,
    c'est d'où il vient et ce qu'il sait faire, pas qui il est. Le tirage
    respecte les mêmes règles que la main du joueur — un clan appartient à son
    village, le budget de points est celui de l'origine tirée — de sorte que
    `valider()` accepte le résultat sans traitement de faveur.
    """
    rng = random.Random(graine if graine is not None else 0)

    # L'ARCHÉTYPE D'ABORD. Sans lui, le hasard donnait ce que la loi des
    # grands nombres donne : un sans-clan de Konoha en ninjutsu, huit fois sur
    # dix. Un joueur qui remet tout au destin veut qu'il se passe quelque
    # chose — un réceptacle, un marqué, un ermite. Voir destinee.yaml.
    archetypes = pack.archetypes()
    arch = rng.choices(archetypes, weights=[a.get("poids", 10) for a in archetypes])[0] \
        if archetypes else {}
    fiche.archetype = arch.get("id", "")

    villages = [v for v in pack.liste("villages") if pack.actif_a(v, annee)] \
        or pack.liste("villages")
    # les grands villages d'abord : un genin sort rarement d'un village mineur
    majeurs = [v for v in villages if v.get("rang") == "majeur"] or villages
    fiche.village_id = rng.choice(majeurs)["id"]

    # Origine : pondérée, parce que la majorité des ninjas n'ont pas de clan.
    # Le poids se lit dans le ruleset (`poids_tirage`), avec un repli raisonnable.
    defaut = {"sans_clan": 50, "clan_mineur": 28, "clan_connu": 20}
    poids = {k: int((o or {}).get("poids_tirage", defaut.get(k, 1)))
             for k, o in rs.origines.items()}

    def clans_possibles(rang: str) -> list[dict]:
        return [c for c in pack.clans(rang=rang, annee=annee)
                if c.get("disperse") or c.get("village") == fiche.village_id]

    # on retire les origines impossibles ici : un village sans clan majeur ne
    # doit pas pouvoir être tiré « clan connu » et échouer à la validation
    if not clans_possibles("majeur"):
        poids.pop("clan_connu", None)
    if not clans_possibles("mineur"):
        poids.pop("clan_mineur", None)
    poids.pop("clan_invente", None)   # inventer un clan est un acte d'auteur
    # …et l'archétype borne ce qui reste : un enfant de clan a un clan.
    if arch.get("origines"):
        borne = {k: v for k, v in poids.items() if k in arch["origines"]}
        poids = borne or poids

    cles = list(poids)
    fiche.origine = rng.choices(cles, weights=[poids[k] for k in cles])[0]

    if fiche.origine in ("clan_connu", "clan_mineur"):
        rang = "majeur" if fiche.origine == "clan_connu" else "mineur"
        # Les clans DU village d'abord ; un clan dispersé n'est tiré qu'à
        # défaut. Un ninja de Suna tiré « Uzumaki » est une bizarrerie, pas
        # une surprise.
        propres = [c for c in clans_possibles(rang)
                   if c.get("village") == fiche.village_id]
        fiche.clan_id = rng.choice(propres or clans_possibles(rang))["id"]
    else:
        fiche.clan_id = ""
        fiche.clan_invente = {}

    fiche.specialisation = arch.get("specialisation") \
        if arch.get("specialisation") in rs.specialisations \
        else rng.choice(list(rs.specialisations))

    # Points libres : répartis au hasard, dans les mêmes bornes que le joueur
    budget = int(rs.origines[fiche.origine].get("stats_libres", 4))
    libres: dict[str, int] = {}
    cles_stats = list(rs.stats)
    while budget > 0:
        k = rng.choice(cles_stats)
        if libres.get(k, 0) >= 3:
            continue
        libres[k] = libres.get(k, 0) + 1
        budget -= 1
    fiche.stats_libres = libres

    fiche.mode_destinee = "aleatoire"
    return fiche


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def valider(pack: LorePack, rs: Ruleset, fiche: Fiche, annee: int) -> Fiche:
    nom = (fiche.nom or "").strip()
    if len(nom) < 2:
        raise ErreurCreation("Le nom doit faire au moins 2 caractères.")
    if len(nom) > 60:
        raise ErreurCreation("Le nom est trop long (60 caractères maximum).")
    fiche.nom = nom

    if fiche.age is not None and not (6 <= fiche.age <= 120):
        raise ErreurCreation("L'âge doit être compris entre 6 et 120 ans.")

    if not pack.village(fiche.village_id):
        raise ErreurCreation("Ce village n'existe pas dans cet univers.")

    if fiche.origine not in rs.origines:
        raise ErreurCreation(f"Origine inconnue : {fiche.origine}.")

    if fiche.origine in ("clan_connu", "clan_mineur"):
        clan = pack.clan(fiche.clan_id)
        if clan is None:
            raise ErreurCreation("Choisis un clan dans la liste.")
        rang_attendu = "majeur" if fiche.origine == "clan_connu" else "mineur"
        if clan.get("rang") != rang_attendu:
            raise ErreurCreation(
                f"« {clan['nom']} » n'est pas un clan {rang_attendu}.")
        if not pack.actif_a(clan, annee):
            raise ErreurCreation(f"Le clan {clan['nom']} n'existe pas à cette époque.")
        # Un clan appartient à un village. Seuls les clans dispersés — Uzumaki
        # après la chute d'Uzushio, survivants Kaguya — se rencontrent ailleurs.
        # Le filtre existe déjà à l'écran ; ici c'est la barrière qui tient même
        # si quelqu'un poste le formulaire à la main.
        if not clan.get("disperse") and clan.get("village") != fiche.village_id:
            village = pack.village(clan.get("village")) or {}
            raise ErreurCreation(
                f"Le clan {clan['nom']} appartient à "
                f"{village.get('nom_fr') or village.get('nom') or 'un autre village'}. "
                "Choisis un clan de ton village, ou change de village.")
    elif fiche.origine == "clan_invente":
        _valider_clan_invente(pack, rs, fiche.clan_invente)
    else:
        fiche.clan_id = ""

    if fiche.specialisation not in rs.specialisations:
        raise ErreurCreation("Choisis une spécialisation.")

    # Enveloppe de caractéristiques libres
    budget = int(rs.origines[fiche.origine].get("stats_libres", 4))
    depense = 0
    for cle, val in (fiche.stats_libres or {}).items():
        if cle not in rs.stats:
            raise ErreurCreation(f"Caractéristique inconnue : {cle}.")
        v = int(val)
        if v < 0:
            raise ErreurCreation("Une répartition ne peut pas être négative.")
        if v > 3:
            raise ErreurCreation("Maximum 3 points sur une seule caractéristique.")
        depense += v
    if depense > budget:
        raise ErreurCreation(f"{depense} points répartis pour un budget de {budget}.")

    if fiche.mode_destinee not in ("choisie", "proposee", "aleatoire"):
        raise ErreurCreation("Mode de destinée inconnu.")
    return fiche


def _valider_clan_invente(pack: LorePack, rs: Ruleset, ci: dict) -> None:
    """Le sas qui empêche l'option « clan inventé » de devenir la porte de
    service par laquelle tout l'équilibrage s'évapore."""
    lim = rs.data.get("clan_invente_limites", {})
    nom = (ci.get("nom") or "").strip()
    if len(nom) < 2:
        raise ErreurCreation("Donne un nom à ton clan.")
    if pack.resoudre_nom(nom):
        raise ErreurCreation(f"« {nom} » existe déjà dans cet univers. Choisis un autre nom.")

    bonus = {k: int(v) for k, v in (ci.get("bonus") or {}).items() if int(v)}
    if len(bonus) > int(lim.get("bonus_stats_max", 2)):
        raise ErreurCreation(
            f"Au plus {lim.get('bonus_stats_max', 2)} caractéristiques peuvent recevoir un bonus.")
    if sum(bonus.values()) > int(lim.get("bonus_total_max", 5)):
        raise ErreurCreation(
            f"Le total des bonus de clan est plafonné à {lim.get('bonus_total_max', 5)}.")
    for cle in bonus:
        if cle not in rs.stats:
            raise ErreurCreation(f"Caractéristique inconnue : {cle}.")


# --------------------------------------------------------------------------
# Dérivation des caractéristiques
# --------------------------------------------------------------------------
def apercu_stats(pack: LorePack, rs: Ruleset, fiche: Fiche,
                 traits_actifs: list | None = None) -> dict:
    """base du ruleset + bonus de clan + spécialisation + traits actifs + libres.

    Une seule implémentation, utilisée par l'aperçu du formulaire ET par
    l'écriture : aucun risque de divergence entre ce que le joueur voit et ce
    qui est enregistré.
    """
    stats = rs.stats_defaut()

    if fiche.origine == "clan_invente":
        bonus = (fiche.clan_invente or {}).get("bonus", {})
    else:
        clan = pack.clan(fiche.clan_id) or {}
        bonus = clan.get("bonus", {})
    for k, v in (bonus or {}).items():
        if k in stats:
            stats[k] += int(v)

    for k, v in (rs.specialisations.get(fiche.specialisation, {}).get("bonus", {})).items():
        if k in stats:
            stats[k] += int(v)

    for t in (traits_actifs or []):
        for k, v in (t.accorde.get("stats", {}) if hasattr(t, "accorde") else {}).items():
            if k in stats:
                stats[k] += int(v)

    for k, v in (fiche.stats_libres or {}).items():
        if k in stats:
            stats[k] += int(v)
    return stats


# --------------------------------------------------------------------------
# Écriture
# --------------------------------------------------------------------------
def creer_personnage(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                     fiche: Fiche, destinee: dst.DestineeTiree) -> Character:
    annee = pack.annee_de(camp.epoque)
    fiche = valider(pack, rs, fiche, annee)

    pjs = session.exec(select(Character).where(
        Character.campaign_id == camp.id, Character.is_pc == True)).all()  # noqa: E712
    if len(pjs) >= MAX_PJ:
        raise ErreurCreation(
            f"Cette campagne a déjà {MAX_PJ} personnages joueurs. "
            "Au-delà, une scène devient illisible.")
    tous = session.exec(select(Character).where(Character.campaign_id == camp.id)).all()
    if any(c.nom.lower() == fiche.nom.lower() for c in tous):
        raise ErreurCreation(
            f"« {fiche.nom} » existe déjà dans cette campagne. "
            "Le moteur identifie les personnages par leur nom.")

    village = pack.village(fiche.village_id) or {}
    if fiche.origine == "clan_invente":
        clan_nom = (fiche.clan_invente or {}).get("nom", "")
        clan_ref = ""
    else:
        clan = pack.clan(fiche.clan_id) or {}
        clan_nom = clan.get("nom", "Sans clan")
        clan_ref = clan.get("id", "")

    stats = apercu_stats(pack, rs, fiche, destinee.actifs)

    # Lieu de départ : celui de l'équipe s'il y en a une, sinon le plus sûr
    depart = None
    if pjs and pjs[0].location_id:
        depart = session.get(Location, pjs[0].location_id)
    if depart is None:
        lieux = session.exec(select(Location).where(
            Location.campaign_id == camp.id).order_by(Location.danger)).all()
        depart = lieux[0] if lieux else None

    pj = Character(
        campaign_id=camp.id, nom=fiche.nom, is_pc=True,
        joueur=(fiche.joueur or "Joueur").strip(),
        sexe=fiche.sexe, age=fiche.age, apparence=fiche.apparence.strip(),
        clan=clan_nom or "Sans clan", clan_ref=clan_ref,
        village=village.get("nom_fr") or village.get("nom", ""),
        village_ref=fiche.village_id,
        grade="genin", niveau=1, xp=0,
        # La spécialité était consommée puis perdue. On la garde : la fiche
        # l'affiche, et `apprentissage.py` s'en sert pour savoir ce que ce
        # personnage peut travailler seul.
        specialisation=fiche.specialisation,
        stats=stats, ressources=rs.ressources_defaut(),
        inventaire=list(rs.data.get("inventaire_depart", [])),
        location_id=depart.id if depart else None,
        source="partie", role_campagne="joueur",
    )
    session.add(pj)
    session.commit()
    session.refresh(pj)

    # --- techniques de départ, dérivées de la spécialisation
    _techniques_depart(session, camp, pack, rs, pj, fiche)

    # --- capacités dormantes accordées par les traits actifs
    for t in destinee.actifs:
        cap = t.accorde.get("capacite")
        if cap:
            session.add(CharacterCapacity(
                campaign_id=camp.id, character_id=pj.id, capacite_ref=cap, palier=0))

    # --- la fiche de destinée : écrite en entier, figée, jamais montrée
    arch = pack.archetype(fiche.archetype) or {}
    d = Destiny(campaign_id=camp.id, character_id=pj.id, mode=fiche.mode_destinee,
                profil=destinee.profil, graine=destinee.graine,
                budget_actif=destinee.budget_actif, budget_latent=destinee.budget_latent,
                archetype=arch.get("id", ""), archetype_nom=arch.get("nom", ""),
                archetype_accroche=arch.get("accroche", ""))
    session.add(d)
    session.commit()
    session.refresh(d)

    for t in destinee.traits:
        dt = DestinyTrait(
            campaign_id=camp.id, destiny_id=d.id, trait_ref=t.ref, libelle=t.libelle,
            famille=t.famille, rarete=t.rarete, cout=t.cout,
            actif_au_depart=t.actif, etat="eveille" if t.actif else "latent",
            palier_courant=1 if t.actif else 0,
            palier_max=max(1, len(t.conditions)) if not t.actif else 1,
            revelation_initiale=t.revelation_initiale, verite=t.verite,
            conditions=t.conditions, contraintes=t.contraintes,
            accorde=t.accorde.get("capacite", "") or t.accorde.get("technique", ""),
            tour_min_prochain=0,
        )
        session.add(dt)
        session.commit()
        session.refresh(dt)
        for i, cond in enumerate(t.conditions, start=1):
            if cond.get("indice"):
                session.add(DestinyClue(campaign_id=camp.id, trait_id=dt.id,
                                        palier=cond.get("palier", i), texte=cond["indice"]))

    d.presage = _presage(pack, rs, fiche, destinee)
    session.add(d)

    _recalculer_puissance(session, camp, rs, pj)
    session.commit()
    session.refresh(pj)
    return pj


def _techniques_depart(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
                       pj: Character, fiche: Fiche) -> None:
    """Deux techniques de rang E + une de rang D dans la spécialisation.
    Maîtrise initiale 40 : fonctionnelle, sans bonus — la progression se fait
    ensuite par l'usage."""
    spec = fiche.specialisation
    categorie = {"medecine": "iryo"}.get(spec, spec)

    base = [t for t in pack.techniques(rangs=["E"], clan=fiche.clan_id)
            if not t.get("clan")][:2]
    avancees = [t for t in pack.techniques(rangs=["D"], clan=fiche.clan_id)
                if t.get("categorie") == categorie]
    if not avancees:
        avancees = [t for t in pack.techniques(rangs=["D"], clan=fiche.clan_id)
                    if not t.get("clan")]
    choix = base + avancees[:1]

    # Une technique de clan, si le clan en a une accessible
    clan = pack.clan(fiche.clan_id) or {}
    for tid in (clan.get("techniques") or [])[:1]:
        t = pack.technique(tid)
        if t and t not in choix:
            choix.append(t)

    for t in choix:
        session.add(CharacterTechnique(
            campaign_id=camp.id, character_id=pj.id, technique_ref=t["id"],
            maitrise=40, appris_tour=0))
    session.commit()


def _presage(pack: LorePack, rs: Ruleset, fiche: Fiche,
             destinee: dst.DestineeTiree) -> str:
    """Le filtre le plus important du jeu.

    On ne donne au modèle QUE les champs autorisés : les traits actifs et les
    `revelation_initiale` des latents. Jamais la vérité. Un modèle à qui l'on
    confie un secret en lui demandant d'être discret ne l'est pas — il le
    laisse filtrer dans un adjectif.
    """
    village = pack.village(fiche.village_id) or {}
    clan_nom = ((fiche.clan_invente or {}).get("nom") if fiche.origine == "clan_invente"
                else (pack.clan(fiche.clan_id) or {}).get("nom", "")) or "sans clan"

    lignes = [
        f"Personnage : {fiche.nom}, {fiche.age or '?'} ans",
        f"Village : {village.get('nom_fr', village.get('nom', ''))}",
        f"Origine : {clan_nom}",
        f"Spécialisation : {rs.specialisations.get(fiche.specialisation, {}).get('label', '')}",
    ]
    if fiche.apparence:
        lignes.append(f"Apparence : {fiche.apparence}")

    lignes.append("\nÉléments observables à évoquer :")
    for t in destinee.actifs:
        if t.revelation_initiale:
            lignes.append(f"- {t.revelation_initiale}")
    for t in destinee.latents:
        if t.revelation_initiale:
            lignes.append(f"- {t.revelation_initiale}")

    return get_llm().text(PRESAGE, "\n".join(lignes), temperature=0.9)


def _recalculer_puissance(session: Session, camp: Campaign, rs: Ruleset,
                          perso: Character) -> None:
    """Recalcule PE et tier depuis la fiche. Appelé après chaque changement
    qui influence la puissance."""
    from app.models import Condition

    techs = session.exec(select(CharacterTechnique).where(
        CharacterTechnique.character_id == perso.id)).all()
    caps = session.exec(select(CharacterCapacity).where(
        CharacterCapacity.character_id == perso.id)).all()
    conds = session.exec(select(Condition).where(
        Condition.character_id == perso.id)).all()

    pack = None
    try:
        pack = __import__("app.lore.pack", fromlist=["charger"]).charger(camp.lore_pack)
    except Exception:  # noqa: BLE001 — la puissance ne doit jamais bloquer le jeu
        pass

    techniques = []
    for ct in techs:
        t = pack.technique(ct.technique_ref) if pack else None
        techniques.append({"rang": (t or {}).get("rang", "E"), "maitrise": ct.maitrise})

    capacites = []
    for cc in caps:
        lg = pack.lignee(cc.capacite_ref) if pack else None
        apport = 0
        if lg and cc.palier > 0:
            paliers = lg.get("paliers", [])
            if cc.palier <= len(paliers):
                apport = paliers[cc.palier - 1].get("apport", 0)
        capacites.append({"apport": apport, "actif": not cc.scellee})

    conditions = [{"effets": c.effets} for c in conds]

    res = rs.puissance(perso.stats, techniques, perso.niveau, capacites, conditions)
    perso.pe = res["pe"]
    perso.tier = res["tier"]
    perso.fiche_hash = fiche_hash(
        perso.stats, perso.niveau,
        [t.technique_ref for t in techs], [c.capacite_ref for c in caps],
        [c.code for c in conds])
    session.add(perso)
