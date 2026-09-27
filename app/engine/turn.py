"""La boucle de tour — le cœur du moteur.

    1. CONTEXTE       ce que le MJ a le droit de savoir, dimensionné par rôle
    2. INTERPRÉTATION le modèle traduit l'action libre en intention (JSON)
    3. RÉSOLUTION     Python lance les dés — pas le modèle
    4. NARRATION      le modèle raconte un résultat DÉJÀ FIXÉ
    5. CONSÉQUENCES   le modèle propose des deltas, validés puis appliqués
    5bis RÉVÉLATION   la destinée, les secrets et la connaissance avancent
    5ter PEUPLEMENT   un inconnu croisé devient un personnage figé
    6. MÉMOIRE        journal, faits, résumé périodique, mission de relais
    7. PROPOSITIONS   des amorces d'action, jamais des rails

Séparer 3 et 4 est ce qui distingue un moteur de jeu d'un chatbot déguisé :
un appel unique connaîtrait le résultat avant de le raconter, donc
l'arrangerait. Ici l'échec est réellement possible.

DEUX RÉGIMES POUR L'ÉTAPE 3. Hors combat, un jet contre une difficulté. En
combat, un ÉCHANGE : voir `app/engine/combat.py`. Le reste de la boucle ne
change pas — même contexte, même narrateur, mêmes conséquences validées — ce
qui garantit qu'un affrontement reste une scène de la même campagne, et non un
mini-jeu greffé à côté.

UN CONTEXTE PAR RÔLE. Les quatre appels recevaient tous le contexte complet —
quatre fois quatre mille jetons à traiter, sans réutilisation de cache possible
puisque le prompt système change à chaque appel. L'arbitre a besoin de la fiche
et du répertoire de techniques ; le simulateur de conséquences n'a besoin que de
la liste des entités qu'il a le droit de citer. Voir `SECTIONS_PAR_ROLE`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.config import settings
from app.engine import combat
from app.engine import liens
from app.engine import missions as gen_missions
from app.engine import monde, reprise, reveal, rythme, secrets
from app.engine.validators import valider_consequences
from app.llm.prompts import (ARBITRE, ARBITRE_COMBAT, CONSEQUENCES, NARRATEUR,
                             NARRATEUR_COMBAT, RESUMEUR)
from app.llm.provider import achever, epurer, get_llm
from app.llm.schemas import CONSEQUENCES as SCHEMA_CONSEQUENCES
from app.llm.schemas import INTENT, INTENT_COMBAT
from app.lore.pack import LorePack
from app.memory.context import construire, taille_estimee, vectoriser
from app.models import (Campaign, Character, CharacterTechnique, Encounter, Event,
                        Location, MemoryFact, Quest, Relation, Summary, Turn,
                        maintenant)
from app.rules.engine import Ruleset


@dataclass
class Preparation:
    """Tout ce qui est décidé AVANT que le narrateur écrive.

    Exister comme objet est ce qui permet de servir la narration en flux sans
    dupliquer la boucle : `preparer` fait les étapes 1bis à 3, l'appelant
    obtient le texte comme il veut — d'un bloc ou morceau par morceau — et
    `conclure` reprend à l'étape 5. Deux chemins, une seule implémentation du
    jeu.
    """

    action: str
    intent: dict
    bloc: str                       # le résultat mécanique, non négociable
    resolution: dict
    systeme: str                    # le prompt du narrateur, selon le régime
    contexte_narrateur: str
    effets_combat: list
    liens_techniques: list
    en_combat: bool
    registre: str = "ordinaire"     # le souffle de la scène, voir rythme.py
    entre_deux: bool = False        # aucune mission, aucun affrontement
    allure: str = rythme.ALLURE_DEFAUT  # court | normal | long, choisi par la table
    autres_pj: list = field(default_factory=list)  # noms des autres joueurs présents
    groupe: bool = False            # tour de table : plusieurs joueurs ont déclaré
    facteur_longueur: float = 1.0   # une scène à plusieurs a besoin de plus de place
    participants: list = field(default_factory=list)  # ids des joueurs du tour

    def invite(self) -> str:
        return (f"{self.contexte_narrateur}\n\n### ACTION DU JOUEUR\n{self.action}"
                f"\n\n### RÉSULTAT MÉCANIQUE (non négociable)\n{self.bloc}"
                f"\n\n{rythme.consigne(self.registre, self.entre_deux, self.allure)}"
                f"\n\n{self.consigne_action}\n\n"
                f"{self.rappel_joueurs}"
                f"Raconte la suite : {self.longueur}, jamais plus, en texte "
                f"simple, sans astérisques ni mise en forme.")

    @property
    def consigne_action(self) -> str:
        """Ce que le narrateur doit raconter d'abord. En tour de table, chaque
        joueur a déclaré l'action de SON personnage : toutes se racontent."""
        if self.groupe:
            lignes = self.action.splitlines()
            numerotees = "\n".join(f"{i}. {l}" for i, l in enumerate(lignes, 1))
            # Mesuré en partie réelle à deux : le narrateur racontait l'action du
            # meneur et laissait l'autre joueur en figurant. D'où une structure
            # IMPOSÉE, action par action, et la réponse de qui est interpellé.
            return ("### CE QUE LES JOUEURS TENTENT — CHACUNE DE CES ACTIONS SE RACONTE\n"
                    f"{numerotees}\n"
                    "Scène à plusieurs, une seule scène où ils sont ensemble. Raconte "
                    "les actions DANS CET ORDRE, un paragraphe chacune : ce que le "
                    "personnage fait, puis son résultat. Quand une action s'adresse à "
                    "quelqu'un (une question, une proposition, un défi), cette personne "
                    "RÉPOND dans la scène. Aucune action ne peut manquer : un joueur "
                    "dont l'action n'est pas racontée a été ignoré. Désigne les "
                    "personnages par leur NOM, à la troisième personne, jamais « tu ». "
                    "Ne leur fais rien dire ni décider d'autre que ce qu'ils ont déclaré.")
        return ("### CE QUE LE JOUEUR TENTE — À RACONTER EN PREMIER\n"
                f"« {self.action} »\nOuvre la scène sur cette tentative et sur son "
                "résultat. Ne la passe jamais sous silence, ne la remplace pas par "
                "autre chose.")

    @property
    def rappel_joueurs(self) -> str:
        """Rappelé en FIN d'invite, comme la longueur : mesuré en partie à
        deux, le narrateur faisait parler et agir l'autre joueur malgré la
        règle écrite plus haut."""
        if not self.autres_pj:
            return ""
        noms = " et ".join(self.autres_pj)
        return (f"{noms} {'appartient' if len(self.autres_pj) == 1 else 'appartiennent'} "
                f"à un AUTRE joueur humain : ne lui fais rien dire, ne lui fais "
                f"rien décider ni tenter. Tu peux seulement décrire ce qu'il "
                f"perçoit ou subit.\n\n")

    @property
    def max_tokens(self) -> int:
        """Le plafond de génération suit la longueur demandée : un modèle en
        ligne dépassait la cible « court » d'un tiers. ~1,5 jeton par mot
        français, marge comprise ; `achever` recoud la dernière phrase."""
        return int(rythme.fourchette(self.registre, self.allure)[1]
                   * self.facteur_longueur * 1.55) + 20

    @property
    def longueur(self) -> str:
        # Rappelée EN FIN d'invite : c'est la consigne la plus oubliée (340
        # mots en moyenne mesurés avec un modèle en ligne), et la dernière lue
        # est la mieux suivie. La même fourchette que le registre de la scène.
        bas, haut = rythme.fourchette(self.registre, self.allure)
        f = self.facteur_longueur
        return f"{int(bas * f)} à {int(haut * f)} mots"


