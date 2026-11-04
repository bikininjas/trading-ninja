> **DOCUMENT HISTORIQUE — PÉRIMÉ, NE PAS APPLIQUER TEL QUEL.**
>
> Ce fichier est la spécification d'origine, conservée pour la traçabilité. Depuis, la
> réalité technique a divergé sur plusieurs points (TimescaleDB indisponible, codes de
> rejet unifiés, états et colonnes manquants, stop et take-profit redéfinis).
>
> Les sources qui font foi sont, dans cet ordre :
> `backend/db/migrations/*.sql` → `backend/app/engine/risk_gate.py` →
> `docs/DECISIONS.md` → `docs/ROADMAP.md`.
>
> Pour comprendre l'écart entre ce document et le code : `docs/DECISIONS.md` (D-001 à
> D-008) et l'entrée du 2026-11-04 dans `docs/JOURNAL.md`.

---

-- ============================================================================
-- SCHÉMA DE BASE DE DONNÉES : TRADING BOT ENGINE & RISK GATEWAY AUDIT
-- Compatible PostgreSQL 15+ et extension TimescaleDB (optionnelle mais intégrée)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";     -- Génération UUID v4 native
CREATE EXTENSION IF NOT EXISTS "timescaledb";   -- Hypertables & partitionnement temporel

-- ----------------------------------------------------------------------------
-- 1. ÉNUMÉRATIONS (ENUMS) TYPÉES
-- ----------------------------------------------------------------------------

CREATE TYPE signal_action AS ENUM ('BUY', 'SELL', 'HOLD');

CREATE TYPE market_regime AS ENUM ('TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'VOLATILE');

CREATE TYPE risk_rejection_code AS ENUM (
    'NONE',
    'ACTION_IS_NOT_BUY',
    'CIRCUIT_BREAKER_DAILY_DRAWDOWN',
    'CIRCUIT_BREAKER_CONSECUTIVE_LOSSES',
    'POSITION_ALREADY_OPEN',
    'CONFIDENCE_TOO_LOW',
    'REJECTED_COUNTER_TREND_EMA200',
    'REJECTED_FALLING_KNIFE',
    'INSUFFICIENT_FREE_BALANCE',
    'INVALID_STOP_LOSS_CALCULATION',
    'EXCHANGE_MIN_NOTIONAL_FAILED',
    'STALE_MARKET_DATA'
);

CREATE TYPE order_side AS ENUM ('BUY', 'SELL');

CREATE TYPE order_type AS ENUM ('MARKET', 'LIMIT', 'STOP_MARKET', 'STOP_LOSS_LIMIT');

CREATE TYPE order_status AS ENUM (
    'PENDING',
    'SUBMITTED',
    'FILLED',
    'PARTIALLY_FILLED',
    'CANCELED',
    'REJECTED',
    'EXPIRED'
);

CREATE TYPE position_status AS ENUM ('OPEN', 'CLOSED', 'EMERGENCY_LIQUIDATED');

CREATE TYPE system_state AS ENUM (
    'IDLE',
    'EVALUATING',
    'IN_POSITION',
    'POST_TRADE_SWEEP',
    'COOLDOWN',
    'LOCKED_CIRCUIT_BREAKER',
    'EMERGENCY_HALT'
);

-- ----------------------------------------------------------------------------
-- 2. SIGNAUX DU MODÈLE LOCAL (LLM INFERENCE LOGS)
-- Append-only : converti en hypertable TimescaleDB
-- ----------------------------------------------------------------------------

CREATE TABLE llm_signals (
    id UUID DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    symbol VARCHAR(20) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    action signal_action NOT NULL,
    confidence NUMERIC(5, 4) NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    market_regime market_regime NOT NULL,
    thesis TEXT NOT NULL,
    prompt_payload JSONB NOT NULL,
    raw_response JSONB NOT NULL,
    inference_time_ms INTEGER NOT NULL CHECK (inference_time_ms >= 0),
    PRIMARY KEY (created_at, id)
);

SELECT create_hypertable('llm_signals', 'created_at', if_not_exists => TRUE);

-- ----------------------------------------------------------------------------
-- 3. AUDIT TRAIL RISK GATEWAY
-- Trace absolue de chaque évaluation (validée ou rejetée).
-- Hypertable TimescaleDB partitionnée par tranche temporelle.
-- ----------------------------------------------------------------------------

CREATE TABLE risk_evaluations (
    id UUID DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    signal_id UUID NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    approved BOOLEAN NOT NULL,
    rejection_code risk_rejection_code NOT NULL DEFAULT 'NONE',
    rejection_details TEXT,
    
    -- Métriques de marché observées au moment T
    market_price NUMERIC(28, 10) NOT NULL CHECK (market_price > 0),
    ema_200_4h NUMERIC(28, 10) NOT NULL CHECK (ema_200_4h > 0),
    atr_14 NUMERIC(28, 10) NOT NULL CHECK (atr_14 > 0),
    prev_candle_drop_pct NUMERIC(7, 4) NOT NULL DEFAULT 0.0,
    
    -- État du compte confronté
    account_equity_quote NUMERIC(28, 10) NOT NULL CHECK (account_equity_quote > 0),
    free_quote_balance NUMERIC(28, 10) NOT NULL CHECK (free_quote_balance >= 0),
    daily_drawdown_pct NUMERIC(6, 4) NOT NULL,
    consecutive_losses INTEGER NOT NULL CHECK (consecutive_losses >= 0),
    
    -- Dimensionnement calculé (NULL si rejeté avant calcul)
    computed_amount NUMERIC(28, 10),
    computed_stop_loss NUMERIC(28, 10),
    computed_risk_quote NUMERIC(28, 10),
    
    evaluation_time_ms INTEGER NOT NULL CHECK (evaluation_time_ms >= 0),
    PRIMARY KEY (created_at, id)
);

SELECT create_hypertable('risk_evaluations', 'created_at', if_not_exists => TRUE);

-- ----------------------------------------------------------------------------
-- 4. POSITIONS (LIFECYCLE OLTP)
-- Table relationnelle mutable stockant le cycle de vie d'une position.
-- ----------------------------------------------------------------------------

CREATE TABLE positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(20) NOT NULL,
    status position_status NOT NULL DEFAULT 'OPEN',
    
    opened_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    closed_at TIMESTAMPTZ,
    
    -- Tarifs et volumes
    entry_price NUMERIC(28, 10) NOT NULL CHECK (entry_price > 0),
    exit_price NUMERIC(28, 10) CHECK (exit_price > 0),
    initial_amount NUMERIC(28, 10) NOT NULL CHECK (initial_amount > 0),
    closed_amount NUMERIC(28, 10) DEFAULT 0 CHECK (closed_amount >= 0),
    
    -- Niveaux de risque
    stop_loss_price NUMERIC(28, 10) NOT NULL CHECK (stop_loss_price > 0),
    risk_amount_quote NUMERIC(28, 10) NOT NULL CHECK (risk_amount_quote > 0),
    
    -- Comptabilité nette finale
    gross_pnl_quote NUMERIC(28, 10) DEFAULT 0,
    total_fees_quote NUMERIC(28, 10) DEFAULT 0,
    net_pnl_quote NUMERIC(28, 10) DEFAULT 0,
    
    -- Flags d'écrémage
    is_profit_skimmed BOOLEAN NOT NULL DEFAULT FALSE,
    skimmed_amount_quote NUMERIC(28, 10) DEFAULT 0
);

