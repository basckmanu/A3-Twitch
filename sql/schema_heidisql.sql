-- =============================================
-- A3 Database Schema - HeidiSQL / MySQL / MariaDB
-- Run this script to create the database and tables
-- =============================================

-- Create database (si pas déjà fait)
CREATE DATABASE IF NOT EXISTS a3_db
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE a3_db;

-- =============================================
-- Table principale : tous les events structurés
-- =============================================
CREATE TABLE IF NOT EXISTS events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    -- Identification du moment
    timestamp DATETIME(3) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    channel VARCHAR(128) NOT NULL,
    session_id VARCHAR(16) NOT NULL,

    -- Niveau de log
    level ENUM('DEBUG', 'INFO', 'WARNING', 'ERROR') DEFAULT 'INFO',

    -- Données JSON (flexible pour tous les types d'events)
    data JSON,

    -- Index pour requêtes fréquentes
    INDEX idx_channel (channel),
    INDEX idx_event_type (event_type),
    INDEX idx_timestamp (timestamp),
    INDEX idx_session (session_id),
    INDEX idx_channel_event_time (channel, event_type, timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================
-- Table : clips générés
-- =============================================
CREATE TABLE IF NOT EXISTS clips (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,

    clip_num INT UNSIGNED NOT NULL,
    channel VARCHAR(128) NOT NULL,
    session_id VARCHAR(16) NOT NULL,

    score DECIMAL(5,4) NOT NULL,
    auteur VARCHAR(128),
    message_excerpt VARCHAR(500),

    chemin_fichier VARCHAR(512),
    duree_sec DECIMAL(8,1),

    timestamp_creation DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    timestamp_decision DATETIME(3) NULL,
    decision ENUM('garder', 'highlight', 'supprimer') NULL,
    decision_user VARCHAR(128) NULL,

    INDEX idx_channel_session (channel, session_id),
    INDEX idx_timestamp (timestamp_creation),
    INDEX idx_decision (decision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================
-- Table : review Discord (garder/highlight/supprimer)
-- =============================================
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================
-- Table : stats par session
-- =============================================
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================
-- Table : filtres (stats de calibration)
-- =============================================
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- =============================================
-- Vue : bilan session rapide
-- =============================================
CREATE OR REPLACE VIEW v_session_bilan AS
SELECT
    session_id,
    channel,
    debut_session,
    clips_detectes,
    clips_rejetes,
    ROUND(clips_detectes * 100.0 / NULLIF(clips_detectes + clips_rejetes, 0), 1) AS taux_validation_pct,
    score_moyen,
    score_max
FROM session_stats
ORDER BY debut_session DESC;


-- =============================================
-- Vue : top clips par score
-- =============================================
CREATE OR REPLACE VIEW v_top_clips AS
SELECT
    clip_num,
    channel,
    score,
    auteur,
    duree_sec,
    decision,
    timestamp_creation
FROM clips
WHERE decision IS NOT NULL
ORDER BY score DESC
LIMIT 100;