"""Le conteur en ligne, et le relais vers Ollama.

Aucun appel réseau : le service est simulé par `httpx.MockTransport`. Ce qu'on
vérifie, c'est ce qui décide de l'expérience du joueur — que la partie ne
s'arrête jamais, et qu'on ne change pas de voix pour rien.
"""
import json

import httpx
import pytest

from app.config import settings
from app.llm import provider as p


def _reponse(texte: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": texte}}]}


def _service(gestion, monkeypatch, cle="cle-de-test"):
    monkeypatch.setattr(settings, "en_ligne_service", "mistral")
    monkeypatch.setattr(settings, "en_ligne_cle", cle)
    monkeypatch.setattr(settings, "en_ligne_modele", "")
    monkeypatch.setattr(settings, "en_ligne_intervalle", 0.0)
    monkeypatch.setattr(p.time, "sleep", lambda s: None)
    return p.EnLigneProvider(client=httpx.Client(transport=httpx.MockTransport(gestion)))


class Secours:
    """Un Ollama de paille : il répond toujours, et compte ses appels."""
    nom = "Ollama"

    def __init__(self):
        self.appels = 0

    def text(self, system, user, **kw):
        self.appels += 1
        return "récit du secours"

    def json(self, system, user, schema, **kw):
        self.appels += 1
        return {"ok": True}

    def flux(self, system, user, **kw):
        self.appels += 1
        yield "récit "
        yield "du secours"


# --- le service en ligne ------------------------------------------------------
def test_la_requete_suit_le_dialecte_openai(monkeypatch):
    vu = {}

    def gestion(req):
        vu["url"] = str(req.url)
        vu["auth"] = req.headers.get("authorization")
        vu["corps"] = json.loads(req.content)
        return httpx.Response(200, json=_reponse("La pluie tombe sur Kiri."))

    svc = _service(gestion, monkeypatch)
    assert svc.text("système", "joueur") == "La pluie tombe sur Kiri."
    assert vu["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert vu["auth"] == "Bearer cle-de-test"
    assert vu["corps"]["model"] == "ministral-14b-latest"
    assert vu["corps"]["messages"][0] == {"role": "system", "content": "système"}


def test_la_cle_ne_passe_jamais_dans_l_adresse(monkeypatch):
    urls = []

    def gestion(req):
        urls.append(str(req.url))
        return httpx.Response(200, json=_reponse("ok"))

    _service(gestion, monkeypatch).text("s", "u")
    assert all("cle-de-test" not in u for u in urls)


def test_le_flux_arrive_morceau_par_morceau(monkeypatch):
    lignes = [
        'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}',
        'data: {"choices":[{"delta":{"content":"Le vent "}}]}',
        ": ping",
        'data: {"choices":[{"delta":{"content":"se l\\u00e8ve."}}]}',
        "data: [DONE]",
    ]

    def gestion(req):
        assert json.loads(req.content)["stream"] is True
        return httpx.Response(200, text="\n\n".join(lignes),
                              headers={"content-type": "text/event-stream"})

    morceaux = list(_service(gestion, monkeypatch).flux("s", "u"))
    assert "".join(morceaux) == "Le vent se lève."
    assert len(morceaux) >= 2


def test_la_reflexion_du_modele_n_est_jamais_montree(monkeypatch):
    def gestion(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": [
            {"type": "thinking", "thinking": [{"type": "text", "text": "brouillon"}]},
            {"type": "text", "text": "<think>hmm</think>La scène."}]}}]})

    assert _service(gestion, monkeypatch).text("s", "u") == "La scène."


