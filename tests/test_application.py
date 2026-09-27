"""L'application : versions, nouveautés, sûreté des mises à jour, clé Mistral."""
import pytest

from app import maj, reglages
from app.version import VERSION


def test_comparaison_des_versions():
    assert maj.plus_recente("1.2.0", "1.1.9")
    assert maj.plus_recente("1.10.0", "1.9.0")
    assert not maj.plus_recente("1.0.0", "1.0.0")
    assert not maj.plus_recente("0.9.9", "1.0.0")


def test_les_nouveautes_de_la_version_courante_existent():
    versions = maj.nouveautes_locales()
    assert versions and versions[0]["version"] == VERSION, \
        "NOUVEAUTES.md doit commencer par la version de app/version.py"
    assert versions[0]["points"]
    assert "<strong>" in versions[0]["points"][0]


def test_les_nouveautes_sont_echappees():
    assert maj._en_html("**Gras** <script>") == "<strong>Gras</strong> &lt;script&gt;"


def test_on_ne_telecharge_que_depuis_le_depot(monkeypatch):
    monkeypatch.setattr(maj, "DEPOT", "moi/nindo")
    assert maj._url_sure("https://github.com/moi/nindo/releases/download/v1.1.0/Nindo-windows.zip")
    assert not maj._url_sure("https://github.com/autre/nindo/releases/download/v1/x.zip")
    assert not maj._url_sure("https://exemple.com/moi/nindo/releases/download/x.zip")


def test_sans_depot_le_lanceur_ne_cherche_rien(monkeypatch):
    monkeypatch.setattr(maj, "DEPOT", "")
    etat = maj.etat()
    assert etat["maj"] is False and etat["nouveautes"]


def test_depuis_le_code_source_on_n_installe_pas(monkeypatch):
    monkeypatch.setattr(maj, "EMPAQUETE", False)
    with pytest.raises(maj.MajImpossible):
        maj.installer()


def test_la_cle_mistral_s_enregistre_dans_le_fichier_du_joueur(tmp_path, monkeypatch):
    fichier = tmp_path / ".env"
    fichier.write_text("LLM_PROVIDER=ollama\nEN_LIGNE_CLE=\nAUTRE=1\n", encoding="utf-8")
    monkeypatch.setattr(reglages, "ENV_FICHIER", fichier)
    from app.config import settings
    monkeypatch.setattr(settings, "en_ligne_cle", "")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    # Une clé visiblement factice : la protection de GitHub bloquait l'envoi
    # d'une chaîne qui avait simplement la forme d'une vraie clé Mistral.
    fausse = "cle-de-test-" + "x" * 12
    reglages.enregistrer_cle_mistral(fausse)
    texte = fichier.read_text(encoding="utf-8")
    assert f"EN_LIGNE_CLE={fausse}" in texte
    assert "LLM_PROVIDER=en_ligne" in texte and "AUTRE=1" in texte
    assert reglages.cle_masquee() == "…xxxx"


def test_une_cle_fantaisiste_est_refusee(tmp_path, monkeypatch):
    monkeypatch.setattr(reglages, "ENV_FICHIER", tmp_path / ".env")
    with pytest.raises(ValueError):
        reglages.enregistrer_cle_mistral("pas une clé !")
