-- ============================================================================
-- 0001 — Schéma initial Trading Ninja
--
-- Cible : PostgreSQL 17 (Supabase). AUCUN TimescaleDB : l'extension est
-- dépréciée chez Supabase et absente en PG17. Les tables de télémétrie sont
-- donc des tables partitionnées NATIVEMENT (PARTITION BY RANGE), avec :
--   * une partition DEFAULT par table  -> un INSERT ne peut jamais échouer,
--     même si la maintenance des partitions a pris du retard ;
--   * public.ensure_partitions()       -> crée les mois à venir (idempotent).
--
-- Conventions :
--   * montants et quantités  : NUMERIC(28,10), jamais FLOAT
--   * taux / pourcentages    : NUMERIC(9,6), exprimés en FRACTION (0.025 = 2.5 %)
--   * horodatages            : TIMESTAMPTZ, toujours en UTC (CLOCK_TIMESTAMP)
--   * multi-symbole          : toutes les tables portent `symbol`
--   * `execution_mode`       : sépare hermétiquement paper / testnet / live,
--     pour qu'aucune statistique ne mélange des trades fictifs et réels.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ----------------------------------------------------------------------------
-- 1. ÉNUMÉRATIONS
-- Miroir EXACT de backend/app/engine/risk_gate.py. Toute valeur ajoutée ici
-- doit être ajoutée là-bas (sinon rejet à l'exécution pour une décision valide).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'signal_action') THEN
        CREATE TYPE signal_action AS ENUM ('BUY', 'SELL', 'HOLD');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'market_regime') THEN
        CREATE TYPE market_regime AS ENUM ('TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'VOLATILE');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'execution_mode') THEN
        CREATE TYPE execution_mode AS ENUM ('PAPER', 'TESTNET', 'LIVE');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'risk_rejection_code') THEN
        CREATE TYPE risk_rejection_code AS ENUM (
            'NONE',
            'ACTION_IS_NOT_BUY',
            'STALE_MARKET_DATA',
            'INVALID_MARKET_DATA',
            'MALFORMED_INTENT',
            'BOT_LOCKED',
            'CIRCUIT_BREAKER_DAILY_DRAWDOWN',
            'CIRCUIT_BREAKER_WEEKLY_DRAWDOWN',
            'CIRCUIT_BREAKER_MAX_DRAWDOWN',
            'CIRCUIT_BREAKER_CONSECUTIVE_LOSSES',
            'MAX_ORDERS_PER_HOUR_EXCEEDED',
            'CONFIDENCE_TOO_LOW',
            'POSITION_ALREADY_OPEN',
            'REJECTED_LOW_VOLATILITY',
            'REJECTED_EXTREME_VOLATILITY',
            'REJECTED_COUNTER_TREND_EMA200',
            'REJECTED_REGIME_CONFLICT',
            'REJECTED_FALLING_KNIFE',
            'INSUFFICIENT_FREE_BALANCE',
            'INVALID_STOP_LOSS_CALCULATION',
            'SIZING_BELOW_EXCHANGE_MINIMUM',
            'EXCHANGE_MIN_NOTIONAL_FAILED',
            'RISK_LIMIT_EXCEEDED'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'order_side') THEN
        CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
    END IF;

    -- STOP_MARKET n'existe pas sur Binance SPOT : le type canonique est
    -- STOP_LOSS (déclenchement au marché). STOP_MARKET est conservé pour
    -- les exchanges qui l'exposent nativement (futures, Bybit, etc.).
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'order_type') THEN
        CREATE TYPE order_type AS ENUM ('MARKET', 'LIMIT', 'STOP_LOSS', 'STOP_LOSS_LIMIT', 'STOP_MARKET', 'TAKE_PROFIT');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'order_status') THEN
        CREATE TYPE order_status AS ENUM (
            'PENDING', 'SUBMITTED', 'FILLED', 'PARTIALLY_FILLED',
            'CANCELED', 'REJECTED', 'EXPIRED', 'UNKNOWN'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'position_status') THEN
        CREATE TYPE position_status AS ENUM ('OPEN', 'CLOSED', 'EMERGENCY_LIQUIDATED');
    END IF;

    -- EXECUTING ajouté : la machine à états le mentionne mais il manquait
    -- dans le DDL d'origine, ce qui rendait l'état non persitable.
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'system_state') THEN
        CREATE TYPE system_state AS ENUM (
            'IDLE', 'EVALUATING', 'EXECUTING', 'IN_POSITION', 'POST_TRADE_SWEEP',
            'COOLDOWN', 'LOCKED_CIRCUIT_BREAKER', 'EMERGENCY_HALT'
        );
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'order_purpose') THEN
        CREATE TYPE order_purpose AS ENUM ('ENTRY', 'STOP_LOSS', 'TAKE_PROFIT', 'EMERGENCY_EXIT', 'RECONCILIATION');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sweep_status') THEN
        CREATE TYPE sweep_status AS ENUM ('PENDING', 'SUCCESS', 'FAILED', 'SKIPPED_BELOW_MINIMUM', 'SIMULATED');
    END IF;
