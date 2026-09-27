# Nindō 忍道

> *Devenir un ninja n’a jamais été aussi simple.*

<img src="app/web/static/logo.svg" width="140" alt="Nindō">

Jeu de rôle narratif Naruto avec un maître du jeu IA, en local sur ton PC.

Tu crées un personnage. L'IA crée ton équipe, ton instructeur et ton rival.
Puis elle devient ton maître du jeu — et tu découvres ton histoire en la jouant.

Depuis la 2.0, ça se joue **comme à une table** : le MJ annonce le jet et ses
chances, tu lances le dé toi-même, le combat se joue en rounds sur un plateau
avec des techniques aux effets réels, les missions ont trois actes et un
débrief noté, et la première mission — l'épreuve des clochettes — apprend
tout ça sans manuel. La conception est dans [docs/NINDO-2.md](docs/NINDO-2.md).

## Jouer (pour tout le monde)

1. Télécharge **`Nindo-windows.zip`** dans la dernière version publiée
   (onglet *Releases* de ce dépôt), et décompresse-le où tu veux.
2. Lance **`Nindo.exe`** (Windows le signale peut-être comme « éditeur
   inconnu » : *Informations complémentaires*, puis *Exécuter quand même*).
3. Dans le lanceur, colle ta **clé Mistral** gratuite
   ([console.mistral.ai](https://console.mistral.ai), offre « Experiment ») — ou
   installe [Ollama](https://ollama.com) pour jouer hors ligne. Les deux
   ensemble, c'est le mieux : Ollama prend le relais si Mistral refuse.
4. **Jouer.** Les mises à jour arrivent toutes seules : le lanceur les propose.

Tes parties et ta clé restent sur ton PC, dans `%APPDATA%\Nindo` : une mise à
jour n'y touche jamais.

## Publier une version (pour le mainteneur)

1. Change `VERSION` dans `app/version.py`, et ajoute la section
   `## X.Y.Z — Nom` en tête de `NOUVEAUTES.md`.
2. `git tag vX.Y.Z` puis `git push --tags`.
3. GitHub vérifie le jeu, fabrique `Nindo.exe` et publie la version
   (`.github/workflows/publier.yml`). Les lanceurs la proposent au lancement suivant.

Pour fabriquer l'application à la main : `pip install -r requirements-app.txt`
puis `pyinstaller nindo.spec` (résultat dans `dist/Nindo/`).

---

## 1. Installation

```bash
python -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # Windows : copy .env.example .env
```

Laisse `LLM_PROVIDER=mock` pour la première exécution : l'application tourne
**sans GPU et sans Ollama**, avec un modèle factice. C'est le bon moyen de
vérifier que l'installation est saine avant d'ajouter l'IA.

Ensuite, installe [Ollama](https://ollama.com), puis :

```bash
ollama pull qwen3:8b       # le narrateur
ollama pull qwen3:4b       # l'utilitaire : JSON, résumés, génération de PNJ
```

et passe `LLM_PROVIDER=ollama` dans `.env`.

---

## 2. Jouer

```bash
uvicorn app.main:app --reload
```

Ouvre http://localhost:8000 et crée une campagne. C'est tout — il n'y a rien à
préparer, rien à écrire à l'avance.

**Ce qui se passe quand tu valides ton personnage :**

1. le monde est instancié — lieux, factions, sièges de grade ;
2. ta **destinée** est écrite en entier, figée, et tu n'en reçois qu'un présage ;
3. l'IA génère ton **instructeur, deux coéquipiers et un rival**, chacun avec sa
   personnalité, sa manière de parler, ses objectifs et **un secret** que tu ignores ;
4. la scène d'ouverture s'écrit et la première mission apparaît.

Un problème ? Ouvre http://localhost:8000/sante : la page dit si Ollama répond
et quels modèles manquent.

---

## 3. Vérifier que tout marche

```bash
pip install pytest
python -m pytest tests/ -q        # 420+ tests : règles, destinée, lore, combat, missions…
python -m scripts.parcours        # parcours joueur complet, ~200 vérifications
```

Le parcours écrit dans `data/parcours.db`, jamais dans ta partie : lance-le
autant que tu veux, ton accueil n'en gardera aucune trace.

```bash
```

`scripts/parcours.py` rejoue tout sans navigateur : campagne, personnage,
destinée, distribution générée, tours joués, narration en flux, affrontement
complet, second joueur, filtre des secrets, sauvegarde. Lance-le après chaque
modification — c'est ton filet de sécurité.

---

## 4. Ce qui est en place

| Système | État |
|---|---|
| Campagne, sauvegarde, reprise | ✅ |
| Création de personnage — 4 origines, 3 modes de destinée | ✅ |
| Destinée à potentiel latent, présage, révélation progressive | ✅ |
| Distribution générée par l'IA avec secrets cachés | ✅ |
| Boucle de tour : intention → dés → narration → conséquences | ✅ |
| Propositions d'action **et** texte libre | ✅ |
| Mémoire en couches, résumés, faits atomiques | ✅ |
| Filtre de divulgation (secrets, destinée, connaissance) | ✅ |
| Hot-seat jusqu'à 3 joueurs | ✅ |
| Grades, autorité, puissance effective, tiers | ✅ |
| Maîtrise des techniques, progression par l'usage | ✅ |
| Cristallisation des PNJ rencontrés | ✅ câblée dans le tour |
| **Dossier lore injecté au MJ** — clans, techniques, armes, région, époque | ✅ |
| **Armement** — armes, outils, consommables, écoles d'armes | ✅ |
| **Bestiaire, flore, minéraux par pays** | ✅ |
| **Moteur de révélation** — éveil des destinées, secrets, connaissance | ✅ |
| **Générateur de missions** ancré sur le lore | ✅ |
| **Combat** — rencontres qui durent, postures, leviers, règle du fossé | ✅ |
| **Adversaires fabriqués depuis le lore**, sans appel au modèle | ✅ |
| **Embuscades** déclenchées par le danger du lieu | ✅ |
| **Blessures, moral, fuite, butin, repos** | ✅ |
| **Narration en flux (SSE)** — la scène s'écrit sous les yeux du joueur | ✅ |
| **Scellé de destinée en direct** — le monde naît étape par étape | ✅ |
| **Échéances de mission** — un délai dépassé fait vraiment échouer | ✅ |
| **Blessures qui se referment avec le temps**, par sévérité | ✅ |
| **Synergies de techniques** — combos, contres, prérequis opposables | ✅ |
| **Rappel sémantique** — la mémoire retrouve le sens, pas les mots | ✅ |
| **Voix** — lecture à voix haute, par personnage, hors ligne | ✅ |
| **Carte et voyage** — la distance se paie en tours | ✅ |
| **Verrou de tour** — un seul tour s'écrit à la fois par campagne | ✅ |
| **Rythme narratif** — un souffle par scène, jamais deux fois le même | ✅ |
| **Horloge du monde** — factions et PNJ avancent sans le joueur | ✅ |
| **Chronique** — la campagne relue comme un récit | ✅ |
| **Brouillard de carte** — les lieux s'apprennent en jouant | ✅ |
| **Relations qui agissent** — un palier franchi est un événement | ✅ |
| **Scènes d'entre-deux** — un creux n'est pas un vide | ✅ |
| **Trois appels par tour** au lieu de quatre | ✅ |
| **Échantillonnage réglé pour le local** — répétition, min_p, timeout | ✅ |
| **`scripts/calibrer`** — mesure sur ta machine et conseille | ✅ |
| **Largeurs étroites** — le jeu se relit sur téléphone | ✅ |
| **Schéma qui se met à niveau** — une sauvegarde survit aux versions | ✅ |
| **Montée de niveau qui se joue** — des points à placer, un plafond par caractéristique | ✅ |
| **Secrets qui pressent** — une question relancée, puis refermée si on l'ignore | ✅ |
| **« Précédemment »** — le rappel qu'on lit en rouvrant sa campagne | ✅ |
| **Apprendre une technique** — un maître, un parchemin, ou le travail | ✅ |
| **Les PNJ qui reviennent** — un visage croisé souvent devient quelqu'un | ✅ |
| **Le mot de la fin** — une campagne s'achève, et laisse un épilogue | ✅ |
| **Le ton des répliques** — une réplique criée ne se lit plus comme un murmure | ✅ |
| **Plans de village dessinés** — Konoha, Suna, Kiri, Kumo, Iwa, Oto, vus de dessus | ✅ |
| **Le français du moteur** — « à la Tour du Kage », et non « à Tour du Kage » | ✅ |
| **Les archétypes du sort** — « je m'en remets au destin » fait un réceptacle, un marqué, un ermite… | ✅ |
| **Les clans de TON village** — les dispersés repliés à part, jamais proposés en premier | ✅ |
| **Refonte « l'estampe dans la nuit »** — estampes par village (`_estampe.html`), verre, médaillons de clan (logos du wiki dans `static/emblemes/`), calligraphie (`eclat.css`) | ✅ |
| **19 clans d'après le wiki Solve** — Konoha, Suna, Kiri, Kumo, Iwa et Oto (désormais jouable), chacun avec sa lignée, sa technique et son trait de destinée | ✅ |
| **La colonne en onglets** — Lieu · Gens · Voie · Journal, un pan à la fois | ✅ |
| **Six villages à part entière** — chacun ses lieux de départ (`lore/naruto/lieux.yaml`), ses institutions, ses voisins, le titre de son Kage ; Oto a sa faune et son plan (`tests/test_villages.py`) | ✅ |
| **Le conteur en ligne, et son relais** — un service gratuit (Mistral) raconte, Ollama reprend la plume dès qu'il refuse ; badge « qui raconte », `/sante` dit quoi faire (`tests/test_relais.py`) | ✅ |
| **Le lanceur** — `Lancer Nindo.bat` : réveille Ollama, démarre le jeu, dit qui raconte, ouvre le navigateur (`scripts/lancer.py`) | ✅ |
| **Les garde-fous de la partie réelle** — récit coupé ramené à sa dernière phrase, markdown retiré, noms de la série et PNJ mineurs sexualisés corrigés par le code (`app/engine/garde.py`) | ✅ |
| **Le ménage** — effacer une campagne depuis l'accueil ; le parcours écrit dans sa propre base | ✅ |

---

## 5. Les fichiers qui comptent

| Fichier | Rôle |
|---|---|
| `app/llm/prompts.py` | **Le plus important.** Un rôle, un prompt. C'est ici que se gagne la qualité du MJ. |
| `app/lore/brief.py` | Le dossier lore de la scène. Sans lui, le MJ narrait Naruto de mémoire au lieu de le narrer depuis le pack. |
| `app/memory/context.py` | Les couches de mémoire, le budget, le filtre de divulgation, et un contexte par rôle. |
| `app/engine/reveal.py` | Ce qui ouvre ce que la création a scellé : destinées, secrets, connaissance. |
| `app/engine/missions.py` | L'ossature d'une mission vient du lore, pas du modèle. |
| `app/engine/turn.py` | La boucle de tour en six étapes, coupée en `preparer` / `conclure` pour que le flux et l'appel bloquant partagent le même jeu. |
| `app/engine/combat.py` | La rencontre qui dure. Postures, leviers, fossé, moral. Le modèle habille, il ne décide rien. |
| `app/engine/rythme.py` | Le souffle de chaque scène. Un échange de coups ne se raconte pas comme une découverte. |
| `app/engine/monde.py` | L'horloge du monde : ce qui bouge quand le joueur regarde ailleurs. |
| `app/engine/progression.py` | Les points de niveau, le plafond, et le fait qu'on ne dépense jamais à la place du joueur. |
| `app/engine/secrets.py` | Les deux horloges d'un secret : le monde y revient, puis il est trop tard. |
| `app/engine/reprise.py` | « Précédemment » — le relevé déterministe, et son récit mis en cache. |
| `app/engine/apprentissage.py` | Les trois chemins vers une technique nouvelle, et ce qu'ils coûtent en temps. |
| `app/engine/epilogue.py` | Le mot de la fin : quand on peut conclure, ce qu'on écrit, et comment on rouvre. |
| `app/engine/francais.py` | Les petits mots. « à la Tour du Kage » plutôt que « à Tour du Kage ». |
| `app/web/templates/_plan.html` | Les plans de village, dessinés en SVG. La géographie vient du canon, le trait est d'ici. |
| `app/engine/carte.py` | Les lieux, leurs distances, ce que coûte un voyage, et ce qu'on ignore encore. |
| `app/engine/voix.py` | Le découpage vocal, en Python : un dialogue français est entre guillemets. |
| `app/llm/embeddings.py` | Le rappel par le sens, et sa dégradation silencieuse. |
| `app/db.py` | Ajoute aux tables existantes les colonnes qui leur manquent — une sauvegarde survit aux versions. |
| `lore/naruto/adversaires.yaml` | Ce qui se met en travers du chemin. Le champ `tactique` est celui que le MJ lit vraiment. |
| `app/engine/campaign.py` | L'amorce : monde, distribution générée, scène d'ouverture. |
| `app/engine/destiny.py` | Le générateur de destinée, éligibilité et pondérations. |
| `app/engine/crystallize.py` | Un PNJ inconnu devient un personnage — et ne change plus jamais. |
| `app/engine/validators.py` | Rien n'atteint la base sans passer ici. |
| `app/rules/engine.py` | Dés, puissance, tiers, fossé, éléments. Testable à 100 %. |
| `lore/naruto/*.yaml` | L'univers comme donnée. Ajouter un clan ne demande aucun code. |
| `lore/naruto/armes.yaml` | Armement et matériel. Le champ `tactique` est celui que le MJ lit vraiment. |
| `lore/naruto/bestiaire.yaml` | Faune, flore, minéraux par pays — chaque entrée porte une `accroche` de mission. |
| `lore/naruto/missions.yaml` | Les archétypes de mission. La variété est une donnée, pas un espoir. |
| `rulesets/naruto.yaml` | Les mécaniques comme donnée. |
| `app/web/templates/_emblemes.html` | Les sceaux : une plaque de bandeau frontal, une gravure par village et par clan. Tout le dessin est là, et nulle part ailleurs. |
| `app/web/templates/_paysages.html` | Les douze paysages du monde, en SVG. Trois plans, de la brume entre les plans, un motif de cimes. |
| `app/web/static/monde.css` | L'identité visuelle — métal, tissu, papier, encre, et la mise en scène illustrée. Retire la ligne de `base.html` et l'interface redevient neutre. |

---

## 6. Les règles du moteur

Douze constantes. Si une modification les contredit, c'est la modification qui
a tort.

1. **La base est la source de vérité.** Le LLM ne décide jamais d'un résultat chiffré.
2. **Le moteur décide, le modèle habille.** En cas de désaccord, le moteur gagne.
3. **Tout ce qui varie est une donnée.** Ajouter un clan ou une technique ne
   demande pas de modifier du Python.
4. **Une interface par dépendance externe** — LLM, plus tard voix et embeddings.
5. **Le journal est append-only.** On n'efface jamais un tour, un fait, un événement.
6. **Ce qui est généré est figé.** Une fiche cristallisée ne se régénère pas.
7. **Un seul filtre de divulgation.** Pas d'exception pour un cas particulier.
8. **Le budget de contexte est fixe** — il ne grandit ni avec le monde, ni avec la durée.
9. **Toute sortie du modèle est validée avant écriture.**
10. **L'IA ne contrôle jamais un personnage joueur.**
11. **Le pack lore est la vérité de l'univers**, y compris quand le souvenir de
    la série dit autre chose. Un MJ qui n'a pas lu le pack n'est pas le MJ de
    CE monde.
12. **Un combat perdu n'est pas une campagne perdue.** On tombe, on garde une
    cicatrice, on se relève. La mort est un réglage de campagne, jamais un
    accident de dé.

---

## 7. Arborescence

```
naruto-jdr/
├── app/
│   ├── main.py            point d'entrée FastAPI
│   ├── config.py          réglages lus depuis .env
│   ├── db.py              base et sessions
│   ├── models.py          tout le schéma de données
│   ├── rules/             dés, puissance, tiers, fossé
│   ├── lore/              pack (chargement, interrogation) et brief (dossier de scène)
│   ├── llm/               provider, schémas JSON, prompts
│   ├── memory/            contexte, budget et filtre de divulgation
│   ├── engine/            campagne, création, destinée, tour, combat,
│   │                      révélation, missions, cristallisation
│   ├── api/routes.py      HTTP : pages et API JSON
│   └── web/               gabarits Jinja et CSS
├── lore/naruto/           monde, clans, techniques, destinée, armes,
│                          bestiaire, adversaires, missions, sites et cultes
├── rulesets/naruto.yaml   les mécaniques
├── scripts/parcours.py    test de bout en bout
├── tests/                 moteur de règles et générateur
└── data/parties.db        créée au premier lancement
```

---

## 8. Le combat

Un affrontement n'est pas un jet de dé : c'est une **rencontre qui dure**.
Elle s'ouvre, compte ses échanges, se ferme. Tant qu'elle est ouverte, chaque
tour de jeu est un échange.

**Quatre idées, et elles se tiennent.**

1. **La posture est le choix.** Six intentions — offensive, mesurée, défensive,
   technique, manœuvre, désengagement — chacune un compromis chiffré dans le
   ruleset. Personne ne peut rester en offensive douze échanges : la défense
   s'effondre. Le jeu est là, pas dans le dé.
2. **Les leviers réduisent l'écart, ils ne donnent pas de bonus.** Un genin ne
   bat pas un nukenin en frappant mieux. Il le bat en préparant le terrain, en
   apprenant sa faiblesse, en acceptant d'y laisser quelque chose. Trois
   leviers ramènent « ce n'est plus un combat » à « difficile mais possible »
   — et **jamais au-delà** : le plancher d'un cran est la règle.
3. **Quand vaincre est hors de portée, on ne lance pas les dés pour vaincre.**
   Le moteur impose les objectifs réellement ouverts — fuir, retarder,
   protéger, obtenir une réponse — et compte les succès qui y mènent. Frapper
   un adversaire écrasant ne fait rien avancer, et le joueur l'apprend en une
   scène plutôt qu'en lisant une règle.
4. **Personne ne se bat jusqu'à la mort.** Les PNJ rompent le contact quand le
   moral lâche, et un personnage joueur qui tombe est relevé — sauf en
   difficulté `impitoyable`. Une mort sur un mauvais jet au tour 30 détruit une
   campagne ; le genre a toujours préféré la capture, la dette et la cicatrice.

Les adversaires sont **fabriqués depuis le lore, sans aucun appel au modèle** :
une embuscade s'ouvre instantanément. Leur puissance n'est pas écrite à la main
— `stats_pour_tier` monte les caractéristiques jusqu'au tier demandé, donc
rééquilibrer `seuils_tier` rééquilibre tout le bestiaire du même geste.

---

## 9. Le parti pris visuel

Une interface juste et lisible peut être celle de n'importe quelle application
sombre. Celle-ci doit se reconnaître avant d'être lue.

### Les paysages

Chaque village a son pays, **dessiné en SVG** dans
`app/web/templates/_paysages.html`. Ils servent partout : le bandeau de
l'accueil, l'en-tête de la création, les cartes de village, les vignettes de
campagne, le lieu où l'on se trouve à la table, l'en-tête de la fiche.

La méthode est la même pour les douze : un ciel en dégradé profond qui donne
l'heure, un astre bas avec son halo, **trois plans de silhouettes** du plus
clair au plus sombre, des voiles de brume *entre* les plans, et un premier plan
très sombre qui encadre. La perspective atmosphérique fait tout le travail de
profondeur. Les masses sont lisses et c'est un **motif de cimes** qui les
dentelle — un relief anguleux plus des cimes donnait de grosses tentes vertes.

Chaque plan porte une classe `p1` `p2` `p3` ; au défilement, une vingtaine de
lignes de JavaScript dans `base.html` les décale à des vitesses différentes.

**Pourquoi du vectoriel plutôt que des images.** Le jeu doit tourner hors
ligne, tenir dans un dépôt qu'on peut lire, et n'embarquer aucune œuvre tierce.
Un paysage pèse deux kilo-octets, reste net sur tout écran, se décline en dix
versions en changeant trois dégradés et se laisse animer par couches.

Un décor n'est dessiné **qu'une fois par page** (`paysage_defs`) et les vues
n'en tiennent qu'une référence (`paysage_ref`). Sur une base de trente-six
campagnes, le redessiner à chaque carte coûtait 339 Ko et deux secondes et
demie de rendu.

Ajouter un paysage : une branche de plus dans la macro `scene`. Ni code Python,
ni fichier à déposer.

### Les plans de village

Un paysage est une vue **de face** : il dit à quoi ressemble un endroit. La
carte du jeu avait besoin d'autre chose — une vue **de dessus** — et utilisait
faute de mieux un paysage flouté. Les coordonnées des lieux ne voulaient donc
rien dire : « au nord-est » tombait sur un morceau de ciel.

`app/web/templates/_plan.html` dessine un vrai plan par village, dans le même
repère que `Location.x/y` (un `viewBox 0 0 100 100`), si bien qu'une épingle
posée à (53, 49) tombe exactement où le plan dit qu'elle tombe. Les lieux de
départ de chaque village, avec leurs coordonnées accordées à son plan, sont
dans `lore/naruto/lieux.yaml` : un village ajouté là n'hérite plus des lieux
de Konoha.

| Village | Ce que le plan montre |
|---|---|
| **Konoha** | une forêt, une muraille percée d'une porte au sud, le Rocher des Hokage et ses cinq visages taillés, la Naka et ses ponts, les îlots de maisons, le quartier Uchiha à part, la clairière d'entraînement et ses trois poteaux |
| **Suna** | une vallée fortifiée derrière des falaises, une **seule** faille pour entrer, des dômes d'argile éclairés du nord-ouest, le bâtiment du Kazekage marqué 風 |
| **Kiri** | des îles dans la brume, des tours cylindriques aux toits végétalisés, des ponts, les montagnes qui la cachent |
| **Kumo** | des aiguilles de roche au-dessus des nuages, des ponts suspendus, le bâtiment du Raikage bâti *dans* le plus haut piton |
| **Iwa** | des tours de pierre à toit conique, reliées par un réseau de ponts, la résidence du Tsuchikage marquée 土 |
| **Oto** | des rizières en terrasses et un marais, le repaire creusé dans la colline aux portes rondes marqué 音, la digue du Riz au nord-ouest, la forêt des laboratoires au nord-est |

**La géographie vient du canon, le trait est d'ici.** On lit ce que le monde
dit de chaque village et on le dessine : aucune image n'est recopiée, aucune
n'est téléchargée. Un village qu'on n'a pas dessiné retombe sur un plan
générique plutôt que sur rien.

### Quatre matières, et elles suffisent

| Matière | Où | Ce qu'elle porte |
|---|---|---|
| **Le métal** | chaque sceau, la marque du bandeau | la plaque frappée d'un bandeau frontal, gravée, avec ses quatre rivets |
| **Le tissu** | le liseré de chaque plaque, la barre du haut | la couleur d'appartenance — vert Konoha, or Suna, rouge Uchiha |
| **Le papier** | la scène de jeu | un rouleau, avec ses baguettes et sa lettrine : on lit une chronique, pas un tableau de bord |
| **L'encre** | titres, sceaux, filigranes | le trait de pinceau, le hanko rouge, le kanji du village derrière sa carte |

Deux règles tiennent l'ensemble.

1. **Aucune décoration ne coûte en lisibilité.** Les motifs restent sous 8 %
   d'opacité, le texte de récit gagne en contraste au lieu d'en perdre, et tout
   ce qui bouge s'arrête sous `prefers-reduced-motion`.
2. **Une seule table de couleurs.** `[data-cle="konoha"]` définit l'accent du
   village dans `jeu.css` ; poser cet attribut suffit à teinter le bandeau,
   le filigrane, le filet de la carte et le sceau de sélection — à la création
   comme à la table, sur la fiche comme sur l'accueil.

Tout le dessin est dans `app/web/templates/_emblemes.html`, tout l'habillage
dans `app/web/static/monde.css`. Retirer la ligne `monde.css` de `base.html`
rend l'interface neutre sans rien casser : c'est exactement pour cela que les
deux feuilles sont séparées.

**Les sceaux sont des compositions originales, pas des reproductions.** Le
projet n'embarque aucune image tierce. Si tu veux les tiennes — celles d'un
wiki, les tiennes, celles d'un artiste — dépose-les dans
`app/web/static/emblemes/` nommées d'après l'identifiant : elles prennent le
pas sur tout, sans toucher au code. Détails et conseils de rendu dans
`app/web/static/emblemes/LISEZ-MOI.txt`.

---

## 10. La séance

Trois systèmes existent uniquement parce que ce jeu se joue **une soirée par
semaine, à plusieurs, sur un écran partagé**. Ils ne servent à rien sur une
partie d'un seul trait, et ils changent tout sur une campagne qui dure.

### « Précédemment » — `app/engine/reprise.py`

Rouvrir sa table après dix heures d'absence affiche un rappel avant la scène :
où l'on est, avec qui, ce que disait le dernier chapitre, les délais qui
courent, les questions ouvertes, les points de niveau non placés.

Il se construit en **deux étages**. Le relevé est déterministe et instantané —
il s'affiche avec la page, et il marche avec Ollama éteint. Le récit est une
génération courte écrite par-dessus, substituée quand elle arrive et **mise en
cache** : on la paie une fois par reprise, pas une fois par rechargement. Le
joueur ne voit donc jamais d'écran d'attente.

Réglages : `HEURES_AVANT_RAPPEL` et `TOURS_MINIMUM` dans le module.

### Les secrets ont une horloge — `app/engine/secrets.py`

Un secret entamé puis laissé de côté restait entamé jusqu'au tour 200. Il a
maintenant **deux horloges**, réglées dans `rulesets/naruto.yaml` sous
`secrets:` :

- **le rappel** — après six tours sans progrès, le monde y revient de lui-même.
  C'est une consigne au narrateur, pas une révélation : le palier ne tombe pas.
  Trois relances espacées, puis on lâche.
- **la fenêtre** — après vingt tours sans progrès, il est trop tard. La vérité
  reste en base ; elle n'est plus atteignable en jeu, et la chronique en garde
  la trace.

C'est le seul endroit du jeu où **ne rien faire** a une conséquence écrite.
Sans ça, la curiosité ne coûte rien et ne rapporte rien.

### Les gens reviennent — `app/engine/crystallize.py`

Un figurant croisé trois fois cesse d'être un figurant : sa fiche se complète,
une relation naît vers le joueur, et il entre dans le panneau de l'entourage
comme dans le contexte du narrateur. **Une promotion par tour au maximum** —
en promouvoir trois dans la même scène les banaliserait toutes.

Et le monde est petit : en arrivant quelque part, on peut tomber sur une
connaissance. Seules les personnes DÉJÀ promues bougent ainsi ; un figurant
reste où il est, et c'est ce qui distingue un décor d'un personnage.

### Apprendre une technique — `app/engine/apprentissage.py`

Trois chemins, et ils ne valent pas pareil. **Un maître** présent qui connaît
vraiment la technique — le chemin court, et le seul qui donne un départ
au-dessus du minimum. **Un parchemin**, qui se consomme — c'est ce qui donne
une valeur au butin. **Le travail**, seul, dans sa spécialité ou sur les bases
de l'Académie — le filet qui ne dépend de personne.

Tous coûtent du **temps de campagne** : un rang C prend trois tours, et les
échéances de mission courent pendant qu'on s'entraîne. L'éligibilité est
entièrement déterministe — rang, grade, caractéristique, clan, prérequis — et
s'entraîner marche avec Ollama éteint, comme le repos.

### Le mot de la fin — `app/engine/epilogue.py`

`Campaign.phase` connaissait « terminee » et rien ne la déclenchait. On peut
maintenant **clore une chronique** depuis la page de chronique, une fois qu'il
y a de quoi raconter : vingt-cinq tours joués, ou une destinée menée à son
terme. Le jeu dit qu'on PEUT conclure ; c'est la table qui décide.

Clore écrit un épilogue — le seul texte qu'on montre à quelqu'un qui n'a pas
joué — et ferme la table. Le filtre de divulgation tient jusqu'au bout : une
question restée ouverte reste ouverte. Et **on rouvre en un clic**, sans rien
effacer.

### Monter de niveau se joue — `app/engine/progression.py`

`progression.points_par_niveau` est enfin dépensé. Les points s'accumulent tant
que le joueur ne choisit pas — on ne décide jamais à sa place — et
`progression.stat_max` empêche la pente optimale qui consiste à tout verser
dans la même case. Le panneau apparaît en haut de la colonne sur la table et
sur la fiche, et la puissance effective est recalculée **au moment du clic** :
le tier bouge sous les yeux du joueur, pas au tour suivant.

---

## 11. Étape suivante

Joue quarante tours d'affilée sans toucher au code, et note ce qui te gêne.
Cette liste sera plus juste que tout ce qu'on peut planifier maintenant — et
la moitié des corrections tiendront dans `app/llm/prompts.py` ou dans un
fichier YAML du lore.

**Le jeu se joue en local, à plusieurs autour d'un écran.** Le multijoueur en
réseau n'est pas un objectif : le hot-seat couvre le besoin, et un partage
d'écran fait le reste. Le verrou de tour (`app/engine/verrou.py`) est là pour
empêcher deux onglets de décaler l'horloge, pas pour ouvrir le jeu au réseau.

Ce qui reste à construire, par ordre de gain :

1. **Le passage de grade.** `grades` décrit chûnin et jônin, leurs droits et
   leurs sièges ; personne ne monte jamais. Un examen de chûnin est le premier
   grand arc que ce moteur sait déjà presque jouer.
2. **Les objets qui comptent.** L'inventaire est une liste de chaînes. Une
   arme n'a pas de statistiques opposables alors que `armes.yaml` en porte.
3. **Les factions qu'on rejoint.** Elles agissent (`monde.py`), on ne peut pas
   en être. Rejoindre la Racine ou l'Akatsuki devrait changer ce qu'on reçoit
   comme missions.
4. **Les plans de village pour les douze pays.** Six sont dessinés ; les
   autres retombent sur un plan générique.

Ce qui a été fermé au dernier passage : la montée de niveau se joue
(`progression.py`), les secrets ont une horloge (`secrets.py`), on reprend sur
un « Précédemment » (`reprise.py`), on apprend des techniques
(`apprentissage.py`), les figurants deviennent des gens (`crystallize.py`), une
campagne peut s'achever (`epilogue.py`), une réplique criée ne se lit plus
comme un murmure (`voix.py`), la carte est un vrai plan (`_plan.html`) et le
moteur écrit enfin un français correct (`francais.py`).

---

## 12. Le modèle local

Un tour, c'est **trois appels** : l'arbitre lit l'action, le narrateur écrit,
le simulateur extrait les conséquences ET les pistes. Le quatrième appel a été
supprimé — conséquences et propositions sortent du même texte et du même
regard, les demander séparément faisait relire la scène deux fois pour rien.

**Mesure avant de régler** : `python -m scripts.calibrer` joue un tour complet
avec des prompts de taille réelle sur ta machine et te dit quoi changer. En
dessous de 15 jetons/seconde, le modèle est trop gros pour la carte.

**Le réglage qui change le plus la qualité perçue est `LLM_REPETITION`.** Un
modèle de 8 milliards de paramètres reprend ses propres tournures d'un tour à
l'autre, et au bout de dix tours le joueur reconnaît les phrases avant de les
lire. C'était laissé au défaut d'Ollama ; c'est maintenant à 1.10, avec une
fenêtre de 320 jetons.

### Une règle qui vaut pour la suite

Trois des ajouts de cette passe n'ont rien inventé : ils ont **branché ce qui
existait déjà**. `Condition.guerit_tour` était déclaré et lu par personne,
`Quest.echeance_tour` était posé et jamais comparé à l'horloge, et le bloc
`synergies:` de `techniques.yaml` — combos, contres, prérequis — dormait depuis
le premier jour.

Avant d'ajouter un village, un clan ou une technique, cherche donc d'abord le
champ ou la donnée qui promet quelque chose que le code ne tient pas. Le pack
lore est déjà plus riche que ce que le moteur exploite.
