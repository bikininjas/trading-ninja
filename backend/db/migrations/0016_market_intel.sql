-- Veille publique indépendante : aucun ordre ni droit financier.
ALTER TYPE risk_rejection_code ADD VALUE 'REJECTED_INTEL_SYSTEMIC_RISK';

CREATE TABLE market_intel_digests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observed_at TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL CHECK (valid_until > observed_at),
    status TEXT NOT NULL CHECK (status IN ('OK', 'DEGRADED')),
    overall_sentiment TEXT NOT NULL CHECK (overall_sentiment IN ('BULLISH','NEUTRAL','BEARISH','CRISIS')),
    sentiment_score NUMERIC(5,4) NOT NULL CHECK (sentiment_score BETWEEN -1 AND 1),
    systemic_risk_detected BOOLEAN NOT NULL,
    affected_assets TEXT[] NOT NULL CHECK (cardinality(affected_assets) <= 30),
    catalyst_summary VARCHAR(240) NOT NULL,
    model TEXT NOT NULL,
    source_hashes TEXT[] NOT NULL CHECK (cardinality(source_hashes) <= 80),
    CHECK (status = 'OK' OR NOT systemic_risk_detected)
);
CREATE INDEX market_intel_latest ON market_intel_digests (observed_at DESC);
ALTER TABLE market_intel_digests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON market_intel_digests FROM PUBLIC, anon, authenticated;

CREATE ROLE tn_market_intel_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT tn_market_intel_writer TO postgres;
GRANT USAGE ON SCHEMA public TO tn_market_intel_writer;
GRANT SELECT, INSERT, DELETE ON market_intel_digests TO tn_market_intel_writer;
CREATE POLICY intel_writer ON market_intel_digests TO tn_market_intel_writer USING (true) WITH CHECK (true);
GRANT SELECT ON market_intel_digests TO tn_paper_engine, tn_paper_api;
CREATE POLICY intel_private_read ON market_intel_digests FOR SELECT TO tn_paper_engine, tn_paper_api USING (true);
