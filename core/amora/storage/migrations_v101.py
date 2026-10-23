"""Additive infrastructure migration; existing asset metadata is unchanged.

Existing file assets must be explicitly imported before switching to SQL blobs.
No inference that an empty object store means files can be discarded.
"""
MIGRATION_V101 = (11, '''
CREATE TABLE IF NOT EXISTS avatar_asset_blobs (
    file_name TEXT PRIMARY KEY,
    body BLOB NOT NULL CHECK(length(body) > 0)
);
''')
LATEST_SCHEMA_VERSION = 11