def jouer(session: Session, camp: Campaign, pj: Character, action: str,
          rs: Ruleset, pack: LorePack, posture: str = "",
          levier: str = "", entrainement: str = "",
          choix: dict | None = None) -> Turn:
    """Joue un tour d'un bloc. Voir `preparer` / `conclure` pour le détail."""
    prep = preparer(session, camp, pj, action, rs, pack, posture, levier, entrainement,
                    choix=choix)
    narration = get_llm().text(prep.systeme, prep.invite(), max_tokens=prep.max_tokens)
    return conclure(session, camp, pj, prep, narration, rs, pack)


# Les difficultés, telles qu'on les annonce à la table.
DIFFICULTE_LIBELLES = {
    "triviale": "Triviale", "facile": "Facile", "normal": "Normale",
    "difficile": "Difficile", "ardue": "Ardue", "legendaire": "Légendaire",
}


@dataclass
class Arbitrage:
    """Ce que le maître du jeu annonce AVANT que le dé roule.

    C'est la pièce qui manquait pour ressembler à une table : « c'est un jet
    de Perception, difficile, tu as +2, ça se tente — et si ça rate, la
    patrouille te voit ». Le joueur lit, choisit une technique, décide de
    forcer ou de reformuler, PUIS lance. Voir docs/NINDO-2.md, pilier 1.

    Rien ici n'est écrit en base : un arbitrage attend le clic du joueur entre
    deux requêtes, et un joueur qui se ravise n'a rien à défaire. D'où des
    identifiants plutôt que des objets de session.
    """

    action: str
    intent: dict
    requiert_jet: bool = False
    stat: str = ""
    stat_label: str = ""
    stat_valeur: int = 10
    difficulte: str = "normal"
    dc: int = 12
    modificateur: int = 0                           # (caractéristique - 10) // 2, déjà dans la formule
    bonus: list = field(default_factory=list)       # en PLUS de la formule : [{"libelle", "valeur"}]
    chances: dict = field(default_factory=dict)     # reussite / partielle / echec, en %
    enjeu_echec: str = ""
    impossible: str = ""                            # l'obstacle, si l'action ne peut avoir lieu
    techniques: list = field(default_factory=list)  # utilisables : {ref, nom, fr, bonus, cout, palier, dispo}
    forcer: dict = field(default_factory=dict)      # {bonus, chakra, dispo}
    en_combat: bool = False
    posture: str = ""
    levier: str = ""
    combat: dict = field(default_factory=dict)      # l'aperçu du round : action, cible, plus rapides
    seance: dict | None = None                      # l'offre d'entraînement vérifiée
    liens_techniques: list = field(default_factory=list)   # ids de CharacterTechnique cités

    @property
    def annonce(self) -> bool:
        """Y a-t-il quelque chose à montrer au joueur avant le dé ?"""
        if self.en_combat:
            return True
        return self.requiert_jet and not self.impossible

    @property
    def difficulte_label(self) -> str:
        d = DIFFICULTE_LIBELLES.get(self.difficulte, self.difficulte)
        return d[0].upper() + d[1:] if d else d

    @property
    def total_bonus(self) -> int:
        """Ce qui s'ajoute au jet, hors caractéristique : le moteur l'applique
        tel quel, la formule du ruleset ajoute la caractéristique elle-même."""
        return sum(int(b["valeur"]) for b in self.bonus)

    def resume(self) -> dict:
        """Ce qui reste dans le tour enregistré, pour relire l'annonce plus tard."""
        return {"stat": self.stat, "stat_label": self.stat_label,
                "stat_valeur": self.stat_valeur, "modificateur": self.modificateur,
                "difficulte": self.difficulte_label, "dc": self.dc,
                "bonus": list(self.bonus), "chances": dict(self.chances),
                "enjeu_echec": self.enjeu_echec}


# Les actions de combat que l'interface propose, et la posture du ruleset
# qu'elles engagent. Le joueur choisit une CARTE ; le texte libre devient une
# couleur pour le narrateur, et l'arbitre n'a plus rien à deviner.
ACTIONS_COMBAT = {
    "attaquer": {"posture": "mesuree", "label": "Attaquer",
                 "aide": "Tu frappes sans t'exposer."},
    "assaut": {"posture": "offensive", "label": "Assaut",
               "aide": "Tu frappes fort et tu t'ouvres."},
    "technique": {"posture": "technique", "label": "Technique",
                  "aide": "Tu engages du chakra : l'effet dépend de la technique."},
    "defendre": {"posture": "defensive", "label": "Défendre",
                 "aide": "Tu encaisses, tu protèges, tu attends l'ouverture."},
    "manoeuvre": {"posture": "manoeuvre", "label": "Manœuvre",
                  "aide": "Terrain, ruse, renfort : tu réduis l'écart au lieu de frapper."},
    "objet": {"posture": "mesuree", "label": "Objet",
              "aide": "Parchemin, fumigène, pilule : ta besace agit."},
    "desengager": {"posture": "desengagement", "label": "Se désengager",
                   "aide": "Tu cherches à rompre le contact."},
}


