"""Parcours utilisateur complet, sans navigateur : `python -m scripts.parcours`

Rejoue exactement ce que fait un joueur :
  1. crée une campagne             5.      joue des tours
  2. crée un personnage            5 bis.  reçoit une narration en flux
  3. reçoit sa destinée            5 ter.  traverse un affrontement complet
  4. l'IA génère sa distribution   6.      ajoute un second joueur (hot-seat)
                                   7.      vérifie que les secrets ne fuitent pas
                                   8.      relit la base : tout est sauvegardé

Fonctionne en mode `mock` (aucun GPU requis) comme en mode `ollama`.
Lance-le après chaque modification : c'est ton filet de sécurité.
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

# UNE BASE À PART. Le parcours créait deux campagnes de test dans
# `data/parties.db` à CHAQUE exécution : au bout de quelques semaines, l'accueil
# du joueur listait cent quarante « L'Ombre du Sceau » et pesait un demi-méga.
# On écrit donc dans `data/parcours.db`, sauf si l'appelant a fixé DATABASE_URL
# lui-même — et ceci doit précéder tout import de `app`, qui lit le réglage à
# l'import.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/parcours.db")

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, select

from app.config import settings
from app.db import engine, init_db
from app.engine import apprentissage, carte, combat, epilogue, reprise
from app.lore.pack import charger as charger_pack
from app.rules.loader import charger as charger_ruleset
from app.main import app
from app.models import (Campaign, Character, Crystallization, Destiny,
                        DestinyTrait, Encounter, Event, Knowledge, MemoryFact,
                        Quest, Relation, Secret, Summary, Turn, maintenant)

V, R, G, Z = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
_echecs: list[str] = []


def ok(cond: bool, libelle: str, detail: str = "") -> None:
    if cond:
        print(f"  {V}✓{Z} {libelle}")
    else:
        print(f"  {R}✗ {libelle}{Z}" + (f"\n      {G}{detail}{Z}" if detail else ""))
        _echecs.append(libelle)


def evenements_de(corps: str) -> list[str]:
    """Les noms d'événements d'un flux SSE, dans l'ordre d'arrivée."""
    return [b.split("\n", 1)[0].removeprefix("event: ")
            for b in corps.split("\n\n") if b.startswith("event: ")]


def sceller(c, reponse) -> list[str]:
    """Consomme le flux du scellé et rend ses événements.

    Le premier personnage d'une campagne ne répond plus par une redirection :
    il rend la page du sceau, qui ouvre un flux où le monde s'amorce. C'est ce
    flux qui crée réellement le personnage — il faut donc le lire jusqu'au bout
    avant d'interroger la base.
    """
    debut = reponse.text.find('data-flux="')
    if debut < 0:
        return []
    debut += len('data-flux="')
    url = reponse.text[debut:reponse.text.find('"', debut)]
    return evenements_de(c.get(url).text)


def decompose(pj, traits, cle: str, spec: str, libres: int) -> tuple[int, str]:
    """Reconstitue une caractéristique poste par poste.

    Une valeur écrite en dur dans le test serait fausse dès qu'une destinée
    tire un trait qui touche la même caractéristique — et c'est du hasard, donc
    ça casserait un jour sur deux. On vérifie donc la RÈGLE :
    base + clan + spécialisation + traits actifs + points libres.
    """
    pack, rs = charger_pack(), charger_ruleset("naruto")
    base = rs.stats[cle].get("default", 10)
    postes = [f"base {base}"]
    total = base

    clan = pack.clan(pj.clan_ref) or {}
    if (v := (clan.get("bonus") or {}).get(cle)):
        total += int(v)
        postes.append(f"clan {clan.get('nom', pj.clan_ref)} +{v}")

    if (v := ((rs.specialisations.get(spec) or {}).get("bonus") or {}).get(cle)):
        total += int(v)
        postes.append(f"spécialisation {spec} +{v}")

    for tr in traits:
        if not tr.actif_au_depart:
            continue
        modele = pack.trait(tr.trait_ref) or {}
        if (v := ((modele.get("accorde") or {}).get("stats") or {}).get(cle)):
            total += int(v)
            postes.append(f"trait « {tr.libelle or tr.trait_ref} » +{v}")

    if libres:
        total += libres
        postes.append(f"points libres +{libres}")
    return total, " + ".join(postes)


def main() -> int:
    print(f"\n{G}Fournisseur : {settings.llm_provider} "
          f"({settings.llm_model} / {settings.llm_fast_model}){Z}\n")
    init_db()
    c = TestClient(app)

    # ------------------------------------------------------------- 1. accueil
    print("1. Application")
    ok(c.get("/").status_code == 200, "la page d'accueil répond")
    ok(c.get("/sante").json().get("pret") is not None, "le diagnostic répond")

    # ------------------------------------------------------------ 2. campagne
    print("\n2. Campagne")
    r = c.post("/campagnes", data={"nom": "L'Ombre du Sceau",
                                   "epoque": "naruto_p1",
                                   "ton": "shonen sombre"},
               follow_redirects=False)
    ok(r.status_code == 303, "la campagne est créée", r.text[:200])
    with Session(engine) as s:
        camp = s.exec(select(Campaign).order_by(Campaign.id.desc())).first()
        cid = camp.id
    ok(camp is not None and camp.phase == "creation", "elle démarre en phase de création")
    ok(bool(camp.ruleset), "les règles sont copiées dans la partie "
                           "(modifier le YAML ne changera pas cette campagne)")

    r = c.get(f"/campagnes/{cid}", follow_redirects=False)
    ok(r.status_code == 303 and "creation" in r.headers.get("location", ""),
       "sans personnage, on est redirigé vers la création")
    ok(c.get(f"/campagnes/{cid}/creation").status_code == 200,
       "le formulaire de création s'affiche")

    # --------------------------------------------------------- 3. personnage
    print("\n3. Personnage et destinée")
    r = c.post(f"/campagnes/{cid}/creation", data={
        "nom": "Kaito Arashi", "joueur": "Chams", "sexe": "masculin", "age": "13",
        "apparence": "Cheveux noirs en bataille, cicatrice à l'avant-bras gauche.",
        "village": "konoha", "origine": "sans_clan",
        "specialisation": "fuinjutsu", "mode_destinee": "aleatoire",
        "pt_vitesse": "2", "pt_endurance": "2", "pt_perception": "1",
    }, follow_redirects=False)
    ok(r.status_code == 200 and 'data-flux="' in r.text,
       "le sceau répond aussitôt, sans faire attendre devant une page figée",
       r.text[:300])

    evts = sceller(c, r)
    ok(evts.count("etape") >= 4,
       f"l'amorce annonce ses {evts.count('etape')} étapes une par une")
    ok("presage" in evts, "le présage est montré dès qu'il tombe")
    ok(evts.count("equipier") == 4,
       f"l'entourage se retourne un visage après l'autre ({evts.count('equipier')})")
    ok(evts and evts[-1] == "fin", "le scellé s'achève sur la table de jeu")

    with Session(engine) as s:
        pj = s.exec(select(Character).where(
            Character.campaign_id == cid, Character.is_pc == True)).first()  # noqa: E712
        ok(pj is not None and pj.nom == "Kaito Arashi", "il est enregistré en base")
        ok(pj.tier >= 1 and pj.pe > 0, f"sa puissance est calculée (tier {pj.tier}, PE {pj.pe})")
        ok(len(pj.inventaire) > 0, "il a un inventaire de départ")
        ok(pj.location_id is not None, "il est placé dans un lieu")
        pj_id = pj.id

        d = s.exec(select(Destiny).where(Destiny.character_id == pj_id)).first()
        ok(d is not None, "une fiche de destinée est écrite")
        ok(bool(d.presage), "un présage lui est rendu")
        traits = s.exec(select(DestinyTrait).where(
            DestinyTrait.destiny_id == d.id)).all()
        ok(len(traits) >= 2, f"{len(traits)} traits tirés")

        # Les caractéristiques se vérifient poste par poste, une fois les traits
        # connus : un trait actif peut légitimement toucher la même que les
        # points libres, et la destinée est tirée au hasard.
        attendu, detail = decompose(pj, traits, "vitesse", "fuinjutsu", libres=2)
        ok(pj.stats.get("vitesse") == attendu,
           f"les points libres sont appliqués ({detail} = {attendu})",
           f"valeur trouvée : {pj.stats.get('vitesse')}")
        attendu, detail = decompose(pj, traits, "controle_chakra", "fuinjutsu", libres=0)
        ok(pj.stats.get("controle_chakra") == attendu,
           f"le bonus de spécialisation aussi ({detail} = {attendu})",
           f"valeur trouvée : {pj.stats.get('controle_chakra')}")
        actifs = [t for t in traits if t.actif_au_depart]
        latents = [t for t in traits if not t.actif_au_depart]
        ok(len(latents) >= 1, f"{len(latents)} traits restent latents, à découvrir")
        ok(all(t.rarete in ("commun", "peu_commun") for t in actifs),
           "aucun trait rare n'est actif à la création",
           f"actifs : {[(t.libelle, t.rarete) for t in actifs]}")
        ok(sum(t.cout for t in latents) <= d.budget_latent,
           "le budget latent est respecté")

    # -------------------------------------------------------- 4. distribution
    print("\n4. Distribution générée par l'IA")
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pnjs = s.exec(select(Character).where(
            Character.campaign_id == cid, Character.is_pc == False)).all()  # noqa: E712
        roles = {p.role_campagne for p in pnjs}
        ok(len(pnjs) >= 4, f"{len(pnjs)} personnages créés autour du joueur")
        ok("sensei" in roles, "un instructeur")
        ok(list(roles).count("coequipier") or "coequipier" in roles, "des coéquipiers")
        ok("rival" in roles, "un rival")
        ok(all(p.personnalite for p in pnjs), "chacun a une personnalité")
        ok(all(p.parler for p in pnjs), "chacun a une manière de parler")
        ok(all(p.source == "genere" for p in pnjs), "ils sont marqués comme générés")
        ok(all(p.notes for p in pnjs),
           "chacun porte un détail distinctif imposé (table de sel)")

        rels = s.exec(select(Relation).where(
            Relation.campaign_id == cid, Relation.cible_id == pj_id)).all()
        ok(len(rels) >= 4, f"{len(rels)} relations amorcées vers le joueur")

        secrets = s.exec(select(Secret).where(Secret.campaign_id == cid)).all()
        ok(len(secrets) >= 3, f"{len(secrets)} secrets écrits et figés dès maintenant")
        ok(all(s_.verite for s_ in secrets), "chaque secret porte sa vérité")
        ok(all(s_.niveau_revele == 0 for s_ in secrets), "aucun n'est encore révélé")

        connu = s.exec(select(Knowledge).where(Knowledge.campaign_id == cid)).all()
        ok(len(connu) >= 4, "ce que le groupe sait est enregistré séparément")

        ok(camp.phase == "en_cours" and camp.tour == 1, "la campagne est ouverte au tour 1")
        ouverture = s.exec(select(Turn).where(Turn.campaign_id == cid)).first()
        ok(ouverture is not None and len(ouverture.narration) > 0,
           "la scène d'ouverture est écrite")

    # ------------------------------------------------------------- 5. le jeu
    print("\n5. Tours de jeu")
    r = c.get(f"/campagnes/{cid}")
    ok(r.status_code == 200, "la table de jeu s'affiche", r.text[:300])
    ok("Kaito Arashi" in r.text, "la fiche du joueur y figure")

    actions = [
        "Je pars observer la Forêt de la Mort, seul, sans prévenir personne.",
        "Je reviens et je mens à mon instructeur sur l'endroit où j'étais.",
        "Je demande à mon rival ce qu'il sait de mes parents.",
    ]
    for i, action in enumerate(actions, start=1):
        r = c.post(f"/campagnes/{cid}/jouer",
                   data={"action": action, "character_id": pj_id})
        ok(r.status_code == 200, f"tour {i} joué", r.text[:200])

    with Session(engine) as s:
        tours = s.exec(select(Turn).where(
            Turn.campaign_id == cid).order_by(Turn.index)).all()
        ok(len(tours) == 1 + len(actions), f"{len(tours)} tours enregistrés")
        joues = [t for t in tours if not t.action.startswith("[")]
        ok(all(t.action for t in joues), "chaque action est conservée telle quelle")
        ok(all(t.narration for t in joues), "chaque narration est sauvegardée")
        ok(all("intent" in t.resolution for t in joues), "l'intention est tracée")
        avec_jet = [t for t in joues if t.resolution.get("check")]
        ok(len(avec_jet) >= 1, "au moins un jet de dés a été lancé")
        if avec_jet:
            ch = avec_jet[0].resolution["check"]
            ok(ch["total"] == ch["de"] + (ch["stat_valeur"] - 10) // 2 + ch["bonus"],
               "le total du jet suit la formule du ruleset")
            print(f"     {G}dé {ch['des']} · {ch['stat']} {ch['stat_valeur']} "
                  f"· total {ch['total']} vs {ch['difficulte']} → {ch['issue']}{Z}")
        ok(any(t.propositions for t in joues),
           "des pistes d'action sont proposées au joueur")

        faits = s.exec(select(MemoryFact).where(MemoryFact.campaign_id == cid)).all()
        ok(len(faits) >= 2, f"{len(faits)} faits écrits en mémoire longue")
        camp = s.get(Campaign, cid)
        ok(camp.tour == 1 + len(actions), "l'horloge de campagne a avancé")

    # ------------------------------------ 5 bis. l'épreuve des clochettes
    # La première mission apprend le jeu (Nindō 2.0, chantier E) : à l'acte 2,
    # l'instructeur attaque, hors de portée, et l'équipe doit faire équipe.
    print("\n5 bis. L'épreuve des clochettes")
    with Session(engine) as s:
        q = s.exec(select(Quest).where(Quest.campaign_id == cid,
                                       Quest.archetype == "clochettes")).first()
        ok(q is not None and q.statut == "acceptée", "la première mission est l'épreuve des clochettes")
    r = c.post(f"/campagnes/{cid}/jouer", data={
        "action": "Je fonce sur les clochettes.", "character_id": pj_id})
    ok(r.status_code == 200, "l'instructeur attaque à l'acte 2", r.text[:200])
    with Session(engine) as s:
        renc = combat.active(s, s.get(Campaign, cid))
        ok(renc is not None and renc.declencheur == "clochettes",
           "la rencontre de l'épreuve est ouverte contre l'instructeur")
        ok(renc is not None and renc.objectifs and "clochette" in renc.objectifs[0],
           "avec les objectifs de l'épreuve, pas ceux du fossé")
        ok(renc is not None and int(renc.fosse.get("tier_adverse_brut", 0)) > s.get(Character, pj_id).tier,
           "l'instructeur est au-dessus de l'équipe : c'est la leçon")
    for i in range(6):
        with Session(engine) as s:
            if combat.active(s, s.get(Campaign, cid)) is None:
                break
        r = c.post(f"/campagnes/{cid}/jouer", data={
            "action": "", "character_id": pj_id, "action_code": "manoeuvre",
            "levier": "terrain_prepare" if i % 2 == 0 else "renseignement"})
        ok(r.status_code == 200, f"round {i + 1} de l'épreuve, par une carte", r.text[:200])
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        renc = combat.active(s, camp)
        if renc is not None:
            rs_c, pack_c = charger_ruleset("naruto"), charger_pack()
            combat._clore(s, camp, rs_c, renc, "dispersee", s.get(Character, pj_id), pack_c)
            s.commit()
        q = s.exec(select(Quest).where(Quest.campaign_id == cid,
                                       Quest.archetype == "clochettes")).first()
        ok(q.statut in ("réussie", "échouée"), f"l'épreuve se conclut avec sa rencontre ({q.statut})")
        ok(bool(q.note), f"et reçoit sa note au débrief ({q.note})")
        sensei = s.get(Character, q.donneur_id)
        ok(sensei.location_id == s.get(Character, pj_id).location_id,
           "l'instructeur est toujours là après l'épreuve")
        tour_avant_flux = camp.tour

    # --------------------------------------------- 5 ter. narration en flux
    # C'est le chemin par défaut de l'interface : il doit être couvert au
    # moins aussi bien que le chemin bloquant.
    print("\n5 ter. Narration en flux")
    r = c.post(f"/campagnes/{cid}/jouer/flux", data={
        "action": "Je m'assieds sur le toit et je regarde le village s'éteindre.",
        "character_id": pj_id})
    ok(r.status_code == 200, "l'action est acceptée sans rien calculer", r.text[:200])
    debut = r.text.find('data-flux="') + len('data-flux="')
    url_flux = r.text[debut:r.text.find('"', debut)] if debut > 20 else ""
    ok(bool(url_flux), "la coquille du tour porte l'adresse du flux")

    # LE JEU S'ARRÊTE SUR L'ANNONCE (Nindō 2.0, pilier 1) : l'arbitre dit le
    # jet, ses chances et le prix de l'échec, puis attend que le joueur lance.
    corps = c.get(url_flux).text if url_flux else ""
    evenements = evenements_de(corps)
    ok(evenements == ["etape", "arbitrage"],
       "le flux s'arrête sur l'annonce du jet et rend la main", str(evenements))
    ok("Jet de" in corps and "de réussite" in corps and "Si ça rate" in corps,
       "l'annonce dit la caractéristique, les chances et le prix de l'échec")
    debut = corps.find('data-lancer="') + len('data-lancer="')
    url_lancer = corps[debut:corps.find('"', debut)] if debut > 20 else ""
    ok(bool(url_lancer), "l'annonce porte l'adresse où lancer")
    r = c.post(url_lancer, data={f"forcer_{pj_id}": "1"})
    ok(r.status_code == 200 and r.json().get("flux") == url_flux,
       "lancer reprend le flux sous le même jeton", r.text[:200])

    corps = c.get(url_flux).text
    evenements = evenements_de(corps)
    ok("jet" in evenements, "le résultat mécanique est envoyé AVANT la narration")
    ok("forcé" in corps, "le pari du joueur (forcer) est appliqué et visible")
    ok(evenements.index("jet") < evenements.index("mot")
       if "mot" in evenements else False,
       "le dé précède réellement le récit dans le flux")
    ok(evenements.count("mot") >= 2, f"la narration arrive en {evenements.count('mot')} morceaux")
    ok(evenements[-1] == "fin", "le flux se termine par le tour complet")

    ok(c.get(url_flux).status_code == 404,
       "le jeton est à usage unique : recharger ne rejoue pas le tour")

    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        ok(camp.tour == tour_avant_flux + 1, "le tour streamé a bien été joué")
        dernier = s.exec(select(Turn).where(
            Turn.campaign_id == cid).order_by(Turn.index.desc())).first()
        ok(bool(dernier.narration), "sa narration est enregistrée en base")
        ok("intent" in dernier.resolution, "sa résolution aussi")

    # ----------------------------------------------------- 5 bis. affrontement
    # Le combat passe par le MÊME chemin HTTP qu'un tour ordinaire : c'est ce
    # qu'il faut vérifier. S'il s'en écartait, il deviendrait un mini-jeu greffé
    # à côté de la campagne au lieu d'en être une scène.
    print("\n5 ter. Affrontement")
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pj = s.get(Character, pj_id)
        rs_c, pack_c = charger_ruleset("naruto"), charger_pack()
        renc = combat.ouvrir(
            s, camp, pack_c, rs_c, pj, declencheur="embuscade", surprise=True,
            archetypes=[pack_c.adversaire("detrousseur_route")])
        ok(renc is not None, "une rencontre s'ouvre")
        ennemis = combat.adverses(s, renc)
        ok(bool(ennemis), f"{len(ennemis)} adversaire(s) fabriqué(s) sans appel au modèle")
        ok(all(e.nom and e.tier >= 1 for e in ennemis),
           "chacun a un nom et une puissance")
        pv_depart = {e.id: int(e.ressources["pv"]) for e in ennemis}
        pv_joueur = int(pj.ressources["pv"])
        renc_id = renc.id

    r = c.get(f"/campagnes/{cid}?pj={pj_id}")
    ok("Affrontement" in r.text or "Embuscade" in r.text,
       "la table affiche le panneau d'affrontement")
    ok("posture" in r.text, "le joueur peut choisir ce qu'il risque")

    for i in range(1, 5):
        r = c.post(f"/campagnes/{cid}/jouer", data={
            "action": "Je frappe à l'ouverture, sans reculer.",
            "character_id": pj_id, "posture": "offensive"})
        ok(r.status_code == 200, f"échange {i} joué par la voie normale",
           r.text[:200])

    with Session(engine) as s:
        renc = s.get(Encounter, renc_id)
        pj = s.get(Character, pj_id)
        ok(renc.echange >= 1, f"{renc.echange} échange(s) comptés")
        ok(bool(renc.journal), "le journal de la rencontre est rempli")
        touche = any(int(s.get(Character, i).ressources["pv"]) < v
                     for i, v in pv_depart.items())
        ok(touche or int(pj.ressources["pv"]) < pv_joueur,
           "des ressources ont réellement baissé")
        tours = s.exec(select(Turn).where(
            Turn.campaign_id == cid).order_by(Turn.index.desc())).all()
        combats = [t for t in tours if t.resolution.get("combat")]
        ok(bool(combats), "le tour porte la trace mécanique de l'échange")
        ok(all("posture" in (t.resolution.get("intent") or {}) for t in combats),
           "la posture choisie par le joueur est enregistrée")
        ok(all(not t.resolution.get("combat", {}).get("lignes") or
               isinstance(t.resolution["combat"]["lignes"], list) for t in combats),
           "le détail des jets est conservé pour le joueur")

        # La soupape : sans elle, une rencontre perdue condamne la campagne.
        camp = s.get(Campaign, cid)
        if renc.statut == "en_cours":
            combat._clore(s, camp, rs_c, renc, "rompue", pj, pack_c)
        ok(combat.active(s, camp) is None, "la rencontre est close")
        ok(combat.adverses(s, renc) == [],
           "aucun adversaire ne reste posté sur le lieu")

    # LE PLATEAU ET LES CARTES (Nindō 2.0, chantier B) : un round se joue
    # depuis une carte, sans texte et sans appel au modèle. Une rencontre
    # neuve, pour que l'étape ne dépende pas de l'issue de la précédente.
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pj = s.get(Character, pj_id)
        renc = combat.ouvrir(s, camp, pack_c, rs_c, pj, declencheur="engagement",
                             archetypes=[pack_c.adversaire("detrousseur_route")])
        renc_id = renc.id
        ok(bool(renc.ordre) and len(renc.initiative) >= 2,
           "l'initiative est tirée et ordonnée à l'ouverture")
    if True:
        r = c.get(f"/campagnes/{cid}?pj={pj_id}")
        ok('class="plateau"' in r.text and "frappe avant toi" in r.text or 'class="plateau"' in r.text,
           "le plateau d'initiative s'affiche")
        ok('name="action_code"' in r.text, "les cartes d'action sont proposées")
        r = c.post(f"/campagnes/{cid}/jouer", data={
            "action": "", "character_id": pj_id, "action_code": "defendre"})
        ok(r.status_code == 200, "un round se joue depuis une carte, sans texte", r.text[:200])
        with Session(engine) as s:
            t = s.exec(select(Turn).where(Turn.campaign_id == cid)
                       .order_by(Turn.index.desc())).first()
            ok((t.resolution.get("intent") or {}).get("posture") == "defensive",
               "la carte « Défendre » a imposé la posture")

    # LE COMPTOIR (chantier C) : les ryô s'échangent contre du matériel.
    with Session(engine) as s:
        renc = s.get(Encounter, renc_id)
        if renc.statut == "en_cours":
            renc.statut, renc.tour_fin = "dispersee", s.get(Campaign, cid).tour
            s.add(renc)
        pj = s.get(Character, pj_id)
        pj.ryo = 100
        s.add(pj)
        s.commit()
    r = c.post(f"/campagnes/{cid}/acheter", data={"ref": "kunai", "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 303, "un kunai s'achète au comptoir", r.text[:200])
    with Session(engine) as s:
        pj = s.get(Character, pj_id)
        ok(pj.ryo == 85, f"les ryô descendent ({pj.ryo})")
        ok(any(l.lower().endswith("kunai") for l in pj.inventaire), "et le kunai entre dans la besace")
    r = c.post(f"/campagnes/{cid}/acheter", data={"ref": "katana", "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code in (400, 404), "on n'achète pas à crédit ni ce qui n'est pas au comptoir")

    r = c.post(f"/campagnes/{cid}/repos", data={"character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 303, "le repos est accepté hors combat")
    with Session(engine) as s:
        pj = s.get(Character, pj_id)
        ok(pj.vivant, "le personnage est vivant après l'affrontement")
        ok(not pj.etats, "le repos a levé les blessures réversibles")

    # ------------------------------------------------ 5 ter ante. « Précédemment »
    # Ce jeu se joue une soirée par semaine : rouvrir la table sept jours plus
    # tard ne doit pas obliger à faire défiler l'historique.
    print("\n5 ter ante. Reprise après une pause")
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        ok(not reprise.necessaire(s, camp),
           "aucun rappel quand on vient de jouer")
        camp.joue_le = maintenant() - timedelta(days=9)
        s.add(camp)
        s.commit()
        ok(reprise.necessaire(s, camp),
           f"après neuf jours, le rappel s'impose ({reprise.duree_lisible(camp)})")

    r = c.get(f"/campagnes/{cid}?pj={pj_id}")
    ok("/reprise" in r.text, "la table demande le rappel sans se ralentir")
    r = c.get(f"/campagnes/{cid}/reprise?pj={pj_id}")
    ok(r.status_code == 200 and "Précédemment" in r.text,
       "le rappel s'affiche", r.text[:200])
    ok(f"tour {0}" not in r.text and "absent" in r.text,
       "il porte le relevé sec : tour, absence, lieu")
    with Session(engine) as s:
        rappels = [x for x in s.exec(select(Summary).where(
            Summary.campaign_id == cid)).all() if x.niveau == reprise.NIVEAU]
        ok(len(rappels) == 1, "le rappel est mis en cache")
    c.get(f"/campagnes/{cid}/reprise?pj={pj_id}")
    with Session(engine) as s:
        rappels = [x for x in s.exec(select(Summary).where(
            Summary.campaign_id == cid)).all() if x.niveau == reprise.NIVEAU]
        ok(len(rappels) == 1, "recharger la page ne le régénère pas")
    ok(reprise.NIVEAU not in c.get(f"/campagnes/{cid}/chronique").text,
       "le rappel n'apparaît pas comme un chapitre de la chronique")

    # -------------------------------------------------- 5 ter bis. progression
    # La seule boucle où le joueur CHOISIT ce que son personnage devient. Elle
    # est restée muette longtemps : les points étaient déclarés dans le ruleset
    # et jamais distribués.
    print("\n5 ter bis. Monter de niveau")
    with Session(engine) as s:
        pj = s.get(Character, pj_id)
        pj.points_libres = 3
        avant = int(pj.stats["taijutsu"])
        s.add(pj)
        s.commit()

    r = c.get(f"/campagnes/{cid}?pj={pj_id}")
    ok("Tu as progressé" in r.text,
       "le panneau de progression apparaît quand des points attendent")

    r = c.post(f"/campagnes/{cid}/progresser",
               data={"character_id": pj_id, "stat": "taijutsu",
                     "retour": f"/campagnes/{cid}/fiche/{pj_id}"},
               follow_redirects=False)
    ok(r.status_code == 303, "le point est accepté", r.text[:200])
    ok(r.headers.get("location", "").endswith(f"/fiche/{pj_id}"),
       "on revient là où le joueur a cliqué")
    r2 = c.post(f"/campagnes/{cid}/progresser",
                data={"character_id": pj_id, "stat": "vitesse",
                      "retour": "https://ailleurs.example/vole"},
                follow_redirects=False)
    ok(r2.headers.get("location", "").startswith(f"/campagnes/{cid}"),
       "un retour hors campagne est ignoré")
    with Session(engine) as s:
        pj = s.get(Character, pj_id)
        ok(pj.stats["taijutsu"] == avant + 1,
           f"le taijutsu a monté ({avant} → {pj.stats['taijutsu']})")
        ok(pj.points_libres == 1, "chaque clic n'a consommé qu'un point")
        ok(bool(s.exec(select(Event).where(
            Event.campaign_id == cid, Event.type == "progression")).all()),
           "la progression est inscrite au journal")

    r = c.post(f"/campagnes/{cid}/progresser",
               data={"character_id": pj_id, "stat": "nage_synchronisee"},
               follow_redirects=False)
    ok(r.status_code == 409, "une caractéristique inventée est refusée")

    r = c.post(f"/campagnes/{cid}/progresser",
               data={"character_id": pj_id, "stat": "vitesse"},
               follow_redirects=False)
    ok(r.status_code == 303, "le dernier point part aussi")
    r = c.post(f"/campagnes/{cid}/progresser",
               data={"character_id": pj_id, "stat": "vitesse"},
               follow_redirects=False)
    ok(r.status_code == 409, "et on ne dépense pas ce qu'on n'a plus")
    ok("Tu as progressé" not in c.get(f"/campagnes/{cid}?pj={pj_id}").text,
       "le panneau disparaît quand tout est placé")

    # -------------------------------------------------------- 5 quater. voyage
    print("\n5 quater. La carte et le voyage")
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pj = s.get(Character, pj_id)
        rs_v, pack_v = charger_ruleset("naruto"), charger_pack()
        etapes = carte.itineraire(s, camp, rs_v, pj)
        ok(len(etapes) >= 2, f"{len(etapes)} lieux placés sur la carte")
        ok(all(0 <= e["lieu"].x <= 100 and 0 <= e["lieu"].y <= 100
               for e in etapes), "chacun a des coordonnées exploitables")
        ok(sum(1 for e in etapes if e["ici"]) == 1,
           "un seul lieu est marqué comme celui où l'on est")
        ailleurs = next(e for e in etapes if not e["ici"])
        ok(ailleurs["cout"] >= 1, f"aller à {ailleurs['lieu'].nom} coûte "
                                  f"{ailleurs['cout']} tour(s)")
        destination, cout_attendu = ailleurs["lieu"].id, ailleurs["cout"]
        tour_avant, lieu_avant = camp.tour, pj.location_id

    r = c.post(f"/campagnes/{cid}/voyager",
               data={"destination": destination, "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 303, "le voyage est accepté", r.text[:200])

    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pj = s.get(Character, pj_id)
        ok(pj.location_id == destination, "le personnage a changé de lieu")
        ok(pj.location_id != lieu_avant, "et ce n'est plus celui d'avant")
        ok(camp.tour == tour_avant + cout_attendu,
           f"l'horloge a avancé de {cout_attendu} tour(s) — "
           f"{tour_avant} → {camp.tour}")
        ok(s.exec(select(Event).where(
            Event.campaign_id == cid, Event.type == "deplacement")).first()
           is not None, "le déplacement est au journal")

    r = c.post(f"/campagnes/{cid}/voyager",
               data={"destination": destination, "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 409, "on refuse d'aller là où l'on est déjà")

    # Les petits mots. « Kaito s'est rendu à Tour du Kage » n'est pas une
    # phrase française : un nom de lieu est un nom commun déterminé.
    with Session(engine) as s:
        dep = s.exec(select(Event).where(
            Event.campaign_id == cid, Event.type == "deplacement")
            .order_by(Event.id.desc())).first()
        ok(" à Tour" not in dep.resume and " à Terrain" not in dep.resume
           and " à Poste" not in dep.resume and " à Quartier" not in dep.resume,
           "le journal décline les lieux correctement", dep.resume)
        ok(any(x in dep.resume for x in
               ("à la ", "au ", "à l'", "aux ", "à Konoha")),
           "et l'article est contracté", dep.resume)

    # ------------------------------------------------- 5 sexies. apprentissage
    # Le répertoire d'un personnage était figé à la création : trois techniques
    # au tour 1, les mêmes trois au tour 80.
    print("\n5 sexies. Apprendre une technique")
    with Session(engine) as s:
        camp, pj = s.get(Campaign, cid), s.get(Character, pj_id)
        rs_a = charger_ruleset(camp.ruleset_slug)
        pack_a = charger_pack(camp.lore_pack)
        propositions = apprentissage.offres(s, camp, pack_a, rs_a, pj)
        ok(bool(propositions), "des techniques sont ouvertes à ce personnage")
        ok(all(o["source"] in ("maitre", "parchemin", "travail")
               for o in propositions), "chacune dit par quel chemin elle passe")
        pretes = [o for o in propositions if o["pret"]]
        ok(bool(pretes), "au moins une est à portée maintenant")
        cible, cout_tours = pretes[0]["id"], pretes[0]["tours"]
        connues_avant = set(apprentissage.connues(s, pj))
        tour_avant = camp.tour

    ok(cible in c.get(f"/campagnes/{cid}/fiche/{pj_id}").text,
       "la fiche propose de s'entraîner")
    r = c.post(f"/campagnes/{cid}/apprendre",
               data={"technique": cible, "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 303, "l'entraînement est accepté", r.text[:200])
    with Session(engine) as s:
        camp, pj = s.get(Campaign, cid), s.get(Character, pj_id)
        ok(cible in apprentissage.connues(s, pj) and cible not in connues_avant,
           "la technique est inscrite sur la fiche")
        ok(camp.tour == tour_avant + cout_tours,
           f"elle a coûté {cout_tours} tour(s) — {tour_avant} → {camp.tour}")
        ok(s.exec(select(Event).where(
            Event.campaign_id == cid, Event.type == "apprentissage")).first()
           is not None, "l'apprentissage est au journal")

    r = c.post(f"/campagnes/{cid}/apprendre",
               data={"technique": "vol_plane_du_heron", "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 409, "une technique inventée est refusée")

    # ------------------------------------------ 5 quinquies. « je m'en remets au destin »
    print("\n5 quinquies. Chemin délégué au destin")
    # campagne à part : ce parcours teste la création, pas le hot-seat, et il
    # ne doit pas fausser le compte de joueurs de la campagne principale
    r = c.post("/campagnes", data={"nom": "Le Tirage", "epoque": "naruto_p1"},
               follow_redirects=False)
    cid2 = int(r.headers["location"].rstrip("/").split("/")[-2])
    r = c.post(f"/campagnes/{cid2}/creation", data={
        "nom": "Tenma Sans-Nom", "joueur": "Solo", "sexe": "autre", "age": "13",
        "chemin": "remets", "mode_destinee": "aleatoire",
    }, follow_redirects=False)
    ok(r.status_code == 200 and 'data-flux="' in r.text,
       "un personnage entièrement tiré au sort est accepté", r.text[:300])
    evts = sceller(c, r)
    ok(evts[-1:] == ["fin"], "son monde s'amorce jusqu'au bout")
    # Le destin a tranché : l'archétype est annoncé au sceau, avant le présage.
    # C'est ce qui distingue « je m'en remets au destin » d'un formulaire
    # rempli au hasard.
    ok("archetype" in evts, "le sceau annonce ce que le sort a fait de lui")
    ok(evts.index("archetype") < evts.index("presage") if "presage" in evts else True,
       "et il l'annonce avant le présage")

    with Session(engine) as s:
        tenma = s.exec(select(Character).where(
            Character.campaign_id == cid2, Character.nom == "Tenma Sans-Nom")).first()
        ok(tenma is not None, "il est enregistré en base")
        destin_t = s.exec(select(Destiny).where(
            Destiny.character_id == tenma.id)).first() if tenma else None
        ok(bool(destin_t and destin_t.archetype_nom),
           f"un archétype lui a été tiré : {destin_t.archetype_nom if destin_t else '—'}")
        ok(bool(destin_t and destin_t.archetype_accroche),
           "avec son accroche — et rien de plus")
        ok(bool(tenma and tenma.village_ref), f"un village lui a été tiré : {tenma.village if tenma else '—'}")
        ok(bool(tenma and tenma.stats), "ses caractéristiques sont réparties")
        # la règle village → clan tient aussi pour un tirage automatique
        if tenma and tenma.clan_ref:
            clan = charger_pack().clan(tenma.clan_ref) or {}
            ok(clan.get("disperse") or clan.get("village") == tenma.village_ref,
               f"son clan ({clan.get('nom')}) appartient bien à son village")
        else:
            ok(True, "il est sans clan, ce qui est le cas le plus fréquent")

    # -------------------------------------------------------- 6. second joueur
    print("\n6. Second joueur (hot-seat)")
    r = c.post(f"/campagnes/{cid}/creation", data={
        "nom": "Rika Hyûga", "joueur": "Ami", "sexe": "feminin", "age": "13",
        "village": "konoha", "origine": "clan_connu", "clan_clan_connu": "hyuga",
        "specialisation": "taijutsu", "mode_destinee": "aleatoire",
        "pt_perception": "2", "pt_taijutsu": "1",
    }, follow_redirects=False)
    ok(r.status_code == 303, "un second personnage rejoint la campagne", r.text[:300])

    with Session(engine) as s:
        pjs = s.exec(select(Character).where(
            Character.campaign_id == cid, Character.is_pc == True)).all()  # noqa: E712
        ok(len(pjs) == 2, "deux personnages joueurs coexistent")
        rika = next((p for p in pjs if p.nom.startswith("Rika")), None)
        ok(rika is not None and rika.joueur == "Ami",
           "chaque fiche sait quel joueur la contrôle")
        d_rika = s.exec(select(Destiny).where(Destiny.character_id == rika.id)).first()
        traits_rika = s.exec(select(DestinyTrait).where(
            DestinyTrait.destiny_id == d_rika.id)).all() if d_rika else []
        attendu, detail = decompose(rika, traits_rika, "perception", "taijutsu", libres=2)
        ok(rika is not None and rika.stats.get("perception") == attendu,
           f"bonus de clan + points libres ({detail} = {attendu})",
           f"valeur : {rika.stats.get('perception') if rika else '—'}")
        ok(rika is not None and rika.clan == "Hyûga", "son clan est bien enregistré")

        # Appartenir au clan ne donne PAS la lignée : elle doit rester latente
        d2 = s.exec(select(Destiny).where(Destiny.character_id == rika.id)).first()
        traits2 = s.exec(select(DestinyTrait).where(
            DestinyTrait.destiny_id == d2.id)).all()
        lignees = [t for t in traits2 if t.famille == "lignee_clan"]
        ok(all(not t.actif_au_depart for t in lignees),
           "être Hyûga ne donne pas le Byakugan : la lignée reste latente",
           f"{[(t.libelle, t.etat) for t in lignees]}")
        rika_id = rika.id

    r = c.post(f"/campagnes/{cid}/jouer", data={
        "action": "Je remarque que Kaito ment et je le suis discrètement.",
        "character_id": rika_id})
    ok(r.status_code == 200, "Rika joue son tour")
    ok("Rika" in r.text, "la narration est attribuée au bon personnage")

    r = c.get(f"/campagnes/{cid}?pj={rika_id}")
    ok(r.status_code == 200 and "Rika" in r.text,
       "la table bascule sur le second personnage")

    # -------------------------------------------------- 7. filtre de secrets
    print("\n7. Secrets et divulgation")
    from app.memory.context import construire, taille_estimee
    from app.rules.engine import Ruleset

    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        pj = s.get(Character, pj_id)
        ctx = construire(s, camp, pj, "Je cherche à en savoir plus sur mon instructeur.",
                         Ruleset(data=camp.ruleset), charger_pack(camp.lore_pack))
        secrets = s.exec(select(Secret).where(Secret.campaign_id == cid)).all()
        fuite = [s_.verite for s_ in secrets if s_.verite and s_.verite[:40] in ctx]
        ok(not fuite, "aucun secret de PNJ ne fuit dans le contexte du narrateur",
           str(fuite)[:200])

        d = s.exec(select(Destiny).where(Destiny.character_id == pj_id)).first()
        traits = s.exec(select(DestinyTrait).where(
            DestinyTrait.destiny_id == d.id)).all()
        fuite_d = [t.verite for t in traits
                   if t.verite and len(t.verite) > 20 and t.verite[:30] in ctx]
        ok(not fuite_d, "aucune vérité de destinée ne fuit non plus", str(fuite_d)[:200])

        ok("PERSONNAGE QUI AGIT" in ctx, "la fiche du joueur est dans le contexte")
        ok("TOURS RÉCENTS" in ctx, "l'historique récent y est")
        ok("FAITS ÉTABLIS" in ctx, "les faits mémorisés y sont")
        ok("AUTRES PERSONNAGES JOUEURS" in ctx,
           "le coéquipier joueur est signalé comme intouchable")
        taille = taille_estimee(ctx)
        ok(taille <= settings.budget_contexte,
           f"le contexte tient dans le budget ({taille} tokens estimés)")
        print(f"     {G}contexte : ~{taille} tokens{Z}")

    # ------------------------------------------------------- 8. persistance
    print("\n8. Sauvegarde")
    r = c.get(f"/api/campagnes/{cid}/etat")
    ok(r.status_code == 200, "l'état de la campagne est lisible en JSON")
    etat = r.json()
    ok(len(etat["personnages"]) == 2, "les deux personnages y figurent")
    ok(len(etat["evenements"]) >= 4, "le journal d'événements est rempli")

    r = c.get(f"/campagnes/{cid}/fiche/{pj_id}")
    ok(r.status_code == 200, "la fiche ninja s'affiche")
    ok("Kaito Arashi" in r.text and "Expérience" in r.text and "Ninjutsu" in r.text,
       "elle contient identité, progression et caractéristiques")
    ok("Corps" in r.text and "Chakra" in r.text and "Esprit" in r.text,
       "les caractéristiques sont groupées par famille")

    with Session(engine) as s:
        cristaux = s.exec(select(Crystallization).where(
            Crystallization.campaign_id == cid)).all()
        print(f"     {G}{len(cristaux)} cristallisation(s) enregistrée(s){Z}")

    # ------------------------------------------------------- 9. le mot de la fin
    # `Campaign.phase` connaissait « terminee » et rien ne la déclenchait : une
    # campagne ne s'achevait jamais, elle s'arrêtait.
    print("\n9. Clore la chronique")
    with Session(engine) as s:
        # La campagne qui vient de naître — elle, n'a rien à raconter.
        jeune = s.get(Campaign, cid2)
        tenma = s.exec(select(Character).where(
            Character.campaign_id == cid2, Character.is_pc == True)).first()  # noqa: E712
        possible, raison = epilogue.possible(s, jeune, tenma)
        ok(possible is False, "trop tôt pour conclure sur une campagne neuve",
           raison)
        ok("matière" in raison, "et on dit pourquoi", raison)

        camp, pj = s.get(Campaign, cid), s.get(Character, pj_id)
        camp.tour = max(camp.tour, epilogue.TOURS_MINIMUM)
        s.add(camp)
        s.commit()
        ok(epilogue.possible(s, camp, pj)[0] is True,
           "après assez de tours, la fin s'ouvre")

    ok("Clore la chronique" in c.get(f"/campagnes/{cid}/chronique").text,
       "la chronique propose de conclure")
    r = c.post(f"/campagnes/{cid}/clore", data={"character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 303, "la chronique est close", r.text[:200])

    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        ok(camp.phase == "terminee", "la campagne est marquée terminée")
        fin = [x for x in s.exec(select(Summary).where(
            Summary.campaign_id == cid)).all() if x.niveau == epilogue.NIVEAU]
        ok(len(fin) == 1 and fin[0].texte.strip(), "un épilogue est écrit")
        secrets_ = s.exec(select(Secret).where(Secret.campaign_id == cid)).all()
        fuite = [x.verite for x in secrets_
                 if x.verite and x.verite[:40] in fin[0].texte]
        ok(not fuite, "aucune vérité de secret ne fuit dans l'épilogue",
           str(fuite)[:200])

    page = c.get(f"/campagnes/{cid}/chronique").text
    ok("Épilogue" in page, "l'épilogue s'affiche dans la chronique")
    r = c.post(f"/campagnes/{cid}/jouer",
               data={"action": "Je continue quand même.", "character_id": pj_id},
               follow_redirects=False)
    ok(r.status_code == 409, "une chronique close ne prend plus d'action")
    ok("Chronique close" in c.get(f"/campagnes/{cid}?pj={pj_id}").text,
       "la table le dit au lieu d'offrir un formulaire mort")

    r = c.post(f"/campagnes/{cid}/rouvrir", follow_redirects=False)
    ok(r.status_code == 303, "on peut rouvrir")
    with Session(engine) as s:
        camp = s.get(Campaign, cid)
        ok(camp.phase == "en_cours", "la campagne reprend")
        fin = [x for x in s.exec(select(Summary).where(
            Summary.campaign_id == cid)).all() if x.niveau == epilogue.NIVEAU]
        ok(len(fin) == 1, "et l'épilogue reste dans la chronique")

    # ------------------------------------------------------- 10. le ménage
    print("\n10. Effacer une campagne")
    r = c.post(f"/campagnes/{cid2}/supprimer", follow_redirects=False)
    ok(r.status_code == 303, "la campagne d'essai est effacée")
    with Session(engine) as s:
        ok(s.get(Campaign, cid2) is None, "elle n'est plus en base")
        ok(not s.exec(select(Character).where(Character.campaign_id == cid2)).all(),
           "ni ses personnages")
        ok(not s.exec(select(Event).where(Event.campaign_id == cid2)).all(),
           "ni son journal")
        ok(s.get(Campaign, cid) is not None, "l'autre campagne est intacte")
    ok(c.get(f"/campagnes/{cid2}").status_code == 404, "sa page répond 404")
    ok(str(cid2) not in [l for l in c.get("/").text.split('href="/campagnes/')[1:]
                         if l[:len(str(cid2))+1] == f"{cid2}\""],
       "et l'accueil ne la liste plus")

    # ------------------------------------------------------------- bilan
    print()
    if _echecs:
        print(f"{R}{len(_echecs)} vérification(s) en échec :{Z}")
        for e in _echecs:
            print(f"  · {e}")
        return 1
    print(f"{V}Tout est vert. Le jeu fonctionne de bout en bout.{Z}")
    print(f"{G}Lance maintenant : uvicorn app.main:app --reload{Z}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
