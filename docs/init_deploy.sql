-- ============================================================
-- A3 — POST-DEPLOYMENT SCRIPT (RUN AFTER schema.sql)
-- Rafraîchit les vues matérialisées sans CONCURRENTLY
-- (les vues sont vides à la création, CONCURRENTLY exige un index unique sur données)
-- ============================================================

-- Premier refresh sans CONCURRENTLY (vues vides à la création)
REFRESH MATERIALIZED VIEW mv_clip_stats;
REFRESH MATERIALIZED VIEW mv_filter_performance;
REFRESH MATERIALIZED VIEW mv_author_activity;

-- Protection contre les doublons si on rejoue le schema
SELECT cron.unschedule('refresh-mvs');
SELECT cron.unschedule('create-partitions');
SELECT cron.unschedule('create-stream-partitions');