"""Abstraction du modèle de langage.

Tout le moteur appelle `get_llm()` et ignore ce qu'il y a derrière. C'est ce
qui permettra de router la narration vers un modèle distant sans toucher au
jeu — ou de développer entièrement sans GPU, en mode `mock`.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Any, Iterator, Protocol

import httpx

from app.config import settings

# Visible dans la console du serveur : chaque passage de relais y est écrit.
journal = logging.getLogger("uvicorn.error")


class LLMProvider(Protocol):
    def text(self, system: str, user: str, *, rapide: bool = False,
             temperature: float = 0.85) -> str: ...
    def json(self, system: str, user: str, schema: dict, *,
             rapide: bool = True) -> dict: ...
    def flux(self, system: str, user: str, *, rapide: bool = False,
             temperature: float = 0.85) -> Iterator[str]: ...


# Les modèles à réflexion encadrent leur raisonnement. Sans ce nettoyage, le
# joueur lit le brouillon du modèle au lieu de la scène.
BALISES_REFLEXION = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
BALISE_OUVERTE = re.compile(
    r"<(think|thinking|reasoning)>.*\Z", re.DOTALL | re.IGNORECASE)


def nettoyer(brut: str) -> str:
    """Retire les blocs de réflexion et les restes de balises.

    Défense en profondeur : `think: false` suffit sur Ollama récent, mais un
    modèle mal converti, une version plus ancienne ou un autre moteur peuvent
    les produire quand même. Le joueur ne doit jamais les voir.
    """
    texte = BALISES_REFLEXION.sub("", brut or "")
    texte = BALISE_OUVERTE.sub("", texte)          # bloc tronqué par num_predict
    texte = re.sub(r"</?(think|thinking|reasoning)>", "", texte,
                   flags=re.IGNORECASE)
    return texte.strip()


def epurer(texte: str) -> str:
    """Retire la mise en forme markdown : l'interface affiche du texte brut,
    et `*« Tu sens ça ? »*` s'y lisait avec ses astérisques."""
    texte = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", texte or "")
    texte = texte.replace("*", "")
    return re.sub(r"(?m)^#{1,6}\s*", "", texte)


_FIN_DE_PHRASE = re.compile(r"[.!?…][»”\"')\]\s]*")


def achever(texte: str) -> str:
    """Un récit coupé par le plafond de génération finit sur « près d'un ».
    On le ramène à sa dernière phrase complète — tant qu'on ne perd pas plus
    du tiers du texte ; sinon on le laisse en suspens, avec des points."""
    texte = (texte or "").rstrip()
    if not texte or re.search(r"[.!?…»”\"')\]]$", texte):
        return texte
    fins = list(_FIN_DE_PHRASE.finditer(texte))
    if fins and fins[-1].end() >= len(texte) * 2 / 3:
        return texte[:fins[-1].end()].rstrip()
    return texte + "…"


_OUVERTURES = ("<think>", "<thinking>", "<reasoning>")
_FERMETURES = ("</think>", "</thinking>", "</reasoning>")


class FiltreReflexion:
    """Le même nettoyage, mais morceau par morceau, pour un flux.

    En flux, on ne peut pas attendre la fin du texte pour retirer un bloc de
    réflexion : il serait déjà affiché. On retient donc tout ce qui POURRAIT
    être le début d'une balise, et on ne relâche que ce qui est sûrement du
    récit. Le coût est un retard de quelques caractères, invisible à la
    lecture ; le bénéfice est que le joueur ne lit jamais le brouillon du
    modèle.
    """

    def __init__(self) -> None:
        self.tampon = ""
        self.dedans = False

    @staticmethod
    def _prefixe_possible(tampon: str, mots: tuple[str, ...]) -> bool:
        """Le tampon se termine-t-il sur le DÉBUT d'une de ces balises ?"""
        bas = tampon.lower()
        depart = bas.rfind("<")
        if depart < 0:
            return False
        reste = bas[depart:]
        return any(m.startswith(reste) for m in mots)

    def __call__(self, morceau: str) -> str:
        self.tampon += morceau or ""
        sortie = ""
        while True:
            bas = self.tampon.lower()
            if self.dedans:
                fins = [(bas.find(f), f) for f in _FERMETURES if f in bas]
                if not fins:
                    self.tampon = self.tampon[-32:]   # on jette le raisonnement
                    break
                i, f = min(fins)
                self.tampon = self.tampon[i + len(f):]
                self.dedans = False
                continue
            debuts = [(bas.find(o), o) for o in _OUVERTURES if o in bas]
            if debuts:
                i, o = min(debuts)
                sortie += self.tampon[:i]
                self.tampon = self.tampon[i + len(o):]
                self.dedans = True
                continue
            if self._prefixe_possible(self.tampon, _OUVERTURES):
                coupe = self.tampon.rfind("<")
                sortie += self.tampon[:coupe]
                self.tampon = self.tampon[coupe:]
            else:
                sortie += self.tampon
                self.tampon = ""
            break
        return sortie

    def fin(self) -> str:
        """Ce qui reste une fois le flux terminé. Un bloc de réflexion resté
        ouvert (génération tronquée par `num_predict`) est jeté."""
        reste = "" if self.dedans else self.tampon
        self.tampon, self.dedans = "", False
        return nettoyer(reste)


