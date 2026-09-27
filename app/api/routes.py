"""Routes HTTP.

Aucune logique de jeu ici : ces fonctions lisent une requête, appellent le
moteur, et rendent un gabarit. Toute tentation de calcul appartient à
`app/rules/` ou `app/engine/`.
"""
from __future__ import annotations

import json
import random
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import ROOT, settings
from app.db import engine, get_session
from app.engine import apprentissage
from app.engine import campaign as amorce
from app.engine import carte
from app.engine import combat as cbt
from app.engine import destiny as dst
from app.engine import epilogue
from app.engine import missions as gen_missions
from app.engine import progression
from app.engine import reprise
from app.engine.creation import ErreurCreation, Fiche, apercu_stats, creer_personnage
from app.engine.turn import conclure, jouer, preparer
from app.engine.verrou import CampagneOccupee, tour_exclusif
from app.engine.voix import plan_de_lecture
from app.llm.provider import conteur, get_llm
from app.engine import fils as fils_du_recit
from app.lore.pack import charger as charger_pack
from app.models import (Campaign, Character, CharacterTechnique, Destiny,
                        DestinyTrait, Encounter, Event, Location, Quest,
                        Relation, Secret, Summary, Turn)
from app.rules.engine import Ruleset
from app.rules.loader import charger as charger_ruleset
from app.rules.loader import charger_pour
from app.rules.loader import lister as lister_rulesets

router = APIRouter()
templates = Jinja2Templates(directory=str(ROOT / "app" / "web" / "templates"))

# --------------------------------------------------------------------------
# Emblèmes fournis par toi
# --------------------------------------------------------------------------
# Les sceaux dessinés dans `_emblemes.html` sont des formes de notre cru. Si tu
# déposes une image nommée d'après l'identifiant — app/web/static/emblemes/
# konoha.svg, uchiha.png, suna.webp… — l'interface l'utilise à la place, partout
# où ce sceau apparaît (cartes, fiche, table). Rien d'autre à modifier.
EMBLEMES = ROOT / "app" / "web" / "static" / "emblemes"
_EXT = (".svg", ".png", ".webp", ".jpg", ".jpeg", ".gif")


def embleme_fichier(cle: str) -> str | None:
    """URL de l'image déposée pour cette clé, ou None si tu n'en as pas mis."""
    if not cle:
        return None
    for ext in _EXT:
        chemin = EMBLEMES / f"{cle}{ext}"
        if chemin.is_file():
            # l'horodatage casse le cache du navigateur quand tu remplaces l'image
            return f"/static/emblemes/{cle}{ext}?v={int(chemin.stat().st_mtime)}"
    return None


templates.env.globals["embleme_fichier"] = embleme_fichier

STATIQUE = ROOT / "app" / "web" / "static"


def statique(chemin: str) -> str:
    """URL d'un fichier statique, horodatée : sans elle, un navigateur garde
    l'ancienne feuille de style après une mise à jour du jeu."""
    fichier = STATIQUE / chemin
    v = int(fichier.stat().st_mtime) if fichier.is_file() else 0
    return f"/static/{chemin}?v={v}"


templates.env.globals["statique"] = statique
templates.env.globals["conteur"] = conteur

# Le nom du jeu, défini UNE fois : barre, titres d'onglet, accueil.
JEU = {"nom": "Nindō", "kanji": "忍道", "sens": "la voie du ninja",
       "slogan": "Devenir un ninja n’a jamais été aussi simple."}
templates.env.globals["jeu"] = JEU

# Destinées proposées, gardées le temps de l'écran de choix
_PROPOSITIONS: dict[str, list[dst.DestineeTiree]] = {}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _camp(session: Session, cid: int) -> Campaign:
    c = session.get(Campaign, cid)
    if not c:
        raise HTTPException(404, "Campagne introuvable")
    return c


def _regles(camp: Campaign) -> Ruleset:
    """Les règles DE LA PARTIE, copiées en base : modifier le YAML plus tard ne
    change pas rétroactivement une campagne en cours.

    Seule exception, et elle est nécessaire : ce qui MANQUE entièrement à la
    copie est complété depuis le fichier. Une partie commencée avant l'arrivée
    du moteur de combat n'a ni postures, ni seuils de blessure — ses joueurs ne
    pourraient pas se battre du tout. Voir `loader.completer` : on ajoute ce
    qui est absent, on ne remplace jamais ce qui existe.
    """
    return charger_pour(camp.ruleset, camp.ruleset_slug)


def _exige_en_cours(camp: Campaign) -> None:
    """Rien ne s'écrit dans une chronique close.

    Le garde est ici plutôt que dans le moteur parce que les routes sont la
    seule porte d'entrée : une vérification, et elle couvre tout ce qui fait
    avancer l'horloge. On rouvre en un clic depuis la chronique.
    """
    if camp.phase == "terminee":
        raise HTTPException(
            409, "Cette chronique est close. Rouvre-la depuis la chronique "
                 "si tu veux jouer une scène de plus.")


def _pjs(session: Session, cid: int) -> list[Character]:
    return list(session.exec(select(Character).where(
        Character.campaign_id == cid, Character.is_pc == True)  # noqa: E712
        .order_by(Character.id)).all())


def _pj_actif(session: Session, cid: int, demande: int | None) -> Character | None:
    pjs = _pjs(session, cid)
    if not pjs:
        return None
    if demande is not None:
        choisi = next((p for p in pjs if p.id == demande), None)
        if choisi:
            return choisi
    return pjs[0]


# --------------------------------------------------------------------------
# Accueil
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def accueil(request: Request, session: Session = Depends(get_session)):
    camps = session.exec(select(Campaign).order_by(Campaign.joue_le.desc())).all()
    lignes = []
    for c in camps:
        lignes.append({"c": c, "pjs": _pjs(session, c.id)})

    # Les paysages à déclarer pour cette page. Chaque décor n'est dessiné
    # qu'une fois et les cartes n'en tiennent qu'une référence : avec une base
    # de trente-six campagnes, les redessiner coûtait 339 Ko et deux secondes
    # et demie de rendu.
    pays = sorted({(l["pjs"][0].village_ref or "konoha") if l["pjs"] else "konoha"
                   for l in lignes})

    return templates.TemplateResponse("accueil.html", {
        "request": request, "campagnes": lignes, "rulesets": lister_rulesets(),
        "pack": charger_pack("naruto"), "paysages_utilises": pays,
    })


@router.post("/campagnes/{cid}/supprimer")
def supprimer_campagne(cid: int, session: Session = Depends(get_session)):
    """Efface une campagne et TOUT ce qui s'y rattache.

    Il n'y avait aucun moyen de faire le ménage : chaque essai, chaque partie
    abandonnée restait sur l'accueil pour toujours. On parcourt toutes les
    tables qui portent un `campaign_id` plutôt que d'en tenir la liste à la
    main — une table ajoutée demain sera nettoyée sans qu'on y pense.
    """
    camp = _camp(session, cid)
    from sqlmodel import SQLModel, delete

    for table in SQLModel.metadata.sorted_tables:
        if "campaign_id" in table.c:
            session.exec(delete(table).where(table.c.campaign_id == cid))
    session.delete(camp)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/campagnes")