-- ----------------------------------------------------------------------------
-- 5. ORDRES D'EXÉCUTION CCXT
-- Historique unitaire des ordres envoyés à l'exchange distant.
-- ----------------------------------------------------------------------------

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID REFERENCES positions(id) ON DELETE SET NULL,
    risk_evaluation_id UUID,
    
    exchange_order_id VARCHAR(100),
    client_order_id VARCHAR(100) NOT NULL UNIQUE,
    symbol VARCHAR(20) NOT NULL,
    
    side order_side NOT NULL,
    order_type order_type NOT NULL,
    status order_status NOT NULL DEFAULT 'PENDING',
    
    -- Quantités et prix demandés vs exécutés
    requested_amount NUMERIC(28, 10) NOT NULL CHECK (requested_amount > 0),
    filled_amount NUMERIC(28, 10) NOT NULL DEFAULT 0 CHECK (filled_amount >= 0),
    requested_price NUMERIC(28, 10),
    stop_price NUMERIC(28, 10),
    average_fill_price NUMERIC(28, 10) DEFAULT 0,
    
    -- Frais réels déduits par l'exchange
    fee_amount NUMERIC(28, 10) DEFAULT 0,
    fee_currency VARCHAR(10),
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    raw_exchange_payload JSONB
);

-- ----------------------------------------------------------------------------
-- 6. PROFIT SWEEPER LEDGER (RÉSERVE ET ÉCRÉMAGE)
-- Enregistrement comptable des 30% transférés hors du portefeuille Spot.
-- ----------------------------------------------------------------------------

CREATE TABLE profit_sweeps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    position_id UUID NOT NULL REFERENCES positions(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    
    quote_currency VARCHAR(10) NOT NULL DEFAULT 'USDT',
    realized_net_pnl NUMERIC(28, 10) NOT NULL CHECK (realized_net_pnl > 0),
    sweep_ratio NUMERIC(5, 4) NOT NULL DEFAULT 0.3000,
    swept_amount NUMERIC(28, 10) NOT NULL CHECK (swept_amount > 0),
    retained_trading_capital NUMERIC(28, 10) NOT NULL CHECK (retained_trading_capital > 0),
    
    from_account VARCHAR(30) NOT NULL DEFAULT 'spot',
    to_account VARCHAR(30) NOT NULL DEFAULT 'funding',
    transfer_transaction_id VARCHAR(120),
    transfer_status VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    error_message TEXT
);

