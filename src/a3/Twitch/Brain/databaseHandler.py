# src/a3/Twitch/Brain/databaseHandler.py
#
# Implémentation MySQL/MariaDB du DatabaseHandler pour StructuredLogger.
# Utilise mysql-connector-python (ou mariadb connector).
# Configure via variables d'environnement :
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

import json
import logging
import os
import queue
import threading
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from src.a3.Twitch.Brain.structuredLogger import DatabaseHandler

log = logging.getLogger("A3")


class MySQLHandler(DatabaseHandler):
    """
    Handler MySQL/MariaDB pour StructuredLogger.
    Bufferise les events en mémoire puis les insère en base.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        batch_size: int = 50,
        flush_interval_sec: float = 5.0,
    ) -> None:
        self._host = host or os.getenv("DB_HOST", "localhost")
        self._port = int(port or os.getenv("DB_PORT", "3306"))
        self._user = user or os.getenv("DB_USER", "root")
        self._password = password or os.getenv("DB_PASSWORD", "")
        self._database = database or os.getenv("DB_NAME", "a3_db")
        self._batch_size = batch_size
        self._flush_interval = flush_interval_sec

        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=10000)
        self._closed = False
        self._worker: threading.Thread | None = None
        self._db: Any = None
        self._cursor: Any = None

        self._connect()
        self._start_worker()

    def _connect(self) -> None:
        try:
            import mysql.connector

            self._db = mysql.connector.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                password=self._password,
                database=self._database,
                autocommit=False,
            )
            self._cursor = self._db.cursor(dictionary=True)
            log.info(f"[MySQLHandler] ✅ Connecté à {self._host}:{self._port}/{self._database}")
        except ImportError:
            log.warning("[MySQLHandler] ⚠️ mysql-connector-python non installé — tentative avec pymysql")
            try:
                import pymysql

                self._db = pymysql.connect(
                    host=self._host,
                    port=self._port,
                    user=self._user,
                    password=self._password,
                    database=self._database,
                    autocommit=False,
                    cursorclass=pymysql.cursors.DictCursor,
                )
                self._cursor = self._db.cursor()
                log.info(f"[MySQLHandler] ✅ Connecté à {self._host}:{self._port}/{self._database} (pymysql)")
            except ImportError:
                log.error("[MySQLHandler] ❌ Aucun driver MySQL disponible (mysql-connector-python ou pymysql)")
                self._db = None
                self._cursor = None
        except Exception as e:
            log.error(f"[MySQLHandler] ❌ Erreur connexion : {e}")
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

        insert_sql = """
            INSERT INTO events (timestamp, event_type, channel, session_id, level, data)
            VALUES (%s, %s, %s, %s, %s, %s)
        """

        try:
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
            log.debug(f"[MySQLHandler] 📦 {len(batch)} events insérés")
        except Exception as e:
            log.error(f"[MySQLHandler] ❌ Erreur insert batch : {e}")
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
            log.warning("[MySQLHandler] ⚠️ Queue pleine, event droppé")

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
        log.info("[MySQLHandler] 🔌 Connexion fermée")
