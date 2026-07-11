# src/a3/Twitch/Brain/decisions.py
#
# Logger des décisions de review (garder / highlight / supprimer)
# Stocke un fichier JSON par session dans le dossier decisions/
# IMPORTANT: Aucun identifiant utilisateur en clair — tous pseudonymizés.

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from a3.config import BASE_DIR as _BASE
from a3.utils.privacy import pseudonymize

log = logging.getLogger("A3")


def _dossier_decisions(channel: str) -> Path:
    return _BASE / "decisions" / channel


# "validated"/"highlights" ne sont VOLONTAIREMENT PAS balayés par la rétention : un
# reviewer humain a explicitement choisi de garder ce clip, contrairement à "rejected"
# (décision de suppression) ou au buffer pré-review (jamais reviewé). Les inclure ici a
# causé une perte de données réelle (2026-07-11) : les 5 seuls clips "garder"/"highlight"
# alors en base ont été supprimés par le cleanup automatique dès qu'ils ont dépassé
# RETENTION_DAYS, alors que la décision explicite du reviewer était de les CONSERVER.
CLIP_SUBDIRS_POST_REVIEW = ["rejected"]
RETENTION_DAYS = 2
CLEANUP_INTERVAL_SEC = 3600


class DecisionLogger:
    """
    Enregistre chaque clip généré et chaque décision de review.
    Un fichier JSON par session par channel : decisions/{channel}/session_YYYY-MM-DD_HH-MM-SS.json
    Cleanup policy : supprime automatiquement les clips > 2 jours.
    """

    def __init__(self, channel: str = "unknown", retention_days: int = RETENTION_DAYS) -> None:
        self.channel = channel
        self._dossier = _dossier_decisions(channel)
        self._dossier.mkdir(parents=True, exist_ok=True)
        self._session_debut = datetime.now()
        self._nom_fichier = self._dossier / f"session_{self._session_debut.strftime('%Y-%m-%d_%H-%M-%S')}.json".replace(":", "-")
        self._clips: dict[int, dict] = {}
        self._retention_days = retention_days
        self._cleanup_task: asyncio.Task | None = None
        log.info(f"[Decisions] 📋 Session démarrée → {self._nom_fichier}")

    def _start_cleanup(self) -> None:
        """Lance le cleanup en arrière-plan (une seule fois, après démarrage event loop)."""
        if self._cleanup_task is not None:
            return
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        log.info(f"[Decisions] 🧹 Cleanup policy actif — retention: {self._retention_days} jours")

    async def _cleanup_loop(self) -> None:
        # Nettoie tout de suite au démarrage plutôt que d'attendre CLEANUP_INTERVAL_SEC
        # (1h) — sinon un redémarrage fréquent du bot fait que le cleanup ne tourne
        # quasiment jamais (il ne survit pas à l'arrêt du process).
        self._supprimer_vieux_clips()
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SEC)
            self._supprimer_vieux_clips()

    def _supprimer_vieux_clips(self) -> None:
        """Supprime les fichiers clips plus vieux que retention_days.

        Balaie TOUS les channels trouvés sur disque (pas seulement self.channel) : un
        channel retiré de CHANNELS n'a plus de DecisionLogger/StreamCapture actif, donc
        plus rien ne nettoierait jamais ses fichiers si on se limitait à self.channel.

        Couvre le dossier pré-review (clips_output/{channel} — clip HQ tant que non
        reviewé, et previews qui n'en bougent jamais même après review), les dossiers
        post-review (clips/{channel}/{validated,highlights,rejected}), et
        buffer_segments/{channel} — jamais balayé auparavant : StreamCapture ne purge
        que les segments connus du buffer EN MÉMOIRE d'une capture en cours pour CE
        channel précis (10s max de latence normalement, buffer plafonné à 10 min), donc
        les segments d'un channel retiré de CHANNELS ou laissés par un process tué sans
        passer par arreter() restaient orphelins pour toujours (observé : 2.8 Go
        accumulés sur des channels plus surveillés depuis des mois)."""
        limite = time.time() - (self._retention_days * 86400)
        total_supprimes = 0

        dossiers: list[Path] = []

        clips_output_root = _BASE / "clips_output"
        if clips_output_root.exists():
            dossiers += [d for d in clips_output_root.iterdir() if d.is_dir()]

        clips_root = _BASE / "clips"
        if clips_root.exists():
            for channel_dir in clips_root.iterdir():
                if channel_dir.is_dir():
                    dossiers += [channel_dir / sub for sub in CLIP_SUBDIRS_POST_REVIEW]

        # buffer_segments : uniquement les segments .ts et les listes de concat
        # résiduelles — jamais les .log (activement tenus ouverts en append par
        # streamlink/ffmpeg tant qu'une capture tourne).
        segments_root = _BASE / "buffer_segments"
        segments_dossiers = [d for d in segments_root.iterdir() if d.is_dir()] if segments_root.exists() else []

        for dossier in dossiers:
            if not dossier.exists():
                continue
            for f in dossier.iterdir():
                if f.is_file():
                    age = f.stat().st_mtime
                    if age < limite:
                        try:
                            f.unlink()
                            total_supprimes += 1
                        except Exception as e:
                            log.warning(f"[Decisions] ⚠️ Cannot delete {f}: {e}")

        for dossier in segments_dossiers:
            for f in dossier.glob("seg_*.ts"):
                age = f.stat().st_mtime
                if age < limite:
                    try:
                        f.unlink()
                        total_supprimes += 1
                    except Exception as e:
                        log.warning(f"[Decisions] ⚠️ Cannot delete {f}: {e}")
            for f in dossier.glob("_concat_*.txt"):
                age = f.stat().st_mtime
                if age < limite:
                    try:
                        f.unlink()
                        total_supprimes += 1
                    except Exception as e:
                        log.warning(f"[Decisions] ⚠️ Cannot delete {f}: {e}")

        if total_supprimes > 0:
            log.info(f"[Decisions] 🗑️ Cleanup: {total_supprimes} ancien(s) fichier(s) supprimé(s)")

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
            "decision_reason": None,
            "channel": self.channel,
        }
        self._sauvegarder()
        log.info(f"[Decisions] 📝 Clip #{clip_num} enregistré (score: {score:.2f})")

    def log_decision(
        self,
        clip_num: int,
        decision: str,  # "garder" | "highlight" | "supprimer"
        user: str,
        reason: str | None = None,
        user_is_hash: bool = False,
    ) -> None:
        """Appelé par le Renderer quand un bouton Discord est cliqué.

        `user_is_hash` : True si `user` est déjà pseudonymisé (review reconstruite
        après un redémarrage depuis pending_reviews.json) — évite un double hash."""
        if clip_num not in self._clips:
            log.warning(f"[Decisions] ⚠️ Clip #{clip_num} introuvable pour logguer la décision")
            return

        user_hash = user if user_is_hash else (pseudonymize(user) or "unknown")
        self._clips[clip_num]["decision"] = decision
        self._clips[clip_num]["decision_user"] = user_hash  # pseudonymized
        self._clips[clip_num]["decision_timestamp"] = datetime.now().isoformat()
        self._clips[clip_num]["decision_reason"] = reason
        self._sauvegarder()
        log.info(f"[Decisions] ✅ Clip #{clip_num} — {decision} ({reason}) par [hash:{user_hash}] (score: {self._clips[clip_num]['score']:.2f})")

    def _sauvegarder(self) -> None:
        temp = self._nom_fichier.with_suffix(".tmp")
        try:
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "session": self._session_debut.isoformat(),
                        "channel": self.channel,
                        "clips": list(self._clips.values()),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            temp.replace(self._nom_fichier)
        except Exception as e:
            log.error(f"[Decisions] ❌ Erreur sauvegarde : {e}")
            if temp.exists():
                temp.unlink(missing_ok=True)
