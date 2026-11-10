-- Groupes de services privés. Aucun LOGIN, secret, ordre ou droit LIVE.
CREATE ROLE tn_paper_engine NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tn_paper_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tn_paper_observer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tn_paper_probe NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT tn_paper_engine, tn_paper_api, tn_paper_observer, tn_paper_probe TO postgres;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO tn_paper_engine, tn_paper_api, tn_paper_observer, tn_paper_probe;

-- Le moteur et l'API administrent exclusivement le ledger PAPER.
DO $$ DECLARE name text; BEGIN
  FOREACH name IN ARRAY ARRAY[
    'bot_state','positions','orders','equity_snapshots','llm_signals',
    'risk_evaluations','profit_sweeps','system_state_logs','runtime_settings',
    'shadow_trades','lessons','capital_accounts','capital_events','pending_exits',
    'execution_requests','entry_controls','decision_cycles','opportunity_events',
    'observation_experiments'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', name);
    EXECUTE format('CREATE POLICY tn_paper_runtime ON public.%I TO tn_paper_engine,tn_paper_api USING (mode=''PAPER''::public.execution_mode) WITH CHECK (mode=''PAPER''::public.execution_mode)', name);
    EXECUTE format('GRANT SELECT,INSERT ON public.%I TO tn_paper_engine,tn_paper_api', name);
  END LOOP;
END $$;
GRANT UPDATE ON bot_state,positions,orders,runtime_settings,shadow_trades,lessons,
  capital_accounts,pending_exits,execution_requests,entry_controls,decision_cycles,
  observation_experiments TO tn_paper_engine,tn_paper_api;
GRANT DELETE ON pending_exits TO tn_paper_engine,tn_paper_api;

-- Objets sans mode : contenus immuables, pas d'UPDATE/DELETE des artefacts.
CREATE POLICY tn_paper_policy_read ON position_policies FOR SELECT TO tn_paper_engine,tn_paper_api
  USING (EXISTS (SELECT 1 FROM positions WHERE policy_id=position_policies.id AND mode='PAPER'));
CREATE POLICY tn_paper_policy_insert ON position_policies FOR INSERT TO tn_paper_engine,tn_paper_api WITH CHECK (true);
GRANT SELECT,INSERT ON position_policies TO tn_paper_engine,tn_paper_api;
CREATE POLICY tn_paper_cycle_artifacts ON decision_cycle_artifacts TO tn_paper_engine,tn_paper_api
  USING (EXISTS (SELECT 1 FROM decision_cycles WHERE id=cycle_id AND mode='PAPER'))
  WITH CHECK (EXISTS (SELECT 1 FROM decision_cycles WHERE id=cycle_id AND mode='PAPER'));
GRANT SELECT,INSERT ON decision_cycle_artifacts TO tn_paper_engine,tn_paper_api;
CREATE POLICY tn_paper_artifact_read ON decision_artifacts FOR SELECT TO tn_paper_engine,tn_paper_api
  USING (EXISTS (SELECT 1 FROM decision_cycle_artifacts WHERE digest=decision_artifacts.digest));
CREATE POLICY tn_paper_artifact_insert ON decision_artifacts FOR INSERT TO tn_paper_engine,tn_paper_api WITH CHECK (true);
GRANT SELECT,INSERT ON decision_artifacts TO tn_paper_engine,tn_paper_api;

-- Le surveillant peut constater et arrêter, jamais écrire un fill ni reprendre.
GRANT SELECT ON bot_state,positions,decision_cycles TO tn_paper_observer;
CREATE POLICY tn_paper_observer_read ON bot_state FOR SELECT TO tn_paper_observer USING (mode='PAPER');
CREATE POLICY tn_paper_observer_read ON positions FOR SELECT TO tn_paper_observer USING (mode='PAPER');
CREATE POLICY tn_paper_observer_read ON decision_cycles FOR SELECT TO tn_paper_observer USING (mode='PAPER');
GRANT UPDATE(state,since,locked_at,lock_reason,lock_details,cooldown_until,updated_at) ON bot_state TO tn_paper_observer;
CREATE POLICY tn_paper_observer_halt ON bot_state FOR UPDATE TO tn_paper_observer
  USING (mode='PAPER') WITH CHECK (mode='PAPER' AND state='EMERGENCY_HALT' AND cooldown_until IS NULL);
GRANT INSERT ON system_state_logs TO tn_paper_observer;
CREATE POLICY tn_paper_observer_log ON system_state_logs FOR INSERT TO tn_paper_observer
  WITH CHECK (mode='PAPER' AND current_state='EMERGENCY_HALT');

GRANT SELECT,INSERT,UPDATE ON protection_observations,protection_incidents TO tn_paper_observer;
CREATE POLICY tn_paper_observer_write ON protection_observations TO tn_paper_observer USING (mode='PAPER') WITH CHECK (mode='PAPER');
CREATE POLICY tn_paper_observer_write ON protection_incidents TO tn_paper_observer USING (mode='PAPER') WITH CHECK (mode='PAPER');
GRANT SELECT,INSERT ON observation_commands TO tn_paper_observer;
CREATE POLICY tn_paper_observer_commands ON observation_commands TO tn_paper_observer
  USING (mode='PAPER') WITH CHECK (mode='PAPER' AND EXISTS(
    SELECT 1 FROM protection_incidents WHERE id=incident_id AND mode='PAPER'));

GRANT SELECT ON protection_observations,protection_incidents,observation_commands TO tn_paper_engine,tn_paper_api;
CREATE POLICY tn_paper_runtime_read ON protection_observations FOR SELECT TO tn_paper_engine,tn_paper_api USING (mode='PAPER');
CREATE POLICY tn_paper_runtime_read ON protection_incidents FOR SELECT TO tn_paper_engine,tn_paper_api USING (mode='PAPER');
CREATE POLICY tn_paper_runtime_read ON observation_commands FOR SELECT TO tn_paper_engine,tn_paper_api USING (mode='PAPER');
GRANT UPDATE(applied_at) ON observation_commands TO tn_paper_engine,tn_paper_api;
CREATE POLICY tn_paper_runtime_consume ON observation_commands FOR UPDATE TO tn_paper_engine,tn_paper_api USING (mode='PAPER') WITH CHECK (mode='PAPER');
GRANT UPDATE(acknowledged_at,acknowledged_by) ON protection_incidents TO tn_paper_api;
CREATE POLICY tn_paper_api_ack ON protection_incidents FOR UPDATE TO tn_paper_api USING (mode='PAPER') WITH CHECK (mode='PAPER');

-- La sonde n'a que les quatre lectures nécessaires et aucune mutation.
GRANT SELECT ON bot_state,protection_observations,decision_cycles TO tn_paper_probe;
CREATE POLICY tn_paper_probe_read ON bot_state FOR SELECT TO tn_paper_probe USING (mode='PAPER');
CREATE POLICY tn_paper_probe_read ON protection_observations FOR SELECT TO tn_paper_probe USING (mode='PAPER');
CREATE POLICY tn_paper_probe_read ON decision_cycles FOR SELECT TO tn_paper_probe USING (mode='PAPER');
GRANT SELECT ON schema_migrations TO tn_paper_engine,tn_paper_api,tn_paper_observer,tn_paper_probe;
CREATE POLICY tn_paper_migration_read ON schema_migrations FOR SELECT TO tn_paper_engine,tn_paper_api,tn_paper_observer,tn_paper_probe USING (true);
GRANT SELECT ON market_candles_history TO tn_paper_engine,tn_paper_api;
CREATE POLICY tn_paper_market_read ON market_candles_history FOR SELECT TO tn_paper_engine,tn_paper_api USING (true);

-- Index utiles aux jointures de contenu et à l'isolation des politiques.
CREATE INDEX decision_cycle_artifacts_digest ON decision_cycle_artifacts(digest);
CREATE INDEX positions_policy_id ON positions(policy_id) WHERE policy_id IS NOT NULL;
COMMENT ON SCHEMA public IS 'Backend privé : groupes PAPER sans LOGIN et RLS par mode ; anon/authenticated sans droits.';
