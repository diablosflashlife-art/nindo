"""Configuration lue depuis les variables d'environnement / .env."""
import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Le CODE et ses ressources (gabarits, lore, règles). Dans l'application
# empaquetée, c'est le dossier d'installation — remplacé à chaque mise à jour.
ROOT = Path(__file__).resolve().parent.parent

# Nindō.exe (PyInstaller) plutôt que le code source ?
EMPAQUETE = bool(getattr(sys, "frozen", False))


def _dossier_donnees() -> Path:
    """Les DONNÉES du joueur : ses parties et sa clé. Jamais dans le dossier
    d'installation, qu'une mise à jour remplace en entier — dans %APPDATA%.
    Depuis le code source, rien ne change : tout reste dans le projet."""
    if os.environ.get("NINDO_DONNEES"):
        return Path(os.environ["NINDO_DONNEES"])
    if EMPAQUETE:
        return Path(os.environ.get("APPDATA") or Path.home()) / "Nindo"
    return ROOT


DONNEES = _dossier_donnees()
(DONNEES / "data").mkdir(parents=True, exist_ok=True)
ENV_FICHIER = DONNEES / ".env"

# Premier lancement de l'application : un réglage par défaut raisonnable.
# Mistral raconte si le joueur a mis sa clé (depuis le lanceur), Ollama sinon.
if EMPAQUETE and not ENV_FICHIER.exists():
    ENV_FICHIER.write_text(
        "LLM_PROVIDER=en_ligne\nEN_LIGNE_SERVICE=mistral\nEN_LIGNE_CLE=\n"
        "LLM_SECOURS=ollama\nLLM_MODEL=qwen3:8b\nEMBEDDINGS=true\n",
        encoding="utf-8")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FICHIER), extra="ignore")

    # --- IA -----------------------------------------------------------------
    llm_provider: str = "mock"          # en_ligne | ollama | mock
    ollama_base_url: str = "http://localhost:11434"

    # --- IA en ligne (LLM_PROVIDER=en_ligne) ----------------------------------
    # Un service « compatible OpenAI » raconte ; Ollama prend le relais dès
    # qu'il refuse (quota, réseau, clé). Voir app/llm/provider.py.
    en_ligne_service: str = "mistral"   # mistral | gemini | groq | openrouter | lmstudio
    en_ligne_cle: str = ""
    en_ligne_modele: str = ""           # vide : le modèle conseillé du service
    en_ligne_modele_rapide: str = ""    # JSON et résumés ; vide : le même
    en_ligne_url: str = ""              # vide : l'adresse du service
    en_ligne_intervalle: float = -1     # secondes entre deux requêtes ; -1 : selon le service
    en_ligne_essais: int = 3            # tentatives sur « trop de requêtes »
    en_ligne_max_json: int = 3000
    llm_secours: str = "ollama"         # ollama | aucun
    relais_pause: int = 600             # quota atteint : secondes avant de réessayer
    llm_model: str = "qwen3:8b"         # narrateur
    llm_fast_model: str = "qwen3:4b"    # utilitaire : JSON, résumés, génération
    llm_num_ctx: int = 8192

    # Un seul modèle pour tout. Sur une carte de 8 Go, 5 Go + 2,5 Go ne
    # cohabitent pas : Ollama décharge et recharge à chaque bascule, deux fois
    # par tour, ce qui coûte plus que le gain de vitesse du petit modèle.
    llm_modele_unique: bool = True
    # Garde le modèle en VRAM entre les appels. Sans ça, chaque tour recommence
    # par un chargement de plusieurs secondes.
    llm_keep_alive: str = "30m"
    # Plafond de génération. Le prompt demande 150 à 280 mots ; sans plafond un
    # modèle local part en roue libre une fois sur dix.
    llm_max_narration: int = 600
    # Le JSON contraint s'arrête de lui-même quand l'objet est fermé : ce
    # plafond ne ralentit rien, il évite seulement de couper l'entourage (quatre
    # fiches de PNJ, ~2 700 caractères) en plein milieu. 800 le coupait.
    llm_max_json: int = 2000
    # Les modèles à réflexion (qwen3 et suivants) émettent un bloc <think> avant
    # de répondre. Non désactivé, il finit dans la narration du joueur.
    llm_reflexion: bool = False

    # --- échantillonnage ----------------------------------------------------
    # Les réglages qui décident de la QUALITÉ sur un modèle local, et qu'on
    # laissait par défaut. Mesurés sur des parties réelles :
    #
    #   `repetition` est le plus important des quatre. Un modèle de 8 milliards
    #   de paramètres reprend ses propres tournures d'un tour à l'autre — « le
    #   silence se fait », « quelque chose vient de changer » — et au bout de
    #   dix tours le joueur reconnaît les phrases avant de les lire. 1.10 casse
    #   la boucle sans appauvrir le vocabulaire ; au-delà de 1.2 la syntaxe
    #   commence à se déformer.
    #
    #   `min_p` remplace avantageusement un top_p fixe : il coupe la queue en
    #   PROPORTION du meilleur candidat, donc sévèrement quand le modèle est
    #   sûr de lui et largement quand il hésite. C'est exactement ce qu'on veut
    #   d'un narrateur.
    llm_temperature: float = 0.82       # narration : vivant sans partir ailleurs
    llm_min_p: float = 0.05
    llm_repetition: float = 1.10
    llm_repetition_fenetre: int = 320   # tokens regardés en arrière

    # Le JSON n'a pas à être créatif : il a à être juste. Une température
    # basse et une fenêtre de répétition nulle — sinon le modèle évite de
    # répéter un nom de personnage qu'il DOIT répéter.
    llm_temperature_json: float = 0.1

    # Au-delà, on considère que le modèle est parti ou que le service ne
    # répond plus. Cinq minutes d'attente sans message n'aident personne.
    llm_timeout: float = 120.0

    # --- base ---------------------------------------------------------------
    database_url: str = f"sqlite:///{(DONNEES / 'data' / 'parties.db').as_posix()}"

    # --- mémoire ------------------------------------------------------------
    tours_recents: int = 6              # tours injectés mot à mot
    resume_tous_les: int = 10           # fréquence des résumés de scène
    budget_contexte: int = 6000         # tokens visés, tous anneaux confondus
    budget_lore: int = 1400             # part réservée au dossier lore de la scène

    # Rappel par le sens : « ce que j'ai juré à Tetsuo » doit retrouver « a
    # promis au vieux forgeron ». Demande `ollama pull nomic-embed-text` ;
    # sans ce modèle, le jeu retombe silencieusement sur le rappel lexical.
    embeddings: bool = True
    embed_model: str = "nomic-embed-text"

    # --- cristallisation ----------------------------------------------------
    cristallisations_par_session: int = 3
    seuil_complet: int = 3              # apparitions avant fiche complète

    # --- confort ------------------------------------------------------------
    # La narration arrive mot à mot au lieu d'apparaître d'un bloc. C'est le
    # plus gros écart entre ce que le jeu vaut et ce qu'on en ressent : vingt
    # secondes devant un écran figé sont longues, les mêmes vingt secondes
    # passées à lire une scène qui s'écrit ne le sont pas. Repasser à `false`
    # rebascule sur le chemin bloquant, qui reste entièrement fonctionnel.
    narration_en_flux: bool = True

    # --- voix ---------------------------------------------------------------
    voix_active: bool = False


settings = Settings()
