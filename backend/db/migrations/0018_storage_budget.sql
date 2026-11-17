-- Rétention privée : jamais de purge des données financières ou des preuves de cycle.
CREATE SCHEMA tn_storage_maintenance;
REVOKE ALL ON SCHEMA tn_storage_maintenance FROM PUBLIC, anon, authenticated;

CREATE TABLE tn_storage_maintenance.status (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    checked_at timestamptz NOT NULL,
    db_bytes bigint NOT NULL CHECK (db_bytes >= 0),
    level text NOT NULL CHECK (level IN ('OK','WARNING','CRITICAL','EXCEEDED')),
    digests_deleted integer NOT NULL DEFAULT 0,
    candles_deleted integer NOT NULL DEFAULT 0,
    last_candle_purge_at timestamptz
);
REVOKE ALL ON tn_storage_maintenance.status FROM PUBLIC, anon, authenticated;
GRANT USAGE ON SCHEMA tn_storage_maintenance TO tn_paper_api;
GRANT SELECT ON tn_storage_maintenance.status TO tn_paper_api;

CREATE FUNCTION tn_storage_maintenance.run()
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog AS $$
DECLARE
    bytes bigint;
    prior_purge timestamptz;
    removed_digests integer := 0;
    removed_candles integer := 0;
    moment timestamptz := clock_timestamp();
    severity text;
BEGIN
    -- Évite deux purges concurrentes, sans bloquer les transactions du moteur.
    IF NOT pg_try_advisory_xact_lock(1414416963, 180) THEN RETURN; END IF;
    PERFORM set_config('lock_timeout', '2s', true);
    PERFORM set_config('statement_timeout', '20s', true);
    bytes := pg_database_size(current_database());
    SELECT last_candle_purge_at INTO prior_purge FROM tn_storage_maintenance.status WHERE singleton;

    WITH expired AS (
        SELECT id FROM public.market_intel_digests
        WHERE observed_at < moment - interval '30 days' AND valid_until < moment
        ORDER BY observed_at LIMIT 1000 FOR UPDATE SKIP LOCKED
    )
    DELETE FROM public.market_intel_digests d USING expired e WHERE d.id=e.id;
    GET DIAGNOSTICS removed_digests = ROW_COUNT;

    -- Réserve de 60 Mo : au plus 10 000 bougies publiques >3 ans par jour.
    -- Les traces, positions, ordres et ledger ne sont jamais concernés.
    IF bytes >= 390000000 AND (prior_purge IS NULL OR prior_purge < moment - interval '1 day') THEN
        WITH expired AS (
            SELECT ctid FROM public.market_candles_history
            WHERE open_time < moment - interval '3 years'
              AND source IN ('kraken-archive', 'kraken-rest')
            ORDER BY open_time LIMIT 10000 FOR UPDATE SKIP LOCKED
        )
        DELETE FROM public.market_candles_history c USING expired e WHERE c.ctid=e.ctid;
        GET DIAGNOSTICS removed_candles = ROW_COUNT;
        prior_purge := moment;
    END IF;

    severity := CASE WHEN bytes >= 450000000 THEN 'EXCEEDED'
                     WHEN bytes >= 425000000 THEN 'CRITICAL'
                     WHEN bytes >= 390000000 THEN 'WARNING' ELSE 'OK' END;
    INSERT INTO tn_storage_maintenance.status(singleton,checked_at,db_bytes,level,digests_deleted,candles_deleted,last_candle_purge_at)
    VALUES(true,moment,bytes,severity,removed_digests,removed_candles,prior_purge)
    ON CONFLICT(singleton) DO UPDATE SET checked_at=excluded.checked_at,db_bytes=excluded.db_bytes,
        level=excluded.level,digests_deleted=excluded.digests_deleted,candles_deleted=excluded.candles_deleted,
        last_candle_purge_at=excluded.last_candle_purge_at;
    IF severity <> 'OK' THEN
        RAISE WARNING 'Trading Ninja storage %: % bytes; vacuum and capacity review may be required', severity, bytes;
    END IF;

    -- pg_cron conserve ses propres journaux : limiter ceux de notre seul job.
    IF to_regclass('cron.job_run_details') IS NOT NULL THEN
        EXECUTE 'DELETE FROM cron.job_run_details WHERE end_time < now() - interval ''7 days'' '
                'AND jobid IN (SELECT jobid FROM cron.job WHERE jobname = ''tn-storage-budget'')';
    END IF;
END;
$$;
REVOKE ALL ON FUNCTION tn_storage_maintenance.run() FROM PUBLIC, anon, authenticated;

-- pg_cron est préchargé sur Supabase. La base jetable PostgreSQL standard peut
-- ne pas disposer de l'extension : la fonction reste alors testable sans job.
DO $install$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='pg_cron')
       AND position('pg_cron' in current_setting('shared_preload_libraries')) > 0 THEN
        CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
        PERFORM cron.schedule('tn-storage-budget', '*/15 * * * *',
                              'SELECT tn_storage_maintenance.run()');
    END IF;
END
$install$;
