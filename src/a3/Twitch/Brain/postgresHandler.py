# src/a3/Twitch/Brain/postgresHandler.py
#
# Handler PostgreSQL pour StructuredLogger.
# Bufferise les events en mémoire puis les insère en base (batch).
# Configure via variables d'environnement :
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, DB_SSLMODE
#
# Créé automatiquement les tables à la première connexion.

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
        self._port = int(port or os.getenv("DB_PORT", "5432"))  # type: ignore[arg-type]
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
        self._psycopg2_extras: Any = None  # initialisé avant psycopg2.connect() dans _connect()
        self._tables_created = False
        self._session_pks: dict[str, int] = {}
        self._channel_ids: dict[str, str] = {}
        self._model_version_ids: dict[str, int] = {}
        self._default_org_id: str = ""
        self._current_session_id: str = ""

        self._connect()
        self._start_worker()

    def set_session_id(self, session_id: str) -> None:
        """Permet de définir le session_id courant pour les liens avec les tables."""
        self._current_session_id = session_id

    def _connect(self) -> None:
        try:
            import psycopg2
            import psycopg2.extras
            # Doit être assigné avant connect() pour rester disponible même si connect échoue
            self._psycopg2_extras = psycopg2.extras

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

    def _exec_ddl(self, sql: str, label: str = "") -> None:
        """Exécute un statement DDL dans un savepoint isolé.
        Si le statement échoue, le savepoint est rollbacké et l'erreur loguée
        sans avorter la transaction globale."""
        sp = "_ddl"
        try:
            self._cursor.execute(f"SAVEPOINT {sp}")
            self._cursor.execute(sql)
            self._cursor.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception as e:
            self._cursor.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            log.debug(f"[PostgresHandler] DDL ignoré ({label or sql[:60]}): {e}")

    def _exec_safe(self, sql: str, params: tuple, label: str = "") -> bool:
        """Exécute un INSERT/UPDATE secondaire (best-effort) dans un savepoint isolé.
        Si le statement échoue, seul ce savepoint est rollbacké — la transaction
        globale (et l'event principal qui l'accompagne) n'est pas invalidée.
        Retourne True si l'exécution a réussi."""
        sp = "_dml"
        try:
            self._cursor.execute(f"SAVEPOINT {sp}")
            self._cursor.execute(sql, params)
            self._cursor.execute(f"RELEASE SAVEPOINT {sp}")
            return True
        except Exception as e:
            self._cursor.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            log.debug(f"[PostgresHandler] ⚠️ {label or sql[:60]} ignoré: {e}")
            return False

    def _creer_tables(self) -> None:
        """Crée toutes les tables + migrations. Privacy-by-design : aucune donnée personnelle brute."""
        if self._tables_created:
            return

        # Nettoyer toute transaction avortée héritée d'une session précédente
        try:
            self._db.rollback()
        except Exception:
            pass

        # ── Entités de base ────────────────────────────────────────────

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS organizations (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(128) UNIQUE NOT NULL,
                plan VARCHAR(20) DEFAULT 'self-hosted',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """, "organizations")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS model_versions (
                id SERIAL PRIMARY KEY,
                version_tag VARCHAR(32) UNIQUE NOT NULL,
                seuil_clip DECIMAL(5,4),
                poids_filtres JSONB,
                deployed_at TIMESTAMPTZ DEFAULT NOW(),
                description TEXT
            )
        """, "model_versions")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS channels (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(64) UNIQUE NOT NULL,
                org_id UUID REFERENCES organizations(id),
                twitch_id VARCHAR(64),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """, "channels")
        self._exec_ddl("CREATE INDEX IF NOT EXISTS idx_channels_name ON channels(name)", "idx_channels_name")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS sessions (
                id BIGSERIAL PRIMARY KEY,
                channel_id UUID NOT NULL,
                org_id UUID REFERENCES organizations(id),
                model_version_id INT REFERENCES model_versions(id),
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ,
                duration_seconds INT,
                avg_viewers INT,
                clips_detected INT DEFAULT 0,
                clips_validated INT DEFAULT 0,
                clips_rejected INT DEFAULT 0,
                clips_highlighted INT DEFAULT 0,
                score_avg FLOAT,
                score_max FLOAT,
                status VARCHAR(20) DEFAULT 'active',
                version VARCHAR(20)
            )
        """, "sessions")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS clips (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
                clip_num INT NOT NULL,
                channel_id UUID NOT NULL,
                model_version_id INT REFERENCES model_versions(id),

                score_final DECIMAL(5,4) NOT NULL,
                score_components JSONB,

                trigger_author_hash CHAR(16),
                repetition_word CHAR(16),

                file_path_hq TEXT,
                file_path_preview TEXT,
                duration_seconds DECIMAL(8,1),

                viewer_count INT,
                game_category VARCHAR(128),
                stream_language CHAR(2),

                detected_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),

                decision VARCHAR(20),
                reviewer_hash CHAR(16),
                reviewed_at TIMESTAMPTZ,

                ml_features JSONB,
                ai_confidence FLOAT
            )
        """, "clips")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                org_id UUID REFERENCES organizations(id),
                discord_hash CHAR(16) UNIQUE NOT NULL,
                role VARCHAR(20) DEFAULT 'reviewer',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """, "users")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS reviews (
                id BIGSERIAL PRIMARY KEY,
                clip_id BIGINT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
                session_id BIGINT REFERENCES sessions(id),
                action VARCHAR(20) NOT NULL,
                reviewer_hash CHAR(16) NOT NULL,
                latency_ms INT,
                reaction_time_sec DECIMAL(6,1),
                is_first_review BOOLEAN DEFAULT TRUE,
                reviewed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """, "reviews")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS chat_windows (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
                channel_id UUID NOT NULL,
                window_start TIMESTAMPTZ NOT NULL,
                window_end TIMESTAMPTZ NOT NULL,

                message_count INT DEFAULT 0,
                unique_authors_count INT DEFAULT 0,
                message_rate_avg DECIMAL(8,4),
                emote_density_avg DECIMAL(8,4),
                emotion_score_avg DECIMAL(8,4),
                repetition_score_avg DECIMAL(8,4),
                clip_activity_score DECIMAL(8,4),

                viewer_count INT,
                game_category VARCHAR(128),

                triggered_clip_id BIGINT REFERENCES clips(id) NULL,
                label VARCHAR(20) NULL
            )
        """, "chat_windows")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS filter_performance (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
                model_version_id INT REFERENCES model_versions(id),
                channel_id UUID NOT NULL,
                filter_name VARCHAR(64) NOT NULL,
                trigger_count INT DEFAULT 0,
                true_positive_count INT DEFAULT 0,
                computed_at TIMESTAMPTZ DEFAULT NOW()
            )
        """, "filter_performance")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS filter_events (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
                channel_id UUID NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                filter_name VARCHAR(64),
                score_raw DECIMAL(10,4),
                score_weighted DECIMAL(10,4),
                z_score DECIMAL(8,4),
                threshold DECIMAL(10,4),
                author_id CHAR(16),
                is_triggered BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            )
        """, "filter_events")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS calibration (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
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
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (session_id, filtre_nom)
            )
        """, "calibration")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS stream_events (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
                channel_id UUID,
                event_type VARCHAR(64) NOT NULL,
                level VARCHAR(10) DEFAULT 'INFO',
                message TEXT,
                component VARCHAR(128),
                erreur TEXT,
                data JSONB,
                timestamp TIMESTAMPTZ DEFAULT NOW()
            )
        """, "stream_events")

        self._exec_ddl("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id BIGSERIAL PRIMARY KEY,
                session_id BIGINT REFERENCES sessions(id),
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
        """, "snapshots")

        # ── Index ──────────────────────────────────────────────────────
        for sql, label in [
            ("CREATE INDEX IF NOT EXISTS idx_sessions_channel_id ON sessions(channel_id)", "idx_sessions_channel_id"),
            ("CREATE INDEX IF NOT EXISTS idx_sessions_model ON sessions(model_version_id)", "idx_sessions_model"),
            ("CREATE INDEX IF NOT EXISTS idx_clips_session ON clips(session_id)", "idx_clips_session"),
            ("CREATE INDEX IF NOT EXISTS idx_clips_channel_id ON clips(channel_id)", "idx_clips_channel_id"),
            ("CREATE INDEX IF NOT EXISTS idx_clips_decision ON clips(decision)", "idx_clips_decision"),
            ("CREATE INDEX IF NOT EXISTS idx_reviews_clip ON reviews(clip_id)", "idx_reviews_clip"),
            ("CREATE INDEX IF NOT EXISTS idx_reviews_session ON reviews(session_id)", "idx_reviews_session"),
            ("CREATE INDEX IF NOT EXISTS idx_reviews_reviewer ON reviews(reviewer_hash)", "idx_reviews_reviewer"),
            ("CREATE INDEX IF NOT EXISTS idx_filter_events_session ON filter_events(session_id)", "idx_filter_events_session"),
            ("CREATE INDEX IF NOT EXISTS idx_stream_events_session ON stream_events(session_id)", "idx_stream_events_session"),
            ("CREATE INDEX IF NOT EXISTS idx_stream_events_channel ON stream_events(channel_id)", "idx_stream_events_channel"),
            ("CREATE INDEX IF NOT EXISTS idx_calibration_session ON calibration(session_id)", "idx_calibration_session"),
            ("CREATE INDEX IF NOT EXISTS idx_chat_windows_session ON chat_windows(session_id)", "idx_chat_windows_session"),
            ("CREATE INDEX IF NOT EXISTS idx_chat_windows_start ON chat_windows(window_start)", "idx_chat_windows_start"),
            ("CREATE UNIQUE INDEX IF NOT EXISTS uq_clips_session_num ON clips(session_id, clip_num)", "uq_clips_session_num"),
        ]:
            self._exec_ddl(sql, label)

        # ── Migrations (tables existantes) ─────────────────────────────
        for sql in [
            "ALTER TABLE channels ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id)",
            "ALTER TABLE channels ADD COLUMN IF NOT EXISTS twitch_id VARCHAR(64)",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id)",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS model_version_id INT REFERENCES model_versions(id)",
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS avg_viewers INT",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS model_version_id INT REFERENCES model_versions(id)",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS viewer_count INT",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS game_category VARCHAR(128)",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS stream_language CHAR(2)",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS score_components JSONB",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS ml_features JSONB",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS ai_confidence FLOAT",
            "ALTER TABLE clips ADD COLUMN IF NOT EXISTS file_path_preview TEXT",
            "ALTER TABLE clips DROP COLUMN IF EXISTS reviewer_id",
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS reaction_time_sec DECIMAL(6,1)",
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS is_first_review BOOLEAN DEFAULT TRUE",
            "ALTER TABLE reviews ADD COLUMN IF NOT EXISTS latency_ms INT",
            "ALTER TABLE reviews DROP COLUMN IF EXISTS user_id",
            "ALTER TABLE reviews DROP COLUMN IF EXISTS channel_id",
            "ALTER TABLE stream_events ADD COLUMN IF NOT EXISTS erreur TEXT",
            "ALTER TABLE stream_events ADD COLUMN IF NOT EXISTS component VARCHAR(128)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_calibration_session_filtre ON calibration(session_id, filtre_nom)",
        ]:
            self._exec_ddl(sql)

        # ── Renommages colonnes clips/reviews héritées d'un schéma pré-refonte ──
        for old_col, new_col, table in [
            ("chemin_fichier", "file_path_hq", "clips"),
            ("auteur_hash", "trigger_author_hash", "clips"),
            ("timestamp_trigger", "detected_at", "clips"),
            ("duree_calculee_sec", "duration_seconds", "clips"),
            ("timestamp_creation", "created_at", "clips"),
            ("timestamp_decision", "reviewed_at", "clips"),
            ("timestamp_review", "reviewed_at", "reviews"),
        ]:
            self._exec_ddl(f"""
                DO $$ BEGIN
                    IF EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='{table}' AND column_name='{old_col}')
                    AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                                   WHERE table_name='{table}' AND column_name='{new_col}') THEN
                        ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col};
                    END IF;
                END $$;
            """, f"rename {table}.{old_col}→{new_col}")

        self._exec_ddl("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='clips' AND column_name='clip_number') THEN
                    ALTER TABLE clips RENAME COLUMN clip_number TO clip_num;
                END IF;
            END $$;
        """, "rename clip_number→clip_num")

        self._exec_ddl("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name='reviews' AND column_name='username_hash')
                AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='reviews' AND column_name='reviewer_hash') THEN
                    ALTER TABLE reviews RENAME COLUMN username_hash TO reviewer_hash;
                END IF;
            END $$;
        """, "rename username_hash→reviewer_hash")

        self._ensure_partitions()

        try:
            self._db.commit()
            self._tables_created = True
            log.info("[PostgresHandler] ✅ Tables créées/vérifiées avec succès")
            self._seed_defaults()
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Commit final échoué : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def _ensure_partitions(self) -> None:
        """Crée les partitions mensuelles manquantes de stream_events/filter_events
        (mois courant + 2 mois suivants) — sans ça, tout INSERT au-delà de la
        dernière partition existante échoue avec "no partition of relation found"
        et invalide la transaction entière (session/clip compris)."""
        import calendar
        from datetime import date

        today = date.today()
        for offset in range(3):
            month_index = today.month - 1 + offset
            year = today.year + month_index // 12
            month = month_index % 12 + 1
            start = date(year, month, 1)
            last_day = calendar.monthrange(year, month)[1]
            end = date(year, month, last_day) + __import__("datetime").timedelta(days=1)
            suffix = f"{year}_m{month:02d}"

            for table in ("stream_events", "filter_events"):
                self._exec_ddl(
                    f"""CREATE TABLE IF NOT EXISTS {table}_{suffix}
                        PARTITION OF {table} FOR VALUES FROM ('{start}') TO ('{end}')""",
                    f"partition {table}_{suffix}",
                )

    def _seed_defaults(self) -> None:
        """Insère l'organisation par défaut (self-hosted) si absente."""
        try:
            self._cursor.execute(
                "INSERT INTO organizations (name, plan) VALUES ('default', 'self-hosted') ON CONFLICT (name) DO NOTHING"
            )
            self._cursor.execute("SELECT id FROM organizations WHERE name = 'default'")
            row = self._cursor.fetchone()
            if row:
                self._default_org_id = str(row[0])
            self._db.commit()
            log.debug(f"[PostgresHandler] 🏢 org par défaut : {self._default_org_id}")
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _seed_defaults : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def _get_or_create_model_version(self, data: dict) -> int | None:
        """Crée ou retrouve une version de modèle à partir des poids/seuil de la session."""
        import hashlib
        import json as _json
        try:
            seuil = float(data.get("seuil", 0.42))
            poids = data.get("poids", {})
            config_str = _json.dumps({"seuil": seuil, "poids": poids}, sort_keys=True)
            config_hash = hashlib.md5(config_str.encode()).hexdigest()[:8]
            version_tag = f"auto-{config_hash}"

            self._cursor.execute(
                "INSERT INTO model_versions (version_tag, seuil_clip, poids_filtres) "
                "VALUES (%s, %s, %s) ON CONFLICT (version_tag) DO NOTHING",
                (version_tag, seuil, self._psycopg2_extras.Json(poids)),
            )
            self._cursor.execute("SELECT id FROM model_versions WHERE version_tag = %s", (version_tag,))
            row = self._cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _get_or_create_model_version : {e}")
            return None

    def _ensure_user(self, reviewer_hash: str) -> None:
        """Upsert reviewer dans users (hash uniquement, jamais de nom ni d'ID brut)."""
        if not reviewer_hash or reviewer_hash == "unknown":
            return
        try:
            self._cursor.execute(
                "INSERT INTO users (discord_hash, org_id) VALUES (%s, %s) ON CONFLICT (discord_hash) DO NOTHING",
                (reviewer_hash, self._default_org_id or None),
            )
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _ensure_user : {e}")

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
            log.debug(f"[PostgresHandler] 📤 write() — event_type={event.get('event_type')!r}  queue_size≈{self._queue.qsize()}")
        except queue.Full:
            log.warning("[PostgresHandler] ⚠️ Queue pleine, event droppé")

    def _reconnect(self) -> bool:
        log.info("[PostgresHandler] 🔄 Tentative de reconnexion...")
        try:
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
            self._cursor = None
            self._db = None
            self._tables_created = False
            self._connect()
            return self._db is not None
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Reconnexion échouée : {e}")
            return False

    @staticmethod
    def _is_connection_error(e: Exception) -> bool:
        msg = str(e).lower()
        return any(k in msg for k in ("connection", "broken pipe", "gone away", "closed", "refused", "timeout"))

    def _insert_batch(self, batch: list[dict]) -> None:
        if not self._cursor or not self._db:
            if not self._reconnect():
                log.warning(f"[PostgresHandler] ⚠️ DB indisponible — {len(batch)} events perdus")
                return

        try:
            log.debug(f"[PostgresHandler] 🔍 _insert_batch — {len(batch)} events")
            for event in batch:
                event_type = event.get("event_type", "?")
                try:
                    self._inserer_event(event)
                    self._db.commit()
                except Exception as exc:
                    import traceback
                    log.error(f"[PostgresHandler] ❌ event ÉCHOUÉ — event_type={event_type!r}:\n{traceback.format_exc()}")
                    try:
                        self._db.rollback()
                    except Exception:
                        pass
                    if self._is_connection_error(exc) and self._reconnect():
                        try:
                            self._inserer_event(event)
                            self._db.commit()
                        except Exception:
                            pass
            log.debug(f"[PostgresHandler] ✅ _insert_batch — {len(batch)} events traités")
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur insert batch globale : {e}")
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
                log.debug(f"[PostgresHandler] ✅ _ensure_channel('{channel_name}') → id={self._channel_ids[channel_name]}")
                return self._channel_ids[channel_name]
        except Exception as e:
            log.debug(f"[PostgresHandler] ⚠️ _ensure_channel échoué: {e}")
        return ""

    def _inserer_event(self, event: dict) -> None:
        """Route chaque event vers la bonne table."""
        try:
            channel_name = event.get("channel", "")
            # _ensure_channel peut retourner "" en cas d'échec — normaliser en None
            channel_id = (self._ensure_channel(channel_name) or None) if channel_name else None
            event_type = event.get("event_type", "")
            data = event.get("data", {})
            session_id = event.get("session_id", "") or self._current_session_id or ""

            log.info(f"[PostgresHandler] 📥 _inserer_event — event_type={event_type!r}  channel={channel_name!r}  session_id={session_id!r}")

            auteur_hash = pseudonymize(data.get("auteur", "")) or None
            session_pk = self._session_pks.get(channel_name)
            if session_pk is not None and data.get("filtre"):
                self._exec_safe(
                    """INSERT INTO filter_events
                    (session_id, channel_id, filter_name, event_type, score_weighted, z_score, author_id, is_triggered, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        session_pk, channel_id, data.get("filtre"), event_type,
                        data.get("score_pondere"), data.get("z_score"), auteur_hash,
                        data.get("is_triggered", False),
                        event.get("timestamp", datetime.now(timezone.utc)),
                    ),
                    "filter_events insert",
                )
            elif session_pk is not None:
                log.debug(f"[PostgresHandler] ⏭ filter_events ignoré — pas de filtre dans data pour event={event_type!r}")

            # Route vers tables spécialisées
            if event_type == EventType.SESSION_START:
                self._insert_session(event, data, session_id, channel_id, channel_name)
            elif event_type == EventType.SESSION_STOP:
                self._update_session_fin(event, data, session_id, channel_name)
            elif event_type == EventType.CLIP_DETECTED:
                self._insert_clip(event, data, session_id, channel_id, channel_name)
            elif event_type == EventType.CLIP_GENERATED:
                self._update_clip_generated(event, data, session_id, channel_id, channel_name)
            elif event_type == EventType.CLIP_MERGED:
                self._insert_clip_merge(event, data, session_id, channel_id, channel_name)
            elif event_type in (EventType.REVIEW_GARDER, EventType.REVIEW_HIGHLIGHT, EventType.REVIEW_SUPPRIMER):
                self._insert_review(event, data, session_id, event_type, channel_id, channel_name)
            elif event_type == EventType.FILTER_CALIBRATED:
                self._insert_calibration(event, data, session_id, channel_id, channel_name)

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
                self._insert_stream_event(event, data, session_id, channel_id, channel_name)
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _inserer_event — EXCEPTION COMPLETE:\n{traceback.format_exc()}")

    def _insert_session(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Insère une nouvelle session avec version du modèle et organisation."""
        log.info(f"[PostgresHandler] 🚀 _insert_session — channel={channel_name!r}  channel_id={channel_id!r}")
        try:
            version_id = self._get_or_create_model_version(data)
            if version_id:
                self._model_version_ids[channel_name] = version_id

            # Une session 'active' encore ouverte pour ce channel n'a pu l'être que
            # suite à un arrêt non-propre (crash / kill) du process précédent — on la
            # clôture pour ne pas accumuler des sessions zombies indéfiniment.
            if channel_id:
                self._cursor.execute(
                    """UPDATE sessions SET status = 'interrupted', ended_at = %s
                       WHERE channel_id = %s AND status = 'active'""",
                    (event.get("timestamp", datetime.now(timezone.utc)), channel_id),
                )
                if self._cursor.rowcount:
                    log.warning(f"[PostgresHandler] ⚠️ {self._cursor.rowcount} session(s) 'active' zombie(s) clôturée(s) pour channel={channel_name!r}")

            insert_sql = """
                INSERT INTO sessions (channel_id, org_id, model_version_id, started_at, status, version)
                VALUES (%s, %s, %s, %s, 'active', %s)
                RETURNING id
            """
            self._cursor.execute(insert_sql, (
                channel_id if channel_id else None,
                self._default_org_id or None,
                version_id,
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("version_app", "1.0.0"),
            ))
            row = self._cursor.fetchone()
            if row:
                pk = row[0]
                self._session_pks[channel_name] = pk
                log.info(f"[PostgresHandler] ✅ INSERT sessions OK — id={pk}  version_id={version_id}")
            else:
                log.error(f"[PostgresHandler] ❌ _insert_session — fetchone() None pour channel={channel_name!r}")
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _insert_session ÉCHEC — channel={channel_name!r}\n{traceback.format_exc()}")

    def _update_session_fin(self, event: dict, data: dict, session_id: str, channel_name: str) -> None:
        """Met à jour la fin de session via la PK stockée dans _session_pks."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            log.warning(f"[PostgresHandler] ⚠️ _update_session_fin — pk None pour channel_name={channel_name!r}, ignoré")
            return
        try:
            update_sql = """
                UPDATE sessions SET
                    ended_at = %s,
                    duration_seconds = %s,
                    clips_detected = %s,
                    clips_validated = %s,
                    clips_rejected = %s,
                    score_avg = %s,
                    score_max = %s,
                    status = 'ended'
                WHERE id = %s
            """
            self._cursor.execute(update_sql, (
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("duree_session_sec", 0),
                data.get("clips_detectes", 0),
                data.get("clips_gardes", 0),
                data.get("clips_rejetes", 0),
                data.get("score_moyen", 0),
                data.get("score_max", 0),
                pk,
            ))
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _update_session_fin ÉCHEC\n{traceback.format_exc()}")

    def _insert_clip(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Insère un clip détecté."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            log.warning(f"[PostgresHandler] ⚠️ _insert_clip — pk None pour channel_name={channel_name!r}, ignoré")
            return
        try:
            version_id = self._model_version_ids.get(channel_name)
            filtres = data.get("filtres") or {}
            insert_sql = """
                INSERT INTO clips (
                    session_id, clip_num, channel_id, model_version_id,
                    score_final, score_components, trigger_author_hash, repetition_word, detected_at,
                    viewer_count, game_category, stream_language
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, clip_num) DO UPDATE SET
                    score_final = GREATEST(clips.score_final, EXCLUDED.score_final),
                    score_components = EXCLUDED.score_components,
                    detected_at = EXCLUDED.detected_at
            """
            self._cursor.execute(insert_sql, (
                pk,
                data.get("clip_num"),
                channel_id if channel_id else None,
                version_id,
                data.get("score", 0),
                self._psycopg2_extras.Json(filtres) if filtres else None,
                data.get("auteur") or None,
                data.get("repetition_word") or None,
                event.get("timestamp", datetime.now(timezone.utc)),
                data.get("viewer_count") or None,
                data.get("game_category") or None,
                data.get("stream_language") or None,
            ))
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _insert_clip ÉCHEC\n{traceback.format_exc()}")

    def _insert_review(self, event: dict, data: dict, session_id: str, event_type: str, channel_id: str | None, channel_name: str) -> None:
        """Insère une review Discord. Aucun ID personnel brut — hash uniquement (RGPD art. 25)."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            log.warning(f"[PostgresHandler] ⚠️ _insert_review — pk None pour channel={channel_name!r}, ignoré")
            return
        try:
            action_map = {
                EventType.REVIEW_GARDER: "garder",
                EventType.REVIEW_HIGHLIGHT: "highlight",
                EventType.REVIEW_SUPPRIMER: "supprimer",
            }
            action = action_map.get(event_type, "")
            reviewer_hash = pseudonymize(data.get("user", "")) or "unknown"
            reaction_time = data.get("reaction_time_sec") or None

            self._cursor.execute(
                "SELECT id FROM clips WHERE session_id = %s AND clip_num = %s ORDER BY detected_at DESC LIMIT 1",
                (pk, data.get("clip_num")),
            )
            row = self._cursor.fetchone()
            clip_db_id = row[0] if row else None

            # Première review de ce reviewer sur ce clip ?
            is_first = True
            if clip_db_id is not None:
                self._cursor.execute(
                    "SELECT COUNT(*) FROM reviews WHERE clip_id = %s AND reviewer_hash = %s",
                    (clip_db_id, reviewer_hash),
                )
                count_row = self._cursor.fetchone()
                is_first = (count_row[0] == 0) if count_row else True

            self._ensure_user(reviewer_hash)

            insert_sql = """
                INSERT INTO reviews
                    (clip_id, session_id, action, reviewer_hash,
                     reaction_time_sec, is_first_review, reviewed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            self._cursor.execute(insert_sql, (
                clip_db_id, pk, action, reviewer_hash,
                reaction_time, is_first,
                event.get("timestamp", datetime.now(timezone.utc)),
            ))

            # Décision sur le clip (première review uniquement)
            if clip_db_id is not None and is_first:
                self._cursor.execute(
                    "UPDATE clips SET decision = %s, reviewed_at = %s, reviewer_hash = %s WHERE id = %s",
                    (action, event.get("timestamp", datetime.now(timezone.utc)), reviewer_hash, clip_db_id),
                )

            # Propager le label sur chat_windows liées à ce clip
            if clip_db_id is not None:
                self._cursor.execute(
                    "UPDATE chat_windows SET label = %s WHERE triggered_clip_id = %s AND label IS NULL",
                    (action, clip_db_id),
                )
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _insert_review ÉCHEC\n{traceback.format_exc()}")

    def _insert_calibration(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Insère les stats de calibration d'un filtre."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            return
        try:
            insert_sql = """
                INSERT INTO calibration (session_id, channel_id, filtre_nom, est_calibre, samples_count, timestamp_calibration, mean, std, min_samples_required, z_score_threshold, mean_fond, std_fond)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id, filtre_nom) DO UPDATE SET
                    est_calibre = EXCLUDED.est_calibre,
                    samples_count = EXCLUDED.samples_count,
                    timestamp_calibration = EXCLUDED.timestamp_calibration,
                    mean = EXCLUDED.mean,
                    std = EXCLUDED.std
            """
            self._cursor.execute(insert_sql, (
                pk,
                channel_id if channel_id else None,
                data.get("filtre") or data.get("filtre_name") or "",
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
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _insert_calibration ÉCHEC\n{traceback.format_exc()}")

    def _update_clip_generated(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Met à jour le clip avec le chemin fichier généré et sa durée."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            return
        try:
            self._cursor.execute(
                """UPDATE clips SET file_path_hq = %s, duration_seconds = %s
                   WHERE session_id = %s AND clip_num = %s""",
                (
                    data.get("chemin"),
                    data.get("duree_sec"),
                    pk,
                    data.get("clip_num"),
                ),
            )
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _update_clip_generated ÉCHEC\n{traceback.format_exc()}")

    def _insert_clip_merge(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Met à jour un clip existant lors d'un merge (agrège les scores)."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            return
        try:
            update_sql = """
                UPDATE clips SET
                    score_final = GREATEST(score_final, %s)
                WHERE clip_num = %s AND session_id = %s
            """
            self._cursor.execute(update_sql, (
                data.get("score", 0),
                data.get("clip_num"),
                pk,
            ))
        except Exception:
            import traceback
            log.error(f"[PostgresHandler] ❌ _insert_clip_merge ÉCHEC\n{traceback.format_exc()}")

    def _insert_stream_event(self, event: dict, data: dict, session_id: str, channel_id: str | None, channel_name: str) -> None:
        """Insère un event dans stream_events (non-bloquant, async via le worker)."""
        pk = self._session_pks.get(channel_name)
        if pk is None:
            log.debug(f"[PostgresHandler] ⏭ stream_event ignoré — pk None pour channel_name={channel_name!r}")
            return
        insert_sql = """
            INSERT INTO stream_events (session_id, channel_id, event_type, level, message, component, erreur, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        message = data.get("message", "") or str(data)
        component = data.get("component", "") or data.get("filtre", "") or ""
        error_detail = data.get("erreur", "") or data.get("error_detail", "") or ""

        self._exec_safe(insert_sql, (
            pk,
            channel_id if channel_id else None,
            event.get("event_type", ""),
            event.get("level", "INFO"),
            message,
            component,
            error_detail,
            self._psycopg2_extras.Json(data),
        ), "stream_events insert")

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