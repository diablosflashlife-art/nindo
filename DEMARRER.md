# Lancer et tester Nindō

> **Pour jouer : double-clique `Lancer Nindo.bat`.** Il réveille
> Ollama si besoin, démarre le jeu, dit qui racontera (Mistral, Ollama en
> secours) et ouvre le navigateur. Garde la fenêtre noire ouverte pendant la
> partie : la fermer arrête le jeu, et chaque tour est déjà enregistré. Au tout
> premier lancement, il installe lui-même ce qu'il faut (une minute).
>
> La suite de ce document sert à installer l'IA et à vérifier le jeu.

Trois modes :

- **mock** — aucun modèle, réponses factices. Sert à vérifier que l'installation
  tient debout et à regarder l'interface. Instantané, pas de GPU.
- **ollama** — le maître du jeu est un modèle qui tourne sur ta machine, hors
  ligne, sans rien envoyer nulle part. Illimité, mais plafonné par ta carte
  graphique (un modèle de 8 milliards de paramètres sur 8 Go).
- **en_ligne** — **le mode conseillé.** Un service gratuit (Mistral par défaut)
  raconte avec un modèle bien plus gros, et Ollama prend le relais tout seul
  dès que le service refuse (quota, réseau). Voir la section 3 bis.

---

## 1. Installation (une seule fois)

```bash
cd naruto-jdr

python -m venv .venv
# Windows :
.venv\Scripts\activate
# Linux / macOS :
source .venv/bin/activate

pip install -r requirements.txt

copy .env.example .env      # Windows
cp .env.example .env        # Linux / macOS
```

## 2. Vérifier à blanc (mode mock)

Deux commandes qui ne lancent rien de graphique et te disent si tout va bien :

```bash
python -m pytest tests/ -q     # 321 tests : règles, destinée, combat, mémoire…
python -m scripts.parcours     # crée une campagne, joue des tours, ~180 vérifications
                               # (dans data/parcours.db — ta partie n'est pas touchée)
```

Le parcours doit finir sur `Tout est vert. Le jeu fonctionne de bout en bout.`
S'il échoue, inutile d'aller plus loin : le problème est dans l'installation,
pas dans l'IA.

Puis :

```bash
uvicorn app.main:app --reload
```

Ouvre <http://localhost:8000>. Tu peux créer une campagne, créer un personnage,
jouer des tours — la narration sera préfixée `[mock]`, c'est normal.

---

## 3. Brancher l'IA

### a. Installer Ollama

<https://ollama.com/download> — il tourne en service en arrière-plan.

### b. Télécharger le modèle

```bash
ollama pull mistral-nemo:12b-instruct-2407-q4_K_M   # recommandé (~7 Go)
# ou, si la VRAM ne suit pas :
ollama pull qwen3:8b                                 # (~5 Go)

# Optionnel mais recommandé : le rappel par le sens (~270 Mo)
ollama pull nomic-embed-text
```

**À quoi sert `nomic-embed-text`.** Sans lui, la mémoire longue se rappelle par
les mots : tu écris « ce que j'ai juré à Tetsuo » et le fait enregistré dit
« a promis au vieux forgeron » — aucun mot commun, le souvenir ne remonte pas.
Avec lui, les deux se reconnaissent. Il est minuscule et cohabite sans peine
avec le gros modèle. S'il n'est pas là, le jeu tourne exactement comme avant :
`/sante` te le dira.

**Un seul modèle, pas deux.** C'était l'erreur de la première version : un 8B
pour narrer et un 4B pour le JSON. Sur une carte de 8 Go, 5 Go et 2,5 Go ne
cohabitent pas — Ollama décharge et recharge à chaque bascule, deux fois par
tour. Le petit modèle faisait gagner quelques secondes de génération et en
coûtait dix de chargement. `LLM_MODELE_UNIQUE=true` règle ça.

