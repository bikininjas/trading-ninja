-- Mémoire de leçons : quelques observations courtes, générées à partir des
-- résultats réels (trades clos + shadow trades), injectées dans le prompt.
--
-- Une seule version active par mode (index partiel). Une nouvelle relecture
-- désactive l'ancienne dans la même transaction. La table est volontairement
-- petite et non partitionnée : quelques lignes par semaine, pas de télémétrie.
--
-- Les leçons sont du **contexte**, jamais des ordres : le code ne lit que
-- `action`, `confidence`, `market_regime` et `thesis` dans la réponse du modèle.
-- Aucune leçon ne peut modifier la taille, le prix ou le stop.

CREATE TABLE IF NOT EXISTS lessons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT CLOCK_TIMESTAMP(),
    mode                execution_mode NOT NULL,
    version             INTEGER NOT NULL CHECK (version > 0),

    lessons             TEXT[] NOT NULL CHECK (cardinality(lessons) BETWEEN 1 AND 5),
    source_trade_ids    UUID[] NOT NULL DEFAULT '{}',
    source_count        INTEGER NOT NULL CHECK (source_count >= 0),

    model_name          VARCHAR(120) NOT NULL,
    prompt_version      VARCHAR(40) NOT NULL,
    valid_until         TIMESTAMPTZ,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    review_sha256       CHAR(64),

    UNIQUE (mode, version)
);

-- PostgreSQL ne peut pas imposer cette unicité sur une table partitionnée ;
-- `lessons` est petite, elle reste une table simple.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_lessons_per_mode
    ON lessons (mode) WHERE active;
