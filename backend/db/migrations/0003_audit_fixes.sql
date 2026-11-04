-- ============================================================================
-- 0003 — Corrections issues d'un audit à froid
--
-- Trois défauts qui n'apparaissaient pas à la lecture :
--
-- 1. `risk_evaluations` imposait `market_price > 0`, `ema_200_h4 > 0`, etc. en
--    NOT NULL. Or un refus précoce (`MALFORMED_INTENT`, `INVALID_MARKET_DATA`) se
--    produit précisément quand ces valeurs sont absentes ou invalides : il était
--    donc impossible de journaliser honnêtement ces refus, alors que la phase 2
--    exige de tracer TOUTES les décisions. Les colonnes deviennent nullables, et
--    une contrainte impose leur présence seulement quand la décision est acceptée.
--
-- 2. `bot_state` était un singleton unique tous modes confondus. Passer de PAPER à
--    LIVE héritait du plus-haut d'équité et des verrous de la simulation — un HWM
--    paper de 1000 USDT face à un compte réel de 100 USDT produit un drawdown de
--    -90 %, donc un verrou permanent. L'état devient une ligne PAR MODE.
--
-- 3. `v_system_health` comptait les ordres et évaluations de tous les modes sans
--    filtre, et s'appuyait sur `bot_state.id`, qui disparaît.
--
-- Plus : ajout du code de rejet `INTERNAL_ERROR`, qui rend vraie la promesse du
-- pare-feu de ne jamais lever d'exception.
-- ============================================================================

-- 1. Nouveau code de rejet --------------------------------------------------
-- Ajouté seul : la valeur ne peut pas être utilisée dans la même transaction.
ALTER TYPE risk_rejection_code ADD VALUE IF NOT EXISTS 'INTERNAL_ERROR';

-- 2. Contexte de marché nullable pour les refus précoces ---------------------
ALTER TABLE risk_evaluations ALTER COLUMN market_price          DROP NOT NULL;
ALTER TABLE risk_evaluations ALTER COLUMN ema_200_h4            DROP NOT NULL;
ALTER TABLE risk_evaluations ALTER COLUMN atr_14_h4             DROP NOT NULL;
ALTER TABLE risk_evaluations ALTER COLUMN atr_pct_of_price      DROP NOT NULL;
ALTER TABLE risk_evaluations ALTER COLUMN account_equity_quote  DROP NOT NULL;
ALTER TABLE risk_evaluations ALTER COLUMN free_quote_balance    DROP NOT NULL;

-- Une décision ACCEPTÉE doit être documentée ; un refus peut ne pas l'être.
ALTER TABLE risk_evaluations
    ADD CONSTRAINT chk_risk_eval_context_when_approved CHECK (
        approved = FALSE
        OR (
            market_price IS NOT NULL
            AND ema_200_h4 IS NOT NULL
            AND atr_14_h4 IS NOT NULL
            AND account_equity_quote IS NOT NULL
            AND free_quote_balance IS NOT NULL
        )
    );

-- 3. État du bot par mode ---------------------------------------------------
DROP VIEW IF EXISTS v_system_health;

ALTER TABLE bot_state DROP CONSTRAINT IF EXISTS bot_state_id_check;
ALTER TABLE bot_state DROP CONSTRAINT IF EXISTS bot_state_pkey;
ALTER TABLE bot_state DROP COLUMN IF EXISTS id;
ALTER TABLE bot_state ADD PRIMARY KEY (mode);

INSERT INTO bot_state (mode) VALUES ('TESTNET'), ('LIVE') ON CONFLICT DO NOTHING;

CREATE VIEW v_system_health AS
SELECT
    b.state,
    b.mode,
    b.since,
    b.cooldown_until,
    b.lock_reason,
    b.consecutive_losses,
    b.equity_hwm_quote,
    b.updated_at,
    (SELECT COUNT(*) FROM positions p
      WHERE p.status = 'OPEN' AND p.mode = b.mode)                                     AS open_positions,
    (SELECT COUNT(*) FROM orders o
      WHERE o.mode = b.mode AND o.created_at > CLOCK_TIMESTAMP() - INTERVAL '1 hour')   AS orders_last_hour,
    (SELECT COUNT(*) FROM risk_evaluations r
      WHERE r.mode = b.mode AND r.created_at > CLOCK_TIMESTAMP() - INTERVAL '24 hours') AS evaluations_24h,
    (SELECT COUNT(*) FROM risk_evaluations r
      WHERE r.approved AND r.mode = b.mode
        AND r.created_at > CLOCK_TIMESTAMP() - INTERVAL '24 hours')                     AS approvals_24h
FROM bot_state b;

COMMENT ON VIEW v_system_health IS
    'Une ligne par mode d''exécution (PAPER / TESTNET / LIVE), sans mélange de compteurs.';
