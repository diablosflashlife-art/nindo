"""Modèle de données du jeu.

PRINCIPE DIRECTEUR : la base est la source de vérité, pas le modèle de langage.
Le LLM raconte et propose ; Python calcule, valide et écrit.

Trois natures de tables, à ne jamais confondre :

  ÉTAT      Campaign, Character, Location, Faction, Relation, Quest, Office…
            Modifiable à chaque tour.

  JOURNAL   Turn, Event, MemoryFact, Summary, Crystallization…
            Append-only. On n'efface jamais. C'est ce qui permet de rejouer,
            de déboguer et d'alimenter la mémoire longue.

  CACHÉ     Secret, DestinyTrait, Knowledge…
            Existe en base, mais n'atteint JAMAIS le contexte du narrateur
            tant que les joueurs ne l'ont pas découvert (voir memory/context.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    """UTC, mais NAÏF. `datetime.utcnow()` est déprécié ; passer à des dates
    conscientes du fuseau changerait le type stocké et rendrait les anciennes
    lignes incomparables avec les nouvelles. On garde donc la même valeur
    qu'avant, obtenue par le chemin qui ne disparaîtra pas."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Alias public. Le moteur horodate `Campaign.joue_le` à chaque tour joué, et il
# doit le faire avec EXACTEMENT la même valeur que les colonnes par défaut :
# mélanger des dates naïves et conscientes rendrait les lignes incomparables.
maintenant = _now


# ==========================================================================
# CAMPAGNE — le conteneur de tout
# ==========================================================================
class Campaign(SQLModel, table=True):
    """Une partie. Tout le reste s'y rattache.

    `graine` rend reproductibles toutes les générations aléatoires de la
    campagne : destinée, distribution, cristallisations. Indispensable pour
    déboguer « pourquoi ce personnage a-t-il été généré ainsi ».
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    nom: str
    ruleset_slug: str = "naruto"
    lore_pack: str = "naruto"
    epoque: str = "naruto_p1"

    # Réglages de partie
    fidelite: str = "souple"          # strict | souple | uchronie
    ton: str = "shonen sombre, tension montante, conséquences durables"
    difficulte: str = "normal"        # indulgent | normal | impitoyable
    allure: str = "court"             # court | normal | long — longueur des récits
    # Combien de joueurs créent un personnage AVANT que l'équipe soit scellée.
    nb_joueurs: int = 1

    graine: int = 0
    tour: int = 0                      # horloge de la campagne
    phase: str = "creation"            # creation | amorce | en_cours | terminee

    # Copie du ruleset au moment de la création : modifier le YAML plus tard
    # ne change pas rétroactivement les règles d'une partie en cours.
    ruleset: dict = Field(default_factory=dict, sa_column=Column(JSON))

    resume_ouverture: str = Field(default="", sa_column=Column(Text))
    cree_le: datetime = Field(default_factory=_now)
    joue_le: datetime = Field(default_factory=_now)


# ==========================================================================
# ENTITÉS DU MONDE
# ==========================================================================
class Location(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    lore_ref: str = ""                 # ex. "naruto.lieu.academie" si issu du pack
    nom: str
    description: str = Field(default="", sa_column=Column(Text))
    region: str = ""
    village_ref: str = ""
    danger: int = 1                    # 1..10
    terrain: dict = Field(default_factory=dict, sa_column=Column(JSON))
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    connu: bool = True                 # le joueur sait-il que ce lieu existe ?
    # Position sur la carte, en centièmes de sa largeur et de sa hauteur. Des
    # coordonnées RELATIVES : la carte se redimensionne, les lieux suivent, et
    # aucune échelle n'est écrite en dur. Un lieu à (0, 0) n'est pas placé et
    # ne s'affiche pas sur la carte.
    x: int = 0
    y: int = 0


class Faction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    lore_ref: str = ""
    nom: str
    description: str = Field(default="", sa_column=Column(Text))
    objectifs: list = Field(default_factory=list, sa_column=Column(JSON))
    puissance: int = 50
    village_ref: str = ""


class Character(SQLModel, table=True):
    """PJ et PNJ dans la même table : un PNJ est un personnage sans joueur.

    Évite de dupliquer combat, progression et relations — et le jour où un
    PNJ devient jouable, c'est un booléen.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)

    # --- identité
    nom: str
    is_pc: bool = False
    joueur: str = ""                   # qui tient la manette (hot-seat)
    sexe: str = ""
    age: Optional[int] = None
    apparence: str = Field(default="", sa_column=Column(Text))
    origine: str = Field(default="", sa_column=Column(Text))

    # --- appartenance
    clan: str = ""
    clan_ref: str = ""
    village: str = ""
    village_ref: str = ""
    faction_id: Optional[int] = Field(default=None, foreign_key="faction.id")

    # --- ce que lit le narrateur à chaque tour : court et dense
    personnalite: str = ""             # 3-5 traits
    parler: str = ""                   # manière de parler, tics de langage
    objectifs: list = Field(default_factory=list, sa_column=Column(JSON))

    # --- mécanique
    # La spécialité choisie à la création. Elle pilotait les techniques de
    # départ puis se perdait : rien n'en gardait trace sur la fiche, et
    # `apprentissage.py` en a besoin pour savoir ce qu'on peut travailler seul.
    specialisation: str = ""
    grade: str = "genin"
    niveau: int = 1
    xp: int = 0
    # Points de caractéristique gagnés en montant de niveau et pas encore
    # placés. Ils s'accumulent : on ne dépense jamais à la place du joueur.
    points_libres: int = 0
    # Les techniques en cours d'apprentissage : {technique: pourcentage}. Chaque
    # séance d'entraînement JOUÉE (un tour, un jet) les fait avancer.
    entrainements: dict = Field(default_factory=dict, sa_column=Column(JSON))
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ressources: dict = Field(default_factory=dict, sa_column=Column(JSON))
    inventaire: list = Field(default_factory=list, sa_column=Column(JSON))
    tier: int = 2
    pe: float = 0.0                    # puissance effective, dénormalisée
    fiche_hash: str = ""

    # --- état
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")
    vivant: bool = True
    etats: list = Field(default_factory=list, sa_column=Column(JSON))

    # --- provenance
    source: str = "partie"             # canon | partie | genere
    lore_ref: str = ""
    role_campagne: str = ""            # sensei | coequipier | rival | antagoniste | figurant
    apparitions: int = 0               # pilote la cristallisation
    relu_par_humain: bool = False
    voix_ref: str = ""

    notes: str = Field(default="", sa_column=Column(Text))


