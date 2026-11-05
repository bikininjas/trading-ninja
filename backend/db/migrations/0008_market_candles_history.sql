-- ============================================================================
-- 0008 — Historique de marché multi-années (hors télémétrie du bot)
--
-- Pourquoi cette table existe : l'API REST publique de Kraken plafonne à 720
-- bougies et ignore un `since` plus ancien (30 jours en H1, 120 jours en H4).
-- Aucun filtre de tendance H4 ne peut être validé statistiquement sur 25 jours.
-- Les archives OHLCVT publiques de Kraken remontent à l'ouverture de chaque
-- marché ; cette table les reçoit une fois pour toutes.
--
-- Pourquoi PAS de TimescaleDB ni de partitionnement : l'extension n'existe pas
-- sur Supabase PostgreSQL 17, et le volume ne le justifie pas —
--   3 paires × 2 timeframes × 26 300 bougies (3 ans de H1) ≈ 158 000 lignes,
-- soit quelques Mo. Une table simple avec une clé primaire (symbol, timeframe,
-- open_time) sert les requêtes « fenêtre glissante » par index, sans la
-- complexité d'un DEFAULT partition ni d'un ensure_partitions().
-- Si un jour 20 paires × 1 minute entrent en jeu (~31 M lignes/an), ce sera une
-- nouvelle migration, pas un changement de cette table.
--
-- Sécurité : ces bougies sont publiques, mais la table vit dans le schéma
-- `public` exposé par PostgREST. RLS activée sans policy + révocation explicite,
-- comme le reste (D-011).
-- ============================================================================

CREATE TABLE IF NOT EXISTS market_candles_history (
    symbol        VARCHAR(32)  NOT NULL,
    timeframe     VARCHAR(4)   NOT NULL CHECK (timeframe IN ('1h', '4h')),
    open_time     TIMESTAMPTZ  NOT NULL,

    open_price    NUMERIC(28,10) NOT NULL,
    high_price    NUMERIC(28,10) NOT NULL,
    low_price     NUMERIC(28,10) NOT NULL,
    close_price   NUMERIC(28,10) NOT NULL,
    volume        NUMERIC(28,10) NOT NULL,
    trades        BIGINT,

    source        VARCHAR(20)  NOT NULL
                  CHECK (source IN ('kraken-archive', 'kraken-rest', 'csv')),
    ingested_at   TIMESTAMPTZ  NOT NULL DEFAULT CLOCK_TIMESTAMP(),

    PRIMARY KEY (symbol, timeframe, open_time),

    -- Cohérence OHLC : une bougie incohérente fausserait un stop simulé.
    CONSTRAINT market_candles_history_ohlc_coherent CHECK (
        low_price > 0
        AND high_price >= low_price
        AND high_price >= open_price
        AND high_price >= close_price
        AND low_price <= open_price
        AND low_price <= close_price
    ),
    CONSTRAINT market_candles_history_volume_non_negative CHECK (volume >= 0),
    CONSTRAINT market_candles_history_trades_non_negative CHECK (trades IS NULL OR trades >= 0)
);

COMMENT ON TABLE market_candles_history IS
    'Bougies H1/H4 importées des archives publiques Kraken, pour le backtest multi-années. '
    'Une ligne par (paire, timeframe, ouverture). Écriture idempotente (ON CONFLICT DO NOTHING).';

COMMENT ON COLUMN market_candles_history.open_time IS
    'Ouverture de la bougie en UTC — la clôture vaut open_time + 1 h (H1) ou 4 h (H4).';
COMMENT ON COLUMN market_candles_history.source IS
    'kraken-archive = ZIP trimestriel officiel ; kraken-rest = complément des 720 dernières bougies.';

-- 2. Couverture et trous, lisibles d'un coup d'œil.
--    `expected_candles` est calculé sur l'intervalle first→last : c'est le nombre
--    de bougies qu'une série SANS trou aurait. Kraken ne publie pas les intervalles
--    sans trade, donc un trou peut être réel (marché inactif) ou une anomalie —
--    la vue ne tranche pas, elle rend l'écart visible et le rapport d'ingestion
--    détaille chaque trou avec sa durée.
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
        / CASE timeframe WHEN '1h' THEN 3600 ELSE 14400 END
    )::BIGINT + 1                                                  AS expected_candles,
    GREATEST(
        0,
        FLOOR(
            EXTRACT(EPOCH FROM (last_open - first_open))
            / CASE timeframe WHEN '1h' THEN 3600 ELSE 14400 END
        )::BIGINT + 1 - candles
    )                                                              AS missing_candles,
    ROUND(
        100.0 * GREATEST(
            0,
            FLOOR(
                EXTRACT(EPOCH FROM (last_open - first_open))
                / CASE timeframe WHEN '1h' THEN 3600 ELSE 14400 END
            )::BIGINT + 1 - candles
        ) / NULLIF(
            FLOOR(
                EXTRACT(EPOCH FROM (last_open - first_open))
                / CASE timeframe WHEN '1h' THEN 3600 ELSE 14400 END
            )::BIGINT + 1, 0
        ), 3
    )                                                              AS missing_pct,
    EXTRACT(EPOCH FROM (last_open - first_open)) / 86400.0         AS span_days
FROM bounds;

COMMENT ON VIEW v_history_coverage IS
    'Couverture de l''historique importé : bougies, trous attendus, profondeur en jours.';

-- 3. Fermeture de l'accès public (le schéma public est exposé par PostgREST).
ALTER TABLE market_candles_history ENABLE ROW LEVEL SECURITY;
ALTER VIEW v_history_coverage SET (security_invoker = true);
REVOKE ALL ON market_candles_history FROM anon, authenticated;
REVOKE ALL ON v_history_coverage FROM anon, authenticated;
