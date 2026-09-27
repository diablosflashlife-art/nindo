"""Ce que le modèle ne doit jamais écrire sur un personnage, vérifié par le code.

Les consignes disent déjà « aucun nom de la série » et « les genin sont des
enfants ». Un modèle les suit presque toujours ; la partie test a montré
l'exception : un instructeur nommé Kakuzu, une coéquipière de treize ans
décrite « sensuelle » et « masochiste ». Une consigne ne suffit pas pour ce qui
ne doit jamais arriver : on vérifie à l'arrivée.
"""
from __future__ import annotations

import random
import re
import unicodedata

# Prénoms de la série assez connus pour qu'un joueur les reconnaisse. Les noms
# de clan n'y sont pas : un PNJ Uchiha ou Hozuki est légitime.
PRENOMS_CANON = {
    "naruto", "sasuke", "sakura", "kakashi", "itachi", "madara", "obito", "shisui",
    "hashirama", "tobirama", "hiruzen", "minato", "kushina", "jiraiya", "tsunade",
    "orochimaru", "kabuto", "gaara", "kankuro", "temari", "rasa", "chiyo", "sasori",
    "deidara", "kisame", "zabuza", "haku", "mei", "yagura", "chojuro", "suigetsu",
    "jugo", "karin", "kimimaro", "tayuya", "sakon", "ukon", "jirobo", "kidomaru",
    "darui", "samui", "omoi", "karui", "onoki", "kurotsuchi", "akatsuchi",
    "kakuzu", "hidan", "konan", "nagato", "yahiko", "zetsu", "tobi", "neji",
    "hinata", "hiashi", "hanabi", "gai", "tenten", "shikamaru", "ino", "choji",
    "shino", "kiba", "asuma", "kurenai", "yamato", "danzo", "iruka", "anko",
    "genma", "ibiki", "mangetsu", "fuguki", "jinin", "kushimaru", "jinpachi",
    "ameyuri", "raiga", "shikaku", "inoichi", "choza", "tsume", "yugao", "hayate",
    "mizuki", "konohamaru", "ebisu", "guren", "utakata", "roshi", "yugito",
    "kinkaku", "ginkaku", "gengetsu", "hanzo", "boruto", "sarada", "mitsuki",
    "kaguya", "hagoromo", "hamura", "indra", "ashura", "fugaku", "mikoto",
}

# Ce qui sexualise. Sur un personnage de moins de dix-huit ans, la phrase qui
# le contient est retirée.
_SEXUALISANT = re.compile(
    r"sensuel|s[ée]duct|s[ée]duisant|sexy|sexuel|[ée]rotique|aguich|lascif|"
    r"lascive|masochis|sadomaso|charnel|provocant|voluptu",
    re.IGNORECASE)


def _cle(mot: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", mot.lower())
                   if unicodedata.category(c) != "Mn")


def nettoyer_nom(nom: str) -> str:
    """« Famille : Shiraishi », « Nom : Ren » : le modèle recopie parfois
    l'étiquette avec la valeur. On ne garde que le nom."""
    nom = re.sub(r"^\s*(nom|pr[ée]nom|famille|name)\s*:\s*", "", nom or "",
                 flags=re.IGNORECASE)
    return nom.strip(" \"'«»“”*").strip()


_ETIQUETTES = {"inconnu", "inconnue", "l'inconnu", "l'inconnue", "anonyme",
               "personne", "silhouette", "ombre", "étranger", "étrangère"}


def nom_propre(nom: str) -> bool:
    """Un nom, et pas une étiquette ni une description : une majuscule, pas
    plus de six mots, et rien qui ne soit un simple « Inconnu »."""
    nom = (nom or "").strip()
    if not nom or not nom[0].isupper() or len(nom.split()) > 6:
        return False
    return nom.lower() not in _ETIQUETTES and nom.split()[0].lower() not in _ETIQUETTES


def nom_canon(nom: str) -> bool:
    return any(_cle(m) in PRENOMS_CANON for m in re.findall(r"[\wÀ-ÿ'-]+", nom or ""))


def decanoniser(nom: str, rng: random.Random, prenoms: list[str]) -> str:
    """Remplace chaque prénom de la série par un prénom ordinaire."""
    choix = [p for p in prenoms if _cle(p) not in PRENOMS_CANON] or ["Goro"]
    return re.sub(r"[\wÀ-ÿ'-]+",
                  lambda m: rng.choice(choix) if _cle(m.group(0)) in PRENOMS_CANON
                  else m.group(0), nom or "")


def pudeur(texte: str, age: int | None) -> str:
    """Retire d'une description de mineur les segments qui le sexualisent."""
    if not texte or (age is not None and age >= 18):
        return texte
    morceaux = re.split(r"(?<=[.;,!?])\s+", texte)
    gardes = [m for m in morceaux if not _SEXUALISANT.search(m)]
    propre = " ".join(gardes).strip()
    return re.sub(r"[,;]\s*$", ".", propre) if propre else ""
