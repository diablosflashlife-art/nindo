"""Moteur de règles piloté par les données.

Le moteur ne connaît pas Naruto : il sait lire un ruleset et en dériver des
jets, de la puissance et de la progression. C'est la seule partie du projet
qui n'a besoin ni de base, ni de réseau, ni de modèle — donc la seule
testable à 100 %.

Les formules viennent du YAML et sont évaluées par un AST restreint
(simpleeval), JAMAIS par eval().
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from simpleeval import EvalWithCompoundTypes

DICE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.IGNORECASE)


def roll_dice(expr: str, rng: random.Random | None = None) -> tuple[int, list[int]]:
    rng = rng or random
    m = DICE_RE.match(expr.strip())
    if not m:
        raise ValueError(f"Expression de dé invalide : {expr!r}")
    count = int(m.group(1) or 1)
    faces = int(m.group(2))
    modif = int(m.group(3) or 0)
    jets = [rng.randint(1, faces) for _ in range(count)]
    return sum(jets) + modif, jets


@dataclass
class CheckResult:
    stat: str
    difficulte: int
    de: int
    des: list[int]
    stat_valeur: int
    bonus: int
    total: int
    marge: int
    issue: str          # echec_critique|echec|reussite_partielle|reussite|reussite_critique
    reussi: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OpposeResult:
    """Jet opposé : les deux camps lancent, c'est l'écart qui compte.

    Un jet contre une difficulté fixe ne sait pas dire « il a paré de
    justesse » — or c'est la seule information intéressante d'un échange.
    """

    stat_a: str
    stat_b: str
    de_a: int
    de_b: int
    total_a: int
    total_b: int
    marge: int          # total_a - total_b, du point de vue de l'attaquant
    issue: str          # touche_net | touche | effleure | pare | contre
    touche: bool
    critique: bool

    def to_dict(self) -> dict:
        return asdict(self)


# Les tables dont les clés sont des ENTIERS dans le YAML. Une campagne conserve
# une copie de son ruleset dans une colonne JSON ; or JSON n'a pas d'autre type
# de clé que le texte. Au rechargement, `fosse[2]` et `tiers[4]` ne trouvaient
# donc plus rien — silencieusement, avec un repli qui rendait la règle du fossé
# inopérante sur toute partie sauvegardée. On normalise à la construction,
# c'est-à-dire au seul endroit par lequel tout le monde passe.
TABLES_A_CLES_ENTIERES = ("fosse", "tiers")


@dataclass
class Ruleset:
    data: dict
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self) -> None:
        for nom in TABLES_A_CLES_ENTIERES:
            table = self.data.get(nom)
            if not isinstance(table, dict):
                continue
            self.data[nom] = {
                (int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k): v
                for k, v in table.items()
            }

    # ---------------------------------------------------------------- accès
    @property
    def stats(self) -> dict:
        return self.data.get("stats", {})

    @property
    def difficultes(self) -> dict:
        return self.data.get("difficulties", {"normal": 12})

    @property
    def origines(self) -> dict:
        return self.data.get("origines", {})

    @property
    def specialisations(self) -> dict:
        return self.data.get("specialisations", {})

    @property
    def destinee(self) -> dict:
        return self.data.get("destinee", {})

    @property
    def grades(self) -> list[dict]:
        return self.data.get("grades", [])

    def grade(self, gid: str) -> dict | None:
        return next((g for g in self.grades if g["id"] == gid), None)

    def stats_defaut(self) -> dict:
        return {k: v.get("default", 10) for k, v in self.stats.items()}

    def ressources_defaut(self) -> dict:
        return {k: v.get("default", 100) for k, v in self.data.get("resources", {}).items()}

    def valeur_difficulte(self, label: str | int) -> int:
        if isinstance(label, int):
            return label
        return int(self.difficultes.get(str(label).lower(), self.difficultes.get("normal", 12)))

    # ------------------------------------------------------- évaluation sûre
    def _eval(self, formule: str, noms: dict[str, Any]) -> float:
        ev = EvalWithCompoundTypes(
            names=noms,
            functions={"min": min, "max": max, "abs": abs, "int": int, "round": round},
        )
        return ev.eval(formule)

    # ----------------------------------------------------------------- jets
    def check(self, stats: dict, stat: str, difficulte: str | int = "normal",
              bonus: int = 0) -> CheckResult:
        """Le MJ choisit la stat et la difficulté ; le RÉSULTAT est calculé ici
        et ne lui est pas négociable. C'est ce qui rend l'échec possible."""
        cfg = self.data.get("check", {})
        dc = self.valeur_difficulte(difficulte)
        de, des = roll_dice(cfg.get("dice", "1d20"), self.rng)
        valeur = int(stats.get(stat, self.stats.get(stat, {}).get("default", 10)))

        total = int(self._eval(
            cfg.get("formula", "roll + (stat_value - 10) // 2 + bonus"),
            {"roll": de, "stat_value": valeur, "bonus": bonus, "dc": dc},
        ))
        marge = total - dc

        naturel = des[0] if des else de
        if naturel <= cfg.get("crit_fail_on", 1):
            issue = "echec_critique"
        elif naturel >= cfg.get("crit_success_on", 20):
            issue = "reussite_critique"
        elif marge >= 0:
            issue = "reussite"
        elif marge >= -cfg.get("partial_margin", 3):
            issue = "reussite_partielle"
        else:
            issue = "echec"

        return CheckResult(
            stat=stat, difficulte=dc, de=de, des=des, stat_valeur=valeur, bonus=bonus,
            total=total, marge=marge, issue=issue,
            reussi=issue in ("reussite", "reussite_critique", "reussite_partielle"),
        )

    # ------------------------------------------------------------ puissance
    def puissance(self, stats: dict, techniques: list[dict], niveau: int,
                  capacites: list[dict] | None = None,
                  conditions: list[dict] | None = None) -> dict:
        """Puissance effective, avec le détail de ses composants.

        `techniques` : [{rang, maitrise}]  —  `capacites` : [{apport, actif}]
        On stocke les composants séparément pour pouvoir répondre plus tard à
        « pourquoi ce PNJ est-il tier 5 ? » sans refaire le calcul à la main.
        """
        cfg = self.data.get("puissance", {})
        poids = cfg.get("socle_poids", {})
        total_poids = sum(poids.values()) or 1

        socle = sum(float(stats.get(k, 10)) * p for k, p in poids.items()) / total_poids
        c_socle = socle * cfg.get("socle_facteur", 4.0)

        # Arsenal : les 5 meilleures, à poids dégressif, pondérées par la maîtrise.
        # Sans ça, quarante techniques de rang E battraient un Kage.
        rangs = cfg.get("arsenal_rangs", {})
        degressif = cfg.get("arsenal_degressif", [1.0])
        plancher = cfg.get("arsenal_maitrise_plancher", 0.4)
        valeurs = sorted(
            (rangs.get(t.get("rang", "E"), 1)
             * (plancher + (1 - plancher) * (t.get("maitrise", 40) / 100))
             for t in techniques),
            reverse=True,
        )
        c_arsenal = sum(v * (degressif[i] if i < len(degressif) else 0)
                        for i, v in enumerate(valeurs))

        c_experience = niveau * cfg.get("experience_niveau", 0.8)

        # Une capacité n'apporte que si elle est éveillée et utilisable
        c_capacites = sum(float(c.get("apport", 0)) for c in (capacites or [])
                          if c.get("actif", True))

        # Les coûts persistants font BAISSER la puissance : le Mangekyô se paie
        malus = sum(float(c.get("effets", {}).get("puissance", 0))
                    for c in (conditions or []))

        pe = max(0.0, c_socle + c_arsenal + c_experience + c_capacites + malus)
        return {
            "pe": round(pe, 1),
            "tier": self.tier(pe),
            "c_socle": round(c_socle, 1),
            "c_arsenal": round(c_arsenal, 1),
            "c_experience": round(c_experience, 1),
            "c_capacites": round(c_capacites, 1),
            "malus": round(malus, 1),
        }

    def tier(self, pe: float) -> int:
        seuils = self.data.get("puissance", {}).get("seuils_tier", [0])
        niveau = 1
        for i, s in enumerate(seuils):
            if pe >= s:
                niveau = i + 1
        return max(1, min(7, niveau))

    def tier_label(self, tier: int) -> str:
        return self.data.get("tiers", {}).get(tier, {}).get("nom", f"tier {tier}")

    # -------------------------------------------------------- règle du fossé
    def fosse(self, tier_a: int, tier_b: int) -> dict:
        """Arbitrage d'un affrontement. Au-delà d'un tier d'écart, le moteur
        ne lance PAS les dés : il impose les objectifs réellement ouverts."""
        ecart = abs(tier_a - tier_b)
        table = self.data.get("fosse", {})
        cle = min(ecart, 3)
        regle = dict(table.get(cle, table.get(0, {"jet": True})))
        regle["ecart"] = ecart
        regle["superieur"] = "a" if tier_a > tier_b else ("b" if tier_b > tier_a else None)
        return regle

    def leviers(self) -> dict:
        return self.data.get("leviers", {})

    def levier(self, code: str) -> dict | None:
        return self.leviers().get(code)

    def ampleur_leviers(self, codes: list[str]) -> int:
        """Ce que les leviers valent RÉELLEMENT, plafond compris.

        Le plafond est la règle qui empêche d'empiler cinq avantages pour
        rendre un Kage abordable : au-delà, accumuler ne sert plus à rien, et
        c'est dit au joueur.
        """
        table = self.leviers()
        brut = sum(int(table.get(c, {}).get("ampleur", 0)) for c in dict.fromkeys(codes))
        return min(brut, int(self.data.get("leviers_cumul_max", 3)))

    def fosse_effectif(self, tier_joueur: int, tier_adverse: int,
                       leviers: list[str] | None = None) -> dict:
        """La règle du fossé appliquée à la puissance EN SITUATION.

        C'est le seul endroit où les leviers servent à quelque chose, et c'est
        tout leur intérêt : ils ne donnent pas un bonus au dé, ils réduisent
        l'écart. Trois leviers transforment « ce n'est plus un combat » en
        « difficile mais possible » — ce qui est exactement la promesse du
        genre.
        """
        gain = self.ampleur_leviers(leviers or [])
        ecart_brut = tier_adverse - tier_joueur
        if ecart_brut > 0:
            # Les leviers ramènent « ce n'est plus un combat » à « difficile
            # mais possible », et PAS plus loin — c'est ce que dit la table du
            # fossé elle-même : « vaincre exige de ramener l'écart à 1 ».
            # Sans ce plancher, trois manœuvres réussies permettaient à une
            # équipe de genin d'abattre un ANBU, ce qui vide la règle de sens.
            plancher = tier_joueur + (1 if ecart_brut >= 2 else 0)
            effectif = max(plancher, tier_adverse - gain)
        else:
            # Ils ne servent jamais à creuser un écart déjà favorable.
            effectif = tier_adverse
        regle = self.fosse(tier_joueur, effectif)
        regle["tier_adverse_brut"] = tier_adverse
        regle["tier_adverse_effectif"] = max(1, effectif)
        regle["gain_leviers"] = gain
        regle["a_l_avantage"] = tier_joueur > tier_adverse
        return regle

    # ----------------------------------------------------------- combat
    @property
    def combat(self) -> dict:
        return self.data.get("combat", {})

    @property
    def postures(self) -> dict:
        return self.combat.get("postures", {})

    def posture(self, nom: str) -> dict:
        p = self.postures
        return dict(p.get(nom) or p.get("mesuree") or {})

    def oppose(self, stats_a: dict, stat_a: str, stats_b: dict, stat_b: str,
               bonus_a: int = 0, bonus_b: int = 0) -> OpposeResult:
        """Un échange d'armes : deux jets, une marge, cinq issues possibles."""
        cfg = self.combat.get("oppose", {})
        formule = cfg.get("formule", self.data.get("check", {}).get(
            "formula", "roll + (stat_value - 10) // 2 + bonus"))
        des = cfg.get("dice", "1d20")

        de_a, _ = roll_dice(des, self.rng)
        de_b, _ = roll_dice(des, self.rng)
        va = int(stats_a.get(stat_a, self.stats.get(stat_a, {}).get("default", 10)))
        vb = int(stats_b.get(stat_b, self.stats.get(stat_b, {}).get("default", 10)))
        ta = int(self._eval(formule, {"roll": de_a, "stat_value": va,
                                      "bonus": bonus_a, "dc": 0}))
        tb = int(self._eval(formule, {"roll": de_b, "stat_value": vb,
                                      "bonus": bonus_b, "dc": 0}))
        marge = ta - tb

        crit = de_a >= int(self.data.get("check", {}).get("crit_success_on", 20))
        rate = de_a <= int(self.data.get("check", {}).get("crit_fail_on", 1))
        if rate:
            issue = "contre"
        elif crit or marge >= 8:
            issue = "touche_net"
        elif marge >= 3:
            issue = "touche"
        elif marge >= 0:
            issue = "effleure"
        elif marge >= -6:
            issue = "pare"
        else:
            issue = "contre"

        return OpposeResult(
            stat_a=stat_a, stat_b=stat_b, de_a=de_a, de_b=de_b,
            total_a=ta, total_b=tb, marge=marge, issue=issue,
            touche=issue in ("touche_net", "touche", "effleure"), critique=crit)

    def degats(self, marge: int, arme: int = 0, posture: int = 0,
               issue: str = "") -> int:
        """Les dégâts d'un coup porté, pondérés par la NETTETÉ de la touche.

        Sans ce facteur par issue, effleurer coûtait presque aussi cher
        qu'ouvrir une garde, et un duel entre pairs se terminait en deux
        échanges — ce qui vidait les postures de tout intérêt.
        """
        cfg = self.combat.get("degats", {})
        roll, _ = roll_dice(cfg.get("dice", "1d6"), self.rng)
        valeur = self._eval(
            cfg.get("formule", "max(1, roll + marge // 4 + arme + posture)"),
            {"roll": roll, "marge": marge, "arme": arme, "posture": posture})
        facteur = float((cfg.get("par_issue") or {}).get(issue, 1.0))
        return max(1, int(round(float(valeur) * facteur)))

    def etats_combat(self) -> list[dict]:
        return list(self.combat.get("etats", []) or [])

    def etat_franchi(self, pv_avant: int, pv_apres: int, pv_max: int) -> dict | None:
        """Le seuil de blessure franchi par ce coup-ci, s'il y en a un.

        On ne réapplique jamais un état déjà acquis : ce sont les
        FRANCHISSEMENTS qui blessent, pas le fait de rester sous le seuil.
        """
        if pv_max <= 0:
            return None
        avant = pv_avant * 100 / pv_max
        apres = pv_apres * 100 / pv_max
        franchis = [e for e in self.etats_combat()
                    if apres < float(e.get("sous", 0)) <= avant]
        return max(franchis, key=lambda e: e.get("severite", 0)) if franchis else None

    def malus_etats(self, codes: list[str]) -> int:
        """Ce que les blessures coûtent au dé. Cumulatif et négatif."""
        table = {e.get("code"): e for e in self.etats_combat()}
        return sum(int(table.get(c, {}).get("jet", 0)) for c in codes)

    def chance_rupture(self, pourcent_pv: float, ecart_tier: int,
                       moral: str = "") -> float:
        """Probabilité qu'un PNJ rompe le combat. Personne ne se bat jusqu'à la
        mort par défaut : un adversaire qui s'enfuit peut revenir, et c'est
        meilleur pour la campagne qu'un cadavre de plus."""
        cfg = self.combat.get("moral", {})
        if moral and moral in (cfg.get("jamais_si") or []):
            return 0.0
        if pourcent_pv > float(cfg.get("seuil_pv", 35)):
            return 0.0
        base = float(cfg.get("base", 35))
        base += max(0, ecart_tier) * float(cfg.get("par_tier_inferieur", 25))
        return max(0.0, min(95.0, base)) / 100.0

    # ------------------------------------------------- fabrique d'adversaire
    def stats_pour_tier(self, tier: int, accents: list[str] | None = None) -> dict:
        """Des caractéristiques qui atteignent RÉELLEMENT le tier demandé.

        Écrire des fiches d'adversaires à la main produit, à la première
        retouche de `seuils_tier`, un bestiaire entier faux. Ici on part du
        socle et on monte jusqu'à ce que le tier visé soit atteint : la seule
        source de vérité reste la table de tiers.
        """
        cfg = self.data.get("adversaires", {})
        pas = int(cfg.get("pas_montee", 2))
        niveau = self.niveau_pour_tier(tier)
        stats = self.stats_defaut()
        cles = [a for a in (accents or []) if a in stats] or list(stats)

        for i in range(int(cfg.get("iterations_max", 40))):
            if self.tier(self.puissance(stats, [], niveau)["pe"]) >= tier:
                break
            cle = cles[i % len(cles)]
            stats[cle] = int(stats[cle]) + pas
        return stats

    def niveau_pour_tier(self, tier: int) -> int:
        table = self.data.get("adversaires", {}).get("niveau_par_tier", [1])
        return int(table[min(max(tier, 1), len(table)) - 1])

    def ressources_pour_tier(self, tier: int) -> dict:
        facteurs = self.data.get("adversaires", {}).get(
            "ressources_facteur_tier", [1.0])
        f = float(facteurs[min(max(tier, 1), len(facteurs)) - 1])
        return {k: max(1, int(round(v * f)))
                for k, v in self.ressources_defaut().items()}

    # ------------------------------------------------------------- maîtrise
    def palier_maitrise(self, maitrise: int) -> dict:
        paliers = self.data.get("maitrise", {}).get("paliers", [])
        courant = paliers[0] if paliers else {}
        for p in paliers:
            if maitrise >= p.get("min", 0):
                courant = p
        return courant

    # ------------------------------------------------------------ éléments
    def avantage_element(self, attaquant: str | None, defenseur: str | None) -> int:
        """Le cycle est une RÈGLE, pas un catalogue de vingt paires."""
        cfg = self.data.get("elements", {})
        cycle = cfg.get("cycle", [])
        if not attaquant or not defenseur or attaquant not in cycle or defenseur not in cycle:
            return 0
        i, j = cycle.index(attaquant), cycle.index(defenseur)
        if (i + 1) % len(cycle) == j:
            return cfg.get("bonus_dominant", 3)
        if (j + 1) % len(cycle) == i:
            return cfg.get("malus_domine", -3)
        return 0

    # ---------------------------------------------------------- progression
    def xp_pour_niveau(self, niveau: int) -> int:
        f = self.data.get("progression", {}).get("xp_curve", "100 * level ** 1.6")
        return int(self._eval(f, {"level": niveau}))

    def appliquer_xp(self, niveau: int, xp: int, gagne: int) -> tuple[int, int, int]:
        xp += gagne
        gagnes = 0
        while xp >= self.xp_pour_niveau(niveau):
            xp -= self.xp_pour_niveau(niveau)
            niveau += 1
            gagnes += 1
        return niveau, xp, gagnes

    def titre_grade(self, grade: str) -> str:
        g = self.grade(grade)
        return g["nom"] if g else grade

    def droits(self, grade: str) -> dict:
        g = self.grade(grade)
        return dict(g.get("droits", {})) if g else {}


def fiche_hash(stats: dict, niveau: int, techniques: list, capacites: list,
               conditions: list) -> str:
    """Empreinte des champs qui influencent la puissance. Trop large, on
    recalcule pour rien ; trop étroite, le tier ne suit plus la progression."""
    charge = json.dumps(
        {"s": stats, "n": niveau, "t": sorted(map(str, techniques)),
         "c": sorted(map(str, capacites)), "e": sorted(map(str, conditions))},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha1(charge.encode("utf-8")).hexdigest()[:16]
