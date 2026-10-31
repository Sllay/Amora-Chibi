from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import zipfile

from pydantic import ValidationError

from .models import AvatarProject
from .store import AvatarStore


AVATAR_BUNDLE_FORMAT = "amora-avatar-bundle"
AVATAR_BUNDLE_VERSION = 1
MAX_AVATAR_BUNDLE_BYTES = 128 * 1024 * 1024
MAX_AVATAR_BUNDLE_UNCOMPRESSED_BYTES = 192 * 1024 * 1024
MAX_AVATAR_BUNDLE_ENTRIES = 512
MAX_AVATAR_BUNDLE_MANIFEST_BYTES = 1024 * 1024
MAX_AVATAR_BUNDLE_PROJECT_BYTES = 4 * 1024 * 1024
MAX_AVATAR_BUNDLE_ASSET_BYTES = 12 * 1024 * 1024

_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ASSET_TYPES = {"image/png", "image/webp", "image/jpeg"}


@dataclass(frozen=True)
class AvatarBundleImportResult:
    project: AvatarProject
    assets_written: int
    assets_unchanged: int


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _safe_member_name(name: str) -> bool:
    if not name or "\\" in name or name.startswith("/"):
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return str(path) == name


def _project_model_counts(project: AvatarProject) -> dict[str, int]:
    return {
        "assets": len(project.assets),
        "parts": len(project.parts),
        "attachments": len(project.attachments),
        "bones": len(project.bones),
        "ik_chains": len(project.ik_chains),
        "physics": len(project.physics),
        "expressions": len(project.expressions),
        "animations": len(project.animations),
    }


def build_avatar_bundle(store: AvatarStore, project: AvatarProject) -> bytes:
    """Build one self-contained, integrity-checked portable Avatar archive."""
    asset_refs = {item.id: item for item in project.assets}
    missing_refs = sorted({part.asset_id for part in project.parts} - set(asset_refs))
    if missing_refs:
        raise ValueError(f"avatar project references undeclared assets: {', '.join(missing_refs)}")

    project_body = _json_bytes(project.model_dump(mode="json"))
    if len(project_body) > MAX_AVATAR_BUNDLE_PROJECT_BYTES:
        raise ValueError("avatar project JSON is too large for a portable bundle")

    asset_payloads: list[tuple[dict[str, object], bytes]] = []
    for ref in project.assets:
        stored = store.read_asset(ref.id)
        if stored is None:
            raise ValueError(f"avatar bundle asset is missing: {ref.id}")
        info, body = stored
        if len(body) > MAX_AVATAR_BUNDLE_ASSET_BYTES:
            raise ValueError(f"avatar bundle asset is too large: {ref.id}")
        if info.content_type not in _ALLOWED_ASSET_TYPES:
            raise ValueError(f"avatar bundle asset has unsupported content type: {ref.id}")
        if ref.content_type != info.content_type:
            raise ValueError(f"avatar project asset metadata does not match stored asset: {ref.id}")
        path = f"assets/{info.file_name}"
        if not _safe_member_name(path):
            raise ValueError(f"avatar bundle asset path is unsafe: {ref.id}")
        asset_payloads.append(
            (
                {
                    "id": ref.id,
                    "name": ref.name,
                    "content_type": info.content_type,
                    "path": path,
                    "size_bytes": len(body),
                    "sha256": _digest(body),
                },
                body,
            )
        )

    manifest = {
        "format": AVATAR_BUNDLE_FORMAT,
        "version": AVATAR_BUNDLE_VERSION,
        "project": {
            "id": project.id,
            "schema_version": project.schema_version,
            "path": "project.json",
            "size_bytes": len(project_body),
            "sha256": _digest(project_body),
        },
        "assets": [item for item, _ in asset_payloads],
        "model": _project_model_counts(project),
    }
    manifest_body = _json_bytes(manifest)
    if len(manifest_body) > MAX_AVATAR_BUNDLE_MANIFEST_BYTES:
        raise ValueError("avatar bundle manifest is too large")

    target = BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", manifest_body)
        archive.writestr("project.json", project_body)
        for item, body in asset_payloads:
            archive.writestr(str(item["path"]), body)

    bundle = target.getvalue()
    if len(bundle) > MAX_AVATAR_BUNDLE_BYTES:
        raise ValueError("avatar portable bundle is too large")
    return bundle


