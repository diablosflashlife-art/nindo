"""Le dossier lore : ce que le maître du jeu sait du monde.

Pourquoi ce module existe
-------------------------
`LorePack` charge trente clans, dix lignées, une trentaine de techniques, une
faune, une flore, des minerais, des tensions entre villages et deux siècles
d'histoire. Avant ce module, rien de tout cela n'atteignait le modèle : il
narrait Naruto de mémoire, pas depuis le pack. C'est la différence entre un
MJ qui dit « le clan Uchiha, réputé puissant » et un MJ qui dit « les Uchiha
tiennent la police du village et savent qu'on les y a relégués ».

Le principe
-----------
On n'injecte JAMAIS le pack entier — il ne tiendrait pas, et la moitié serait
hors sujet. On construit un dossier de la SCÈNE : l'époque, le village, les
clans réellement présents, les techniques qu'on peut plausiblement voir ici,
l'équipement que les personnages portent, ce qui vit dans la région.

Le budget est fixe. Les sections sont classées par priorité et les dernières
tombent quand la place manque — jamais l'inverse. Un dossier qui grossit avec
la campagne finirait par manger le contexte de jeu.

Le filtre de divulgation s'applique ici aussi
---------------------------------------------
Ne sortent jamais de ce module : une organisation marquée `secrete` que le
groupe n'a pas découverte, un site d'invocation `secret`, et surtout les
paliers de lignée NON ENCORE ATTEINTS — décrire le Mangekyô à un modèle qui
narre un porteur de Sharingan à un tomoe, c'est lui offrir le spoiler sur un
plateau.

Ce module ne touche pas la base : il ne prend que du lore et des valeurs
simples. C'est ce qui le rend testable sans serveur ni SQL.
"""
from __future__ import annotations

from typing import Any, Iterable

# Rang maximal de technique qu'on peut plausiblement voir selon le grade le
# plus élevé présent dans la scène. Empêche le narrateur de sortir un Chidori
# dans une cour d'Académie.
RANGS_PAR_GRADE: dict[str, list[str]] = {
    "genin": ["E", "D"],
    "chunin": ["E", "D", "C"],
    "jonin": ["E", "D", "C", "B"],
    "commandant_jonin": ["E", "D", "C", "B", "A"],
    "kage": ["E", "D", "C", "B", "A", "S"],
}

ORDRE_GRADE = ["genin", "chunin", "jonin", "commandant_jonin", "kage"]


def _texte(x: Any, defaut: str = "") -> str:
    return (str(x).strip() if x else defaut)