def arbitrer(session: Session, camp: Campaign, pj: Character, action: str,
             rs: Ruleset, pack: LorePack, posture: str = "",
             levier: str = "", entrainement: str = "",
             choix: dict | None = None) -> Arbitrage:
    """Étapes 1bis et 2 : embuscade, interprétation — et l'annonce du jet.

    `posture` et `levier` sont les choix MÉCANIQUES du joueur. Les laisser
    deviner au modèle marchait mal là où ça compte le plus : un joueur qui
    écrit « je recule en couvrant Mio » voulait une posture défensive, et se
    retrouvait parfois en offensive. Quand l'interface les fournit, ils sont
    imposés — le modèle ne lit plus que l'intention de récit.

    NE LANCE AUCUN DÉ ET NE DÉPENSE RIEN : le joueur peut encore reformuler.
    La seule chose qui change le monde ici est l'embuscade, et c'est voulu.
    """
    llm = get_llm()

    # -- 1bis. le monde a des dents ------------------------------------------
    # Tiré AVANT l'interprétation : si une embuscade s'ouvre, l'action déclarée
    # se joue dedans. C'est la différence entre « un combat quand le joueur en
    # cherche un » et un monde où traverser une forêt frontalière coûte
    # quelque chose.
    renc = combat.active(session, camp)
    if renc is None:
        renc = combat.tirer_embuscade(session, camp, pack, rs, pj)
    if renc is None:
        # L'épreuve des clochettes : à l'acte 2, l'instructeur attaque.
        from app.engine import clochettes
        renc = clochettes.declencher(session, camp, pack, rs, pj)

    contexte = construire(session, camp, pj, action, rs, pack, role="arbitre")

    choix = choix or {}
    if renc is not None:
        # ---------------- régime de combat : l'action sera un round
        code = choix.get("action_code") or ""
        if code in ACTIONS_COMBAT:
            # LA CARTE CHOISIE FAIT FOI, et le modèle n'est pas appelé : un
            # round se prépare en quelques millisecondes, pas en dix secondes.
            intent = {"posture": ACTIONS_COMBAT[code]["posture"],
                      "cible": choix.get("cible", ""), "technique": "",
                      "arme": "", "levier": "", "objectif": "",
                      "resume": action or ACTIONS_COMBAT[code]["label"]}
            if code == "technique" and choix.get("technique"):
                intent["technique"] = choix["technique"]
            if code == "objet" and choix.get("objet"):
                intent["objet"] = choix["objet"]
            if code == "manoeuvre":
                intent["levier"] = choix.get("levier", "")
            if not action.strip():
                t = pack.technique(intent.get("technique", "")) or {}
                action = (f"{ACTIONS_COMBAT[code]['label']}"
                          + (f" — {t.get('nom')}" if t else "")
                          + (f" — {intent['objet']}" if intent.get("objet") else "")
                          + (f" sur {intent['cible']}" if intent.get("cible") else ""))
        else:
            intent = llm.json(
                ARBITRE_COMBAT,
                f"{contexte}\n\n### ACTION DÉCLARÉE PAR LE JOUEUR\n{action}",
                INTENT_COMBAT)
            if posture in rs.postures:
                intent["posture"] = posture
            if levier in rs.leviers():
                intent["levier"] = levier
        bonus, liens = _technique_citee(
            session, pj, f"{intent.get('technique', '')} "
                         f"{intent.get('resume', '')} {action}", rs, pack)
        # Si la technique est celle d'une carte, on la crédite même si le
        # texte libre ne la nomme pas.
        if intent.get("technique") and not liens:
            ct = session.exec(select(CharacterTechnique).where(
                CharacterTechnique.character_id == pj.id,
                CharacterTechnique.technique_ref == intent["technique"])).first()
            if ct is not None:
                liens = [ct]
                bonus = int(rs.palier_maitrise(ct.maitrise).get("jet", 0))
        avant = combat.agissent_avant(renc, pj, combat.adverses(session, renc))
        libelle_action = next((v["label"] for k, v in ACTIONS_COMBAT.items()
                               if v["posture"] == intent.get("posture")), "Échange")
        t = pack.technique(intent.get("technique", "")) or {}
        return Arbitrage(action=action, intent=intent, en_combat=True,
                         posture=intent.get("posture", ""), levier=intent.get("levier", ""),
                         bonus=[{"libelle": "maîtrise", "valeur": bonus}] if bonus else [],
                         liens_techniques=[ct.id for ct in liens],
                         combat={"round": renc.echange + 1, "action": libelle_action,
                                 "cible": intent.get("cible", ""),
                                 "technique": t.get("nom", ""),
                                 "objet": intent.get("objet", ""),
                                 "avant": [c.nom for c in avant],
                                 "titre": renc.titre})

    # ---------------- régime ordinaire : un jet contre une difficulté
    intent = llm.json(
        ARBITRE,
        f"{contexte}\n\n### ACTION DÉCLARÉE PAR LE JOUEUR\n{action}",
        INTENT)

    # Une cible nommée qui n'est pas dans la scène rend l'attaque
    # impossible, quoi qu'en ait dit l'arbitre. L'arbitre précise parfois la
    # cible entre parenthèses (« Raiden (appuis et équilibre) ») : on ne garde
    # que le nom. Et un AUTRE JOUEUR présent est une cible qui existe — un duel
    # amical entre coéquipiers se joue au jet, il ne se refuse pas.
    intent["cible"] = re.sub(r"\s*\(.*?\)", "", str(intent.get("cible") or "")).strip()
    if intent.get("action_type") == "combat" and intent["cible"] \
            and not combat.adversaires_designes(session, camp, pj, intent["cible"]) \
            and not [p for p in _autres_pj(session, camp, pj) if _designe(intent["cible"], p)]:
        intent["faisable"] = "non"
        intent["obstacle"] = (intent.get("obstacle")
                              or f"{intent['cible']} n'est pas ici.")

    # La technique employée est identifiée AVANT le jet pour en tirer le
    # bonus, mais sa maîtrise n'est créditée qu'APRÈS, selon le résultat.
    bonus_technique, liens = _technique_utilisee(session, pj, intent, rs, pack)

    # UNE SÉANCE D'ENTRAÎNEMENT. Le joueur a cliqué « S'entraîner » : le
    # tour est une séance, avec son jet et sa progression.
    seance_offre = None
    if entrainement:
        from app.engine import apprentissage
        try:
            seance_offre = apprentissage.verifier_seance(
                session, camp, pack, rs, pj, entrainement)
            intent.update({
                "action_type": "technique", "requiert_jet": True,
                "faisable": "oui",
                "stat": seance_offre["stat"] if seance_offre["stat"] in rs.stats
                else intent.get("stat"),
                "difficulte": apprentissage.DIFFICULTE_PAR_RANG.get(
                    seance_offre["rang"], "normal")})
        except apprentissage.ApprentissageRefuse as exc:
            intent["faisable"] = "non"
            intent["obstacle"] = str(exc)

    arb = Arbitrage(action=action, intent=intent, seance=seance_offre,
                    liens_techniques=[ct.id for ct in liens],
                    enjeu_echec=" ".join(str(intent.get("enjeu_echec") or "").split())[:160])

    if intent.get("faisable") == "non":
        arb.impossible = ((intent.get("obstacle") or "").strip()
                          or "cette action n'est pas possible ici et maintenant")
        return arb
    if not intent.get("requiert_jet"):
        return arb

    if intent.get("faisable") == "improbable":
        intent["difficulte"] = "legendaire"
    stat = intent.get("stat") if intent.get("stat") in rs.stats else next(iter(rs.stats))
    arb.requiert_jet = True
    arb.stat, arb.stat_label = stat, rs.stats.get(stat, {}).get("label", stat)
    arb.stat_valeur = int(pj.stats.get(stat, rs.stats.get(stat, {}).get("default", 10)))
    arb.difficulte = intent.get("difficulte", "normal")
    if arb.difficulte not in rs.difficultes:
        arb.difficulte = "normal"
    arb.dc = rs.valeur_difficulte(arb.difficulte)

    # UN DUEL ENTRE JOUEURS se joue contre la GARDE de l'autre : dix, plus son
    # modificateur de défense, plus deux. Mesuré en partie à deux : deux jets
    # indépendants réussissaient tous les deux et le narrateur tranchait seul.
    adversaire = next((p for p in _autres_pj(session, camp, pj)
                       if intent.get("action_type") == "combat"
                       and _designe(intent.get("cible", ""), p)), None)
    if adversaire is not None:
        stat_def = rs.combat.get("defense_stat", "endurance")
        valeur = int(adversaire.stats.get(stat_def, 10))
        arb.dc = 10 + (valeur - 10) // 2 + 2
        arb.difficulte = f"garde de {adversaire.nom}"

    # Le détail du bonus, ligne par ligne : c'est ce qu'un joueur vérifie sur
    # sa fiche avant de lancer, et ce que le moteur ne montrait jamais.
    arb.modificateur = (arb.stat_valeur - 10) // 2
    if bonus_technique:
        noms = [(pack.technique(ct.technique_ref) or {}).get("nom", ct.technique_ref)
                for ct in liens]
        arb.bonus.append({"libelle": "maîtrise de " + ", ".join(noms), "valeur": bonus_technique})
    blessures = combat.malus_blessures(session, rs, pj)
    if blessures:
        arb.bonus.append({"libelle": "épuisé" if combat.epuise(rs, pj) and
                          blessures == combat.MALUS_EPUISEMENT else "blessures et états",
                          "valeur": blessures})

    # Les techniques que le joueur peut engager sur CE jet : celles de son
    # répertoire qui se jouent avec la même caractéristique. Chacune avec son
    # coût réel et ce qu'elle apporte — un choix, pas une devinette.
    deja = {ct.id for ct in liens}
    chakra = int(pj.ressources.get("chakra", 0))
    table = rs.combat.get("stat_par_categorie", {})
    for ct in session.exec(select(CharacterTechnique).where(
            CharacterTechnique.character_id == pj.id)).all():
        t = pack.technique(ct.technique_ref) or {}
        if not t or ct.id in deja:
            continue
        stat_t = t.get("stat") or table.get(t.get("categorie", ""), "")
        if stat_t != stat:
            continue
        palier = rs.palier_maitrise(ct.maitrise)
        cout = int(round(int((t.get("cout") or {}).get("chakra", 0))
                         * float(palier.get("cout", 1.0))))
        arb.techniques.append({
            "ref": ct.technique_ref, "nom": t.get("nom", ct.technique_ref),
            "fr": t.get("fr", ""), "bonus": int(palier.get("jet", 0)),
            "cout": cout, "palier": palier.get("nom", ""), "dispo": chakra >= cout})
    arb.techniques.sort(key=lambda x: (-x["bonus"], x["cout"]))

    cfg_forcer = rs.data.get("check", {}).get("forcer") or {}
    if cfg_forcer:
        arb.forcer = {"bonus": int(cfg_forcer.get("bonus", 3)),
                      "chakra": int(cfg_forcer.get("chakra", 2)),
                      "dispo": chakra >= int(cfg_forcer.get("chakra", 2))}
    arb.chances = rs.chances(pj.stats, stat, arb.dc, bonus_technique + blessures)
    return arb