class OllamaProvider:
    def __init__(self) -> None:
        self.base = settings.ollama_base_url.rstrip("/")
        # Le premier appel charge le modèle en VRAM : il peut prendre une
        # minute là où les suivants en prennent dix secondes. On laisse donc
        # la connexion s'établir vite et la lecture durer.
        self.client = httpx.Client(
            timeout=httpx.Timeout(settings.llm_timeout, connect=5.0))

    def _modele(self, rapide: bool) -> str:
        """Un seul modèle par défaut.

        Alterner entre un 8B et un 4B sur une carte de 8 Go force Ollama à
        décharger puis recharger à chaque bascule, deux fois par tour. Le petit
        modèle fait gagner quelques secondes de génération et en coûte dix de
        chargement.
        """
        if settings.llm_modele_unique:
            return settings.llm_model
        return settings.llm_fast_model if rapide else settings.llm_model

    def _payload(self, system: str, user: str, model: str, *, fmt: Any = None,
                 temperature: float = 0.85,
                 max_tokens: int | None = None) -> dict[str, Any]:
        """La requête, construite en UN seul endroit.

        Le flux et l'appel bloquant doivent partager exactement les mêmes
        réglages : sinon la narration change de comportement selon qu'elle est
        streamée ou non, et on débogue deux moteurs au lieu d'un.
        """
        json_attendu = fmt is not None
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": settings.llm_num_ctx,
        }
        if json_attendu:
            # Le JSON n'a pas à être créatif, il a à être juste. Et surtout
            # AUCUNE pénalité de répétition : le modèle doit pouvoir répéter
            # un nom de personnage autant de fois qu'il le faut.
            options["top_p"] = 0.9
        else:
            # La narration, elle, se répète : un modèle local reprend ses
            # propres tournures d'un tour à l'autre jusqu'à ce que le joueur
            # les reconnaisse avant de les lire. Voir app/config.py.
            options["min_p"] = settings.llm_min_p
            options["repeat_penalty"] = settings.llm_repetition
            options["repeat_last_n"] = settings.llm_repetition_fenetre

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            # keep_alive garde le modèle en VRAM entre les appels d'un même tour
            "keep_alive": settings.llm_keep_alive,
            "options": options,
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if not settings.llm_reflexion:
            # Ignoré par les modèles qui ne savent pas réfléchir : sans effet,
            # sans erreur.
            payload["think"] = False
        if fmt is not None:
            payload["format"] = fmt
        return payload

    def _chat(self, system: str, user: str, model: str, *, fmt: Any = None,
              temperature: float = 0.85, max_tokens: int | None = None) -> str:
        payload = self._payload(system, user, model, fmt=fmt,
                                temperature=temperature, max_tokens=max_tokens)
        r = self.client.post(f"{self.base}/api/chat", json=payload)
        if r.status_code == 400 and "think" in payload:
            # Vieille version d'Ollama qui refuse le champ : on réessaie sans,
            # le nettoyage en sortie prendra le relais.
            payload.pop("think")
            r = self.client.post(f"{self.base}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def text(self, system: str, user: str, *, rapide: bool = False,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        brut = self._chat(system, user, self._modele(rapide),
                          temperature=(settings.llm_temperature
                                       if temperature is None else temperature),
                          max_tokens=max_tokens or settings.llm_max_narration)
        return achever(epurer(nettoyer(brut)))

    def flux(self, system: str, user: str, *, rapide: bool = False,
             temperature: float | None = None,
             max_tokens: int | None = None) -> Iterator[str]:
        """La même génération, rendue morceau par morceau.

        C'est le plus gros écart entre ce que le jeu vaut et ce qu'on en
        ressent : vingt secondes passées à regarder un écran figé sont longues,
        les mêmes vingt secondes passées à lire une scène qui s'écrit ne le
        sont pas.
        """
        payload = self._payload(
            system, user, self._modele(rapide),
            temperature=(settings.llm_temperature
                         if temperature is None else temperature),
            max_tokens=max_tokens or settings.llm_max_narration)
        payload["stream"] = True
        filtre = FiltreReflexion()
        with self.client.stream("POST", f"{self.base}/api/chat",
                                json=payload) as r:
            r.raise_for_status()
            for ligne in r.iter_lines():
                if not ligne:
                    continue
                try:
                    bloc = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                morceau = (bloc.get("message") or {}).get("content") or ""
                if morceau:
                    propre = filtre(morceau)
                    if propre:
                        yield propre
                if bloc.get("done"):
                    break
        reste = filtre.fin()
        if reste:
            yield reste

    def json(self, system: str, user: str, schema: dict, *,
             rapide: bool = True) -> dict:
        """Sortie structurée : Ollama contraint le décodage au schéma, donc le
        modèle NE PEUT PAS produire autre chose."""
        model = self._modele(rapide)
        brut = self._chat(system, user, model, fmt=schema,
                          temperature=settings.llm_temperature_json,
                          max_tokens=settings.llm_max_json)
        try:
            return json.loads(nettoyer(brut) or brut)
        except json.JSONDecodeError:
            brut2 = self._chat(
                system,
                f"{user}\n\nTa réponse précédente n'était pas du JSON valide :\n"
                f"{brut[:400]}\nRéponds UNIQUEMENT avec l'objet JSON demandé.",
                model, fmt=schema, temperature=0.0,
                max_tokens=settings.llm_max_json)
            return json.loads(nettoyer(brut2) or brut2)