**Pourquoi Mistral Nemo plutôt que qwen3 :** il est entraîné sur du français, et
ça s'entend immédiatement sur la prose d'un MJ. qwen3 reste un bon choix si la
VRAM est juste — mais laisse `LLM_REFLEXION=false`, sinon ses blocs `<think>`
partent dans la narration et le joueur lit le brouillon du modèle.

Vérifie que ça répond :

```bash
ollama run qwen3:8b "Dis bonjour en une phrase."
```

Si la réponse contient un bloc de réflexion avant la phrase, c'est normal : le
jeu le supprime (`LLM_REFLEXION=false` plus un nettoyage de sécurité dans
`app/llm/provider.py`).

### c. Basculer le jeu

Dans `.env`, une seule ligne à changer :

```ini
LLM_PROVIDER=ollama
```

Relance `uvicorn`, puis ouvre <http://localhost:8000/sante>. C'est la page qui
te dit tout :

```json
{
  "fournisseur": "ollama",
  "modele": "qwen3:8b",
  "ollama": "joignable",
  "modeles_installes": ["qwen3:8b", "qwen3:4b"],
  "manquants": [],
  "pret": true
}
```

- `"ollama": "INJOIGNABLE"` → le service Ollama n'est pas démarré.
- `"manquants": [...]` → la page te donne la commande `ollama pull` à lancer.
- `"pret": true` → tu peux jouer.

## 3 bis. Le conteur en ligne, avec Ollama en secours (conseillé)

**Pourquoi.** Ta carte de 8 Go plafonne à un modèle de 8 milliards de
paramètres : c'est le bas de gamme pour un maître du jeu (français moyen,
consignes oubliées, mémoire courte). L'offre gratuite de Mistral prête un
modèle bien plus gros, et Mistral écrit un français naturel.

**Aucun service gratuit n'est illimité.** C'est pourquoi le jeu garde Ollama
en secours : quand Mistral refuse (quota atteint, coupure réseau, clé
refusée), la scène est racontée par Ollama sans que le tour échoue. Le jeu
réessaie Mistral après une pause (10 minutes par défaut, `RELAIS_PAUSE`). La
mémoire de la partie ne se perd pas au passage : elle est dans la base, pas
dans l'IA, et chaque appel reçoit tout le dossier de la scène.

Un badge dans la barre de la table dit **qui raconte** : vert pour Mistral,
ambré quand Ollama a pris le relais (survole-le pour savoir pourquoi).

### a. Créer la clé Mistral (gratuite)

1. Crée un compte sur <https://console.mistral.ai>.
2. Choisis l'offre gratuite (« Experiment »). Elle demande une vérification
   par téléphone. **N'enregistre aucun moyen de paiement** : sans carte, rien
   ne peut t'être facturé ; au pire le service refuse, et Ollama raconte.
3. Dans « API Keys », crée une clé et copie-la.

### b. La mettre dans `.env`

```ini
LLM_PROVIDER=en_ligne
EN_LIGNE_SERVICE=mistral
EN_LIGNE_CLE=ta-clé-ici
LLM_SECOURS=ollama
```

La clé reste sur ta machine, dans `.env`. Ne montre pas ce fichier en stream.

### c. Vérifier

Relance le jeu et ouvre <http://localhost:8000/sante> :

- `"en_ligne": {"etat": "joignable"}` → Mistral répond.
- `"secours_ollama": {"ollama": "joignable"}` → le secours est prêt (installe
  Ollama et son modèle comme en section 3).
- `"pret": true` dès que l'un des deux répond.

**Autres services.** `EN_LIGNE_SERVICE` accepte aussi `gemini`, `groq`,
`openrouter` et `lmstudio` (local). `EN_LIGNE_MODELE` choisit un autre modèle
du service ; vide, le jeu prend celui qu'il conseille.

---

## 4. Ta première vraie partie

