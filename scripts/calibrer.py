"""Calibrer le modèle local : `python -m scripts.calibrer`

POURQUOI CET OUTIL. Les bons réglages d'un jeu local dépendent de la machine —
VRAM, modèle installé, vitesse de génération — et personne ne peut les deviner
à ta place. Plutôt qu'un tableau de recommandations qui sera faux chez toi, ce
script MESURE : il joue un tour complet avec des prompts de taille réelle,
chronomètre chaque appel, et te rend les lignes de `.env` à copier.

CE QU'IL MESURE, ET POURQUOI CHAQUE CHIFFRE COMPTE.

  Le CHARGEMENT. Premier appel après un démarrage d'Ollama : c'est lui qui
  donne l'impression que le jeu rame alors qu'il attend la VRAM. S'il est long
  et revient souvent, c'est `LLM_KEEP_ALIVE` qu'il faut monter.

  Le DÉBIT en jetons par seconde. C'est la seule mesure qui prédit vraiment
  la durée d'un tour. En dessous de 15 jetons/s, le modèle est trop gros pour
  la carte et déborde sur la mémoire système.

  Le TEMPS DE PROMPT. Si lire le contexte coûte plus cher que d'écrire la
  réponse, c'est `LLM_NUM_CTX` ou `BUDGET_LORE` qu'il faut baisser.

Le script n'écrit rien : il regarde, il mesure, il conseille.
"""
from __future__ import annotations

import statistics
import sys
import time

import httpx

from app.config import settings
from app.llm.prompts import ARBITRE, CONSEQUENCES, NARRATEUR
from app.llm.schemas import CONSEQUENCES as SCHEMA_CONSEQUENCES
from app.llm.schemas import INTENT

V, R, J, G, Z = "\033[32m", "\033[31m", "\033[33m", "\033[90m", "\033[0m"

# Un contexte de taille réaliste. Mesurer sur « bonjour » ne dit rien : c'est
# la longueur du prompt qui fait le coût d'un tour.
REMPLISSAGE = (
    "### DOSSIER DU MONDE\nVillage Caché de la Feuille, an 0. Les clans "
    "majeurs présents sont les Uchiha, les Hyûga, les Nara, les Akimichi et "
    "les Yamanaka. Le quartier Uchiha est à l'écart du centre, ce qui se voit "
    "et ne se dit pas. Techniques visibles ici : Kawarimi, Bunshin, Henge, "
    "Konoha Senpûu. Matériel courant : kunai, shuriken, parchemin explosif, "
    "fil d'acier, bombe fumigène.\n\n"
) * 6

SCENE = (
    "Hiroshi te regarde sans se presser, comme s'il avait déjà décidé. « Tu es "
    "en retard », dit-il, et il ne lève pas la voix. Derrière lui, Mio range "
    "ses kunai un par un, trop lentement pour que ce soit innocent. La cour de "
    "l'Académie sent la craie et la poussière chaude. Quelque part au-dessus "
    "du mur, un corbeau s'envole et personne ne le regarde."
)


def _chrono(fn):
    depart = time.perf_counter()
    try:
        sortie = fn()
    except Exception as exc:  # noqa: BLE001 — on rend la panne, on ne la masque pas
        return None, time.perf_counter() - depart, exc
    return sortie, time.perf_counter() - depart, None


def _appel(client: httpx.Client, system: str, user: str, *, fmt=None,
           max_tokens: int = 400) -> dict:
    """Un appel brut, pour lire les compteurs qu'Ollama renvoie."""
    options = {"temperature": settings.llm_temperature_json if fmt
               else settings.llm_temperature,
               "num_ctx": settings.llm_num_ctx,
               "num_predict": max_tokens}
    if not fmt:
        options.update({"min_p": settings.llm_min_p,
                        "repeat_penalty": settings.llm_repetition,
                        "repeat_last_n": settings.llm_repetition_fenetre})
    charge = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False, "keep_alive": settings.llm_keep_alive,
        "options": options,
    }
    if fmt:
        charge["format"] = fmt
    if not settings.llm_reflexion:
        charge["think"] = False
    r = client.post(f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                    json=charge)
    if r.status_code == 400 and "think" in charge:
        charge.pop("think")
        r = client.post(f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                        json=charge)
    r.raise_for_status()
    return r.json()


def _secondes(nanos: int | None) -> float:
    return (nanos or 0) / 1e9


