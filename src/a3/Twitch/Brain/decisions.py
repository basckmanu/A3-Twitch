# src/a3/Twitch/Brain/decisions.py
#
# Logger des décisions de review (garder / highlight / supprimer)
# Stocke un fichier JSON par session dans le dossier decisions/

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("A3")

DOSSIER_DECISIONS = Path("decisions")


class DecisionLogger:
    """
    Enregistre chaque clip généré et chaque décision de review.
    Un fichier JSON par session : decisions/session_YYYY-MM-DD_HH-MM-SS.json
    """

    def __init__(self) -> None:
        DOSSIER_DECISIONS.mkdir(exist_ok=True)
        self._session_debut = datetime.now()
        self._nom_fichier = DOSSIER_DECISIONS / f"session_{self._session_debut.strftime('%Y-%m-%d_%H-%M-%S')}.json"
        self._clips: dict[int, dict] = {}
        log.info(f"[Decisions] 📋 Session démarrée → {self._nom_fichier}")

    def log_clip(
        self,
        clip_num: int,
        score: float,
        filtres: dict,
        chemin: str | None,
        mot_repetition: str | None = None,
    ) -> None:
        """Appelé par le Brain à chaque clip généré."""
        self._clips[clip_num] = {
            "clip_num": clip_num,
            "timestamp": datetime.now().isoformat(),
            "score": round(score, 4),
            "filtres": {nom: round(v.get("score_pondéré", 0.0), 4) for nom, v in filtres.items() if v.get("score_pondéré", 0.0) > 0},
            "chemin": chemin,
            "mot_repetition": mot_repetition,
            "decision": None,
            "decision_user": None,
            "decision_timestamp": None,
        }
        self._sauvegarder()
        log.info(f"[Decisions] 📝 Clip #{clip_num} enregistré (score: {score:.2f})")

    def log_decision(
        self,
        clip_num: int,
        decision: str,  # "garder" | "highlight" | "supprimer"
        user: str,
    ) -> None:
        """Appelé par le Renderer quand un bouton Discord est cliqué."""
        if clip_num not in self._clips:
            log.warning(f"[Decisions] ⚠️ Clip #{clip_num} introuvable pour logguer la décision")
            return

        self._clips[clip_num]["decision"] = decision
        self._clips[clip_num]["decision_user"] = user
        self._clips[clip_num]["decision_timestamp"] = datetime.now().isoformat()
        self._sauvegarder()
        log.info(f"[Decisions] ✅ Clip #{clip_num} — {decision} par {user} (score: {self._clips[clip_num]['score']:.2f})")

    def _sauvegarder(self) -> None:
        try:
            with open(self._nom_fichier, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "session": self._session_debut.isoformat(),
                        "clips": list(self._clips.values()),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as e:
            log.error(f"[Decisions] ❌ Erreur sauvegarde : {e}")
