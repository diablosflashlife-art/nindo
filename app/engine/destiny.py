"""Générateur de destinée.

Principe : la destinée est un SECRET dont le sujet est le personnage du joueur.
Elle est écrite intégralement à la création, figée, et révélée par paliers.
Un modèle qui improvise un passé en invente un différent à chaque séance.

Deux budgets :
  actif  — ce que le personnage possède au jour 1 (traits communs seulement)
  latent — ce qui est écrit dans sa destinée et s'éveillera en jeu

Un trait de rareté `rare` ou plus ne peut JAMAIS être actif à la création.
C'est la règle qui interdit « Mangekyô + Rinnegan + bijû » dès le départ.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.lore.pack import LorePack
from app.rules.engine import Ruleset


@dataclass
class TraitTire:
    ref: str
    libelle: str
    famille: str
    rarete: str
    cout: int
    actif: bool
    revelation_initiale: str = ""
    verite: str = ""
    conditions: list = field(default_factory=list)
    contraintes: list = field(default_factory=list)
    accorde: dict = field(default_factory=dict)


@dataclass
class DestineeTiree:
    profil: str
    profil_nom: str
    traits: list[TraitTire]
    graine: int
    budget_actif: int
    budget_latent: int
    depense_actif: int
    depense_latent: int

    @property
    def actifs(self) -> list[TraitTire]:
        return [t for t in self.traits if t.actif]

    @property
    def latents(self) -> list[TraitTire]:
        return [t for t in self.traits if not t.actif]


# --------------------------------------------------------------------------
# Éligibilité
# --------------------------------------------------------------------------
def _eligible(trait: dict, *, origine: str, clan_id: str, village_id: str,
              annee: int, pack: LorePack, deja: set[str]) -> bool:
    """Filtre de cohérence. Un trait non éligible n'entre jamais dans le vivier —
    ni au tirage, ni en destinée choisie."""
    elig = trait.get("eligibilite", {}) or {}

    if elig.get("clans") and clan_id not in elig["clans"]:
        return False
    if elig.get("villages") and village_id not in elig["villages"]:
        return False
    if elig.get("origines") and origine not in elig["origines"]:
        return False
    if elig.get("epoques"):
        # bornes d'époque exprimées en identifiants
        ids = [e["id"] for e in pack.epoques
               if e["annee"] <= annee < e["annee"] + e.get("duree", 999)]
        if not set(ids) & set(elig["epoques"]):
            return False
    if elig.get("incompatible") and deja & set(elig["incompatible"]):
        return False
    if elig.get("requiert_trait") and elig["requiert_trait"] not in deja:
        return False

    # Une lignée de clan n'est accessible qu'aux membres du clan concerné
    if trait.get("famille") == "lignee_clan" and not elig.get("clans"):
        return False
    return True


def _poids(trait: dict, rs: Ruleset, clan_id: str) -> float:
    """Pondération, puis repondération par l'origine.

    C'est l'étape qui décide de tout : elle fait qu'un Uchiha a des chances
    réelles d'éveiller un Sharingan sans que ce soit acquis, et qu'un sans-clan
    ne peut pas en éveiller un par hasard.
    """
    cfg = rs.destinee
    rar = cfg.get("raretes", {}).get(trait.get("rarete", "commun"), {})
    poids = float(rar.get("poids", 100))

    elig = trait.get("eligibilite", {}) or {}
    if trait.get("famille") == "lignee_clan":
        if clan_id and clan_id in (elig.get("clans") or []):
            poids *= cfg.get("clan_multiplicateur", 20)
        else:
            poids *= cfg.get("hors_clan_multiplicateur", 0)
    return poids


def _to_trait(trait: dict, rs: Ruleset, actif: bool) -> TraitTire:
    rar = trait.get("rarete", "commun")
    cout = int(rs.destinee.get("raretes", {}).get(rar, {}).get("cout", 1))
    return TraitTire(
        ref=trait["id"], libelle=trait.get("nom", trait["id"]),
        famille=trait.get("famille", ""), rarete=rar, cout=cout, actif=actif,
        revelation_initiale=trait.get("revelation_initiale", ""),
        verite=trait.get("verite", "") or trait.get("revelation_initiale", ""),
        conditions=list(trait.get("conditions", []) or []),
        contraintes=list(trait.get("contraintes", []) or []),
        accorde=dict(trait.get("accorde", {}) or {}),
    )


# --------------------------------------------------------------------------
# Tirage
# --------------------------------------------------------------------------
AFFINITES_ACTIVES_MAX = 1
AFFINITES_MAX = 2


def generer(pack: LorePack, rs: Ruleset, *, origine: str, clan_id: str,
            village_id: str, specialisation: str, annee: int,
            graine: int | None = None, traits_forces: list[str] | None = None,
            profil_force: str | None = None) -> DestineeTiree:
    """Tire une destinée.

    `traits_forces` : des traits latents imposés AVANT le tirage — c'est ce
    que fait un archétype de « je m'en remets au destin ». Ils passent le
    filtre d'éligibilité (un sans-clan n'a pas de Sharingan, archétype ou pas)
    mais pas le filtre de budget : un jinchûriki coûte plus que tout le budget
    latent d'un sans-clan, et c'est précisément le point.
    """
    rng = random.Random(graine if graine is not None else random.randrange(1, 10**9))
    graine_utilisee = graine if graine is not None else rng.randrange(1, 10**9)
    rng = random.Random(graine_utilisee)

    cfg = rs.destinee
    org = rs.origines.get(origine, {})
    budget_actif = int(org.get("budget_actif", 3))
    budget_latent = int(org.get("budget_latent", 12))

    # --- 1. profil : c'est la silhouette qu'on remarque en jeu, pas la liste
    profils = [p for p in pack.profils()
               if not p.get("origines") or origine in p["origines"]]
    if not profils:
        profils = pack.profils()
    profil = next((p for p in pack.profils() if p["id"] == profil_force), None) \
        if profil_force else None
    if profil is None:
        profil = rng.choices(profils, weights=[p.get("poids", 100) for p in profils])[0]
    familles_favorisees = set(profil.get("familles", []))
    spec_favorise = set(rs.specialisations.get(specialisation, {}).get("favorise", []))

    choisis: list[TraitTire] = []
    deja: set[str] = set()

    def nb_affinites() -> int:
        return sum(1 for r in deja if r.startswith("affinite_"))

    def vivier(actif_seulement: bool) -> list[dict]:
        out = []
        for t in pack.traits():
            if t["id"] in deja:
                continue
            # Un genin a UNE nature de chakra, rarement deux. Sans ce plafond,
            # un personnage sur deux naissait avec trois affinités actives et
            # les deux autres en réserve : la destinée se noyait dans une
            # liste d'éléments au lieu de porter un secret.
            if t["id"].startswith("affinite_") and \
                    nb_affinites() >= (AFFINITES_ACTIVES_MAX if actif_seulement
                                       else AFFINITES_MAX):
                continue
            if t["id"] == "seconde_affinite" and nb_affinites() >= AFFINITES_MAX:
                continue
            rar = t.get("rarete", "commun")
            peut_actif = cfg.get("raretes", {}).get(rar, {}).get("actif_possible", False)
            if actif_seulement and not peut_actif:
                continue
            if not _eligible(t, origine=origine, clan_id=clan_id, village_id=village_id,
                             annee=annee, pack=pack, deja=deja):
                continue
            out.append(t)
        return out

    def tirer(candidats: list[dict], budget_restant: int) -> dict | None:
        eligibles = [t for t in candidats
                     if int(cfg["raretes"].get(t.get("rarete", "commun"), {})
                            .get("cout", 1)) <= budget_restant]
        if not eligibles:
            return None
        poids = []
        for t in eligibles:
            p = _poids(t, rs, clan_id)
            if t.get("famille") in familles_favorisees:
                p *= 3
            if t.get("famille") in spec_favorise:
                p *= 2
            poids.append(max(p, 0.0001))
        return rng.choices(eligibles, weights=poids)[0]

    # --- 2. budget actif : traits communs uniquement
    reste = budget_actif
    for _ in range(8):
        if reste <= 0:
            break
        t = tirer(vivier(actif_seulement=True), reste)
        if t is None:
            break
        tt = _to_trait(t, rs, actif=True)
        choisis.append(tt)
        deja.add(t["id"])
        reste -= tt.cout
    depense_actif = budget_actif - reste

    # --- 2 bis. les traits imposés par l'archétype : latents, hors budget,
    # mais jamais hors éligibilité. Leurs prérequis sont ajoutés à l'étape 4.
    reste_l = budget_latent

    def imposer(ref: str, actif: bool) -> bool:
        nonlocal reste_l
        t = pack.trait(ref)
        if t is None or ref in deja:
            return ref in deja
        # Le prérequis d'abord — Hyôton demande Suiton — et s'il n'est pas
        # tenable, le trait imposé ne l'est pas non plus.
        req = (t.get("eligibilite", {}) or {}).get("requiert_trait")
        if req and req not in deja and not imposer(req, actif=True):
            return False
        if not _eligible(t, origine=origine, clan_id=clan_id, village_id=village_id,
                         annee=annee, pack=pack, deja=deja):
            return False
        tt = _to_trait(t, rs, actif=actif)
        if not actif:
            tt.palier_max = max(1, len(tt.conditions))
        choisis.append(tt)
        deja.add(ref)
        reste_l -= tt.cout
        return True

    for ref in (traits_forces or []):
        imposer(ref, actif=False)

    # --- 3. budget latent : jusqu'à consommer au moins 70 %
    cible = budget_latent * float(cfg.get("latent_min_consomme", 0.7))
    legendaires = 0
    for _ in range(12):
        if budget_latent - reste_l >= cible and rng.random() < 0.35:
            break
        if reste_l <= 0:
            break
        candidats = vivier(actif_seulement=False)
        if legendaires >= int(cfg.get("legendaire_max", 1)):
            candidats = [c for c in candidats if c.get("rarete") != "legendaire"]
        t = tirer(candidats, reste_l)
        if t is None:
            break
        tt = _to_trait(t, rs, actif=False)
        tt.palier_max = max(1, len(tt.conditions))
        choisis.append(tt)
        deja.add(t["id"])
        reste_l -= tt.cout
        if tt.rarete == "legendaire":
            legendaires += 1

    # --- 4. cohérence : ajouter les prérequis manquants s'ils tiennent au budget
    for tt in list(choisis):
        t = pack.trait(tt.ref) or {}
        req = (t.get("eligibilite", {}) or {}).get("requiert_trait")
        if req and req not in deja:
            src = pack.trait(req)
            if src:
                add = _to_trait(src, rs, actif=True)
                choisis.insert(0, add)
                deja.add(req)

    return DestineeTiree(
        profil=profil["id"], profil_nom=profil.get("nom", profil["id"]),
        traits=choisis, graine=graine_utilisee,
        budget_actif=budget_actif, budget_latent=budget_latent,
        depense_actif=depense_actif, depense_latent=budget_latent - reste_l,
    )


def proposer(pack: LorePack, rs: Ruleset, *, nombre: int = 3, **kwargs) -> list[DestineeTiree]:
    """Mode « destinée proposée » : plusieurs avenirs distincts, dont le joueur
    ne comprend que la moitié. Le meilleur défaut — il donne l'agentivité sans
    tuer la découverte.

    Les propositions sont contraintes à différer : profils distincts, et au plus
    une seule comportant un trait de rareté élevée.
    """
    base = kwargs.pop("graine", None) or random.randrange(1, 10**9)
    out: list[DestineeTiree] = []
    profils_vus: set[str] = set()
    riches = 0
    essais = 0
    while len(out) < nombre and essais < nombre * 12:
        essais += 1
        d = generer(pack, rs, graine=base + essais * 7919, **kwargs)
        if d.profil in profils_vus:
            continue
        rare = any(t.rarete in ("tres_rare", "legendaire") for t in d.traits)
        if rare and riches >= 1:
            continue
        profils_vus.add(d.profil)
        riches += 1 if rare else 0
        out.append(d)
    while len(out) < nombre:            # repli si le vivier est trop étroit
        out.append(generer(pack, rs, graine=base + len(out) * 104729, **kwargs))
    return out
