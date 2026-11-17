-- Sources causales de chaque digest, privées sous les droits/RLS de 0016.
ALTER TABLE market_intel_digests
    ADD COLUMN source_items JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(source_items) = 'array'
               AND jsonb_array_length(source_items) <= 80
               AND octet_length(source_items::text) <= 1000000),
    ADD COLUMN fear_and_greed_value SMALLINT CHECK (fear_and_greed_value BETWEEN 0 AND 100),
    ADD COLUMN fear_and_greed_at TIMESTAMPTZ,
    ADD COLUMN analysis_response TEXT CHECK (octet_length(analysis_response) <= 4096),
    ADD COLUMN analysis_error TEXT CHECK (analysis_error IN
        ('SOURCES_UNAVAILABLE','MODEL_UNAVAILABLE','INVALID_RESPONSE','UNKNOWN_ASSET')),
    ADD CONSTRAINT market_intel_fng_pair CHECK
        ((fear_and_greed_value IS NULL) = (fear_and_greed_at IS NULL));
