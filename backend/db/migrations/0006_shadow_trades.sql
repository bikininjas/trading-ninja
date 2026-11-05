-- Shadow trades : ce qu'aurait donné un achat que le bot n'a pas pris.
--
-- Deux cas sont enregistrés :
--   * GATE_REJECTED_BUY : le modèle a dit BUY, le pare-feu a refusé ;
--   * MODEL_HOLD        : le modèle a dit HOLD alors que la règle d'entrée avait
--                         déclenché (setup valide), donc le pare-feu n'a jamais été
--                         appelé.
--
-- Le plan (taille, stop, take-profit) est recalculé avec une confiance forcée à 1,0
-- et le régime du signal, dans le même pare-feu. C'est une simulation : elle
-- n'engage rien et ne crée aucun ordre. Si le plan forcé est encore refusé
-- (contre-tendance, volatilité, minimum exchange), la ligne reste `NO_PLAN` : on
-- mesure le refus, pas un trade imaginaire.
--
-- Les lignes `OPEN` sont évaluées plus tard par `scripts/shadow_report.py`, qui
-- rejoue les bougies H4 suivantes et écrit le résultat (STOP_LOSS, TAKE_PROFIT,
-- END_OF_DATA).

CREATE TABLE IF NOT EXISTS shadow_trades (
    id                  UUID NOT NULL DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                execution_mode NOT NULL DEFAULT 'PAPER',
    symbol              VARCHAR(30) NOT NULL,

    kind                VARCHAR(24) NOT NULL
                        CHECK (kind IN ('GATE_REJECTED_BUY', 'MODEL_HOLD')),
    action              signal_action NOT NULL,

    signal_id           UUID NOT NULL,
    evaluation_id       UUID,
    rejection_code      risk_rejection_code NOT NULL DEFAULT 'NONE',
    rejection_details   TEXT,

    -- Prix observé au moment de la décision et plan contrefactuel.
    decision_price      NUMERIC(28, 10) NOT NULL CHECK (decision_price > 0),
    amount              NUMERIC(28, 10),
    notional_quote      NUMERIC(28, 10),
    stop_loss_price     NUMERIC(28, 10),
    take_profit_price   NUMERIC(28, 10),
    risk_amount_quote   NUMERIC(28, 10),

    -- NO_PLAN : le pare-feu refuserait aussi le plan forcé.
    -- OPEN : un plan finançable existe, en attente de bougies futures.
    plan_status         VARCHAR(16) NOT NULL DEFAULT 'NO_PLAN'
                        CHECK (plan_status IN (
                            'OPEN', 'NO_PLAN', 'STOP_LOSS', 'TAKE_PROFIT', 'END_OF_DATA'
                        )),

    evaluated_at        TIMESTAMPTZ,
    exit_price          NUMERIC(28, 10),
    pnl_quote           NUMERIC(28, 10),
    outcome_reason      TEXT,

    PRIMARY KEY (created_at, id)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS shadow_trades_default PARTITION OF shadow_trades DEFAULT;

CREATE INDEX IF NOT EXISTS idx_shadow_status_time
    ON shadow_trades (plan_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_symbol_time
    ON shadow_trades (symbol, mode, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_mode_time
    ON shadow_trades (mode, created_at DESC);
