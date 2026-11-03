"""Durable visual-enrichment state for perceptual expression assets."""

MIGRATION_V107 = (
    17,
    '''
    ALTER TABLE perceptual_expression_assets ADD COLUMN content_fingerprint TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN content_mime TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN content_size_bytes INTEGER
        CHECK(content_size_bytes IS NULL OR content_size_bytes >= 0);
    ALTER TABLE perceptual_expression_assets ADD COLUMN last_visual_analysis_content_fingerprint TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_provider TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_model TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_claim_token TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_claimed_until TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_attempt_count INTEGER NOT NULL DEFAULT 0
        CHECK(visual_attempt_count >= 0);
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_last_error_code TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN visual_defer_until TEXT;
    ALTER TABLE perceptual_expression_assets ADD COLUMN remote_processing_allowed INTEGER NOT NULL DEFAULT 0
        CHECK(remote_processing_allowed IN (0, 1));
    ALTER TABLE perceptual_expression_assets ADD COLUMN privacy_scope TEXT NOT NULL DEFAULT 'PERSONAL';

    CREATE INDEX IF NOT EXISTS idx_perceptual_expression_visual_claim
        ON perceptual_expression_assets(
            semantic_status,
            visual_defer_until,
            visual_claimed_until,
            last_seen_at DESC
        );
    ''',
)

LATEST_SCHEMA_VERSION = 17