class MockProvider:
    """Permet de développer et de tester toute l'application sans GPU.

    Les sorties sont plausibles et déterministes par graine, ce qui rend le
    parcours de bout en bout vérifiable automatiquement.
    """

    def __init__(self) -> None:
        self.rng = random.Random(7)

    def text(self, system: str, user: str, *, rapide: bool = False,
             temperature: float = 0.85, max_tokens: int | None = None) -> str:
        if "PRÉCÉDEMMENT" in system.upper():
            return ("[mock] Tu as quitté la scène sans avoir refermé ce que tu "
                    "y avais ouvert. Ce qui t'attendait t'attend encore, et "
                    "quelqu'un, entre-temps, a pris de l'avance.")
        if "PRÉSAGE" in system.upper():
            return ("[mock] Tu as grandi sans savoir d'où tu venais, et personne au village "
                    "n'a jamais voulu te le dire.\n\nUne marque sous l'épaule, que l'on a "
                    "toujours appelée une cicatrice.\n\nTu pars avec peu. Il te faudra le reste.")
        return ("[mock] La scène se resserre autour de toi. Ton geste ne passe pas inaperçu : "
                "quelqu'un, quelque part, vient d'en prendre note. Le silence qui suit dure "
                "un peu trop longtemps pour être innocent.")

    def flux(self, system: str, user: str, *, rapide: bool = False,
             temperature: float = 0.85, max_tokens: int | None = None) -> Iterator[str]:
        """Découpe la réponse factice en morceaux, sans attente artificielle :
        le parcours de bout en bout doit rester instantané."""
        texte = self.text(system, user, rapide=rapide, temperature=temperature)
        mots = texte.split(" ")
        for i in range(0, len(mots), 4):
            yield " ".join(mots[i:i + 4]) + (" " if i + 4 < len(mots) else "")

    def json(self, system: str, user: str, schema: dict, *, rapide: bool = True) -> dict:
        props = schema.get("properties", {})
        if "posture" in props:
            # Le mock se bat toujours de la même façon : les autres postures se
            # testent en appelant `combat.echanger` directement, ce qui rend le
            # parcours de bout en bout reproductible.
            return {"posture": "offensive", "cible": "", "technique": "",
                    "arme": "", "levier": "", "objectif": "",
                    "resume": "[mock] échange de coups"}
        if "action_type" in props:
            return {"action_type": "autre", "resume": "action du joueur",
                    "requiert_jet": True, "stat": "taijutsu", "difficulte": "normal",
                    "cible": "", "justification": "mock"}
        if "faits" in props:
            return {"faits": [{"texte": "[mock] Le joueur a agi de façon notable.",
                               "importance": 2}],
                    "relations": [], "quetes": [], "ressources": [], "xp": 15,
                    "graine_intrigue": "", "rencontres": [],
                    "propositions": [
                        {"texte": "Observer sans intervenir", "risque": "faible"},
                        {"texte": "Aborder directement", "risque": "moyen"},
                        {"texte": "Chercher un autre angle", "risque": "faible"}]}
        if "realises" in props:
            # Aucune révélation en mode mock : le parcours doit rester
            # reproductible, et une destinée qui s'ouvre au hasard ne l'est pas.
            return {"realises": []}
        if "titre" in props and "enjeu" in props:
            return {"titre": "[mock] Le sanglier du charbonnier",
                    "description": "[mock] Un charbonnier ne peut plus monter à "
                                   "sa coupe. Il paie mal et il le sait.",
                    "enjeu": "[mock] Sans charbon, le quartier passe l'hiver au froid.",
                    "commanditaire_nom": "[mock] Bureau des missions",
                    "premiere_piste": "[mock] La route du charbon, au nord."}
        if "propositions" in props:
            return {"propositions": [
                {"texte": "Observer sans intervenir", "risque": "faible"},
                {"texte": "Aborder directement", "risque": "moyen"},
                {"texte": "Chercher un autre angle", "risque": "faible"},
            ]}
        if "personnages" in props:
            noms = ["Hiroshi Tanaka", "Mio Uchiha", "Daichi Mori", "Rin Kurosawa"]
            return {"personnages": [
                {"nom": noms[i % len(noms)],
                 "sexe": "masculin" if i % 2 == 0 else "feminin",
                 "age": 30 if i == 0 else 13,
                 "personnalite": "[mock] calme, exigeant, dissimule mal son inquiétude",
                 "parler": "[mock] phrases courtes, ton posé",
                 "apparence": "[mock] silhouette reconnaissable de loin",
                 "histoire": "[mock] a vécu la dernière guerre et n'en parle pas",
                 "objectifs": ["[mock] former une équipe qui survivra"],
                 "secret": "[mock] sait quelque chose sur l'origine du joueur",
                 "relation_valeur": 20,
                 "relation_nature": "mentor"}
                for i in range(4)]}
        if "personnalite" in props:
            return {"nom": "Inconnu de passage", "personnalite": "[mock] méfiant, économe de mots",
                    "parler": "[mock] accent rural, phrases brèves",
                    "apparence": "[mock] vêtements de voyage usés",
                    "objectif": "[mock] repartir avant la nuit",
                    "secret": "[mock] transporte quelque chose qu'il ne déclare pas"}
        if "segments" in props:
            return {"segments": [{"texte": "[mock]", "locuteur": "", "type": "narration",
                                  "emotion": "neutre", "intensite": 2}]}
        if "resume" in props:
            return {"resume": "[mock] Résumé de la période écoulée."}
        return {k: {"string": "", "integer": 0, "number": 0, "boolean": False,
                    "array": [], "object": {}}.get(v.get("type"), None)
                for k, v in props.items()}


