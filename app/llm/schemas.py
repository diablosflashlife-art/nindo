"""Contrats JSON entre le moteur et le modèle.

Le modèle ne « parle » au moteur qu'à travers ces schémas. Il ne décide jamais
d'un résultat chiffré : il propose une intention, puis des conséquences, que
le moteur valide avant d'écrire.
"""

# --- étape 2 du tour : que tente le joueur, avec quelle caractéristique ?
INTENT = {
    "type": "object",
    "properties": {
        "action_type": {"type": "string", "enum": [
            "dialogue", "deplacement", "exploration", "combat", "technique",
            "discretion", "social", "repos", "autre"]},
        "resume": {"type": "string"},
        "requiert_jet": {"type": "boolean"},
        "stat": {"type": "string"},
        "difficulte": {"type": "string", "enum": [
            "triviale", "facile", "normal", "difficile", "ardue", "legendaire"]},
        "cible": {"type": "string"},
        "justification": {"type": "string"},
        # L'action peut-elle seulement avoir lieu ici ? Voir ARBITRE : « je tue
        # Madara » était passé sous silence, faute de savoir quoi en faire.
        "faisable": {"type": "string", "enum": ["oui", "improbable", "non"]},
        "obstacle": {"type": "string"},
    },
    "required": ["action_type", "resume", "requiert_jet", "stat", "difficulte",
                 "faisable"],
}

# --- étape 2 pendant un affrontement : la même question, un autre vocabulaire.
# On ne demande pas au modèle QUI touche ni COMBIEN il encaisse — seulement ce
# que le joueur a voulu faire. Tout le reste est calculé.
INTENT_COMBAT = {
    "type": "object",
    "properties": {
        "posture": {"type": "string", "enum": [
            "offensive", "mesuree", "defensive", "technique", "manoeuvre",
            "desengagement"]},
        "cible": {"type": "string"},
        "technique": {"type": "string"},
        "arme": {"type": "string"},
        # Uniquement quand la posture est une manœuvre : quel avantage elle
        # cherche à établir. La liste est celle du ruleset.
        "levier": {"type": "string", "enum": [
            "", "embuscade", "terrain_prepare", "renseignement", "nombre",
            "adversaire_diminue", "enjeu_emotionnel", "contre_mesure",
            "sacrifice"]},
        # Uniquement quand vaincre est hors de portée : ce que le joueur vise.
        "objectif": {"type": "string"},
        "resume": {"type": "string"},
    },
    "required": ["posture", "resume"],
}

# --- étape 5 : des deltas, pas de la prose
CONSEQUENCES = {
    "type": "object",
    "properties": {
        "faits": {"type": "array", "items": {
            "type": "object",
            "properties": {"texte": {"type": "string"},
                           "importance": {"type": "integer"}},
            "required": ["texte", "importance"]}},
        "relations": {"type": "array", "items": {
            "type": "object",
            "properties": {"personnage": {"type": "string"},
                           "delta": {"type": "integer"},
                           "raison": {"type": "string"}},
            "required": ["personnage", "delta", "raison"]}},
        "quetes": {"type": "array", "items": {
            "type": "object",
            "properties": {"titre": {"type": "string"},
                           "statut": {"type": "string", "enum": [
                               "proposée", "acceptée", "en cours",
                               "réussie", "échouée", "refusée"]}},
            "required": ["titre", "statut"]}},
        "ressources": {"type": "array", "items": {
            "type": "object",
            "properties": {"nom": {"type": "string"}, "delta": {"type": "integer"}},
            "required": ["nom", "delta"]}},
        "xp": {"type": "integer"},
        "graine_intrigue": {"type": "string"},
        # Les fils du récit : une question nouvelle, et les questions résolues
        # (par leur numéro dans FILS OUVERTS). Voir engine/fils.py.
        "mystere_nouveau": {"type": "string"},
        "mysteres_resolus": {"type": "array", "items": {
            "type": "object",
            "properties": {"numero": {"type": "integer"},
                           "reponse": {"type": "string"}},
            "required": ["numero", "reponse"]}},
        # Qui la scène a-t-elle introduit qui n'existait pas encore ? C'est ce
        # qui déclenche la cristallisation — sans quoi le monde reste peuplé
        # des quatre personnages de l'amorce et de personne d'autre.
        "rencontres": {"type": "array", "items": {
            "type": "object",
            "properties": {"role": {"type": "string"},
                           "nom": {"type": "string"},
                           "importance": {"type": "string", "enum": [
                               "figurant", "notable"]},
                           "scene": {"type": "string"}},
            "required": ["role", "importance"]}},
        # Les pistes offertes au joueur sont extraites DE LA MÊME SCÈNE, par le
        # même appel. Deux appels séparés faisaient relire au modèle le même
        # texte pour la même chose : un quart de la latence d'un tour, pour
        # rien. Voir app/llm/prompts.py — CONSEQUENCES.
        "propositions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "texte": {"type": "string"},
                "risque": {"type": "string", "enum": ["faible", "moyen", "eleve"]},
            },
            "required": ["texte", "risque"]}},
    },
    "required": ["faits", "relations", "quetes", "ressources", "xp",
                 "propositions"],
}

