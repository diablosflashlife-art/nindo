"""Les fils du récit — une question posée est une dette.

LE DÉFAUT MESURÉ. Sur une partie réelle de cinquante tours, le narrateur a
ouvert une énigme presque à chaque scène — une fissure, un fil, une poulie, des
chiffres « 12-7-3 », puis « 6-1-8 », une main squelettique — et n'en a résolu
aucune. Intrigant au tour cinq, frustrant au tour trente ; l'épilogue parlait
surtout de « questions sans réponse ». Un modèle de langage ouvre volontiers :
ouvrir ne coûte rien. Refermer demande de savoir ce qu'on a promis.

CE QUE FAIT CE MODULE.
1. Il TIENT LE COMPTE. Chaque tour, l'extraction des conséquences dit si la
   scène a posé une question nouvelle, et si elle a répondu à une question
   ouverte. Les deux sont écrites ici.
2. Il BORNE. Au-delà de trois fils ouverts, le narrateur a interdiction d'en
   ouvrir un autre : il doit faire avancer ceux qui existent.
3. Il EXIGE LE PAIEMENT. Un fil ouvert depuis huit tours doit recevoir une vraie
   réponse — pas un indice de plus — dans la scène qui vient.
4. Il DONNE UNE FORME À L'ARC. Une mission acceptée depuis huit tours monte
   vers son dénouement ; depuis douze, elle doit se conclure. C'est ce qui fait
   qu'une première campagne a un premier chapitre, et pas seulement un début.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.models import Campaign, Fil, MemoryFact, Quest

OUVERTS_MAX = 3          # au-delà, plus aucun mystère nouveau
AGE_REPONSE = 8          # un fil qui attend depuis 8 tours doit être payé
ARC_MONTEE = 8           # une mission engagée depuis 8 tours monte vers sa fin
ARC_DENOUEMENT = 12      # depuis 12, elle se conclut


def ouverts(session: Session, camp: Campaign) -> list[Fil]:
    return list(session.exec(select(Fil).where(
        Fil.campaign_id == camp.id, Fil.statut == "ouvert")
        .order_by(Fil.ouvert_au_tour)).all())


def resolus(session: Session, camp: Campaign) -> list[Fil]:
    return list(session.exec(select(Fil).where(
        Fil.campaign_id == camp.id, Fil.statut == "resolu")
        .order_by(Fil.resolu_au_tour)).all())


# --------------------------------------------------------------------------
# Ce que l'extraction des conséquences reçoit, et ce qu'elle rend
# --------------------------------------------------------------------------
def liste_numerotee(session: Session, camp: Campaign) -> str:
    """Les fils ouverts, numérotés, pour que l'extraction puisse dire lequel
    la scène vient de résoudre."""
    fils = ouverts(session, camp)
    if not fils:
        return "### FILS OUVERTS\n(aucun)"
    return "### FILS OUVERTS\n" + "\n".join(
        f"{i}. {f.question}" for i, f in enumerate(fils))


def appliquer(session: Session, camp: Campaign, net: dict) -> list[str]:
    """Écrit les fils résolus et le fil nouveau. Retourne les effets à montrer :
    une réponse obtenue se dit — c'est la récompense du joueur curieux."""
    effets: list[str] = []
    fils = ouverts(session, camp)

    for r in net.get("mysteres_resolus") or []:
        i = r.get("numero")
        if not isinstance(i, int) or not 0 <= i < len(fils) or fils[i].statut != "ouvert":
            continue
        f = fils[i]
        f.statut = "resolu"
        f.reponse = (r.get("reponse") or "").strip()[:300]
        f.resolu_au_tour = camp.tour
        session.add(f)
        session.add(MemoryFact(
            campaign_id=camp.id, tour=camp.tour, nature="fait", importance=4,
            texte=f"Réponse obtenue à « {f.question} » : {f.reponse}"))
        effets.append(f"Tu sais maintenant : {f.question} — {f.reponse}")

    nouveau = (net.get("mystere_nouveau") or "").strip()
    encore = [f for f in fils if f.statut == "ouvert"]
    if nouveau and len(encore) < OUVERTS_MAX + 1:
        deja = {f.question.lower() for f in encore}
        if nouveau.lower() not in deja:
            session.add(Fil(campaign_id=camp.id, question=nouveau[:200],
                            ouvert_au_tour=camp.tour))
    return effets


# --------------------------------------------------------------------------
# Ce que le narrateur reçoit
# --------------------------------------------------------------------------
def mission_engagee(session: Session, camp: Campaign) -> Quest | None:
    return session.exec(select(Quest).where(
        Quest.campaign_id == camp.id,
        Quest.statut.in_(["acceptée", "en cours"]))
        .order_by(Quest.debut_tour)).first()


def consigne(session: Session, camp: Campaign) -> str:
    """Les fils ouverts et ce qu'il faut en faire dans CETTE scène.

    Placée dans le contexte du narrateur ; elle passe avant la « sortie » du
    registre de style, qui invite parfois à finir sur une énigme.
    """
    lignes: list[str] = []
    fils = ouverts(session, camp)
    if fils:
        lignes.append("### LES QUESTIONS QUE LE RÉCIT DOIT AU JOUEUR")
        for f in fils:
            age = camp.tour - f.ouvert_au_tour
            lignes.append(f"- « {f.question} » (posée il y a {age} tour{'s' if age > 1 else ''})")
        vieux = fils[0]
        if camp.tour - vieux.ouvert_au_tour >= AGE_REPONSE:
            lignes.append(
                f"CETTE SCÈNE RÉPOND à « {vieux.question} ». Une vraie réponse, "
                "claire, que le joueur pourra répéter après coup — pas un indice "
                "de plus, pas une nouvelle question. Elle peut en ouvrir une "
                "autre plus tard ; celle-ci se referme maintenant.")
        if len(fils) >= OUVERTS_MAX:
            lignes.append(
                "N'OUVRE AUCUN NOUVEAU MYSTÈRE : pas d'objet inexpliqué, pas de "
                "silhouette, pas de message codé. Fais avancer les questions "
                "ci-dessus. Cette règle passe avant la « sortie » du registre.")

    quete = mission_engagee(session, camp)
    if quete and quete.debut_tour is not None:
        age = camp.tour - quete.debut_tour
        if age >= ARC_DENOUEMENT:
            lignes.append(
                f"### DÉNOUEMENT — « {quete.titre} »\nLa mission est engagée "
                f"depuis {age} tours. Elle se conclut DANS CETTE SCÈNE : succès "
                "ou échec, mais un résultat net, que l'équipe peut rapporter. "
                "Les fils qui y sont liés reçoivent leur réponse.")
        elif age >= ARC_MONTEE:
            lignes.append(
                f"### L'ARC MONTE — « {quete.titre} »\nLes pièces se "
                "rapprochent. Fais converger la scène vers une confrontation ou "
                "une découverte décisive : le dénouement doit arriver dans les "
                "trois prochaines scènes.")
    return "\n".join(lignes)
