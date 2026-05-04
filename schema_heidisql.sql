-- schema_heidisql.sql
-- Schéma MySQL/MariaDB pour A3 Twitch Clip Detector
-- Importer ce fichier dans HeidiSQL (Fichier > Ouvrir fichier SQL)

CREATE DATABASE IF NOT EXISTS a3_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE a3_db;

-- ─────────────────────────────────────────────────────────────
-- Table principale : events
-- Stocke tous les événements structurés du StructuredLogger
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    timestamp   DATETIME(3)     NOT NULL,           -- millisecondes
    event_type  VARCHAR(64)     NOT NULL,
    channel     VARCHAR(128)    NOT NULL,
    session_id  VARCHAR(16)    NOT NULL,
    level       VARCHAR(16)     NOT NULL DEFAULT 'INFO',
    data        JSON           NOT NULL,             -- données variables

    INDEX idx_timestamp   (timestamp),
    INDEX idx_event_type  (event_type),
    INDEX idx_channel     (channel),
    INDEX idx_session     (session_id),
    INDEX idx_channel_time (channel, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────
-- Table : clips
-- Résumé des clips générés (1 ligne = 1 clip)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clips (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    clip_num        INT UNSIGNED     NOT NULL,       -- numéro dans la session
    session_id      VARCHAR(16)      NOT NULL,
    channel         VARCHAR(128)     NOT NULL,
    score           DECIMAL(6,4)     NOT NULL,
    chemin          VARCHAR(512)     NULL,           -- chemin vers le clip
    mot_repetition  VARCHAR(128)     NULL,           -- mot qui a déclenché Repetition
    auteur          VARCHAR(128)     NULL,           -- auteur du message déclencheur
    duree_sec       DECIMAL(6,1)     NULL,
    decision        ENUM('garder','highlight','supprimer',NULL) DEFAULT NULL,
    decision_user   VARCHAR(128)     NULL,
    decision_time   DATETIME(3)      NULL,
    created_at      DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    UNIQUE KEY uk_session_clipnum (session_id, clip_num),
    INDEX idx_channel     (channel),
    INDEX idx_timestamp   (created_at),
    INDEX idx_decision   (decision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────
-- Table : filter_scores
-- Scores détaillés par filtre (gros volume — partitionnable par date)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS filter_scores (
    id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    timestamp     DATETIME(3)       NOT NULL,
    channel       VARCHAR(128)     NOT NULL,
    session_id    VARCHAR(16)      NOT NULL,
    filtre        VARCHAR(64)      NOT NULL,
    score_raw     DECIMAL(8,4)     NOT NULL,
    score_pondere DECIMAL(8,4)     NOT NULL,
    auteur        VARCHAR(128)     NOT NULL,

    INDEX idx_timestamp (timestamp),
    INDEX idx_channel   (channel),
    INDEX idx_filtre    (filtre),
    INDEX idx_session   (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────
-- Table : sessions
-- Métadonnées des sessions de monitoring
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    session_id    VARCHAR(16)   PRIMARY KEY,
    channel       VARCHAR(128)  NOT NULL,
    started_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    stopped_at    DATETIME(3)   NULL,
    status        ENUM('active','stopped','error') DEFAULT 'active',
    clip_count    INT UNSIGNED  NOT NULL DEFAULT 0,

    INDEX idx_channel (channel),
    INDEX idx_status  (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ─────────────────────────────────────────────────────────────
-- Vue utile : stats par session
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_session_stats AS
SELECT
    s.session_id,
    s.channel,
    s.started_at,
    s.status,
    COUNT(DISTINCT c.id)                         AS total_clips,
    SUM(CASE WHEN c.decision = 'garder'     THEN 1 ELSE 0 END) AS clips_gardes,
    SUM(CASE WHEN c.decision = 'highlight'  THEN 1 ELSE 0 END) AS clips_highlights,
    SUM(CASE WHEN c.decision = 'supprimer'  THEN 1 ELSE 0 END) AS clips_supprimes,
    ROUND(AVG(c.score), 4)                       AS score_moyen,
    ROUND(MAX(c.score), 4)                       AS score_max
FROM sessions s
LEFT JOIN clips c ON c.session_id = s.session_id
GROUP BY s.session_id, s.channel, s.started_at, s.status;