def main() -> int:
    if settings.llm_provider != "ollama":
        print(f"{J}LLM_PROVIDER vaut « {settings.llm_provider} » : rien à "
              f"calibrer. Passe à « ollama » puis relance.{Z}")
        return 0

    base = settings.ollama_base_url.rstrip("/")
    print(f"\n{G}Modèle : {settings.llm_model} — contexte {settings.llm_num_ctx} "
          f"jetons — keep_alive {settings.llm_keep_alive}{Z}\n")

    client = httpx.Client(timeout=httpx.Timeout(300.0, connect=5.0))

    # --- le service répond-il, et le modèle est-il là ? --------------------
    try:
        tags = client.get(f"{base}/api/tags").json()
    except Exception as exc:  # noqa: BLE001
        print(f"{R}Ollama est injoignable ({exc.__class__.__name__}).{Z}")
        print(f"{G}Démarre-le, puis relance cette commande.{Z}")
        return 1

    installes = [m["name"] for m in tags.get("models", [])]
    if settings.llm_model not in installes:
        print(f"{R}Le modèle {settings.llm_model} n'est pas installé.{Z}")
        print(f"{G}  ollama pull {settings.llm_model}{Z}")
        print(f"{G}Installés : {', '.join(installes) or 'aucun'}{Z}")
        return 1

    racines = {m.split(":")[0] for m in installes}
    embed_ok = settings.embed_model.split(":")[0] in racines

    # --- 1. le chargement --------------------------------------------------
    print("1. Chargement du modèle en VRAM")
    _, duree, err = _chrono(lambda: _appel(client, "Réponds par « oui ».",
                                           "Prêt ?", max_tokens=4))
    if err:
        print(f"  {R}✗ le modèle n'a pas répondu : {err}{Z}")
        return 1
    print(f"  {V}✓{Z} premier appel en {duree:.1f} s")
    if duree > 25:
        print(f"    {J}C'est long. Le modèle déborde probablement sur la "
              f"mémoire système : essaie un modèle plus petit.{Z}")

    # --- 2. les trois appels d'un tour ------------------------------------
    print("\n2. Un tour complet, avec des prompts de taille réelle")
    etapes = [
        ("arbitre    ", ARBITRE,
         f"{REMPLISSAGE}\n### ACTION\nJe mens à mon instructeur sur l'endroit "
         f"où j'étais.", INTENT, 220),
        ("narrateur  ", NARRATEUR,
         f"{REMPLISSAGE}\n### ACTION DU JOUEUR\nJe mens à mon instructeur.\n\n"
         f"### RÉSULTAT MÉCANIQUE\nRÉSULTAT IMPOSÉ : ÉCHEC.\n\nRaconte la "
         f"suite.", None, settings.llm_max_narration),
        ("conséquences", CONSEQUENCES,
         f"{REMPLISSAGE}\n### CE QUI VIENT DE SE PASSER\n{SCENE}",
         SCHEMA_CONSEQUENCES, settings.llm_max_json),
    ]

    total = 0.0
    debits: list[float] = []
    lecture_totale = 0.0
    for nom, systeme, invite, schema, plafond in etapes:
        rep, duree, err = _chrono(
            lambda s=systeme, u=invite, f=schema, p=plafond:
            _appel(client, s, u, fmt=f, max_tokens=p))
        if err:
            print(f"  {R}✗ {nom} : {err}{Z}")
            return 1
        total += duree
        lecture = _secondes(rep.get("prompt_eval_duration"))
        ecriture = _secondes(rep.get("eval_duration"))
        jetons = rep.get("eval_count") or 0
        debit = jetons / ecriture if ecriture else 0
        debits.append(debit)
        lecture_totale += lecture
        print(f"  {V}✓{Z} {nom} {duree:5.1f} s  "
              f"{G}lecture {lecture:4.1f} s · écriture {ecriture:4.1f} s · "
              f"{jetons:4d} jetons · {debit:5.1f} j/s{Z}")

    debit = statistics.median(debits) if debits else 0
    print(f"\n  {V}Un tour ≈ {total:.0f} s{Z}  "
          f"{G}(débit médian {debit:.0f} jetons/s){Z}")

    # --- 3. ce qu'on en conclut -------------------------------------------
    print("\n3. Ce que je changerais")
    conseils: list[tuple[str, str]] = []

    if debit and debit < 15:
        conseils.append((
            f"{R}Le débit est bas ({debit:.0f} j/s).{Z}",
            "Le modèle est trop gros pour ta carte et déborde sur la mémoire "
            "système. Descends d'un cran : LLM_MODEL=qwen3:4b, ou une "
            "quantification plus légère du même modèle."))
    elif debit and debit < 30:
        conseils.append((
            f"{J}Débit correct sans plus ({debit:.0f} j/s).{Z}",
            "Baisse LLM_MAX_NARRATION à 450 : la consigne demande déjà des "
            "scènes courtes, et le plafond n'a pas à être généreux."))

    if lecture_totale > total * 0.45:
        conseils.append((
            f"{J}Lire le contexte coûte {lecture_totale:.0f} s sur "
            f"{total:.0f}.{Z}",
            f"Baisse BUDGET_LORE (1400 → 900) et LLM_NUM_CTX "
            f"({settings.llm_num_ctx} → 6144). Le dossier du monde est ce qui "
            f"pèse le plus, et il se réduit sans perte visible."))

    if total > 45:
        conseils.append((
            f"{J}Un tour dépasse 45 s.{Z}",
            "Garde NARRATION_EN_FLUX=true : le joueur lit pendant que le "
            "modèle écrit, et la même attente se vit deux fois plus courte."))

    if not embed_ok and settings.embeddings:
        conseils.append((
            f"{J}Le rappel par le sens est inactif.{Z}",
            f"ollama pull {settings.embed_model}  (~270 Mo). Sans lui, la "
            f"mémoire longue ne retrouve que les mots exacts."))

    if settings.llm_num_ctx > 8192 and debit and debit < 40:
        conseils.append((
            f"{J}Contexte à {settings.llm_num_ctx} jetons.{Z}",
            "Au-delà de 8192, le coût de lecture grimpe plus vite que le "
            "gain de mémoire. Le budget de contexte du jeu est déjà borné."))

    if not conseils:
        print(f"  {V}Rien. Ta configuration est bonne telle quelle.{Z}")
    for titre, quoi in conseils:
        print(f"  • {titre}\n    {G}{quoi}{Z}")

    print(f"\n{G}Rappel : la durée d'un tour ne dépend pas que du modèle. "
          f"Le jeu fait trois appels par tour — arbitre, narrateur, "
          f"conséquences — et c'est déjà le minimum.{Z}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
