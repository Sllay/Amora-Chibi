"""Northflank read-only audit assertions: no API secrets, writes or guessed passes."""
import unittest
from unittest.mock import patch

from tools.ops.northflank_readonly_audit import NorthflankReader, _read_config, audit


class FakeReader:
    def __init__(self):
        self.records = {
            "core": ({"buildSettings": {"dockerfile": {"dockerFilePath": "/Dockerfile"}},
                      "vcsData": {"projectBranch": "main"},
                      "deployment": {"docker": {"configType": "default"}, "instances": 1}}, {}),
            "pcs4": ({"jobType": "manual", "settings": {"runOnSourceChange": "never", "backoffLimit": 0},
                      "buildSettings": {"dockerfile": {"dockerFilePath": "/Dockerfile"}},
                      "deployment": {"docker": {"configType": "customCommand",
                                                "customCommand": "python -m amora.storage.aiven_snapshot_audit"}}}, {}),
            "aiven": ({"jobType": "manual", "settings": {"runOnSourceChange": "never", "backoffLimit": 0},
                       "buildSettings": {"dockerfile": {"dockerFilePath": "/deploy/Dockerfile.aiven-preflight"}},
                       "deployment": {"docker": {"configType": "default"}}}, {}),
        }

    def item(self, category, identifier):
        return self.records[{"loyal-coat": "core", "pcs4c-recovery": "pcs4", "aiven-job": "aiven"}[identifier]

    def find_aiven_job(self):
        return "aiven-job"


class ConfigAuditTests(unittest.TestCase):
    def test_values_not_emitted_even_if_api_contains_secret(self):
        doc = {"runtimeEnvironment": {"AMORA_POSTGRES_DSN": "secret://not-a-real-value"},
               "deployment": {"docker": {"configType": "customCommand",
                                         "customCommand": "echo secret://not-a-real-value"}}}
        result = _read_config(doc, {}, kind="core")
        self.assertNotIn("secret://", repr(result))
        self.assertEqual(result["command"], "different_custom_command")
        self.assertEqual(result["dockerfile"], "unknown")
        self.assertEqual(result["cd"], "unknown")

    def test_original_pcs4_command_flagged_not_mutated(self):
        report = audit(FakeReader(), "loyal-coat", "pcs4c-recovery", None)
        self.assertEqual(report["resources"]["pcs4"]["command"], "different_custom_command")
        self.assertEqual(report["resources"]["aiven"]["dockerfile"], "match")
        self.assertFalse(report["production_cutover_authorized"])
        self.assertFalse(report["runtime_secret_values_read"])

    def test_only_get_api_calls_and_fixed_host(self):
        reader = NorthflankReader("SENSITIVE_DONT_LOG", "mejorberrys-team", "default")
        with patch("tools.ops.northflank_readonly_audit.request.urlopen") as api:
            api.return_value.__enter__.return_value.read.return_value = (
                b'{"data":{"status":"ok","runtimeEnvironment":{"password":"PRIVATE"}}}'
            )
            result = reader.get(reader.prefix + "/jobs")
            self.assertEqual(result["status"], "ok")
            req = api.call_args.args[0]
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.full_url, "https://api.northflank.com/v1" + reader.prefix + "/jobs")
        with self.assertRaises(ValueError):
            reader.get(reader.prefix + "/jobs/../../credentials")
        with self.assertRaises(ValueError):
            NorthflankReader("token", "unsafe/../../", "default")

    def test_unknown_cd_is_not_reported_as_disabled(self):
        self.assertEqual(_read_config({"deployment": {"docker": {"configType": "default"}}},
                                      {}, kind="core")["cd"], "unknown")

    def test_disabled_cd_flag_is_interpreted_as_disabled(self):
        self.assertEqual(_read_config({"disabledCD": True}, {}, kind="pcs4")["cd"], "disabled")


if __name__ == "__main__":
    unittest.main()