-- ----------------------------------------------------------------------------
-- 7. ÉTATS SYSTÈME & CIRCUIT BREAKER AUDIT
-- Journalisation des blocages d'urgence et déverrouillages 2FA.
-- ----------------------------------------------------------------------------

CREATE TABLE system_state_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    previous_state system_state NOT NULL,
    current_state system_state NOT NULL,
    trigger_reason VARCHAR(150) NOT NULL,
    context_data JSONB,
    resolved_at TIMESTAMPTZ,
    resolved_by_admin VARCHAR(100)
);

-- ----------------------------------------------------------------------------
-- 8. INDEXATION STRATÉGIQUE POUR DASHBOARD ET PERFORMANCES
-- ----------------------------------------------------------------------------

-- Recherche rapide des positions actives
CREATE INDEX idx_positions_status_symbol ON positions (status, symbol);
CREATE INDEX idx_positions_opened_at ON positions (opened_at DESC);

-- Jointure rapide des ordres d'une position
CREATE INDEX idx_orders_position_id ON orders (position_id);
CREATE INDEX idx_orders_exchange_id ON orders (exchange_order_id);
CREATE INDEX idx_orders_status ON orders (status);

-- Recherche TimescaleDB optimisée sur l'audit trail
CREATE INDEX idx_risk_eval_symbol_time ON risk_evaluations (symbol, created_at DESC);
CREATE INDEX idx_risk_eval_approved ON risk_evaluations (approved, created_at DESC);
CREATE INDEX idx_llm_signals_action_time ON llm_signals (action, created_at DESC);

-- Ledger de profit
CREATE INDEX idx_profit_sweeps_position ON profit_sweeps (position_id);

-- ----------------------------------------------------------------------------
-- 9. VUES ANALYTIQUES POUR LE DASHBOARD FASTAPI / NEXT.JS
-- ----------------------------------------------------------------------------

-- Vue synthétique du PnL journalier et cumulé
CREATE OR REPLACE VIEW v_dashboard_pnl_metrics AS
SELECT
    COUNT(p.id) AS total_trades,
    COUNT(p.id) FILTER (WHERE p.net_pnl_quote > 0) AS winning_trades,
    COUNT(p.id) FILTER (WHERE p.net_pnl_quote <= 0) AS losing_trades,
    ROUND(
        (COUNT(p.id) FILTER (WHERE p.net_pnl_quote > 0)::NUMERIC / 
        NULLIF(COUNT(p.id), 0)::NUMERIC) * 100, 2
    ) AS winrate_pct,
    COALESCE(SUM(p.gross_pnl_quote), 0) AS total_gross_pnl,
    COALESCE(SUM(p.total_fees_quote), 0) AS total_fees_paid,
    COALESCE(SUM(p.net_pnl_quote), 0) AS total_net_pnl,
    COALESCE(SUM(p.skimmed_amount_quote), 0) AS total_vault_reserved
FROM positions p
WHERE p.status = 'CLOSED';

-- Vue du taux de rejet de la Risk Gateway
CREATE OR REPLACE VIEW v_risk_rejection_stats AS
SELECT
    rejection_code,
    COUNT(*) AS occurrences,
    ROUND((COUNT(*)::NUMERIC / SUM(COUNT(*)) OVER ()) * 100, 2) AS percentage_of_total
FROM risk_evaluations
WHERE approved = FALSE
GROUP BY rejection_code
ORDER BY occurrences DESC;
Points techniques clés du schémaPrécision Crypto (NUMERIC(28, 10)) : Aucune utilisation du type FLOAT ou REAL. La manipulation monétaire garantit une exactitude arithmétique absolue jusqu'à la 10e décimale, couvrant les satoshis et les micro-frais des exchanges.Architecture Hybride TimescaleDB + Relationnelle :llm_signals et risk_evaluations sont des Hypertables append-only indexées sur created_at. Elles absorbent sans latence un flux continu d'inférences et de rejets sans dégrader les perfs de la base.positions, orders et profit_sweeps restent des tables relationnelles strictes avec contraintes d'intégrité référentielle (FOREIGN KEY), garantissant la cohérence comptable des ordres exécutés.Traçabilité End-to-End : Un ordre exécuté remonte directement à la décision du modèle :$$\text{orders} \xrightarrow{\text{fk}} \text{positions} \xleftarrow{\text{ref}} \text{risk\_evaluations} \xrightarrow{\text{fk}} \text{llm\_signals}$$Ledger d'Écrémage Dédié (profit_sweeps) : Chaque dollar transféré vers le compte réserve/funding dispose de son identifiant de virement exchange et de sa date UTC, simplifiant la justification comptable et le calcul des plus-values.