END
$$;


-- ----------------------------------------------------------------------------
-- 2. TÉLÉMÉTRIE : SIGNAUX LLM  (partitionnée par mois)
-- ----------------------------------------------------------------------------

CREATE TABLE llm_signals (
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                execution_mode NOT NULL DEFAULT 'PAPER',
    symbol              VARCHAR(30) NOT NULL,
    model_name          VARCHAR(120) NOT NULL,
    action              signal_action NOT NULL,
    confidence          NUMERIC(9, 6) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    market_regime       market_regime NOT NULL,
    thesis              TEXT NOT NULL,
    prompt_payload      JSONB NOT NULL,
    raw_response        JSONB NOT NULL,
    inference_time_ms   INTEGER NOT NULL CHECK (inference_time_ms >= 0),
    prompt_sha256       CHAR(64),
    temperature         NUMERIC(4, 3) NOT NULL DEFAULT 0,
    PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);

CREATE TABLE llm_signals_default PARTITION OF llm_signals DEFAULT;

CREATE INDEX idx_llm_signals_action_time ON llm_signals (action, created_at DESC);
CREATE INDEX idx_llm_signals_symbol_time ON llm_signals (symbol, mode, created_at DESC);
CREATE INDEX idx_llm_signals_id ON llm_signals (id);


-- ----------------------------------------------------------------------------
-- 3. AUDIT TRAIL RISK GATEWAY  (partitionnée par mois)
-- ----------------------------------------------------------------------------

CREATE TABLE risk_evaluations (
    id                      UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                    execution_mode NOT NULL DEFAULT 'PAPER',
    symbol                  VARCHAR(30) NOT NULL,

    -- Référence LOGICIELLE vers llm_signals (pas de FK possible : la PK de la
    -- table partitionnée est composite). Intégrité assurée par l'application,
    -- garantie par idx_risk_eval_signal.
    signal_id               UUID NOT NULL,
    signal_created_at       TIMESTAMPTZ,

    approved                BOOLEAN NOT NULL,
    rejection_code          risk_rejection_code NOT NULL DEFAULT 'NONE',
    rejection_details       TEXT,

    -- Contexte marché observé au moment T
    market_price            NUMERIC(28, 10) NOT NULL CHECK (market_price > 0),
    ema_200_h4              NUMERIC(28, 10) NOT NULL CHECK (ema_200_h4 > 0),
    atr_14_h4               NUMERIC(28, 10) NOT NULL CHECK (atr_14_h4 > 0),
    atr_pct_of_price        NUMERIC(9, 6) NOT NULL,
    prev_candle_return_atr  NUMERIC(10, 4) NOT NULL DEFAULT 0,
    decision_timeframe      VARCHAR(8) NOT NULL DEFAULT '4h',

    -- État du compte confronté
    account_equity_quote    NUMERIC(28, 10) NOT NULL CHECK (account_equity_quote >= 0),
    free_quote_balance      NUMERIC(28, 10) NOT NULL CHECK (free_quote_balance >= 0),
    daily_drawdown_pct      NUMERIC(9, 6) NOT NULL DEFAULT 0,
    weekly_drawdown_pct     NUMERIC(9, 6) NOT NULL DEFAULT 0,
    drawdown_from_hwm_pct   NUMERIC(9, 6) NOT NULL DEFAULT 0,
    consecutive_losses      INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_losses >= 0),

    -- Dimensionnement (NULL si rejeté avant calcul)
    computed_amount         NUMERIC(28, 10),
    computed_stop_loss      NUMERIC(28, 10),
    computed_take_profit    NUMERIC(28, 10),
    intended_risk_quote     NUMERIC(28, 10),
    effective_risk_quote    NUMERIC(28, 10),
    effective_risk_pct      NUMERIC(9, 6),

    checks_passed           TEXT[] NOT NULL DEFAULT '{}',
    evaluation_time_ms      INTEGER NOT NULL CHECK (evaluation_time_ms >= 0),

    PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);