def creer_campagne(nom: str = Form(...), epoque: str = Form("naruto_p1"),
                   ton: str = Form(""), allure: str = Form("court"),
                   session: Session = Depends(get_session)):
    rs = charger_ruleset("naruto")
    camp = Campaign(
        nom=nom.strip() or "Nouvelle campagne", epoque=epoque,
        allure=allure if allure in ("court", "normal", "long") else "court",
        ton=ton.strip() or "shonen sombre, tension montante, conséquences durables",
        graine=random.randrange(1, 10**9), ruleset=rs.data, phase="creation")
    session.add(camp)
    session.commit()
    session.refresh(camp)
    return RedirectResponse(f"/campagnes/{camp.id}/creation", status_code=303)


# --------------------------------------------------------------------------
# Création de personnage
# --------------------------------------------------------------------------
def _ctx_creation(request: Request, camp: Campaign, rs: Ruleset, erreur: str = "",
                  saisie: dict | None = None) -> dict:
    pack = charger_pack(camp.lore_pack)
    annee = pack.annee_de(camp.epoque)
    return {
        "request": request, "camp": camp, "rs": rs, "pack": pack,
        "villages": [v for v in pack.villages(annee) if v.get("rang") == "majeur"],
        "clans_majeurs": pack.clans(rang="majeur", annee=annee),
        "clans_mineurs": pack.clans(rang="mineur", annee=annee),
        "lignees": {l["id"]: l for l in pack.liste("lignees")},
        "origines": rs.origines, "specialisations": rs.specialisations,
        "stats_cfg": rs.stats, "groupes": rs.data.get("groupes_stats", {}),
        "erreur": erreur, "saisie": saisie or {},
    }


