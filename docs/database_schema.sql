-- ============================================================
-- A3 — TWITCH CLIP DETECTION & AI TRAINING DATABASE
-- PostgreSQL 15+ | Haute écriture / ML ready
-- v1.0 | Corrigé pour performance & agents IA
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_cron;  -- nécessite superuser + shared_preload_libraries

-- ============================================================
-- CHANNELS
-- ============================================================
CREATE TABLE channels (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    twitch_id       BIGINT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- AGENT RUNS (trace chaque recalibration — défini avant filter_weights_history)
-- ============================================================
CREATE TABLE agent_runs (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL,
    agent_type      VARCHAR(50) NOT NULL,  -- 'recalibrator' 'ai_predictor' 'review_suggester'
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    input_snapshot  BIGINT,  -- pas de FK vers snapshots pour éviter dépendance circulaire ; link via agent_runs.input_snapshot
    output_weights  JSONB,   -- nouveaux poids générés
    outcome         VARCHAR(20),  -- 'success' 'failed' 'aborted'
    error_detail    TEXT
);

CREATE INDEX idx_agent_runs_session ON agent_runs(session_id);
CREATE INDEX idx_agent_runs_type ON agent_runs(agent_type);

-- ============================================================
-- SESSIONS (non partitionnée — faible volume, PK simple)
-- ============================================================
CREATE TABLE sessions (
    id                  BIGSERIAL PRIMARY KEY,
    channel_id          UUID NOT NULL REFERENCES channels(id),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at            TIMESTAMPTZ,
    duration_seconds    INTEGER,
    clips_detected      INT DEFAULT 0,
    clips_validated     INT DEFAULT 0,
    clips_rejected      INT DEFAULT 0,
    clips_highlighted   INT DEFAULT 0,
    score_avg           FLOAT,
    score_max           FLOAT,
    status              VARCHAR(20) DEFAULT 'active',
    version             VARCHAR(20) DEFAULT 'v0.0.4'
);

-- ============================================================
-- FILTER WEIGHTS HISTORY (table dédiée pour recalibration)
-- ============================================================
CREATE TABLE filter_weights_history (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          BIGINT NOT NULL,
    filter_name         VARCHAR(50) NOT NULL,
    weight_before       FLOAT NOT NULL,
    weight_after        FLOAT NOT NULL,
    agent_run_id        BIGINT REFERENCES agent_runs(id),
    reason              TEXT,
    snapshot_id         BIGINT,
    changed_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(session_id, filter_name, agent_run_id)
);

-- ============================================================
-- AUTHORS (sans colonne message_count — calculé via vue)
-- username_hash = SHA-256(A3_HASH_SALT:username)[:16]
-- ============================================================
CREATE TABLE authors (
    id          BIGSERIAL PRIMARY KEY,
    twitch_id   BIGINT,
    username_hash CHAR(16) NOT NULL,
    channel_id  UUID NOT NULL REFERENCES channels(id),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(username_hash, channel_id)
);

-- ============================================================
-- CLIPS
-- trigger_author_hash = SHA-256(A3_HASH_SALT:username)[:16]
-- repetition_word = hash du mot dominant (non le mot en clair)
-- ============================================================
CREATE TABLE clips (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          BIGINT NOT NULL,
    channel_id          UUID NOT NULL REFERENCES channels(id),
    clip_number         INT NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    score_final         FLOAT NOT NULL,
    score_components    JSONB,

    trigger_author_hash CHAR(16),
    repetition_word     CHAR(16),

    file_path_hq        TEXT,
    file_path_preview   TEXT,
    duration_seconds    FLOAT,

    decision            VARCHAR(20),
    reviewed_at         TIMESTAMPTZ,
    reviewer_id         BIGINT,
    reviewer_hash       CHAR(16),

    ml_features         JSONB,
    ai_confidence       FLOAT,

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_clips_session ON clips(session_id);
CREATE INDEX idx_clips_channel ON clips(channel_id);
CREATE INDEX idx_clips_decision ON clips(decision);
CREATE INDEX idx_clips_detected ON clips(detected_at);
CREATE INDEX idx_clips_score ON clips(score_final DESC);
CREATE INDEX idx_clips_ml_features ON clips USING GIN (ml_features);
CREATE INDEX idx_clips_score_components ON clips USING GIN (score_components);

-- ============================================================
-- REVIEWS
-- username_hash = SHA-256(A3_HASH_SALT:username)[:16]
-- ============================================================
CREATE TABLE reviews (
    id              BIGSERIAL PRIMARY KEY,
    clip_id         BIGINT NOT NULL REFERENCES clips(id),
    session_id      BIGINT NOT NULL,
    action          VARCHAR(20) NOT NULL,
    user_id         BIGINT NOT NULL,
    username_hash   CHAR(16) NOT NULL,
    reviewed_at     TIMESTAMPTZ DEFAULT NOW(),
    latency_ms      INTEGER
);

CREATE INDEX idx_reviews_clip ON reviews(clip_id);
CREATE INDEX idx_reviews_user ON reviews(user_id);

-- ============================================================
-- FILTER EVENTS (BIGSERIAL PK — haute fréquence, pas d'UUID)
-- ============================================================
CREATE TABLE filter_events (
    id          BIGSERIAL,
    session_id  BIGINT NOT NULL,
    channel_id  UUID NOT NULL REFERENCES channels(id),
    author_id   BIGINT,  -- pas de FK vers authors(id) : évite locks sur table mère lors d'insertions massives ; volontaire pour performance
    event_type  VARCHAR(50) NOT NULL,
    filter_name VARCHAR(50) NOT NULL,
    score_raw   FLOAT NOT NULL,
    score_weighted FLOAT NOT NULL,
    z_score     FLOAT,
    threshold   FLOAT,
    is_triggered BOOLEAN DEFAULT FALSE,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

CREATE TABLE filter_events_2025 PARTITION OF filter_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE filter_events_2026_m01 PARTITION OF filter_events
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE filter_events_2026_m02 PARTITION OF filter_events
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE filter_events_2026_m03 PARTITION OF filter_events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE filter_events_2026_m04 PARTITION OF filter_events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE filter_events_2026_m05 PARTITION OF filter_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE filter_events_2026_m06 PARTITION OF filter_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE INDEX idx_filter_events_session ON filter_events(session_id);
CREATE INDEX idx_filter_events_filter ON filter_events(filter_name);
CREATE INDEX idx_filter_events_triggered ON filter_events(is_triggered) WHERE is_triggered = TRUE;
CREATE INDEX idx_filter_events_timestamp ON filter_events(timestamp DESC);

-- ============================================================
-- FILTER CALIBRATION STATE
-- ============================================================
CREATE TABLE filter_calibration (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          BIGINT NOT NULL,
    filter_name         VARCHAR(50) NOT NULL,
    is_calibrated       BOOLEAN DEFAULT FALSE,
    sample_count        INT DEFAULT 0,
    mean_baseline       FLOAT,
    std_baseline        FLOAT,
    mean_fond           FLOAT,
    std_fond            FLOAT,
    z_score_threshold   FLOAT,
    calibrated_at       TIMESTAMPTZ,
    UNIQUE(session_id, filter_name)
);

-- ============================================================
-- STREAM EVENTS (BIGSERIAL — système, haute fréquence aussi)
-- ============================================================
CREATE TABLE stream_events (
    id          BIGSERIAL,
    session_id  BIGINT,
    channel_id  UUID,
    event_type  VARCHAR(50) NOT NULL,
    level       VARCHAR(10) NOT NULL,
    message     TEXT,
    component   VARCHAR(50),
    error_detail TEXT,
    data        JSONB,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

CREATE TABLE stream_events_2026_m05 PARTITION OF stream_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE stream_events_2026_m06 PARTITION OF stream_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE stream_events_history PARTITION OF stream_events
    FOR VALUES FROM (MINVALUE) TO ('2026-05-01');

CREATE INDEX idx_stream_events_data ON stream_events USING GIN (data);
CREATE INDEX idx_stream_events_timestamp ON stream_events(timestamp DESC);

-- ============================================================
-- SNAPSHOTS (BIGSERIAL — periodic, medium fréquence)
-- ============================================================
CREATE TABLE snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          BIGINT NOT NULL,
    channel_id          UUID NOT NULL REFERENCES channels(id),
    timestamp           TIMESTAMPTZ DEFAULT NOW(),
    message_count       INT DEFAULT 0,
    unique_authors      INT DEFAULT 0,
    clip_count          INT DEFAULT 0,
    score_avg           FLOAT,
    message_rate_avg    FLOAT,
    emote_density_avg   FLOAT,
    emotion_hype_avg    FLOAT,
    emotion_rage_avg    FLOAT,
    filters_calibrated  INT DEFAULT 0,
    filters_active      INT DEFAULT 0
);

CREATE INDEX idx_snapshots_session ON snapshots(session_id);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp DESC);

-- ============================================================
-- EMOTE CACHE
-- ============================================================
CREATE TABLE emotes (
    id          BIGSERIAL PRIMARY KEY,
    channel_id  UUID NOT NULL REFERENCES channels(id),
    provider    VARCHAR(20) NOT NULL,
    code        VARCHAR(100) NOT NULL,
    emote_id    VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    cached_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(channel_id, provider, code)
);

-- ============================================================
-- AI PREDICTIONS & TRAINING
-- ============================================================
CREATE TABLE ai_predictions (
    id              BIGSERIAL PRIMARY KEY,
    clip_id         BIGINT REFERENCES clips(id),
    session_id      BIGINT,
    model_version   VARCHAR(20),
    predicted_score FLOAT,
    predicted_decision VARCHAR(20),
    confidence      FLOAT,
    features_used   JSONB,
    prediction_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ai_predictions_clip ON ai_predictions(clip_id);
CREATE INDEX idx_ai_predictions_model ON ai_predictions(model_version);
CREATE INDEX idx_ai_predictions_features ON ai_predictions USING GIN (features_used);

-- ============================================================
-- STREAMERS CONSENT (RGPD — consentement explicite pour la collecte)
-- ============================================================
CREATE TABLE streamers_consent (
    id              BIGSERIAL PRIMARY KEY,
    channel_id      UUID NOT NULL REFERENCES channels(id),
    consent_given   BOOLEAN NOT NULL DEFAULT FALSE,
    consent_date    TIMESTAMPTZ,
    withdrawal_date TIMESTAMPTZ,
    legal_basis     VARCHAR(50) NOT NULL,  -- 'legitimate_interest' 'consent' 'contract'
    data_categories JSONB NOT NULL,         -- ['chat_messages', 'clips', 'reviews']
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_streamers_consent_channel ON streamers_consent(channel_id);

-- ============================================================
-- DATA RETENTION POLICIES
-- ============================================================
CREATE TABLE data_retention_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_name     VARCHAR(50) NOT NULL UNIQUE,
    table_name      VARCHAR(50) NOT NULL,
    retention_days  INT NOT NULL,
    purge_criteria  JSONB,
    is_active       BOOLEAN DEFAULT TRUE,
    last_purge_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- pg_cron: purge quotidienne à 3h du matin
SELECT cron.schedule('purge-data', '0 3 * * *',
    $$DELETE FROM filter_events WHERE timestamp < NOW() - INTERVAL '90 days'$$);
SELECT cron.schedule('purge-clips', '0 3 * * *',
    $$DELETE FROM clips WHERE created_at < NOW() - INTERVAL '180 days' AND decision IN ('rejected', 'pending')$$);

-- ============================================================
-- VUES MATERIALISÉES (ML / dashboards)
-- ============================================================

CREATE MATERIALIZED VIEW mv_clip_stats AS
SELECT
    c.channel_id,
    DATE(c.detected_at) AS date,
    COUNT(*) AS total_clips,
    AVG(c.score_final) AS avg_score,
    MAX(c.score_final) AS max_score,
    COUNT(*) FILTER (WHERE c.decision = 'validated') AS validated,
    COUNT(*) FILTER (WHERE c.decision = 'highlighted') AS highlighted,
    COUNT(*) FILTER (WHERE c.decision = 'rejected') AS rejected,
    AVG(EXTRACT(EPOCH FROM (c.reviewed_at - c.detected_at))) AS avg_review_latency
FROM clips c
GROUP BY c.channel_id, DATE(c.detected_at);

CREATE UNIQUE INDEX ON mv_clip_stats(channel_id, date);

CREATE MATERIALIZED VIEW mv_filter_performance AS
SELECT
    fe.filter_name,
    DATE(fe.timestamp) AS date,
    COUNT(*) AS total_events,
    COUNT(*) FILTER (WHERE fe.is_triggered) AS triggers,
    AVG(fe.z_score) AS avg_zscore,
    AVG(fe.score_weighted) AS avg_weighted_score
FROM filter_events fe
GROUP BY fe.filter_name, DATE(fe.timestamp);

CREATE UNIQUE INDEX ON mv_filter_performance(filter_name, date);

-- FIX: auteur_activity sur filter_events (author_id disponible)
CREATE MATERIALIZED VIEW mv_author_activity AS
SELECT
    fe.channel_id,
    DATE(fe.timestamp) AS date,
    COUNT(*) AS message_count,
    COUNT(DISTINCT fe.author_id) AS unique_authors
FROM filter_events fe
WHERE fe.event_type = 'message'
GROUP BY fe.channel_id, DATE(fe.timestamp);

CREATE UNIQUE INDEX ON mv_author_activity(channel_id, date);

-- ============================================================
-- TRIGGERS OPTIMISÉS (delta +1/-1 au lieu de 3 sous-SELECTs)
-- ============================================================

CREATE OR REPLACE FUNCTION sync_session_clip_counts_delta()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.action = 'validate' THEN
            UPDATE sessions SET clips_validated = clips_validated + 1 WHERE id = NEW.session_id;
        ELSIF NEW.action = 'highlight' THEN
            UPDATE sessions SET clips_highlighted = clips_highlighted + 1 WHERE id = NEW.session_id;
        ELSIF NEW.action = 'reject' THEN
            UPDATE sessions SET clips_rejected = clips_rejected + 1 WHERE id = NEW.session_id;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.action = 'validate' THEN
            UPDATE sessions SET clips_validated = clips_validated - 1 WHERE id = OLD.session_id;
        ELSIF OLD.action = 'highlight' THEN
            UPDATE sessions SET clips_highlighted = clips_highlighted - 1 WHERE id = OLD.session_id;
        ELSIF OLD.action = 'reject' THEN
            UPDATE sessions SET clips_rejected = clips_rejected - 1 WHERE id = OLD.session_id;
        END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        UPDATE sessions SET
            clips_validated   = clips_validated   + CASE WHEN NEW.action = 'validate' AND OLD.action <> 'validate' THEN 1 WHEN NEW.action <> 'validate' AND OLD.action = 'validate' THEN -1 ELSE 0 END,
            clips_highlighted = clips_highlighted + CASE WHEN NEW.action = 'highlight' AND OLD.action <> 'highlight' THEN 1 WHEN NEW.action <> 'highlight' AND OLD.action = 'highlight' THEN -1 ELSE 0 END,
            clips_rejected    = clips_rejected    + CASE WHEN NEW.action = 'reject' AND OLD.action <> 'reject' THEN 1 WHEN NEW.action <> 'reject' AND OLD.action = 'reject' THEN -1 ELSE 0 END
        WHERE id = NEW.session_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_sync_session_reviews ON reviews;
CREATE TRIGGER trigger_sync_session_reviews
AFTER INSERT OR DELETE OR UPDATE ON reviews
FOR EACH ROW EXECUTE FUNCTION sync_session_clip_counts_delta();

-- ============================================================
-- FONCTIONS UTILES
-- ============================================================

-- Refresh toutes les vues materialisées automatiquement
CREATE OR REPLACE FUNCTION refresh_all_mvs()
RETURNS VOID AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_clip_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filter_performance;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_author_activity;
END;
$$ LANGUAGE plpgsql;

-- pg_cron: refresh quotidien à 4h du matin
SELECT cron.schedule('refresh-mvs', '0 4 * * *', 'SELECT refresh_all_mvs()');

-- Partition automatique (appelé par pg_cron ou schedule)
CREATE OR REPLACE FUNCTION create_filter_events_partition(partition_date DATE)
RETURNS VOID AS $$
DECLARE
    partition_name TEXT;
    start_date DATE;
    end_date DATE;
BEGIN
    partition_name := 'filter_events_' || TO_CHAR(partition_date, 'YYYY_mm');
    start_date := DATE_TRUNC('month', partition_date);
    end_date := start_date + INTERVAL '1 month';
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF filter_events FOR VALUES FROM (%L) TO (%L)',
        partition_name, start_date, end_date
    );
END;
$$ LANGUAGE plpgsql;

-- pg_cron: crée partitions filter_events 1 mois en avance le 25 du mois
SELECT cron.schedule('create-partitions', '0 0 25 * *',
    $$SELECT create_filter_events_partition(CURRENT_DATE + INTERVAL '1 month')$$);

-- Fonction de partition pour stream_events (même logique)
CREATE OR REPLACE FUNCTION create_stream_events_partition(partition_date DATE)
RETURNS VOID AS $$
DECLARE
    partition_name TEXT;
    start_date DATE;
    end_date DATE;
BEGIN
    partition_name := 'stream_events_' || TO_CHAR(partition_date, 'YYYY_mm');
    start_date := DATE_TRUNC('month', partition_date);
    end_date := start_date + INTERVAL '1 month';
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF stream_events FOR VALUES FROM (%L) TO (%L)',
        partition_name, start_date, end_date
    );
END;
$$ LANGUAGE plpgsql;

-- pg_cron: crée partitions stream_events 1 mois en avance le 25 du mois
SELECT cron.schedule('create-stream-partitions', '0 0 25 * *',
    $$SELECT create_stream_events_partition(CURRENT_DATE + INTERVAL '1 month')$$);