CREATE TABLE risk_evaluations_default PARTITION OF risk_evaluations DEFAULT;

CREATE INDEX idx_risk_eval_signal ON risk_evaluations (signal_id);
CREATE INDEX idx_risk_eval_symbol_time ON risk_evaluations (symbol, created_at DESC);
CREATE INDEX idx_risk_eval_approved_time ON risk_evaluations (approved, created_at DESC);
CREATE INDEX idx_risk_eval_mode_time ON risk_evaluations (mode, created_at DESC);


-- ----------------------------------------------------------------------------
-- 4. ÉTAT COURANT DU BOT  (singleton — la machine à états s'y appuie au boot)
-- ----------------------------------------------------------------------------

CREATE TABLE bot_state (
    id                  SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    state               system_state NOT NULL DEFAULT 'IDLE',
    mode                execution_mode NOT NULL DEFAULT 'PAPER',
    since               TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),

    -- Minuteries persistées : un redémarrage ne doit pas annuler un cooldown
    cooldown_until      TIMESTAMPTZ,
    locked_at           TIMESTAMPTZ,
    lock_reason         risk_rejection_code,
    lock_details        TEXT,
    unlocked_by         VARCHAR(100),
    unlocked_at         TIMESTAMPTZ,

    -- Compteurs de risque
    consecutive_losses  INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_losses >= 0),
    equity_hwm_quote    NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (equity_hwm_quote >= 0),

    -- Suivi opérationnel
    last_evaluated_at   TIMESTAMPTZ,
    last_trade_at       TIMESTAMPTZ,
    last_heartbeat_at   TIMESTAMPTZ,
    last_error          TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP()
);

INSERT INTO bot_state (id) VALUES (1) ON CONFLICT DO NOTHING;


-- ----------------------------------------------------------------------------
-- 5. INSTANTANÉS D'ÉQUITÉ  (source de vérité des drawdowns 24 h / 7 j / HWM)
-- ----------------------------------------------------------------------------

CREATE TABLE equity_snapshots (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    taken_at            TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                execution_mode NOT NULL,
    symbol              VARCHAR(30),
    equity_quote        NUMERIC(28, 10) NOT NULL CHECK (equity_quote >= 0),
    free_quote          NUMERIC(28, 10) NOT NULL CHECK (free_quote >= 0),
    position_value_quote NUMERIC(28, 10) NOT NULL DEFAULT 0,
    source              VARCHAR(30) NOT NULL DEFAULT 'SCHEDULER'
);

CREATE INDEX idx_equity_snap_mode_time ON equity_snapshots (mode, taken_at DESC);
CREATE INDEX idx_equity_snap_brin ON equity_snapshots USING BRIN (taken_at);


