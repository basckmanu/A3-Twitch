# src/a3/Twitch/Brain/structuredLogger.py
#
# Logger structuré JSON pour alimentation BD et IA.
# Chaque event est un objet JSON avec des champs fixes :
#   timestamp, event_type, channel, session_id, data
#
# Formats de sortie :
#   - JSON fichier (pour ingestion BD / ELK / Logstash)
#   - DatabaseHandler stub (brancher sur PostgreSQL/SQLite plus tard)

import json
import logging
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path


class EventType:
    # Session
    SESSION_START = "session_start"
    SESSION_STOP = "session_stop"

    # Filtres
    FILTER_SCORE = "filter_score"      # score d'un filtre sur un message
    FILTER_TRIGGER = "filter_trigger"  # un filtre se déclenche
    FILTER_CALIBRATED = "filter_calibrated"
    CALIBRATION_COMPLETE = "calibration_complete"

    # Clip lifecycle
    CLIP_DETECTED = "clip_detected"
    CLIP_MERGED = "clip_merged"
    CLIP_RECORDING = "clip_recording"
    CLIP_GENERATED = "clip_generated"
    CLIP_VALIDATED = "clip_validated"
    CLIP_REJECTED = "clip_rejected"
    CLIP_HIGHLIGHTED = "clip_highlighted"
    CLIP_DELETED = "clip_deleted"

    # Review Discord
    REVIEW_GARDER = "review_garder"
    REVIEW_HIGHLIGHT = "review_highlight"
    REVIEW_SUPPRIMER = "review_supprimer"

    # System
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DatabaseHandler(ABC):
    """Abstract handler — implémenter pour PostgreSQL/SQLite plus tard."""

    @abstractmethod
    def write(self, event: dict) -> None:
        """Insère ou met à jour l'event en base."""
        raise NotImplementedError

    @abstractmethod
    def flush(self) -> None:
        """Force l'écriture de tous les events en buffer."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Ferme proprement la connexion."""
        raise NotImplementedError


