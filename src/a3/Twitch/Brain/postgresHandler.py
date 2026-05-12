# src/a3/Twitch/Brain/postgresHandler.py
#
# Handler PostgreSQL pour StructuredLogger.
# Bufferise les events en mémoire puis les insère en base (batch).
# Configure via variables d'environnement :
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, DB_SSLMODE
#
# Créé automatiquement les tables à la première connexion.

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from a3.Twitch.Brain.structuredLogger import DatabaseHandler, EventType
from a3.utils.privacy import pseudonymize

log = logging.getLogger("A3")


class PostgresHandler(DatabaseHandler):
    """
    Handler PostgreSQL pour StructuredLogger.
    Bufferise les events en mémoire puis les insère en base via COPY (bulk rapide).
    Crée automatiquement les tables à la première connexion.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        sslmode: str | None = None,
        batch_size: int = 100,
        flush_interval_sec: float = 5.0,
    ) -> None:
        self._host = host or os.getenv("DB_HOST", "localhost")
        self._port = int(port or os.getenv("DB_PORT", "5432"))
        self._user = user or os.getenv("DB_USER", "postgres")
        self._password = password or os.getenv("DB_PASSWORD", "")
        self._database = database or os.getenv("DB_NAME", "a3_db")
        self._sslmode = sslmode or os.getenv("DB_SSLMODE", "prefer")
        self._batch_size = batch_size
        self._flush_interval = flush_interval_sec

        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=20000)
        self._closed = False
        self._worker: threading.Thread | None = None
        self._db: Any = None
        self._cursor: Any = None
        self._tables_created = False
        self._current_session_id: str | None = None
        self._channel_ids: dict[str, str] = {}  # cache name → UUID

        self._connect()
        self._start_worker()

    def set_session_id(self, session_id: str) -> None:
        """Permet de définir le session_id courant pour les liens avec les tables."""
        self._current_session_id = session_id

    def _connect(self) -> None:
        try:
            import psycopg2
            import psycopg2.extras

            self._db = psycopg2.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                dbname=self._database,
                sslmode=self._sslmode,
            )
            self._db.autocommit = False
            self._cursor = self._db.cursor()
            self._psycopg2_extras = psycopg2.extras
            log.info(f"[PostgresHandler] ✅ Connecté à {self._host}:{self._port}/{self._database} (sslmode={self._sslmode})")

            # Créer les tables à la première connexion
            self._creer_tables()

        except ImportError:
            log.error("[PostgresHandler] ❌ psycopg2 non installé — pip install psycopg2-binary")
            self._db = None
            self._cursor = None
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur connexion : {e}")
            self._db = None
            self._cursor = None

    def _creer_tables(self) -> None:
        """Crée toutes les tables si elles n'existent pas déjà."""
        if self._tables_created:
            return

        try:
            # Table sessions
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(16) UNIQUE NOT NULL,
                    channel_id UUID NOT NULL,
                    debut_session TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    fin_session TIMESTAMPTZ,
                    statut VARCHAR(20) DEFAULT 'active',

                    clips_detectes INT DEFAULT 0,
                    clips_rejetes INT DEFAULT 0,
                    clips_gardes INT DEFAULT 0,
                    clips_highlightes INT DEFAULT 0,
                    clips_supprimes INT DEFAULT 0,

                    score_moyen DECIMAL(5,4),
                    score_max DECIMAL(5,4),
                    score_min DECIMAL(5,4),
                    seuil_clip DECIMAL(4,3),
                    poids_filtres JSONB,
                    version_app VARCHAR(20),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Table clips
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS clips (
                    id BIGSERIAL PRIMARY KEY,
                    clip_num INT NOT NULL,
                    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
                    channel_id UUID NOT NULL,
                    chemin_fichier VARCHAR(512),

                    score_final DECIMAL(5,4) NOT NULL,
                    score_unique_authors DECIMAL(5,4),
                    score_message_rate DECIMAL(5,4),
                    score_emotions DECIMAL(5,4),
                    score_emote_density DECIMAL(5,4),
                    score_repetition DECIMAL(5,4),
                    score_clip_activity DECIMAL(5,4),

                    auteur_hash     CHAR(16),
                    repetition_word CHAR(16),
                    filtres_actifs TEXT[],

                    timestamp_trigger TIMESTAMPTZ NOT NULL,
                    duree_calculee_sec DECIMAL(8,1),
                    timestamp_creation TIMESTAMPTZ DEFAULT NOW(),

                    decision VARCHAR(20),
                    reviewer_id BIGINT,
                    reviewer_hash CHAR(16),
                    timestamp_decision TIMESTAMPTZ
                )
            """)

            # Table reviews
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id BIGSERIAL PRIMARY KEY,
                    clip_id BIGINT REFERENCES clips(id) ON DELETE CASCADE,
                    session_id VARCHAR(16) NOT NULL,
                    channel_id UUID NOT NULL,
                    action VARCHAR(20) NOT NULL,
                    user_id BIGINT NOT NULL,
                    username_hash CHAR(16) NOT NULL,
                    timestamp_review TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Table filter_events (auteur_hash = SHA-256 pseudonymized, message_excerpt retiré)
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS filter_events (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(16) NOT NULL,
                    channel_id UUID NOT NULL,
                    event_type VARCHAR(64) NOT NULL,
                    filtre_nom VARCHAR(64),
                    score_raw DECIMAL(10,4),
                    score_pondere DECIMAL(10,4),
                    z_score DECIMAL(8,4),
                    mean_baseline DECIMAL(10,4),
                    std_baseline DECIMAL(10,4),
                    seuil DECIMAL(10,4),
                    auteur_hash CHAR(16),
                    level VARCHAR(10) DEFAULT 'INFO',
                    data JSONB,
                    timestamp TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Table calibration
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS calibration (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(16) NOT NULL,
                    channel_id UUID NOT NULL,
                    filtre_nom VARCHAR(64) NOT NULL,
                    est_calibre BOOLEAN DEFAULT FALSE,
                    samples_count INT DEFAULT 0,
                    timestamp_calibration TIMESTAMPTZ,
                    mean DECIMAL(10,4),
                    std DECIMAL(10,4),
                    min_samples_required INT,
                    z_score_threshold DECIMAL(4,2),
                    mean_fond DECIMAL(10,4),
                    std_fond DECIMAL(10,4),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Table stream_events
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS stream_events (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(16),
                    channel_id UUID,
                    event_type VARCHAR(64) NOT NULL,
                    level VARCHAR(10) DEFAULT 'INFO',
                    message TEXT,
                    component VARCHAR(128),
                    erreur TEXT,
                    data JSONB,
                    timestamp TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Table channels
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    name VARCHAR(64) UNIQUE NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name)")

            # Table snapshots
            self._cursor.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    session_id VARCHAR(16) NOT NULL,
                    channel_id UUID NOT NULL,
                    timestamp_snapshot TIMESTAMPTZ NOT NULL,
                    messages_count INT DEFAULT 0,
                    auteurs_uniques_count INT DEFAULT 0,
                    clips_count INT DEFAULT 0,
                    score_moyen DECIMAL(5,4),
                    message_rate_avg DECIMAL(10,4),
                    emote_density_avg DECIMAL(10,4),
                    filtres_calibres TEXT[],
                    filtres_actifs TEXT[],
                    data JSONB
                )
            """)

            # Index
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_channel_id ON sessions(channel_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_clips_session ON clips(session_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_clips_channel_id ON clips(channel_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_reviews_clip ON reviews(clip_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_filter_events_session ON filter_events(session_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_stream_events_session ON stream_events(session_id)")
            self._cursor.execute("CREATE INDEX IF NOT EXISTS idx_stream_events_channel ON stream_events(channel_id)")

            self._db.commit()
            self._tables_created = True
            log.info("[PostgresHandler] ✅ Tables créées/vérifiées avec succès")

        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur création tables : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while not self._closed:
            batch: list[dict] = []
            try:
                while len(batch) < self._batch_size:
                    try:
                        event = self._queue.get(timeout=self._flush_interval)
                        batch.append(event)
                    except queue.Empty:
                        break
            except Exception:
                if self._closed:
                    break
                continue

            if batch:
                self._insert_batch(batch)

    def write(self, event: dict) -> None:
        """Insère un event et le route vers la table appropriée."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            log.warning("[PostgresHandler] ⚠️ Queue pleine, event droppé")

    def _insert_batch(self, batch: list[dict]) -> None:
        if not self._cursor or not self._db:
            return

        try:
            for event in batch:
                self._inserer_event(event)
            self._db.commit()
            log.debug(f"[PostgresHandler] 📦 {len(batch)} events insérés")
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur insert batch : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def _ensure_channel(self, channel_name: str) -> str:
        """S'assure que le channel existe en DB et retourne son UUID (cache en mémoire)."""
        if channel_name in self._channel_ids:
            return self._channel_ids[channel_name]
        try:
            self._cursor.execute(
                "INSERT INTO channels (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                (channel_name,),
            )
            self._cursor.execute("SELECT id FROM channels WHERE name = %s", (channel_name,))
            row = self._cursor.fetchone()
            if row:
                self._channel_ids[channel_name] = str(row[0])
                return self._channel_ids[channel_name]
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _ensure_channel échoué: {e}")
        return ""

    def _inserer_event(self, event: dict) -> None:
        """Route chaque event vers la bonne table."""
        try:
            channel_name = event.get("channel", "")
            channel_id = self._ensure_channel(channel_name) if channel_name else ""
            event_type = event.get("event_type", "")
            data = event.get("data", {})
            session_id = event.get("session_id", "") or self._current_session_id or ""

            auteur_hash = pseudonymize(data.get("auteur", "")) or ""
            values = (
                session_id,
                channel_id,
                event_type,
                auteur_hash,
                event.get("level", "INFO"),
                json.dumps(data, default=str),
                event.get("timestamp", datetime.now(timezone.utc)),
            )
            self._cursor.execute(
                "INSERT INTO filter_events (session_id, channel_id, event_type, auteur_hash, level, data, timestamp) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                values,
            )

            # Route vers tables spécialisées
            if event_type == EventType.SESSION_START:
                self._insert_session(event, data, session_id, channel_id)
            elif event_type == EventType.SESSION_STOP:
                self._update_session_fin(event, data, session_id)
            elif event_type == EventType.CLIP_DETECTED:
                self._insert_clip(event, data, session_id, channel_id)
            elif event_type == EventType.CLIP_MERGED:
                self._insert_clip_merge(event, data, session_id, channel_id)
            elif event_type in (EventType.REVIEW_GARDER, EventType.REVIEW_HIGHLIGHT, EventType.REVIEW_SUPPRIMER):
                self._insert_review(event, data, session_id, event_type, channel_id)
            elif event_type == EventType.FILTER_CALIBRATED:
                self._insert_calibration(event, data, session_id, channel_id)

            # Insertion non-bloquante dans stream_events pour tous les events listés
            if event_type in (
                EventType.SESSION_START,
                EventType.CLIP_DETECTED,
                EventType.CLIP_MERGED,
                EventType.FILTER_TRIGGER,
                EventType.FILTER_CALIBRATED,
                EventType.CALIBRATION_COMPLETE,
                EventType.ERROR,
            ):
                self._insert_stream_event(event, data, session_id, channel_id)
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _inserer_event échoué: {e}")

    def _insert_session(self, event: dict, data: dict, session_id: str, channel_id: str) -> None:
        """Insère une nouvelle session."""
        try:
            insert_sql = """
                INSERT INTO sessions (session_id, channel_id, debut_session, statut, seuil_clip, poids_filtres, version_app)
                VALUES (%s, %s, %s, 'active', %s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
            """
            self._cursor.execute(insert_sql, (
                session_id,
                channel_id,
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("seuil"),
                json.dumps(data.get("poids", {}), default=str),
                data.get("version_app", "1.0.0"),
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert session échoué: {e}")

    def _update_session_fin(self, event: dict, data: dict, session_id: str) -> None:
        """Met à jour la fin de session."""
        try:
            update_sql = """
                UPDATE sessions SET
                    fin_session = %s,
                    statut = 'stopped',
                    clips_detectes = %s,
                    clips_rejetes = %s,
                    score_moyen = %s,
                    score_max = %s
                WHERE session_id = %s
            """
            self._cursor.execute(update_sql, (
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("clips_detectes", 0),
                data.get("clips_rejetes", 0),
                data.get("score_moyen", 0),
                data.get("score_max", 0),
                session_id,
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Update session fin échoué: {e}")

    def _insert_clip(self, event: dict, data: dict, session_id: str, channel_id: str) -> None:
        """Insère un clip détecté."""
        try:
            insert_sql = """
                INSERT INTO clips (
                    clip_num, session_id, channel_id, score_final,
                    auteur_hash, repetition_word, filtres_actifs,
                    timestamp_trigger, score_unique_authors, score_message_rate,
                    score_emotions, score_emote_density, score_repetition, score_clip_activity
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """

            filtres = data.get("filtres", {})

            self._cursor.execute(insert_sql, (
                data.get("clip_num"),
                session_id,
                channel_id,
                data.get("score", 0),
                # auteur_hash CHAR(16), pseudonymized par Brain
                data.get("auteur"),
                # CHAR(16), hash du mot dominant
                data.get("repetition_word"),
                list(filtres.keys()) if filtres else [],
                event.get("timestamp", datetime.now(timezone.utc)),
                filtres.get("FiltreUniqueAuthors", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreUniqueAuthors"), dict) else 0,
                filtres.get("FiltreMessageRate", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreMessageRate"), dict) else 0,
                filtres.get("FiltreEmotions", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreEmotions"), dict) else 0,
                filtres.get("FiltreEmoteDensity", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreEmoteDensity"), dict) else 0,
                filtres.get("FiltreRepetition", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreRepetition"), dict) else 0,
                filtres.get("FiltreClipActivity", {}).get("score_pondere", 0) if isinstance(filtres.get("FiltreClipActivity"), dict) else 0,
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert clip échoué: {e}")

    def _insert_review(self, event: dict, data: dict, session_id: str, event_type: str, channel_id: str) -> None:
        """Insère une review Discord."""
        try:
            action_map = {
                EventType.REVIEW_GARDER: "garder",
                EventType.REVIEW_HIGHLIGHT: "highlight",
                EventType.REVIEW_SUPPRIMER: "supprimer",
            }
            action = action_map.get(event_type, "")

            insert_sql = """
                INSERT INTO reviews (clip_id, session_id, channel_id, action, user_id, username_hash, timestamp_review)
                VALUES (
                    (SELECT id FROM clips WHERE clip_num = %s AND session_id = %s ORDER BY timestamp_creation DESC LIMIT 1),
                    %s, %s, %s, %s, %s, %s
                )
            """
            username_hash = pseudonymize(data.get("user", "")) or "unknown"
            self._cursor.execute(insert_sql, (
                data.get("clip_num"),
                session_id,
                session_id,
                channel_id,
                action,
                data.get("user_id", 0),
                username_hash,
                event.get("timestamp", datetime.now(timezone.utc)),
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert review échoué: {e}")

    def _insert_calibration(self, event: dict, data: dict, session_id: str, channel_id: str) -> None:
        """Insère les stats de calibration d'un filtre."""
        try:
            insert_sql = """
                INSERT INTO calibration (
                    session_id, channel_id, filtre_nom, est_calibre, samples_count,
                    timestamp_calibration, mean, std, min_samples_required, z_score_threshold,
                    mean_fond, std_fond
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, filtre_nom) DO UPDATE SET
                    est_calibre = EXCLUDED.est_calibre,
                    samples_count = EXCLUDED.samples_count,
                    timestamp_calibration = EXCLUDED.timestamp_calibration,
                    mean = EXCLUDED.mean,
                    std = EXCLUDED.std
            """
            self._cursor.execute(insert_sql, (
                session_id,
                channel_id,
                data.get("filtre_name", ""),
                True,
                data.get("samples", 0),
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("mean", 0),
                data.get("std", 0),
                data.get("min_samples", 50),
                data.get("z_score", 1.8),
                data.get("mean_fond", 0),
                data.get("std_fond", 0),
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert calibration échoué: {e}")

    def _insert_clip_merge(self, event: dict, data: dict, session_id: str, channel_id: str) -> None:
        """Met à jour un clip existant lors d'un merge (ajoute les scores agrégés)."""
        try:
            update_sql = """
                UPDATE clips SET
                    score_final = GREATEST(score_final, %s)
                WHERE clip_num = %s AND session_id = %s
            """
            self._cursor.execute(update_sql, (
                data.get("score", 0),
                data.get("clip_num"),
                session_id,
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert clip merge échoué: {e}")

    def _insert_stream_event(self, event: dict, data: dict, session_id: str, channel_id: str) -> None:
        """Insère un event dans stream_events (non-bloquant, async via le worker)."""
        try:
            insert_sql = """
                INSERT INTO stream_events (session_id, channel_id, event_type, level, message, component, erreur, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            message = data.get("message", "") or str(data)
            component = data.get("component", "") or data.get("filtre", "") or ""
            erreur = data.get("erreur", "") or ""

            self._cursor.execute(insert_sql, (
                session_id,
                channel_id,
                event.get("event_type", ""),
                event.get("level", "INFO"),
                message,
                component,
                erreur,
                self._psycopg2_extras.Json(data, default=str),
            ))
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ Insert stream_event échoué: {e}")

    def flush(self) -> None:
        batch: list[dict] = []
        while True:
            try:
                event = self._queue.get_nowait()
                batch.append(event)
            except queue.Empty:
                break
        if batch:
            self._insert_batch(batch)

    def close(self) -> None:
        self._closed = True
        if self._worker:
            self._worker.join(timeout=5.0)
        self.flush()
        if self._cursor:
            try:
                self._cursor.close()
            except Exception:
                pass
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
        log.info("[PostgresHandler] 🔌 Connexion fermée")