# src/a3/Twitch/Brain/postgresHandler.py
#
# Handler PostgreSQL pour StructuredLogger.
# Bufferise les events en mémoire puis les insère en base (batch).
# Configure via variables d'environnement :
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME, DB_SSLMODE
#
# Utilise psycopg2 avec COPY pour l'insertion rapide en bulk.

import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from src.a3.Twitch.Brain.structuredLogger import DatabaseHandler

log = logging.getLogger("A3")


class PostgresHandler(DatabaseHandler):
    """
    Handler PostgreSQL pour StructuredLogger.
    Bufferise les events en mémoire puis les insère en base via COPY (bulk rapide).
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

        self._connect()
        self._start_worker()

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
                autocommit=False,
            )
            self._cursor = self._db.cursor()
            self._psycopg2_extras = psycopg2.extras
            log.info(f"[PostgresHandler] ✅ Connecté à {self._host}:{self._port}/{self._database} (sslmode={self._sslmode})")
        except ImportError:
            log.error("[PostgresHandler] ❌ psycopg2 non installé — pip install psycopg2-binary")
            self._db = None
            self._cursor = None
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur connexion : {e}")
            self._db = None
            self._cursor = None

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

    def _insert_batch(self, batch: list[dict]) -> None:
        if not self._cursor or not self._db:
            return

        try:
            # Utilise COPY pour performance (vs INSERT + executemany)
            # Format: CSV avec colonnes dans l'ordre de la table events
            import io

            buffer = io.StringIO()
            for e in batch:
                timestamp = e.get("timestamp", "")
                event_type = e.get("event_type", "")
                channel = e.get("channel", "")
                session_id = e.get("session_id", "")
                level = e.get("level", "INFO")
                data = json.dumps(e.get("data", {}), default=str)

                # Échapper les champs pour COPY (pas de Null byte, pas de backslash)
                timestamp = timestamp.replace("\\", "\\\\").replace("\n", "\\n")
                event_type = event_type.replace("\\", "\\\\").replace("\n", "\\n")
                channel = channel.replace("\\", "\\\\").replace("\n", "\\n")
                session_id = session_id.replace("\\", "\\\\").replace("\n", "\\n")
                level = level.replace("\\", "\\\\").replace("\n", "\\n")
                data = data.replace("\\", "\\\\").replace("\n", "\\n")

                buffer.write(f"{timestamp}\t{event_type}\t{channel}\t{session_id}\t{level}\t{data}\n")

            buffer.seek(0)

            # COPY FROM STDIN est plus rapide que INSERT
            self._cursor.copy_from(
                buffer,
                "events",
                columns=("timestamp", "event_type", "channel", "session_id", "level", "data"),
                sep="\t",
            )
            self._db.commit()
            log.debug(f"[PostgresHandler] 📦 {len(batch)} events insérés (COPY)")
        except Exception as e:
            log.error(f"[PostgresHandler] ❌ Erreur insert batch : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

            # Fallback: INSERT classique si COPY échoue
            self._insert_batch_fallback(batch)

    def _insert_batch_fallback(self, batch: list[dict]) -> None:
        """Fallback si COPY échoue (ex: table pas encore créée)."""
        try:
            insert_sql = """
                INSERT INTO events (timestamp, event_type, channel, session_id, level, data)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            values = [
                (
                    e.get("timestamp"),
                    e.get("event_type"),
                    e.get("channel"),
                    e.get("session_id"),
                    e.get("level"),
                    json.dumps(e.get("data", {}), default=str),
                )
                for e in batch
            ]
            self._cursor.executemany(insert_sql, values)
            self._db.commit()
            log.debug(f"[PostgresHandler] 📦 {len(batch)} events insérés (fallback INSERT)")
        except Exception as e2:
            log.error(f"[PostgresHandler] ❌ Fallback INSERT a aussi échoué : {e2}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def write(self, event: dict) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            log.warning("[PostgresHandler] ⚠️ Queue pleine, event droppé")

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