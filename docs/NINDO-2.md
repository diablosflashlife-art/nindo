# Nindō 2.0 — Le cœur d'un vrai jeu de rôle

Document de conception. Il fixe la direction de la refonte du cœur du jeu,
décidée le 27 septembre 2026, et sert de référence à chaque chantier. Quand
un choix de code hésite, c'est ici qu'on tranche.

## Le diagnostic

Le moteur est profond : postures, règle du fossé, leviers, blessures, moral
des adversaires, missions générées, fils d'intrigue, secrets, destinée. Mais le
joueur n'en voit presque rien. Il tape une phrase, attend, lit un paragraphe,
et une pastille lui dit « réussite · dé 14 ». Les règles tournent en cachette
derrière la prose.

À une vraie table, on VOIT le maître du jeu arbitrer (« c'est un jet de
Perception, difficile »), on LANCE le dé soi-même, on CHOISIT dans une
situation claire, et le combat se joue à tour de rôle avec des points de vie
sous les yeux. C'est cette sensation-là qui manque, pas de la mécanique.

Retour du joueur (v1.1) : « on est très loin d'une v1 », techniques apprises
sans scène, menu pas instinctif, l'IA qui ignore les actions, plusieurs
joueurs mal gérés. La 1.1 a réparé les symptômes ; la 2.0 change la boucle.

## Ce dont on s'inspire

- **D&D / Pathfinder** : le MJ annonce le jet, le joueur lance, tout le monde
  regarde le dé. Initiative et rounds en combat.
- **Blades in the Dark** : position et effet annoncés AVANT le jet, « pousser »
  (bonus contre un prix), horloges, temps mort entre deux coups.
- **Apocalypse World / Avatar Legends** : le MJ termine toujours sur une
  question ; réussite partielle = « oui, mais » ; liens et credo qui
  rapportent de l'expérience quand on les joue.
- **Naruto D20 / Shinobigami** : chakra comme ressource centrale, affinités,
  équipe de trois genin et un jônin, missions de rang D à S, examen chûnin.
- **Ultimate Ninja / jeux vidéo Naruto** : la lisibilité du combat — barres,
  ordre des tours, une technique = une action visible.

## Les six piliers

### 1. Le tour de jeu comme à une table : Déclarer → Arbitrer → Lancer → Raconter

Le joueur déclare. L'arbitre répond D'ABORD, comme un MJ :

> Jet de **Perception**, difficulté **Difficile (16)**.
> Ton bonus : +2 (Perception 14) · +2 (Kikaichû, assurée).
> Environ **45 %** de réussite, 15 % de « oui, mais ».
> Si ça rate : la patrouille te repère avant que tu aies vu quoi que ce soit.

Avec des boutons : **Lancer le dé** · **Reformuler** · une technique du
répertoire (coût en chakra, bonus de maîtrise) · **Forcer** (+3 au jet, coûte
du chakra, et l'échec devient critique).

Puis le dé roule à l'écran, avec un son, et SEULEMENT ENSUITE la scène s'écrit.
Les actions sans enjeu (parler, marcher) sautent l'étape : aucun clic inutile.
Un réglage « lancer tout seul » pour ceux qui préfèrent la vitesse.

Chaque scène se termine sur une situation ouverte et 2-3 options cliquables,
en plus du texte libre.

### 2. Le combat en rounds, avec un plateau

Initiative (Vitesse + d20), ordre des combattants affiché. À son tour, un vrai
choix : **Attaquer** (cible), **Technique** (chaque jutsu avec son coût, son
effet, sa portée), **Manœuvre** (levier), **Défendre**, **Se désengager**,
**Objet** (parchemin explosif, fumigène, pilule militaire).

Plateau visible : PV et chakra de chaque camp, blessures, distance (au
contact / à distance). Les techniques reçoivent des effets mécaniques réels :
dégâts, paralysie, garde, soin, clones qui encaissent, zone. Le cycle des
éléments s'applique. Les adversaires agissent en Python, comme aujourd'hui.

Une seule narration par round, courte : l'attente ne doit pas doubler.

La règle du fossé et les leviers restent le cœur du genre : ils ne changent
pas, ils deviennent visibles.

### 3. Une campagne structurée : missions en actes, temps mort, examen chûnin

Mission = **briefing** (commanditaire, rang, objectif, récompense en ryō et
XP) → **trois actes** (approche, complication, dénouement) → **débrief** avec
note (S à D), réputation et rapport au village.

Entre deux missions, un écran **Temps mort** : chacun choisit deux activités —
séance d'entraînement, scène avec un PNJ (lien), soins, boutique, enquête sur
un fil, repos. C'est là que vivent l'apprentissage et les relations.

Un arc scripté qui est LA promesse Naruto : l'**examen chûnin** (épreuve
écrite, forêt, tournoi), débloqué après quelques missions et un niveau.

### 4. Une fiche qui pèse : nindō, liens, conditions, ryō

Un **nindō** (credo) et deux **liens** par personnage, avec un vrai crochet :
agir selon son nindō ou pour son lien rapporte de l'XP au débrief, et le MJ
met ces liens en scène. Affinité élémentaire qui conditionne les techniques.
Ryō et inventaire avec des objets utilisables. Conditions au-delà des
blessures : épuisé (chakra bas), sous genjutsu, empoisonné.

### 5. Une interface de table de jeu

Scène au centre ; barre d'action avec options, texte libre et techniques ;
colonne « ta fiche courte » (barres PV et chakra, conditions), « la scène »
(présents, lieu, mission et son acte, horloge) ; un **plateau de dés** animé
et sonore. En combat, le plateau remplace la colonne.

### 6. L'IA comme MJ, pas comme romancier

Scènes plus courtes, closes sur une question, enjeux annoncés avant le jet,
un MJ qui s'adresse à chaque joueur. Le modèle ne décide jamais d'un chiffre.

### Apprendre sans manuel : l'épreuve des clochettes

Première mission scriptée contre le sensei. Elle enseigne la boucle (déclarer,
lancer), le combat, la règle du fossé (on ne le bat pas seul) et le levier du
nombre (l'équipe). La scène fondatrice du genre, jouée plutôt qu'expliquée.

## Les chantiers, dans l'ordre

| | Chantier | Version |
|---|---|---|
| A | La boucle du tour : arbitrage annoncé, lancer du dé, forcer, techniques au choix, plateau de dés | 1.2 |
| B | Le combat en rounds : initiative, plateau, actions, effets des techniques, objets | 1.3 |
| C | Missions en actes, débrief noté, temps mort, ryō et boutique | 1.4 |
| D | Nindō et liens, conditions, examen chûnin | 1.5 |
| E | Interface de table, sons, épreuve des clochettes | 2.0 |

Chaque chantier sort en version intermédiaire, testée en partie réelle à deux
avant le suivant. La 2.0 marque la fin.

## Principes qui ne bougent pas

- **Python décide, le modèle raconte.** Aucun chiffre ne vient du modèle.
- **Le joueur voit le jeu.** Chaque règle qui compte s'affiche au moment où
  elle compte, jamais dans un manuel.
- **Un chemin, pas deux.** Solo, tour de table, flux ou d'un bloc : la même
  implémentation, assemblée différemment.
- **La voix, la mise à jour, les sauvegardes** ne cassent jamais un tour.
- **Gratuit.** Mistral gratuit, Ollama, Piper. Rien de payant.
