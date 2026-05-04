-- schema_postgresql.sql
-- Schéma PostgreSQL pour A3 Twitch Clip Detector
-- Utilise JSONB pour performance, INDEX GiST pour queries rapides

-- Activation de l'extension pour UUID si besoin
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─────────────────────────────────────────────────────────────
-- Table : sessions
-- Métadonnées des sessions de monitoring (PRIMARY KEY)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    session_id    VARCHAR(16)   PRIMARY KEY,
    channel       VARCHAR(128) NOT NULL,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    stopped_at    TIMESTAMPTZ  NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'active',
    clip_count    BIGINT       NOT NULL DEFAULT 0
);

CREATE INDEX idx_sessions_channel ON sessions(channel);
CREATE INDEX idx_sessions_status  ON sessions(status);
CREATE INDEX idx_sessions_started ON sessions(started_at DESC);

-- ─────────────────────────────────────────────────────────────
-- Table : events
-- Stocke tous les événements structurés du StructuredLogger
-- Partitionnable par timestamp pour gros volume (cf. notes fin)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id            BIGSERIAL,
    timestamp     TIMESTAMPTZ  NOT NULL,
    event_type    VARCHAR(64)  NOT NULL,
    channel       VARCHAR(128) NOT NULL,
    session_id    VARCHAR(16)  NOT NULL,
    level         VARCHAR(16)  NOT NULL DEFAULT 'INFO',
    data          JSONB        NOT NULL,

    -- Clustering order: channel + timestamp = query pattern le plus courant
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Partition mensuelle (à créer dans pg_partman ou manuellement)
-- Les partitions suivantes seront créées automatiquement par un job de maintenance:
-- events_2026_01, events_2026_02, events_2026_03, ...
CREATE TABLE IF NOT EXISTS events_default
    PARTITION OF events DEFAULT;

CREATE INDEX idx_events_type     ON events(event_type);
CREATE INDEX idx_events_channel  ON events(channel, timestamp DESC);
CREATE INDEX idx_events_session ON events(session_id);
-- Index GiST sur JSONB pour requêter le champ "event_type" dans data (rare mais utile)
CREATE INDEX idx_events_data    ON events USING GIN (data);

-- ─────────────────────────────────────────────────────────────
-- Table : filter_scores
-- Scores détaillés par filtre (gros volume — partitionnable)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS filter_scores (
    id            BIGSERIAL,
    timestamp     TIMESTAMPTZ  NOT NULL,
    channel       VARCHAR(128) NOT NULL,
    session_id    VARCHAR(16)  NOT NULL,
    filtre        VARCHAR(64)  NOT NULL,
    score_raw     DECIMAL(8,4) NOT NULL,
    score_pondere DECIMAL(8,4) NOT NULL,
    auteur        VARCHAR(128) NOT NULL
) PARTITION BY RANGE (timestamp);

CREATE TABLE IF NOT EXISTS filter_scores_default
    PARTITION OF filter_scores DEFAULT;

CREATE INDEX idx_filter_scores_ts      ON filter_scores(timestamp DESC);
CREATE INDEX idx_filter_scores_channel ON filter_scores(channel, timestamp DESC);
CREATE INDEX idx_filter_scores_filtre  ON filter_scores(filtre, timestamp DESC);
CREATE INDEX idx_filter_scores_session ON filter_scores(session_id);

-- ─────────────────────────────────────────────────────────────
-- Table : clips
-- Résumé des clips générés (1 ligne = 1 clip)
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clips (
    id              BIGSERIAL PRIMARY KEY,
    clip_num        BIGINT    NOT NULL,
    session_id      VARCHAR(16) NOT NULL,
    channel         VARCHAR(128) NOT NULL,
    score           DECIMAL(6,4) NOT NULL,
    chemin          VARCHAR(512) NULL,
    mot_repetition  VARCHAR(128) NULL,
    auteur          VARCHAR(128) NULL,
    duree_sec       DECIMAL(6,1) NULL,
    decision        VARCHAR(32)  NULL,
    decision_user   VARCHAR(128) NULL,
    decision_time   TIMESTAMPTZ  NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (session_id, clip_num)
);
CREATE INDEX idx_clips_channel    ON clips(channel, created_at DESC);
CREATE INDEX idx_clips_decision   ON clips(decision) WHERE decision IS NOT NULL;
CREATE INDEX idx_clips_session    ON clips(session_id);

-- ─────────────────────────────────────────────────────────────
-- Vue : stats par session (materializable pour perf)
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
    ROUND(AVG(c.score)::numeric, 4)             AS score_moyen,
    ROUND(MAX(c.score)::numeric, 4)             AS score_max
FROM sessions s
LEFT JOIN clips c ON c.session_id = s.session_id
GROUP BY s.session_id, s.channel, s.started_at, s.status;

-- ─────────────────────────────────────────────────────────────
-- Vue : stats par channel (daily/weekly) — utile pour dashboarding
-- ─────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW v_channel_stats AS
SELECT
    channel,
    DATE(created_at) AS jour,
    COUNT(*)                        AS total_clips,
    SUM(CASE WHEN decision = 'garder'    THEN 1 ELSE 0 END) AS clips_gardes,
    SUM(CASE WHEN decision = 'highlight' THEN 1 ELSE 0 END) AS clips_highlights,
    SUM(CASE WHEN decision = 'supprimer' THEN 1 ELSE 0 END) AS clips_supprimes,
    ROUND(AVG(score)::numeric, 4) AS score_moyen
FROM clips
GROUP BY channel, DATE(created_at)
ORDER BY jour DESC, channel;

-- ─────────────────────────────────────────────────────────────
-- NOTES pour partitionnement PostgreSQL :
--
-- Pour activer le partitionnement temporel automatique :
-- Installer pg_partman et créer un job de maintenance :
--
--   CREATE EXTENSION IF NOT EXISTS pg_partman;
--   SELECT partman.create_parent(
--       'public.events',
--       'timestamp',
--       'monthly',
--       pre_make := 3,    -- créer les 3 mois suivants d'avance
--       retention := '3 months'
--   );
--
-- Même chose pour filter_scores :
--   SELECT partman.create_parent(
--       'public.filter_scores',
--       'timestamp',
--       'monthly',
--       pre_make := 3,
--       retention := '1 month'  -- filter_scores = plus gros volume, rétention courte
--   );
--
-- Dashboarding temps réel : utiliser v_channel_stats avec un REFRESH MATERIALIZED VIEW.
-- ─────────────────────────────────────────────────────────────