def _court(x: Any, n: int) -> str:
    t = " ".join(_texte(x).split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _attr(obj: Any, nom: str, defaut: Any = None) -> Any:
    """Lit un attribut ou une clé, pour accepter aussi bien un Character
    qu'un dict — c'est ce qui permet de tester ce module sans base."""
    if obj is None:
        return defaut
    if isinstance(obj, dict):
        return obj.get(nom, defaut)
    return getattr(obj, nom, defaut)


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
def _section_epoque(pack, epoque_id: str, annee: int, village_ref: str) -> str:
    ep = pack.epoque(epoque_id) or {}
    lignes = [f"Année {annee} — {ep.get('nom', epoque_id)}."]

    pays = pack.pays_du_village(village_ref) or {}
    if pays:
        note = _texte(pays.get("note"))
        lignes.append(f"{pays.get('nom', '')}" + (f" — {note}" if note else ""))

    # Les trois derniers événements marquants : ce qui est encore dans toutes
    # les têtes, donc ce à quoi les PNJ font allusion sans l'expliquer.
    passe = pack.histoire(avant=annee)[-3:]
    if passe:
        lignes.append("Événements encore dans les mémoires :")
        for h in passe:
            lignes.append(f"- {h.get('titre')} (an {h.get('annee')}) — "
                          f"{_court(h.get('note'), 130)}")

    tensions = [t for t in pack.tensions_de(village_ref) if t.get("avec")][:3]
    if tensions:
        lignes.append("Relations extérieures :")
        for t in tensions:
            autre = pack.village(t["avec"]) or {}
            nom = autre.get("nom_fr") or autre.get("nom") or t["avec"]
            ton = ("hostilité ouverte" if t["valeur"] <= -40 else
                   "défiance" if t["valeur"] < 0 else
                   "alliance prudente" if t["valeur"] < 40 else "alliance")
            lignes.append(f"- {nom} : {ton} — {_court(t.get('note'), 90)}")
    return "### MONDE ET ÉPOQUE\n" + "\n".join(lignes)


def _section_village(pack, village_ref: str) -> str:
    v = pack.village(village_ref)
    if not v:
        return ""
    lignes = [v.get("nom_fr") or v.get("nom", village_ref)]
    # Le titre du chef DE CE village. Sans lui, le narrateur disait « le
    # Hokage » partout, y compris à Suna et à Oto.
    if v.get("dirigeant"):
        lignes.append(f"Son chef : le {v['dirigeant']}. N'emploie aucun autre titre de Kage pour ce village.")
    if v.get("culture"):
        lignes.append("Ce que le village croit de lui-même :\n"
                      + _court(v["culture"], 300))
    if v.get("parler"):
        lignes.append(f"Manière de parler locale : {_court(v['parler'], 200)}")
    eff = v.get("effectifs") or {}
    if eff:
        lignes.append("Effectifs : "
                      + ", ".join(f"{k} {n}" for k, n in eff.items())
                      + " — utile pour savoir si un visage est connu de tous.")
    return "### VILLAGE\n" + "\n".join(lignes)


def _section_clans(pack, refs: Iterable[str], annee: int) -> str:
    """Les clans réellement présents dans la scène, et eux seuls.

    `tension` est le champ décisif : c'est ce qui donne à un PNJ de clan une
    raison d'être amer, prudent ou arrogant sans qu'on ait à l'écrire.
    """
    blocs = []
    vus: set[str] = set()
    for ref in refs:
        if not ref or ref in vus:
            continue
        clan = pack.clan(ref)
        if not clan or not pack.actif_a(clan, annee):
            continue
        vus.add(ref)
        # `secret` n'entre JAMAIS ici : c'est une amorce pour le moteur, pas
        # une information que le narrateur pourrait laisser transparaître.
        nature = "clan de sang" if clan.get("rang") == "majeur" else "clan d'art"
        surnom = f", « {clan['epithete']} »" if clan.get("epithete") else ""
        l = [f"**{clan.get('nom', ref)}**{surnom} — {nature} "
             f"de {(pack.village(clan.get('village', '')) or {}).get('nom_fr', '?')}"]
        if clan.get("affinite"):
            l.append("Affinité : " + ", ".join(clan["affinite"]))
        if clan.get("histoire"):
            l.append(_court(clan["histoire"], 220))
        if clan.get("tension"):
            l.append(f"Ce qui le travaille : {_court(clan['tension'], 180)}")
        if clan.get("traditions"):
            l.append("Traditions : " + " ; ".join(clan["traditions"][:2]))
        if clan.get("obligations"):
            l.append("Obligations : " + " ; ".join(clan["obligations"][:2]))
        lignee = pack.lignee(clan.get("lignee", "")) if clan.get("lignee") else None
        if lignee:
            l.append(f"Lignée : {lignee.get('nom')} "
                     f"({lignee.get('famille', '')}, transmission "
                     f"{lignee.get('transmission', 'héréditaire')})")
        blocs.append("\n".join(l))
    return "### CLANS PRÉSENTS DANS LA SCÈNE\n" + "\n\n".join(blocs) if blocs else ""


def _section_lignee(pack, lignee_ref: str, palier_atteint: int) -> str:
    """Le palier ATTEINT, et rien au-delà.

    C'est le point le plus sensible du module. Les paliers supérieurs portent
    leurs déclencheurs — « la perte d'un être cher, par sa propre faute ».
    Un modèle qui lit ça arrange la scène pour y arriver.
    """
    lignee = pack.lignee(lignee_ref)
    if not lignee or palier_atteint < 1:
        return ""
    paliers = lignee.get("paliers") or []
    courant = None
    for p in paliers:
        if int(p.get("niveau", 0)) <= palier_atteint:
            courant = p
    if courant is None:
        return ""
    l = [f"**{lignee.get('nom')}** — palier atteint : {courant.get('nom')}.",
         f"Coût en chakra à l'usage : {courant.get('cout_chakra', '?')}."]
    if lignee.get("risques"):
        l.append("Risques connus du porteur : " + ", ".join(lignee["risques"][:2]))
    l.append("N'évoque AUCUN stade supérieur : le porteur ne sait pas qu'il existe.")
    return "### LIGNÉE DU PERSONNAGE\n" + "\n".join(l)


def _section_equipement(pack, inventaire: Iterable[str],
                        village_ref: str, limite: int = 5) -> str:
    """Ce que les personnages portent, avec son emploi tactique.

    Le champ `tactique` du catalogue est écrit pour ce moment précis : il ne
    décrit pas l'objet, il décrit la manœuvre. C'est ce qui fait la différence
    entre « tu attaques » et « tu lances trois shuriken pour le forcer à
    découvert, puis tu tends le fil ».
    """
    vus: dict[str, dict] = {}
    for brut in inventaire or []:
        item = pack.resoudre_nom(str(brut))
        if item is None:
            # « 10 shuriken » -> on retente sans les quantités
            mots = [m for m in str(brut).split() if not m.isdigit()]
            item = pack.resoudre_nom(" ".join(mots)) if mots else None
        if item and item.get("id") not in vus:
            vus[item["id"]] = item

    lignes = []
    for item in list(vus.values())[:limite]:
        nom = item.get("nom", item["id"])
        fr = item.get("fr")
        titre = f"{nom} ({fr})" if fr and fr.lower() != nom.lower() else nom
        detail = _court(item.get("tactique") or item.get("particularite"), 150)
        lignes.append(f"- {titre} : {detail}")

    style = next(iter(pack.styles_armes(village_ref)), None)
    if style and style.get("village") == village_ref:
        lignes.append(f"- École d'armes locale — {style.get('nom')} : "
                      f"{_court(style.get('principe'), 160)}")

    if not lignes:
        return ""
    return ("### ÉQUIPEMENT EN MAIN\n"
            "Sers-t'en nommément. Un ninja résout ses problèmes avec son "
            "matériel autant qu'avec son chakra.\n" + "\n".join(lignes))


def _section_techniques(pack, grade_max: str, clans: Iterable[str],
                        affinites: Iterable[str], limite: int = 9) -> str:
    """Le répertoire plausible ICI, pour que les PNJ emploient de vraies
    techniques au lieu d'en inventer.

    Le narrateur a l'interdiction d'inventer une technique. Sans cette
    section, l'interdiction le condamne à ne décrire que du taijutsu.
    """
    rangs = RANGS_PAR_GRADE.get(grade_max, ["E", "D"])
    clans = {c for c in clans if c}
    aff = {a for a in affinites if a}

    dispo: list[dict] = []
    for t in pack.liste("techniques"):
        if t.get("rang") not in rangs:
            continue
        if t.get("clan") and t["clan"] not in clans:
            continue
        dispo.append(t)

    def score(t: dict) -> tuple:
        return (
            0 if t.get("clan") in clans and t.get("clan") else 1,
            0 if t.get("element") in aff else 1,
            -"ESDCBA".find(t.get("rang", "E")),
        )

    lignes = []
    for t in sorted(dispo, key=score)[:limite]:
        nom = t.get("nom", t["id"])
        fr = t.get("fr")
        titre = f"{nom} ({fr})" if fr else nom
        cout = (t.get("cout") or {}).get("chakra")
        suffixe = f" — chakra {cout}" if cout else ""
        lignes.append(f"- [{t.get('rang')}] {titre} : "
                      f"{_court(t.get('effet'), 110)}{suffixe}")

    if not lignes:
        return ""
    return ("### TECHNIQUES QU'ON PEUT VOIR ICI\n"
            f"Plafond de rang pour cette scène : {', '.join(rangs)}. "
            "N'emploie aucune technique absente de cette liste ou de la fiche "
            "du personnage.\n" + "\n".join(lignes))


def _section_environnement(pack, pays_ref: str, danger: int,
                           limite: int = 5) -> str:
    """Ce qui vit et ce qu'on ramasse dans la région.

    Sert deux choses : meubler un décor autrement vide, et donner au MJ de
    quoi faire surgir un imprévu qui appartient VRAIMENT à cet endroit.
    """
    faune = pack.faune(pays_ref, danger_max=max(2, danger + 2))[:limite]
    flore = pack.flore(pays_ref)[:3]
    mineraux = pack.mineraux(pays_ref)[:2]
    if not (faune or flore or mineraux):
        return ""

    lignes = []
    if faune:
        lignes.append("Faune locale :")
        for f in faune:
            lignes.append(f"- {f.get('nom')} (danger {f.get('danger')}) — "
                          f"{_court(f.get('comportement'), 130)}")
    if flore:
        lignes.append("Flore utile :")
        for f in flore:
            lignes.append(f"- {f.get('nom')} ({f.get('usage')}) — "
                          f"{_court(f.get('effet'), 100)}")
    if mineraux:
        lignes.append("Ressources :")
        for m in mineraux:
            lignes.append(f"- {m.get('nom')} ({m.get('usage')}) — "
                          f"{_court(m.get('effet'), 100)}")
    return "### RÉGION\n" + "\n".join(lignes)


# --------------------------------------------------------------------------
# Assemblage
# --------------------------------------------------------------------------
def dossier(pack, *, village_ref: str, epoque_id: str, annee: int,
            pj: Any = None, pnjs: Iterable[Any] = (), lieu: Any = None,
            lignee_ref: str = "", lignee_palier: int = 0,
            budget_tokens: int = 1500, complet: bool = True) -> str:
    """Construit le dossier lore de la scène, sous budget.

    `complet=False` produit une version réduite — de quoi arbitrer une action
    sans payer le prix du dossier entier. C'est ce qui permet aux appels
    utilitaires du tour de ne pas retraiter quatre mille jetons pour choisir
    entre `taijutsu` et `intelligence`.
    """
    clans = [_attr(pj, "clan_ref") or _attr(pj, "clan")]
    grades = [_attr(pj, "grade", "genin")]
    affinites = list(_attr(pj, "affinites", []) or [])
    inventaire = list(_attr(pj, "inventaire", []) or [])

    for pnj in pnjs or []:
        clans.append(_attr(pnj, "clan_ref") or _attr(pnj, "clan"))
        grades.append(_attr(pnj, "grade", "genin"))

    # Le plafond de rang suit le personnage le plus gradé présent : c'est lui
    # qui décide de ce qui est plausible dans la scène.
    grade_max = max(
        (g for g in grades if g in ORDRE_GRADE),
        key=lambda g: ORDRE_GRADE.index(g), default="genin")

    # Les clans nourrissent aussi les affinités : un Uchiha rend le katon
    # plausible dans la scène même s'il ne l'a pas sur sa fiche.
    for ref in clans:
        clan = pack.clan(ref) if ref else None
        if clan:
            affinites.extend(clan.get("affinite") or [])

    pays = pack.pays_du_village(village_ref) or {}
    danger = int(_attr(lieu, "danger", 2) or 2)

    # (priorité, texte) — les dernières tombent quand le budget est atteint.
    candidats = [
        (1, _section_epoque(pack, epoque_id, annee, village_ref)),
        (2, _section_village(pack, village_ref)),
        (3, _section_clans(pack, clans, annee)),
        (4, _section_lignee(pack, lignee_ref, lignee_palier)),
        (5, _section_equipement(pack, inventaire, village_ref)),
        (6, _section_techniques(pack, grade_max, clans, affinites)),
        (7, _section_environnement(pack, pays.get("id", ""), danger)),
    ]
    if not complet:
        candidats = [(p, t) for p, t in candidats if p in (5, 6)]

    budget_chars = max(600, budget_tokens * 4)
    parts: list[str] = []
    total = 0
    for _, texte in sorted(candidats):
        if not texte:
            continue
        if total + len(texte) > budget_chars and parts:
            break
        parts.append(texte)
        total += len(texte) + 2
    return "\n\n".join(parts)


def taille_estimee(texte: str) -> int:
    return len(texte) // 4