def _load_json_object(body: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}: expected object")
    return value


def _validate_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_AVATAR_BUNDLE_ENTRIES:
        raise ValueError("avatar bundle contains too many entries")

    members: dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError("encrypted avatar bundle entries are not supported")
        if info.is_dir():
            continue
        if not _safe_member_name(info.filename):
            raise ValueError("avatar bundle contains unsafe paths")
        if info.filename in members:
            raise ValueError("avatar bundle contains duplicate paths")
        total_size += int(info.file_size)
        if total_size > MAX_AVATAR_BUNDLE_UNCOMPRESSED_BYTES:
            raise ValueError("avatar bundle expands beyond the allowed size")
        members[info.filename] = info
    return members


def _manifest_int(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"invalid avatar bundle {label}")
    return value


def _manifest_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid avatar bundle {label}")
    return value


def _rollback_assets(
    store: AvatarStore,
    changed: list[str],
    previous: dict[str, tuple[object, bytes] | None],
) -> None:
    errors: list[Exception] = []
    for asset_id in reversed(changed):
        old = previous[asset_id]
        try:
            if old is None:
                store.delete_asset(asset_id)
            else:
                info, body = old
                store.put_asset(asset_id, getattr(info, "content_type"), body)
        except Exception as exc:  # pragma: no cover - catastrophic storage recovery path
            errors.append(exc)
    if errors:
        raise RuntimeError("avatar bundle import failed and asset rollback was incomplete") from errors[0]


def _restore_previous_state(
    store: AvatarStore,
    project: AvatarProject,
    changed: list[str],
    previous: dict[str, tuple[object, bytes] | None],
) -> None:
    errors: list[Exception] = []
    try:
        _rollback_assets(store, changed, previous)
    except Exception as exc:  # pragma: no cover - catastrophic storage recovery path
        errors.append(exc)
    try:
        store.put_project(project)
    except Exception as exc:  # pragma: no cover - catastrophic storage recovery path
        errors.append(exc)
    if errors:
        raise RuntimeError("avatar bundle import failed and previous state could not be fully restored") from errors[0]


