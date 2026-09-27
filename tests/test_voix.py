"""Le découpage vocal d'une narration.

Tout l'échafaudage existait — champ `Turn.segments`, schéma `SEGMENTS`, prompt
`SEGMENTEUR`, archétypes de voix dans le ruleset, prononciations dans le lore —
et rien ne lisait quoi que ce soit.

Le découpage est fait en Python plutôt que par le modèle : un dialogue français
est entre guillemets, et le locuteur est dans la phrase qui l'entoure. Un appel
de modèle par tour pour une tâche déterministe ne se justifiait pas.
"""
import pytest

from app.engine.voix import (TONS_PAR_VERBE, archetype, attribuer, modulation,
                             prononcer, segmenter, ton_de_narration,
                             ton_du_dialogue)
from app.lore.pack import charger as charger_pack
from app.models import Character
from app.rules.loader import charger


@pytest.fixture(scope="module")
def rs():
    return charger("naruto")


@pytest.fixture(scope="module")
def pack():
    return charger_pack("naruto")


def test_une_narration_sans_dialogue_reste_entiere():
    segs = segmenter("La brume passe entre vous deux sans se presser.")
    assert len(segs) == 1
    assert segs[0]["type"] == "narration"


def test_les_guillemets_decoupent_le_dialogue():
    texte = ("Hiroshi te regarde longuement. « Tu es en retard », dit-il "
             "sans lever la voix. La cour se vide.")
    segs = segmenter(texte)
    types = [s["type"] for s in segs]
    assert types == ["narration", "dialogue", "narration"]
    assert segs[1]["texte"] == "Tu es en retard"


def test_le_locuteur_se_lit_dans_l_incise():
    texte = ("« Tu es en retard », dit Hiroshi Tanaka sans lever la voix.")
    segs = attribuer(segmenter(texte), ["Hiroshi Tanaka", "Rika Hyûga"])
    dialogue = next(s for s in segs if s["type"] == "dialogue")
    assert dialogue["locuteur"] == "Hiroshi Tanaka"


def test_le_nom_le_plus_long_l_emporte():
    """« Rika » et « Rika Hyûga » coexistent : on ne doit pas attribuer la
    réplique au mauvais personnage sur un préfixe."""
    texte = "« J'y vais », lance Rika Hyûga."
    segs = attribuer(segmenter(texte), ["Rika", "Rika Hyûga"])
    assert segs[-1 if segs[-1]["type"] == "dialogue" else 0]["type"]
    dialogue = next(s for s in segs if s["type"] == "dialogue")
    assert dialogue["locuteur"] == "Rika Hyûga"


def test_une_replique_sans_incise_reprend_le_dernier_locuteur():
    """Deux répliques qui se suivent sont presque toujours un échange entre
    les deux mêmes personnes."""
    texte = ("« Tu es en retard », dit Hiroshi Tanaka. Un silence. "
             "« Recommence demain. »")
    segs = attribuer(segmenter(texte), ["Hiroshi Tanaka"])
    dialogues = [s for s in segs if s["type"] == "dialogue"]
    assert len(dialogues) == 2
    assert dialogues[1]["locuteur"] == "Hiroshi Tanaka"


def test_un_dialogue_sans_personnage_connu_n_invente_personne():
    segs = attribuer(segmenter("« Qui va là ? », demande une voix."), ["Kaito"])
    assert next(s for s in segs if s["type"] == "dialogue")["locuteur"] == ""


def test_l_archetype_suit_l_age_et_le_sexe(rs):
    enfant = Character(nom="A", age=12, sexe="masculin", tier=2)
    ancien = Character(nom="B", age=68, sexe="masculin", tier=3)
    assert archetype(rs, enfant)["pitch"] > archetype(rs, ancien)["pitch"]
    assert archetype(rs, ancien)["vitesse"] < 1.0


def test_sans_personnage_la_voix_est_neutre(rs):
    neutre = archetype(rs, None)
    assert neutre["pitch"] == 1.0 and neutre["vitesse"] == 1.0


def test_la_prononciation_ne_touche_que_le_texte_lu(pack):
    """Aucun synthétiseur francophone ne lit « Sharingan » tout seul. On aide
    le moteur vocal ; on ne corrige pas ce que le joueur voit."""
    couples = pack.lexique
    assert couples, "le lore ne porte plus aucune prononciation"
    terme, attendu = couples[0]
    lu = prononcer(f"Il active son {terme} sans prévenir.", pack)
    assert attendu in lu
    assert terme not in lu

# ==========================================================================
# LE TON
# `SEGMENTS.emotion` etait declare dans le schema et rempli par personne :
# une replique criee et une replique murmuree se lisaient exactement pareil.
# ==========================================================================
def test_l_incise_donne_le_ton():
    assert ton_du_dialogue("Va-t'en.", "murmure-t-il sans se retourner") == "murmure"
    assert ton_du_dialogue("Recule.", ", gronde Hiroshi") == "menace"
    assert ton_du_dialogue("Jamais.", ", hurle-t-elle") == "cri"
    assert ton_du_dialogue("Je... je ne sais pas.", ", bredouille le garde") == "hesitation"


def test_l_incise_l_emporte_sur_la_ponctuation():
    """C'est l'auteur qui a tranche : une replique murmuree reste un murmure
    meme avec un point d'exclamation."""
    assert ton_du_dialogue("Va-t'en !", "murmure-t-il") == "murmure"


def test_la_ponctuation_suffit_quand_l_incise_se_tait():
    assert ton_du_dialogue("Qui va la ?") == "question"
    assert ton_du_dialogue("Attention !") == "colere"
    assert ton_du_dialogue("Je crois que...") == "hesitation"
    assert ton_du_dialogue("Il fait froid.") == "neutre"


def test_les_capitales_font_un_cri():
    assert ton_du_dialogue("COURS MAINTENANT") == "cri"


def test_un_segment_vide_reste_neutre():
    assert ton_du_dialogue("") == "neutre"
    assert ton_du_dialogue("   ", "") == "neutre"


def test_le_registre_de_la_scene_donne_le_ton_de_la_narration(rs):
    """Le registre etait choisi et stocke a chaque tour, et ne servait qu'a
    ecrire la consigne du narrateur. Il sert maintenant aussi a la lire."""
    assert ton_de_narration(rs, "echange") == "urgence"
    assert ton_de_narration(rs, "revers") == "gravite"
    assert ton_de_narration(rs, "ordinaire") == "neutre"
    assert ton_de_narration(rs, "registre_inconnu") == "neutre"


def test_chaque_ton_declare_a_bien_une_modulation(rs):
    tons = set(rs.data["voix"]["tons"])
    par_verbe = set(TONS_PAR_VERBE)
    par_registre = set(rs.data["voix"]["ton_par_registre"].values())
    manquants = (par_verbe | par_registre | {"neutre", "colere", "question"}) - tons
    assert not manquants, f"tons sans modulation : {manquants}"


def test_le_ton_module_l_archetype_sans_l_ecraser(rs):
    """Le grain de voix reste celui du personnage ; seul le moment change."""
    fort = modulation(rs, "cri")
    bas = modulation(rs, "murmure")
    assert fort["volume"] > bas["volume"]
    assert fort["vitesse"] > bas["vitesse"]
    assert bas["pause"] > fort["pause"], "un murmure appelle un blanc"
