#!/usr/bin/env python3
"""Package a Northflank snapshot and inspect its encrypted archive safely.

No arbitrary log text is printed or emitted by diagnose. The ZIP is encrypted
as a WHOLE by GPG AES256 outside this program; names and contents stay opaque.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

MAX_FILES = 600
MAX_ZIP_BYTES = 16_000_000
MAX_ITEM_BYTES = 150_000
MAX_DIAGNOSTIC_EVENTS = 40

# Audit-safe event types: only fixed symbolic codes, not freeform log lines.
EVENT_CODES = (
    "WEB_SCREENSHOT_PIPELINE_READY",
    "WEB_SCREENSHOT_DISPATCH",
    "WEB_SCREENSHOT_UNAVAILABLE",
    "WEB_SCREENSHOT_METADATA_MISSING",
    "WEB_SCREENSHOT_UPLOAD_REJECTED",
    "WEB_SCREENSHOT_ATTACHMENT_NOT_CONFIRMED",
    "WEB_SCREENSHOT_ATTACHMENT_CONFIRMED",
    "WEB_SCREENSHOT_RECEIPT_NOTICE_FAILED",
    "OUTBOUND_CLAIMED",
    "OUTBOUND_SENT",
    "OUTBOUND_SEND_FAILED",
    "OUTBOUND_SEND_OUTCOME_UNKNOWN",
    "OUTBOUND_ACK_FAILED",
    "CORE_INBOUND_OK",
    "WEB_REFERENCE_NOT_FOUND",
    "WEB_SEARCH_UNAVAILABLE",
    "WEB_PAGE_CONTENT_UNAVAILABLE",
    "AIVEN_STAGING_OPERATOR",
    "PCS4C_JOB_REPORT",
)
REASONS = (
    "disabled", "invalid_public_url", "daily_limit", "provider_rate_limited",
    "provider_auth_required", "provider_http_error", "provider_refused",
    "provider_response_invalid", "provider_image_url_invalid",
    "image_cdn_http_error", "image_mime_invalid", "image_bytes_invalid",
    "provider_timeout", "provider_network_error", "provider_unknown",
    "no_authorized_public_page",
)
SYMBOLS = re.compile(r"(?<![A-Z0-9_])(" + "|".join(map(re.escape, EVENT_CODES)) + r")(?![A-Z0-9_])")
REASON = re.compile(r"\breason=(" + "|".join(map(re.escape, REASONS)) + r")\b")
SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]")


def zip_logs(root: Path, destination: Path) -> dict:
    paths = sorted(
        p for p in root.rglob("*") if p.is_file()
        and (p.name == "index.json" or p.suffix == ".log")
    )
    if not paths or len(paths) > MAX_FILES:
        raise ValueError("invalid_log_file_count")
    if root.is_symlink() or any(p.is_symlink() or p.stat().st_size > MAX_ITEM_BYTES for p in paths):
        raise ValueError("invalid_log_file")
    total = sum(p.stat().st_size for p in paths)
    if total > MAX_ZIP_BYTES:
        raise ValueError("archive_size_limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for path in paths:
            out.write(path, arcname=str(path.relative_to(root)))
    return {"files": len(paths), "bytes_uncompressed": total}


def diagnostic_report(archive: Path, ciphertext: Path) -> dict:
    # ZipFile uses a limited archive and never extracts to disk.
    if archive.stat().st_size > MAX_ZIP_BYTES:
        raise ValueError("archive_size_limit")
    overview = {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "archive_sha256": hashlib.sha256(ciphertext.read_bytes()).hexdigest(),
                "resources": [], "summary_events": {}}
    all_events = Counter()
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not members or len(members) > MAX_FILES:
            raise ValueError("invalid_archive_entry_count")
        if any(m.file_size > MAX_ITEM_BYTES or m.is_dir() or
               ".." in Path(m.filename).parts or m.filename.startswith("/")
               for m in members):
            raise ValueError("invalid_archive_entry")
        if sum(m.file_size for m in members) > MAX_ZIP_BYTES:
            raise ValueError("archive_uncompressed_limit")
        for member in members:
            if member.filename == "index.json" or member.filename.endswith("/index.json"):
                continue
            if not member.filename.endswith(".log"):
                continue
            parts = Path(member.filename).parts
            if len(parts) < 4 or parts[0] not in ("services", "jobs"):
                continue
            events = Counter()
            failures = Counter()
            with source.open(member) as fp:
                for raw in fp:
                    # The line content never enters the public report.
                    line = raw.decode("utf-8", "replace")
                    for found in SYMBOLS.finditer(line):
                        event = found.group(1)
                        events[event] += 1
                        all_events[event] += 1
                        if event == "WEB_SCREENSHOT_UNAVAILABLE":
                            reason = REASON.search(line)
                            if reason:
                                failures[reason.group(1)] += 1
            resource = SAFE_NAME.sub("-", parts[1])[:75]
            version = SAFE_NAME.sub("-", Path(member.filename).stem)[:75]
            overview["resources"].append({
                "kind": parts[0], "name": resource,
                "version": version, "events": dict(events.most_common(MAX_DIAGNOSTIC_EVENTS)),
                "screenshot_failure_reasons": dict(failures),
            })
    overview["summary_events"] = dict(all_events.most_common(MAX_DIAGNOSTIC_EVENTS))
    return overview


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("pack", "diagnose"))
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ciphertext")
    args = parser.parse_args()
    target = Path(args.output)
    if args.operation == "pack":
        result = zip_logs(Path(args.input), target)
        # Only bounded statistics, never log lines.
        print("archive_pack_ok files=" + str(result["files"]))
    else:
        if not args.ciphertext:
            raise ValueError("missing_ciphertext")
        report = diagnostic_report(Path(args.input), Path(args.ciphertext))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("archive_diagnostics_ok resources=" + str(len(report["resources"])))


if __name__ == "__main__":
    main()
