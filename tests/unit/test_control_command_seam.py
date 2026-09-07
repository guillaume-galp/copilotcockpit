import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import cockpit_control as cc
import cockpit_control_commands as control_commands


def _snapshot_tree(root: Path):
    rows = []
    for current, directories, files in os.walk(root):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            rows.append((str((current_path / name).relative_to(root)), "dir"))
        for name in files:
            path = current_path / name
            rows.append((str(path.relative_to(root)), "file", path.read_bytes()))
    return tuple(rows)


def _without_guard(rows):
    return tuple(
        row for row in rows if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}"
    )


def _initialize_root(root: Path) -> None:
    cc.initialize_control_store(cc.ResolvedControlRoot(path=root, source="configured"))


class ControlCommandSeamTests(unittest.TestCase):
    def _envelope(self, command_id: str, digest: str) -> dict:
        return cc.build_command_envelope(
            command_id=command_id,
            command_type="dispatch",
            mission_id=str(uuid4()),
            queue_item_id="QI-11",
            target_kind=cc.COMMAND_TARGET_WORKER,
            target_id="worker-dev",
            trace_id=str(uuid4()),
            payload_digest=digest,
            control_root="/control",
            queue_root="/queue",
            planning_root="/planning",
            implementation_roots=["/impl"],
            runtime_boundaries=["runtime:tmux"],
            created_at="2026-01-01T00:00:00.000000Z",
        )

    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.validate_command_envelope, control_commands.validate_command_envelope)
        self.assertIs(
            cc.validate_command_acknowledgement, control_commands.validate_command_acknowledgement
        )
        self.assertIs(cc.fold_commands, control_commands.fold_commands)
        self.assertIs(cc.register_command, control_commands.register_command)
        self.assertIs(cc.acknowledge_command, control_commands.acknowledge_command)
        self.assertIs(cc.observe_commands, control_commands.observe_commands)
        self.assertIs(cc.CommandRecordResult, control_commands.CommandRecordResult)

    def test_register_acknowledge_duplicate_and_cli_status_are_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            command_id = str(uuid4())
            digest = cc.command_payload_digest({"type": "dispatch", "value": 1})
            envelope = self._envelope(command_id, digest)

            registered = cc.register_command(root, envelope, actor="worker-dev")
            self.assertTrue(registered.committed)
            self.assertEqual(registered.outcome, cc.COMMAND_FOLD_REGISTERED)

            accepted = cc.acknowledge_command(
                root,
                cc.build_command_acknowledgement(
                    command_id=command_id,
                    payload_digest=digest,
                    outcome=cc.COMMAND_ACCEPTED,
                    acknowledged_by="worker-dev",
                ),
            )
            self.assertEqual(accepted.outcome, cc.COMMAND_FOLD_ACKNOWLEDGED)
            applied = cc.acknowledge_command(
                root,
                cc.build_command_acknowledgement(
                    command_id=command_id,
                    payload_digest=digest,
                    outcome=cc.COMMAND_APPLIED,
                    acknowledged_by="worker-dev",
                    result_refs=["result:1"],
                ),
            )
            self.assertEqual(applied.materialized["status"], cc.COMMAND_APPLIED)

            duplicate = cc.register_command(root, envelope, actor="worker-dev")
            self.assertTrue(duplicate.committed)
            self.assertEqual(duplicate.materialized["status"], cc.COMMAND_APPLIED)
            applied_count = sum(
                1
                for entry in duplicate.materialized["acknowledgements"]
                if entry["acknowledgement"]["outcome"] == cc.COMMAND_APPLIED
            )
            self.assertEqual(applied_count, 1)

            env = dict(os.environ)
            env["COCKPIT_CONTROL_ROOT"] = str(root)
            report = subprocess.run(
                [str(BIN / "cockpit-control"), "command-status", "--command-id", command_id],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(report.returncode, 0)
            self.assertIn(f"command {command_id}", report.stdout)
            self.assertIn("status applied", report.stdout)
            self.assertIn("outcome duplicate", report.stdout)

    def test_identifier_reuse_conflict_and_fail_closed_refusals_do_not_mutate_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            command_id = str(uuid4())
            digest = cc.command_payload_digest({"value": "first"})
            cc.register_command(root, self._envelope(command_id, digest), actor="worker-dev")

            conflict = cc.register_command(
                root,
                self._envelope(command_id, cc.command_payload_digest({"value": "changed"})),
                actor="worker-dev",
            )
            self.assertTrue(conflict.conflicted)
            self.assertEqual(conflict.outcome, cc.COMMAND_FOLD_CONFLICT_RECORDED)
            self.assertEqual(
                conflict.materialized["conflicts"][-1]["reason"], cc.COMMAND_CONFLICT_DIGEST
            )

            before = _snapshot_tree(root)
            with self.assertRaisesRegex(cc.ControlStoreError, "has never registered"):
                cc.acknowledge_command(
                    root,
                    cc.build_command_acknowledgement(
                        command_id=str(uuid4()),
                        payload_digest=digest,
                        outcome=cc.COMMAND_ACCEPTED,
                        acknowledged_by="worker-dev",
                    ),
                )

            malformed = dict(self._envelope(str(uuid4()), digest))
            del malformed["boundaries"]
            with self.assertRaisesRegex(cc.ControlStoreError, "requires boundaries"):
                cc.register_command(root, malformed, actor="worker-dev")

            future = dict(self._envelope(str(uuid4()), digest))
            future["schema_version"] = cc.COMMAND_SCHEMA_VERSION + 1
            with self.assertRaisesRegex(cc.ControlStoreError, "unsupported future schema_version"):
                cc.register_command(root, future, actor="worker-dev")

            after = _snapshot_tree(root)
            self.assertEqual(_without_guard(before), _without_guard(after))

    def test_dry_run_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            command_id = str(uuid4())
            digest = cc.command_payload_digest({"dry": True})
            envelope = self._envelope(command_id, digest)
            before = _snapshot_tree(root)
            result = cc.register_command(root, envelope, actor="worker-dev", dry_run=True)
            after = _snapshot_tree(root)
            self.assertFalse(result.committed)
            self.assertEqual(result.outcome, cc.COMMAND_FOLD_REGISTERED)
            self.assertEqual(_without_guard(before), _without_guard(after))


if __name__ == "__main__":
    unittest.main()
