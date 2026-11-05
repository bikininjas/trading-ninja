-- ============================================================================
-- 0009 — Autoriser le Daily dans l'historique de marché
--
-- Pourquoi cette migration : SWING_MACRO_D1 (D-060) décide sur des bougies
-- Daily. Le backtest les agrège depuis H4, mais l'ingestion doit pouvoir
-- conserver le D1 natif sans contourner la contrainte CHECK de 0008.
--
-- Le changement est volontairement minimal : élargissement de la contrainte,
-- puis recalcul de la couverture avec 86 400 secondes pour '1d'. Aucune ligne
-- existante n'est réécrite ; les timeframes H1/H4 gardent exactement le même
-- comportement.
-- ============================================================================

ALTER TABLE market_candles_history
    DROP CONSTRAINT IF EXISTS market_candles_history_timeframe_check;

ALTER TABLE market_candles_history
    ADD CONSTRAINT market_candles_history_timeframe_check
    CHECK (timeframe IN ('1h', '4h', '1d'));

COMMENT ON TABLE market_candles_history IS
    'Bougies H1/H4/D1 importées des archives publiques Kraken, pour le backtest multi-années. '
    'Une ligne par (paire, timeframe, ouverture). Écriture idempotente (ON CONFLICT DO NOTHING).';

COMMENT ON COLUMN market_candles_history.open_time IS
    'Ouverture de la bougie en UTC — la clôture vaut open_time + 1 h (H1), 4 h (H4) ou 24 h (D1).';

CREATE OR REPLACE VIEW v_history_coverage AS
WITH bounds AS (
    SELECT
        symbol,
        timeframe,
        COUNT(*)          AS candles,
        MIN(open_time)    AS first_open,
        MAX(open_time)    AS last_open,
        MAX(ingested_at)  AS last_ingest
    FROM market_candles_history
    GROUP BY symbol, timeframe
)
SELECT
    symbol,
    timeframe,
    candles,
    first_open,
    last_open,
    last_ingest,
    FLOOR(
        EXTRACT(EPOCH FROM (last_open - first_open))
        / CASE timeframe
            WHEN '1h' THEN 3600
            WHEN '4h' THEN 14400
            WHEN '1d' THEN 86400
        END
    )::BIGINT + 1                                                  AS expected_candles,
    GREATEST(
        0,
        FLOOR(
            EXTRACT(EPOCH FROM (last_open - first_open))
            / CASE timeframe
                WHEN '1h' THEN 3600
                WHEN '4h' THEN 14400
                WHEN '1d' THEN 86400
            END
        )::BIGINT + 1 - candles
    )                                                              AS missing_candles,
    ROUND(
        100.0 * GREATEST(
            0,
            FLOOR(
                EXTRACT(EPOCH FROM (last_open - first_open))
                / CASE timeframe
                    WHEN '1h' THEN 3600
                    WHEN '4h' THEN 14400
                    WHEN '1d' THEN 86400
                END
            )::BIGINT + 1 - candles
        ) / NULLIF(
            FLOOR(
                EXTRACT(EPOCH FROM (last_open - first_open))
                / CASE timeframe
                    WHEN '1h' THEN 3600
                    WHEN '4h' THEN 14400
                    WHEN '1d' THEN 86400
                END
            )::BIGINT + 1, 0
        ), 3
    )                                                              AS missing_pct,
    EXTRACT(EPOCH FROM (last_open - first_open)) / 86400.0         AS span_days
FROM bounds;

COMMENT ON VIEW v_history_coverage IS
    'Couverture de l''historique importé : bougies, trous attendus, profondeur en jours.';

-- La vue doit rester security_invoker (D-011) : sinon elle contourne la RLS.
ALTER VIEW v_history_coverage SET (security_invoker = true);
REVOKE ALL ON v_history_coverage FROM anon, authenticated;