-- ----------------------------------------------------------------------------
-- 6. POSITIONS  (cycle de vie mutable)
-- ----------------------------------------------------------------------------

CREATE TABLE positions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol                  VARCHAR(30) NOT NULL,
    mode                    execution_mode NOT NULL DEFAULT 'PAPER',
    status                  position_status NOT NULL DEFAULT 'OPEN',

    opened_at               TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    closed_at               TIMESTAMPTZ,
    CHECK (closed_at IS NULL OR closed_at >= opened_at),

    -- Traçabilité vers la décision qui a ouvert la position
    entry_signal_id         UUID,
    entry_risk_evaluation_id UUID,

    entry_price             NUMERIC(28, 10) NOT NULL CHECK (entry_price > 0),
    exit_price              NUMERIC(28, 10) CHECK (exit_price > 0),
    initial_amount          NUMERIC(28, 10) NOT NULL CHECK (initial_amount > 0),
    closed_amount           NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (closed_amount >= 0),
    initial_notional_quote  NUMERIC(28, 10) NOT NULL DEFAULT 0,

    -- Niveaux de risque. Le TP n'est plus implicite : il est calculé à
    -- l'entrée et persisté, sinon la machine à états n'a aucune sortie à
    -- atteindre en dehors du stop.
    stop_loss_price         NUMERIC(28, 10) NOT NULL CHECK (stop_loss_price > 0),
    take_profit_price       NUMERIC(28, 10) CHECK (take_profit_price > 0),
    risk_amount_quote       NUMERIC(28, 10) NOT NULL CHECK (risk_amount_quote > 0),
    r_multiple_at_tp        NUMERIC(6, 3),

    -- Protection effective constatée sur l'exchange
    protective_order_id     VARCHAR(100),
    protective_order_ok     BOOLEAN NOT NULL DEFAULT FALSE,
    protective_checked_at   TIMESTAMPTZ,

    -- Comptabilité nette finale
    gross_pnl_quote         NUMERIC(28, 10) NOT NULL DEFAULT 0,
    total_fees_quote        NUMERIC(28, 10) NOT NULL DEFAULT 0,
    net_pnl_quote           NUMERIC(28, 10) NOT NULL DEFAULT 0,

    exit_reason             VARCHAR(40),
    is_profit_skimmed       BOOLEAN NOT NULL DEFAULT FALSE,
    skimmed_amount_quote    NUMERIC(28, 10) NOT NULL DEFAULT 0,

    strategy_version        VARCHAR(20) NOT NULL DEFAULT 'v1'
);

-- Garde-fou « monoposition » appliqué par la BASE, pas seulement par le code.
-- Deux couches valent mieux qu'une : une course entre deux workers ne peut
-- pas ouvrir deux positions sur le même symbole.
CREATE UNIQUE INDEX ux_positions_one_open_per_symbol
    ON positions (symbol, mode) WHERE status = 'OPEN';

CREATE INDEX idx_positions_status_symbol ON positions (status, symbol);
CREATE INDEX idx_positions_opened_at ON positions (opened_at DESC);
CREATE INDEX idx_positions_closed_at ON positions (closed_at DESC) WHERE status <> 'OPEN';


-- ----------------------------------------------------------------------------
-- 7. ORDRES D'EXÉCUTION
-- ----------------------------------------------------------------------------

CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id         UUID REFERENCES positions(id) ON DELETE SET NULL,
    risk_evaluation_id  UUID,

    exchange_order_id   VARCHAR(100),
    client_order_id     VARCHAR(100) NOT NULL UNIQUE,
    symbol              VARCHAR(30) NOT NULL,
    mode                execution_mode NOT NULL DEFAULT 'PAPER',

    side                order_side NOT NULL,
    order_type          order_type NOT NULL,
    purpose             order_purpose NOT NULL DEFAULT 'ENTRY',
    status              order_status NOT NULL DEFAULT 'PENDING',

    requested_amount    NUMERIC(28, 10) NOT NULL CHECK (requested_amount > 0),
    filled_amount       NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (filled_amount >= 0),
    requested_price     NUMERIC(28, 10),
    stop_price          NUMERIC(28, 10),
    average_fill_price  NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (average_fill_price >= 0),

    fee_amount          NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_currency        VARCHAR(12),

    slippage_quote      NUMERIC(28, 10),
    latency_ms          INTEGER,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    raw_exchange_payload JSONB,
    error_message       TEXT
);

