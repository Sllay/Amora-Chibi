"""Secret-free contract tests for encrypted Northflank log rotation."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Core CI adds /core to sys.path, not the repository root. The operational
# tools are source scripts, not a published Python distribution.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ops.northflank_log_archive import diagnostic_report, zip_logs
from tools.ops.northflank_log_collector import collect, safe_log


class FakeNorthflank:
    project = "amora-project"

    def __init__(self):
        self.history = [f"deploy-{n}" for n in range(1, 9)]
        self.log_calls = []

    def list_resources(self, kind):
        return [{"id": "test-service", "name": "amora-core"}] if kind == "services" else [
            {"id": "test-job", "name": "backup-job"}]

    def versions(self, kind, resource_id):
        return [{"id": value, "status": "SUCCESS", "startedAt": "2026-11-20T00:00:00Z"}
                for value in self.history[:6]]

    def logs(self, kind, resource_id, version_id):
        self.log_calls.append((kind, resource_id, version_id))
        return [
            {"ts": "2026-11-20T00:00:01Z",
             "log": "WEB_SCREENSHOT_UNAVAILABLE reason=provider_rate_limited"},
            {"ts": "2026-11-20T00:00:02Z",
             "log": "authorization: Bearer secret_should_never_leak"},
        ]


class NorthflankLogArchiveTests(unittest.TestCase):
    def test_rotation_keeps_only_latest_six_per_resource(self):
        api = FakeNorthflank()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "collected"
            result = collect(api, root)
            self.assertEqual(result["error_count"], 0)
            self.assertEqual(len(list(root.rglob("*.log"))), 12)
            self.assertEqual(len(api.log_calls), 12)
            self.assertNotIn("secret_should_never_leak",
                             "\n".join(path.read_text() for path in root.rglob("*.log")))
            # The old sixth version must be pruned after fresh deployments.
            api.history = [f"deploy-{n}" for n in range(0, 8)]
            collect(api, root)
            self.assertEqual(len(list(root.rglob("*.log"))), 12)
            self.assertNotIn("deploy-6",
                             "\n".join(path.read_text() for path in root.rglob("index.json")))

    def test_expired_old_logs_keep_other_snapshots_and_mark_failed(self):
        class MissingOldLogs(FakeNorthflank):
            def logs(self, kind, resource_id, version_id):
                if version_id == "deploy-6":
                    from tools.ops.northflank_log_collector import CollectionError
                    raise CollectionError("northflank_http_404")
                return super().logs(kind, resource_id, version_id)
        with tempfile.TemporaryDirectory() as tmp:
            result = collect(MissingOldLogs(), Path(tmp))
            self.assertEqual(result["error_count"], 2)
            self.assertEqual(result["readable_snapshots"], 10)
            index_data = [json.loads(p.read_text()) for p in Path(tmp).rglob("index.json")
                          if p != Path(tmp) / "index.json"]
            self.assertEqual(
                sum(row["collection_status"] == "failed"
                    for index in index_data for row in index["versions"]), 2
            )

    def test_zip_contains_subfolders_but_reports_only_fixed_event_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "raw" / "services" / "amora-core" / "deployment-1"
            source.mkdir(parents=True)
            (root / "raw" / "index.json").write_text('{"project":"test"}')
            (source / "index.json").write_text('{"versions":[{"id":"deploy-1"}]}')
            (source / "deploy-1.log").write_text(
                "2026-11-20 WEB_SCREENSHOT_UNAVAILABLE reason=provider_timeout "
                "UserName=private https://example.com/account?token=123\n"
                "2026-11-20 WEB_SCREENSHOT_ATTACHMENT_CONFIRMED\n"
                "2026-11-20 WEB_SCREENSHOT_PIPELINE_READY version=20261120-r2\n"
                "2026-11-20 WEB_SCREENSHOT_DISPATCH requested=true authorized=false\n"
                "2026-11-20 WEB_SCREENSHOT_UPLOAD_REJECTED status=400\n"
                "2026-11-20 random freeform user message\n"
            )
            archive = root / "archive.zip"
            zip_logs(root / "raw", archive)
            with zipfile.ZipFile(archive) as zf:
                self.assertTrue(any(name.endswith("deploy-1.log") for name in zf.namelist()))
            ciphertext = root / "archive.zip.gpg"
            ciphertext.write_bytes(b"opaque-fake-ciphertext")
            report = diagnostic_report(archive, ciphertext)
            document = json.dumps(report)
            self.assertEqual(report["summary_events"]["WEB_SCREENSHOT_UNAVAILABLE"], 1)
            self.assertEqual(report["summary_events"]["WEB_SCREENSHOT_ATTACHMENT_CONFIRMED"], 1)
            for forbidden in ("UserName", "private", "example.com", "random freeform"):
                self.assertNotIn(forbidden, document)
            self.assertIn("provider_timeout", document)
            self.assertIn("requested_true_authorized_false", document)
            self.assertIn("20261120-r2", document)
            self.assertIn('"400"', document)

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../../unsafe.log", "WEB_SCREENSHOT_UNAVAILABLE")
            ciphertext = Path(tmp) / "cipher"
            ciphertext.write_bytes(b"example")
            with self.assertRaises(ValueError):
                diagnostic_report(path, ciphertext)

    def test_existing_secrets_and_project_are_wired_into_archive_workflow(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/northflank-encrypted-archive.yml"
        ).read_text(encoding="utf-8")
        assert "secrets.NF_BACKUP_API_TOKEN" in workflow
        assert "secrets.AMORA_LOG_ARCHIVE_PASSPHRASE" in workflow
        assert "vars.NORTHFLANK_PROJECT_ID || 'default'" in workflow
        assert "vars.NORTHFLANK_TEAM_ID || 'mejorberrys-team'" in workflow
        assert "secrets.NORTHFLANK_API_TOKEN" not in workflow

    def test_failure_report_is_safe_when_decryptor_exits_early(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/northflank-encrypted-diagnosis.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("id: diagnose", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("DIAGNOSIS_STEP_OUTCOME", workflow)
        self.assertIn('"reason":"decrypt_or_scan_failed"', workflow)
        self.assertNotIn('echo "$ARCHIVE_PASSPHRASE"', workflow)

    def test_redaction_of_credentials_precedes_any_public_diagnostics(self):
        self.assertEqual(
            safe_log('Authorization: Bearer token-super-secret'),
            "[SENSITIVE_LINE_REDACTED]",
        )

    @unittest.skipUnless(shutil.which("gpg"), "Requires GPG for local encryption smoke")
    def test_gpg_aes256_round_trip_with_disposable_test_only_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.zip"
            source.write_bytes(b"ZIP test data - not production logs")
            encrypted = Path(tmp) / "sample.zip.gpg"
            decrypted = Path(tmp) / "restored.zip"
            key = b"disposable-test-passphrase-not-a-production-secret"
            base = [
                "gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
                "--passphrase-fd", "0",
            ]
            subprocess.run(base + ["--symmetric", "--cipher-algo", "AES256",
                                   "--output", str(encrypted), str(source)],
                           input=key, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self.assertNotIn(source.read_bytes(), encrypted.read_bytes())
            subprocess.run(base + ["--decrypt", "--output", str(decrypted),
                                   str(encrypted)],
                           input=key, check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self.assertEqual(source.read_bytes(), decrypted.read_bytes())


if __name__ == "__main__":
    unittest.main()
