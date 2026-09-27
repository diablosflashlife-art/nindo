"""La montée de niveau, et le fait qu'elle change quelque chose.

`progression.points_par_niveau: 2` était écrit dans le ruleset depuis le
premier jour et personne ne les distribuait : monter de niveau incrémentait un
compteur. Ces tests verrouillent les trois choses qui peuvent redevenir
muettes — le crédit, le plafond, et le recalcul de puissance derrière.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.engine import progression
from app.models import Campaign, Character, Event
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture()
def partie(rs):
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=rs.data, tour=4,
                        phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        pj = Character(campaign_id=camp.id, nom="Kaito", is_pc=True,
                       stats=rs.stats_defaut(), ressources=rs.ressources_defaut(),
                       niveau=1, tier=2)
        session.add(pj)
        session.commit()
        session.refresh(pj)
        yield session, camp, pj


# ==========================================================================
# LE CRÉDIT
# ==========================================================================
def test_le_ruleset_declare_un_plafond(rs):
    """Sans plafond, la pente optimale est de tout verser dans une seule case."""
    assert progression.plafond(rs) > max(
        c.get("default", 10) for c in rs.stats.values())


def test_monter_de_niveau_credite_des_points(rs, partie):
    _, _, pj = partie
    assert pj.points_libres == 0
    assert progression.crediter(rs, pj, 2) == 2 * progression.par_niveau(rs)


def test_les_points_s_accumulent_tant_qu_on_ne_choisit_pas(rs, partie):
    """Un joueur qui garde ses points en réserve ne doit pas les perdre au
    niveau suivant : on ne dépense jamais à sa place."""
    _, _, pj = partie
    progression.crediter(rs, pj, 1)
    progression.crediter(rs, pj, 1)
    assert pj.points_libres == 2 * progression.par_niveau(rs)


def test_aucun_point_sans_niveau_gagne(rs, partie):
    _, _, pj = partie
    progression.crediter(rs, pj, 0)
    assert pj.points_libres == 0


# ==========================================================================
# LA DÉPENSE
# ==========================================================================
def test_depenser_monte_la_caracteristique_et_consomme_le_point(rs, partie):
    session, camp, pj = partie
    progression.crediter(rs, pj, 1)
    avant = pj.stats["taijutsu"]
    effets = progression.depenser(session, camp, rs, pj, "taijutsu")
    assert pj.stats["taijutsu"] == avant + 1
    assert pj.points_libres == progression.par_niveau(rs) - 1
    assert effets and str(avant + 1) in effets[0]


def test_la_depense_laisse_une_trace_dans_le_journal(rs, partie):
    session, camp, pj = partie
    progression.crediter(rs, pj, 1)
    progression.depenser(session, camp, rs, pj, "vitesse")
    evs = session.exec(select(Event).where(Event.type == "progression")).all()
    assert len(evs) == 1
    assert "Kaito" in evs[0].resume and evs[0].tour == camp.tour


def test_on_ne_depense_pas_ce_qu_on_n_a_pas(rs, partie):
    session, camp, pj = partie
    with pytest.raises(progression.ProgressionRefusee):
        progression.depenser(session, camp, rs, pj, "taijutsu")


def test_une_caracteristique_inconnue_est_refusee(rs, partie):
    session, camp, pj = partie
    progression.crediter(rs, pj, 1)
    with pytest.raises(progression.ProgressionRefusee):
        progression.depenser(session, camp, rs, pj, "charisme_de_pirate")
    assert pj.points_libres == progression.par_niveau(rs), \
        "un refus ne doit pas consommer de point"


def test_le_plafond_tient(rs, partie):
    session, camp, pj = partie
    maxi = progression.plafond(rs)
    pj.stats = {**pj.stats, "genjutsu": maxi}
    progression.crediter(rs, pj, 1)
    with pytest.raises(progression.ProgressionRefusee):
        progression.depenser(session, camp, rs, pj, "genjutsu")
    assert pj.stats["genjutsu"] == maxi
    assert pj.points_libres == progression.par_niveau(rs)


def test_une_demande_qui_depasse_le_plafond_est_rognee(rs, partie):
    """Demander trois points quand il n'en reste qu'un à gagner ne doit ni
    échouer ni brûler les deux autres."""
    session, camp, pj = partie
    maxi = progression.plafond(rs)
    pj.stats = {**pj.stats, "perception": maxi - 1}
    progression.crediter(rs, pj, 3)
    reserve = pj.points_libres
    progression.depenser(session, camp, rs, pj, "perception", points=3)
    assert pj.stats["perception"] == maxi
    assert pj.points_libres == reserve - 1


def test_la_puissance_est_recalculee_apres_la_depense(rs, partie):
    """Le tier doit bouger au moment du choix, pas au tour suivant."""
    session, camp, pj = partie
    pe_avant = pj.pe
    progression.crediter(rs, pj, 12)
    for _ in range(progression.par_niveau(rs) * 12):
        if pj.stats["chakra"] >= progression.plafond(rs):
            break
        progression.depenser(session, camp, rs, pj, "chakra")
    assert pj.pe > pe_avant, "la puissance effective n'a pas suivi la fiche"


# ==========================================================================
# CE QUE VOIT LE JOUEUR
# ==========================================================================
def test_les_offres_couvrent_toutes_les_caracteristiques(rs, partie):
    _, _, pj = partie
    groupes = progression.offres(rs, pj)
    cles = {s["cle"] for g in groupes for s in g["stats"]}
    assert cles == set(rs.stats)
    assert all(g["label"] for g in groupes), "un groupe sans libellé lisible"


def test_une_caracteristique_au_plafond_reste_affichee(rs, partie):
    """La masquer ferait croire qu'elle a disparu de la fiche."""
    _, _, pj = partie
    pj.stats = {**pj.stats, "social": progression.plafond(rs)}
    trouvee = [s for g in progression.offres(rs, pj) for s in g["stats"]
               if s["cle"] == "social"]
    assert trouvee and trouvee[0]["plein"] is True