class Relation(SQLModel, table=True):
    """Arête du graphe social. La VALEUR pilote le comportement des PNJ ;
    le texte n'en est que la traduction au moment du contexte."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    source_id: int = Field(foreign_key="character.id", index=True)
    cible_id: int = Field(foreign_key="character.id", index=True)
    nature: str = "connaissance"       # mentor, rival, famille, dette, rancune…
    valeur: int = 0                    # -100..+100
    note: str = ""
    publique: bool = True              # si faux, à découvrir en jeu
    # Le dernier palier FRANCHI, et quand. La valeur bougeait sans que rien
    # n'arrive jamais : une relation à +80 ne produisait pas plus qu'une
    # relation à +10. Ces deux champs font d'un franchissement un événement.
    # Voir app/engine/liens.py.
    palier_vu: int = 0                 # -2..+2
    palier_tour: int = 0


class Quest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    titre: str
    description: str = Field(default="", sa_column=Column(Text))
    rang: str = "D"
    statut: str = "proposée"           # proposée|acceptée|en cours|réussie|échouée|refusée|expirée
    donneur_id: Optional[int] = Field(default=None, foreign_key="character.id")
    enjeu: str = Field(default="", sa_column=Column(Text))
    echeance_tour: Optional[int] = None
    # Le tour où l'équipe s'y est engagée : c'est l'horloge de l'arc. Une
    # mission acceptée depuis dix tours doit se diriger vers son dénouement.
    debut_tour: Optional[int] = None
    recompense_donnee: bool = False


class Fil(SQLModel, table=True):
    """Une question que le récit a posée au joueur, et qu'il lui doit.

    Mesuré sur une partie de cinquante tours : le narrateur ouvrait un mystère
    à chaque scène et n'en refermait aucun. L'épilogue parlait surtout de
    « questions sans réponse ». Le moteur tient donc le compte des fils
    ouverts, interdit d'en ouvrir un quatrième, et exige une réponse quand un
    fil a trop attendu. Voir engine/fils.py.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    question: str
    ouvert_au_tour: int = 0
    statut: str = "ouvert"             # ouvert | resolu
    reponse: str = Field(default="", sa_column=Column(Text))
    resolu_au_tour: Optional[int] = None


