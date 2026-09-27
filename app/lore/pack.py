"""Chargement et interrogation du pack lore.

Le YAML est la source de vérité ; ce module en fait un index en mémoire,
chargé une fois au démarrage. Ajouter un clan, une technique ou une époque
ne demande aucune modification de code — seulement d'éditer un fichier.
"""
from __future__ import annotations

import functools
import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from app.config import ROOT

LORE_DIR = ROOT / "lore"


def _slug(texte: str) -> str:
    """Normalise pour la comparaison de noms : sans accent, minuscules."""
    t = unicodedata.normalize("NFD", texte or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


class LorePack:
    """Un univers chargé en mémoire, interrogeable par identifiant ou par nom."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        base = LORE_DIR / slug
        if not base.exists():
            raise FileNotFoundError(f"Pack lore introuvable : {slug}")

        self.data: dict[str, Any] = {}
        for fichier in sorted(base.glob("*.yaml")):
            contenu = yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}
            for cle, valeur in contenu.items():
                if isinstance(valeur, list) and cle in self.data:
                    self.data[cle].extend(valeur)
                else:
                    self.data[cle] = valeur

        # Index par identifiant, toutes collections confondues
        self._index: dict[str, dict] = {}
        self._par_type: dict[str, list[dict]] = {}
        for cle, valeur in self.data.items():
            if not isinstance(valeur, list):
                continue
            for item in valeur:
                if isinstance(item, dict) and "id" in item:
                    item.setdefault("_type", cle)
                    self._index[item["id"]] = item
                    self._par_type.setdefault(cle, []).append(item)

        # Index de noms et d'alias, pour résoudre ce que le joueur écrit
        self._noms: dict[str, str] = {}
        for item in self._index.values():
            for champ in ("nom", "nom_fr", "fr"):
                if item.get(champ):
                    self._noms[_slug(item[champ])] = item["id"]
            for alias in item.get("alias", []) or []:
                self._noms[_slug(alias)] = item["id"]

    # -- accès -------------------------------------------------------------
    def get(self, ref: str) -> dict | None:
        return self._index.get(ref)

    def liste(self, type_: str) -> list[dict]:
        return list(self._par_type.get(type_, []))

    def resoudre_nom(self, nom: str) -> dict | None:
        """« Kakashi du Sharingan » -> la fiche. Tolérant aux accents."""
        ref = self._noms.get(_slug(nom))
        return self._index.get(ref) if ref else None

    # -- époques -----------------------------------------------------------
    @property
    def epoques(self) -> list[dict]:
        return self.data.get("epoques", [])

    def epoque(self, eid: str) -> dict | None:
        return next((e for e in self.epoques if e["id"] == eid), None)

    def annee_de(self, eid: str) -> int:
        e = self.epoque(eid)
        return int(e["annee"]) if e else 0

    def actif_a(self, entite: dict, annee: int) -> bool:
        """Une entité existe-t-elle à cette date ? Un clan fondé après, un
        village créé plus tard, un personnage mort : tous exclus."""
        if entite.get("fonde") is not None and annee < int(entite["fonde"]):
            return False
        if entite.get("eteint") is not None and annee >= int(entite["eteint"]):
            return False
        if entite.get("naissance") is not None and annee < int(entite["naissance"]):
            return False
        if entite.get("mort") is not None and annee >= int(entite["mort"]):
            return False
        return True

    # -- raccourcis métier -------------------------------------------------
    def villages(self, annee: int | None = None) -> list[dict]:
        v = self.liste("villages")
        return [x for x in v if annee is None or self.actif_a(x, annee)]

    def village(self, vid: str) -> dict | None:
        return next((v for v in self.liste("villages") if v["id"] == vid), None)

    def clans(self, village: str | None = None, rang: str | None = None,
              annee: int | None = None) -> list[dict]:
        out = self.liste("clans")
        if village:
            out = [c for c in out if c.get("village") == village]
        if rang:
            out = [c for c in out if c.get("rang") == rang]
        if annee is not None:
            out = [c for c in out if self.actif_a(c, annee)]
        return out

    def clan(self, cid: str) -> dict | None:
        return next((c for c in self.liste("clans") if c["id"] == cid), None)

    def techniques(self, rangs: list[str] | None = None, categorie: str | None = None,
                   clan: str | None = None) -> list[dict]:
        out = self.liste("techniques")
        if rangs:
            out = [t for t in out if t.get("rang") in rangs]
        if categorie:
            out = [t for t in out if t.get("categorie") == categorie]
        # Une technique de clan n'est accessible qu'aux membres de ce clan
        out = [t for t in out if not t.get("clan") or t.get("clan") == clan]
        return out

    def technique(self, tid: str) -> dict | None:
        return next((t for t in self.liste("techniques") if t["id"] == tid), None)

    # -- synergies entre techniques ----------------------------------------
    def synergies(self, type_: str | None = None) -> list[dict]:
        """Combos, contres et prérequis.

        Écrits dans `techniques.yaml` depuis le premier jour et lus par
        personne : deux techniques maîtrisées ensemble valaient exactement
        autant que prises séparément, et un prérequis n'empêchait rien.
        """
        out = self.data.get("synergies", []) or []
        return [s for s in out if not type_ or s.get("type") == type_]

    def combos_de(self, refs: list[str]) -> list[dict]:
        """Les combos entièrement couverts par ce répertoire de techniques."""
        connues = set(refs or [])
        return [s for s in self.synergies("combo")
                if connues.issuperset(s.get("requiert") or [])]

    def contres_de(self, refs: list[str]) -> list[dict]:
        connues = set(refs or [])
        return [s for s in self.synergies("contre")
                if connues.issuperset(s.get("requiert") or [])]

    def prerequis_de(self, ref: str) -> list[dict]:
        """Ce qu'il faut posséder pour débloquer cette technique."""
        return [s for s in self.synergies("prerequis") if s.get("debloque") == ref]

    def lignee(self, lid: str) -> dict | None:
        return next((l for l in self.liste("lignees") if l["id"] == lid), None)

    def traits(self) -> list[dict]:
        return self.liste("traits")

    def trait(self, tid: str) -> dict | None:
        return next((t for t in self.liste("traits") if t["id"] == tid), None)

    def profils(self) -> list[dict]:
        return self.liste("profils")

    def lieux_de_depart(self, village_id: str) -> list[dict]:
        """Les lieux par lesquels une campagne commence DANS CE VILLAGE.

        Chaque village a les siens (voir lieux.yaml). Un village sans jeu de
        lieux retombe sur celui de Konoha plutôt que sur rien — mais c'est un
        trou dans le lore, pas un comportement voulu.
        """
        table = self.data.get("lieux_par_village") or {}
        return list(table.get(village_id) or table.get("konoha") or [])

    def archetypes(self) -> list[dict]:
        """Ce que tire « je m'en remets au destin ». Voir destinee.yaml."""
        return self.data.get("archetypes", []) or []

    def archetype(self, aid: str) -> dict | None:
        return next((a for a in self.archetypes() if a["id"] == aid), None)

    @property
    def sel(self) -> list[str]:
        return self.data.get("sel", [])

    def tensions(self) -> list[dict]:
        return self.data.get("tensions", [])

    def histoire(self, avant: int | None = None) -> list[dict]:
        h = self.liste("histoire")
        if avant is not None:
            h = [e for e in h if int(e.get("annee", -999)) <= avant]
        return sorted(h, key=lambda e: e.get("annee", 0))

    # -- armement et matériel ----------------------------------------------
    def armes(self, usage: str | None = None,
              disponibilite: str | None = None) -> list[dict]:
        out = self.liste("armes")
        if usage:
            out = [a for a in out if a.get("usage") == usage]
        if disponibilite:
            out = [a for a in out if a.get("disponibilite") == disponibilite]
        return out

    def consommables(self) -> list[dict]:
        return self.liste("consommables")

    def materiel(self) -> list[dict]:
        """Armes, consommables et objets légendaires dans une seule liste."""
        return self.liste("armes") + self.liste("consommables") + self.liste("objets")

    def styles_armes(self, village: str | None = None) -> list[dict]:
        out = self.liste("styles_armes")
        if village:
            out = [s for s in out if s.get("village") in (village, None)]
        return out

    # -- environnement -----------------------------------------------------
    def _par_pays(self, cle: str, pays: str | None,
                  danger_max: int | None = None) -> list[dict]:
        out = self.liste(cle)
        if pays:
            out = [x for x in out
                   if "toutes" in (x.get("pays") or []) or pays in (x.get("pays") or [])]
        if danger_max is not None:
            out = [x for x in out if int(x.get("danger", 0)) <= danger_max]
        return out

    def faune(self, pays: str | None = None,
              danger_max: int | None = None) -> list[dict]:
        return self._par_pays("faune", pays, danger_max)

    def flore(self, pays: str | None = None) -> list[dict]:
        return self._par_pays("flore", pays)

    def mineraux(self, pays: str | None = None) -> list[dict]:
        return self._par_pays("mineraux", pays)

    # -- géographie --------------------------------------------------------
    def pays(self, pid: str) -> dict | None:
        return next((p for p in self.liste("pays") if p["id"] == pid), None)

    def pays_du_village(self, vid: str) -> dict | None:
        v = self.village(vid) or {}
        return self.pays(v.get("pays", "")) if v.get("pays") else None

    def tensions_de(self, ref: str) -> list[dict]:
        """Tensions où `ref` (village ou organisation) est partie prenante.

        Retourne des copies enrichies d'un champ `avec` : l'autre partie.
        """
        out = []
        for t in self.tensions():
            entre = t.get("entre") or []
            if ref in entre:
                autre = next((e for e in entre if e != ref), "")
                out.append({**t, "avec": autre})
        return sorted(out, key=lambda t: t.get("valeur", 0))

    # -- antagonistes et amorces -------------------------------------------
    def organisations(self, village: str | None = None, *,
                      inclure_secretes: bool = False) -> list[dict]:
        out = self.liste("organisations")
        if not inclure_secretes:
            out = [o for o in out if not o.get("secrete")]
        if village:
            out = [o for o in out if o.get("village") in (village, None)]
        return out

    def est_secrete(self, ref: str) -> bool:
        """Une organisation marquée secrète ne doit jamais entrer dans le
        contexte du narrateur tant que le groupe ne l'a pas découverte."""
        return bool((self.get(ref) or {}).get("secrete"))

    def sites_invocation(self, *, inclure_secrets: bool = False) -> list[dict]:
        out = self.liste("sites_invocation")
        if not inclure_secrets:
            out = [s for s in out if not s.get("secrete")]
        return out

    def bingo_book(self, tier_max: int | None = None) -> list[dict]:
        out = self.liste("bingo_book")
        if tier_max is not None:
            out = [n for n in out if int(n.get("tier", 9)) <= tier_max]
        return sorted(out, key=lambda n: int(n.get("tier", 0)))

    # -- adversaires -------------------------------------------------------
    def adversaires(self, *, tier_max: int | None = None,
                    tier_min: int | None = None, milieu: str | None = None,
                    pays: str | None = None) -> list[dict]:
        """Les adversaires plausibles ici et maintenant.

        `milieu` se compare aux tags de terrain (route, forêt, frontière…) ;
        un adversaire sans `milieu` est possible partout. Le filtre par tier
        est ce qui empêche une embuscade de tier 5 sur une équipe de genin —
        et ce qui permet d'en vouloir une, le jour où la campagne y arrive.
        """
        out = self.liste("adversaires")
        if tier_max is not None:
            out = [a for a in out if int(a.get("tier", 1)) <= tier_max]
        if tier_min is not None:
            out = [a for a in out if int(a.get("tier", 1)) >= tier_min]
        if pays:
            out = [a for a in out
                   if "toutes" in (a.get("pays") or ["toutes"])
                   or pays in (a.get("pays") or [])]
        if milieu:
            out = [a for a in out
                   if not a.get("milieu") or milieu in a["milieu"]]
        return out

    def adversaire(self, aid: str) -> dict | None:
        return next((a for a in self.liste("adversaires") if a["id"] == aid), None)

    @property
    def epithetes_adversaire(self) -> list[str]:
        return list(self.data.get("epithetes_adversaire", []) or [])

    @property
    def prenoms_adversaire(self) -> list[str]:
        return list(self.data.get("prenoms_adversaire", []) or [])

    def rumeurs(self, portee: str | None = None) -> list[dict]:
        out = self.data.get("rumeurs", []) or []
        if portee:
            out = [r for r in out if r.get("portee") == portee]
        return list(out)

    # -- missions ----------------------------------------------------------
    def archetypes_mission(self, rang: str | None = None,
                           categorie: str | None = None) -> list[dict]:
        out = self.liste("archetypes_mission")
        if rang:
            out = [a for a in out if rang in (a.get("rangs") or [])]
        if categorie:
            out = [a for a in out if a.get("categorie") == categorie]
        return out

    def commanditaires(self) -> dict:
        return self.data.get("commanditaires", {}) or {}

    def revers(self) -> list[str]:
        return list(self.data.get("revers", []) or [])

    def accroches(self, *collections: str, pays: str | None = None) -> list[dict]:
        """Toutes les amorces de mission disponibles, avec leur origine.

        Une accroche est une phrase qui justifie une mission à elle seule. Les
        rassembler ici évite au générateur d\'aller les chercher collection
        par collection.
        """
        out = []
        for cle in (collections or ("faune", "flore", "mineraux",
                                    "organisations", "bingo_book",
                                    "sites_invocation")):
            for item in self.liste(cle):
                if not item.get("accroche"):
                    continue
                zones = item.get("pays")
                if pays and isinstance(zones, list) and zones \
                        and "toutes" not in zones and pays not in zones:
                    continue
                out.append({"source": cle, "ref": item["id"],
                            "nom": item.get("nom", item["id"]),
                            "accroche": item["accroche"]})
        return out

    # -- prononciation -----------------------------------------------------
    @functools.cached_property
    def lexique(self) -> list[tuple[str, str]]:
        """Couples (terme, réécriture française), les plus longs d'abord.

        La prononciation se règle AVANT le moteur vocal : aucun TTS
        francophone ne sait lire « Mangekyô Sharingan » tout seul.
        """
        paires: list[tuple[str, str]] = []
        for item in self._index.values():
            p = item.get("prononciation")
            if p:
                for champ in ("nom", "nom_fr"):
                    if item.get(champ):
                        paires.append((item[champ], p))
        for t in self.data.get("termes", []) or []:
            if t.get("terme") and t.get("fr"):
                paires.append((t["terme"], t["fr"]))
        return sorted(set(paires), key=lambda x: -len(x[0]))


_PACKS: dict[str, LorePack] = {}


def charger(slug: str = "naruto") -> LorePack:
    if slug not in _PACKS:
        _PACKS[slug] = LorePack(slug)
    return _PACKS[slug]
