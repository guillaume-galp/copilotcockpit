import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

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

    def test_tmux_queue_root_reads_only_the_requested_variable(self):
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/test-socket,1,0"}, clear=True):
            with mock.patch.object(control_root_schema.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess(
                    [], 0, stdout="COCKPIT_QUEUE_ROOT=/tmp/queue\n", stderr=""
                )
                self.assertEqual(control_root_schema.tmux_queue_root(), "/tmp/queue")
                self.assertEqual(
                    run.call_args.args[0], ["tmux", "show-environment", "COCKPIT_QUEUE_ROOT"]
                )
                run.return_value.stdout = "COCKPIT_CONTROL_ROOT=/tmp/control\n"
                self.assertIsNone(control_root_schema.tmux_queue_root())

    def test_tmux_queue_root_does_not_probe_without_tmux_identity(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(control_root_schema.subprocess, "run") as run:
                self.assertIsNone(control_root_schema.tmux_queue_root())
                run.assert_not_called()

    def test_preflight_honors_tmux_queue_and_explicit_shell_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            root, queue = base / "control", base / "queue"
            with mock.patch.dict(os.environ, {}, clear=True):
                cc.initialize_control_store(
                    cc.ResolvedControlRoot(root, "configured"),
                    queue_root=str(queue),
                    planning_root=str(base / "planning"),
                    implementation_roots=[str(base / "implementation")],
                )
                for shell, tmux, blocked, advisory in (
                    ({}, str(queue), False, False),
                    ({}, str(base / "other"), True, True),
                    ({}, None, False, True),
                    ({"COCKPIT_QUEUE_ROOT": str(queue)}, "/other", False, False),
                    ({"COCKPIT_QUEUE_ROOT": ""}, str(queue), True, True),
                ):
                    with self.subTest(shell=shell, tmux=tmux):
                        with mock.patch.dict(os.environ, shell, clear=True):
                            with mock.patch.object(
                                control_root_schema, "tmux_queue_root", return_value=tmux
                            ):
                                before = _snapshot_tree(root)
                                report = cc.run_control_preflight(root)
                                self.assertEqual(report.blocked, blocked)
                                self.assertEqual(
                                    any(
                                        f.dimension == "queue" and f.state != cc.PREFLIGHT_READY
                                        for f in report.findings
                                    ),
                                    advisory,
                                )
                                self.assertEqual(_snapshot_tree(root), before)

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


class CapabilityUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "control"
        self.queue = self.base / "queue"
        self.environment = mock.patch.dict(
            os.environ,
            {
                "COCKPIT_CONTROL_ROOT": str(self.root),
                "COCKPIT_QUEUE_ROOT": str(self.queue),
                "COCKPIT_SESSION_ID": "legacy-cockpit",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        with mock.patch.object(cc, "_session_identity", return_value="legacy-cockpit"):
            cc.initialize_control_store(
                cc.ResolvedControlRoot(self.root, "configured"),
                queue_root=str(self.queue),
                planning_root=str(self.base / "planning"),
                implementation_roots=[str(self.base / "implementation"), str(self.base / "sibling")],
            )
        self.metadata = self.read_metadata()
        self.metadata["capabilities"] = {"control_store": 1}
        self.metadata["operator_extension"] = {"note": "preserve compatible metadata"}
        self.write_metadata()
        self.control_id = self.metadata["control_id"]

    def read_metadata(self):
        return json.loads((self.root / cc.CONTROL_METADATA_NAME).read_text())

    def write_metadata(self):
        (self.root / cc.CONTROL_METADATA_NAME).write_text(cc._serialized_record(self.metadata))

    def upgrade(self, **kwargs):
        return cc.upgrade_control_capabilities(self.root, self.control_id, **kwargs)

    def assert_refused_unchanged(self, message):
        before = _snapshot_tree(self.base)
        with self.assertRaisesRegex(cc.ControlStoreError, message):
            self.upgrade()
        self.assertEqual(_snapshot_tree(self.base), before)

    def test_empty_bound_legacy_upgrade_preserves_identity_roots_and_history(self):
        before = self.read_metadata()
        ledger = (self.root / cc.LEDGER_NAME).read_bytes()
        preflight = cc.run_control_preflight(self.root)
        self.assertTrue(preflight.blocked)
        self.assertTrue(any(
            f"upgrade-capabilities --expect-control-id {self.control_id}" in finding.repair
            for finding in preflight.findings if finding.repair is not None
        ))
        with mock.patch.object(cc, "utc_timestamp", return_value="2099-01-01T00:00:00Z"):
            result = self.upgrade()
        self.assertEqual(result.added, ("worker_lifecycle", "command_protocol"))
        expected = dict(before)
        expected["capabilities"] = dict(cc.SUPPORTED_CONTROL_CAPABILITIES)
        expected["last_migration_at"] = "2099-01-01T00:00:00Z"
        self.assertEqual(self.read_metadata(), expected)
        self.assertEqual((self.root / cc.LEDGER_NAME).read_bytes(), ledger)
        self.assertEqual((self.root / cc.EVENTS_NAME).read_bytes(), b"")
        self.assertFalse(cc.read_committed_events(self.root, self.control_id).events)
        self.assertEqual(cc.run_control_preflight(self.root).status, cc.PREFLIGHT_STATUS_READY)
        for name in ("events", "pending", "commands", "escalations"):
            self.assertEqual(list((self.root / name).iterdir()), [])
        status = cc.protocol_worker_status(self.root, ["worker-dev"])
        self.assertNotEqual(status["workers"]["worker-dev"]["status"], "idle")

    def test_dry_run_and_idempotent_upgrade_preserve_files(self):
        before = _snapshot_tree(self.base)
        self.assertEqual(self.upgrade(dry_run=True).added, ("worker_lifecycle", "command_protocol"))
        self.assertEqual(_snapshot_tree(self.base), before)
        self.upgrade()
        before = _snapshot_tree(self.base)
        metadata_stat = (self.root / cc.CONTROL_METADATA_NAME).stat()
        self.assertEqual(self.upgrade().added, ())
        self.assertEqual(_snapshot_tree(self.base), before)
        after_stat = (self.root / cc.CONTROL_METADATA_NAME).stat()
        self.assertEqual((after_stat.st_ino, after_stat.st_mtime_ns),
                         (metadata_stat.st_ino, metadata_stat.st_mtime_ns))

    def test_partial_supported_capabilities_add_only_missing_declaration(self):
        self.metadata["capabilities"]["worker_lifecycle"] = 1
        self.write_metadata()
        self.assertEqual(self.upgrade().added, ("command_protocol",))

    def test_wrong_or_malformed_expected_id_never_mutates(self):
        for expected in (str(uuid4()), "not-a-uuid", self.control_id.upper()):
            with self.subTest(expected=expected):
                before = _snapshot_tree(self.base)
                with self.assertRaises(cc.ControlStoreError):
                    cc.upgrade_control_capabilities(self.root, expected)
                self.assertEqual(_snapshot_tree(self.base), before)

    def test_missing_or_incomplete_boundaries_refused(self):
        original = self.read_metadata()
        original_ledger = json.loads((self.root / cc.LEDGER_NAME).read_text())
        for field, empty in (("queue_root", None), ("planning_root", None), ("implementation_roots", [])):
            with self.subTest(field=field):
                self.metadata = json.loads(json.dumps(original))
                self.metadata[field] = empty
                self.metadata["canonical_roots"][field] = empty
                self.write_metadata()
                ledger = json.loads(json.dumps(original_ledger))
                ledger["canonical_roots"][field] = empty
                (self.root / cc.LEDGER_NAME).write_text(cc._serialized_record(ledger))
                self.assert_refused_unchanged("complete bound roots")

    def test_conflicting_or_missing_root_paths_refused(self):
        self.metadata["queue_root"] = str(self.base / "other")
        self.write_metadata()
        self.assert_refused_unchanged("must match canonical_roots")
        self.metadata["queue_root"] = str(self.queue)
        self.write_metadata()
        (self.base / "planning").rmdir()
        self.assert_refused_unchanged("missing required declared root path")

    def test_duplicate_boundaries_refused(self):
        self.metadata["planning_root"] = str(self.queue)
        self.metadata["canonical_roots"]["planning_root"] = str(self.queue)
        self.write_metadata()
        (self.root / cc.LEDGER_NAME).write_text(cc._serialized_record(cc.build_ledger_projection(self.metadata)))
        self.assert_refused_unchanged("requires distinct paths")

    def test_future_schema_and_incompatible_capability_versions_refused(self):
        original = self.read_metadata()
        cases = [("schema_version", None, 2)]
        for field, names in (
            ("capabilities", ("control_store", "worker_lifecycle", "command_protocol", "unknown_protocol")),
            ("tool_capability_versions", ("cockpit-control", "unknown_tool")),
        ):
            for name in names:
                values = (1, 2, 0, -1, True, 1.0, "1", None) if name.startswith("unknown") else (
                    2, 0, -1, True, 1.0, "1", None,
                )
                for value in values:
                    cases.append((field, name, value))
        for field, name, value in cases:
            with self.subTest(field=field, name=name, value=value):
                self.metadata = json.loads(json.dumps(original))
                if name is None:
                    self.metadata[field] = value
                else:
                    self.metadata[field][name] = value
                self.write_metadata()
                self.assert_refused_unchanged("unsupported")

    def test_missing_base_capability_or_tool_refused(self):
        for field in ("capabilities", "tool_capability_versions"):
            with self.subTest(field=field):
                original = self.metadata[field]
                self.metadata[field] = {}
                self.write_metadata()
                self.assert_refused_unchanged("requires supported")
                self.metadata[field] = original

    def test_committed_history_refused_even_after_upgrade(self):
        self.upgrade()
        cc.publish_control_event(self.root, "capability-upgrade-fixture")
        self.assert_refused_unchanged("empty committed history")
        self.metadata = self.read_metadata()
        self.metadata["capabilities"] = {"control_store": 1}
        self.metadata["force"] = True
        self.metadata["approval"] = {"approved": True}
        self.write_metadata()
        self.assert_refused_unchanged("empty committed history")

    def test_stored_commands_and_escalations_refused_without_journal(self):
        for directory, kind, id_field in (
            ("commands", "command", "command_id"),
            ("escalations", "escalation", "escalation_id"),
        ):
            with self.subTest(directory=directory):
                identifier = str(uuid4())
                record = {
                    "schema_version": 1, "record_type": kind, id_field: identifier,
                    "control_id": self.control_id, "mission_id": str(uuid4()),
                    "queue_item_id": "QI-legacy", "worker_id": "worker-dev",
                    "command_type": "mission-dispatch", "status": "open",
                    "created_at": self.metadata["created_at"],
                }
                entry = self.root / directory / f"{identifier}.json"
                entry.write_text(cc._serialized_record(record))
                self.assert_refused_unchanged("empty store directories")
                entry.unlink()

    def test_legacy_projection_without_committed_history_refused(self):
        path = self.root / cc.LEDGER_NAME
        original = json.loads(path.read_text())
        for field, value in (("revision", 1), ("active_queue_item_id", "QI-legacy"),
                             ("unknown_legacy_workers", {"worker-dev": "idle"}),
                             ("updated_at", "2099-01-01T00:00:00Z")):
            with self.subTest(field=field):
                ledger = dict(original)
                ledger[field] = value
                path.write_text(cc._serialized_record(ledger))
                self.assert_refused_unchanged("empty journal projection")

    def test_nonempty_legacy_events_view_refused(self):
        cc.publish_control_event(self.root, "legacy-view-fixture")
        view = (self.root / cc.EVENTS_NAME).read_bytes()
        for entry in (self.root / cc.EVENTS_DIR_NAME).iterdir():
            entry.unlink()
        (self.root / cc.LEDGER_NAME).write_text(cc._serialized_record(cc.build_ledger_projection(self.metadata)))
        self.assert_refused_unchanged("empty events.jsonl")
        self.assertEqual((self.root / cc.EVENTS_NAME).read_bytes(), view)

    def test_corrupt_authority_and_projections_refused(self):
        for name in (cc.CONTROL_METADATA_NAME, cc.LEDGER_NAME, cc.EVENTS_NAME):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_text("{corrupt")
                self.assert_refused_unchanged("malformed")
                path.write_bytes(original)
        event = self.root / "events" / "invalid.json"
        event.write_text("{}")
        self.assert_refused_unchanged("not a committed event")

    def test_candidates_unexpected_state_and_queue_activity_refused(self):
        for relative in ("pending/candidate", "ledger.json.tmp", ".control.json.bind-old.tmp",
                         f"locks/{cc.LOCK_CANDIDATE_PREFIX}{uuid4()}",
                         "locks/.candidate-unknown", "items", "workers", "quarantine",
                         "commands/unknown", "escalations/unknown"):
            with self.subTest(relative=relative):
                entry = self.root / relative
                entry.write_text("legacy evidence")
                self.assert_refused_unchanged("debris|unexpected|unsupported")
                entry.unlink()
        (self.queue / "items").mkdir()
        (self.queue / cc.EVENTS_NAME).write_bytes(b"")
        self.assertEqual(self.upgrade(dry_run=True).added, ("worker_lifecycle", "command_protocol"))
        for relative in ("items/QI-old.yaml", "events.jsonl", "unexpected"):
            with self.subTest(queue=relative):
                entry = self.queue / relative
                entry.write_text("historical activity")
                self.assert_refused_unchanged("queue activity")
                entry.unlink()

    def test_symlinked_files_and_directories_refused(self):
        for relative in ("control.json", "ledger.json", "events.jsonl", "events", "pending", "locks"):
            with self.subTest(relative=relative):
                entry = self.root / relative
                target = self.base / "symlink-target"
                entry.rename(target)
                entry.symlink_to(target, target_is_directory=target.is_dir())
                self.assert_refused_unchanged("symlink")
                entry.unlink()
                target.rename(entry)
        planning = self.base / "planning"
        planning.rmdir()
        planning.symlink_to(self.base / "implementation", target_is_directory=True)
        self.assert_refused_unchanged("symlink")

    def test_symlinked_control_root_ancestor_refused_before_mutation(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        before = _snapshot_tree(self.root)
        with self.assertRaisesRegex(cc.ControlStoreError, "symlink"):
            cc.upgrade_control_capabilities(alias / "control", self.control_id)
        self.assertEqual(_snapshot_tree(self.root), before)

    def test_unsafe_guard_refused_without_opening_it(self):
        guard = self.root / "locks" / cc.CONTROL_GUARD_NAME
        os.mkfifo(guard)
        before = (self.root / cc.CONTROL_METADATA_NAME).read_bytes()
        with self.assertRaisesRegex(cc.ControlStoreError, "regular file"):
            self.upgrade()
        self.assertEqual((self.root / cc.CONTROL_METADATA_NAME).read_bytes(), before)

    def test_live_control_lock_refused_without_mutation(self):
        with cc.PortableControlLock(self.root, "fixture"):
            self.assert_refused_unchanged("empty store directories")

    def test_identity_revalidated_after_lock_acquisition(self):
        def replace_identity(boundary, lock):
            if boundary == "lock-published":
                self.metadata["control_id"] = str(uuid4())
                self.write_metadata()
                ledger = cc.build_ledger_projection(self.metadata)
                (self.root / cc.LEDGER_NAME).write_text(cc._serialized_record(ledger))

        with mock.patch.object(cc, "_lock_transition_fault", side_effect=replace_identity):
            with self.assertRaisesRegex(cc.ControlStoreError, "expected control_id"):
                self.upgrade()
        self.assertEqual(self.read_metadata()["capabilities"], {"control_store": 1})

    def test_history_revalidated_after_lock_acquisition(self):
        cc.publish_control_event(self.root, "upgrade-race-fixture")
        event = next((self.root / "events").iterdir())
        contents = event.read_bytes()
        event.unlink()
        (self.root / cc.LEDGER_NAME).write_text(cc._serialized_record(cc.build_ledger_projection(self.metadata)))
        (self.root / cc.EVENTS_NAME).write_bytes(b"")

        def add_history(boundary, lock):
            if boundary == "lock-published":
                event.write_bytes(contents)

        with mock.patch.object(cc, "_lock_transition_fault", side_effect=add_history):
            with self.assertRaisesRegex(cc.ControlStoreError, "empty committed history"):
                self.upgrade()
        self.assertEqual(self.read_metadata()["capabilities"], {"control_store": 1})
        self.assertEqual(event.read_bytes(), contents)

    def test_new_candidate_under_lock_refused(self):
        def add_candidate(boundary, lock):
            if boundary == "lock-published":
                (self.root / "pending" / "candidate").write_text("interrupted publication")

        with mock.patch.object(cc, "_lock_transition_fault", side_effect=add_candidate):
            with self.assertRaisesRegex(cc.ControlStoreError, "unresolved store debris"):
                self.upgrade()
        self.assertEqual(self.read_metadata()["capabilities"], {"control_store": 1})

    def test_missing_owned_lock_refused_before_metadata_replacement(self):
        def remove_lock(boundary, lock):
            if boundary == "lock-published":
                (lock.path / cc.LOCK_OWNER_NAME).unlink()
                lock.path.rmdir()

        before = self.read_metadata()
        with mock.patch.object(cc, "_lock_transition_fault", side_effect=remove_lock):
            with self.assertRaises(cc.ControlStoreError):
                self.upgrade()
        self.assertEqual(self.read_metadata(), before)

    def test_metadata_write_uses_both_locks_and_atomic_failure_preserves_authority(self):
        original = cc._write_replacement_json

        def inspect_locks(path, record, label):
            with self.assertRaisesRegex(cc.ControlStoreError, "timed out"):
                with cc.ControlTransitionGuard(self.root / "locks", timeout_seconds=0):
                    self.fail("transition guard was not held")
            self.assertTrue((self.root / "locks" / cc.CONTROL_LOCK_NAME).is_dir())
            original(path, record, label)

        before = self.read_metadata()
        with mock.patch.object(cc, "_write_replacement_json", side_effect=inspect_locks):
            with mock.patch.object(cc.os, "replace", side_effect=OSError("fixture failure")):
                with self.assertRaisesRegex(cc.ControlStoreError, "cannot atomically replace"):
                    self.upgrade()
        self.assertEqual(self.read_metadata(), before)
        self.assertFalse(list(self.root.glob(".control.json.bind-*.tmp")))
        self.assertFalse((self.root / "locks" / cc.CONTROL_LOCK_NAME).exists())

    def test_public_cli_requires_exact_id_and_reports_protocol_support_only(self):
        binary = str(ROOT / "bin" / "cockpit-control")
        before = _snapshot_tree(self.base)
        missing = subprocess.run([binary, "upgrade-capabilities"], capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--expect-control-id", missing.stderr)
        self.assertEqual(_snapshot_tree(self.base), before)
        args = [binary, "upgrade-capabilities", "--expect-control-id", self.control_id]
        for option in ("--force", "--approve", "--authorize"):
            refused = subprocess.run(args + [option], capture_output=True, text=True)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(_snapshot_tree(self.base), before)
        preview = subprocess.run(args + ["--dry-run"], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("would upgrade", preview.stdout)
        self.assertEqual(_snapshot_tree(self.base), before)
        upgraded = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        self.assertIn("not worker acceptance or idle evidence", upgraded.stdout)
        self.assertEqual(upgraded.stderr, "")
        repeated = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("already current", repeated.stdout)


if __name__ == "__main__":
    unittest.main()
