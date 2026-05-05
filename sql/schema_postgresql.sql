-- =============================================
-- A3 - Schema PostgreSQL pour HeidiSQL / pgAdmin
-- Projet : Twitch clip detection system
-- =============================================

-- Extension pour JSONB (si pas déjà présent)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- =============================================
-- Table : sessions
-- Une ligne par session de monitoring
-- =============================================
CREATE TABLE sessions (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) UNIQUE NOT NULL,
    channel VARCHAR(128) NOT NULL,
    debut_session TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fin_session TIMESTAMPTZ,
    statut VARCHAR(20) DEFAULT 'active' CHECK (statut IN ('active', 'stopped', 'error')),

    -- Stats agrégées
    clips_detectes INT DEFAULT 0,
    clips_rejetes INT DEFAULT 0,
    clips_gardes INT DEFAULT 0,
    clips_highlightes INT DEFAULT 0,
    clips_supprimes INT DEFAULT 0,

    score_moyen DECIMAL(5,4),
    score_max DECIMAL(5,4),
    score_min DECIMAL(5,4),

    -- Config au moment de la session
    seuil_clip DECIMAL(4,3),
    poids_filtres JSONB,

    -- Metadata
    version_app VARCHAR(20),
    platform VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_channel ON sessions(channel);
CREATE INDEX idx_sessions_debut ON sessions(debut_session DESC);
CREATE INDEX idx_sessions_statut ON sessions(statut);


-- =============================================
-- Table : clips
-- Chaque clip détecté par le système
-- =============================================
CREATE TABLE clips (
    id BIGSERIAL PRIMARY KEY,
    clip_num INT NOT NULL,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),

    -- Localisation
    channel VARCHAR(128) NOT NULL,
    chemin_fichier VARCHAR(512),

    -- Scoring
    score_final DECIMAL(5,4) NOT NULL,
    score_unique_authors DECIMAL(5,4),
    score_message_rate DECIMAL(5,4),
    score_emotions DECIMAL(5,4),
    score_emote_density DECIMAL(5,4),
    score_repetition DECIMAL(5,4),
    score_clip_activity DECIMAL(5,4),

    -- Contexte
    auteur VARCHAR(128),
    message_excerpt VARCHAR(500),
    mot_repetition VARCHAR(128),
    filtres_actifs TEXT[],  -- array des noms de filtres déclenchés

    -- Timing
    timestamp_trigger TIMESTAMPTZ NOT NULL,
    duree_calculee_sec DECIMAL(8,1),
    timestamp_creation TIMESTAMPTZ DEFAULT NOW(),

    -- Review Discord
    decision VARCHAR(20) CHECK (decision IN ('garder', 'highlight', 'supprimer')),
    decision_user VARCHAR(128),
    decision_user_id BIGINT,
    timestamp_decision TIMESTAMPTZ
);

CREATE INDEX idx_clips_session ON clips(session_id);
CREATE INDEX idx_clips_channel ON clips(channel);
CREATE INDEX idx_clips_timestamp ON clips(timestamp_trigger DESC);
CREATE INDEX idx_clips_decision ON clips(decision) WHERE decision IS NOT NULL;
CREATE INDEX idx_clips_score ON clips(score_final DESC);


-- =============================================
-- Table : reviews
-- Log de chaque action Discord (garder/highlight/supprimer)
-- =============================================
CREATE TABLE reviews (
    id BIGSERIAL PRIMARY KEY,
    clip_id BIGINT REFERENCES clips(id) ON DELETE CASCADE,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
    channel VARCHAR(128) NOT NULL,

    action VARCHAR(20) NOT NULL CHECK (action IN ('garder', 'highlight', 'supprimer')),
    user_id BIGINT NOT NULL,
    username VARCHAR(128) NOT NULL,

    timestamp_review TIMESTAMPTZ DEFAULT NOW(),
    ip_address VARCHAR(45)
);

CREATE INDEX idx_reviews_clip ON reviews(clip_id);
CREATE INDEX idx_reviews_user ON reviews(user_id);
CREATE INDEX idx_reviews_timestamp ON reviews(timestamp_review DESC);


