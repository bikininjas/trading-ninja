-- Mémoire d'expérience PAPER ; aucun portefeuille ni droit d'exécution ajouté.
ALTER TABLE shadow_trades ADD COLUMN simulation_context JSONB NOT NULL DEFAULT '{}';
ALTER TABLE shadow_trades ADD CONSTRAINT shadow_simulation_context_object
    CHECK (jsonb_typeof(simulation_context) = 'object');

CREATE TABLE learning_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mode execution_mode NOT NULL DEFAULT 'PAPER' CHECK (mode = 'PAPER'),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','WAITING','LEARNED','FAILED')),
    reason TEXT NOT NULL DEFAULT '',
    evaluated_count INTEGER NOT NULL DEFAULT 0 CHECK (evaluated_count >= 0),
    source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
    new_source_count INTEGER NOT NULL DEFAULT 0 CHECK (new_source_count >= 0),
    lesson_id UUID REFERENCES lessons(id),
    model_name TEXT NOT NULL,
    policy JSONB NOT NULL CHECK (jsonb_typeof(policy) = 'object')
);
CREATE INDEX learning_runs_latest ON learning_runs (mode, started_at DESC);
CREATE INDEX shadow_training_pending ON shadow_trades (mode, evaluated_at, created_at)
    WHERE plan_status='OPEN' AND simulation_context->>'version'='forward-v1';
ALTER TABLE learning_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON learning_runs FROM PUBLIC, anon, authenticated;

CREATE ROLE tn_paper_learner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT tn_paper_learner TO postgres;
GRANT USAGE ON SCHEMA public TO tn_paper_learner;
GRANT SELECT ON schema_migrations, bot_state, positions, llm_signals, shadow_trades, lessons, learning_runs TO tn_paper_learner;
GRANT INSERT ON lessons, learning_runs TO tn_paper_learner;
GRANT UPDATE(active) ON lessons TO tn_paper_learner;
GRANT UPDATE(plan_status, exit_price, pnl_quote, outcome_reason, evaluated_at, simulation_context) ON shadow_trades TO tn_paper_learner;
GRANT UPDATE(finished_at,status,reason,evaluated_count,source_count,new_source_count,lesson_id) ON learning_runs TO tn_paper_learner;
DO $$ DECLARE name text; BEGIN
  FOREACH name IN ARRAY ARRAY['bot_state','positions','llm_signals','shadow_trades','lessons'] LOOP
    EXECUTE format('CREATE POLICY tn_learning_read ON %I FOR SELECT TO tn_paper_learner USING (mode=''PAPER'')', name);
  END LOOP;
END $$;
CREATE POLICY tn_learning_migrations ON schema_migrations FOR SELECT TO tn_paper_learner USING (true);
CREATE POLICY tn_learning_lessons_insert ON lessons FOR INSERT TO tn_paper_learner WITH CHECK (mode='PAPER');
CREATE POLICY tn_learning_lessons_update ON lessons FOR UPDATE TO tn_paper_learner USING (mode='PAPER') WITH CHECK (mode='PAPER');
CREATE POLICY tn_learning_shadow_update ON shadow_trades FOR UPDATE TO tn_paper_learner USING (mode='PAPER') WITH CHECK (mode='PAPER');
CREATE POLICY tn_learning_runs ON learning_runs TO tn_paper_learner USING (mode='PAPER') WITH CHECK (mode='PAPER');
GRANT SELECT ON learning_runs TO tn_paper_api;
CREATE POLICY tn_learning_api_read ON learning_runs FOR SELECT TO tn_paper_api USING (mode='PAPER');