def restore_avatar_bundle(store: AvatarStore, body: bytes) -> AvatarBundleImportResult:
    """Validate a complete archive before mutating the active Avatar project."""
    if not body:
        raise ValueError("empty avatar bundle")
    if len(body) > MAX_AVATAR_BUNDLE_BYTES:
        raise ValueError("avatar portable bundle is too large")

    try:
        archive = zipfile.ZipFile(BytesIO(body), "r")
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ValueError("invalid avatar bundle ZIP") from exc

    with archive:
        members = _validate_archive_members(archive)
        manifest_info = members.get("manifest.json")
        if manifest_info is None:
            raise ValueError("avatar bundle is missing manifest.json")
        if manifest_info.file_size > MAX_AVATAR_BUNDLE_MANIFEST_BYTES:
            raise ValueError("avatar bundle manifest is too large")
        manifest = _load_json_object(archive.read(manifest_info), label="avatar bundle manifest")

        if manifest.get("format") != AVATAR_BUNDLE_FORMAT:
            raise ValueError("unsupported avatar bundle format")
        if manifest.get("version") != AVATAR_BUNDLE_VERSION:
            raise ValueError("unsupported avatar bundle version")

        project_meta = manifest.get("project")
        asset_meta = manifest.get("assets")
        if not isinstance(project_meta, dict) or not isinstance(asset_meta, list):
            raise ValueError("invalid avatar bundle manifest structure")

        project_path = _manifest_text(project_meta.get("path"), label="project path")
        if project_path != "project.json" or not _safe_member_name(project_path):
            raise ValueError("invalid avatar bundle project path")
        project_info = members.get(project_path)
        if project_info is None:
            raise ValueError("avatar bundle is missing project.json")
        if project_info.file_size > MAX_AVATAR_BUNDLE_PROJECT_BYTES:
            raise ValueError("avatar bundle project JSON is too large")
        project_body = archive.read(project_info)
        expected_project_size = _manifest_int(project_meta.get("size_bytes"), label="project size", minimum=1)
        expected_project_sha = _manifest_text(project_meta.get("sha256"), label="project sha256")
        if not _SHA256.fullmatch(expected_project_sha):
            raise ValueError("invalid avatar bundle project sha256")
        if len(project_body) != expected_project_size or _digest(project_body) != expected_project_sha:
            raise ValueError("avatar bundle project integrity mismatch")

        try:
            project = AvatarProject.model_validate_json(project_body)
        except ValidationError as exc:
            raise ValueError("avatar bundle contains an invalid Avatar project") from exc
        if project.id != "default":
            raise ValueError("avatar bundle v1 only supports the active default project")
        if project_meta.get("id") != project.id or project_meta.get("schema_version") != project.schema_version:
            raise ValueError("avatar bundle project metadata mismatch")

        refs = {item.id: item for item in project.assets}
        part_asset_ids = {part.asset_id for part in project.parts}
        if not part_asset_ids.issubset(refs):
            raise ValueError("avatar bundle project references undeclared assets")

        parsed_assets: list[tuple[str, str, bytes, str]] = []
        manifest_ids: set[str] = set()
        expected_members = {"manifest.json", project_path}
        for raw in asset_meta:
            if not isinstance(raw, dict):
                raise ValueError("invalid avatar bundle asset entry")
            asset_id = _manifest_text(raw.get("id"), label="asset id")
            name = _manifest_text(raw.get("name"), label=f"asset name for {asset_id}")
            content_type = _manifest_text(raw.get("content_type"), label=f"asset content type for {asset_id}").lower()
            path = _manifest_text(raw.get("path"), label=f"asset path for {asset_id}")
            size_bytes = _manifest_int(raw.get("size_bytes"), label=f"asset size for {asset_id}", minimum=1)
            sha256 = _manifest_text(raw.get("sha256"), label=f"asset sha256 for {asset_id}")

            if asset_id in manifest_ids:
                raise ValueError("avatar bundle contains duplicate asset ids")
            manifest_ids.add(asset_id)
            if not _ASSET_ID.fullmatch(asset_id):
                raise ValueError(f"invalid avatar bundle asset id: {asset_id}")
            if content_type not in _ALLOWED_ASSET_TYPES:
                raise ValueError(f"unsupported avatar bundle asset content type: {asset_id}")
            if not _SHA256.fullmatch(sha256):
                raise ValueError(f"invalid avatar bundle asset sha256: {asset_id}")
            if size_bytes > MAX_AVATAR_BUNDLE_ASSET_BYTES:
                raise ValueError(f"avatar bundle asset is too large: {asset_id}")
            if not _safe_member_name(path) or PurePosixPath(path).parent != PurePosixPath("assets"):
                raise ValueError(f"invalid avatar bundle asset path: {asset_id}")

            ref = refs.get(asset_id)
            if ref is None:
                raise ValueError(f"avatar bundle manifest contains undeclared asset: {asset_id}")
            if ref.name != name or ref.content_type != content_type:
                raise ValueError(f"avatar bundle asset metadata mismatch: {asset_id}")

            info = members.get(path)
            if info is None:
                raise ValueError(f"avatar bundle is missing asset payload: {asset_id}")
            payload = archive.read(info)
            if len(payload) != size_bytes or _digest(payload) != sha256:
                raise ValueError(f"avatar bundle asset integrity mismatch: {asset_id}")
            parsed_assets.append((asset_id, content_type, payload, sha256))
            expected_members.add(path)

        if manifest_ids != set(refs):
            missing = sorted(set(refs) - manifest_ids)
            raise ValueError(f"avatar bundle manifest is missing project assets: {', '.join(missing)}")
        if set(members) != expected_members:
            raise ValueError("avatar bundle contains unlisted or unexpected files")

    old_project = store.get_project("default")
    previous: dict[str, tuple[object, bytes] | None] = {}
    changed: list[str] = []
    written = 0
    unchanged = 0
    for asset_id, content_type, payload, sha256 in parsed_assets:
        old = store.read_asset(asset_id)
        previous[asset_id] = old
        if old is not None and getattr(old[0], "content_type") == content_type and getattr(old[0], "sha256") == sha256:
            unchanged += 1
            continue
        changed.append(asset_id)
        try:
            store.put_asset(asset_id, content_type, payload)
        except Exception:
            _rollback_assets(store, changed, previous)
            raise
        written += 1

    try:
        store.put_project(project)
    except Exception:
        _restore_previous_state(store, old_project, changed, previous)
        raise

    return AvatarBundleImportResult(project=project, assets_written=written, assets_unchanged=unchanged)