CREATE INDEX idx_orders_position_id ON orders (position_id);
CREATE INDEX idx_orders_exchange_id ON orders (exchange_order_id);
CREATE INDEX idx_orders_status ON orders (status);
-- Source de vérité du coupe-circuit « max ordres / heure »
CREATE INDEX idx_orders_mode_created ON orders (mode, created_at DESC);
CREATE INDEX idx_orders_open_by_symbol ON orders (symbol, status)
    WHERE status IN ('PENDING', 'SUBMITTED', 'PARTIALLY_FILLED');


-- ----------------------------------------------------------------------------
-- 8. PROFIT SWEEPER  (écrémage vers la réserve)
-- ----------------------------------------------------------------------------

CREATE TABLE profit_sweeps (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id             UUID NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                    execution_mode NOT NULL DEFAULT 'PAPER',

    quote_currency          VARCHAR(12) NOT NULL DEFAULT 'USDT',
    realized_net_pnl        NUMERIC(28, 10) NOT NULL CHECK (realized_net_pnl > 0),
    sweep_ratio             NUMERIC(6, 5) NOT NULL DEFAULT 0.30000
                            CHECK (sweep_ratio > 0 AND sweep_ratio < 1),
    computed_sweep_quote    NUMERIC(28, 10) NOT NULL CHECK (computed_sweep_quote > 0),
    swept_amount            NUMERIC(28, 10) NOT NULL CHECK (swept_amount >= 0),
    retained_trading_capital NUMERIC(28, 10) NOT NULL CHECK (retained_trading_capital >= 0),

    from_account            VARCHAR(30) NOT NULL DEFAULT 'spot',
    to_account              VARCHAR(30) NOT NULL DEFAULT 'funding',
    transfer_transaction_id VARCHAR(120),
    transfer_status         sweep_status NOT NULL DEFAULT 'PENDING',
    attempts                SMALLINT NOT NULL DEFAULT 0,
    next_retry_at           TIMESTAMPTZ,
    error_message           TEXT,

    -- Un écrémage sous le minimum de transfert de l'exchange est reporté,
    -- jamais marqué réussi : sinon le ledger ment sur l'argent sécurisé.
    UNIQUE (position_id)
);

CREATE INDEX idx_profit_sweeps_position ON profit_sweeps (position_id);
CREATE INDEX idx_profit_sweeps_pending ON profit_sweeps (transfer_status, next_retry_at)
    WHERE transfer_status IN ('PENDING', 'FAILED');


-- ----------------------------------------------------------------------------
-- 9. JOURNAL DES TRANSITIONS D'ÉTAT
-- ----------------------------------------------------------------------------

CREATE TABLE system_state_logs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                execution_mode NOT NULL DEFAULT 'PAPER',
    previous_state      system_state NOT NULL,
    current_state       system_state NOT NULL,
    trigger_reason      VARCHAR(150) NOT NULL,
    trigger_code        risk_rejection_code,
    context_data        JSONB,
    resolved_at         TIMESTAMPTZ,
    resolved_by_admin   VARCHAR(100)
);

CREATE INDEX idx_system_state_logs_time ON system_state_logs (created_at DESC);


-- ----------------------------------------------------------------------------
-- 10. MAINTENANCE DES PARTITIONS
-- ----------------------------------------------------------------------------

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

COMMENT ON FUNCTION public.ensure_partitions(INTEGER) IS
    'Crée les partitions mensuelles manquantes (idempotent). Appelée au démarrage et périodiquement.';

