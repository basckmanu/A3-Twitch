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


# "validated"/"highlights" (décision explicite de garder) ont une rétention plus longue
# que "rejected"/le buffer pré-review (jamais reviewé, ou décision explicite de
# supprimer) : un reviewer humain a choisi de les conserver, ça mérite plus que 2 jours.
# Incident du 2026-07-11 : les traiter avec la même rétention courte que "rejected" a
# supprimé définitivement les 5 seuls clips "garder"/"highlight" alors en base dès
# qu'ils ont dépassé RETENTION_DAYS. KEPT_RETENTION_DAYS borne quand même leur durée de
# vie (pas de conservation illimitée = pas de croissance disque non bornée).
CLIP_SUBDIRS_POST_REVIEW = ["rejected"]
CLIP_SUBDIRS_KEPT = ["validated", "highlights"]
RETENTION_DAYS = 2
KEPT_RETENTION_DAYS = 30
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

    def _purger(self, dossier: Path, limite: float, motif: str, glob: str = "*") -> int:
        """Supprime dans `dossier` les fichiers matchant `glob` plus vieux que `limite`
        (timestamp epoch). Retourne le nombre de fichiers supprimés."""
        if not dossier.exists():
            return 0
        supprimes = 0
        for f in dossier.glob(glob):
            if not f.is_file():
                continue
            if f.stat().st_mtime < limite:
                try:
                    f.unlink()
                    supprimes += 1
                except Exception as e:
                    log.warning(f"[Decisions] ⚠️ Cannot delete {f} ({motif}): {e}")
        return supprimes

    def _supprimer_vieux_clips(self) -> None:
        """Politique de rétention :
        - clips_output/{channel} (buffer pré-review, jamais reviewé) et
          clips/{channel}/rejected (décision explicite de supprimer/expirer) : purgés
          après RETENTION_DAYS (2j).
        - clips/{channel}/{validated,highlights} (décision explicite de garder) : purgés
          après KEPT_RETENTION_DAYS (30j) seulement — un reviewer humain a choisi de les
          conserver, ils méritent plus que 2 jours, mais pas une conservation illimitée
          (voir incident du 2026-07-11 dans la mémoire du projet : ne JAMAIS leur
          appliquer la même rétention courte que rejected/clips_output).
        - buffer_segments/{channel} (segments vidéo bruts, jamais des décisions
          humaines) : purgé après RETENTION_DAYS — jamais balayé auparavant, seul
          StreamCapture purgeait (uniquement les segments connus de son buffer EN
          MÉMOIRE pour une capture en cours), donc un channel retiré de CHANNELS ou un
          process tué sans passer par arreter() laissait ses segments orphelins pour
          toujours (observé : 2,8 Go accumulés sur des channels plus surveillés depuis
          des mois).

        Balaie TOUS les channels trouvés sur disque (pas seulement self.channel) : un
        channel retiré de CHANNELS n'a plus de DecisionLogger/StreamCapture actif, donc
        plus rien ne nettoierait jamais ses fichiers si on se limitait à self.channel."""
        limite_courte = time.time() - (self._retention_days * 86400)
        limite_longue = time.time() - (KEPT_RETENTION_DAYS * 86400)
        total_supprimes = 0

        clips_output_root = _BASE / "clips_output"
        if clips_output_root.exists():
            for channel_dir in clips_output_root.iterdir():
                if channel_dir.is_dir():
                    total_supprimes += self._purger(channel_dir, limite_courte, "clips_output")

        clips_root = _BASE / "clips"
        if clips_root.exists():
            for channel_dir in clips_root.iterdir():
                if not channel_dir.is_dir():
                    continue
                for sub in CLIP_SUBDIRS_POST_REVIEW:
                    total_supprimes += self._purger(channel_dir / sub, limite_courte, sub)
                for sub in CLIP_SUBDIRS_KEPT:
                    total_supprimes += self._purger(channel_dir / sub, limite_longue, sub)

        # buffer_segments : uniquement les segments .ts et les listes de concat
        # résiduelles — jamais les .log (activement tenus ouverts en append par
        # streamlink/ffmpeg tant qu'une capture tourne).
        segments_root = _BASE / "buffer_segments"
        if segments_root.exists():
            for channel_dir in segments_root.iterdir():
                if channel_dir.is_dir():
                    total_supprimes += self._purger(channel_dir, limite_courte, "buffer_segments", "seg_*.ts")
                    total_supprimes += self._purger(channel_dir, limite_courte, "buffer_segments", "_concat_*.txt")

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