def resoudre(session: Session, camp: Campaign, pj: Character, arb: Arbitrage,
             rs: Ruleset, pack: LorePack, technique: str = "",
             forcer: bool = False, round_partage: dict | None = None) -> Preparation:
    """Étape 3 : le dé, et tout ce qui en découle avant la narration.

    `technique` et `forcer` sont les choix faits par le joueur SUR l'annonce.
    Ils se paient ici, jamais avant : reformuler ne coûte rien.
    `round_partage` : en tour de table, le round déjà joué pour tout le camp
    (voir `combat.round_de_table`), pour ne pas le rejouer par joueur.
    """
    action, intent = arb.action, arb.intent
    liens_techniques = [ct for ct in (session.get(CharacterTechnique, i)
                                      for i in arb.liens_techniques) if ct is not None]
    resolution: dict = {"intent": intent}

    if arb.en_combat:
        # ---------------- régime de combat : l'action est un échange
        # Le round partagé peut avoir REFERMÉ la rencontre (dernier adversaire
        # tombé) : on la retrouve par son identifiant, pas par « active ».
        renc = (session.get(Encounter, round_partage["rencontre_id"])
                if round_partage is not None else combat.active(session, camp))
        if renc is None:
            # La rencontre s'est refermée entre l'annonce et le dé (un autre
            # joueur, une reprise) : l'action se rejoue à froid.
            return resoudre(session, camp, pj, arbitrer(
                session, camp, pj, action, rs, pack, arb.posture, arb.levier), rs, pack)
        bonus = arb.total_bonus
        maitrise = max((ct.maitrise for ct in liens_techniques), default=50)
        if round_partage is not None:
            # Tour de table : le round a été joué UNE fois pour tout le camp.
            issue = dict(round_partage)
            issue["reussi"] = bool(round_partage["reussi"].get(pj.id))
        else:
            issue = combat.echanger(session, camp, pack, rs, pj, renc, intent,
                                    bonus, maitrise)
        bloc = issue["bloc"]
        resolution["combat"] = {
            "rencontre_id": renc.id, "echange": renc.echange,
            "statut": issue["statut"], "reussi": issue["reussi"],
            "titre": renc.titre, "lignes": issue["lignes"],
            "leviers": list(renc.leviers), "objectif": renc.objectif,
            "progres": renc.progres, "fosse": dict(renc.fosse),
            "partage": round_partage is not None}
        effets_combat = list(issue["effets"])
        systeme, en_combat = NARRATEUR_COMBAT, True
    else:
        # ---------------- régime ordinaire : un jet contre une difficulté
        bloc = "Aucun jet : l'action n'a pas d'enjeu mécanique."
        effets_combat, systeme, en_combat = [], NARRATEUR, False

        if arb.impossible:
            # PAS DE SILENCE. Mesuré en partie réelle : « je tue Madara en 1v1 »
            # et le récit continuait comme si de rien n'était. Une action
            # impossible se raconte : la tentative, puis ce qui l'arrête.
            bloc = (f"ACTION IMPOSSIBLE TELLE QUELLE : {arb.impossible}\n"
                    f"Raconte la tentative de {pj.nom} — ce qu'il fait, dit ou ose — "
                    f"puis ce qui l'arrête, et comment les autres réagissent. Le "
                    f"joueur doit voir que sa décision a été entendue.")
            resolution["impossible"] = arb.impossible
        elif arb.requiert_jet:
            bonus = arb.total_bonus
            annonce = arb.resume()

            # La technique choisie sur l'annonce : son bonus, son coût, et sa
            # maîtrise créditée après le jet comme une technique citée.
            choisie = next((t for t in arb.techniques
                            if t["ref"] == technique and t["dispo"]), None)
            if choisie is not None:
                bonus += int(choisie["bonus"])
                if choisie["cout"]:
                    pj.ressources = {**pj.ressources,
                                     "chakra": int(pj.ressources.get("chakra", 0)) - choisie["cout"]}
                    effets_combat.append(f"chakra -{choisie['cout']} → {pj.ressources['chakra']}")
                ct = session.exec(select(CharacterTechnique).where(
                    CharacterTechnique.character_id == pj.id,
                    CharacterTechnique.technique_ref == choisie["ref"])).first()
                if ct is not None:
                    liens_techniques.append(ct)
                annonce["technique"] = choisie["nom"]
                annonce["bonus"] = annonce["bonus"] + [
                    {"libelle": choisie["nom"], "valeur": int(choisie["bonus"])}]

            # Forcer : le pari. Le chakra tout de suite, et l'échec devient
            # critique — sinon ce n'est pas un pari, c'est un bonus gratuit.
            force = bool(forcer and arb.forcer.get("dispo"))
            if force:
                bonus += int(arb.forcer["bonus"])
                pj.ressources = {**pj.ressources,
                                 "chakra": int(pj.ressources.get("chakra", 0)) - int(arb.forcer["chakra"])}
                effets_combat.append(f"forcé : chakra -{arb.forcer['chakra']} → {pj.ressources['chakra']}")
                annonce["forcer"] = True
                annonce["bonus"] = annonce["bonus"] + [
                    {"libelle": "forcé", "valeur": int(arb.forcer["bonus"])}]
            session.add(pj)

            check = rs.check(pj.stats, arb.stat, arb.dc, bonus)
            if force and not check.reussi and check.issue != "echec_critique":
                check.issue = "echec_critique"
            resolution["check"] = check.to_dict()
            resolution["arbitrage"] = annonce
            bloc = (f"Jet de {arb.stat_label} : dé {check.des} → total {check.total} "
                    f"contre difficulté {check.difficulte}. Marge {check.marge:+d}.\n"
                    f"RÉSULTAT IMPOSÉ : {check.issue.replace('_', ' ').upper()}.")
            if bonus:
                bloc += f"\n(bonus appliqué : {bonus:+d})"
            if arb.enjeu_echec and not check.reussi:
                bloc += f"\nCe que l'échec entraîne, annoncé au joueur : {arb.enjeu_echec}"
            if force and not check.reussi:
                bloc += ("\nLe joueur avait FORCÉ : l'échec est critique, il coûte "
                         "quelque chose de concret et visible.")
            if arb.seance is not None:
                from app.engine import apprentissage
                suite, effets_seance = apprentissage.seance(
                    session, camp, pack, rs, pj, arb.seance, check.issue)
                bloc += "\n" + suite
                effets_combat = effets_combat + effets_seance
                resolution["entrainement"] = arb.seance["id"]

        # Hors combat, le corps se répare. Le goutte-à-goutte referme les
        # écorchures d'un trajet ; un repos déclaré remet vraiment debout, et
        # c'est la seule chose qui rende un combat perdu jouable.
        effets_combat += combat.recuperer(
            session, camp, rs, pj, repos=intent.get("action_type") == "repos")

        # Le joueur en vient aux mains avec quelqu'un qui EXISTE : la rencontre
        # s'ouvre ici et le tour suivant sera un échange. Un combat qui a un
        # passé vaut toujours mieux qu'un bandit fabriqué — et s'il n'y a
        # personne à combattre, il n'y a pas de combat. On ne fait surgir
        # d'adversaires que par embuscade, où c'est le lieu qui les justifie.
        # Un COÉQUIPIER visé n'ouvre rien : le duel amical s'est joué au jet.
        # Sans ce garde-fou, « je fonce sur Hana » rabattait sur « les
        # hostiles présents » et ouvrait un affrontement contre le rival.
        vise_un_joueur = bool(intent.get("cible")) and any(
            _designe(intent["cible"], p) for p in _autres_pj(session, camp, pj))
        if intent.get("action_type") == "combat" and not vise_un_joueur:
            cibles = combat.adversaires_designes(
                session, camp, pj, intent.get("cible", ""))
            ouverte = combat.ouvrir(
                session, camp, pack, rs, pj, declencheur="engagement",
                adversaires_existants=cibles) if cibles else None
            if ouverte is not None:
                bloc += ("\nUn affrontement s'engage. Il se jouera échange par "
                         "échange à partir de maintenant.")
                effets_combat = effets_combat + [
                    f"Affrontement engagé : {ouverte.titre}"]

    def ctx(role: str) -> str:
        return construire(session, camp, pj, action, rs, pack, role=role)

    # -- 4. le contexte du narrateur, prêt à servir --------------------------
    contexte_narrateur = ctx("narrateur")
    resolution["contexte_tokens"] = taille_estimee(contexte_narrateur)

    # Le souffle de la scène. Un échange de coups ne se raconte pas comme une
    # découverte, et six tours du même registre endorment — voir rythme.py.
    precedents = session.exec(select(Turn).where(
        Turn.campaign_id == camp.id).order_by(Turn.index.desc())
        .limit(rythme.LASSITUDE)).all()
    registre = rythme.choisir(
        intent, resolution, en_combat,
        revelation_recente=bool(precedents
                                and precedents[0].resolution.get("revelation")),
        recents=list(reversed(rythme.derniers(precedents))))
    resolution["registre"] = registre

    # Un creux n'est pas un vide. Sans mission ni affrontement, on le dit au
    # narrateur : ce sont les scènes d'entre-deux dont on se souvient.
    entre_deux = (not en_combat
                  and not gen_missions.ouvertes(session, camp)
                  and registre in ("ordinaire", "respiration", "conversation"))
    resolution["entre_deux"] = entre_deux

    return Preparation(
        action=action, intent=intent, bloc=bloc, resolution=resolution,
        systeme=systeme, contexte_narrateur=contexte_narrateur,
        effets_combat=effets_combat, liens_techniques=liens_techniques,
        en_combat=en_combat, registre=registre, entre_deux=entre_deux,
        allure=getattr(camp, "allure", None) or rythme.ALLURE_DEFAUT,
        autres_pj=[c.nom for c in _autres_pj(session, camp, pj)])