# --------------------------------------------------------------------------
# Les IA en ligne, et le relais
# --------------------------------------------------------------------------
# Une carte de 8 Go plafonne à un modèle de 8 milliards de paramètres : c'est
# le bas de gamme pour un maître du jeu. Les services en ligne gratuits prêtent
# des modèles bien plus gros, mais aucun n'est illimité. D'où le RELAIS : le
# jeu raconte avec le service en ligne tant qu'il répond, et bascule sur Ollama
# dès qu'il refuse (quota, réseau, clé). La partie ne s'arrête jamais ; seule
# la plume change, le temps que le quota revienne.
#
# Changer de conteur ne coûte aucune mémoire : aucun appel n'a d'historique,
# le moteur renvoie à chaque fois tout le dossier de la scène depuis la base.

# Tous parlent le dialecte « compatible OpenAI ». `intervalle` est l'écart
# minimal entre deux requêtes : l'offre gratuite de Mistral en tolère une par
# seconde, celle de Gemini une dizaine par minute.
SERVICES: dict[str, dict[str, Any]] = {
    # Mesuré sur une clé gratuite (septembre 2026) : Medium, Small et Large y
    # sont fermés (0 requête/minute ou 403) ; Ministral 14B est ouvert à 30
    # requêtes/minute — d'où un intervalle de 2,1 s.
    "mistral": {"nom": "Mistral", "url": "https://api.mistral.ai/v1",
                "modele": "ministral-14b-latest", "intervalle": 2.1, "cle": True},
    "gemini": {"nom": "Gemini",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "modele": "gemini-2.5-flash", "intervalle": 6.0, "cle": True},
    "groq": {"nom": "Groq", "url": "https://api.groq.com/openai/v1",
             "modele": "llama-3.3-70b-versatile", "intervalle": 2.0, "cle": True},
    "openrouter": {"nom": "OpenRouter", "url": "https://openrouter.ai/api/v1",
                   "modele": "", "intervalle": 3.0, "cle": True},
    "lmstudio": {"nom": "LM Studio", "url": "http://localhost:1234/v1",
                 "modele": "", "intervalle": 0.0, "cle": False},
}

