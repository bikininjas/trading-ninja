-- ============================================================================
-- 0002 — Fermeture du schéma côté API publique (Supabase / PostgREST)
--
-- Constat mesuré sur la base :
--   * les TABLES sont protégées : RLS activée sans aucune policy => 0 ligne
--     visible pour `anon`. C'est le bon comportement.
--   * les VUES ne le sont PAS : une vue s'exécute par défaut avec les droits
--     de son propriétaire (ici `postgres`), donc elle contourne la RLS et
--     `select count(*) from v_system_health` renvoyait 1 ligne à `anon`.
--
-- Ces tables ne sont destinées qu'au backend (rôle `postgres`), jamais au
-- frontend. On coupe donc complètement l'accès aux rôles PostgREST et on
-- repasse les vues en `security_invoker` pour que la RLS s'applique même si
-- un GRANT revenait un jour.
-- ============================================================================

-- 1. Les vues respectent les droits et la RLS de l'appelant (PG 15+).
DO $$
DECLARE
    v TEXT;
BEGIN
    FOR v IN SELECT viewname FROM pg_views WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER VIEW public.%I SET (security_invoker = true)', v);
    END LOOP;
END
$$;

-- 2. Révocation totale des rôles exposés par l'API publique.
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;

-- 3. Les objets créés plus tard ne seront plus accordés automatiquement.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

-- 4. Ceinture et bretelles : RLS activée partout, y compris sur les futures
--    partitions (les tables partitionnées héritent de la RLS du parent).
DO $$
DECLARE
    t TEXT;
BEGIN
    FOR t IN
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END
$$;

COMMENT ON SCHEMA public IS
    'Réservé au backend (rôle postgres). Accès anon/authenticated révoqué — voir migration 0002.';