def preparer(session: Session, camp: Campaign, pj: Character, action: str,
             rs: Ruleset, pack: LorePack, posture: str = "",
             levier: str = "", entrainement: str = "",
             choix: dict | None = None) -> Preparation:
    """Étapes 1bis à 3 d'un trait : l'annonce, puis le dé sans attendre.

    C'est le chemin « lancer tout seul », et celui de tous les appels qui
    n'ont pas de joueur à qui demander (tests, scripts, tour d'un bloc).
    """
    arb = arbitrer(session, camp, pj, action, rs, pack, posture, levier, entrainement,
                   choix=choix)
    return resoudre(session, camp, pj, arb, rs, pack)


def conclure(session: Session, camp: Campaign, pj: Character,
             prep: Preparation, narration: str, rs: Ruleset,
             pack: LorePack) -> Turn:
    """Étapes 5 à 7 : conséquences, révélation, peuplement, journal, pistes.

    Appelé une fois la narration obtenue, qu'elle soit arrivée d'un bloc ou en
    flux. Tout ce qui change l'état du monde est ici — le streaming ne fait que
    changer la façon dont le joueur a lu le texte.
    """
    llm = get_llm()
    # Le flux arrive brut : on le ramène à du texte simple et à une dernière
    # phrase complète avant de l'enregistrer (voir `achever`).
    narration = achever(epurer(narration))
    action, intent = prep.action, prep.intent
    resolution, effets_combat = prep.resolution, prep.effets_combat
    liens_techniques, en_combat = prep.liens_techniques, prep.en_combat

    def ctx(role: str) -> str:
        return construire(session, camp, pj, action, rs, pack, role=role)

    # -- 5. conséquences ET pistes, en un seul appel -------------------------
    # Les deux s'extraient du même texte et du même regard. Les demander
    # séparément faisait relire la scène deux fois au modèle : un quart de la
    # latence d'un tour, pour rien.
    brut = llm.json(
        CONSEQUENCES,
        f"{ctx('consequences')}\n\n### ACTION\n{action}\n\n"
        f"### CE QUI VIENT DE SE PASSER\n{narration}",
        SCHEMA_CONSEQUENCES)
    net, rejets = valider_consequences(session, camp, brut)
    if en_combat:
        # Pendant un échange, ressources et XP appartiennent au moteur de
        # combat : les laisser au modèle les décompterait deux fois.
        if net["ressources"]:
            rejets.append("ressources ignorées : le combat les décompte lui-même")
        net["ressources"], net["xp"], net["rencontres"] = [], 0, []
    if not net["propositions"]:
        # Une zone d'action vide n'aide personne. Le joueur garde de toute
        # façon le texte libre ; ces amorces ne sont qu'un filet.
        net["propositions"] = _PISTES_DE_SECOURS
    resolution["rejets"] = rejets

    camp.tour += 1
    # `joue_le` était déclaré et jamais écrit : l'accueil triait les campagnes
    # par une date qui ne bougeait pas depuis leur création, et le rappel de
    # reprise n'avait aucun moyen de savoir depuis quand on avait lâché.
    camp.joue_le = maintenant()
    effets = effets_combat + _appliquer(session, camp, pj, net, rs, resolution,
                                        liens_techniques)
    from app.engine import fils
    effets += fils.appliquer(session, camp, net)
    # Tour de table : l'expérience de la scène revient à chaque joueur.
    if net["xp"] and len(prep.participants) > 1:
        from app.engine import progression
        for pid in prep.participants:
            autre = session.get(Character, pid)
            if autre is not None and autre.id != pj.id:
                effets.append(f"{autre.nom} — "
                              f"{progression.gagner_xp(session, camp, rs, autre, net['xp'])}")

    # L'horloge vient d'avancer : les délais promis se vérifient ici, et les
    # blessures qui devaient se refermer se referment. Sans ce point de
    # passage, une échéance annoncée ne tombait jamais et une entaille se
    # gardait jusqu'à la fin de la campagne.
    # Les relations viennent de bouger : un palier franchi est un événement,
    # pas un chiffre de plus. Voir liens.py.
    effets += liens.verifier(session, camp, pj, rs)
    effets += gen_missions.verifier_echeances(session, camp)
    effets += combat.soigner_le_temps(session, camp, pj, rs)
    # Le monde avance sans le joueur. Le prompt du narrateur l'affirmait
    # depuis le premier jour ; c'est ici que ça devient vrai.
    effets += monde.avancer(session, camp)

    # -- 5bis. révélation ----------------------------------------------------
    # Les PNJ présents servent trois fois : compteur d'apparitions, seuils de
    # secret, et connaissance de leurs intentions.
    pnjs = _pnjs_presents(session, camp, pj)
    reveal.compter_apparitions(session, pnjs)
    # À force de se croiser, un figurant devient quelqu'un : une fiche
    # complète, une relation, une place dans l'entourage. Voir
    # crystallize.promouvoir — c'est ce qui fait un monde plutôt qu'un décor.
    try:
        from app.engine import crystallize

        effets += crystallize.promouvoir(session, camp, pack, rs, pj, pnjs)
    except Exception:  # noqa: BLE001 — une promotion manquée se rejoue au tour suivant
        session.rollback()
    ouvert = reveal.reveler(session, camp, pj, narration, pnjs)
    effets += ouvert
    # Une destinée qui s'ouvre change le souffle du tour SUIVANT : c'est là
    # qu'il faut laisser la nouvelle peser. Voir rythme.choisir.
    resolution["revelation"] = bool(ouvert)

    # APRÈS la révélation : un secret dont un palier vient de tomber ne doit pas
    # être relancé dans le même tour. Les questions laissées de côté, elles,
    # reviennent — puis finissent par se refermer. Voir secrets.py.
    effets += secrets.verifier_echeances(session, camp, rs, pj)

    # -- 5ter. peuplement ----------------------------------------------------
    effets += _peupler(session, camp, pack, rs, pj, net.get("rencontres") or [])

    # -- 7. propositions : déjà là, extraites avec les conséquences ----------
    propositions = net["propositions"]

    # -- 6. journal et mémoire ----------------------------------------------
    tour = Turn(campaign_id=camp.id, index=camp.tour, character_id=pj.id,
                action=action, narration=narration, resolution=resolution,
                effets=effets, propositions=propositions)
    session.add(tour)
    session.add(Event(
        campaign_id=camp.id, tour=camp.tour,
        type=intent.get("action_type", "narratif"),
        resume=(intent.get("resume") or action)[:300],
        importance=max((f["importance"] for f in net["faits"]), default=2),
        entites=[pj.id]))
    # Les faits du tour reçoivent leur vecteur AVANT d'être écrits, et tous
    # en un seul appel : c'est ce qui permettra de les retrouver par le sens
    # au tour 200. Sans modèle d'embedding, la fonction ne fait rien.
    nouveaux = [MemoryFact(campaign_id=camp.id, texte=f["texte"],
                           importance=f["importance"], tour=camp.tour,
                           entites=[pj.id]) for f in net["faits"]]
    try:
        vectoriser(nouveaux)
    except Exception:  # noqa: BLE001 — une mémoire moins fine, jamais un tour perdu
        pass
    for f in nouveaux:
        session.add(f)
    session.add(camp)
    session.commit()
    session.refresh(tour)

    if camp.tour % settings.resume_tous_les == 0:
        _resumer(session, camp)

    # Il doit toujours y avoir quelque chose à faire : sans ce relais, une
    # campagne s'arrête faute d'objectif dès la première mission terminée.
    try:
        gen_missions.proposer_si_vide(session, camp, pack, rs, pj)
    except Exception:  # noqa: BLE001 — l'absence de mission ne casse pas un tour
        session.rollback()
    return tour


