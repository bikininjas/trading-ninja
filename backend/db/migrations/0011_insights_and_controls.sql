-- D-073 : observation privée, politiques et contrôle des entrées. Aucun ordre ajouté.
CREATE TABLE position_policies (
    id TEXT PRIMARY KEY CHECK (length(id)=64),
    body JSONB NOT NULL CHECK (jsonb_typeof(body)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP()
);
ALTER TABLE positions ADD COLUMN policy_id TEXT REFERENCES position_policies(id);
CREATE TABLE entry_controls (
    mode execution_mode PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision>=0),
    reason TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP()
);
INSERT INTO entry_controls(mode) SELECT unnest(enum_range(NULL::execution_mode));
CREATE TABLE decision_cycles (
    id UUID PRIMARY KEY,
    mode execution_mode NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','COMPLETE','FAILED','INCOMPLETE')),
    manifest JSONB NOT NULL,
    trace JSONB NOT NULL DEFAULT '[]',
    report JSONB,
    digest TEXT,
    error TEXT,
    CHECK (octet_length(trace::text)<=8388608)
);
CREATE INDEX decision_cycles_mode_time ON decision_cycles(mode,started_at DESC,id);
CREATE TABLE decision_artifacts (
    digest TEXT PRIMARY KEY CHECK(length(digest)=64),
    payload JSONB NOT NULL,
    CHECK(octet_length(payload::text)<=8388608)
);
CREATE TABLE decision_cycle_artifacts (
    cycle_id UUID NOT NULL REFERENCES decision_cycles(id),
    digest TEXT NOT NULL REFERENCES decision_artifacts(digest),
    PRIMARY KEY(cycle_id,digest)
);
CREATE TABLE opportunity_events (
    cycle_id UUID NOT NULL REFERENCES decision_cycles(id),
    seq INTEGER NOT NULL,
    mode execution_mode NOT NULL,
    opportunity_id TEXT,
    symbol TEXT,
    stage TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    terminal BOOLEAN NOT NULL DEFAULT FALSE,
    body JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY(cycle_id,seq)
);
CREATE INDEX opportunity_events_mode_time ON opportunity_events(mode,observed_at DESC);
CREATE INDEX opportunity_events_opportunity ON opportunity_events(mode,opportunity_id) WHERE opportunity_id IS NOT NULL;
CREATE TABLE observation_experiments (
    id UUID PRIMARY KEY,
    mode execution_mode NOT NULL,
    manifest JSONB NOT NULL,
    manifest_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('RUNNING','COMPLETE','FAILED')),
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    finished_at TIMESTAMPTZ,
    CHECK(octet_length(manifest::text)<=1048576),
    CHECK(result IS NULL OR octet_length(result::text)<=8388608)
);
CREATE INDEX observation_experiments_mode_time ON observation_experiments(mode,created_at DESC);
CREATE TABLE protection_observations (
    mode execution_mode PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL,
    body JSONB NOT NULL
);
CREATE TABLE protection_incidents (
    id UUID PRIMARY KEY,
    mode execution_mode NOT NULL,
    incident_key TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('WARNING','CRITICAL')),
    evidence JSONB NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by TEXT,
    resolved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX protection_incident_active ON protection_incidents(mode,incident_key) WHERE resolved_at IS NULL;
CREATE INDEX protection_incidents_mode_time ON protection_incidents(mode,opened_at DESC);
CREATE TABLE observation_commands (
    id UUID PRIMARY KEY,
    mode execution_mode NOT NULL,
    incident_id UUID NOT NULL REFERENCES protection_incidents(id),
    kind TEXT NOT NULL CHECK(kind='HALT'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    applied_at TIMESTAMPTZ,
    UNIQUE(incident_id,kind)
);
CREATE INDEX observation_commands_pending ON observation_commands(mode,created_at) WHERE applied_at IS NULL;
ALTER TABLE runtime_settings ADD COLUMN revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_settings ADD COLUMN applied_revision BIGINT;
ALTER TABLE runtime_settings ADD COLUMN applied_payload JSONB;
ALTER TABLE runtime_settings ADD COLUMN applied_at TIMESTAMPTZ;
ALTER TABLE runtime_settings ADD COLUMN apply_error TEXT;
ALTER TABLE position_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE entry_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE opportunity_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE observation_experiments ENABLE ROW LEVEL SECURITY;
ALTER TABLE protection_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE protection_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE observation_commands ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON position_policies,entry_controls,decision_cycles,opportunity_events,
 observation_experiments,protection_observations,protection_incidents,observation_commands FROM anon,authenticated;
ALTER TABLE decision_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_cycle_artifacts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON decision_artifacts,decision_cycle_artifacts FROM anon,authenticated;
CREATE INDEX positions_mode_entry_signal ON positions(mode,entry_signal_id) WHERE entry_signal_id IS NOT NULL;
