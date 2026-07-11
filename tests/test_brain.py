# tests/test_brain.py
#
# Tests unitaires sur la logique de décision de Brain (scoring, seuil, merge,
# cooldown, dedup) — sans dépendance Twitch/Discord/ffmpeg/DB. C'est la logique
# la plus subtile du projet et celle où plusieurs bugs réels ont déjà été trouvés
# (voir memoire/audits) via des tests manuels en live plutôt qu'automatisés.

import asyncio
import time
from types import SimpleNamespace

from a3.Twitch.Brain.mainBrainTwitch import (
    COOLDOWN_MAX_SEC,
    COOLDOWN_MIN_SEC,
    MERGE_WINDOW_SEC,
    SEUIL_CLIP,
    Brain,
    _calculer_cooldown,
)


def _message(auteur: str = "bob", contenu: str = "hype") -> SimpleNamespace:
    return SimpleNamespace(author=SimpleNamespace(name=auteur), content=contenu)


def _donnees(scores: dict[str, float], message: SimpleNamespace | None = None) -> dict:
    """Construit un dict `données` minimal tel que produit par Watcher._collecter()."""
    détails = {nom: {"score_pondéré": score, "passé": score > 0.0} for nom, score in scores.items()}
    return {
        "détails": détails,
        "message": message or _message(),
        "timestamp": None,
        "mot_repetition": None,
        "channel": "test",
        "viewer_count": None,
        "game_category": None,
        "stream_language": None,
    }


def _run(scenario) -> None:
    asyncio.run(scenario())


def test_score_sous_le_seuil_ne_declenche_pas():
    async def scenario():
        brain = Brain()
        résultat = await brain.analyze(_donnees({"FiltreMessageRate": 0.1}))
        assert résultat is None
        assert brain.clips_detectes == 0
        assert brain.is_recording is False
        await brain.stop()

    _run(scenario)


def test_score_au_dessus_du_seuil_avec_filtre_volume_declenche():
    async def scenario():
        brain = Brain()
        # FiltreEmotions (0.35) + FiltreMessageRate (0.20) à 1.0 => 0.55 >= SEUIL_CLIP (0.42)
        résultat = await brain.analyze(_donnees({"FiltreEmotions": 1.0, "FiltreMessageRate": 1.0}))
        assert résultat is not None
        assert brain.clips_detectes == 1
        assert brain.is_recording is True
        await brain.stop()

    _run(scenario)


def test_score_suffisant_mais_sans_filtre_volume_est_rejete():
    async def scenario():
        brain = Brain()
        # FiltreEmotions seul n'est jamais dans FILTRES_VOLUME => garde-fou volume_ok bloque
        # même à score maximal (0.35 pondéré, de toute façon < SEUIL_CLIP à lui seul).
        résultat = await brain.analyze(_donnees({"FiltreEmotions": 1.0}))
        assert résultat is None
        assert brain.clips_detectes == 0

    _run(scenario)


def test_dedup_rejette_le_meme_moment_relance():
    async def scenario():
        brain = Brain()
        message = _message(auteur="bob")
        scores = {"FiltreEmotions": 1.0, "FiltreMessageRate": 1.0}

        r1 = await brain.analyze(_donnees(scores, message))
        assert r1 is not None
        assert brain.clips_detectes == 1

        # Simule la fin de l'enregistrement précédent (sans passer par _executeur_clip,
        # qui attendrait DUREE_ATTENTE_HYPE_SEC réelles) — même auteur, mêmes filtres actifs
        # dans la fenêtre de dedup (60s) => doit être rejeté comme doublon.
        brain.is_recording = False
        brain._memoire_filtres.clear()  # miroir de _executeur_clip() en conditions réelles
        r2 = await brain.analyze(_donnees(scores, message))
        assert r2 is None
        assert brain.clips_detectes == 1

        await brain.stop()

    _run(scenario)


