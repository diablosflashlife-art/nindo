"""La carte et le voyage.

Les lieux avaient une région, un danger et un terrain — mais aucune POSITION,
et rien pour aller de l'un à l'autre. Le joueur changeait d'endroit quand la
narration le décidait, sans que cela coûte jamais rien.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import carte
from app.lore.pack import charger as charger_pack
from app.models import Campaign, Character, Location, Quest
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


@pytest.fixture
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=10,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)

        proche = Location(campaign_id=camp.id, nom="Académie", danger=1,
                          x=44, y=58)
        loin = Location(campaign_id=camp.id, nom="Poste frontière du Nord",
                        danger=4, x=58, y=13)
        cache = Location(campaign_id=camp.id, nom="Repaire inconnu", danger=8,
                         x=90, y=90, connu=False)
        for l in (proche, loin, cache):
            session.add(l)
        session.commit()
        for l in (proche, loin, cache):
            session.refresh(l)

        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       stats=rs.stats_defaut(), ressources=rs.ressources_defaut(),
                       location_id=proche.id, tier=2)
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj, proche, loin, cache


def test_un_lieu_ignore_n_est_pas_sur_la_carte(partie, rs):
    """Le monde contient plus que ce que le joueur connaît. La carte ne doit
    pas révéler ce que le jeu garde caché."""
    session, camp, pj, proche, loin, cache = partie
    noms = {e["lieu"].nom for e in carte.itineraire(session, camp, rs, pj)}
    assert "Académie" in noms and "Poste frontière du Nord" in noms
    assert "Repaire inconnu" not in noms


def test_le_cout_croit_avec_la_distance_et_reste_borne(partie, rs):
    session, camp, pj, proche, loin, _ = partie
    assert carte.cout(rs, proche, loin) >= 1
    cfg = rs.data["voyage"]
    # Deux points diamétralement opposés ne doivent pas engloutir une échéance.
    a = Location(campaign_id=camp.id, nom="A", x=0, y=0)
    b = Location(campaign_id=camp.id, nom="B", x=100, y=100)
    assert carte.cout(rs, a, b) == cfg["tours_max"]
    # Et un pas de côté coûte tout de même du temps.
    c = Location(campaign_id=camp.id, nom="C", x=44, y=59)
    assert carte.cout(rs, proche, c) == cfg["tours_min"]


def test_voyager_avance_l_horloge_et_deplace(partie, pack, rs):
    session, camp, pj, proche, loin, _ = partie
    avant = camp.tour
    attendu = carte.cout(rs, proche, loin)

    effets = carte.voyager(session, camp, pack, rs, pj, loin.id)

    assert pj.location_id == loin.id
    assert camp.tour == avant + attendu
    assert any("Voyage" in e for e in effets)


def test_le_temps_du_voyage_fait_tomber_les_echeances(partie, pack, rs):
    """Les tours de marche comptent. Sans ça, voyager serait le moyen le plus
    simple de ne jamais rater un délai."""
    session, camp, pj, proche, loin, _ = partie
    q = Quest(campaign_id=camp.id, titre="Livraison", statut="acceptée",
              echeance_tour=camp.tour + 1, description="…")
    session.add(q)
    session.commit()

    carte.voyager(session, camp, pack, rs, pj, loin.id)
    session.refresh(q)
    assert q.statut == "échouée"


def test_on_ne_voyage_pas_vers_un_lieu_inconnu(partie, pack, rs):
    session, camp, pj, proche, loin, cache = partie
    with pytest.raises(carte.VoyageImpossible):
        carte.voyager(session, camp, pack, rs, pj, cache.id)
    assert pj.location_id == proche.id


def test_on_ne_voyage_pas_vers_l_endroit_ou_l_on_est(partie, pack, rs):
    session, camp, pj, proche, _, _ = partie
    with pytest.raises(carte.VoyageImpossible):
        carte.voyager(session, camp, pack, rs, pj, proche.id)


def test_on_ne_quitte_pas_un_affrontement_en_marchant(partie, pack, rs):
    from app.engine import combat

    session, camp, pj, proche, loin, _ = partie
    combat.ouvrir(session, camp, pack, rs, pj,
                  archetypes=[pack.adversaire("detrousseur_route")])
    with pytest.raises(carte.VoyageImpossible):
        carte.voyager(session, camp, pack, rs, pj, loin.id)
    assert pj.location_id == proche.id


def test_un_lieu_se_decouvre_une_seule_fois(partie, rs):
    """Le champ `connu` existait et valait vrai partout : le monde entier
    était donné d'emblée, et la carte n'avait aucune raison de grandir."""
    session, camp, pj, _, _, cache = partie

    effet = carte.decouvrir(session, camp, cache, "une rumeur au marché")
    session.commit()
    assert cache.connu is True
    assert cache.nom in effet

    # Deux fois ne produit rien : ce n'est plus une découverte.
    assert carte.decouvrir(session, camp, cache, "la même rumeur") == ""

    noms = {e["lieu"].nom for e in carte.itineraire(session, camp, rs, pj)}
    assert "Repaire inconnu" in noms, "le lieu découvert n'est pas sur la carte"


def test_une_mission_fait_decouvrir_son_lieu(partie, rs, pack):
    """On n'apprend pas le monde en le lisant : on l'apprend en y étant
    envoyé."""
    from app.engine import missions

    session, camp, pj, _, _, cache = partie
    assert cache.connu is False

    oss = {"lieu_id": cache.id}
    from app.engine.carte import decouvrir
    decouvrir(session, camp, cache, f"le bureau t'y envoie")
    session.commit()
    assert cache.connu is True


def test_les_campagnes_d_avant_la_carte_sont_replacees(partie, rs):
    """Une sauvegarde commencée avant la carte a des lieux sans coordonnées.
    Leur carte serait vide pour toujours si on ne les posait pas."""
    session, camp, pj, *_ = partie
    nus = [Location(campaign_id=camp.id, nom=n, danger=2, x=0, y=0)
           for n in ("Quartier marchand", "Une ruelle sans nom", "Un autre lieu")]
    for l in nus:
        session.add(l)
    session.commit()

    carte.placer(session, camp)
    for l in nus:
        session.refresh(l)
        assert (l.x or l.y), f"{l.nom} n'a pas été placé"
        assert 0 <= l.x <= 100 and 0 <= l.y <= 100

    # Le nom connu retrouve sa vraie place ; les autres n'empiètent pas.
    marchand = next(l for l in nus if l.nom == "Quartier marchand")
    assert (marchand.x, marchand.y) == carte.PLACES_CONNUES["quartier marchand"]
    positions = [(l.x, l.y) for l in session.exec(
        select(Location).where(Location.campaign_id == camp.id)).all()]
    assert len(positions) == len(set(positions)), "deux lieux au même endroit"
