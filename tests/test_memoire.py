"""Le rappel de la mémoire longue.

Le cas qui a motivé tout ça : le joueur écrit « ce que j'ai juré à Tetsuo » et
le fait enregistré dit « a promis au vieux forgeron de ne jamais révéler
l'origine de la lame ». Aucun mot commun. Le classement par recouvrement
lexical ne le remontait pas, et le maître du jeu ne s'en souvenait pas.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.llm import embeddings as emb
from app.memory.context import rappeler_faits, vectoriser
from app.models import Campaign, MemoryFact
from app.rules.loader import charger


class FauxEmbeddeur:
    """Un sens simulé : chaque texte devient un vecteur de présence de
    THÈMES. Deux phrases qui parlent du même thème se ressemblent, même sans
    partager un mot — c'est exactement la propriété qu'on teste."""

    THEMES = {
        "serment": ["juré", "jurer", "promis", "promesse", "serment", "parole"],
        "forge": ["forgeron", "tetsuo", "lame", "forge", "acier"],
        "mission": ["mission", "escorte", "convoi", "marchand", "rapport"],
        "combat": ["combat", "frappé", "blessé", "sang", "coup"],
    }
    disponible = True

    def vecteurs(self, textes):
        out = []
        for t in textes:
            bas = (t or "").lower()
            out.append([1.0 if any(m in bas for m in mots) else 0.0
                        for mots in self.THEMES.values()])
        return out


@pytest.fixture
def partie():
    moteur = create_engine("sqlite://")
    SQLModel.metadata.create_all(moteur)
    with Session(moteur) as session:
        camp = Campaign(nom="test", graine=1, ruleset=charger("naruto").data,
                        tour=40, phase="en_cours")
        session.add(camp)
        session.commit()
        session.refresh(camp)
        yield session, camp
    emb.reset_embeddeur()


def _fait(session, camp, texte, importance=3, tour=10, nature="fait"):
    f = MemoryFact(campaign_id=camp.id, texte=texte, importance=importance,
                   tour=tour, nature=nature)
    session.add(f)
    session.commit()
    session.refresh(f)
    return f


def test_le_sens_retrouve_ce_que_les_mots_ratent(partie):
    session, camp = partie
    emb.poser(FauxEmbeddeur())

    promesse = _fait(session, camp,
                     "Kaito a promis au vieux forgeron de ne jamais révéler "
                     "l'origine de la lame.", importance=3, tour=8)
    for i, bruit in enumerate([
            "Kaito a escorté un convoi de marchands jusqu'au pont.",
            "Kaito a été blessé au bras pendant un combat de rue.",
            "Kaito a remis son rapport de mission au bureau.",
            "Kaito a dormi à l'auberge du quartier marchand."]):
        _fait(session, camp, bruit, importance=4, tour=30 + i)

    faits = session.exec(__import__("sqlmodel").select(MemoryFact)).all()
    vectoriser(faits)
    session.commit()

    rappel = rappeler_faits(session, camp, "ce que j'ai juré à Tetsuo", limite=3)
    assert promesse.id in [f.id for f in rappel], \
        "la promesse n'est pas remontée alors qu'elle parle exactement de ça"
    assert rappel[0].id == promesse.id, \
        "elle remonte, mais derrière des faits plus récents et hors sujet"


def test_sans_embeddeur_le_rappel_lexical_continue(partie):
    """La dégradation doit être SILENCIEUSE : un modèle absent ne casse rien,
    il rend seulement la mémoire moins fine."""
    session, camp = partie
    emb.poser(emb.AbsentEmbeddeur())

    vise = _fait(session, camp, "Kaito a refusé la mission d'escorte proposée "
                                "par Hiroshi.", importance=4, tour=14)
    _fait(session, camp, "Il pleuvait sur le terrain d'entraînement.",
          importance=1, tour=15)

    faits = session.exec(__import__("sqlmodel").select(MemoryFact)).all()
    vectoriser(faits)                      # ne doit rien poser, ni lever
    assert all(not f.vecteur for f in faits)

    rappel = rappeler_faits(session, camp, "la mission d'escorte", limite=2)
    assert rappel[0].id == vise.id


def test_un_fait_sans_vecteur_n_est_ni_favorise_ni_puni(partie):
    """Une campagne commencée avant les embeddings garde des faits nus. Ils
    doivent rester classables par leur seul mérite lexical."""
    session, camp = partie
    emb.poser(FauxEmbeddeur())

    ancien = _fait(session, camp, "Kaito a juré fidélité au forgeron Tetsuo.",
                   importance=5, tour=3)          # aucun vecteur posé
    recent = _fait(session, camp, "Kaito a acheté des rations.",
                   importance=1, tour=39)
    vectoriser([recent])
    session.commit()

    rappel = rappeler_faits(session, camp, "mon serment à Tetsuo", limite=2)
    assert rappel[0].id == ancien.id


def test_les_secrets_ne_remontent_jamais(partie):
    """Le moteur sème des graines d'intrigue en mémoire longue. Les laisser
    remonter contournerait le filtre de divulgation depuis l'intérieur."""
    session, camp = partie
    emb.poser(FauxEmbeddeur())
    graine = _fait(session, camp,
                   "Le forgeron Tetsuo travaille en réalité pour la Racine.",
                   importance=5, tour=12, nature="secret")
    _fait(session, camp, "Kaito a promis quelque chose à Tetsuo.", tour=12)
    vectoriser(session.exec(__import__("sqlmodel").select(MemoryFact)).all())
    session.commit()

    rappel = rappeler_faits(session, camp, "Tetsuo", limite=8)
    assert graine.id not in [f.id for f in rappel]


def test_cosinus_ignore_les_vecteurs_absents():
    assert emb.cosinus([], [1.0, 0.0]) == 0.0
    assert emb.cosinus([1.0, 0.0], []) == 0.0
    assert emb.cosinus([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert emb.cosinus([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert emb.cosinus([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
