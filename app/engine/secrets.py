"""Les secrets qui pressent — une question sans horloge n'est pas une intrigue.

CE QUI MANQUAIT. `reveal.py` fait très bien tomber les paliers d'un secret :
à force de côtoyer celui qui le porte, on finit par en voir un bout. Mais un
secret entamé puis laissé de côté restait entamé. Au tour 40, une campagne
traînait quatre questions ouvertes dont aucune ne bougeait, et dont aucune ne
coûtait rien à ignorer. Le joueur n'avait aucune raison d'y revenir.

DEUX HORLOGES, ET ELLES NE FONT PAS LA MÊME CHOSE.

  LE RAPPEL. Au bout de quelques tours sans progrès, le monde y revient de
  lui-même : une remarque, un visage, une porte qu'on referme trop vite. Ce
  n'est PAS une révélation — le palier ne tombe pas. C'est une consigne au
  narrateur, et elle s'épuise : trois relances, puis on lâche. Au-delà, ce
  n'est plus une intrigue qui insiste, c'est le jeu qui harcèle.

  LA FENÊTRE. Au bout de vingt tours sans progrès, il est trop tard. La vérité
  reste en base — elle a toujours existé, c'est tout l'intérêt de la figer à la
  création — mais elle n'est plus atteignable en jeu, et la chronique en garde
  la trace. C'est le seul endroit du jeu où NE RIEN FAIRE a une conséquence
  écrite. Sans ça, la curiosité ne coûte rien et ne rapporte rien.

LE FILTRE DE DIVULGATION TIENT ICI AUSSI. On ne sort d'ici que la `question`
d'un secret DÉJÀ ENTAMÉ (`niveau_revele >= 1`) : le joueur en a vu un bout, la
question est donc à lui. Jamais la `verite`, jamais un secret intact — dont la
question seule suffirait à annoncer qu'il y a quelque chose à trouver.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import Campaign, Character, Event, MemoryFact, Secret
from app.rules.engine import Ruleset


def _cfg(rs: Ruleset) -> dict:
    return rs.data.get("secrets", {}) or {}


def rappel_apres(rs: Ruleset) -> int:
    return int(_cfg(rs).get("rappel_apres", 6))


def rappels_max(rs: Ruleset) -> int:
    return int(_cfg(rs).get("rappels_max", 3))


def fenetre_rappel(rs: Ruleset) -> int:
    return int(_cfg(rs).get("fenetre_rappel", 2))


def fenetre(rs: Ruleset) -> int:
    return int(_cfg(rs).get("fenetre", 20))


def fenetre_deuil(rs: Ruleset) -> int:
    return int(_cfg(rs).get("fenetre_deuil", 3))


def entames(session: Session, camp: Campaign) -> list[Secret]:
    """Les secrets dont le joueur a vu au moins un bout et qui restent ouverts.

    Un secret intact n'est pas là : il n'existe pas encore pour le joueur, et
    l'horloge ne doit pas tourner sur une question qu'il ignore.
    """
    return [s for s in session.exec(select(Secret).where(
        Secret.campaign_id == camp.id)).all()
        if s.niveau_revele >= 1 and not s.resolu and not s.perdu]


def _depuis(camp: Campaign, secret: Secret) -> int:
    """Tours écoulés depuis le dernier progrès.

    `tour_progres` valait zéro sur toutes les campagnes d'avant ce module : on
    retombe alors sur le tour courant plutôt que de déclarer d'un coup vingt
    tours d'inertie et de refermer quatre secrets au premier tour joué.
    """
    if not secret.tour_progres:
        secret.tour_progres = camp.tour
        return 0
    return max(0, camp.tour - secret.tour_progres)


def marquer_progres(secret: Secret, tour: int) -> None:
    """Un palier vient de tomber : les deux horloges repartent de zéro.

    Appelé par `reveal._secrets`. Les relances déjà consommées sont rendues :
    le joueur a montré qu'il cherchait, il a droit à ce que le monde
    l'accompagne de nouveau plus tard.
    """
    secret.tour_progres = tour
    secret.rappels = 0
    secret.rappel_tour = 0


def garder_vivant(secret: Secret, tour: int) -> None:
    """Le porteur est dans la scène : la fenêtre ne court pas. Les relances,
    elles, ne sont pas rendues — ce n'est pas un progrès, c'est une présence."""
    secret.tour_progres = tour


# --------------------------------------------------------------------------
# Ce que le narrateur reçoit
# --------------------------------------------------------------------------
_RELANCES = [
    "Fais revenir cette question d'elle-même, par un détail : une phrase "
    "entendue de travers, un objet mal rangé, un silence trop long. "
    "N'apporte AUCUNE réponse.",
    "Cette question insiste, et cette fois quelqu'un d'autre la pose tout "
    "haut. Montre que le joueur n'est pas seul à s'y intéresser. "
    "N'apporte AUCUNE réponse.",
    "Dernier rappel : rends sensible que le moment de savoir est en train de "
    "passer — une porte qui se ferme, quelqu'un qui part, une trace effacée. "
    "N'apporte AUCUNE réponse.",
]


def pressions(session: Session, camp: Campaign, rs: Ruleset) -> list[str]:
    """Les consignes actives sur les secrets, pour le contexte du narrateur.

    Deux sortes : les relances des secrets qui dorment, et le deuil des
    secrets qui viennent de se refermer. Les deux sont bornées dans le temps —
    une pression permanente n'est plus une pression.
    """
    out: list[str] = []
    vie = fenetre_rappel(rs)

    for s in entames(session, camp):
        # Une relance vit deux tours : c'est une scène à faire arriver, pas une
        # consigne permanente collée au contexte jusqu'au tour vingt.
        if s.rappels <= 0 or camp.tour - (s.rappel_tour or 0) > vie:
            continue
        out.append(f"« {s.question.strip()} » — "
                   + _RELANCES[min(s.rappels, len(_RELANCES)) - 1])

    deuil = fenetre_deuil(rs)
    for s in session.exec(select(Secret).where(
            Secret.campaign_id == camp.id, Secret.perdu == True)).all():  # noqa: E712
        if camp.tour - (s.tour_progres or 0) > deuil:
            continue
        out.append(f"« {s.question.strip()} » — il est TROP TARD pour y "
                   "répondre. Laisse-le sentir sans jamais le dire : ce qui "
                   "pouvait être su ne le sera plus.")
    return out


# --------------------------------------------------------------------------
# L'horloge
# --------------------------------------------------------------------------
def verifier_echeances(session: Session, camp: Campaign, rs: Ruleset,
                       pj: Character | None = None) -> list[str]:
    """Fait tourner les deux horloges. Appelé une fois par tour.

    Retourne des lignes d'effet pour le joueur. Le rappel n'en produit AUCUNE :
    annoncer « une piste se rappelle à toi » ferait le travail que la narration
    doit faire. La fermeture, elle, se dit — c'est une perte, et une perte
    qu'on ne voit pas n'existe pas.
    """
    effets: list[str] = []
    seuil, plafond, limite = rappel_apres(rs), rappels_max(rs), fenetre(rs)

    for s in entames(session, camp):
        inertie = _depuis(camp, s)

        # La fenêtre d'abord : un secret qui se referme n'a pas à être relancé
        # dans le même tour.
        if inertie >= limite:
            s.perdu = True
            s.resolu = True
            s.tour_progres = camp.tour          # date le deuil
            session.add(s)
            sujet = session.get(Character, s.sujet_id) if s.sujet_id else None
            chez = f" autour de {sujet.nom}" if sujet else ""
            session.add(Event(
                campaign_id=camp.id, tour=camp.tour, type="secret_perdu",
                resume=f"Il est trop tard : « {s.question.strip()} » "
                       f"restera sans réponse{chez}.",
                importance=4,
                entites=[i for i in (pj.id if pj else None, s.sujet_id) if i]))
            session.add(MemoryFact(
                campaign_id=camp.id, tour=camp.tour, nature="fait",
                importance=4,
                texte=f"Au tour {camp.tour}, la question « {s.question.strip()} » "
                      f"a cessé d'avoir une réponse accessible : personne n'a "
                      f"cherché à temps.",
                entites=[i for i in (pj.id if pj else None, s.sujet_id) if i]))
            effets.append(f"Une piste s'est refermée : « {s.question.strip()} »")
            continue

        # Puis le rappel. Les relances sont ESPACÉES d'un seuil chacune — 6, 12
        # puis 18 tours d'inertie — et non lâchées trois tours de suite.
        if s.rappels < plafond and inertie >= seuil * (s.rappels + 1):
            s.rappels += 1
            s.rappel_tour = camp.tour
            session.add(s)

    return effets