def test_cooldown_rejette_un_clip_trop_proche():
    async def scenario():
        brain = Brain()
        scores = {"FiltreEmotions": 1.0, "FiltreMessageRate": 1.0}

        r1 = await brain.analyze(_donnees(scores, _message(auteur="bob")))
        assert r1 is not None

        # Simule un clip qui vient de se terminer il y a 170s : hors fenêtre de merge
        # (150s) mais toujours dans un cooldown adaptatif de 200s (le cooldown peut
        # dépasser MERGE_WINDOW_SEC — voir _calculer_cooldown, jusqu'à COOLDOWN_MAX_SEC).
        brain.is_recording = False
        brain._memoire_filtres.clear()  # miroir de _executeur_clip() en conditions réelles
        assert MERGE_WINDOW_SEC < 200
        brain._ts_dernier_clip = time.time() - 170
        brain._score_dernier_clip = 0.9
        brain._cooldown_actuel = 200

        # Auteur différent pour ne pas être rejeté par la dedup plutôt que le cooldown.
        r2 = await brain.analyze(_donnees(scores, _message(auteur="alice")))
        assert r2 is None
        assert brain.clips_detectes == 1

        await brain.stop()

    _run(scenario)


def test_merge_remplace_le_clip_precedent_si_pic_plus_fort():
    async def scenario():
        brain = Brain()
        scores_initial = {"FiltreEmotions": 1.0, "FiltreMessageRate": 1.0}  # 0.55

        r1 = await brain.analyze(_donnees(scores_initial, _message(auteur="bob")))
        assert r1 is not None
        assert brain.clips_detectes == 1

        # Simule la fin de l'enregistrement précédent (peak à 0.50), dans la fenêtre
        # de merge (150s) — sans passer par _executeur_clip (temps réel trop long).
        brain.is_recording = False
        brain._memoire_filtres.clear()  # miroir de _executeur_clip() en conditions réelles
        brain._ts_dernier_clip = time.time() - 10
        brain._score_dernier_clip = 0.50
        brain.historique.append({"score_final": 0.50})

        # 0.55 >= 0.50 => remplace le clip précédent plutôt que d'être bloqué par le cooldown.
        r2 = await brain.analyze(_donnees(scores_initial, _message(auteur="alice")))
        assert r2 is not None
        # _annuler_dernier_clip décrémente puis analyze() ré-incrémente : la numérotation
        # du clip ne saute pas, c'est un remplacement et non un nouveau clip.
        assert brain.clips_detectes == 1
        assert len(brain.historique) == 0

        await brain.stop()

    _run(scenario)


def test_merge_rejette_si_pic_plus_faible():
    async def scenario():
        brain = Brain()
        scores_fort = {"FiltreEmotions": 1.0, "FiltreMessageRate": 1.0}  # 0.55

        r1 = await brain.analyze(_donnees(scores_fort, _message(auteur="bob")))
        assert r1 is not None
        assert brain.clips_detectes == 1

        brain.is_recording = False
        brain._memoire_filtres.clear()  # miroir de _executeur_clip() en conditions réelles
        brain._ts_dernier_clip = time.time() - 10
        brain._score_dernier_clip = 0.55
        brain.historique.append({"score_final": 0.55})

        # 0.7*0.35 + 1.0*0.20 = 0.445 : au-dessus du seuil (0.42) mais sous le pic
        # précédent (0.55) => rejeté par la fenêtre de merge, pas un remplacement.
        scores_plus_faible = {"FiltreEmotions": 0.7, "FiltreMessageRate": 1.0}
        r2 = await brain.analyze(_donnees(scores_plus_faible, _message(auteur="alice")))
        assert r2 is None
        assert brain.clips_detectes == 1
        assert len(brain.historique) == 1  # l'entrée précédente n'a pas été retirée

        await brain.stop()

    _run(scenario)


def test_calculer_cooldown_borne_min_et_max():
    assert _calculer_cooldown(SEUIL_CLIP, SEUIL_CLIP) == COOLDOWN_MIN_SEC
    assert _calculer_cooldown(1.0, SEUIL_CLIP) == COOLDOWN_MAX_SEC
    # Score intermédiaire => cooldown strictement entre les deux bornes
    intermédiaire = _calculer_cooldown((SEUIL_CLIP + 1.0) / 2, SEUIL_CLIP)
    assert COOLDOWN_MIN_SEC < intermédiaire < COOLDOWN_MAX_SEC