def test_le_json_est_ramene_au_schema(monkeypatch):
    schema = {"type": "object", "properties": {
        "action_type": {"type": "string", "enum": ["attaque", "autre"]},
        "difficulte": {"type": "string", "enum": ["facile", "normal", "difficile"]},
        "requiert_jet": {"type": "boolean"},
        "xp": {"type": "integer"},
        "propositions": {"type": "array", "items": {"type": "object", "properties": {
            "texte": {"type": "string"}, "risque": {"type": "string"}}}}}}

    def gestion(req):
        corps = json.loads(req.content)
        assert corps["response_format"] == {"type": "json_object"}
        assert "action_type" in corps["messages"][0]["content"]   # le schéma voyage
        return httpx.Response(200, json=_reponse(
            '```json\n{"action_type": "Attaque", "difficulte": "moyenne", '
            '"xp": "15", "propositions": [{"texte": "Fuir"}]}\n```'))

    r = _service(gestion, monkeypatch).json("s", "u", schema)
    assert r["action_type"] == "attaque"            # casse rapprochée
    assert r["difficulte"] == "facile"              # hors liste : une valeur sûre
    assert r["requiert_jet"] is False               # manquant : complété
    assert r["xp"] == 15                            # « 15 » converti
    assert r["propositions"] == [{"texte": "Fuir", "risque": ""}]


def test_un_json_illisible_est_redemande_puis_refuse(monkeypatch):
    appels = []

    def gestion(req):
        appels.append(1)
        return httpx.Response(200, json=_reponse("désolé, je ne peux pas"))

    with pytest.raises(p.Refus):
        _service(gestion, monkeypatch).json("s", "u", {"properties": {"a": {"type": "string"}}})
    assert len(appels) == 2


def test_trop_de_requetes_est_attendu_puis_retente(monkeypatch):
    appels = []

    def gestion(req):
        appels.append(1)
        if len(appels) == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json=_reponse("ok."))

    assert _service(gestion, monkeypatch).text("s", "u") == "ok."
    assert len(appels) == 2


def test_sans_cle_le_service_se_declare_inutilisable(monkeypatch):
    svc = _service(lambda req: httpx.Response(200, json=_reponse("x")), monkeypatch, cle="")
    with pytest.raises(p.ServiceIndisponible):
        svc.text("s", "u")


# --- le relais -----------------------------------------------------------------
def test_le_relais_garde_le_principal_tant_qu_il_repond(monkeypatch):
    svc = _service(lambda req: httpx.Response(200, json=_reponse("principal.")), monkeypatch)
    secours = Secours()
    relais = p.RelaisProvider(svc, secours)
    assert relais.text("s", "u") == "principal."
    assert secours.appels == 0
    assert relais.etat()["conteur"] == "Mistral"
    assert relais.etat()["en_secours"] is False


def test_quota_atteint_le_secours_prend_la_main_et_la_garde(monkeypatch):
    appels = []

    def gestion(req):
        appels.append(1)
        return httpx.Response(429)

    svc = _service(gestion, monkeypatch)
    secours = Secours()
    relais = p.RelaisProvider(svc, secours)
    assert relais.text("s", "u") == "récit du secours"
    essais = len(appels)
    # Pendant la pause, on ne réessaie pas le principal à chaque appel : une
    # seule voix, et pas de quota gaspillé.
    assert relais.text("s", "u") == "récit du secours"
    assert len(appels) == essais
    etat = relais.etat()
    assert etat["en_secours"] and etat["conteur"] == "Ollama"
    assert "Quota" in etat["raison"]


def test_apres_la_pause_le_principal_revient(monkeypatch):
    horloge = [1000.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: horloge[0])
    etat = {"panne": True}

    def gestion(req):
        return httpx.Response(429) if etat["panne"] else httpx.Response(200, json=_reponse("revenu."))

    relais = p.RelaisProvider(_service(gestion, monkeypatch), Secours())
    relais.text("s", "u")
    assert relais.etat()["en_secours"]
    etat["panne"] = False
    horloge[0] += settings.relais_pause + 1
    assert relais.text("s", "u") == "revenu."
    assert relais.etat()["raison"] == ""


def test_une_cle_refusee_bascule_jusqu_au_redemarrage(monkeypatch):
    horloge = [0.0]
    monkeypatch.setattr(p.time, "monotonic", lambda: horloge[0])
    relais = p.RelaisProvider(_service(lambda req: httpx.Response(401), monkeypatch), Secours())
    relais.text("s", "u")
    horloge[0] += 10 ** 6
    assert relais.etat()["en_secours"]
    assert "refusée" in relais.etat()["raison"]