SELECT public.ensure_partitions(6);


-- ----------------------------------------------------------------------------
-- 11. VUES ANALYTIQUES
-- ----------------------------------------------------------------------------

-- 11.a PnL cumulé (la vue d'origine prétendait être « journalière » sans
-- aucun filtre de date : elle est renommée et une vraie vue daily est ajoutée).
CREATE OR REPLACE VIEW v_dashboard_pnl_metrics AS
SELECT
    mode,
    COUNT(*)                                                            AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl_quote > 0)                           AS winning_trades,
    COUNT(*) FILTER (WHERE net_pnl_quote <= 0)                          AS losing_trades,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE net_pnl_quote > 0) / NULLIF(COUNT(*), 0), 2
    )                                                                   AS winrate_pct,
    COALESCE(SUM(gross_pnl_quote), 0)                                   AS total_gross_pnl,
    COALESCE(SUM(total_fees_quote), 0)                                  AS total_fees_paid,
    COALESCE(SUM(net_pnl_quote), 0)                                     AS total_net_pnl,
    COALESCE(SUM(skimmed_amount_quote), 0)                              AS total_vault_reserved,
    COALESCE(SUM(net_pnl_quote) / NULLIF(SUM(risk_amount_quote), 0), 0) AS realized_r_multiple
FROM positions
WHERE status = 'CLOSED'
GROUP BY mode;

CREATE OR REPLACE VIEW v_dashboard_pnl_daily AS
SELECT
    mode,
    date_trunc('day', closed_at)::DATE AS trade_day,
    COUNT(*)                                                    AS trades,
    COUNT(*) FILTER (WHERE net_pnl_quote > 0)                   AS wins,
    COALESCE(SUM(net_pnl_quote), 0)                             AS net_pnl,
    COALESCE(SUM(total_fees_quote), 0)                          AS fees_paid
FROM positions
WHERE status = 'CLOSED' AND closed_at IS NOT NULL
GROUP BY mode, date_trunc('day', closed_at)
ORDER BY trade_day DESC;

CREATE OR REPLACE VIEW v_risk_rejection_stats AS
SELECT
    mode,
    rejection_code,
    COUNT(*)                                                                AS occurrences,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY mode), 0), 2) AS percentage_of_total
FROM risk_evaluations
WHERE approved = FALSE
GROUP BY mode, rejection_code
ORDER BY occurrences DESC;

CREATE OR REPLACE VIEW v_open_positions AS
SELECT
    p.id,
    p.symbol,
    p.mode,
    p.entry_price,
    p.initial_amount,
    p.stop_loss_price,
    p.take_profit_price,
    p.risk_amount_quote,
    p.protective_order_ok,
    p.opened_at,
    CLOCK_TIMESTAMP() - p.opened_at AS age
FROM positions p
WHERE p.status = 'OPEN';

-- 11.b Tableau de bord d'un coup d'œil
CREATE OR REPLACE VIEW v_system_health AS
SELECT
    b.state,
    b.mode,
    b.since,
    b.cooldown_until,
    b.lock_reason,
    b.consecutive_losses,
    b.equity_hwm_quote,
    b.updated_at,
    (SELECT COUNT(*) FROM positions p WHERE p.status = 'OPEN')                       AS open_positions,
    (SELECT COUNT(*) FROM orders o WHERE o.created_at > CLOCK_TIMESTAMP() - INTERVAL '1 hour') AS orders_last_hour,
    (SELECT COUNT(*) FROM risk_evaluations r WHERE r.created_at > CLOCK_TIMESTAMP() - INTERVAL '24 hours') AS evaluations_24h,
    (SELECT COUNT(*) FROM risk_evaluations r WHERE r.approved AND r.created_at > CLOCK_TIMESTAMP() - INTERVAL '24 hours') AS approvals_24h
FROM bot_state b
WHERE b.id = 1;