# --- étape 5bis : la scène a-t-elle réalisé un déclencheur de destinée ?
# On n'envoie JAMAIS la vérité du trait — seulement la condition à remplir.
REVELATION = {
    "type": "object",
    "properties": {
        "realises": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"},
                           "pourquoi": {"type": "string"}},
            "required": ["id", "pourquoi"]}},
    },
    "required": ["realises"],
}

# --- génération de mission : le moteur fournit l'ossature, le modèle habille
MISSION = {
    "type": "object",
    "properties": {
        "titre": {"type": "string"},
        "description": {"type": "string"},
        "enjeu": {"type": "string"},
        "commanditaire_nom": {"type": "string"},
        "premiere_piste": {"type": "string"},
    },
    "required": ["titre", "description", "enjeu"],
}

# --- propositions d'actions offertes au joueur (il garde le texte libre)
PROPOSITIONS = {
    "type": "object",
    "properties": {
        "propositions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "texte": {"type": "string"},
                "risque": {"type": "string", "enum": ["faible", "moyen", "eleve"]},
            },
            "required": ["texte", "risque"]}},
    },
    "required": ["propositions"],
}

# --- amorce de campagne : la distribution générée par l'IA
DISTRIBUTION = {
    "type": "object",
    "properties": {
        "personnages": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "nom": {"type": "string"},
                "sexe": {"type": "string"},
                "age": {"type": "integer"},
                "personnalite": {"type": "string"},
                "parler": {"type": "string"},
                "apparence": {"type": "string"},
                "histoire": {"type": "string"},
                "objectifs": {"type": "array", "items": {"type": "string"}},
                "secret": {"type": "string"},
                "relation_valeur": {"type": "integer"},
                "relation_nature": {"type": "string"},
            },
            "required": ["nom", "personnalite", "parler", "objectifs", "secret",
                         "relation_valeur", "relation_nature"]}},
    },
    "required": ["personnages"],
}

# --- cristallisation d'un PNJ rencontré en jeu
CRISTALLISATION = {
    "type": "object",
    "properties": {
        "nom": {"type": "string"},
        "personnalite": {"type": "string"},
        "parler": {"type": "string"},
        "apparence": {"type": "string"},
        "objectif": {"type": "string"},
        "secret": {"type": "string"},
    },
    "required": ["nom", "personnalite", "parler", "objectif"],
}

RESUME = {
    "type": "object",
    "properties": {"resume": {"type": "string"}},
    "required": ["resume"],
}

# --- segmentation vocale (préparé pour la couche voix)
SEGMENTS = {
    "type": "object",
    "properties": {
        "segments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "texte": {"type": "string"},
                "locuteur": {"type": "string"},
                "type": {"type": "string", "enum": [
                    "narration", "dialogue", "pensee", "cri", "chuchotement"]},
                "emotion": {"type": "string", "enum": [
                    "neutre", "tension", "combat", "mystere", "tristesse", "surprise",
                    "epique", "humour", "froideur", "chaleur", "menace", "epuisement"]},
                "intensite": {"type": "integer"},
            },
            "required": ["texte", "type", "emotion", "intensite"]}},
    },
    "required": ["segments"],
}