def test_sans_cle_on_part_directement_sur_le_secours(monkeypatch):
    appels = []
    svc = _service(lambda req: appels.append(1) or httpx.Response(200, json=_reponse("x")),
                   monkeypatch, cle="")
    relais = p.RelaisProvider(svc, Secours())
    assert relais.text("s", "u") == "récit du secours"
    assert appels == []
    assert "EN_LIGNE_CLE" in relais.etat()["raison"]


def test_une_reponse_vide_ne_passe_la_main_que_pour_cet_appel(monkeypatch):
    reponses = iter(["", "de nouveau là."])
    relais = p.RelaisProvider(
        _service(lambda req: httpx.Response(200, json=_reponse(next(reponses))), monkeypatch),
        Secours())
    assert relais.text("s", "u") == "récit du secours"
    assert not relais.etat()["en_secours"]
    assert relais.text("s", "u") == "de nouveau là."


def test_le_flux_bascule_avant_le_premier_mot(monkeypatch):
    def gestion(req):
        raise httpx.ConnectError("réseau coupé")

    relais = p.RelaisProvider(_service(gestion, monkeypatch), Secours())
    assert "".join(relais.flux("s", "u")) == "récit du secours"
    assert relais.dernier == "Ollama"
    assert "injoignable" in relais.etat()["raison"]


def test_le_json_passe_aussi_par_le_relais(monkeypatch):
    relais = p.RelaisProvider(
        _service(lambda req: httpx.Response(503), monkeypatch), Secours())
    assert relais.json("s", "u", {"properties": {}}) == {"ok": True}


def test_sans_secours_l_erreur_remonte(monkeypatch):
    relais = p.RelaisProvider(_service(lambda req: httpx.Response(503), monkeypatch), None)
    with pytest.raises(httpx.HTTPStatusError):
        relais.text("s", "u")


def test_le_conteur_se_declare(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "mock")
    p.reset_llm()
    try:
        assert p.conteur()["relais"] is False
    finally:
        p.reset_llm()


def test_sante_dit_quoi_faire_sans_cle_ni_ollama(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(settings, "llm_provider", "en_ligne")
    monkeypatch.setattr(settings, "en_ligne_cle", "")
    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9")
    p.reset_llm()
    try:
        info = TestClient(app).get("/sante").json()
    finally:
        p.reset_llm()
    assert info["pret"] is False
    assert info["en_ligne"]["cle"] == "absente"
    assert "INUTILISABLE" in info["en_ligne"]["etat"]
    assert info["secours_ollama"]["ollama"] == "INJOIGNABLE"
    assert any("EN_LIGNE_CLE" in a for a in info["action"])
    assert info["conteur_actuel"]["en_secours"] is True


def test_un_modele_ferme_n_est_pas_un_quota(monkeypatch):
    """Sur une offre gratuite, Mistral répond 429 avec un plafond de ZÉRO pour
    les modèles qu'elle n'ouvre pas. Attendre dix minutes n'y changerait rien :
    le relais doit le dire, et ne plus réessayer."""
    appels = []

    def gestion(req):
        appels.append(1)
        return httpx.Response(429, headers={"x-ratelimit-limit-req-minute": "0"})

    relais = p.RelaisProvider(_service(gestion, monkeypatch), Secours())
    assert relais.text("s", "u") == "récit du secours"
    assert len(appels) == 1                     # pas de nouvel essai inutile
    assert "pas ouvert" in relais.etat()["raison"]
    relais.text("s", "u")
    assert len(appels) == 1


def test_un_acces_refuse_dit_pourquoi(monkeypatch):
    relais = p.RelaisProvider(_service(lambda req: httpx.Response(
        403, json={"message": "This model is not available in your subscription tier"}),
        monkeypatch), Secours())
    relais.text("s", "u")
    assert "subscription" in relais.etat()["raison"]
    assert "Clé" not in relais.etat()["raison"]
