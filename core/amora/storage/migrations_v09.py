MIGRATION_V09 = (
    9,
    """
    CREATE TABLE IF NOT EXISTS avatar_projects (
        id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        project_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS avatar_assets (
        id TEXT PRIMARY KEY,
        content_type TEXT NOT NULL,
        file_name TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_avatar_assets_updated
        ON avatar_assets(updated_at DESC);
    """,
)