class Office(SQLModel, table=True):
    """Siège de grade à places comptées : Commandant Jônin (2), Kage (1).

    Créé à l'amorce y compris VACANT — c'est ce NULL qui rend les arcs de
    succession possibles.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    village_ref: str
    grade: str
    siege_no: int = 1
    titulaire_id: Optional[int] = Field(default=None, foreign_key="character.id")
    statut: str = "vacant"             # occupe | vacant | conteste
    interim_id: Optional[int] = Field(default=None, foreign_key="character.id")
    interim_jusqu_a: Optional[int] = None


# ==========================================================================
# TECHNIQUES ET CAPACITÉS — définition dans le lore, maîtrise en base
# ==========================================================================
class CharacterTechnique(SQLModel, table=True):
    """Le lien personnage <-> technique. `maitrise` est le champ central :
    connaître une technique et la maîtriser sont deux choses différentes."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    technique_ref: str
    maitrise: int = 40                 # 0..100
    usages: int = 0
    usages_reussis: int = 0
    appris_tour: int = 0
    appris_de_id: Optional[int] = Field(default=None, foreign_key="character.id")
    signature: bool = False


class CharacterCapacity(SQLModel, table=True):
    """Kekkei genkai, dôjutsu, senjutsu… Le palier 0 signifie « possédée mais
    jamais éveillée » : c'est l'état d'un Uchiha sans Sharingan."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    capacite_ref: str
    palier: int = 0
    palier_max_atteint: int = 0
    active: bool = False
    eveil_tour: Optional[int] = None
    declencheur_realise: str = Field(default="", sa_column=Column(Text))
    usages_cumules: int = 0
    scellee: bool = False
    connue_de: list = Field(default_factory=list, sa_column=Column(JSON))


class Condition(SQLModel, table=True):
    """États durables : blessures, coûts persistants de capacités, poisons.
    Entrent dans le calcul de puissance — un Mangekyô se paie vraiment."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    code: str
    libelle: str = ""
    severite: int = 1                  # 1..5
    effets: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reversible: bool = True
    origine: str = ""
    depuis_tour: int = 0
    guerit_tour: Optional[int] = None


