"""Les petits mots qui font qu'une phrase est française.

LE DÉFAUT QU'ON VOYAIT TOUS LES TOURS. « Kaito s'est rendu à Tour du Kage. »
« Kaito est à Terrain d'entraînement 3. » On écrivait `f"à {lieu.nom}"` partout,
ce qui marche pour un nom propre — « à Konoha » — et ne marche pour rien
d'autre. Un lieu comme « la Tour du Kage » est un nom COMMUN déterminé : il
appelle son article, et l'article se contracte avec la préposition.

CE QUE FAIT CE MODULE. Il devine l'article à partir du nom, et il fabrique les
formes contractées :

    a("Tour du Kage")               → « à la Tour du Kage »
    a("Terrain d'entraînement 3")   → « au Terrain d'entraînement 3 »
    a("Académie")                   → « à l'Académie »
    a("Konoha")                     → « à Konoha »
    de("Forêt frontalière")         → « de la Forêt frontalière »
    de("Poste frontière du Nord")   → « du Poste frontière du Nord »

POURQUOI UNE DEVINETTE ET PAS UNE COLONNE EN BASE. Parce que c'est une
fonction pure du nom : rien à migrer, rien à garder cohérent, et un test qui
dit tout. Et parce que la règle de décision est la bonne par défaut : ce qu'on
ne reconnaît pas est traité comme un NOM PROPRE, donc sans article. « à
Ichiraku » est juste ; « à la Ichiraku » ne le serait pas.

CE MODULE NE TRADUIT RIEN. Il ne corrige pas le texte du modèle — on ne
réécrit jamais une narration. Il sert aux phrases que le MOTEUR fabrique :
effets, événements, mémoire, rappel de reprise, et le contexte qu'on donne au
narrateur pour qu'il écrive lui-même « la Tour du Kage ».
"""
from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Le lexique des têtes de nom. C'est la seule donnée du module.
# --------------------------------------------------------------------------
# On ne reconnaît que le PREMIER mot : en français, la tête d'un groupe
# nominal est devant. « Tour du Kage », « Poste frontière du Nord »,
# « Quartier marchand » — c'est toujours le premier mot qui porte le genre.
_MASCULIN = {
    "quartier", "terrain", "poste", "pont", "marché", "temple", "bureau",
    "camp", "port", "sanctuaire", "cimetière", "jardin", "chemin", "col",
    "lac", "mont", "ravin", "village", "hameau", "domaine", "complexe",
    "bâtiment", "entrepôt", "sentier", "carrefour", "pavillon", "dojo",
    "souterrain", "tunnel", "canal", "désert", "bois", "champ", "moulin",
    "phare", "quai", "rempart", "torii", "bassin", "cratère", "labyrinthe",
    "marais", "plateau", "belvédère", "donjon", "manoir", "palais", "puits",
    "refuge", "relais", "verger", "vallon", "gouffre", "étang",
    # les lieux des autres villages (lieux.yaml) : Suna, Kiri, Kumo, Iwa, Oto
    "dôme", "récif", "réseau", "massif", "repaire", "chenal", "ponton",
    "atelier", "laboratoire", "grenier", "sanctuaire",
}
_FEMININ = {
    "tour", "académie", "forêt", "montagne", "vallée", "rivière", "plaine",
    "grotte", "caverne", "auberge", "place", "rue", "ruelle", "clairière",
    "cascade", "falaise", "frontière", "prison", "taverne", "boutique",
    "école", "bibliothèque", "salle", "porte", "colline", "île", "source",
    "mine", "ferme", "arène", "chapelle", "citadelle", "forge", "crypte",
    "carrière", "digue", "lande", "muraille", "passe", "pagode", "cour",
    "demeure", "maison", "tombe", "cabane", "gorge", "baie", "crête",
    "mer", "résidence", "anse", "digue", "rizière", "ruche", "oasis", "dune",
    "faille", "tanière",
}
# Les pluriels doivent être écrits : « marais » est masculin singulier, et une
# règle sur le « s » final y verrait un pluriel.
_PLURIEL = {
    "portes", "ruines", "grottes", "falaises", "monts", "collines", "sources",
    "terres", "jardins", "catacombes", "chutes", "thermes", "docks",
    "faubourgs", "remparts", "sables", "cavernes", "tombes", "îles",
    "ponts", "carrières", "blocs", "rizières", "dunes", "passerelles",
    "marécages", "laboratoires",
}

# Devant ces sons, l'article s'élide : « l'Académie », « l'Hôtel ». Le h
# aspiré ne s'élide pas, mais aucun nom de lieu du pack n'en commence, et se
# tromper dans ce sens ne produit jamais de faute visible.
_VOYELLES = "aeiouyàâäéèêëîïôöûüÿ"

MASCULIN, FEMININ, PLURIEL, AUCUN = "le", "la", "les", ""


def _sans_accent(mot: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", mot)
                   if unicodedata.category(c) != "Mn")


def _tete(nom: str) -> str:
    """Le premier mot, nettoyé — c'est lui qui porte le genre."""
    m = re.match(r"[^\W\d_]+", (nom or "").strip(), re.UNICODE)
    return m.group(0).lower() if m else ""