1. **Nouvelle campagne** depuis l'accueil : un nom, une époque, un ton.
2. **Création du personnage**, cinq étapes, et la première commande tout :
   **destinée** (tracer sa route soi-même, ou s'en remettre au destin) →
   identité et village → origine (clan de ton village, ou aucun) → voie et
   caractéristiques → sceau.

   **Les clans sont ceux de ton village.** Choisir Suna propose Sabaku et
   Chikamatsu, pas Uchiha. Les clans dispersés (Uzumaki, Kaguya), qui se
   rencontrent partout, sont repliés à part et jamais proposés en premier.

   **Si tu t'en remets au destin**, le sort tire ton village, ta maison, ta
   voie — et un **archétype** : l'orphelin, l'enfant de clan, le réceptacle
   d'un bijû, le marqué, l'ermite en devenir, le porteur de sceau, le
   contractant, la lame, les mains qui savent. Tu ne saisis que ton nom et
   ton visage ; le sceau t'annonce ce que le sort a fait de toi — le nom et
   une phrase, jamais la vérité de ce qui sommeille. Les archétypes rares
   sortent vraiment : un réceptacle, ça arrive.
3. **« Sceller ma destinée »** — c'est le moment lourd. Le moteur amorce le
   monde, puis le modèle **génère ton instructeur, tes deux coéquipiers et ton
   rival**, chacun avec son caractère, sa façon de parler et un secret que tu
   ignores. Compte **une à trois minutes** sur une RTX 4060 : c'est le seul
   endroit où l'attente est longue, et elle n'arrive qu'une fois par campagne.

   Tu la passes sur **l'écran du sceau**, qui montre l'amorce se faire : chaque
   étape est annoncée, ton présage s'affiche dès qu'il est écrit, puis ton
   entourage se retourne un visage après l'autre. Ne ferme pas la page — c'est
   là que ta campagne naît. Si la liaison casse, rien n'a été écrit à moitié :
   reviens à la création et recommence.
4. **La table** : tu écris ce que tu tentes, ou tu cliques une des pistes
   proposées. La colonne de droite tient sur un écran grâce à quatre onglets —
   **Lieu** (où tu es, la carte), **Gens** (entourage, missions), **Voie**
   (techniques, ce que tu peux apprendre), **Journal** — et un onglet qui a
   quelque chose d'urgent le dit d'une pastille. Le choix est retenu par
   campagne. La scène **s'écrit sous tes yeux, mot à mot** — le dé apparaît
   avant la première phrase, la narration arrive ensuite. Le tour complet prend
   toujours 10 à 30 secondes, mais tu lis pendant.

### Quand ça tourne au combat

Un affrontement n'est pas un jet de dé de plus : c'est une **rencontre** qui
dure plusieurs tours. Un panneau rouge apparaît au-dessus de ta zone d'action,
et une rangée de boutons te demande **ce que tu risques cet échange** :

| Posture | Ce qu'elle fait |
|---|---|
| Offensive | Tu frappes fort et tu t'ouvres. |
| Mesurée | Ni risque pris, ni avantage gagné. |
| Défensive | Tu encaisses et tu attends l'ouverture. |
| Technique | Tu engages du chakra — et il descend vraiment. |
| Manœuvre | Tu ne frappes pas : tu prépares. Terrain, piège, faiblesse repérée. |
| Désengagement | Tu cherches à rompre le contact. |

Tu écris toujours librement ce que tu fais ; la posture dit seulement ce que la
mécanique doit en retenir.

**La chose à comprendre :** contre un adversaire trop fort, le panneau affiche
« Vaincre est hors de portée » et te donne les issues réellement ouvertes —
fuir, retarder, protéger, obtenir une réponse. Le frapper ne mènera à rien. Ce
qui marche, ce sont les **manœuvres** : chacune réussie referme l'écart d'un
cran. Trois manœuvres, et le panneau te dit « l'écart est refermé, vaincre
redevient possible ». C'est le cœur du jeu, et c'est aussi tout le genre.

Tes coéquipiers et ton sensei se battent avec toi s'ils sont là, et les
attaques adverses se répartissent entre vous. Seul, contre trois, tu tombes —
c'est voulu.

Tu ne meurs pas d'un combat perdu (sauf en difficulté `impitoyable`) : tu
tombes, tu gardes une blessure, et le bouton **« Souffler et panser tes
blessures »** apparaît dans la colonne de droite dès que la rencontre est
close. Il ne passe ni par le modèle ni par la narration — il marche même si
Ollama ne répond plus.

### Monter de niveau

Quand tu gagnes un niveau, l'effet du tour l'annonce : `NIVEAU 3 atteint ! 2
point(s) à placer.` Un panneau **« Tu as progressé »** apparaît en haut de la
colonne de droite, sur la table comme sur ta fiche, avec toutes tes
caractéristiques et une flèche par clic.

Rien ne t'oblige à choisir tout de suite : les points s'accumulent, et garder
deux points en réserve avant une mission difficile est une décision valable.
Chaque caractéristique plafonne à 30 — on ne fabrique pas un genin qui frappe
comme un Kage et ne sait rien faire d'autre. Ton rang de puissance se
recalcule au moment du clic : si tu changes de tier, on te le dit.

### Apprendre une technique

Un panneau **« Ce que tu peux apprendre »** apparaît dans la colonne de droite
et, en entier, sur ta fiche. Trois chemins peuvent t'ouvrir une technique :

- **un maître présent** — quelqu'un sur place qui la maîtrise vraiment et qui
  ne te déteste pas. C'est le chemin court, et le seul qui donne un meilleur
  départ ;
- **un parchemin** dans ton inventaire — ils se trouvent sur les vaincus. Il
  se consomme ;
- **le travail**, seul, dans ta spécialité ou sur les bases de l'Académie.

**S'entraîner coûte des tours de campagne**, et les échéances de mission
courent pendant ce temps-là. Partir en formation trois tours quand il en reste
deux avant la livraison est une décision, pas un bouton gratuit.

Les techniques hors de portée restent affichées, avec ce qui manque : une
caractéristique trop basse, ou un prérequis. C'est ton objectif d'entraînement.

### Les gens finissent par te connaître

Un passant que tu croises trois fois cesse d'être un passant : sa fiche se
complète, il apparaît dans **« Celles et ceux qui t'entourent »**, et le
maître du jeu sait désormais ce qu'il veut. Tu verras la ligne « X n'est plus
un inconnu ».

Et en arrivant quelque part, tu peux tomber sur l'une de ces connaissances.
Les figurants, eux, restent où ils sont — c'est ce qui fait la différence
entre un décor et quelqu'un.

### Finir une campagne

Au bout de vingt-cinq tours, ou dès qu'une destinée est arrivée à son terme,
la page **chronique** propose de **clore la chronique**. Le maître du jeu
écrit alors l'épilogue : ce que tu as décidé, ce que tu as perdu, qui est
resté, et ce que tu n'as jamais su. C'est le texte qu'on montre à quelqu'un
qui n'a pas joué.

La table refuse ensuite toute action. **Rouvrir est à un clic** et n'efface
rien : l'épilogue reste dans la chronique, daté du tour où tu croyais avoir
fini.

### Reprendre la semaine suivante

Si tu rouvres une campagne après plus de dix heures, un bloc **« Précédemment »**
s'affiche avant la scène : où tu es, avec qui, ce qui court, ce qui reste sans
réponse, ce que tu n'as pas placé. Il s'affiche **immédiatement** dans sa
version sèche, puis se remplace par une version racontée quand le modèle a
fini — et il est écrit une seule fois, pas à chaque rechargement. La croix en
haut à droite le referme.

C'est fait pour la partie du vendredi soir : personne n'a besoin de faire
défiler l'historique pour se rappeler qui était le type qu'on devait retrouver.

### Les questions qu'on laisse traîner

Chaque PNJ important porte un secret, dont tu découvres des bouts à force de le
côtoyer. **Un secret entamé ne t'attend plus indéfiniment.** Au bout de
quelques tours sans progrès, le monde y revient de lui-même — une remarque, un
objet mal rangé, quelqu'un d'autre qui pose la question. Trois relances, puis
plus rien.

Et au bout de vingt tours sans avancer, il est **trop tard** : tu lis « Une
piste s'est refermée », la chronique l'inscrit, et la réponse — qui existait
depuis le premier jour — n'est plus atteignable. C'est voulu. Une campagne où
ignorer les choses ne coûte rien est une campagne où s'y intéresser ne rapporte
rien.

### Ce qu'il faut regarder pour juger que l'IA fait son travail

- **Le dé est visible sous la narration** (`dé 13 · 13 contre 12 · réussite`).
  Le jet est lancé en Python **avant** la narration : le modèle raconte un
  résultat qu'il n'a pas choisi. Tente une action absurde pour un genin — tu
  dois pouvoir échouer.
- **Les PNJ ont une voix.** Ton rival et ton instructeur ne doivent pas parler
  pareil. C'est le germe culturel du village plus le détail imposé à la
  génération.
- **La mémoire tient.** Mentionne au tour 15 un détail du tour 3 : il doit être
  repris correctement. Le contexte est reconstruit à chaque tour sur quatre
  couches sous un budget fixe de jetons.
- **Les secrets ne fuient pas.** Un PNJ ne doit jamais révéler ce que tu n'as
  pas découvert. Le filtre est vérifié automatiquement par `scripts.parcours`.
- **La fiche bouge.** XP, relations, états, techniques : tout ce que la
  narration affirme passe par la base. Si un PNJ te déteste après un mensonge,
  sa relation doit baisser dans la colonne de droite.
- **Le MJ connaît le monde.** C'est le test le plus parlant. Parle à un PNJ
  Uchiha : il ne doit pas être « fier », il doit appartenir à un clan écarté du
  pouvoir par le village qu'il a fondé, et ça doit s'entendre. Demande une
  mission au bureau : elle doit citer un lieu et un objet qui existent
  réellement dans `lore/naruto/`. Lance un combat : le MJ doit nommer tes
  shuriken, ton fil d'acier, ton parchemin explosif.
- **Le combat coûte quelque chose.** Regarde la colonne de droite pendant un
  affrontement : les points de vie et le chakra descendent à chaque échange, et
  ils remontent lentement hors combat. Si rien ne bouge, c'est que le moteur de
  combat n'est pas branché — lance `python -m scripts.parcours`, la section
  « Affrontement » te le dira.
- **Les délais tombent.** Regarde le compte à rebours sur tes missions, dans la
  colonne de droite. Laisse volontairement passer l'échéance d'une mission : au
  tour suivant elle passe en **échouée**, le monde s'en souvient, et le MJ a le
  droit de te le reprocher.
- **Les blessures se referment.** Une entaille disparaît d'elle-même au bout de
  six tours, un état « blessé » au bout de quatorze. Au-delà, il faut le bouton
  **« Souffler et panser tes blessures »**.
- **Ton répertoire vaut plus que la somme de ses techniques.** Ouvre ta fiche
  complète : le panneau *« Ce que tu peux enchaîner »* te dit quels combos sont
  prêts, lesquels attendent encore un peu de maîtrise, et ce qu'il te manque
  pour les autres. Un combo prêt frappe plus fort en combat, et le détail des
  jets le nomme.
- **Le souffle change.** Compare un échange de combat et une scène de
  découverte : le premier doit être court, sec, coupé au milieu du mouvement ;
  la seconde plus longue, avec au moins un sens qui n'est pas la vue. Et si
  trois scènes d'affilée prennent le même ton, le MJ change de lui-même.
- **Le monde bouge sans toi.** Tous les quatre tours, une faction ou un PNJ
  avance sur un de ses objectifs. Tu l'apprends par une rumeur, jamais comme
  un fait établi — et le MJ peut y faire allusion aux tours suivants. Rien de
  tout cela ne touche ta fiche : le monde bouge dans ton dos, il ne te punit
  pas dans ton dos.
- **La carte grandit.** Au premier jour tu ne connais que ton village. La
  Forêt frontalière et le Poste du Nord n'apparaissent que le jour où le
  bureau des missions t'y envoie.
- **Tu peux relire ton histoire.** Le lien **chronique** dans la barre du haut
  ouvre la campagne comme un récit : l'ouverture, les chapitres résumés, les
  affrontements, ce que ta destinée a ouvert. C'est la page à montrer quand
  quelqu'un demande « raconte » — et celle qui passe le mieux en partage
  d'écran.
- **La distance coûte.** Dans la colonne de droite, le panneau *« Le pays »*
  place les lieux les uns par rapport aux autres et donne le prix du trajet en
  tours. Va au Poste frontière du Nord : quatre tours passent, tes échéances de
  mission courent pendant ce temps, et tu peux très bien arriver dans une
  embuscade.
- **La mémoire retrouve le sens.** Au tour 30, parle d'un engagement pris au
  tour 3 sans employer les mêmes mots. Si tu as installé `nomic-embed-text`, le
  MJ doit le reprendre correctement.
- **La voix.** Le bouton **voix** dans la barre du haut lit chaque nouvelle
  scène à voix haute, avec une hauteur par personnage — un enfant, un ancien et
  un Kage ne parlent pas pareil. Le petit haut-parleur sur un tour le relit
  seul. Tout se passe dans ton navigateur, avec les voix de Windows : rien
  n'est envoyé nulle part.

  **Le TON aussi se joue.** Une réplique murmurée est plus basse, plus lente
  et nettement moins forte qu'une réplique criée, et elle laisse un blanc
  derrière elle. C'est l'incise qui décide — « … », *murmure-t-il* — puis la
  ponctuation, puis le souffle de la scène pour la narration. Rien n'est
  deviné : sans signal, le ton reste neutre.
- **Le monde a des dents.** Traverse la Forêt frontalière ou le Poste du Nord
  plusieurs tours de suite : une embuscade finira par s'ouvrir. Elle ne peut
  pas se produire à l'Académie — c'est le `danger` du lieu qui décide, et il
  est affiché.
- **La destinée s'ouvre.** Au bout d'une dizaine de tours mouvementés, un
  indice doit tomber dans la colonne des conséquences — « Ta vue s'est
  brouillée, puis trop nette, l'espace d'un instant. » C'est le moteur de
  révélation qui travaille. Il est bridé : une avancée par tour au maximum, et
  quatre tours de carence ensuite.
- **Le monde se peuple.** Après vingt tours, la colonne des relations ne doit
  plus contenir seulement ton sensei, tes coéquipiers et ton rival : les gens
  croisés en jeu se cristallisent en fiches figées, et reviennent identiques.

---

## 5. Régler selon ta machine

**Commence par mesurer, pas par deviner :**

```bash
python -m scripts.calibrer
```

Le script joue un tour complet avec des prompts de taille réelle, chronomètre
chaque appel, et te dit quoi changer. Il mesure trois choses :

- **le chargement** — le premier appel après un démarrage d'Ollama ; c'est lui
  qui donne l'impression que le jeu rame alors qu'il attend la VRAM ;
- **le débit** en jetons par seconde — la seule mesure qui prédit la durée d'un
  tour. **En dessous de 15 j/s, ton modèle est trop gros pour ta carte** et
  déborde sur la mémoire système ;
- **le temps de lecture du contexte** — s'il dépasse le temps d'écriture, c'est
  `LLM_NUM_CTX` ou `BUDGET_LORE` qu'il faut baisser, pas le modèle.

Un tour, c'est **trois appels** : l'arbitre lit ton action, le narrateur écrit,
le simulateur extrait les conséquences et les pistes. C'est déjà le minimum —
les conséquences et les pistes ont été fusionnées en un seul appel justement
pour ça.

### Réglages à la main

Dans `.env` :

| Situation | Réglage |
|---|---|
| Trop lent | `LLM_MODEL=qwen3:4b` — et vérifie `LLM_MODELE_UNIQUE=true` |
| Le modèle recharge à chaque tour | `LLM_KEEP_ALIVE=30m` |
| Manque de mémoire vive vidéo | `LLM_NUM_CTX=4096` puis `BUDGET_LORE=800` |
| Narration qui part en roue libre | baisse `LLM_MAX_NARRATION` à 450 |
| Le MJ ignore le lore | augmente `BUDGET_LORE` (1400 par défaut) |
| Tu veux une machine plus costaude | `LLM_MODEL=qwen3:14b` si ta VRAM suit |
| Le texte n'arrive pas mot à mot | `NARRATION_EN_FLUX=true` (défaut) ; à `false` le jeu retombe sur l'affichage d'un bloc, qui reste parfaitement fonctionnel |
| Les combats sont trop meurtriers | monte `pv` dans `rulesets/naruto.yaml`, ou passe la campagne en difficulté `indulgent` |
| **Le MJ répète les mêmes tournures** | monte `LLM_REPETITION` à 1.14. C'est le réglage le plus utile du fichier |
| La prose part en vrille | baisse `LLM_TEMPERATURE` à 0.72 |
| Le JSON sort parfois faux | `LLM_TEMPERATURE_JSON=0.0` |
| Une action reste bloquée très longtemps | baisse `LLM_TIMEOUT` : tu auras un message au lieu d'une attente |

Surveille la VRAM pendant un tour : `nvidia-smi -l 2`. Si le modèle déborde sur
la mémoire système, les tours passent de 20 secondes à plusieurs minutes — c'est
le signe qu'il faut descendre d'un cran.

---

## 6. Jouer à deux ou trois

Même PC, chacun son tour : ajoute les joueurs à la campagne, des onglets
apparaissent au-dessus de la zone d'action pour choisir qui agit. Partage ton
écran sur Discord et passe le clavier. L'IA ne joue **jamais** un personnage
joueur — elle ne contrôle que le monde et les PNJ.

C'est le mode prévu, et il n'y a rien à installer de plus : **le jeu n'a pas de
multijoueur en réseau**, et n'en cherche pas. Un seul tour peut s'écrire à la
fois par campagne — si quelqu'un ouvre la table dans un second onglet et agit
pendant qu'une scène s'écrit, le jeu refuse poliment au lieu de décaler
l'horloge deux fois.

Pour un stream, trois choses valent le détour : la **chronique** (l'histoire
relue, lisible à l'écran), le bloc **« Précédemment »** en début de séance, et
le bouton **voix**, qui lit chaque scène à voix haute avec une hauteur et un
ton par personnage.

Et quand la campagne arrive à son terme, **clore la chronique** depuis cette
même page écrit l'épilogue : c'est la dernière chose à montrer à l'écran.

---

## 7. Hors ligne

Le jeu n'a besoin d'aucune connexion : HTMX est servi depuis `app/web/static/
vendor/`, le modèle tourne chez toi, la base est un fichier local. Seules les
polices viennent de Google Fonts, et si elles ne chargent pas, l'interface
retombe sur des polices système sans rien casser.

## 8. Sauvegardes

Pour **effacer une campagne**, survole sa carte sur l'accueil et clique la
croix : tout ce qui s'y rattache disparaît, après confirmation. Ça ne se
défait pas — copie `data/parties.db` avant si tu hésites.

Tout est dans `data/parties.db`, un seul fichier SQLite. Copie-le pour
sauvegarder, remplace-le pour restaurer. Pour repartir de zéro : ferme le
serveur, supprime le fichier, relance.

## 9. Mettre tes propres emblèmes

Dépose tes images dans `app/web/static/emblemes/` en les nommant d'après
l'identifiant (`konoha.svg`, `uchiha.svg`, `hyuga.png`…). Elles remplacent les
sceaux dessinés partout dans l'interface, sans toucher au code. Détails dans
`app/web/static/emblemes/LISEZ-MOI.txt`.