# --------------------------------------------------------------------------
def _designe(cible: str, perso: Character) -> bool:
    """« Hana », « hana yotsuki », « Yotsuki » : est-ce ce personnage ?"""
    c = " ".join((cible or "").lower().split())
    nom = perso.nom.lower()
    return bool(c) and (nom in c or c in nom or c.split()[0] == nom.split()[0])


def _autres_pj(session: Session, camp: Campaign, pj: Character) -> list[Character]:
    """Les autres personnages JOUEURS dans la même scène que celui qui agit."""
    if not pj.location_id:
        return []
    return list(session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.location_id == pj.location_id,
        Character.is_pc == True,           # noqa: E712
        Character.id != pj.id)).all())


def _pnjs_presents(session: Session, camp: Campaign,
                   pj: Character) -> list[Character]:
    if not pj.location_id:
        return []
    return list(session.exec(select(Character).where(
        Character.campaign_id == camp.id,
        Character.location_id == pj.location_id,
        Character.is_pc == False,          # noqa: E712
        Character.vivant == True)).all())  # noqa: E712


def _technique_citee(session: Session, pj: Character, texte: str, rs: Ruleset,
                     pack: LorePack) -> tuple[int, list[CharacterTechnique]]:
    """Les techniques du personnage que ce texte nomme, et leur bonus de jet.

    Ne modifie RIEN : la maîtrise se crédite après le résultat, dans
    `_appliquer`. Auparavant le gain était appliqué avant le jet et sans
    regarder l'issue — une technique ratée progressait autant qu'une technique
    réussie.
    """
    texte = (texte or "").lower()
    liens = session.exec(select(CharacterTechnique).where(
        CharacterTechnique.character_id == pj.id)).all()

    meilleur = 0
    utilisees: list[CharacterTechnique] = []
    for ct in liens:
        t = pack.technique(ct.technique_ref) or {}
        noms = [t.get("nom", ""), t.get("fr", ""), ct.technique_ref]
        if any(n and n.lower().split(" (")[0] in texte for n in noms):
            meilleur = max(meilleur, int(rs.palier_maitrise(ct.maitrise).get("jet", 0)))
            utilisees.append(ct)
    return meilleur, utilisees