# ==========================================================================
# RENCONTRE — un affrontement qui dure
# ==========================================================================
class Encounter(SQLModel, table=True):
    """Un affrontement, étalé sur plusieurs tours de jeu.

    Sans cette table, un combat se résumait à un jet de dé par tour : aucune
    ressource ne descendait, aucun avantage ne se construisait, et la règle du
    fossé — le cœur du genre, où un genin ne bat pas un jônin mais peut lui
    tenir tête assez longtemps pour sauver quelqu'un — n'était branchée nulle
    part.

    ÉTAT et JOURNAL à la fois : les champs du haut changent à chaque échange,
    `journal` ne fait que grandir. C'est ce qui permet de raconter un combat
    après coup, et de déboguer un échange qui a mal tourné.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    location_id: Optional[int] = Field(default=None, foreign_key="location.id")

    titre: str = ""
    # en_cours | gagnee | perdue | rompue | objectif_atteint | dispersee
    statut: str = "en_cours"
    declencheur: str = ""              # embuscade | engagement | patrouille…
    surprise: bool = False             # le joueur a-t-il été pris de court ?

    tour_debut: int = 0
    tour_fin: Optional[int] = None
    echange: int = 0

    camp_joueur: list = Field(default_factory=list, sa_column=Column(JSON))
    camp_adverse: list = Field(default_factory=list, sa_column=Column(JSON))

    # Leviers acquis par le camp du joueur. Ils ne donnent pas un bonus au dé :
    # ils réduisent l'écart de tier. Voir Ruleset.fosse_effectif.
    leviers: list = Field(default_factory=list, sa_column=Column(JSON))
    # Ceux dont la portée est `premier_echange` valent en plus un bonus, une
    # seule fois. On note ici lesquels ont déjà servi — sans ça, une embuscade
    # frappait avant d'être vue à tous les échanges du combat.
    leviers_consommes: list = Field(default_factory=list, sa_column=Column(JSON))

    # Quand vaincre est hors de portée, le moteur impose les objectifs
    # réellement ouverts et compte les succès qui y mènent.
    fosse: dict = Field(default_factory=dict, sa_column=Column(JSON))
    objectifs: list = Field(default_factory=list, sa_column=Column(JSON))
    objectif: str = ""
    progres: int = 0

    journal: list = Field(default_factory=list, sa_column=Column(JSON))
    butin: list = Field(default_factory=list, sa_column=Column(JSON))
    cree_le: datetime = Field(default_factory=_now)

    # LE ROUND (Nindō 2.0, chantier B). L'initiative de chaque combattant,
    # tirée à l'ouverture, et l'ordre qui en découle : qui frappe avant le
    # joueur frappe AVANT son action. `effets_actifs` porte ce que les
    # techniques posent sur un combattant — garde, esquive, doublures — par
    # identifiant de personnage.
    initiative: dict = Field(default_factory=dict, sa_column=Column(JSON))
    ordre: list = Field(default_factory=list, sa_column=Column(JSON))
    effets_actifs: dict = Field(default_factory=dict, sa_column=Column(JSON))


# ==========================================================================
# DESTINÉE — un secret dont le sujet est le personnage du joueur
# ==========================================================================
class Destiny(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    mode: str = "proposee"             # choisie | proposee | aleatoire
    profil: str = ""
    # L'archétype tiré par « je m'en remets au destin » — le réceptacle,
    # l'ermite, le marqué. C'est la seule chose qu'on DIT au joueur de sa
    # destinée : le nom et l'accroche, jamais la vérité des traits.
    archetype: str = ""
    archetype_nom: str = ""
    archetype_accroche: str = ""
    graine: int = 0
    rerolls: int = 0
    budget_actif: int = 3
    budget_latent: int = 12
    presage: str = Field(default="", sa_column=Column(Text))


class DestinyTrait(SQLModel, table=True):
    """ATTENTION : `verite` ne doit JAMAIS entrer dans le contexte du narrateur.
    Un modèle qui connaît la réponse la laisse transparaître."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    destiny_id: int = Field(foreign_key="destiny.id", index=True)
    trait_ref: str
    libelle: str = ""
    famille: str = ""
    rarete: str = "commun"
    cout: int = 1
    actif_au_depart: bool = False
    etat: str = "latent"               # latent|pressenti|en_eveil|eveille|refuse
    palier_courant: int = 0
    palier_max: int = 1
    revelation_initiale: str = Field(default="", sa_column=Column(Text))
    verite: str = Field(default="", sa_column=Column(Text))     # JAMAIS exposé
    conditions: list = Field(default_factory=list, sa_column=Column(JSON))
    contraintes: list = Field(default_factory=list, sa_column=Column(JSON))
    accorde: str = ""                  # capacité, technique ou effet accordé
    tour_min_prochain: int = 0