class DummyDBHandler(DatabaseHandler):
    """Handler pass-through qui ne fait rien (pour quand aucune DB n'est configurée)."""

    def write(self, event: dict) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class StructuredLogger:
    _instance: "StructuredLogger | None" = None

    """
    Logger structuré JSON.
    Usage :
        logger = StructuredLogger(channel="kamet0")
        StructuredLogger.set_instance(logger)
        logger.log_event(EventType.CLIP_DETECTED, {"clip_num": 1, "score": 0.72})
    """

    @classmethod
    def set_instance(cls, instance: "StructuredLogger") -> None:
        cls._instance = instance

    @classmethod
    def get_instance(cls) -> "StructuredLogger | None":
        return cls._instance

    @classmethod
    def log_review(cls, clip_num: int, action: str, user: str, user_id: int = 0) -> None:
        """Shortcut global pour logger une review Discord (appelable depuis ClipView).
        NOTE: le champ `user` est pseudonymizé avant écriture en DB/JSON."""
        inst = cls._instance
        if inst is None:
            return
        from a3.utils.privacy import pseudonymize
        user_hash = pseudonymize(user) or "unknown"
        event_map = {
            "garder": EventType.REVIEW_GARDER,
            "highlight": EventType.REVIEW_HIGHLIGHT,
            "supprimer": EventType.REVIEW_SUPPRIMER,
        }
        inst.log_event(event_map.get(action, EventType.INFO), {
            "clip_num": clip_num,
            "action": action,
            "user": user_hash,  # pseudonymized — never stored in clear
            "user_id": user_id,
        })

    def __init__(
        self,
        channel: str,
        session_id: str | None = None,
        output_dir: Path | None = None,
        db_handler: DatabaseHandler | None = None,
    ) -> None:
        self.channel = channel
        self.session_id = session_id or str(uuid.uuid4())[:8]

        # Logging standard pour la console (humain lisible) — doit être avant _auto_db_handler
        self._console = logging.getLogger(f"A3.{channel}.structured")
        self._console.setLevel(logging.INFO)

        self._db = db_handler or self._auto_db_handler()
        self._buffer: list[dict] = []

        # Output directory (défaut : logs/structured/)
        if output_dir is None:
            base = Path(__file__).resolve().parents[3]
            output_dir = base / "logs" / "structured"
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Fichier JSONL par session
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        self._file_path = self._output_dir / f"a3_{channel}_{ts}_{self.session_id}.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")

    def _auto_db_handler(self) -> DatabaseHandler:
        """Auto-detects DB: PostgreSQL (DB_TYPE=postgres) > MySQL > Dummy."""
        db_type = os.getenv("DB_TYPE", "").lower()
        db_password = os.getenv("DB_PASSWORD", "")

        # PostgreSQL
        if db_type == "postgres" or (not db_type and db_password):
            try:
                from a3.Twitch.Brain.postgresHandler import PostgresHandler
                handler = PostgresHandler()
                if handler._db is not None:
                    self._console.info("[StructuredLogger] 📦 PostgreSQL handler activé")
                    return handler
            except Exception as e:
                self._console.warning(f"[StructuredLogger] ⚠️ PostgreSQL non disponible : {e}")

        # MySQL
        if db_password:
            try:
                from a3.Twitch.Brain.databaseHandler import MySQLHandler
                handler = MySQLHandler()
                if handler._db is not None:
                    self._console.info("[StructuredLogger] 📦 MySQL handler activé")
                    return handler
            except Exception as e:
                self._console.warning(f"[StructuredLogger] ⚠️ MySQL non disponible : {e}")

        return DummyDBHandler()

    # ── Public API ──────────────────────────────────────────────────

    def log_event(self, event_type: str, data: dict, level: str = "INFO") -> None:
        """
        Log un event structuré.
        - Écrit dans le fichier JSONL
        - Passe au DatabaseHandler (buffer async plus tard)
        - Affiche sur console si level >= INFO
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "channel": self.channel,
            "session_id": self.session_id,
            "level": level,
            "data": data,
        }

        # 1. Fichier JSONL
        try:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception:
            pass

        # 2. DatabaseHandler
        self._db.write(event)

        # 3. Console
        if level in ("INFO", "WARNING", "ERROR"):
            msg = f"[{event_type}] {data}"
            if level == "WARNING":
                self._console.warning(msg)
            elif level == "ERROR":
                self._console.error(msg)
            else:
                self._console.info(msg)

    # ── Convenience shortcuts ────────────────────────────────────

    def log_clip_detected(self, clip_num: int, score: float, détails: dict, auteur: str, repetition_word: str | None, message: str) -> None:
        self.log_event(EventType.CLIP_DETECTED, {
            "clip_num": clip_num,
            "score": round(score, 4),
            "filtres": {k: round(v.get("score_pondéré", 0.0), 4) for k, v in détails.items()},
            "auteur": auteur,  # pseudonymized
            "repetition_word": repetition_word,  # pseudonymized
            "message_excerpt": message[:80],
        })

    def log_clip_generated(self, clip_num: int, score: float, chemin: str | None, duree_sec: float) -> None:
        self.log_event(EventType.CLIP_GENERATED, {
            "clip_num": clip_num,
            "score": round(score, 4),
            "chemin": chemin,
            "duree_sec": round(duree_sec, 1),
        })

    def log_clip_merged(self, clip_num: int, score: float, merged_from: int | None = None) -> None:
        self.log_event(EventType.CLIP_MERGED, {
            "clip_num": clip_num,
            "score": round(score, 4),
            "merged_from": merged_from,
        })

    def log_filter_trigger(self, filtre: str, z_score: float, score_pondere: float, auteur: str) -> None:
        self.log_event(EventType.FILTER_TRIGGER, {
            "filtre": filtre,
            "z_score": round(z_score, 4),
            "score_pondere": round(score_pondere, 4),
            "auteur": auteur,
        })

    def log_calibration_complete(self, filtre: str, samples: int, mean: float, std: float, z_score_threshold: float) -> None:
        self.log_event(EventType.CALIBRATION_COMPLETE, {
            "filtre": filtre,
            "samples": samples,
            "mean": round(mean, 4),
            "std": round(std, 4),
            "z_score_threshold": round(z_score_threshold, 2),
        })

    def _log_review_instance(self, clip_num: int, action: str, user: str) -> None:
        from a3.utils.privacy import pseudonymize
        user_hash = pseudonymize(user) or "unknown"
        event_map = {
            "garder": EventType.REVIEW_GARDER,
            "highlight": EventType.REVIEW_HIGHLIGHT,
            "supprimer": EventType.REVIEW_SUPPRIMER,
        }
        self.log_event(event_map.get(action, EventType.INFO), {
            "clip_num": clip_num,
            "action": action,
            "user": user_hash,
        })

    def log_filter_score(self, filtre: str, score_raw: float, score_pondere: float, auteur: str) -> None:
        self.log_event(EventType.FILTER_SCORE, {
            "filtre": filtre,
            "score_raw": round(score_raw, 4),
            "score_pondere": round(score_pondere, 4),
            "auteur": auteur,
        })

    def log_error(self, component: str, erreur: str, contexte: dict | None = None) -> None:
        self.log_event(EventType.ERROR, {
            "component": component,
            "erreur": erreur,
            "contexte": contexte or {},
        }, level="ERROR")

    # ── Lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme le fichier et flush la DB."""
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass
        self._db.flush()
        self._db.close()

    def __enter__(self) -> "StructuredLogger":
        return self

    def __exit__(self, *args) -> None:
        self.close()