def _technique_utilisee(session: Session, pj: Character, intent: dict,
                        rs: Ruleset,
                        pack: LorePack) -> tuple[int, list[CharacterTechnique]]:
    """Hors combat, seule une action DE TYPE technique engage une technique :
    décrire un kunai en marchant ne fait pas progresser le lancer d'armes."""
    if intent.get("action_type") != "technique":
        return 0, []
    return _technique_citee(
        session, pj, f"{intent.get('resume', '')} {intent.get('cible', '')}",
        rs, pack)


def _crediter_maitrise(session: Session, rs: Ruleset,
                       liens: list[CharacterTechnique], reussi: bool) -> list[str]:
    """La progression par l'usage, enfin asymétrique.

    Le ruleset distingue `gain_par_reussite` (3) et `gain_par_echec` (1) : une
    technique travaillée dans la difficulté progresse, une technique réussie
    progresse trois fois plus.
    """
    cfg = rs.data.get("maitrise", {})
    gain = int(cfg.get("gain_par_reussite" if reussi else "gain_par_echec",
                       3 if reussi else 1))
    effets = []
    for ct in liens:
        avant = rs.palier_maitrise(ct.maitrise).get("nom", "")
        ct.usages += 1
        if reussi:
            ct.usages_reussis += 1
        ct.maitrise = min(100, ct.maitrise + gain)
        session.add(ct)
        apres = rs.palier_maitrise(ct.maitrise).get("nom", "")
        if apres and apres != avant:
            effets.append(f"Maîtrise : {ct.technique_ref} → {apres}")
    return effets


def _appliquer(session: Session, camp: Campaign, pj: Character, net: dict,
               rs: Ruleset, resolution: dict,
               liens_techniques: list[CharacterTechnique]) -> list[str]:
    """Applique les deltas validés. Chaque effet est tracé en clair pour que le
    joueur voie ce que sa décision a réellement changé."""
    effets: list[str] = []

    for r in net["relations"]:
        pnj = session.get(Character, r["character_id"])
        # Le modèle cite parfois le personnage joueur lui-même : la partie de
        # 50 tours a fini avec une relation « Sayuri → Sayuri ».
        if pnj is None or pnj.is_pc or pnj.id == pj.id:
            continue
        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id,
            Relation.source_id == pnj.id,
            Relation.cible_id == pj.id)).first()
        if rel is None:
            rel = Relation(campaign_id=camp.id, source_id=pnj.id, cible_id=pj.id, valeur=0)
        rel.valeur = max(-100, min(100, rel.valeur + r["delta"]))
        rel.note = r["raison"]
        session.add(rel)
        effets.append(f"{pnj.nom} : relation {r['delta']:+d} → {rel.valeur}")

    for q in net["quetes"]:
        quete = session.get(Quest, q["quest_id"])
        if quete:
            effets += gen_missions.changer_statut(session, camp, pj, rs, quete, q["statut"])

    for r in net["ressources"]:
        if r["nom"] in pj.ressources:
            avant = pj.ressources[r["nom"]]
            pj.ressources = {**pj.ressources, r["nom"]: max(0, avant + r["delta"])}
            effets.append(f"{r['nom']} {r['delta']:+d} → {pj.ressources[r['nom']]}")

    if liens_techniques:
        # Sans jet, l'usage compte comme un entraînement : gain minimal.
        reussi = bool((resolution.get("check") or {}).get("reussi")
                      or (resolution.get("combat") or {}).get("reussi"))
        effets += _crediter_maitrise(session, rs, liens_techniques, reussi)

    if net["xp"]:
        from app.engine import progression

        effets.append(progression.gagner_xp(session, camp, rs, pj, net["xp"]))

    # LE JEU DE RÔLE RAPPORTE (chantier D). Tenir son nindō, faire avancer
    # un lien : c'est ce qu'un bon MJ récompense en fin de séance. Ici c'est
    # au tour, plafonné pour que ça reste un accent, pas une ferme à XP.
    effets += _recompenser_le_jeu(session, camp, pj, net, rs)

    if net["graine_intrigue"]:
        session.add(MemoryFact(campaign_id=camp.id, texte=net["graine_intrigue"],
                               nature="secret", importance=4, tour=camp.tour))
        effets.append("Une intrigue latente a été semée.")

    session.add(pj)
    return effets