_DEFAUTS_JSON = {"string": "", "integer": 0, "number": 0, "boolean": False,
                 "array": [], "object": {}}


class Refus(Exception):
    """Le service a répondu, mais rien d'utilisable : réponse vide, filtrée,
    ou JSON illisible deux fois de suite. On passe la main pour CET appel."""


class ServiceIndisponible(Exception):
    """Le service ne peut pas servir du tout (clé absente, modèle non
    configuré). Le relais passe durablement au secours."""


def _sans_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(c) != "Mn")


def conformer(valeur: Any, schema: dict) -> Any:
    """Ramène une réponse JSON au schéma attendu.

    Ollama contraint le décodage : le modèle ne peut pas sortir du schéma. Un
    service en ligne, lui, reçoit le schéma en consigne et le suit presque
    toujours — presque. Une clé oubliée ou une valeur hors liste casserait le
    moteur plus loin ; on complète donc les manques par des valeurs neutres, on
    convertit « 3 » en 3, et on rapproche « Moyen » de « moyen ».
    """
    type_ = schema.get("type")
    if type_ == "object" or "properties" in schema:
        if not isinstance(valeur, dict):
            valeur = {}
        for cle, sous in (schema.get("properties") or {}).items():
            if cle not in valeur or valeur[cle] is None:
                valeur[cle] = conformer(None, sous) if (
                    sous.get("type") == "object" or "properties" in sous) else \
                    json.loads(json.dumps(_DEFAUTS_JSON.get(sous.get("type"), "")))
            else:
                valeur[cle] = conformer(valeur[cle], sous)
        return valeur
    if type_ == "array":
        if not isinstance(valeur, list):
            return []
        items = schema.get("items") or {}
        return [conformer(v, items) for v in valeur] if items else valeur
    if "enum" in schema and valeur not in schema["enum"]:
        proches = [e for e in schema["enum"]
                   if isinstance(e, str) and _sans_accents(e) == _sans_accents(str(valeur))]
        return proches[0] if proches else schema["enum"][0]
    if type_ == "integer":
        try:
            return int(float(valeur))
        except (TypeError, ValueError):
            return 0
    if type_ == "number":
        try:
            return float(valeur)
        except (TypeError, ValueError):
            return 0
    if type_ == "boolean" and isinstance(valeur, str):
        return valeur.strip().lower() in ("true", "vrai", "oui", "1")
    if type_ == "string":
        # Les textes du JSON s'affichent tels quels (fiches, pistes) : pas
        # d'astérisques de mise en forme.
        return epurer("" if valeur is None else str(valeur))
    return valeur


