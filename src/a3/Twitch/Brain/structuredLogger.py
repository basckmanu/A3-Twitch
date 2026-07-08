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

    # Chat aggregation (dataset ML)
    CHAT_WINDOW = "chat_window"

    # État périodique (monitoring/dashboard)
    SNAPSHOT = "snapshot"

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
    REVIEW_EXPIRE = "review_expire"  # auto-rejeté, faute de review humaine dans le délai

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
    """
    Logger structuré JSON.
    Usage :
        logger = StructuredLogger(channel="kamet0")
        logger.log_event(EventType.CLIP_DETECTED, {"clip_num": 1, "score": 0.72})
    """

    def log_review(
        self,
        clip_num: int,
        action: str,
        user: str,
        user_id: int = 0,
        reaction_time_sec: float | None = None,
        channel: str | None = None,
        reason: str | None = None,
        user_is_hash: bool = False,
    ) -> None:
        """Log une review Discord. user_id brut jamais stocké en DB (RGPD).

        `channel` doit être le channel d'origine du clip reviewé — cette instance de
        StructuredLogger est partagée entre tous les streams surveillés, donc sans ce
        paramètre la review serait attribuée à self.channel (le 1er channel démarré ou
        "multi"), et le handler DB (qui route par channel_id → session) la perdrait
        silencieusement.

        `reason` : catégorie choisie par le reviewer (raison de garder/highlight/
        supprimer) — alimente `reviews.reason` pour l'analyse des données.
        `user_is_hash` : True si `user` est déjà un hash pseudonymisé (cas d'une
        review reconstruite depuis pending_reviews.json après un redémarrage, où
        le nom brut n'a jamais été persisté) — évite de le hasher une seconde fois,
        ce qui casserait la cohérence du hash reviewer entre les deux chemins.
        """
        from a3.utils.privacy import pseudonymize
        user_hash = (user if user_is_hash else pseudonymize(user)) or "unknown"
        event_map = {
            "garder": EventType.REVIEW_GARDER,
            "highlight": EventType.REVIEW_HIGHLIGHT,
            "supprimer": EventType.REVIEW_SUPPRIMER,
            "expire": EventType.REVIEW_EXPIRE,
        }
        self.log_event(event_map.get(action, EventType.INFO), {
            "clip_num": clip_num,
            "action": action,
            "user": user_hash,
            "user_id": 0,  # jamais d'ID Discord brut en base
            "reaction_time_sec": reaction_time_sec,
            "reason": reason,
        }, channel=channel)

    def __init__(
        self,
        channel: str,
        session_id: str | None = None,
        output_dir: Path | None = None,
        db_handler: DatabaseHandler | None = None,
    ) -> None:
        self.channel = channel
        self.session_id = session_id or uuid.uuid4().hex[:16]

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
        """Auto-detects DB via DB_TYPE: 'postgres' | absent → Dummy."""
        from typing import Any
        db_type = os.getenv("DB_TYPE", "").lower()

        if db_type == "postgres":
            try:
                from a3.Twitch.Brain.postgresHandler import PostgresHandler
                handler: Any = PostgresHandler()
                if handler._db is not None:
                    self._console.info("[StructuredLogger] 📦 PostgreSQL handler activé")
                    return handler
                self._console.warning("[StructuredLogger] ⚠️ PostgreSQL configuré mais connexion échouée")
            except Exception as e:
                self._console.warning(f"[StructuredLogger] ⚠️ PostgreSQL non disponible : {e}")

        elif db_type:
            self._console.warning(f"[StructuredLogger] ⚠️ DB_TYPE inconnu : '{db_type}' (seule valeur supportée : 'postgres')")

        return DummyDBHandler()

    # ── Public API ──────────────────────────────────────────────────

    def log_event(self, event_type: str, data: dict, level: str = "INFO", channel: str | None = None, session_id: str | None = None) -> None:
        """
        Log un event structuré.
        - Écrit dans le fichier JSONL
        - Passe au DatabaseHandler (buffer async plus tard)
        - Affiche sur console si level >= INFO
        """
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "channel": channel if channel is not None else self.channel,
            "session_id": session_id if session_id is not None else self.session_id,
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
        self._console.debug(f"[StructuredLogger] write() → type={type(self._db).__name__}  event_type={event_type}")
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

    def log_clip_detected(
        self,
        clip_num: int,
        score: float,
        détails: dict,
        auteur: str,
        repetition_word: str | None,
        message: str,
        viewer_count: int | None = None,
        game_category: str | None = None,
        stream_language: str | None = None,
        channel: str | None = None,
    ) -> None:
        self.log_event(EventType.CLIP_DETECTED, {
            "channel": channel or self.channel,
            "clip_num": clip_num,
            "score": round(score, 4),
            "filtres": {k: round(v.get("score_pondéré", 0.0), 4) for k, v in détails.items()},
            "auteur": auteur,
            "repetition_word": repetition_word,
            "message_excerpt": message[:80],
            "viewer_count": viewer_count,
            "game_category": game_category,
            "stream_language": stream_language,
        }, channel=channel)

    def log_clip_generated(self, clip_num: int, score: float, chemin: str | None, duree_sec: float, channel: str | None = None) -> None:
        self.log_event(EventType.CLIP_GENERATED, {
            "channel": channel or self.channel,
            "clip_num": clip_num,
            "score": round(score, 4),
            "chemin": chemin,
            "duree_sec": round(duree_sec, 1),
        }, channel=channel)

    def log_clip_merged(self, clip_num: int, score: float, merged_from: int | None = None, channel: str | None = None) -> None:
        self.log_event(EventType.CLIP_MERGED, {
            "channel": channel or self.channel,
            "clip_num": clip_num,
            "score": round(score, 4),
            "merged_from": merged_from,
        }, channel=channel)

    def log_filter_trigger(self, filtre: str, z_score: float, score_pondere: float, auteur: str, channel: str | None = None) -> None:
        self.log_event(EventType.FILTER_TRIGGER, {
            "channel": channel or self.channel,
            "filtre": filtre,
            "z_score": round(z_score, 4),
            "score_pondere": round(score_pondere, 4),
            "auteur": auteur,
        }, channel=channel)

    def log_calibration_complete(self, filtre: str, samples: int, mean: float, std: float, z_score_threshold: float, channel: str | None = None) -> None:
        self.log_event(EventType.CALIBRATION_COMPLETE, {
            "channel": channel or self.channel,
            "filtre": filtre,
            "samples": samples,
            "mean": round(mean, 4),
            "std": round(std, 4),
            "z_score_threshold": round(z_score_threshold, 2),
        }, channel=channel)

    def log_filter_score(self, filtre: str, score_raw: float, score_pondere: float, auteur: str, channel: str | None = None) -> None:
        self.log_event(EventType.FILTER_SCORE, {
            "channel": channel or self.channel,
            "filtre": filtre,
            "score_raw": round(score_raw, 4),
            "score_pondere": round(score_pondere, 4),
            "auteur": auteur,
        }, channel=channel)

    def log_chat_window(
        self,
        window_start: datetime,
        window_end: datetime,
        message_count: int,
        unique_authors_count: int,
        message_rate_avg: float,
        emote_density_avg: float,
        emotion_score_avg: float,
        repetition_score_avg: float,
        clip_activity_score: float,
        clip_num: int | None = None,
        viewer_count: int | None = None,
        game_category: str | None = None,
        channel: str | None = None,
    ) -> None:
        """Log une fenêtre de chat agrégée — dataset pour un futur entraînement
        supervisé (features de fenêtre → label de review une fois le clip traité)."""
        self.log_event(EventType.CHAT_WINDOW, {
            "channel": channel or self.channel,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "message_count": message_count,
            "unique_authors_count": unique_authors_count,
            "message_rate_avg": round(message_rate_avg, 4),
            "emote_density_avg": round(emote_density_avg, 4),
            "emotion_score_avg": round(emotion_score_avg, 4),
            "repetition_score_avg": round(repetition_score_avg, 4),
            "clip_activity_score": round(clip_activity_score, 4),
            "clip_num": clip_num,
            "viewer_count": viewer_count,
            "game_category": game_category,
        }, channel=channel)

    def log_snapshot(
        self,
        timestamp_snapshot: datetime,
        messages_count: int,
        auteurs_uniques_count: int,
        clips_count: int,
        score_moyen: float,
        message_rate_avg: float,
        emote_density_avg: float,
        filtres_calibres: list[str],
        filtres_actifs: list[str],
        channel: str | None = None,
    ) -> None:
        """Log un instantané périodique de l'état d'un channel (table `snapshots`)
        — pensé pour un dashboard de monitoring, distinct du dataset ML chat_windows.

        La table `snapshots` déployée en base stocke filters_calibrated/filters_active
        comme des compteurs (INT) — les listes complètes de noms de filtres sont
        conservées telles quelles dans le JSONL pour le détail, au cas où utile."""
        self.log_event(EventType.SNAPSHOT, {
            "channel": channel or self.channel,
            "timestamp_snapshot": timestamp_snapshot.isoformat(),
            "messages_count": messages_count,
            "auteurs_uniques_count": auteurs_uniques_count,
            "clips_count": clips_count,
            "score_moyen": round(score_moyen, 4),
            "message_rate_avg": round(message_rate_avg, 4),
            "emote_density_avg": round(emote_density_avg, 4),
            "filtres_calibres": filtres_calibres,
            "filtres_actifs": filtres_actifs,
            "filtres_calibres_count": len(filtres_calibres),
            "filtres_actifs_count": len(filtres_actifs),
        }, channel=channel)

    def log_error(self, component: str, erreur: str, contexte: dict | None = None, channel: str | None = None) -> None:
        self.log_event(EventType.ERROR, {
            "channel": channel or self.channel,
            "component": component,
            "erreur": erreur,
            "contexte": contexte or {},
        }, level="ERROR", channel=channel)

    # ── Lifecycle ────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme le fichier et flush la DB.

        Ne pas appeler self._db.flush() ici : le worker thread du DB handler
        tourne encore à ce stade, et flush() (appelé depuis ce thread-ci)
        utiliserait le même curseur psycopg2 en même temps que lui — un
        curseur psycopg2 n'est pas thread-safe pour un accès concurrent.
        self._db.close() fait déjà les choses dans le bon ordre : il arrête
        le worker (join) puis flush une fois qu'il ne tourne plus."""
        try:
            self._file.flush()
            self._file.close()
        except Exception:
            pass
        self._db.close()

    def __enter__(self) -> "StructuredLogger":
        return self

    def __exit__(self, *args) -> None:
        self.close()
