-- D-070 : enveloppes virtuelles et clôtures idempotentes. Migration additive.
-- Convention de nommage du coureur de migrations du dépôt (AGENTS.md §3).
CREATE TABLE capital_accounts (
    mode execution_mode PRIMARY KEY,
    quote_currency TEXT NOT NULL,
    initial_quote NUMERIC(28,10) NOT NULL CHECK (initial_quote > 0),
    active_quote NUMERIC(28,10) NOT NULL CHECK (active_quote >= 0),
    reserve_quote NUMERIC(28,10) NOT NULL DEFAULT 0 CHECK (reserve_quote >= 0),
    growth_quote NUMERIC(28,10) NOT NULL DEFAULT 0 CHECK (growth_quote >= 0),
    realized_pnl NUMERIC(28,10) NOT NULL DEFAULT 0,
    surplus_hwm NUMERIC(28,10) NOT NULL DEFAULT 0 CHECK (surplus_hwm >= 0),
    last_promotion_at TIMESTAMPTZ NOT NULL,
    promotions INTEGER NOT NULL DEFAULT 0 CHECK (promotions >= 0),
    CHECK (active_quote + reserve_quote + growth_quote = initial_quote + realized_pnl)
);
CREATE TABLE capital_events (
    position_id UUID PRIMARY KEY REFERENCES positions(id),
    mode execution_mode NOT NULL REFERENCES capital_accounts(mode),
    closed_at TIMESTAMPTZ NOT NULL,
    pnl_quote NUMERIC(28,10) NOT NULL,
    active_delta NUMERIC(28,10) NOT NULL,
    reserve_delta NUMERIC(28,10) NOT NULL CHECK (reserve_delta >= 0),
    growth_delta NUMERIC(28,10) NOT NULL,
    promoted_quote NUMERIC(28,10) NOT NULL CHECK (promoted_quote >= 0),
    CHECK (active_delta + reserve_delta + growth_delta = pnl_quote)
);
CREATE INDEX capital_events_mode_closed ON capital_events(mode, closed_at DESC);
-- Intent persisté avant une vente réelle ; résultat rempli avant la comptabilité.
-- Une intention sans résultat certain bloque toute nouvelle vente automatique.
CREATE TABLE pending_exits (
    position_id UUID PRIMARY KEY REFERENCES positions(id),
    mode execution_mode NOT NULL,
    reason TEXT NOT NULL,
    purpose TEXT NOT NULL,
    halt BOOLEAN NOT NULL,
    fill JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP()
);
ALTER TABLE positions ADD COLUMN monitor_cursor_at TIMESTAMPTZ;
-- Les positions historiques sans curseur ne doivent pas être rejouées avec un
-- stop dont la date d'effet est inconnue. Le runtime exige leur réconciliation.
ALTER TABLE capital_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE capital_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_exits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON capital_accounts, capital_events, pending_exits FROM PUBLIC, anon, authenticated;
