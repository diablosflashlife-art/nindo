"""Connexion à la base, session, et mise à niveau du schéma."""
import json

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite") else {},
)


def _completer_schema() -> None:
    """Ajoute aux tables existantes les colonnes qui leur manquent.

    POURQUOI C'EST NÉCESSAIRE. `create_all` crée les TABLES absentes et ne
    touche jamais à celles qui existent. Une nouvelle table (`Encounter`)
    apparaissait donc toute seule, mais une nouvelle colonne sur une table
    ancienne — `MemoryFact.vecteur` — faisait planter la partie de quiconque
    avait déjà joué : « no such column ».

    Une sauvegarde de campagne doit survivre aux mises à jour du jeu. C'est
    même la raison d'être du fichier `data/parties.db`.

    CE QUE ÇA NE FAIT PAS, ET VOLONTAIREMENT : renommer, supprimer, changer un
    type. Ces opérations-là détruisent des données et méritent une vraie
    migration écrite à la main, pas une heuristique lancée au démarrage.
    """
    inspecteur = inspect(engine)
    with engine.begin() as conn:
        for mapper in SQLModel._sa_registry.mappers:
            table = mapper.local_table
            if table is None or not inspecteur.has_table(table.name):
                continue
            presentes = {c["name"] for c in inspecteur.get_columns(table.name)}
            champs = getattr(mapper.class_, "model_fields", {})

            for col in table.columns:
                if col.name in presentes:
                    continue
                type_sql = col.type.compile(engine.dialect)
                conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" '
                    f'ADD COLUMN "{col.name}" {type_sql}')

                # Les lignes déjà écrites reçoivent la valeur par défaut du
                # modèle. Sans ça, une colonne JSON revient à `None` et le
                # code qui attend une liste tombe au premier tour rejoué.
                defaut = _defaut_de(champs.get(col.name))
                if defaut is not None:
                    conn.exec_driver_sql(
                        f'UPDATE "{table.name}" SET "{col.name}" = ?',
                        (defaut,))


def _defaut_de(champ):
    """La valeur par défaut du modèle, prête pour SQLite.

    Les structures (listes, dictionnaires) partent en JSON ; les scalaires
    partent tels quels. C'est important : une colonne entière neuve laissée à
    NULL fait tomber tout le code qui écrit `valeur + 1` sur une ligne
    ancienne, et ce code-là est écrit partout.
    """
    if champ is None:
        return None
    fabrique = getattr(champ, "default_factory", None)
    if fabrique is not None:
        try:
            valeur = fabrique()
        except Exception:  # noqa: BLE001 — une fabrique exotique ne bloque rien
            return None
        return json.dumps(valeur) if isinstance(valeur, (list, dict)) else None

    valeur = getattr(champ, "default", None)
    if isinstance(valeur, bool):
        return int(valeur)
    if isinstance(valeur, (int, float, str)):
        return valeur
    return None


def init_db() -> None:
    import app.models  # noqa: F401  — enregistre les tables

    SQLModel.metadata.create_all(engine)
    _completer_schema()
    if settings.database_url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL;")


def get_session():
    with Session(engine) as session:
        yield session
