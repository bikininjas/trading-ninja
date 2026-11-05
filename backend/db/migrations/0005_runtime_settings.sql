-- Réglages runtime pilotés depuis le dashboard, stockés en base.
--
-- Une ligne par mode : le bot papier et le futur bot réel ne partagent pas leurs
-- réglages. Le payload est un JSONB dont seules les clés de `RUNTIME_ALLOWED_KEYS`
-- (côté Python) sont lues : il ne peut pas changer le mode, l'exchange ni les
-- secrets.
--
-- Les lignes commencent vides : la configuration `.env` et le preset restent la
-- base, et le dashboard n'écrit que ce qu'il modifie.

CREATE TABLE IF NOT EXISTS runtime_settings (
    mode        execution_mode PRIMARY KEY,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    updated_by  TEXT NOT NULL DEFAULT 'bootstrap'
);

INSERT INTO runtime_settings (mode) VALUES
    ('PAPER'), ('TESTNET'), ('LIVE')
ON CONFLICT (mode) DO NOTHING;
