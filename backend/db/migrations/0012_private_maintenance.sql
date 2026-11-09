-- Maintenance privée ; aucune mutation de données financières.
CREATE OR REPLACE FUNCTION public.ensure_partitions(months_ahead INTEGER DEFAULT 6)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    parent   TEXT;
    i        INTEGER;
    start_ts TIMESTAMPTZ;
    end_ts   TIMESTAMPTZ;
    part     TEXT;
    created  INTEGER := 0;
BEGIN
    IF months_ahead IS NULL OR months_ahead < 0 OR months_ahead > 24 THEN
        RAISE EXCEPTION 'months_ahead doit être entre 0 et 24';
    END IF;
    FOREACH parent IN ARRAY ARRAY['llm_signals', 'risk_evaluations'] LOOP
        FOR i IN 0..months_ahead LOOP
            start_ts := date_trunc('month', CLOCK_TIMESTAMP()) + (i || ' month')::INTERVAL;
            end_ts   := start_ts + INTERVAL '1 month';
            part     := parent || '_' || to_char(start_ts, 'YYYY_MM');

            CONTINUE WHEN EXISTS (SELECT 1 FROM pg_class WHERE relname = part);

            -- La partition DEFAULT peut déjà contenir des lignes de ce mois
            -- (bot arrêté longtemps). On les met de côté avant de créer la
            -- partition : PostgreSQL refuse sinon l'ATTACH. Table de transit
            -- propre à chaque table parent (les colonnes diffèrent).
            EXECUTE format('DROP TABLE IF EXISTS pg_temp.%I', '_moved_' || parent);
            EXECUTE format('CREATE TEMP TABLE %I (LIKE %I) ON COMMIT DROP', '_moved_' || parent, parent);
            EXECUTE format(
                'WITH moved AS (DELETE FROM %I_default WHERE created_at >= %L AND created_at < %L RETURNING *)
                 INSERT INTO %I SELECT * FROM moved',
                parent, start_ts, end_ts, '_moved_' || parent);

            EXECUTE format(
                'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                part, parent, start_ts, end_ts);

            EXECUTE format('INSERT INTO %I SELECT * FROM %I', parent, '_moved_' || parent);
            created := created + 1;
        END LOOP;
    END LOOP;

    RETURN created;
END;
$$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated;
ALTER VIEW public.v_system_health SET (security_invoker=true);
