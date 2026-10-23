"""Binary persistence separate from cognitive tables and compute filesystem."""
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    durable_remote: bool
    def put(self, key: str, body: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...


class FileBlobStore:
    durable_remote = False

    def __init__(self, directory: Path):
        self.directory = directory

    def put(self, key, body):
        path = self.directory / key
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(body)
        temporary.replace(path)

    def get(self, key):
        path = self.directory / key
        return path.read_bytes() if path.is_file() else None

    def delete(self, key):
        (self.directory / key).unlink(missing_ok=True)


class SQLBlobStore:
    """Small avatar sprites stored atomically with their existing SQL metadata.

    Uses the owning AvatarStore transaction/lock; not a generic large-object store.
    A future object store adapter can replace this without changing AvatarEngine.
    """
    durable_remote = True

    def __init__(self, connection):
        self.connection = connection

    def put(self, key, body):
        self.connection.execute('INSERT INTO avatar_asset_blobs(file_name,body) VALUES (?,?) ON CONFLICT(file_name) DO UPDATE SET body=excluded.body', (key, body))

    def get(self, key):
        row = self.connection.execute('SELECT body FROM avatar_asset_blobs WHERE file_name=?', (key,)).fetchone()
        return bytes(row[0]) if row is not None else None

    def delete(self, key):
        self.connection.execute('DELETE FROM avatar_asset_blobs WHERE file_name=?', (key,))