def genre(nom: str) -> str:
    """« le », « la », « les », ou « » pour un nom propre.

    Ce qu'on ne reconnaît pas n'a PAS d'article : c'est le défaut sûr. Un nom
    propre traité comme un nom commun produit « à la Konoha » ; un nom commun
    traité comme un nom propre produit « à Konoha » là où on aurait voulu « à
    la Tour », ce qui est une maladresse et non une faute grossière.
    """
    tete = _tete(nom)
    if not tete:
        return AUCUN
    nu = _sans_accent(tete)
    if tete in _PLURIEL or nu in {_sans_accent(m) for m in _PLURIEL}:
        return PLURIEL
    if tete in _FEMININ or nu in {_sans_accent(m) for m in _FEMININ}:
        return FEMININ
    if tete in _MASCULIN or nu in {_sans_accent(m) for m in _MASCULIN}:
        return MASCULIN
    return AUCUN


def _elide(nom: str) -> bool:
    tete = _tete(nom)
    return bool(tete) and tete[0] in _VOYELLES


def determine(nom: str, art: str | None = None) -> str:
    """Le nom précédé de son article : « la Tour du Kage », « Konoha »."""
    nom = (nom or "").strip()
    if not nom:
        return ""
    art = genre(nom) if art is None else art
    if art == AUCUN:
        return nom
    if art in (MASCULIN, FEMININ) and _elide(nom):
        return f"l'{nom}"
    return f"{art} {nom}"


def a(nom: str, art: str | None = None) -> str:
    """« à » + le lieu, contracté : à la / au / à l' / aux / à."""
    nom = (nom or "").strip()
    if not nom:
        return ""
    art = genre(nom) if art is None else art
    if art == AUCUN:
        return f"à {nom}"
    if _elide(nom) and art in (MASCULIN, FEMININ):
        return f"à l'{nom}"
    if art == MASCULIN:
        return f"au {nom}"
    if art == PLURIEL:
        return f"aux {nom}"
    return f"à la {nom}"


def de(nom: str, art: str | None = None) -> str:
    """« de » + le lieu, contracté : de la / du / de l' / des / de."""
    nom = (nom or "").strip()
    if not nom:
        return ""
    art = genre(nom) if art is None else art
    if art == AUCUN:
        return f"de {nom}"
    if _elide(nom) and art in (MASCULIN, FEMININ):
        return f"de l'{nom}"
    if art == MASCULIN:
        return f"du {nom}"
    if art == PLURIEL:
        return f"des {nom}"
    return f"de la {nom}"


def dans(nom: str, art: str | None = None) -> str:
    """« dans » ne se contracte pas, mais il veut l'article."""
    nom = (nom or "").strip()
    if not nom:
        return ""
    art = genre(nom) if art is None else art
    return f"à {nom}" if art == AUCUN else f"dans {determine(nom, art)}"


def vers(nom: str, art: str | None = None) -> str:
    nom = (nom or "").strip()
    return f"vers {determine(nom)}" if nom else ""


def depuis(nom: str, art: str | None = None) -> str:
    nom = (nom or "").strip()
    return f"depuis {determine(nom)}" if nom else ""


# --------------------------------------------------------------------------
# Les accords qu'on écrivait à la main
# --------------------------------------------------------------------------
def pluriel(n: int, singulier: str, plur: str | None = None) -> str:
    """« 1 tour », « 3 tours ». Zéro prend le pluriel en français moderne
    quand le nom est comptable : « 0 tour » reste toutefois l'usage courant,
    et c'est celui qu'on garde."""
    return singulier if abs(n) < 2 else (plur or singulier + "s")


def accorde(n: int, singulier: str, plur: str | None = None) -> str:
    """Le nombre ET le mot accordé : « 3 tours »."""
    return f"{n} {pluriel(n, singulier, plur)}"


def majuscule(texte: str) -> str:
    """La première lettre en capitale, et RIEN D'AUTRE.

    `str.capitalize()` met le reste en minuscules : « la Tour du Kage »
    devenait « La tour du kage ». C'est exactement le genre de correction
    silencieuse qui abîme un nom propre à l'intérieur d'un groupe nominal.
    """
    texte = texte or ""
    return texte[:1].upper() + texte[1:]


