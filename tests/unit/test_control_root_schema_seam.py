import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

import cockpit_control as cc
import cockpit_control_root_schema as control_root_schema


def _snapshot_tree(root: Path):
    rows = []
    for current, directories, files in os.walk(root):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            rows.append((str(path.relative_to(root)), "dir"))
        for name in files:
            path = current_path / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append((str(path.relative_to(root)), "file", digest))
    return tuple(rows)


class ControlRootSchemaSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.ResolvedControlRoot, control_root_schema.ResolvedControlRoot)
        self.assertEqual(cc.CONTROL_SCHEMA_VERSION, control_root_schema.CONTROL_SCHEMA_VERSION)
        self.assertEqual(cc.CONTROL_ROOT_VARIABLE, control_root_schema.CONTROL_ROOT_VARIABLE)

    def test_resolve_control_root_uses_valid_shell_root(self):
        resolved = cc.resolve_control_root({"COCKPIT_CONTROL_ROOT": "/tmp/../tmp/control"})
        self.assertEqual(resolved.source, "shell")
        self.assertEqual(str(resolved.path), "/tmp/control")

    def test_resolve_control_root_uses_tmux_fallback(self):
        with mock.patch.object(
            cc.control_root_schema, "tmux_control_root", return_value="/opt/cockpit"
        ):
            resolved = cc.resolve_control_root({})
        self.assertEqual(resolved.source, "tmux")
        self.assertEqual(str(resolved.path), "/opt/cockpit")

    def test_relative_control_root_is_refused_fail_closed(self):
        with self.assertRaisesRegex(
            cc.ControlStoreError,
            "must be an absolute path; refusing to infer it from cwd",
        ):
            cc.resolve_control_root({"COCKPIT_CONTROL_ROOT": "relative/path"})

    def test_future_schema_is_refused_by_validator(self):
        with self.assertRaisesRegex(
            cc.ControlStoreError,
            "uses unsupported future schema_version 2; upgrade cockpit tools before mutation",
        ):
            cc._require_schema_version({"schema_version": 2}, "control.json")

    def test_preflight_future_schema_diagnostic_stays_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in cc.REQUIRED_STORE_DIRECTORIES:
                (root / directory).mkdir()
            (root / cc.CONTROL_METADATA_NAME).write_text(
                json.dumps({"schema_version": cc.CONTROL_SCHEMA_VERSION + 1}) + "\n",
                encoding="utf-8",
            )
            report = cc.run_control_preflight(root, source="configured")

        self.assertTrue(report.blocked)
        schema_findings = [finding for finding in report.findings if finding.dimension == "schema"]
        self.assertEqual(len(schema_findings), 1)
        self.assertEqual(
            schema_findings[0].detail,
            "control.json declares future schema_version 2; this build supports schema_version 1 and refuses mutation",
        )
        self.assertEqual(schema_findings[0].repair, cc.REPAIR_ACTION_UPGRADE)

    def test_repair_store_dry_run_future_schema_remains_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in cc.REQUIRED_STORE_DIRECTORIES:
                (root / directory).mkdir()
            (root / cc.CONTROL_METADATA_NAME).write_text(
                json.dumps({"schema_version": cc.CONTROL_SCHEMA_VERSION + 1}) + "\n",
                encoding="utf-8",
            )
            before = _snapshot_tree(root)
            with self.assertRaisesRegex(
                cc.ControlStoreError,
                "uses unsupported future schema_version 2; upgrade cockpit tools before mutation",
            ):
                cc.repair_control_store(root, dry_run=True)
            after = _snapshot_tree(root)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