class DestinyClue(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    trait_id: int = Field(foreign_key="destinytrait.id", index=True)
    palier: int = 1
    texte: str = Field(default="", sa_column=Column(Text))
    delivre_tour: Optional[int] = None


# ==========================================================================
# SECRETS ET CONNAISSANCE
# ==========================================================================
class Secret(SQLModel, table=True):
    """La vérité est FIGÉE à la création. Un modèle qui ne connaît pas la
    réponse en invente une nouvelle à chaque interrogation, et la campagne
    n'a jamais de résolution."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    question: str
    verite: str = Field(default="", sa_column=Column(Text))      # JAMAIS exposé
    sujet_id: Optional[int] = Field(default=None, foreign_key="character.id")
    indices: list = Field(default_factory=list, sa_column=Column(JSON))
    niveau_revele: int = 0
    fausse_piste_id: Optional[int] = Field(default=None, foreign_key="character.id")
    resolu: bool = False

    # --- ce qui fait qu'un secret PRESSE (voir app/engine/secrets.py)
    # Un secret entamé puis laissé de côté restait entamé jusqu'au tour 200 :
    # rien ne le rappelait au joueur, et rien ne se refermait s'il l'ignorait.
    tour_progres: int = 0              # tour du dernier palier tombé
    rappels: int = 0                   # combien de fois le monde y est revenu
    rappel_tour: int = 0               # tour du dernier rappel, pour l'éteindre
    perdu: bool = False                # la fenêtre s'est refermée sans le joueur


class Knowledge(SQLModel, table=True):
    """Qui sait quoi. Sert à deux choses : les leviers de combat, et surtout
    le filtre de divulgation — le narrateur ne reçoit que ce que le groupe sait."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    connaisseur_id: Optional[int] = Field(default=None, foreign_key="character.id")
    groupe: bool = True                # vrai = connu de toute la table
    sujet_type: str = "character"      # character | faction | lieu | technique | monde
    sujet_id: Optional[int] = None
    sujet_ref: str = ""
    aspect: str = "identite"           # identite|techniques|faiblesse|capacite|intention|affiliation
    niveau: int = 1                    # 0 ignore .. 3 sait et peut l'exploiter
    contenu: str = Field(default="", sa_column=Column(Text))
    fiable: bool = True
    source: str = ""
    tour: int = 0


# ==========================================================================
# JOURNAL — append-only
# ==========================================================================
class Turn(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    index: int = 0
    character_id: Optional[int] = Field(default=None, foreign_key="character.id")
    action: str = Field(default="", sa_column=Column(Text))
    narration: str = Field(default="", sa_column=Column(Text))
    resolution: dict = Field(default_factory=dict, sa_column=Column(JSON))
    effets: list = Field(default_factory=list, sa_column=Column(JSON))
    propositions: list = Field(default_factory=list, sa_column=Column(JSON))
    segments: list = Field(default_factory=list, sa_column=Column(JSON))   # pour la voix
    cree_le: datetime = Field(default_factory=_now)


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    tour: int = 0
    type: str = "narratif"
    resume: str = Field(default="", sa_column=Column(Text))
    importance: int = 3                # 1..5
    entites: list = Field(default_factory=list, sa_column=Column(JSON))
    portee: str = "public"             # public | scene | prive
    cree_le: datetime = Field(default_factory=_now)


class MemoryFact(SQLModel, table=True):
    """Fait atomique et autoportant. On n'indexe JAMAIS les transcriptions
    brutes : « Kaito a refusé la mission d'escorte (tour 14) », pas « il a refusé »."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    texte: str = Field(default="", sa_column=Column(Text))
    nature: str = "fait"               # fait | promesse | secret | relation | lieu
    importance: int = 3
    tour: int = 0
    entites: list = Field(default_factory=list, sa_column=Column(JSON))
    # Le sens du fait, pour le rappeler autrement que par ses lettres. Vide
    # quand aucun modèle d'embedding n'est installé : le rappel lexical prend
    # alors le relais, exactement comme avant. Voir app/llm/embeddings.py.
    vecteur: list = Field(default_factory=list, sa_column=Column(JSON))


class Summary(SQLModel, table=True):
    """Compression hiérarchique : scène -> arc -> chronique. Le coût en tokens
    de l'historique devient logarithmique au lieu de linéaire."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    niveau: str = "scene"
    du_tour: int = 0
    au_tour: int = 0
    texte: str = Field(default="", sa_column=Column(Text))


class Crystallization(SQLModel, table=True):
    """Trace d'une génération de PNJ. Permet d'ajuster les seuils et les
    prompts après coup, et de savoir quelle part de la distribution est générée."""

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id", index=True)
    character_id: int = Field(foreign_key="character.id", index=True)
    tour: int = 0
    niveau: str = "leger"              # leger | complet
    declencheur: str = ""
    germe: dict = Field(default_factory=dict, sa_column=Column(JSON))
    rejets: list = Field(default_factory=list, sa_column=Column(JSON))