@router.get("/campagnes/{cid}/creation", response_class=HTMLResponse)
def page_creation(cid: int, request: Request, session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    return templates.TemplateResponse(
        "creation.html", _ctx_creation(request, camp, _regles(camp)))


@router.post("/campagnes/{cid}/creation", response_class=HTMLResponse)
async def soumettre_creation(cid: int, request: Request,
                             session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    rs = _regles(camp)
    pack = charger_pack(camp.lore_pack)
    annee = pack.annee_de(camp.epoque)
    form = await request.form()

    origine = form.get("origine", "sans_clan")
    clan_id = form.get(f"clan_{origine}", "") or form.get("clan", "")
    stats_libres = {}
    for cle in rs.stats:
        brut = (form.get(f"pt_{cle}") or "0").strip()
        if brut.isdigit() and int(brut):
            stats_libres[cle] = int(brut)

    age_brut = (form.get("age") or "").strip()
    fiche = Fiche(
        nom=form.get("nom", ""), sexe=form.get("sexe", ""),
        age=int(age_brut) if age_brut.isdigit() else None,
        apparence=form.get("apparence", ""), joueur=form.get("joueur", ""),
        village_id=form.get("village", "konoha"),
        origine=origine, clan_id=clan_id,
        clan_invente={
            "nom": form.get("clan_nom", ""),
            "tradition": form.get("clan_tradition", ""),
            "bonus": {k: int(v) for k, v in (
                (form.get("clan_bonus1", ""), form.get("clan_bonus1_val", "0")),
                (form.get("clan_bonus2", ""), form.get("clan_bonus2_val", "0")),
            ) if k and str(v).isdigit() and int(v)},
        },
        specialisation=form.get("specialisation", "ninjutsu"),
        stats_libres=stats_libres,
        mode_destinee=form.get("mode_destinee", "proposee"),
    )

    # « Je m'en remets au destin » : village, origine, clan, voie et points sont
    # tirés avant validation. Le joueur n'a rempli que son nom et son visage.
    if form.get("chemin") == "remets":
        from app.engine.creation import tirer_fiche
        fiche = tirer_fiche(pack, rs, fiche, annee,
                            graine=camp.graine + len(fiche.nom))

    try:
        from app.engine.creation import valider
        valider(pack, rs, fiche, annee)
    except ErreurCreation as exc:
        return templates.TemplateResponse(
            "creation.html",
            _ctx_creation(request, camp, rs, str(exc), dict(form)),
            status_code=400)

    # Mode « proposée » : on génère trois avenirs distincts et on laisse choisir
    if fiche.mode_destinee == "proposee":
        props = dst.proposer(
            pack, rs, nombre=int(rs.destinee.get("propositions", 3)),
            origine=fiche.origine, clan_id=fiche.clan_id,
            village_id=fiche.village_id, specialisation=fiche.specialisation,
            annee=annee, graine=camp.graine)
        cle = f"{cid}:{fiche.nom}"
        _PROPOSITIONS[cle] = props
        request.session_fiche = fiche  # noqa: SLF001 (non utilisé, gardé lisible)
        return templates.TemplateResponse("destinee.html", {
            "request": request, "camp": camp, "fiche": fiche, "propositions": props,
            "form": dict(form), "cle": cle,
        })

    graine = camp.graine if fiche.mode_destinee == "aleatoire" else camp.graine + 1
    # L'archétype tiré impose ses traits latents : c'est ce qui fait qu'un
    # « réceptacle » en est un, et pas une étiquette sur un genin ordinaire.
    from app.engine.creation import traits_de_l_archetype
    arch = pack.archetype(fiche.archetype) or {}
    forces = traits_de_l_archetype(pack, rs, fiche, random.Random(graine)) \
        if fiche.archetype else []
    destinee = dst.generer(
        pack, rs, origine=fiche.origine, clan_id=fiche.clan_id,
        village_id=fiche.village_id, specialisation=fiche.specialisation,
        annee=annee, graine=graine, traits_forces=forces,
        profil_force=arch.get("profil"))
    return _finaliser(request, session, camp, pack, rs, fiche, destinee)


@router.post("/campagnes/{cid}/destinee", response_class=HTMLResponse)
async def choisir_destinee(cid: int, request: Request,
                           session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    rs = _regles(camp)
    pack = charger_pack(camp.lore_pack)
    form = await request.form()

    cle = form.get("cle", "")
    index = int(form.get("choix", "0") or 0)
    props = _PROPOSITIONS.get(cle) or []
    if not props:
        return RedirectResponse(f"/campagnes/{cid}/creation", status_code=303)
    destinee = props[min(index, len(props) - 1)]

    stats_libres = {}
    for cle_stat in rs.stats:
        brut = (form.get(f"pt_{cle_stat}") or "0").strip()
        if brut.isdigit() and int(brut):
            stats_libres[cle_stat] = int(brut)
    age_brut = (form.get("age") or "").strip()
    origine = form.get("origine", "sans_clan")
    fiche = Fiche(
        nom=form.get("nom", ""), sexe=form.get("sexe", ""),
        age=int(age_brut) if age_brut.isdigit() else None,
        apparence=form.get("apparence", ""), joueur=form.get("joueur", ""),
        village_id=form.get("village", "konoha"), origine=origine,
        clan_id=form.get(f"clan_{origine}", "") or form.get("clan", ""),
        clan_invente={"nom": form.get("clan_nom", "")},
        specialisation=form.get("specialisation", "ninjutsu"),
        stats_libres=stats_libres, mode_destinee="proposee")

    _PROPOSITIONS.pop(cle, None)
    return _finaliser(request, session, camp, pack, rs, fiche, destinee)


def _finaliser(request: Request, session: Session, camp: Campaign, pack,
               rs: Ruleset, fiche: Fiche, destinee):
    """Scelle le personnage.

    DEUX CHEMINS, ET C'EST VOULU. Un joueur qui rejoint une campagne déjà
    ouverte n'attend qu'une création de fiche : on la fait et on le renvoie à
    la table. Le PREMIER joueur, lui, déclenche l'amorce du monde entier —
    instanciation, génération de l'entourage, scène d'ouverture — soit une à
    trois minutes de modèle. Le laisser devant une page figée pendant ce
    temps-là était le pire écart du projet : c'est le moment le plus chargé de
    sens de toute la campagne, et il ressemblait à un plantage.

    On lui rend donc immédiatement la page du sceau, qui ouvre un flux et
    montre le monde se faire — voir `flux_sceau`.
    """
    premier = not _pjs(session, camp.id)
    if not premier:
        pj = creer_personnage(session, camp, pack, rs, fiche, destinee)
        return RedirectResponse(f"/campagnes/{camp.id}?pj={pj.id}",
                                status_code=303)

    _purger_jetons()
    jeton = secrets.token_urlsafe(12)
    _EN_ATTENTE[jeton] = {"cid": camp.id, "sceau": True, "fiche": fiche,
                          "destinee": destinee, "depuis": time.monotonic()}
    return templates.TemplateResponse("sceau.html", {
        "request": request, "camp": camp, "fiche": fiche, "jeton": jeton})


# --------------------------------------------------------------------------
# Table de jeu
# --------------------------------------------------------------------------
@router.get("/campagnes/{cid}", response_class=HTMLResponse)
def table(cid: int, request: Request, pj: int | None = None,
          session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    pjs = _pjs(session, cid)
    if not pjs:
        return RedirectResponse(f"/campagnes/{cid}/creation", status_code=303)
    actif = _pj_actif(session, cid, pj)
    rs = _regles(camp)
    pack = charger_pack(camp.lore_pack)

    tours = session.exec(select(Turn).where(
        Turn.campaign_id == cid).order_by(Turn.index)).all()
    noms = {c.id: c.nom for c in session.exec(select(Character).where(
        Character.campaign_id == cid)).all()}

    relations = []
    for rel in session.exec(select(Relation).where(
            Relation.campaign_id == cid, Relation.cible_id == actif.id)).all():
        src = session.get(Character, rel.source_id)
        if src:
            relations.append({"nom": src.nom, "valeur": rel.valeur,
                              "nature": rel.nature, "pj": src.is_pc,
                              "role": src.role_campagne})
    relations.sort(key=lambda r: -abs(r["valeur"]))

    return templates.TemplateResponse("table.html", {
        "request": request, "camp": camp, "pj": actif, "pjs": pjs, "noms": noms,
        "tours": tours, "rs": rs, "pack": pack,
        "stats_cfg": rs.stats, "res_cfg": rs.data.get("resources", {}),
        "titre_grade": rs.titre_grade(actif.grade),
        "tier_label": rs.tier_label(actif.tier),
        "xp_requis": rs.xp_pour_niveau(actif.niveau),
        "lieu": session.get(Location, actif.location_id) if actif.location_id else None,
        "relations": relations,
        # Les offres expirées (jamais prises) n'encombrent pas le panneau.
        "quetes": session.exec(select(Quest).where(
            Quest.campaign_id == cid, Quest.statut != "expirée")).all(),
        "techniques": _techniques(session, actif, pack, rs),
        "evenements": session.exec(select(Event).where(
            Event.campaign_id == cid).order_by(Event.tour.desc()).limit(15)).all(),
        "presage": _presage(session, actif),
        "dernier": tours[-1] if tours else None,
        "flux": settings.narration_en_flux,
        "offres_progression": progression.offres(rs, actif),
        "offres_apprentissage": apprentissage.offres(session, camp, pack, rs, actif),
        # Ce que le joueur cherche à savoir, et ce qu'il a appris : c'est la
        # preuve, sous ses yeux, que le récit lui répond.
        "fils_ouverts": fils_du_recit.ouverts(session, camp),
        "fils_resolus": fils_du_recit.resolus(session, camp)[-5:],
        # Le rappel de reprise, version DÉTERMINISTE : quelques requêtes, pas
        # de modèle, donc pas d'écran d'attente. Le gabarit demande ensuite la
        # version racontée à HTMX, qui la substitue quand elle arrive.
        "rappel": (reprise.rappeler(session, camp, actif, rs, ecrire=False)
                   if reprise.necessaire(session, camp) else None),
        "carte": carte.itineraire(session, camp, rs, actif),
        **_ctx_rencontre(session, camp, rs, actif),
    })


def _ctx_rencontre(session: Session, camp: Campaign, rs: Ruleset,
                   pj: Character) -> dict:
    """Ce que la table doit montrer d'un affrontement en cours.

    L'état des adversaires est donné en CLAIR, pas en points de vie : le joueur
    doit lire une scène, pas jouer contre une barre. La jauge reste là pour
    qu'il sente la progression sans la compter.
    """
    renc = cbt.active(session, camp)
    if renc is None:
        return {"rencontre": None, "postures": [], "leviers": []}

    ennemis = []
    for c in cbt.adverses(session, renc):
        maximum = cbt.pv_max(session, rs, c)
        pct = int(int(c.ressources.get("pv", 1)) * 100 / maximum)
        ennemis.append({
            "nom": c.nom, "pct": max(2, min(100, pct)), "tier": c.tier,
            "tier_label": rs.tier_label(c.tier),
            "etat": ("intact" if pct > 80 else "entamé" if pct > 60
                     else "blessé" if pct > 35 else "il tient à peine"),
        })

    acquis = set(renc.leviers)
    return {
        "rencontre": renc,
        "ennemis": ennemis,
        "hors_combat": [c.nom for c in cbt.tombes(session, renc)],
        "allies_au_combat": [c.nom for c in cbt.allies(session, renc, pj)],
        "postures": cbt.postures_offertes(rs),
        "leviers": [{"code": c, "libelle": v.get("libelle", c),
                     "acquis": c in acquis}
                    for c, v in rs.leviers().items()],
        "leviers_acquis": [(rs.levier(c) or {}).get("libelle", c)
                           for c in renc.leviers],
        "progres_requis": int(rs.combat.get("objectif_progres_requis", 3)),
        "echanges_max": int(rs.combat.get("echanges_max", 12)),
    }


@router.post("/campagnes/{cid}/jouer", response_class=HTMLResponse)
def action(cid: int, request: Request, action: str = Form(...),
           character_id: int | None = Form(None),
           posture: str = Form(""), levier: str = Form(""),
           session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    texte = action.strip()
    if not texte:
        raise HTTPException(400, "Action vide")

    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    try:
        # Un seul tour s'écrit à la fois : deux requêtes simultanées
        # incrémentaient toutes les deux `camp.tour` sur la même valeur lue.
        with tour_exclusif(cid):
            tour = jouer(session, camp, pj, texte, rs, pack,
                         posture=posture, levier=levier)
    except CampagneOccupee as exc:
        return templates.TemplateResponse("_erreur.html", {
            "request": request, "message": str(exc)}, status_code=409)
    except httpx.HTTPError as exc:
        session.rollback()
        return templates.TemplateResponse("_erreur.html", {
            "request": request,
            "message": f"Aucun conteur n'a répondu ({exc.__class__.__name__}). "
                       "Ouvre /sante pour savoir quoi faire."}, status_code=503)

    noms = {c.id: c.nom for c in session.exec(select(Character).where(
        Character.campaign_id == cid)).all()}
    # Le panneau de combat est renvoyé avec le tour (hx-swap-oob) : sans ça, le
    # joueur devait recharger la page pour voir l'état des adversaires changer.
    return templates.TemplateResponse("_tour.html", {
        "request": request, "tour": tour, "noms": noms, "camp": camp, "pj": pj,
        "rs": rs, "oob": True,
        "res_cfg": rs.data.get("resources", {}),
        "xp_requis": rs.xp_pour_niveau(pj.niveau),
        "offres_progression": progression.offres(rs, pj),
        **_ctx_rencontre(session, camp, rs, pj)})


# --------------------------------------------------------------------------
# Narration en flux
# --------------------------------------------------------------------------
# Une action en attente, le temps que le navigateur ouvre le flux. En mémoire
# et jamais en base : c'est de l'état de requête, pas de l'état de partie, et
# il ne doit survivre ni à un redémarrage ni à une reprise de sauvegarde.
_EN_ATTENTE: dict[str, dict] = {}
_DELAI_JETON = 120.0          # secondes avant péremption


def _purger_jetons() -> None:
    limite = time.monotonic() - _DELAI_JETON
    for cle in [k for k, v in _EN_ATTENTE.items() if v["depuis"] < limite]:
        _EN_ATTENTE.pop(cle, None)


def _sse(evenement: str, donnees: str) -> str:
    """Un événement Server-Sent Events.

    Chaque ligne de la charge doit porter son propre préfixe `data:` — un
    retour à la ligne non préfixé couperait l'événement en deux, ce qui se
    voit immédiatement sur du HTML.
    """
    corps = "\n".join(f"data: {l}" for l in donnees.split("\n"))
    return f"event: {evenement}\n{corps}\n\n"


@router.post("/campagnes/{cid}/jouer/flux", response_class=HTMLResponse)
def jouer_en_flux(cid: int, request: Request, action: str = Form(...),
                  character_id: int | None = Form(None),
                  posture: str = Form(""), levier: str = Form(""),
                  session: Session = Depends(get_session)):
    """Accuse réception de l'action et rend la coquille du tour.

    Rien n'est calculé ici : le but est de répondre en quelques millisecondes
    pour que le joueur voie sa propre phrase s'afficher tout de suite. Le
    travail se fait dans le flux qui suit.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    texte = action.strip()
    if not texte:
        raise HTTPException(400, "Action vide")

    _purger_jetons()
    jeton = secrets.token_urlsafe(12)
    _EN_ATTENTE[jeton] = {"cid": cid, "pj": pj.id, "action": texte,
                          "posture": posture, "levier": levier,
                          "depuis": time.monotonic()}
    return templates.TemplateResponse("_flux.html", {
        "request": request, "camp": camp, "pj": pj, "action": texte,
        "jeton": jeton})


@router.get("/campagnes/{cid}/flux/{jeton}")
def flux(cid: int, request: Request, jeton: str):
    """Le tour, joué en streaming.

    ON OUVRE NOTRE PROPRE SESSION. Une dépendance `get_session` est refermée
    quand la fonction rend sa réponse — c'est-à-dire avant que le générateur
    ait produit le moindre octet. Ici la session doit vivre aussi longtemps que
    le flux.

    Le jeton est à usage unique : un rechargement de page ne rejoue pas le
    tour. C'est la seule protection nécessaire contre le double envoi, et elle
    suffit parce que le jeton n'est connu que du navigateur qui vient d'agir.
    """
    demande = _EN_ATTENTE.pop(jeton, None)
    if demande is None or demande["cid"] != cid:
        raise HTTPException(404, "Action expirée ou déjà jouée")

    def evenements():
        with Session(engine) as session:
            camp = session.get(Campaign, cid)
            pj = session.get(Character, demande["pj"])
            if camp is None or pj is None:
                yield _sse("echec", "Campagne introuvable.")
                return
            rs, pack = _regles(camp), charger_pack(camp.lore_pack)

            # Le verrou tient sur TOUTE la durée du flux : entre la résolution
            # et l'écriture du tour il s'écoule le temps d'une narration, et
            # c'est exactement la fenêtre où une seconde requête ferait des
            # dégâts.
            try:
                verrou = tour_exclusif(cid)
                verrou.__enter__()
            except CampagneOccupee as exc:
                yield _sse("echec", str(exc))
                return
            try:
                yield _sse("etape", "L'arbitre lit ton action…")
                prep = preparer(session, camp, pj, demande["action"], rs, pack,
                                posture=demande["posture"],
                                levier=demande["levier"])

                # Le résultat mécanique est arrêté : on le montre AVANT la
                # narration. Le joueur sait ainsi ce qui lui arrive pendant que
                # la scène s'écrit — et il voit que le dé a précédé le récit.
                yield _sse("jet", templates.get_template("_jet.html").render(
                    resolution=prep.resolution).strip())
                yield _sse("etape", "Le maître du jeu raconte…")

                morceaux = []
                for morceau in get_llm().flux(prep.systeme, prep.invite(), max_tokens=prep.max_tokens):
                    morceaux.append(morceau)
                    yield _sse("mot", morceau.replace("\r", ""))

                narration = "".join(morceaux).strip()
                # Qui a raconté ce tour : l'indicateur de la barre le montre,
                # pour qu'un passage au secours ne soit jamais une surprise.
                yield _sse("conteur", json.dumps(conteur(), ensure_ascii=False))
                yield _sse("etape", "Le monde encaisse les conséquences…")
                tour = conclure(session, camp, pj, prep, narration, rs, pack)

                noms = {c.id: c.nom for c in session.exec(select(Character).where(
                    Character.campaign_id == cid)).all()}
                fin = templates.get_template("_tour.html").render(
                    request=request, tour=tour, noms=noms, camp=camp, pj=pj,
                    rs=rs, oob=True, res_cfg=rs.data.get("resources", {}),
                    xp_requis=rs.xp_pour_niveau(pj.niveau),
                    offres_progression=progression.offres(rs, pj),
                    **_ctx_rencontre(session, camp, rs, pj))
                yield _sse("fin", fin.strip())
            except httpx.HTTPError as exc:
                session.rollback()
                yield _sse("echec",
                           f"Aucun conteur n'a répondu ({exc.__class__.__name__}). "
                           "Ouvre /sante pour savoir quoi faire.")
            except Exception as exc:  # noqa: BLE001 — le joueur doit être prévenu
                session.rollback()
                yield _sse("echec", f"Le tour a échoué : {exc}")
            finally:
                verrou.__exit__(None, None, None)

    return StreamingResponse(evenements(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/campagnes/{cid}/sceau/{jeton}")
def flux_sceau(cid: int, request: Request, jeton: str):
    """L'amorce du monde, jouée en direct.

    Les mêmes appels qu'avant, dans le même ordre — mais chacun est ANNONCÉ
    avant d'être lancé et sa moisson est montrée dès qu'elle tombe. Le présage
    s'écrit sous les yeux du joueur, puis son instructeur, ses coéquipiers et
    son rival se retournent un par un. L'attente n'a pas raccourci ; elle a
    cessé d'être vide.

    Session ouverte à la main, jeton à usage unique : mêmes raisons que pour le
    flux de narration (voir `flux`).
    """
    demande = _EN_ATTENTE.pop(jeton, None)
    if demande is None or demande["cid"] != cid or not demande.get("sceau"):
        raise HTTPException(404, "Scellé expiré ou déjà accompli")

    def evenements():
        with Session(engine) as session:
            camp = session.get(Campaign, cid)
            if camp is None:
                yield _sse("echec", "Campagne introuvable.")
                return
            rs, pack = _regles(camp), charger_pack(camp.lore_pack)
            fiche, destinee = demande["fiche"], demande["destinee"]
            try:
                yield _sse("etape", "Le monde s'instancie — lieux, factions, "
                                    "sièges de grade…")
                amorce.amorcer_monde(session, camp, pack, rs, fiche.village_id)

                yield _sse("etape", "Ton nom est inscrit au registre du village…")
                pj = creer_personnage(session, camp, pack, rs, fiche, destinee)
                camp.phase = "amorce"
                session.add(camp)
                session.commit()

                # Le destin a tranché : on le dit avant le présage. C'est le
                # seul moment où le joueur apprend QUEL genre de shinobi le
                # sort a fait de lui — le nom et l'accroche, rien de plus.
                destin = session.exec(select(Destiny).where(
                    Destiny.character_id == pj.id)).first()
                if destin is not None and destin.archetype_nom:
                    yield _sse("archetype", json.dumps({
                        "nom": destin.archetype_nom,
                        "accroche": destin.archetype_accroche,
                        "village": pj.village, "clan": pj.clan,
                        "voie": rs.specialisations.get(
                            pj.specialisation, {}).get("label", pj.specialisation),
                    }, ensure_ascii=False))

                presage = _presage(session, pj)
                if presage:
                    yield _sse("presage", presage.replace("\n", "\\n"))

                yield _sse("etape", "On te donne une équipe. Elle ne te "
                                    "ressemblera pas.")
                distribution = amorce.generer_distribution(
                    session, camp, pack, rs, pj)
                gabarit = templates.get_template("_equipier.html")
                for c in distribution:
                    yield _sse("equipier", gabarit.render(
                        request=request, c=c, camp=camp).strip())

                yield _sse("etape", "La première scène s'écrit…")
                amorce.amorcer_recit(session, camp, pack, rs, pj, distribution)

                yield _sse("fin", f"/campagnes/{cid}?pj={pj.id}")
            except httpx.HTTPError as exc:
                session.rollback()
                yield _sse("echec",
                           f"Aucun conteur n'a répondu ({exc.__class__.__name__}). "
                           "Ouvre /sante pour savoir quoi faire.")
            except Exception as exc:  # noqa: BLE001 — le joueur doit être prévenu
                session.rollback()
                yield _sse("echec", f"Le scellé a échoué : {exc}")

    return StreamingResponse(evenements(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.post("/campagnes/{cid}/voyager")
def voyager(cid: int, request: Request, destination: int = Form(...),
            character_id: int | None = Form(None),
            session: Session = Depends(get_session)):
    """Se rendre ailleurs. Coûte des tours, et peut mal se passer.

    Passe par le verrou de tour comme une action : le voyage avance l'horloge
    de campagne, et deux déplacements simultanés la décaleraient deux fois.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    try:
        with tour_exclusif(cid):
            carte.voyager(session, camp, pack, rs, pj, destination)
    except (carte.VoyageImpossible, CampagneOccupee) as exc:
        return templates.TemplateResponse(
            "_erreur.html", {"request": request, "message": str(exc)},
            status_code=409)
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.post("/campagnes/{cid}/repos")
def repos(cid: int, character_id: int | None = Form(None),
          session: Session = Depends(get_session)):
    """Souffler : rend des ressources et lève les blessures réversibles.

    Volontairement hors narration et hors modèle. C'est la soupape qui empêche
    une rencontre perdue de condamner la campagne, et elle doit marcher même
    quand Ollama ne répond plus.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    rs = _regles(camp)
    if cbt.active(session, camp) is not None:
        raise HTTPException(400, "On ne se repose pas au milieu d'un combat.")
    cbt.recuperer(session, camp, rs, pj, repos=True)
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.post("/campagnes/{cid}/apprendre")
def apprendre(cid: int, request: Request, technique: str = Form(...),
              character_id: int | None = Form(None), retour: str = Form(""),
              session: Session = Depends(get_session)):
    """Apprendre une technique. Coûte des tours, comme un voyage.

    Hors narration et hors modèle : l'éligibilité est entièrement
    déterministe, et s'entraîner doit marcher avec Ollama éteint. Passe par le
    verrou de tour, parce que l'horloge de campagne avance.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    try:
        with tour_exclusif(cid):
            apprentissage.apprendre(session, camp, pack, rs, pj, technique)
    except (apprentissage.ApprentissageRefuse, CampagneOccupee) as exc:
        return templates.TemplateResponse(
            "_erreur.html", {"request": request, "message": str(exc)},
            status_code=409)
    if retour.startswith(f"/campagnes/{cid}/") and "//" not in retour[1:]:
        return RedirectResponse(retour, status_code=303)
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.get("/campagnes/{cid}/reprise", response_class=HTMLResponse)
def rappel_de_reprise(cid: int, request: Request, pj: int | None = None,
                      session: Session = Depends(get_session)):
    """« Précédemment » — chargé à part pour ne pas ralentir la table.

    La table pose un trou avec `hx-trigger="load"` : la page s'affiche tout de
    suite, le rappel arrive une seconde après. C'est le seul endroit du jeu où
    l'on accepte d'attendre un modèle sans rien avoir à faire, parce qu'on est
    en train de s'installer.
    """
    camp = _camp(session, cid)
    perso = _pj_actif(session, cid, pj)
    if not perso:
        return HTMLResponse("")
    rs = _regles(camp)
    rappel = reprise.rappeler(session, camp, perso, rs)
    return templates.TemplateResponse("_reprise.html", {
        "request": request, "camp": camp, "pj": perso,
        "rappel": rappel, "oob": False})


@router.post("/campagnes/{cid}/progresser")
def progresser(cid: int, request: Request, stat: str = Form(...),
               character_id: int | None = Form(None),
               points: int = Form(1), retour: str = Form(""),
               session: Session = Depends(get_session)):
    """Placer un point de caractéristique gagné en montant de niveau.

    Hors narration et hors modèle, comme le repos : c'est une décision du
    joueur sur sa fiche, elle doit aboutir même si Ollama est éteint. Et elle
    n'avance pas l'horloge — donc pas de verrou de tour.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    rs = _regles(camp)
    try:
        progression.depenser(session, camp, rs, pj, stat, points)
    except progression.ProgressionRefusee as exc:
        return templates.TemplateResponse(
            "_erreur.html", {"request": request, "message": str(exc)},
            status_code=409)
    # On revient là où le joueur a cliqué — la table ou sa fiche. La
    # destination est bornée à SA campagne : un champ de formulaire ne décide
    # pas d'une redirection arbitraire.
    if retour.startswith(f"/campagnes/{cid}/") and "//" not in retour[1:]:
        return RedirectResponse(retour, status_code=303)
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.post("/campagnes/{cid}/missions")
def demander_mission(cid: int, character_id: int | None = Form(None),
                     session: Session = Depends(get_session)):
    """Le bureau des missions.

    L'ossature est tirée du lore par le moteur — archétype, commanditaire, lieu
    réel, objet réel, complication — et le modèle ne fait que l'habiller. C'est
    ce qui garantit que deux missions ne se ressemblent pas, et qu'aucune ne
    cite un lieu ou une faction qui n'existe pas.
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    gen_missions.generer(session, camp, pack, rs, pj)
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.post("/campagnes/{cid}/missions/{qid}/{choix}")
def repondre_mission(cid: int, qid: int, choix: str,
                     character_id: int | None = Form(None),
                     session: Session = Depends(get_session)):
    """Accepter ou refuser une offre du bureau.

    Sans ce bouton, le joueur ne savait pas comment « prendre » une mission :
    il suivait les pistes du récit, et les offres expiraient une à une.
    Accepter lance l'horloge de l'arc (voir engine/fils.py).
    """
    camp = _camp(session, cid)
    _exige_en_cours(camp)
    pj = _pj_actif(session, cid, character_id)
    quete = session.get(Quest, qid)
    if not pj or quete is None or quete.campaign_id != cid:
        raise HTTPException(404, "Mission introuvable")
    statut = {"accepter": "acceptée", "refuser": "refusée"}.get(choix)
    if statut is None or quete.statut != "proposée":
        raise HTTPException(400, "Cette offre n'est plus ouverte")
    gen_missions.changer_statut(session, camp, pj, _regles(camp), quete, statut,
                                par_le_joueur=True)
    session.commit()
    return RedirectResponse(f"/campagnes/{cid}?pj={pj.id}", status_code=303)


@router.post("/campagnes/{cid}/allure")
def changer_allure(cid: int, allure: str = Form(...), pj: int | None = Form(None),
                   session: Session = Depends(get_session)):
    """La longueur des récits, réglable en pleine partie."""
    camp = _camp(session, cid)
    if allure in ("court", "normal", "long"):
        camp.allure = allure
        session.add(camp)
        session.commit()
    suite = f"?pj={pj}" if pj else ""
    return RedirectResponse(f"/campagnes/{cid}{suite}", status_code=303)


@router.post("/campagnes/{cid}/clore")
def clore_la_chronique(cid: int, request: Request,
                       character_id: int | None = Form(None),
                       session: Session = Depends(get_session)):
    """Le mot de la fin. Écrit l'épilogue et ferme la campagne.

    Jamais déclenché tout seul : le jeu dit qu'on PEUT conclure, c'est la table
    qui décide. Et `rouvrir` est à un clic, parce qu'on se trompe.
    """
    camp = _camp(session, cid)
    pj = _pj_actif(session, cid, character_id)
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    try:
        with tour_exclusif(cid):
            epilogue.clore(session, camp, pj, _regles(camp))
    except (epilogue.FinRefusee, CampagneOccupee) as exc:
        return templates.TemplateResponse(
            "_erreur.html", {"request": request, "message": str(exc)},
            status_code=409)
    return RedirectResponse(f"/campagnes/{cid}/chronique", status_code=303)


@router.post("/campagnes/{cid}/rouvrir")
def rouvrir_la_chronique(cid: int, session: Session = Depends(get_session)):
    """Il restait quelque chose à jouer. Rien n'est effacé."""
    camp = _camp(session, cid)
    epilogue.rouvrir(session, camp)
    return RedirectResponse(f"/campagnes/{cid}", status_code=303)


@router.get("/campagnes/{cid}/chronique", response_class=HTMLResponse)
def chronique(cid: int, request: Request,
              session: Session = Depends(get_session)):
    """La campagne relue comme un récit.

    POURQUOI CETTE PAGE. Tout était déjà en base — les résumés hiérarchiques,
    les événements, les faits, les rencontres, les destinées ouvertes — et le
    joueur n'en voyait jamais rien. Une campagne de quarante tours ne laissait
    qu'un mur de scrollback qu'on ne relit pas.

    Ce qu'on assemble ici n'est pas un journal technique : c'est l'histoire,
    dans l'ordre, avec ses chapitres. Et c'est la page qu'on montre quand
    quelqu'un demande « raconte ».

    LE FILTRE DE DIVULGATION TIENT AUSSI ICI. Aucune vérité de secret, aucune
    vérité de destinée : le joueur relit ce qu'il a vécu, pas ce que le moteur
    savait.
    """
    camp = _camp(session, cid)
    pjs = _pjs(session, cid)
    if not pjs:
        return RedirectResponse(f"/campagnes/{cid}/creation", status_code=303)
    rs = _regles(camp)

    # Les rappels de reprise sont rangés dans la même table : ce sont des aides
    # de séance, pas des chapitres de l'histoire.
    resumes = session.exec(select(Summary).where(
        Summary.campaign_id == cid,
        Summary.niveau != reprise.NIVEAU).order_by(Summary.du_tour)).all()
    tours = session.exec(select(Turn).where(
        Turn.campaign_id == cid).order_by(Turn.index)).all()

    # Les chapitres : un résumé couvre une tranche de tours, et la tranche
    # encore en cours n'en a pas — on la montre telle quelle.
    chapitres = []
    for s in resumes:
        chapitres.append({
            "du": s.du_tour, "au": s.au_tour, "texte": s.texte,
            "marquants": [t for t in tours
                          if s.du_tour <= t.index <= s.au_tour
                          and (t.effets or t.resolution.get("combat"))][:3],
        })
    couvert = resumes[-1].au_tour if resumes else 0
    en_cours = [t for t in tours if t.index > couvert]

    rencontres = session.exec(select(Encounter).where(
        Encounter.campaign_id == cid).order_by(Encounter.tour_debut)).all()

    secrets_ouverts = [s for s in session.exec(select(Secret).where(
        Secret.campaign_id == cid)).all() if s.niveau_revele >= 1]

    return templates.TemplateResponse("chronique.html", {
        "request": request, "camp": camp, "pjs": pjs, "pj": pjs[0], "rs": rs,
        "chapitres": chapitres, "en_cours": en_cours, "couvert": couvert,
        "ouverture": tours[0] if tours else None,
        "rencontres": rencontres,
        "jalons": session.exec(select(Event).where(
            Event.campaign_id == cid, Event.importance >= 4)
            .order_by(Event.tour)).all(),
        "eveils": session.exec(select(DestinyTrait).where(
            DestinyTrait.campaign_id == cid,
            DestinyTrait.etat.in_(["pressenti", "en_eveil", "eveille"]))).all(),
        # Les questions ENTAMÉES seulement : un secret intact n'a pas à
        # s'annoncer, et sa question suffirait à trahir qu'il existe.
        "questions": [s for s in secrets_ouverts if not s.perdu],
        "questions_perdues": [s for s in secrets_ouverts if s.perdu],
        # Le mot de la fin, et ce qui l'autorise. Une campagne doit pouvoir
        # s'achever : `Campaign.phase` connaissait « terminee » et rien ne la
        # déclenchait. Voir app/engine/epilogue.py.
        "epilogue": session.exec(select(Summary).where(
            Summary.campaign_id == cid,
            Summary.niveau == epilogue.NIVEAU)
            .order_by(Summary.au_tour.desc())).first(),
        "fin_possible": epilogue.possible(session, camp, pjs[0]),
        "noms": {c.id: c.nom for c in session.exec(select(Character).where(
            Character.campaign_id == cid)).all()},
    })


# --------------------------------------------------------------------------
# Fiche ninja
# --------------------------------------------------------------------------
@router.get("/campagnes/{cid}/fiche/{pid}", response_class=HTMLResponse)
def fiche(cid: int, pid: int, request: Request, session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    perso = session.get(Character, pid)
    if not perso or perso.campaign_id != cid:
        raise HTTPException(404, "Personnage introuvable")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)

    relations = []
    for rel in session.exec(select(Relation).where(
            Relation.campaign_id == cid, Relation.cible_id == perso.id)).all():
        src = session.get(Character, rel.source_id)
        if src:
            relations.append({"nom": src.nom, "valeur": rel.valeur, "nature": rel.nature})

    return templates.TemplateResponse("fiche.html", {
        "request": request, "camp": camp, "pj": perso, "rs": rs, "pack": pack,
        "stats_cfg": rs.stats, "groupes": rs.data.get("groupes_stats", {}),
        "res_cfg": rs.data.get("resources", {}),
        "titre_grade": rs.titre_grade(perso.grade),
        "tier_label": rs.tier_label(perso.tier),
        "xp_requis": rs.xp_pour_niveau(perso.niveau),
        "techniques": _techniques(session, perso, pack, rs),
        "synergies": _synergies(session, perso, pack),
        "relations": sorted(relations, key=lambda r: -abs(r["valeur"])),
        "presage": _presage(session, perso),
        "archetype": _archetype(session, perso),
        "offres_progression": progression.offres(rs, perso),
        "offres_apprentissage": (
            apprentissage.offres(session, camp, pack, rs, perso)
            if perso.is_pc else []),
        "stat_max": progression.plafond(rs),
        "specialite": rs.specialisations.get(
            perso.specialisation, {}).get("label", ""),
        "lieu": session.get(Location, perso.location_id) if perso.location_id else None,
    })


# --------------------------------------------------------------------------
# Le lanceur de l'application
# --------------------------------------------------------------------------
@router.get("/lanceur", response_class=HTMLResponse)
def lanceur(request: Request, session: Session = Depends(get_session)):
    """L'écran d'ouverture de Nindō.exe : jouer, reprendre, les nouveautés,
    le conteur, la clé Mistral, et la mise à jour quand il y en a une.
    Rien de lent ici : ce qui interroge le réseau arrive ensuite, en fond."""
    from app import reglages
    from app.version import VERSION
    derniere = session.exec(select(Campaign).where(Campaign.phase == "en_cours")
                            .order_by(Campaign.joue_le.desc())).first()
    pj = None
    if derniere is not None:
        pj = session.exec(select(Character).where(
            Character.campaign_id == derniere.id, Character.is_pc == True)).first()  # noqa: E712
    return templates.TemplateResponse("lanceur.html", {
        "request": request, "version": VERSION, "derniere": derniere, "pj_recent": pj,
        "cle_masquee": reglages.cle_masquee(),
        "village_heros": (pj.village_ref if pj and pj.village_ref else
                          random.choice(["konoha", "suna", "kiri", "kumo", "iwa", "oto"])),
    })


@router.get("/lanceur/etat")
def lanceur_etat():
    """Version, mise à jour disponible, nouveautés. Interroge GitHub (mis en
    cache dix minutes), donc appelé en fond par la page."""
    from app import maj
    return maj.etat()


@router.post("/lanceur/cle")
def lanceur_cle(cle: str = Form(...)):
    from app import reglages
    try:
        reglages.enregistrer_cle_mistral(cle)
    except ValueError as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return {"ok": True, "cle": reglages.cle_masquee()}


@router.post("/lanceur/maj")
def lanceur_maj():
    from app import maj
    try:
        version = maj.installer()
    except maj.MajImpossible as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
    return {"ok": True, "version": version}


# --------------------------------------------------------------------------
# Diagnostic
# --------------------------------------------------------------------------
@router.get("/sante")
def sante():
    info = {"fournisseur": settings.llm_provider, "modele": settings.llm_model,
            "modele_rapide": settings.llm_fast_model, "contexte": settings.llm_num_ctx}
    if settings.llm_provider == "en_ligne":
        return _sante_en_ligne(info)
    if settings.llm_provider != "ollama":
        info["ollama"] = "non utilisé (mode mock)"
        info["pret"] = True
        return info
    return _sante_ollama(info)


def _sante_en_ligne(info: dict) -> dict:
    """Le service en ligne d'abord, puis son secours. Le jeu est prêt si l'un
    des deux répond : c'est tout l'intérêt du relais."""
    from app.llm.provider import EnLigneProvider, ServiceIndisponible
    service = EnLigneProvider()
    en_ligne = {"service": service.nom, "modele": service.modele,
                "adresse": service.url,
                "cle": (f"présente (…{service.cle[-4:]})" if service.cle
                        else "absente")}
    try:
        service.verifier()
        # /models ne génère rien : il ne coûte pas de quota de génération.
        r = service.client.get(f"{service.url}/models", headers=service._entetes(),
                               timeout=8.0)
        if r.status_code in (401, 403):
            en_ligne["etat"] = f"CLÉ REFUSÉE (erreur {r.status_code})"
        else:
            r.raise_for_status()
            # Être listé ne veut pas dire être ouvert : sur une offre gratuite,
            # un modèle peut figurer au catalogue avec 0 requête/minute. Un
            # appel d'un seul jeton tranche.
            essai = service.client.post(
                f"{service.url}/chat/completions", headers=service._entetes(),
                timeout=20.0, json={"model": service.modele, "max_tokens": 1,
                                    "messages": [{"role": "user", "content": "ok"}]})
            plafond = essai.headers.get("x-ratelimit-limit-req-minute")
            if essai.status_code == 200:
                en_ligne["etat"] = "joignable"
                if plafond:
                    en_ligne["requetes_par_minute"] = int(plafond)
            elif essai.status_code == 429 and plafond == "0":
                en_ligne["etat"] = (f"MODÈLE FERMÉ sur ton offre ({service.modele}, "
                                    f"0 requête/minute)")
            elif essai.status_code == 429:
                en_ligne["etat"] = "QUOTA ATTEINT pour l'instant"
            else:
                en_ligne["etat"] = (f"REFUSÉ (erreur {essai.status_code}) : "
                                    f"{essai.text[:120]}")
    except ServiceIndisponible as exc:
        en_ligne["etat"] = f"INUTILISABLE — {exc}"
    except Exception as exc:  # noqa: BLE001
        en_ligne["etat"] = f"INJOIGNABLE ({exc.__class__.__name__})"
    info["en_ligne"] = en_ligne
    info["conteur_actuel"] = conteur()
    principal_ok = en_ligne["etat"] == "joignable"

    if settings.llm_secours == "ollama":
        secours = _sante_ollama({})
        info["secours_ollama"] = secours
        info["pret"] = principal_ok or bool(secours.get("pret"))
    else:
        info["pret"] = principal_ok
    if not principal_ok:
        info.setdefault("action", []).append(
            "Mets ta clé dans .env (EN_LIGNE_CLE=…) puis redémarre le jeu."
            if en_ligne["cle"] == "absente" and service.cle_requise
            else "Choisis un modèle ouvert sur ton offre (EN_LIGNE_MODELE), "
                 "puis redémarre le jeu."
            if en_ligne["etat"].startswith("MODÈLE FERMÉ")
            else f"Vérifie ta connexion et ta clé {service.nom}.")
    return info


def _sante_ollama(info: dict) -> dict:
    try:
        r = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=5.0)
        r.raise_for_status()
        dispo = [m["name"] for m in r.json().get("models", [])]
        # Le petit modèle ne sert que si LLM_MODELE_UNIQUE est désactivé.
        requis = ((settings.llm_model,) if settings.llm_modele_unique
                  else (settings.llm_model, settings.llm_fast_model))
        manquants = [m for m in requis if m not in dispo]
        info.update({"ollama": "joignable", "modeles_installes": dispo,
                     "manquants": manquants, "pret": not manquants})
        if manquants:
            info["action"] = [f"ollama pull {m}" for m in manquants]

        # Le rappel par le sens est un CONFORT, pas une condition : son
        # absence ne doit jamais faire dire « pas prêt ». On le signale, et
        # on donne la commande.
        if settings.embeddings:
            present = any(m.split(":")[0] == settings.embed_model.split(":")[0]
                          for m in dispo)
            info["rappel_semantique"] = (
                f"actif ({settings.embed_model})" if present
                else "inactif — le rappel reste lexical")
            if not present:
                info.setdefault("action", []).append(
                    f"ollama pull {settings.embed_model}")
    except Exception as exc:  # noqa: BLE001
        info.update({"ollama": "INJOIGNABLE", "erreur": str(exc), "pret": False,
                     "action": ["Démarre Ollama, puis recharge cette page."]})
    return info


# --------------------------------------------------------------------------
# API JSON — même moteur, autre représentation
# --------------------------------------------------------------------------
@router.get("/api/campagnes/{cid}/etat")
def api_etat(cid: int, session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    return {
        "campagne": camp.model_dump(exclude={"ruleset"}),
        "personnages": [p.model_dump() for p in _pjs(session, cid)],
        "quetes": [q.model_dump() for q in session.exec(
            select(Quest).where(Quest.campaign_id == cid)).all()],
        "evenements": [e.model_dump() for e in session.exec(
            select(Event).where(Event.campaign_id == cid)
            .order_by(Event.tour.desc()).limit(30)).all()],
    }


@router.post("/api/campagnes/{cid}/tours")
def api_jouer(cid: int, payload: dict, session: Session = Depends(get_session)):
    camp = _camp(session, cid)
    pj = _pj_actif(session, cid, payload.get("character_id"))
    if not pj:
        raise HTTPException(400, "Aucun personnage joueur")
    texte = (payload.get("action") or "").strip()
    if not texte:
        raise HTTPException(400, "Champ 'action' requis")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    return jouer(session, camp, pj, texte, rs, pack,
                 posture=(payload.get("posture") or ""),
                 levier=(payload.get("levier") or "")).model_dump()


@router.get("/api/campagnes/{cid}/voix/{tid}")
def api_voix(cid: int, tid: int, session: Session = Depends(get_session)):
    """Le plan de lecture d'un tour : quoi dire, avec quelle voix.

    Calculé à la demande plutôt que stocké : l'attribution des répliques
    s'améliorera, et on ne veut pas traîner de vieux découpages. C'est du pur
    Python — quelques dixièmes de milliseconde.
    """
    camp = _camp(session, cid)
    tour = session.get(Turn, tid)
    if tour is None or tour.campaign_id != cid:
        raise HTTPException(404, "Tour introuvable")
    rs, pack = _regles(camp), charger_pack(camp.lore_pack)
    return {"tour": tour.index,
            "segments": plan_de_lecture(session, camp, tour, rs, pack)}


@router.get("/api/campagnes/{cid}/rencontre")
def api_rencontre(cid: int, session: Session = Depends(get_session)):
    """L'affrontement en cours, ou `null`. Même moteur, autre représentation.

    Les points de vie exacts des adversaires ne sortent pas d'ici non plus :
    l'API sert à piloter le jeu, pas à le déjouer.
    """
    camp = _camp(session, cid)
    rs = _regles(camp)
    pj = _pj_actif(session, cid, None)
    if pj is None:
        raise HTTPException(400, "Aucun personnage joueur")
    ctx = _ctx_rencontre(session, camp, rs, pj)
    renc = ctx["rencontre"]
    if renc is None:
        return {"rencontre": None}
    return {
        "rencontre": renc.model_dump(exclude={"journal"}),
        "adversaires": [{k: v for k, v in e.items() if k != "pct"}
                        for e in ctx["ennemis"]],
        "allies": ctx["allies_au_combat"],
        "hors_combat": ctx["hors_combat"],
        "postures": ctx["postures"],
    }


# --------------------------------------------------------------------------
def _techniques(session: Session, perso: Character, pack,
                rs: Ruleset | None = None) -> list[dict]:
    liens = list(session.exec(select(CharacterTechnique).where(
        CharacterTechnique.character_id == perso.id)).all())
    par_ref = {ct.technique_ref: ct for ct in liens}

    out = []
    for ct in liens:
        t = pack.technique(ct.technique_ref) or {}
        palier = rs.palier_maitrise(ct.maitrise).get("nom", "") if rs else ""
        out.append({
            "nom": t.get("nom", ct.technique_ref), "fr": t.get("fr", ""),
            "rang": t.get("rang", "E"), "categorie": t.get("categorie", ""),
            "element": t.get("element", ""), "effet": t.get("effet", ""),
            "maitrise": ct.maitrise, "usages": ct.usages, "palier": palier,
        })
    return sorted(out, key=lambda x: (x["rang"], -x["maitrise"]))


def _synergies(session: Session, perso: Character, pack) -> list[dict]:
    """Ce que le répertoire du personnage permet DE PLUS que ses techniques
    prises une à une.

    Le joueur ne peut pas jouer une profondeur tactique qu'on lui cache : un
    combo qui n'apparaît nulle part ne sera jamais tenté. On montre donc aussi
    ceux qui ne sont pas encore prêts, avec ce qui leur manque — c'est ce qui
    donne un objectif d'entraînement.
    """
    liens = {ct.technique_ref: ct for ct in session.exec(
        select(CharacterTechnique).where(
            CharacterTechnique.character_id == perso.id)).all()}
    if not liens:
        return []

    def nom(ref: str) -> str:
        return (pack.technique(ref) or {}).get("nom", ref)

    out = []
    for s in pack.synergies():
        requis = s.get("requiert") or []
        if s.get("type") == "prerequis":
            # On ne l'affiche que si la technique débloquée n'est pas déjà là.
            if s.get("debloque") in liens:
                continue
        elif not any(r in liens for r in requis):
            continue

        seuil = int(s.get("maitrise_min", 0))
        manque = [nom(r) for r in requis if r not in liens]
        trop_verts = [nom(r) for r in requis
                      if r in liens and liens[r].maitrise < seuil]
        out.append({
            "nom": s.get("nom") or s.get("id"),
            "type": s.get("type"),
            "requiert": [nom(r) for r in requis],
            "debloque": nom(s["debloque"]) if s.get("debloque") else "",
            "contre": [nom(r) for r in (s.get("contre") or [])],
            "seuil": seuil,
            "prete": not manque and not trop_verts,
            "manque": manque,
            "trop_verts": trop_verts,
        })
    return out


def _presage(session: Session, perso: Character) -> str:
    d = session.exec(select(Destiny).where(
        Destiny.character_id == perso.id)).first()
    return d.presage if d else ""


def _archetype(session: Session, perso: Character) -> dict | None:
    """Ce que le sort a fait du personnage — nom et accroche, jamais plus."""
    d = session.exec(select(Destiny).where(
        Destiny.character_id == perso.id)).first()
    if d is None or not d.archetype_nom:
        return None
    return {"nom": d.archetype_nom, "accroche": d.archetype_accroche}