-- =============================================
-- Table : filter_events
-- Chaque event de filtre (score, trigger, calibration)
-- =============================================
CREATE TABLE filter_events (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
    channel VARCHAR(128) NOT NULL,

    event_type VARCHAR(64) NOT NULL,
    filtre_nom VARCHAR(64) NOT NULL,

    -- Valeurs
    score_raw DECIMAL(10,4),
    score_pondere DECIMAL(10,4),
    z_score DECIMAL(8,4),
    mean_baseline DECIMAL(10,4),
    std_baseline DECIMAL(10,4),
    seuil DECIMAL(10,4),

    -- Contexte
    auteur VARCHAR(128),
    message_excerpt VARCHAR(200),
    level VARCHAR(10) DEFAULT 'INFO',

    -- JSON flexible pour données additionnelles
    data JSONB,

    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_filter_events_session ON filter_events(session_id);
CREATE INDEX idx_filter_events_filtre ON filter_events(filtre_nom, timestamp DESC);
CREATE INDEX idx_filter_events_type ON filter_events(event_type);
CREATE INDEX idx_filter_events_channel ON filter_events(channel);


-- =============================================
-- Table : calibration
-- Stats de calibration de chaque filtre par session
-- =============================================
CREATE TABLE calibration (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
    channel VARCHAR(128) NOT NULL,

    filtre_nom VARCHAR(64) NOT NULL,

    -- État calibration
    est_calibre BOOLEAN DEFAULT FALSE,
    samples_count INT DEFAULT 0,
    timestamp_calibration TIMESTAMPTZ,

    -- Paramètres Welford
    mean DECIMAL(10,4),
    std DECIMAL(10,4),
    min_samples_required INT,
    z_score_threshold DECIMAL(4,2),

    -- Fond long terme
    mean_fond DECIMAL(10,4),
    std_fond DECIMAL(10,4),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_calibration_session ON calibration(session_id, filtre_nom);
CREATE INDEX idx_calibration_est_calibre ON calibration(est_calibre) WHERE NOT est_calibre;


-- =============================================
-- Table : stream_events
-- Events système (connexion, déconnexion, erreurs)
-- =============================================
CREATE TABLE stream_events (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) REFERENCES sessions(session_id),
    channel VARCHAR(128),

    event_type VARCHAR(64) NOT NULL,
    level VARCHAR(10) DEFAULT 'INFO',

    -- Message et contexte
    message TEXT,
    component VARCHAR(128),
    erreur TEXT,

    -- Data additionnelle
    data JSONB,

    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_stream_events_session ON stream_events(session_id);
CREATE INDEX idx_stream_events_type ON stream_events(event_type, timestamp DESC);


-- =============================================
-- Table : raw_messages
-- Archivage optionnel des messages chat (si activé)
-- =============================================
CREATE TABLE raw_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
    channel VARCHAR(128) NOT NULL,

    auteur VARCHAR(128) NOT NULL,
    auteur_id BIGINT,
    contenu TEXT,
    contenu_normalise TEXT,

    -- Métadonnées message
    est_bot BOOLEAN DEFAULT FALSE,
    est_action BOOLEAN DEFAULT FALSE,
    timestamp_message TIMESTAMPTZ,

    -- Filtres actifs au moment (si déclenché)
    filtres_actifs TEXT[],

    -- Embeddings pour analyse future (optionnel)
    embedding VECTOR(1536),

    timestamp TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_raw_messages_session ON raw_messages(session_id);
CREATE INDEX idx_raw_messages_auteur ON raw_messages(auteur);
CREATE INDEX idx_raw_messages_timestamp ON raw_messages(timestamp_message DESC);


-- =============================================
-- Table : snapshots
-- Snapshots périodiques pour statistiques
-- =============================================
CREATE TABLE snapshots (
    id BIGSERIAL PRIMARY KEY,
    session_id VARCHAR(16) NOT NULL REFERENCES sessions(session_id),
    channel VARCHAR(128) NOT NULL,

    timestamp_snapshot TIMESTAMPTZ NOT NULL,

    -- Compteurs
    messages_count INT DEFAULT 0,
    auteurs_uniques_count INT DEFAULT 0,
    clips_count INT DEFAULT 0,

    -- Scores moyens窗口
    score_moyen DECIMAL(5,4),
    message_rate_avg DECIMAL(10,4),
    emote_density_avg DECIMAL(10,4),

    -- Filtres actifs
    filtres_calibres TEXT[],
    filtres_actifs TEXT[],

    data JSONB
);

CREATE INDEX idx_snapshots_session ON snapshots(session_id);
CREATE INDEX idx_snapshots_timestamp ON snapshots(timestamp_snapshot DESC);


-- =============================================
-- VUES pour requêtes fréquentes
-- =============================================

-- Vue : bilan sessions
CREATE OR REPLACE VIEW v_bilan_sessions AS
SELECT
    s.session_id,
    s.channel,
    s.debut_session,
    s.fin_session,
    s.clips_detectes,
    s.clips_rejetes,
    s.clips_gardes,
    s.clips_highlightes,
    s.clips_supprimes,
    s.score_moyen,
    s.score_max,
    s.statut,
    ROUND(s.clips_detectes::NUMERIC / NULLIF(s.clips_detectes + s.clips_rejetes, 0) * 100, 1) AS taux_validation_pct,
    ROUND(EXTRACT(EPOCH FROM (COALESCE(s.fin_session, NOW()) - s.debut_session))::NUMERIC / 60, 1) AS duree_minutes
FROM sessions s
ORDER BY s.debut_session DESC;


-- Vue : top clips par score
CREATE OR REPLACE VIEW v_top_clips AS
SELECT
    c.clip_num,
    c.channel,
    c.session_id,
    c.score_final,
    c.auteur,
    c.message_excerpt,
    c.duree_calculee_sec,
    c.decision,
    c.timestamp_trigger,
    s.clips_gardes + s.clips_highlightes AS total_positifs
FROM clips c
JOIN sessions s ON c.session_id = s.session_id
WHERE c.decision IS NOT NULL
ORDER BY c.score_final DESC
LIMIT 100;


-- Vue : stats filtres par session
CREATE OR REPLACE VIEW v_stats_filtres AS
SELECT
    c.session_id,
    c.channel,
    c.filtre_nom,
    c.est_calibre,
    c.samples_count,
    c.mean,
    c.std,
    fe.event_type,
    COUNT(*) AS event_count
FROM calibration c
LEFT JOIN filter_events fe ON c.session_id = fe.session_id AND c.filtre_nom = fe.filtre_nom
GROUP BY c.session_id, c.channel, c.filtre_nom, c.est_calibre, c.samples_count, c.mean, c.std, fe.event_type;


-- Vue : activity heatmap (par heure)
CREATE OR REPLACE VIEW v_activity_heatmap AS
SELECT
    channel,
    EXTRACT(HOUR FROM timestamp_trigger) AS heure,
    COUNT(*) AS clip_count,
    AVG(score_final) AS score_avg
FROM clips
WHERE timestamp_trigger IS NOT NULL
GROUP BY channel, EXTRACT(HOUR FROM timestamp_trigger)
ORDER BY channel, heure;


-- Vue : reviews stats par utilisateur
CREATE OR REPLACE VIEW v_reviews_par_user AS
SELECT
    r.user_id,
    r.username,
    r.channel,
    COUNT(*) AS total_reviews,
    COUNT(*) FILTER (WHERE r.action = 'garder') AS garder_count,
    COUNT(*) FILTER (WHERE r.action = 'highlight') AS highlight_count,
    COUNT(*) FILTER (WHERE r.action = 'supprimer') AS supprimer_count,
    MIN(r.timestamp_review) AS first_review,
    MAX(r.timestamp_review) AS last_review
FROM reviews r
GROUP BY r.user_id, r.username, r.channel;


-- =============================================
-- FONCTIONS utilitaires
-- =============================================

-- Fonction : mettre à jour les stats session après un clip
CREATE OR REPLACE FUNCTION update_session_stats()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE sessions
    SET
        clips_detectes = clips_detectes + 1,
        score_max = GREATEST(score_max, NEW.score_final),
        score_min = LEAST(COALESCE(score_min, NEW.score_final), NEW.score_final),
        score_moyen = (
            SELECT AVG(score_final)
            FROM clips
            WHERE session_id = NEW.session_id
        )
    WHERE session_id = NEW.session_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_session_on_clip
AFTER INSERT ON clips
FOR EACH ROW EXECUTE FUNCTION update_session_stats();


-- Fonction : mettre à jour les stats review
CREATE OR REPLACE FUNCTION update_review_stats()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE sessions
    SET
        clips_gardes = clips_gardes + CASE WHEN NEW.action = 'garder' THEN 1 ELSE 0 END,
        clips_highlightes = clips_highlightes + CASE WHEN NEW.action = 'highlight' THEN 1 ELSE 0 END,
        clips_supprimes = clips_supprimes + CASE WHEN NEW.action = 'supprimer' THEN 1 ELSE 0 END
    WHERE session_id = NEW.session_id;

    UPDATE clips
    SET
        decision = NEW.action,
        decision_user = NEW.username,
        decision_user_id = NEW.user_id,
        timestamp_decision = NEW.timestamp_review
    WHERE id = NEW.clip_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_review_stats
AFTER INSERT ON reviews
FOR EACH ROW EXECUTE FUNCTION update_review_stats();


-- =============================================
-- PERMISSIONS (si multi-users)
-- =============================================

-- CREATE ROLE a3_readonly;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO a3_readonly;

-- CREATE ROLE a3_app;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO a3_app;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO a3_app;