def _contenu(c: Any) -> str:
    """Le texte d'un message. Certains modèles (Magistral…) rendent une liste
    de morceaux typés, dont des morceaux de réflexion à ne pas montrer."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c
                       if isinstance(p, dict) and p.get("type", "text") == "text")
    return ""


class EnLigneProvider:
    """Un service en ligne « compatible OpenAI » : Mistral, Gemini, Groq…"""

    def __init__(self, client: httpx.Client | None = None) -> None:
        cle_service = settings.en_ligne_service.lower().strip()
        base = SERVICES.get(cle_service, SERVICES["mistral"])
        self.nom = base["nom"] if cle_service in SERVICES else cle_service.title()
        self.url = (settings.en_ligne_url or base["url"]).rstrip("/")
        self.modele = settings.en_ligne_modele or base["modele"]
        self.modele_rapide = settings.en_ligne_modele_rapide or self.modele
        self.cle = settings.en_ligne_cle.strip()
        self.intervalle = (base["intervalle"] if settings.en_ligne_intervalle < 0
                           else settings.en_ligne_intervalle)
        self.cle_requise = base["cle"]
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(settings.llm_timeout, connect=10.0))
        self._verrou = threading.Lock()
        self._dernier_envoi = 0.0

    # --- l'envoi --------------------------------------------------------------
    def verifier(self) -> None:
        """Lève `ServiceIndisponible` si le service ne peut pas servir."""
        if self.cle_requise and not self.cle:
            raise ServiceIndisponible(
                f"Pas de clé {self.nom} dans .env (EN_LIGNE_CLE).")
        if not self.modele:
            raise ServiceIndisponible(
                f"Aucun modèle choisi pour {self.nom} (EN_LIGNE_MODELE).")

    def _entetes(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cle}"} if self.cle else {}

    def _attendre_son_tour(self) -> None:
        """Espace les requêtes : dépasser le débit autorisé coûte un refus,
        puis une attente plus longue que celle qu'on voulait éviter."""
        with self._verrou:
            reste = self._dernier_envoi + self.intervalle - time.monotonic()
            if reste > 0:
                time.sleep(reste)
            self._dernier_envoi = time.monotonic()

    def _envoyer(self, payload: dict, *, flux: bool = False) -> httpx.Response:
        """POST avec patience : un « trop de requêtes » passager est attendu
        et retenté avant d'abandonner la main au secours."""
        self.verifier()
        for essai in range(settings.en_ligne_essais):
            self._attendre_son_tour()
            requete = self.client.build_request(
                "POST", f"{self.url}/chat/completions", json=payload,
                headers=self._entetes())
            r = self.client.send(requete, stream=flux)
            # Un plafond de ZÉRO n'est pas un quota épuisé : le modèle n'est
            # pas ouvert sur cette offre. Attendre n'y changera rien.
            if r.status_code == 429 and \
                    r.headers.get("x-ratelimit-limit-req-minute") == "0":
                r.close()
                raise ServiceIndisponible(
                    f"Le modèle {payload['model']} n'est pas ouvert sur ton offre "
                    f"{self.nom} — choisis-en un autre (EN_LIGNE_MODELE).")
            if r.status_code in (429, 500, 502, 503, 504) and \
                    essai + 1 < settings.en_ligne_essais:
                attente = r.headers.get("retry-after", "")
                try:
                    delai = float(attente)
                except ValueError:
                    delai = 2.0 ** essai
                r.close()
                time.sleep(min(delai, 8.0))
                continue
            if r.status_code >= 400:
                if flux:
                    r.read()
                r.raise_for_status()
            return r
        raise AssertionError("inatteignable")  # pragma: no cover

    def _payload(self, system: str, user: str, *, rapide: bool,
                 temperature: float, max_tokens: int) -> dict[str, Any]:
        return {"model": self.modele_rapide if rapide else self.modele,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": temperature, "max_tokens": max_tokens}

    def _message(self, r: httpx.Response) -> str:
        """Le texte de la réponse, ou `Refus` si elle n'a pas la forme
        attendue (un service qui change de format ne doit pas planter un tour :
        le secours prend l'appel)."""
        return self._reponse(r)[0]

    def _reponse(self, r: httpx.Response) -> tuple[str, str]:
        """(texte, raison de fin). « length » : coupé par le plafond."""
        try:
            choix = r.json()["choices"][0]
            return _contenu(choix["message"].get("content")), choix.get("finish_reason") or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise Refus(f"{self.nom} a rendu une réponse illisible.") from exc

    # --- l'interface du moteur --------------------------------------------------
    def text(self, system: str, user: str, *, rapide: bool = False,
             temperature: float | None = None, max_tokens: int | None = None) -> str:
        payload = self._payload(
            system, user, rapide=rapide, max_tokens=max_tokens or settings.llm_max_narration,
            temperature=settings.llm_temperature if temperature is None else temperature)
        texte = achever(epurer(nettoyer(self._message(self._envoyer(payload)))))
        if not texte:
            raise Refus(f"{self.nom} a rendu une réponse vide.")
        return texte

    def flux(self, system: str, user: str, *, rapide: bool = False,
             temperature: float | None = None,
             max_tokens: int | None = None) -> Iterator[str]:
        payload = self._payload(
            system, user, rapide=rapide, max_tokens=max_tokens or settings.llm_max_narration,
            temperature=settings.llm_temperature if temperature is None else temperature)
        payload["stream"] = True
        filtre = FiltreReflexion()
        r = self._envoyer(payload, flux=True)
        try:
            for ligne in r.iter_lines():
                if not ligne.startswith("data:"):
                    continue
                donnees = ligne[5:].strip()
                if donnees == "[DONE]":
                    break
                try:
                    bloc = json.loads(donnees)
                except json.JSONDecodeError:
                    continue
                for choix in bloc.get("choices") or []:
                    morceau = _contenu((choix.get("delta") or {}).get("content"))
                    propre = filtre(morceau) if morceau else ""
                    if propre:
                        yield propre
        finally:
            r.close()
        reste = filtre.fin()
        if reste:
            yield reste

    def json(self, system: str, user: str, schema: dict, *,
             rapide: bool = True) -> dict:
        """Le schéma voyage en consigne, pas en contrainte : on le vérifie donc
        à l'arrivée (`conformer`)."""
        # La concision n'est pas qu'une affaire de goût : un modèle en ligne
        # écrit volontiers un paragraphe par champ, et un JSON coupé par le
        # plafond est un JSON perdu (mesuré : l'entourage de départ dépassait
        # 2 000 jetons et échouait deux fois de suite).
        consigne = (f"{system}\n\nRéponds UNIQUEMENT avec un objet JSON valide, "
                    f"sans texte autour ni bloc de code, conforme à ce schéma :\n"
                    f"{json.dumps(schema, ensure_ascii=False)}\n"
                    f"Sois concis : chaque texte tient en une ou deux phrases courtes.")
        demande = user
        for _ in range(2):
            payload = self._payload(consigne, demande, rapide=rapide,
                                    temperature=settings.llm_temperature_json,
                                    max_tokens=settings.en_ligne_max_json)
            payload["response_format"] = {"type": "json_object"}
            brut, fin = self._reponse(self._envoyer(payload))
            brut = re.sub(r"^```(?:json)?\s*|\s*```$", "", nettoyer(brut).strip())
            try:
                return conformer(json.loads(brut), schema)
            except json.JSONDecodeError:
                if fin == "length":
                    demande = (f"{user}\n\nTa réponse précédente était trop longue et "
                               f"a été coupée. Recommence en réduisant chaque texte à "
                               f"une seule phrase.")
                else:
                    demande = (f"{user}\n\nTa réponse précédente n'était pas du JSON "
                               f"valide :\n{brut[:400]}\nRéponds UNIQUEMENT avec "
                               f"l'objet JSON demandé.")
        raise Refus(f"{self.nom} n'a pas rendu de JSON lisible.")


class RelaisProvider:
    """Le conteur principal, et un secours qui ne s'arrête jamais.

    UNE VOIX LE PLUS LONGTEMPS POSSIBLE. On ne change pas de conteur à chaque
    appel : après une panne, le principal est mis de côté pendant une pause
    (quota : `relais_pause` ; réseau : deux minutes ; clé refusée : jusqu'au
    redémarrage). Un simple refus ponctuel — réponse vide, JSON illisible — ne
    fait passer la main que pour cet appel-là.
    """

    def __init__(self, principal: Any, secours: Any | None,
                 nom_secours: str = "Ollama") -> None:
        self.principal, self.secours = principal, secours
        self.nom_principal = getattr(principal, "nom", "principal")
        self.nom_secours = nom_secours
        self.raison = ""
        self._pause_jusqua = 0.0
        self.dernier = self.nom_principal
        try:
            principal.verifier()
        except ServiceIndisponible as exc:
            self._mettre_de_cote(str(exc), float("inf"))
        except AttributeError:
            pass

    # --- l'état --------------------------------------------------------------
    def _principal_actif(self) -> bool:
        if self.secours is None:
            return True
        if time.monotonic() >= self._pause_jusqua:
            if self._pause_jusqua:
                self._pause_jusqua, self.raison = 0.0, ""
            return True
        return False

    def _mettre_de_cote(self, raison: str, duree: float) -> None:
        self.raison = raison
        self._pause_jusqua = time.monotonic() + duree

    def _panne(self, exc: Exception) -> None:
        """Classe la panne, et décide combien de temps on s'en passe."""
        nom = self.nom_principal
        if isinstance(exc, ServiceIndisponible):
            self._mettre_de_cote(str(exc), float("inf"))
        elif isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            if code == 401:
                self._mettre_de_cote(f"Clé {nom} refusée (erreur 401).", float("inf"))
            elif code == 403:
                # Souvent : « ce modèle n'est pas disponible dans ton offre ».
                try:
                    detail = str(exc.response.json().get("message", ""))[:120]
                except Exception:  # noqa: BLE001
                    detail = ""
                self._mettre_de_cote(
                    f"{nom} refuse l'accès (erreur 403){' : ' + detail if detail else ''} "
                    f"— vérifie EN_LIGNE_MODELE.", float("inf"))
            elif code == 429:
                minutes = round(settings.relais_pause / 60)
                self._mettre_de_cote(
                    f"Quota {nom} atteint — nouvel essai dans {minutes} min.",
                    settings.relais_pause)
            elif code >= 500:
                self._mettre_de_cote(f"{nom} en difficulté (erreur {code}).", 120)
            else:
                self._mettre_de_cote(
                    f"{nom} refuse la requête (erreur {code}) — vérifie "
                    f"EN_LIGNE_MODELE.", settings.relais_pause)
        else:
            self._mettre_de_cote(f"{nom} injoignable ({exc.__class__.__name__}).", 120)

    def etat(self) -> dict[str, Any]:
        actif = self._principal_actif()
        return {"relais": True, "principal": self.nom_principal,
                "secours": self.nom_secours if self.secours else "",
                "conteur": self.nom_principal if actif else self.nom_secours,
                "en_secours": not actif, "raison": self.raison}

    # --- les appels ------------------------------------------------------------
    @staticmethod
    def _recuperable(exc: Exception) -> bool:
        return isinstance(exc, (httpx.HTTPError, ServiceIndisponible, Refus))

    def _appeler(self, methode: str, *args: Any, **kw: Any) -> Any:
        if self._principal_actif():
            try:
                resultat = getattr(self.principal, methode)(*args, **kw)
                self.dernier = self.nom_principal
                return resultat
            except Exception as exc:  # noqa: BLE001 — trié juste en dessous
                if self.secours is None or not self._recuperable(exc):
                    raise
                if not isinstance(exc, Refus):
                    self._panne(exc)
                journal.warning("Relais %s : %s → %s (%s)", methode, self.nom_principal,
                                self.nom_secours, self.raison or exc)
        self.dernier = self.nom_secours
        return getattr(self.secours, methode)(*args, **kw)

    def text(self, system: str, user: str, **kw: Any) -> str:
        return self._appeler("text", system, user, **kw)

    def json(self, system: str, user: str, schema: dict, **kw: Any) -> dict:
        return self._appeler("json", system, user, schema, **kw)

    def flux(self, system: str, user: str, **kw: Any) -> Iterator[str]:
        """Le relais d'un flux se joue AVANT le premier mot : tant que rien
        n'est affiché, on peut changer de conteur sans que ça se voie. Une
        coupure en pleine phrase, elle, fait échouer le tour — le joueur le
        rejoue, et le secours le raconte."""
        if self._principal_actif():
            gen = self.principal.flux(system, user, **kw)
            try:
                premier = next(gen)
            except StopIteration:
                premier = None
            except Exception as exc:  # noqa: BLE001
                if self.secours is None or not self._recuperable(exc):
                    raise
                if not isinstance(exc, Refus):
                    self._panne(exc)
                journal.warning("Relais flux : %s → %s (%s)", self.nom_principal,
                                self.nom_secours, self.raison or exc)
                premier = None
            else:
                self.dernier = self.nom_principal
                yield premier
                try:
                    yield from gen
                except Exception as exc:  # noqa: BLE001
                    if self._recuperable(exc) and not isinstance(exc, Refus):
                        self._panne(exc)
                    raise
                return
            if self.secours is None:
                return
        self.dernier = self.nom_secours
        yield from self.secours.flux(system, user, **kw)


_INSTANCE: LLMProvider | None = None


def _fabriquer() -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider()
    if settings.llm_provider == "en_ligne":
        secours = OllamaProvider() if settings.llm_secours == "ollama" else None
        return RelaisProvider(EnLigneProvider(), secours)
    return MockProvider()


def get_llm() -> LLMProvider:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = _fabriquer()
    return _INSTANCE


def conteur() -> dict[str, Any]:
    """Qui raconte en ce moment — pour l'indicateur de la table et /sante."""
    llm = get_llm()
    if hasattr(llm, "etat"):
        return llm.etat()
    nom = "Ollama" if settings.llm_provider == "ollama" else "mock"
    return {"relais": False, "principal": nom, "secours": "", "conteur": nom,
            "en_secours": False, "raison": ""}


def reset_llm() -> None:
    """Utile aux tests, pour rebasculer de provider."""
    global _INSTANCE
    _INSTANCE = None
