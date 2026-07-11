-- sql/schema_postgresql.sql
-- Schéma canonique de la DB a3_db (PostgreSQL 13, VPS Oracle).
-- Généré via : pg_dump --schema-only --no-owner --no-privileges
-- Régénérer ce fichier plutôt que de l'éditer à la main si le schéma live change.
-- Import : psql -h <host> -U <user> -d <db_name> -f sql/schema_postgresql.sql

--
-- PostgreSQL database dump
--

\restrict Gm8JYhXB7czJU9E9AlYdzooBvlD3e1Ih4X88YrlnteHVM8cMX4JwbROk8fsLESH

-- Dumped from database version 13.23
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: create_filter_events_partition(date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_filter_events_partition(partition_date date) RETURNS void
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: create_stream_events_partition(date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.create_stream_events_partition(partition_date date) RETURNS void
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: refresh_all_mvs(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.refresh_all_mvs() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_clip_stats;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_filter_performance;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_author_activity;
END;
$$;


--
-- Name: sync_session_clip_counts_delta(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sync_session_clip_counts_delta() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: sync_session_clip_counts_delta_fr(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.sync_session_clip_counts_delta_fr() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.action = 'garder' THEN
            UPDATE sessions SET clips_validated = clips_validated + 1 WHERE id = NEW.session_id;
        ELSIF NEW.action = 'highlight' THEN
            UPDATE sessions SET clips_highlighted = clips_highlighted + 1 WHERE id = NEW.session_id;
        ELSIF NEW.action IN ('supprimer', 'expire') THEN
            UPDATE sessions SET clips_rejected = clips_rejected + 1 WHERE id = NEW.session_id;
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.action = 'garder' THEN
            UPDATE sessions SET clips_validated = clips_validated - 1 WHERE id = OLD.session_id;
        ELSIF OLD.action = 'highlight' THEN
            UPDATE sessions SET clips_highlighted = clips_highlighted - 1 WHERE id = OLD.session_id;
        ELSIF OLD.action IN ('supprimer', 'expire') THEN
            UPDATE sessions SET clips_rejected = clips_rejected - 1 WHERE id = OLD.session_id;
        END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        UPDATE sessions SET
            clips_validated   = clips_validated   + CASE WHEN NEW.action = 'garder' AND OLD.action <> 'garder' THEN 1 WHEN NEW.action <> 'garder' AND OLD.action = 'garder' THEN -1 ELSE 0 END,
            clips_highlighted = clips_highlighted + CASE WHEN NEW.action = 'highlight' AND OLD.action <> 'highlight' THEN 1 WHEN NEW.action <> 'highlight' AND OLD.action = 'highlight' THEN -1 ELSE 0 END,
            clips_rejected    = clips_rejected    + CASE WHEN NEW.action IN ('supprimer', 'expire') AND OLD.action NOT IN ('supprimer', 'expire') THEN 1 WHEN NEW.action NOT IN ('supprimer', 'expire') AND OLD.action IN ('supprimer', 'expire') THEN -1 ELSE 0 END
        WHERE id = NEW.session_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: calibration; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.calibration (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    filtre_nom character varying(64) NOT NULL,
    est_calibre boolean DEFAULT false,
    samples_count integer DEFAULT 0,
    timestamp_calibration timestamp with time zone,
    mean numeric(10,4),
    std numeric(10,4),
    min_samples_required integer,
    z_score_threshold numeric(4,2),
    mean_fond numeric(10,4),
    std_fond numeric(10,4),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: calibration_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.calibration_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: calibration_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.calibration_id_seq OWNED BY public.calibration.id;


--
-- Name: channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.channels (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(100) NOT NULL,
    twitch_id bigint,
    created_at timestamp with time zone DEFAULT now(),
    is_active boolean DEFAULT true,
    org_id uuid
);


--
-- Name: chat_windows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chat_windows (
    id bigint NOT NULL,
    session_id bigint,
    channel_id uuid NOT NULL,
    window_start timestamp with time zone NOT NULL,
    window_end timestamp with time zone NOT NULL,
    message_count integer DEFAULT 0,
    unique_authors_count integer DEFAULT 0,
    message_rate_avg numeric(8,4),
    emote_density_avg numeric(8,4),
    emotion_score_avg numeric(8,4),
    repetition_score_avg numeric(8,4),
    clip_activity_score numeric(8,4),
    viewer_count integer,
    game_category character varying(128),
    triggered_clip_id bigint,
    label character varying(20)
);


--
-- Name: chat_windows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.chat_windows_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: chat_windows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.chat_windows_id_seq OWNED BY public.chat_windows.id;


--
-- Name: clips; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clips (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    clip_num integer NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    score_final double precision NOT NULL,
    score_components jsonb,
    trigger_author_hash character(16),
    repetition_word character(16),
    file_path_hq text,
    file_path_preview text,
    duration_seconds double precision,
    decision character varying(20),
    reviewed_at timestamp with time zone,
    reviewer_hash character(16),
    ml_features jsonb,
    ai_confidence double precision,
    created_at timestamp with time zone DEFAULT now(),
    model_version_id integer,
    viewer_count integer,
    game_category character varying(128),
    stream_language character varying(10)
);


--
-- Name: clips_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.clips_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: clips_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.clips_id_seq OWNED BY public.clips.id;


--
-- Name: filter_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
)
PARTITION BY RANGE ("timestamp");


--
-- Name: filter_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.filter_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: filter_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.filter_events_id_seq OWNED BY public.filter_events.id;


--
-- Name: filter_events_2025; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2025 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m01; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m01 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m02; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m02 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m03; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m03 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m04; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m04 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m05; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m05 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m06; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m06 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m07; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m07 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m08; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m08 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_events_2026_m09; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_events_2026_m09 (
    id bigint DEFAULT nextval('public.filter_events_id_seq'::regclass) NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    author_id character(16),
    event_type character varying(50) NOT NULL,
    filter_name character varying(50) NOT NULL,
    score_raw double precision NOT NULL,
    score_weighted double precision NOT NULL,
    z_score double precision,
    threshold double precision,
    is_triggered boolean DEFAULT false,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: filter_performance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.filter_performance (
    id bigint NOT NULL,
    session_id bigint,
    model_version_id integer,
    channel_id uuid NOT NULL,
    filter_name character varying(64) NOT NULL,
    trigger_count integer DEFAULT 0,
    true_positive_count integer DEFAULT 0,
    computed_at timestamp with time zone DEFAULT now()
);


--
-- Name: filter_performance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.filter_performance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: filter_performance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.filter_performance_id_seq OWNED BY public.filter_performance.id;


--
-- Name: model_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_versions (
    id integer NOT NULL,
    version_tag character varying(32) NOT NULL,
    seuil_clip numeric(5,4),
    poids_filtres jsonb,
    deployed_at timestamp with time zone DEFAULT now(),
    description text
);


--
-- Name: model_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.model_versions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: model_versions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.model_versions_id_seq OWNED BY public.model_versions.id;


--
-- Name: mv_clip_stats; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_clip_stats AS
 SELECT c.channel_id,
    date(c.detected_at) AS date,
    count(*) AS total_clips,
    avg(c.score_final) AS avg_score,
    max(c.score_final) AS max_score,
    count(*) FILTER (WHERE ((c.decision)::text = 'validated'::text)) AS validated,
    count(*) FILTER (WHERE ((c.decision)::text = 'highlighted'::text)) AS highlighted,
    count(*) FILTER (WHERE ((c.decision)::text = 'rejected'::text)) AS rejected,
    avg(date_part('epoch'::text, (c.reviewed_at - c.detected_at))) AS avg_review_latency
   FROM public.clips c
  GROUP BY c.channel_id, (date(c.detected_at))
  WITH NO DATA;


--
-- Name: mv_filter_performance; Type: MATERIALIZED VIEW; Schema: public; Owner: -
--

CREATE MATERIALIZED VIEW public.mv_filter_performance AS
 SELECT fe.filter_name,
    date(fe."timestamp") AS date,
    count(*) AS total_events,
    count(*) FILTER (WHERE fe.is_triggered) AS triggers,
    avg(fe.z_score) AS avg_zscore,
    avg(fe.score_weighted) AS avg_weighted_score
   FROM public.filter_events fe
  GROUP BY fe.filter_name, (date(fe."timestamp"))
  WITH NO DATA;


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(128) NOT NULL,
    plan character varying(20) DEFAULT 'self-hosted'::character varying,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reviews (
    id bigint NOT NULL,
    clip_id bigint NOT NULL,
    session_id bigint NOT NULL,
    action character varying(20) NOT NULL,
    reviewer_hash character(16) NOT NULL,
    reviewed_at timestamp with time zone DEFAULT now(),
    latency_ms integer,
    reaction_time_sec numeric(6,1),
    is_first_review boolean DEFAULT true,
    reason character varying(32)
);


--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.reviews_id_seq OWNED BY public.reviews.id;


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id bigint NOT NULL,
    channel_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    duration_seconds integer,
    clips_detected integer DEFAULT 0,
    clips_validated integer DEFAULT 0,
    clips_rejected integer DEFAULT 0,
    clips_highlighted integer DEFAULT 0,
    score_avg double precision,
    score_max double precision,
    status character varying(20) DEFAULT 'active'::character varying,
    version character varying(20) DEFAULT 'v0.0.4'::character varying,
    org_id uuid,
    model_version_id integer,
    avg_viewers integer
);


--
-- Name: sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sessions_id_seq OWNED BY public.sessions.id;


--
-- Name: snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.snapshots (
    id bigint NOT NULL,
    session_id bigint NOT NULL,
    channel_id uuid NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now(),
    message_count integer DEFAULT 0,
    unique_authors integer DEFAULT 0,
    clip_count integer DEFAULT 0,
    score_avg double precision,
    message_rate_avg double precision,
    emote_density_avg double precision,
    emotion_hype_avg double precision,
    emotion_rage_avg double precision,
    filters_calibrated integer DEFAULT 0,
    filters_active integer DEFAULT 0
);


--
-- Name: snapshots_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.snapshots_id_seq OWNED BY public.snapshots.id;


--
-- Name: stream_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events (
    id bigint NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
)
PARTITION BY RANGE ("timestamp");


--
-- Name: stream_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stream_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stream_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stream_events_id_seq OWNED BY public.stream_events.id;


--
-- Name: stream_events_2026_m05; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events_2026_m05 (
    id bigint DEFAULT nextval('public.stream_events_id_seq'::regclass) NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
);


--
-- Name: stream_events_2026_m06; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events_2026_m06 (
    id bigint DEFAULT nextval('public.stream_events_id_seq'::regclass) NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
);


--
-- Name: stream_events_2026_m07; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events_2026_m07 (
    id bigint DEFAULT nextval('public.stream_events_id_seq'::regclass) NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
);


--
-- Name: stream_events_2026_m08; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events_2026_m08 (
    id bigint DEFAULT nextval('public.stream_events_id_seq'::regclass) NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
);


--
-- Name: stream_events_2026_m09; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stream_events_2026_m09 (
    id bigint DEFAULT nextval('public.stream_events_id_seq'::regclass) NOT NULL,
    session_id bigint,
    channel_id uuid,
    event_type character varying(50) NOT NULL,
    level character varying(10) NOT NULL,
    message text,
    component character varying(50),
    error_detail text,
    data jsonb,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    erreur text
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    org_id uuid,
    discord_hash character(16) NOT NULL,
    role character varying(20) DEFAULT 'reviewer'::character varying,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: filter_events_2025; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2025 FOR VALUES FROM ('2025-01-01 00:00:00+00') TO ('2026-01-01 00:00:00+00');


--
-- Name: filter_events_2026_m01; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m01 FOR VALUES FROM ('2026-01-01 00:00:00+00') TO ('2026-02-01 00:00:00+00');


--
-- Name: filter_events_2026_m02; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m02 FOR VALUES FROM ('2026-02-01 00:00:00+00') TO ('2026-03-01 00:00:00+00');


--
-- Name: filter_events_2026_m03; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m03 FOR VALUES FROM ('2026-03-01 00:00:00+00') TO ('2026-04-01 00:00:00+00');


--
-- Name: filter_events_2026_m04; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m04 FOR VALUES FROM ('2026-04-01 00:00:00+00') TO ('2026-05-01 00:00:00+00');


--
-- Name: filter_events_2026_m05; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m05 FOR VALUES FROM ('2026-05-01 00:00:00+00') TO ('2026-06-01 00:00:00+00');


--
-- Name: filter_events_2026_m06; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m06 FOR VALUES FROM ('2026-06-01 00:00:00+00') TO ('2026-07-01 00:00:00+00');


--
-- Name: filter_events_2026_m07; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m07 FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');


--
-- Name: filter_events_2026_m08; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m08 FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00');


--
-- Name: filter_events_2026_m09; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ATTACH PARTITION public.filter_events_2026_m09 FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');


--
-- Name: stream_events_2026_m05; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ATTACH PARTITION public.stream_events_2026_m05 FOR VALUES FROM ('2026-05-01 00:00:00+00') TO ('2026-06-01 00:00:00+00');


--
-- Name: stream_events_2026_m06; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ATTACH PARTITION public.stream_events_2026_m06 FOR VALUES FROM ('2026-06-01 00:00:00+00') TO ('2026-07-01 00:00:00+00');


--
-- Name: stream_events_2026_m07; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ATTACH PARTITION public.stream_events_2026_m07 FOR VALUES FROM ('2026-07-01 00:00:00+00') TO ('2026-08-01 00:00:00+00');


--
-- Name: stream_events_2026_m08; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ATTACH PARTITION public.stream_events_2026_m08 FOR VALUES FROM ('2026-08-01 00:00:00+00') TO ('2026-09-01 00:00:00+00');


--
-- Name: stream_events_2026_m09; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ATTACH PARTITION public.stream_events_2026_m09 FOR VALUES FROM ('2026-09-01 00:00:00+00') TO ('2026-10-01 00:00:00+00');


--
-- Name: calibration id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calibration ALTER COLUMN id SET DEFAULT nextval('public.calibration_id_seq'::regclass);


--
-- Name: chat_windows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_windows ALTER COLUMN id SET DEFAULT nextval('public.chat_windows_id_seq'::regclass);


--
-- Name: clips id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips ALTER COLUMN id SET DEFAULT nextval('public.clips_id_seq'::regclass);


--
-- Name: filter_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events ALTER COLUMN id SET DEFAULT nextval('public.filter_events_id_seq'::regclass);


--
-- Name: filter_performance id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_performance ALTER COLUMN id SET DEFAULT nextval('public.filter_performance_id_seq'::regclass);


--
-- Name: model_versions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions ALTER COLUMN id SET DEFAULT nextval('public.model_versions_id_seq'::regclass);


--
-- Name: reviews id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews ALTER COLUMN id SET DEFAULT nextval('public.reviews_id_seq'::regclass);


--
-- Name: sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions ALTER COLUMN id SET DEFAULT nextval('public.sessions_id_seq'::regclass);


--
-- Name: snapshots id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshots ALTER COLUMN id SET DEFAULT nextval('public.snapshots_id_seq'::regclass);


--
-- Name: stream_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events ALTER COLUMN id SET DEFAULT nextval('public.stream_events_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: calibration calibration_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.calibration
    ADD CONSTRAINT calibration_pkey PRIMARY KEY (id);


--
-- Name: channels channels_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_name_key UNIQUE (name);


--
-- Name: channels channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_pkey PRIMARY KEY (id);


--
-- Name: chat_windows chat_windows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_windows
    ADD CONSTRAINT chat_windows_pkey PRIMARY KEY (id);


--
-- Name: clips clips_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT clips_pkey PRIMARY KEY (id);


--
-- Name: filter_events filter_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events
    ADD CONSTRAINT filter_events_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2025 filter_events_2025_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2025
    ADD CONSTRAINT filter_events_2025_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m01 filter_events_2026_m01_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m01
    ADD CONSTRAINT filter_events_2026_m01_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m02 filter_events_2026_m02_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m02
    ADD CONSTRAINT filter_events_2026_m02_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m03 filter_events_2026_m03_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m03
    ADD CONSTRAINT filter_events_2026_m03_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m04 filter_events_2026_m04_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m04
    ADD CONSTRAINT filter_events_2026_m04_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m05 filter_events_2026_m05_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m05
    ADD CONSTRAINT filter_events_2026_m05_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m06 filter_events_2026_m06_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m06
    ADD CONSTRAINT filter_events_2026_m06_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m07 filter_events_2026_m07_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m07
    ADD CONSTRAINT filter_events_2026_m07_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m08 filter_events_2026_m08_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m08
    ADD CONSTRAINT filter_events_2026_m08_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_events_2026_m09 filter_events_2026_m09_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_events_2026_m09
    ADD CONSTRAINT filter_events_2026_m09_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: filter_performance filter_performance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_performance
    ADD CONSTRAINT filter_performance_pkey PRIMARY KEY (id);


--
-- Name: model_versions model_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_pkey PRIMARY KEY (id);


--
-- Name: model_versions model_versions_version_tag_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_versions
    ADD CONSTRAINT model_versions_version_tag_key UNIQUE (version_tag);


--
-- Name: organizations organizations_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_name_key UNIQUE (name);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: snapshots snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshots
    ADD CONSTRAINT snapshots_pkey PRIMARY KEY (id);


--
-- Name: stream_events stream_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events
    ADD CONSTRAINT stream_events_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: stream_events_2026_m05 stream_events_2026_m05_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events_2026_m05
    ADD CONSTRAINT stream_events_2026_m05_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: stream_events_2026_m06 stream_events_2026_m06_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events_2026_m06
    ADD CONSTRAINT stream_events_2026_m06_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: stream_events_2026_m07 stream_events_2026_m07_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events_2026_m07
    ADD CONSTRAINT stream_events_2026_m07_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: stream_events_2026_m08 stream_events_2026_m08_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events_2026_m08
    ADD CONSTRAINT stream_events_2026_m08_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: stream_events_2026_m09 stream_events_2026_m09_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stream_events_2026_m09
    ADD CONSTRAINT stream_events_2026_m09_pkey PRIMARY KEY (id, "timestamp");


--
-- Name: clips uq_clips_session_num; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT uq_clips_session_num UNIQUE (session_id, clip_num);


--
-- Name: users users_discord_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_discord_hash_key UNIQUE (discord_hash);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_filter_events_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filter_events_channel ON ONLY public.filter_events USING btree (channel_id);


--
-- Name: filter_events_2025_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2025_channel_id_idx ON public.filter_events_2025 USING btree (channel_id);


--
-- Name: idx_filter_events_filter_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filter_events_filter_name ON ONLY public.filter_events USING btree (filter_name);


--
-- Name: filter_events_2025_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2025_filter_name_idx1 ON public.filter_events_2025 USING btree (filter_name);


--
-- Name: idx_filter_events_triggered; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filter_events_triggered ON ONLY public.filter_events USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2025_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2025_is_triggered_idx ON public.filter_events_2025 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: idx_filter_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filter_events_session ON ONLY public.filter_events USING btree (session_id);


--
-- Name: filter_events_2025_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2025_session_id_idx ON public.filter_events_2025 USING btree (session_id);


--
-- Name: idx_filter_events_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_filter_events_timestamp ON ONLY public.filter_events USING btree ("timestamp" DESC);


--
-- Name: filter_events_2025_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2025_timestamp_idx ON public.filter_events_2025 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m01_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m01_channel_id_idx ON public.filter_events_2026_m01 USING btree (channel_id);


--
-- Name: filter_events_2026_m01_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m01_filter_name_idx1 ON public.filter_events_2026_m01 USING btree (filter_name);


--
-- Name: filter_events_2026_m01_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m01_is_triggered_idx ON public.filter_events_2026_m01 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m01_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m01_session_id_idx ON public.filter_events_2026_m01 USING btree (session_id);


--
-- Name: filter_events_2026_m01_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m01_timestamp_idx ON public.filter_events_2026_m01 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m02_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m02_channel_id_idx ON public.filter_events_2026_m02 USING btree (channel_id);


--
-- Name: filter_events_2026_m02_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m02_filter_name_idx1 ON public.filter_events_2026_m02 USING btree (filter_name);


--
-- Name: filter_events_2026_m02_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m02_is_triggered_idx ON public.filter_events_2026_m02 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m02_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m02_session_id_idx ON public.filter_events_2026_m02 USING btree (session_id);


--
-- Name: filter_events_2026_m02_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m02_timestamp_idx ON public.filter_events_2026_m02 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m03_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m03_channel_id_idx ON public.filter_events_2026_m03 USING btree (channel_id);


--
-- Name: filter_events_2026_m03_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m03_filter_name_idx1 ON public.filter_events_2026_m03 USING btree (filter_name);


--
-- Name: filter_events_2026_m03_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m03_is_triggered_idx ON public.filter_events_2026_m03 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m03_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m03_session_id_idx ON public.filter_events_2026_m03 USING btree (session_id);


--
-- Name: filter_events_2026_m03_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m03_timestamp_idx ON public.filter_events_2026_m03 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m04_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m04_channel_id_idx ON public.filter_events_2026_m04 USING btree (channel_id);


--
-- Name: filter_events_2026_m04_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m04_filter_name_idx1 ON public.filter_events_2026_m04 USING btree (filter_name);


--
-- Name: filter_events_2026_m04_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m04_is_triggered_idx ON public.filter_events_2026_m04 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m04_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m04_session_id_idx ON public.filter_events_2026_m04 USING btree (session_id);


--
-- Name: filter_events_2026_m04_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m04_timestamp_idx ON public.filter_events_2026_m04 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m05_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m05_channel_id_idx ON public.filter_events_2026_m05 USING btree (channel_id);


--
-- Name: filter_events_2026_m05_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m05_filter_name_idx1 ON public.filter_events_2026_m05 USING btree (filter_name);


--
-- Name: filter_events_2026_m05_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m05_is_triggered_idx ON public.filter_events_2026_m05 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m05_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m05_session_id_idx ON public.filter_events_2026_m05 USING btree (session_id);


--
-- Name: filter_events_2026_m05_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m05_timestamp_idx ON public.filter_events_2026_m05 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m06_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m06_channel_id_idx ON public.filter_events_2026_m06 USING btree (channel_id);


--
-- Name: filter_events_2026_m06_filter_name_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m06_filter_name_idx1 ON public.filter_events_2026_m06 USING btree (filter_name);


--
-- Name: filter_events_2026_m06_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m06_is_triggered_idx ON public.filter_events_2026_m06 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m06_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m06_session_id_idx ON public.filter_events_2026_m06 USING btree (session_id);


--
-- Name: filter_events_2026_m06_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m06_timestamp_idx ON public.filter_events_2026_m06 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m07_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m07_channel_id_idx ON public.filter_events_2026_m07 USING btree (channel_id);


--
-- Name: filter_events_2026_m07_filter_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m07_filter_name_idx ON public.filter_events_2026_m07 USING btree (filter_name);


--
-- Name: filter_events_2026_m07_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m07_is_triggered_idx ON public.filter_events_2026_m07 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m07_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m07_session_id_idx ON public.filter_events_2026_m07 USING btree (session_id);


--
-- Name: filter_events_2026_m07_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m07_timestamp_idx ON public.filter_events_2026_m07 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m08_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m08_channel_id_idx ON public.filter_events_2026_m08 USING btree (channel_id);


--
-- Name: filter_events_2026_m08_filter_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m08_filter_name_idx ON public.filter_events_2026_m08 USING btree (filter_name);


--
-- Name: filter_events_2026_m08_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m08_is_triggered_idx ON public.filter_events_2026_m08 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m08_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m08_session_id_idx ON public.filter_events_2026_m08 USING btree (session_id);


--
-- Name: filter_events_2026_m08_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m08_timestamp_idx ON public.filter_events_2026_m08 USING btree ("timestamp" DESC);


--
-- Name: filter_events_2026_m09_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m09_channel_id_idx ON public.filter_events_2026_m09 USING btree (channel_id);


--
-- Name: filter_events_2026_m09_filter_name_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m09_filter_name_idx ON public.filter_events_2026_m09 USING btree (filter_name);


--
-- Name: filter_events_2026_m09_is_triggered_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m09_is_triggered_idx ON public.filter_events_2026_m09 USING btree (is_triggered) WHERE (is_triggered = true);


--
-- Name: filter_events_2026_m09_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m09_session_id_idx ON public.filter_events_2026_m09 USING btree (session_id);


--
-- Name: filter_events_2026_m09_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX filter_events_2026_m09_timestamp_idx ON public.filter_events_2026_m09 USING btree ("timestamp" DESC);


--
-- Name: idx_calibration_filtre; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calibration_filtre ON public.calibration USING btree (filtre_nom);


--
-- Name: idx_calibration_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calibration_session ON public.calibration USING btree (session_id);


--
-- Name: idx_calibration_session_filtre; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_calibration_session_filtre ON public.calibration USING btree (session_id, filtre_nom);


--
-- Name: idx_calibration_session_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_calibration_session_id ON public.calibration USING btree (session_id);


--
-- Name: idx_channels_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_channels_name ON public.channels USING btree (name);


--
-- Name: idx_chat_windows_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_windows_session ON public.chat_windows USING btree (session_id);


--
-- Name: idx_chat_windows_start; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chat_windows_start ON public.chat_windows USING btree (window_start);


--
-- Name: idx_clips_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_channel ON public.clips USING btree (channel_id);


--
-- Name: idx_clips_channel_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_channel_id ON public.clips USING btree (channel_id);


--
-- Name: idx_clips_decision; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_decision ON public.clips USING btree (decision);


--
-- Name: idx_clips_detected; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_detected ON public.clips USING btree (detected_at);


--
-- Name: idx_clips_ml_features; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_ml_features ON public.clips USING gin (ml_features);


--
-- Name: idx_clips_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_score ON public.clips USING btree (score_final DESC);


--
-- Name: idx_clips_score_components; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_score_components ON public.clips USING gin (score_components);


--
-- Name: idx_clips_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_session ON public.clips USING btree (session_id);


--
-- Name: idx_clips_session_num; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_clips_session_num ON public.clips USING btree (session_id, clip_num);


--
-- Name: idx_reviews_clip; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reviews_clip ON public.reviews USING btree (clip_id);


--
-- Name: idx_reviews_reviewer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reviews_reviewer ON public.reviews USING btree (reviewer_hash);


--
-- Name: idx_reviews_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reviews_session ON public.reviews USING btree (session_id);


--
-- Name: idx_sessions_channel_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_channel_id ON public.sessions USING btree (channel_id);


--
-- Name: idx_sessions_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_model ON public.sessions USING btree (model_version_id);


--
-- Name: idx_snapshots_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_session ON public.snapshots USING btree (session_id);


--
-- Name: idx_snapshots_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_snapshots_timestamp ON public.snapshots USING btree ("timestamp" DESC);


--
-- Name: idx_stream_events_channel; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stream_events_channel ON ONLY public.stream_events USING btree (channel_id);


--
-- Name: idx_stream_events_data; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stream_events_data ON ONLY public.stream_events USING gin (data);


--
-- Name: idx_stream_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stream_events_event_type ON ONLY public.stream_events USING btree (event_type);


--
-- Name: idx_stream_events_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stream_events_session ON ONLY public.stream_events USING btree (session_id);


--
-- Name: idx_stream_events_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_stream_events_timestamp ON ONLY public.stream_events USING btree ("timestamp" DESC);


--
-- Name: mv_clip_stats_channel_id_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX mv_clip_stats_channel_id_date_idx ON public.mv_clip_stats USING btree (channel_id, date);


--
-- Name: mv_filter_performance_filter_name_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX mv_filter_performance_filter_name_date_idx ON public.mv_filter_performance USING btree (filter_name, date);


--
-- Name: stream_events_2026_m05_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m05_channel_id_idx ON public.stream_events_2026_m05 USING btree (channel_id);


--
-- Name: stream_events_2026_m05_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m05_data_idx ON public.stream_events_2026_m05 USING gin (data);


--
-- Name: stream_events_2026_m05_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m05_event_type_idx ON public.stream_events_2026_m05 USING btree (event_type);


--
-- Name: stream_events_2026_m05_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m05_session_id_idx ON public.stream_events_2026_m05 USING btree (session_id);


--
-- Name: stream_events_2026_m05_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m05_timestamp_idx ON public.stream_events_2026_m05 USING btree ("timestamp" DESC);


--
-- Name: stream_events_2026_m06_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m06_channel_id_idx ON public.stream_events_2026_m06 USING btree (channel_id);


--
-- Name: stream_events_2026_m06_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m06_data_idx ON public.stream_events_2026_m06 USING gin (data);


--
-- Name: stream_events_2026_m06_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m06_event_type_idx ON public.stream_events_2026_m06 USING btree (event_type);


--
-- Name: stream_events_2026_m06_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m06_session_id_idx ON public.stream_events_2026_m06 USING btree (session_id);


--
-- Name: stream_events_2026_m06_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m06_timestamp_idx ON public.stream_events_2026_m06 USING btree ("timestamp" DESC);


--
-- Name: stream_events_2026_m07_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m07_channel_id_idx ON public.stream_events_2026_m07 USING btree (channel_id);


--
-- Name: stream_events_2026_m07_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m07_data_idx ON public.stream_events_2026_m07 USING gin (data);


--
-- Name: stream_events_2026_m07_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m07_event_type_idx ON public.stream_events_2026_m07 USING btree (event_type);


--
-- Name: stream_events_2026_m07_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m07_session_id_idx ON public.stream_events_2026_m07 USING btree (session_id);


--
-- Name: stream_events_2026_m07_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m07_timestamp_idx ON public.stream_events_2026_m07 USING btree ("timestamp" DESC);


--
-- Name: stream_events_2026_m08_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m08_channel_id_idx ON public.stream_events_2026_m08 USING btree (channel_id);


--
-- Name: stream_events_2026_m08_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m08_data_idx ON public.stream_events_2026_m08 USING gin (data);


--
-- Name: stream_events_2026_m08_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m08_event_type_idx ON public.stream_events_2026_m08 USING btree (event_type);


--
-- Name: stream_events_2026_m08_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m08_session_id_idx ON public.stream_events_2026_m08 USING btree (session_id);


--
-- Name: stream_events_2026_m08_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m08_timestamp_idx ON public.stream_events_2026_m08 USING btree ("timestamp" DESC);


--
-- Name: stream_events_2026_m09_channel_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m09_channel_id_idx ON public.stream_events_2026_m09 USING btree (channel_id);


--
-- Name: stream_events_2026_m09_data_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m09_data_idx ON public.stream_events_2026_m09 USING gin (data);


--
-- Name: stream_events_2026_m09_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m09_event_type_idx ON public.stream_events_2026_m09 USING btree (event_type);


--
-- Name: stream_events_2026_m09_session_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m09_session_id_idx ON public.stream_events_2026_m09 USING btree (session_id);


--
-- Name: stream_events_2026_m09_timestamp_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX stream_events_2026_m09_timestamp_idx ON public.stream_events_2026_m09 USING btree ("timestamp" DESC);


--
-- Name: uq_filter_performance_session_filter; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_filter_performance_session_filter ON public.filter_performance USING btree (session_id, filter_name);


--
-- Name: filter_events_2025_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2025_channel_id_idx;


--
-- Name: filter_events_2025_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2025_filter_name_idx1;


--
-- Name: filter_events_2025_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2025_is_triggered_idx;


--
-- Name: filter_events_2025_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2025_pkey;


--
-- Name: filter_events_2025_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2025_session_id_idx;


--
-- Name: filter_events_2025_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2025_timestamp_idx;


--
-- Name: filter_events_2026_m01_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m01_channel_id_idx;


--
-- Name: filter_events_2026_m01_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m01_filter_name_idx1;


--
-- Name: filter_events_2026_m01_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m01_is_triggered_idx;


--
-- Name: filter_events_2026_m01_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m01_pkey;


--
-- Name: filter_events_2026_m01_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m01_session_id_idx;


--
-- Name: filter_events_2026_m01_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m01_timestamp_idx;


--
-- Name: filter_events_2026_m02_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m02_channel_id_idx;


--
-- Name: filter_events_2026_m02_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m02_filter_name_idx1;


--
-- Name: filter_events_2026_m02_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m02_is_triggered_idx;


--
-- Name: filter_events_2026_m02_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m02_pkey;


--
-- Name: filter_events_2026_m02_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m02_session_id_idx;


--
-- Name: filter_events_2026_m02_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m02_timestamp_idx;


--
-- Name: filter_events_2026_m03_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m03_channel_id_idx;


--
-- Name: filter_events_2026_m03_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m03_filter_name_idx1;


--
-- Name: filter_events_2026_m03_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m03_is_triggered_idx;


--
-- Name: filter_events_2026_m03_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m03_pkey;


--
-- Name: filter_events_2026_m03_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m03_session_id_idx;


--
-- Name: filter_events_2026_m03_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m03_timestamp_idx;


--
-- Name: filter_events_2026_m04_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m04_channel_id_idx;


--
-- Name: filter_events_2026_m04_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m04_filter_name_idx1;


--
-- Name: filter_events_2026_m04_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m04_is_triggered_idx;


--
-- Name: filter_events_2026_m04_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m04_pkey;


--
-- Name: filter_events_2026_m04_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m04_session_id_idx;


--
-- Name: filter_events_2026_m04_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m04_timestamp_idx;


--
-- Name: filter_events_2026_m05_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m05_channel_id_idx;


--
-- Name: filter_events_2026_m05_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m05_filter_name_idx1;


--
-- Name: filter_events_2026_m05_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m05_is_triggered_idx;


--
-- Name: filter_events_2026_m05_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m05_pkey;


--
-- Name: filter_events_2026_m05_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m05_session_id_idx;


--
-- Name: filter_events_2026_m05_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m05_timestamp_idx;


--
-- Name: filter_events_2026_m06_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m06_channel_id_idx;


--
-- Name: filter_events_2026_m06_filter_name_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m06_filter_name_idx1;


--
-- Name: filter_events_2026_m06_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m06_is_triggered_idx;


--
-- Name: filter_events_2026_m06_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m06_pkey;


--
-- Name: filter_events_2026_m06_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m06_session_id_idx;


--
-- Name: filter_events_2026_m06_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m06_timestamp_idx;


--
-- Name: filter_events_2026_m07_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m07_channel_id_idx;


--
-- Name: filter_events_2026_m07_filter_name_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m07_filter_name_idx;


--
-- Name: filter_events_2026_m07_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m07_is_triggered_idx;


--
-- Name: filter_events_2026_m07_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m07_pkey;


--
-- Name: filter_events_2026_m07_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m07_session_id_idx;


--
-- Name: filter_events_2026_m07_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m07_timestamp_idx;


--
-- Name: filter_events_2026_m08_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m08_channel_id_idx;


--
-- Name: filter_events_2026_m08_filter_name_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m08_filter_name_idx;


--
-- Name: filter_events_2026_m08_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m08_is_triggered_idx;


--
-- Name: filter_events_2026_m08_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m08_pkey;


--
-- Name: filter_events_2026_m08_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m08_session_id_idx;


--
-- Name: filter_events_2026_m08_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m08_timestamp_idx;


--
-- Name: filter_events_2026_m09_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_channel ATTACH PARTITION public.filter_events_2026_m09_channel_id_idx;


--
-- Name: filter_events_2026_m09_filter_name_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_filter_name ATTACH PARTITION public.filter_events_2026_m09_filter_name_idx;


--
-- Name: filter_events_2026_m09_is_triggered_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_triggered ATTACH PARTITION public.filter_events_2026_m09_is_triggered_idx;


--
-- Name: filter_events_2026_m09_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.filter_events_pkey ATTACH PARTITION public.filter_events_2026_m09_pkey;


--
-- Name: filter_events_2026_m09_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_session ATTACH PARTITION public.filter_events_2026_m09_session_id_idx;


--
-- Name: filter_events_2026_m09_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_filter_events_timestamp ATTACH PARTITION public.filter_events_2026_m09_timestamp_idx;


--
-- Name: stream_events_2026_m05_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_channel ATTACH PARTITION public.stream_events_2026_m05_channel_id_idx;


--
-- Name: stream_events_2026_m05_data_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_data ATTACH PARTITION public.stream_events_2026_m05_data_idx;


--
-- Name: stream_events_2026_m05_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_event_type ATTACH PARTITION public.stream_events_2026_m05_event_type_idx;


--
-- Name: stream_events_2026_m05_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.stream_events_pkey ATTACH PARTITION public.stream_events_2026_m05_pkey;


--
-- Name: stream_events_2026_m05_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_session ATTACH PARTITION public.stream_events_2026_m05_session_id_idx;


--
-- Name: stream_events_2026_m05_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_timestamp ATTACH PARTITION public.stream_events_2026_m05_timestamp_idx;


--
-- Name: stream_events_2026_m06_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_channel ATTACH PARTITION public.stream_events_2026_m06_channel_id_idx;


--
-- Name: stream_events_2026_m06_data_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_data ATTACH PARTITION public.stream_events_2026_m06_data_idx;


--
-- Name: stream_events_2026_m06_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_event_type ATTACH PARTITION public.stream_events_2026_m06_event_type_idx;


--
-- Name: stream_events_2026_m06_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.stream_events_pkey ATTACH PARTITION public.stream_events_2026_m06_pkey;


--
-- Name: stream_events_2026_m06_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_session ATTACH PARTITION public.stream_events_2026_m06_session_id_idx;


--
-- Name: stream_events_2026_m06_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_timestamp ATTACH PARTITION public.stream_events_2026_m06_timestamp_idx;


--
-- Name: stream_events_2026_m07_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_channel ATTACH PARTITION public.stream_events_2026_m07_channel_id_idx;


--
-- Name: stream_events_2026_m07_data_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_data ATTACH PARTITION public.stream_events_2026_m07_data_idx;


--
-- Name: stream_events_2026_m07_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_event_type ATTACH PARTITION public.stream_events_2026_m07_event_type_idx;


--
-- Name: stream_events_2026_m07_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.stream_events_pkey ATTACH PARTITION public.stream_events_2026_m07_pkey;


--
-- Name: stream_events_2026_m07_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_session ATTACH PARTITION public.stream_events_2026_m07_session_id_idx;


--
-- Name: stream_events_2026_m07_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_timestamp ATTACH PARTITION public.stream_events_2026_m07_timestamp_idx;


--
-- Name: stream_events_2026_m08_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_channel ATTACH PARTITION public.stream_events_2026_m08_channel_id_idx;


--
-- Name: stream_events_2026_m08_data_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_data ATTACH PARTITION public.stream_events_2026_m08_data_idx;


--
-- Name: stream_events_2026_m08_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_event_type ATTACH PARTITION public.stream_events_2026_m08_event_type_idx;


--
-- Name: stream_events_2026_m08_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.stream_events_pkey ATTACH PARTITION public.stream_events_2026_m08_pkey;


--
-- Name: stream_events_2026_m08_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_session ATTACH PARTITION public.stream_events_2026_m08_session_id_idx;


--
-- Name: stream_events_2026_m08_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_timestamp ATTACH PARTITION public.stream_events_2026_m08_timestamp_idx;


--
-- Name: stream_events_2026_m09_channel_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_channel ATTACH PARTITION public.stream_events_2026_m09_channel_id_idx;


--
-- Name: stream_events_2026_m09_data_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_data ATTACH PARTITION public.stream_events_2026_m09_data_idx;


--
-- Name: stream_events_2026_m09_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_event_type ATTACH PARTITION public.stream_events_2026_m09_event_type_idx;


--
-- Name: stream_events_2026_m09_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.stream_events_pkey ATTACH PARTITION public.stream_events_2026_m09_pkey;


--
-- Name: stream_events_2026_m09_session_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_session ATTACH PARTITION public.stream_events_2026_m09_session_id_idx;


--
-- Name: stream_events_2026_m09_timestamp_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.idx_stream_events_timestamp ATTACH PARTITION public.stream_events_2026_m09_timestamp_idx;


--
-- Name: reviews trigger_sync_session_reviews; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trigger_sync_session_reviews AFTER INSERT OR DELETE OR UPDATE ON public.reviews FOR EACH ROW EXECUTE FUNCTION public.sync_session_clip_counts_delta_fr();


--
-- Name: channels channels_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.channels
    ADD CONSTRAINT channels_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: chat_windows chat_windows_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_windows
    ADD CONSTRAINT chat_windows_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id);


--
-- Name: chat_windows chat_windows_triggered_clip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chat_windows
    ADD CONSTRAINT chat_windows_triggered_clip_id_fkey FOREIGN KEY (triggered_clip_id) REFERENCES public.clips(id);


--
-- Name: clips clips_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT clips_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id);


--
-- Name: clips clips_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clips
    ADD CONSTRAINT clips_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: filter_events filter_events_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.filter_events
    ADD CONSTRAINT filter_events_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id);


--
-- Name: filter_performance filter_performance_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_performance
    ADD CONSTRAINT filter_performance_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: filter_performance filter_performance_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.filter_performance
    ADD CONSTRAINT filter_performance_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.sessions(id);


--
-- Name: reviews reviews_clip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reviews
    ADD CONSTRAINT reviews_clip_id_fkey FOREIGN KEY (clip_id) REFERENCES public.clips(id);


--
-- Name: sessions sessions_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id);


--
-- Name: sessions sessions_model_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_model_version_id_fkey FOREIGN KEY (model_version_id) REFERENCES public.model_versions(id);


--
-- Name: sessions sessions_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- Name: snapshots snapshots_channel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.snapshots
    ADD CONSTRAINT snapshots_channel_id_fkey FOREIGN KEY (channel_id) REFERENCES public.channels(id);


--
-- Name: users users_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.organizations(id);


--
-- PostgreSQL database dump complete
--

\unrestrict Gm8JYhXB7czJU9E9AlYdzooBvlD3e1Ih4X88YrlnteHVM8cMX4JwbROk8fsLESH