def _recompenser_le_jeu(session: Session, camp: Campaign, pj: Character,
                        net: dict, rs: Ruleset) -> list[str]:
    """Nindō tenu, lien avancé : de l'expérience et un lien plus serré."""
    from app.engine import progression
    effets: list[str] = []
    cfg_n = rs.data.get("nindo", {}) or {}
    cfg_l = rs.data.get("liens", {}) or {}

    if net.get("nindo_joue") and (pj.nindo or "").strip():
        recent = [e for e in session.exec(select(Event).where(
            Event.campaign_id == camp.id, Event.type == "nindo",
            Event.tour > camp.tour - int(cfg_n.get("tours_entre_deux", 3)))).all()
            if pj.id in (e.entites or [])]
        if not recent:
            xp = int(cfg_n.get("xp_par_scene", 5))
            effets.append(f"Nindō tenu — {progression.gagner_xp(session, camp, rs, pj, xp)}")
            session.add(Event(campaign_id=camp.id, tour=camp.tour, type="nindo",
                              resume=f"{pj.nom} a tenu son nindō : « {pj.nindo} »",
                              importance=2, entites=[pj.id]))

    if net.get("lien_joue"):
        rel = session.exec(select(Relation).where(
            Relation.campaign_id == camp.id, Relation.source_id == net["lien_joue"],
            Relation.cible_id == pj.id, Relation.lien == True)).first()  # noqa: E712
        autre = session.get(Character, net["lien_joue"])
        if rel is not None and autre is not None:
            plus = int(cfg_l.get("relation_par_scene", 3))
            rel.valeur = max(-100, min(100, rel.valeur + plus))
            session.add(rel)
            xp = int(cfg_l.get("xp_par_scene", 5))
            effets.append(f"Lien avec {autre.nom} approfondi ({plus:+d}) — "
                          f"{progression.gagner_xp(session, camp, rs, pj, xp)}")
    return effets


def _peupler(session: Session, camp: Campaign, pack: LorePack, rs: Ruleset,
             pj: Character, rencontres: list[dict]) -> list[str]:
    """Une personne croisée dans la scène devient un personnage figé.

    `crystallize.py` était complet et n'était appelé de nulle part : le monde
    restait peuplé des quatre personnages de l'amorce et de silhouettes
    anonymes, puisque le narrateur a interdiction d'inventer un nom.

    On réemploie un figurant connu quand c'est possible — c'est ce que fait un
    maître du jeu humain, et ça vaut mieux que quinze visages neufs.
    """
    if not rencontres:
        return []
    from app.engine import crystallize

    if not crystallize.budget_disponible(session, camp):
        return []

    r = rencontres[0]
    lieu = session.get(Location, pj.location_id) if pj.location_id else None

    if r.get("importance") != "notable":
        connu = crystallize.reemployer(session, camp, pj.location_id)
        if connu is not None:
            connu.apparitions += 1
            if lieu is not None:
                connu.location_id = lieu.id
            session.add(connu)
            return [f"{connu.nom} est de nouveau là."]

    germe = {
        "role": (r.get("role") or "habitant")[:60],
        "nom": (r.get("nom") or "").strip()[:60],
        "importance": r.get("importance", "figurant"),
        "scene": (r.get("scene") or "")[:200],
        "village_ref": pj.village_ref or "",
        "grade": "genin" if r.get("importance") == "notable" else "genin",
    }
    try:
        pnj = crystallize.cristalliser(
            session, camp, pack, rs, germe,
            declencheur="rencontre en jeu", lieu=lieu,
            niveau="leger" if germe["importance"] == "figurant" else "complet")
    except Exception:  # noqa: BLE001 — un PNJ manqué ne casse pas un tour
        session.rollback()
        return []
    return [f"Tu as fait la connaissance de {pnj.nom}."]


# Le filet quand le modèle n'a rien proposé d'exploitable. Volontairement
# génériques et sans enjeu : ce sont des amorces, jamais des rails, et le
# joueur garde toujours le texte libre.
_PISTES_DE_SECOURS = [
    {"texte": "Observer la scène sans intervenir", "risque": "faible"},
    {"texte": "Aborder directement la personne la plus proche", "risque": "moyen"},
    {"texte": "Chercher une autre voie", "risque": "faible"},
]


def _resumer(session: Session, camp: Campaign) -> None:
    """Compression périodique : du verbatim remplacé par du résumé dense."""
    # `niveau != reprise` : les rappels de « Précédemment » sont rangés dans la
    # même table. Sans ce filtre, le dernier rappel écrit ferait croire que
    # l'historique est déjà compressé jusqu'au tour courant, et plus aucun
    # résumé de scène ne serait produit.
    dernier = session.exec(select(Summary).where(
        Summary.campaign_id == camp.id, Summary.niveau != reprise.NIVEAU)
        .order_by(Summary.au_tour.desc())).first()
    depuis = (dernier.au_tour if dernier else 0) + 1
    tours = session.exec(select(Turn).where(
        Turn.campaign_id == camp.id, Turn.index >= depuis).order_by(Turn.index)).all()
    if not tours:
        return
    corpus = "\n".join(f"[{t.index}] Joueur : {t.action}\nMJ : {t.narration}" for t in tours)
    texte = get_llm().text(RESUMEUR, corpus, rapide=True, temperature=0.3)
    # « Résumé factuel (120 mots) : » — le modèle recopie parfois la consigne en
    # guise de titre. Le résumé s'affiche dans la chronique : on l'enlève.
    texte = re.sub(r"^\s*(r[ée]sum[ée][^\n:]{0,40}:)\s*", "", texte or "",
                   flags=re.IGNORECASE).strip()
    session.add(Summary(campaign_id=camp.id, niveau="scene", du_tour=depuis,
                        au_tour=camp.tour, texte=texte))
    session.commit()