# --------------------------------------------------------------------------
# Les codes du moteur, dits en français
# --------------------------------------------------------------------------
# Le moteur parle en codes (`reussite_critique`, `gagnee`, `touche_net`) ; le
# joueur lisait ces codes à peine maquillés — « reussite critique », sans
# accent, « gagnee ». Chaque code affiché passe désormais par ici. Une entrée
# manquante rend le code lisible (souligné → espace), jamais une erreur.
LIBELLES = {
    # issues d'un jet
    "reussite_critique": "réussite critique", "reussite": "réussite",
    "reussite_partielle": "oui, mais…", "echec": "échec",
    "echec_critique": "échec critique",
    # issues d'un jet opposé
    "touche_net": "touché net", "touche": "touché", "effleure": "effleuré",
    "pare": "paré", "contre": "contré",
    # issues d'une rencontre
    "en_cours": "en cours", "gagnee": "gagnée", "perdue": "perdue",
    "rompue": "rompue", "objectif_atteint": "objectif atteint",
    "dispersee": "dispersée",
    # risque d'une piste
    "faible": "faible", "moyen": "moyen", "eleve": "élevé",
    # états de destinée
    "latent": "latent", "pressenti": "pressenti", "en_eveil": "en éveil",
    "eveille": "éveillé", "refuse": "refusé",
    # postures
    "offensive": "offensive", "mesuree": "mesurée", "defensive": "défensive",
    "technique": "technique", "manoeuvre": "manœuvre",
    "desengagement": "désengagement",
    # difficultés
    "triviale": "triviale", "facile": "facile", "normal": "normale",
    "difficile": "difficile", "ardue": "ardue", "legendaire": "légendaire",
    # catégories de techniques
    "ninjutsu": "ninjutsu", "taijutsu": "taijutsu", "genjutsu": "genjutsu",
    "kenjutsu": "kenjutsu", "kugutsu": "kugutsu", "fuinjutsu": "fûinjutsu",
    "iryo": "ninjutsu médical", "espionnage": "espionnage", "arme": "arme",
    # rôles dans l'entourage
    "sensei": "instructeur", "coequipier": "coéquipier", "rival": "rival",
    "antagoniste": "adversaire", "joueur": "joueur", "figurant": "figurant",
    "connaissance": "connaissance",
}


def libelle(code: str) -> str:
    """« reussite_critique » → « réussite critique »."""
    code = str(code or "")
    return LIBELLES.get(code, code.replace("_", " "))


# Les codes qui ont pu se glisser dans un TEXTE conservé (le journal d'une
# rencontre, un événement d'avant la 2.0.1). On les traduit à l'affichage :
# une sauvegarde d'hier ne doit pas parler moins bien qu'une partie neuve.
_CODES_DANS_TEXTE = re.compile(
    r"\b(reussite_critique|reussite_partielle|reussite critique|reussite partielle|reussite|"
    r"echec_critique|echec critique|echec|touche_net|touche net|effleure|"
    r"gagnee|dispersee|objectif_atteint|objectif atteint|en_cours)\b")
# « pare » et « contre » sont aussi des mots ordinaires (« 14 contre 9 ») :
# on ne les traduit qu'en position d'issue, après un deux-points, en fin.
_ISSUES_AMBIGUES = re.compile(r"(?<=: )(pare|contre)(?=\.?$)", re.MULTILINE)


def texte_fr(texte: str) -> str:
    """Traduit les codes restés dans une phrase du moteur."""
    t = _CODES_DANS_TEXTE.sub(
        lambda m: libelle(m.group(0).replace(" ", "_")), str(texte or ""))
    return _ISSUES_AMBIGUES.sub(lambda m: libelle(m.group(0)), t)


# --------------------------------------------------------------------------
# La typographie française du récit
# --------------------------------------------------------------------------
# Un modèle écrit « "Tu viens?" » ou « Tu viens ? » selon l'humeur. Le récit
# affiché suit la règle française : guillemets « », espace insécable avant
# ; : ! ? et à l'intérieur des guillemets, points de suspension en un seul
# caractère. On ne réécrit pas les mots — seulement les signes.
_INSECABLE = " "          # avant « : » et dans les guillemets
_FINE = " "               # avant « ; ! ? »


def typographie(texte: str) -> str:
    t = texte or ""
    if not t:
        return t
    t = t.replace("...", "…")
    # Les guillemets droits, par paires, deviennent des guillemets français.
    if t.count('"') >= 2:
        t = re.sub(r'"([^"\n]{1,400}?)"', "« \\1 »", t)
    # L'apostrophe dactylographique devient typographique dans les mots.
    t = re.sub(r"(?<=\w)'(?=\w)", "’", t)
    # Espaces insécables : on retire l'espace ordinaire ou absent devant le
    # signe et on met la bonne. Jamais devant un « ? » ou « ! » qui ouvre une
    # ligne, ni dans une URL (aucune dans un récit).
    t = re.sub(r"[   ]*([;!?])(?!\w)", _FINE + r"\1", t)
    t = re.sub(r"(?<=\S)[   ]*:(?!\d)", _INSECABLE + ":", t)
    t = re.sub(r"«[   ]*", "«" + _INSECABLE, t)
    t = re.sub(r"[   ]*»", _INSECABLE + "»", t)
    # « ?! » et « !? » ne prennent qu'une espace, au début.
    t = re.sub(_FINE + r"([!?])" + _FINE + r"([!?])", _FINE + r"\1\2", t)
    return re.sub(r"[ ]{2,}", " ", t)


def enumerer(mots: list[str], liaison: str = "et") -> str:
    """« a, b et c ». Une liste jointe par des virgules jusqu'au bout se lit
    comme une énumération technique, pas comme une phrase."""
    propres = [m.strip() for m in mots if (m or "").strip()]
    if not propres:
        return ""
    if len(propres) == 1:
        return propres[0]
    return f"{', '.join(propres[:-1])} {liaison} {propres[-1]}"
