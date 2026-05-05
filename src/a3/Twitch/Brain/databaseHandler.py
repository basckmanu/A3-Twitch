# src/a3/Twitch/Brain/databaseHandler.py
#
# Implémentation MySQL/MariaDB du DatabaseHandler pour StructuredLogger.
# Utilise mysql-connector-python (ou mariadb connector).
# Configure via variables d'environnement :
#   DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
#
# Créé automatiquement les tables à la première connexion.

import json
import logging
import os
import queue
import threading
from datetime import datetime
from typing import Any

from a3.Twitch.Brain.structuredLogger import DatabaseHandler, EventType

log = logging.getLogger("A3")


class MySQLHandler(DatabaseHandler):
    """
    Handler MySQL/MariaDB pour StructuredLogger.
    Bufferise les events en mémoire puis les insère en base.
    Crée automatiquement les tables à la première connexion.
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
        self._tables_created = False

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

            # Créer les tables à la première connexion
            self._creer_tables()

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

                # Créer les tables à la première connexion
                self._creer_tables()

            except ImportError:
                log.error("[MySQLHandler] ❌ Aucun driver MySQL disponible (mysql-connector-python ou pymysql)")
                self._db = None
                self._cursor = None
        except Exception as e:
            log.error(f"[MySQLHandler] ❌ Erreur connexion : {e}")
            self._db = None
            self._cursor = None

    def _creer_tables(self) -> None:
        """Crée toutes les tables si elles n'existent pas déjà."""
        if self._tables_created:
            return

        try:
            self._cursor.execute("CREATE DATABASE IF NOT EXISTS a3_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            self._cursor.execute("USE a3_db")
        except Exception:
            pass

        # Table principale : events
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                timestamp DATETIME(3) NOT NULL,
                event_type VARCHAR(64) NOT NULL,
                channel VARCHAR(128) NOT NULL,
                session_id VARCHAR(16) NOT NULL,
                level ENUM('DEBUG', 'INFO', 'WARNING', 'ERROR') DEFAULT 'INFO',
                data JSON,
                INDEX idx_channel (channel),
                INDEX idx_event_type (event_type),
                INDEX idx_timestamp (timestamp),
                INDEX idx_session (session_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Table : clips
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS clips (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                clip_num INT UNSIGNED NOT NULL,
                channel VARCHAR(128) NOT NULL,
                session_id VARCHAR(16) NOT NULL,
                score DECIMAL(5,4) NOT NULL,
                auteur VARCHAR(128),
                message_excerpt VARCHAR(500),
                filtre_trigger VARCHAR(255),
                chemin_fichier VARCHAR(512),
                duree_sec DECIMAL(8,1),
                timestamp_creation DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                timestamp_decision DATETIME(3) NULL,
                decision ENUM('garder', 'highlight', 'supprimer') NULL,
                decision_user VARCHAR(128) NULL,
                INDEX idx_channel_session (channel, session_id),
                INDEX idx_timestamp (timestamp_creation),
                INDEX idx_decision (decision)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Table : clip_reviews
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS clip_reviews (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                clip_num INT UNSIGNED NOT NULL,
                channel VARCHAR(128) NOT NULL,
                session_id VARCHAR(16) NOT NULL,
                action ENUM('garder', 'highlight', 'supprimer') NOT NULL,
                user_id BIGINT UNSIGNED NOT NULL,
                username VARCHAR(128) NOT NULL,
                timestamp_review DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
                INDEX idx_clip_num (clip_num),
                INDEX idx_user (user_id),
                INDEX idx_timestamp (timestamp_review)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Table : filter_stats
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS filter_stats (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(16) NOT NULL,
                channel VARCHAR(128) NOT NULL,
                filtre_name VARCHAR(64) NOT NULL,
                samples_calibration INT UNSIGNED DEFAULT 0,
                mean_baseline DECIMAL(10,4),
                std_baseline DECIMAL(10,4),
                seuil_zscore DECIMAL(8,4),
                timestamp_calibrated DATETIME(3) NULL,
                INDEX idx_session_filtre (session_id, filtre_name),
                INDEX idx_channel (channel)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # Table : session_stats
        self._cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_stats (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(16) NOT NULL,
                channel VARCHAR(128) NOT NULL,
                debut_session DATETIME(3) NOT NULL,
                fin_session DATETIME(3) NULL,
                clips_detectes INT UNSIGNED DEFAULT 0,
                clips_rejetes INT UNSIGNED DEFAULT 0,
                clips_gardes INT UNSIGNED DEFAULT 0,
                clips_highlightes INT UNSIGNED DEFAULT 0,
                clips_supprimes INT UNSIGNED DEFAULT 0,
                score_moyen DECIMAL(5,4),
                score_max DECIMAL(5,4),
                INDEX idx_session (session_id),
                INDEX idx_channel (channel),
                INDEX idx_debut (debut_session)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        self._db.commit()
        self._tables_created = True
        log.info("[MySQLHandler] ✅ Tables créées/vérifiées avec succès")

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
            log.warning("[MySQLHandler] ⚠️ Queue pleine, event droppé")

    def _insert_batch(self, batch: list[dict]) -> None:
        if not self._cursor or not self._db:
            return

        try:
            for event in batch:
                self._inserer_event(event)
            self._db.commit()
            log.debug(f"[MySQLHandler] 📦 {len(batch)} events insérés")
        except Exception as e:
            log.error(f"[MySQLHandler] ❌ Erreur insert batch : {e}")
            try:
                self._db.rollback()
            except Exception:
                pass

    def _inserer_event(self, event: dict) -> None:
        """Route chaque event vers la bonne table."""
        event_type = event.get("event_type", "")
        data = event.get("data", {})
        channel = event.get("channel", "")

        # INSERT dans la table principale events
        insert_sql = """
            INSERT INTO events (timestamp, event_type, channel, session_id, level, data)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        values = (
            event.get("timestamp"),
            event_type,
            channel,
            event.get("session_id", ""),
            event.get("level", "INFO"),
            json.dumps(data, default=str),
        )
        self._cursor.execute(insert_sql, values)

        # Route vers tables spécialisées
        if event_type == EventType.CLIP_DETECTED:
            self._insert_clip_detected(event, data)
        elif event_type in (EventType.REVIEW_GARDER, EventType.REVIEW_HIGHLIGHT, EventType.REVIEW_SUPPRIMER):
            self._insert_review(event, data, event_type)
        elif event_type == EventType.FILTER_CALIBRATED:
            self._insert_filter_stats(event, data)

    def _insert_clip_detected(self, event: dict, data: dict) -> None:
        """Insère dans la table clips."""
        try:
            # Construire la liste des filtres actifs
            filtres_trigger = []
            filtres_data = data.get("filtres", {})
            for nom, vals in filtres_data.items():
                if isinstance(vals, dict) and vals.get("score_pondéré", 0) > 0:
                    filtres_trigger.append(nom)
                elif isinstance(vals, (int, float)) and vals > 0:
                    filtres_trigger.append(nom)

            insert_sql = """
                INSERT INTO clips (clip_num, channel, session_id, score, auteur, message_excerpt, filtre_trigger, timestamp_creation)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            self._cursor.execute(insert_sql, (
                data.get("clip_num"),
                event.get("channel", ""),
                event.get("session_id", ""),
                data.get("score", 0),
                data.get("auteur", ""),
                data.get("message_excerpt", ""),
                ",".join(filtres_trigger) if filtres_trigger else "",
                event.get("timestamp", datetime.now()),
            ))
        except Exception as e:
            log.debug(f"[MySQLHandler] ⚠️ Insert clip_detected échoué: {e}")

    def _insert_review(self, event: dict, data: dict, event_type: str) -> None:
        """Insère dans la table clip_reviews."""
        try:
            # Mapper l'event_type vers la decision
            action_map = {
                EventType.REVIEW_GARDER: "garder",
                EventType.REVIEW_HIGHLIGHT: "highlight",
                EventType.REVIEW_SUPPRIMER: "supprimer",
            }
            action = action_map.get(event_type, "")

            insert_sql = """
                INSERT INTO clip_reviews (clip_num, channel, session_id, action, user_id, username, timestamp_review)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            user_id = data.get("user_id", 0)
            self._cursor.execute(insert_sql, (
                data.get("clip_num"),
                event.get("channel", ""),
                event.get("session_id", ""),
                action,
                user_id,
                data.get("user", ""),
                event.get("timestamp", datetime.now()),
            ))

            # Mettre à jour la table clips
            update_sql = """
                UPDATE clips SET decision = %s, decision_user = %s, timestamp_decision = %s
                WHERE clip_num = %s AND channel = %s AND session_id = %s
                ORDER BY timestamp_creation DESC LIMIT 1
            """
            self._cursor.execute(update_sql, (
                action,
                data.get("user", ""),
                event.get("timestamp", datetime.now()),
                data.get("clip_num"),
                event.get("channel", ""),
                event.get("session_id", ""),
            ))
        except Exception as e:
            log.debug(f"[MySQLHandler] ⚠️ Insert review échoué: {e}")

    def _insert_filter_stats(self, event: dict, data: dict) -> None:
        """Insère dans la table filter_stats."""
        try:
            insert_sql = """
                INSERT INTO filter_stats (session_id, channel, filtre_name, samples_calibration, mean_baseline, std_baseline, seuil_zscore, timestamp_calibrated)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """
            self._cursor.execute(insert_sql, (
                event.get("session_id", ""),
                event.get("channel", ""),
                data.get("filtre_name", ""),
                data.get("samples", 0),
                data.get("mean", 0),
                data.get("std", 0),
                data.get("seuil", 0),
                event.get("timestamp", datetime.now()),
            ))
        except Exception as e:
            log.debug(f"[MySQLHandler] ⚠️ Insert filter_stats échoué: {e}")

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