"""Une sauvegarde de campagne survit aux mises à jour du jeu.

`create_all` crée les TABLES absentes et ne touche jamais à celles qui
existent. Une nouvelle table apparaissait donc toute seule, mais une nouvelle
COLONNE sur une table ancienne faisait planter la partie de quiconque avait
déjà joué : « no such column: memoryfact.vecteur ».
"""
import json

from sqlalchemy import create_engine as creer, inspect, text
from sqlmodel import Session, SQLModel, select


def _base_ancienne(chemin):
    """Une base d'avant : la table des faits sans sa colonne de vecteur."""
    moteur = creer(f"sqlite:///{chemin}")
    with moteur.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE memoryfact (
                id INTEGER PRIMARY KEY,
                campaign_id INTEGER,
                texte TEXT,
                nature VARCHAR,
                importance INTEGER,
                tour INTEGER,
                entites JSON
            )""")
        conn.exec_driver_sql(
            "INSERT INTO memoryfact (id, campaign_id, texte, nature, "
            "importance, tour, entites) VALUES (1, 1, 'Un fait ancien', "
            "'fait', 3, 7, '[]')")
    moteur.dispose()
    return moteur


def test_une_colonne_neuve_arrive_sur_une_base_existante(tmp_path, monkeypatch):
    chemin = tmp_path / "ancienne.db"
    _base_ancienne(chemin)

    import app.db as bd
    moteur = creer(f"sqlite:///{chemin}")
    monkeypatch.setattr(bd, "engine", moteur)

    avant = {c["name"] for c in inspect(moteur).get_columns("memoryfact")}
    assert "vecteur" not in avant, "la base de départ n'était pas une ancienne"

    bd.init_db()

    apres = {c["name"] for c in inspect(moteur).get_columns("memoryfact")}
    assert "vecteur" in apres, "la colonne manquante n'a pas été ajoutée"

    # La ligne existante est lisible ET porte la valeur par défaut du modèle,
    # pas NULL : du code qui attend une liste ne doit pas tomber.
    from app.models import MemoryFact
    with Session(moteur) as s:
        f = s.exec(select(MemoryFact)).first()
        assert f is not None and f.texte == "Un fait ancien"
        assert f.vecteur == []

    with moteur.begin() as conn:
        brut = conn.execute(text("SELECT vecteur FROM memoryfact")).scalar()
    assert json.loads(brut) == []


def test_relancer_ne_change_plus_rien(tmp_path, monkeypatch):
    """La mise à niveau doit être idempotente : on la lance à CHAQUE
    démarrage."""
    chemin = tmp_path / "deux.db"
    _base_ancienne(chemin)
    import app.db as bd
    moteur = creer(f"sqlite:///{chemin}")
    monkeypatch.setattr(bd, "engine", moteur)

    bd.init_db()
    colonnes = {c["name"] for c in inspect(moteur).get_columns("memoryfact")}
    bd.init_db()
    bd.init_db()
    assert {c["name"] for c in inspect(moteur).get_columns("memoryfact")} == colonnes


def test_toutes_les_tables_du_modele_existent(tmp_path, monkeypatch):
    import app.db as bd
    moteur = creer(f"sqlite:///{tmp_path / 'neuve.db'}")
    monkeypatch.setattr(bd, "engine", moteur)
    bd.init_db()

    presentes = set(inspect(moteur).get_table_names())
    attendues = {t.name for t in SQLModel.metadata.sorted_tables}
    assert attendues <= presentes
