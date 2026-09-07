import os
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

import cockpit_control as cc
import cockpit_control_lifecycle as control_lifecycle


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


class ControlLifecycleSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.validate_worker_lifecycle, control_lifecycle.validate_worker_lifecycle)
        self.assertIs(cc.event_worker_lifecycle, control_lifecycle.event_worker_lifecycle)
        self.assertIs(cc.apply_worker_lifecycle, control_lifecycle.apply_worker_lifecycle)
        self.assertIs(cc.fold_worker_missions, control_lifecycle.fold_worker_missions)
        self.assertIs(cc.record_worker_lifecycle, control_lifecycle.record_worker_lifecycle)
        self.assertIs(cc.observe_worker_lifecycle, control_lifecycle.observe_worker_lifecycle)
        self.assertIs(cc.WorkerLifecycleObservation, control_lifecycle.WorkerLifecycleObservation)
        self.assertEqual(cc.WORKER_LIFECYCLE_TRANSITIONS, control_lifecycle.WORKER_LIFECYCLE_TRANSITIONS)

    def test_sequence_regression_is_retained_and_heartbeat_observation_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            mission = str(uuid4())
            trace = str(uuid4())
            accepted = cc.build_worker_lifecycle(
                state=cc.LIFECYCLE_ACCEPTED,
                worker_id="worker-dev",
                mission_id=mission,
                queue_item_id="QI-1",
                trace_id=trace,
                sequence=1,
                heartbeat_at="2026-01-01T00:00:00.000000Z",
                fresh_until="2026-01-01T00:01:00.000000Z",
            )
            running = cc.build_worker_lifecycle(
                state=cc.LIFECYCLE_RUNNING,
                worker_id="worker-dev",
                mission_id=mission,
                queue_item_id="QI-1",
                trace_id=trace,
                sequence=2,
                heartbeat_at="2026-01-01T00:00:30.000000Z",
                fresh_until="2026-01-01T00:02:30.000000Z",
            )
            stale_seq = dict(running)
            stale_seq["sequence"] = 1
            stale_seq["heartbeat_at"] = "2026-01-01T00:00:10.000000Z"
            stale_seq["fresh_until"] = "2026-01-01T00:01:10.000000Z"

            self.assertTrue(cc.record_worker_lifecycle(root, accepted).applied)
            self.assertTrue(cc.record_worker_lifecycle(root, running).applied)
            retained = cc.record_worker_lifecycle(root, stale_seq)
            self.assertTrue(retained.committed)
            self.assertFalse(retained.applied)
            self.assertEqual(retained.outcome, cc.LIFECYCLE_RETAINED_STALE_SEQUENCE)

            fresh = cc.observe_worker_lifecycle(root, as_of="2026-01-01T00:02:00.000000Z")
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0].observation, cc.LIFECYCLE_OBSERVATION_FRESH)
            self.assertEqual(fresh[0].sequence, 2)

            stale = cc.observe_worker_lifecycle(root, as_of="2026-01-01T00:02:30.000001Z")
            self.assertEqual(stale[0].observation, cc.LIFECYCLE_OBSERVATION_STALE)
            self.assertTrue(stale[0].recoverable)
            self.assertEqual(stale[0].reason, cc.LIFECYCLE_STALE_REASON)

    def test_fail_closed_refuses_future_schema_without_mutating_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            mission = str(uuid4())
            trace = str(uuid4())
            before = _snapshot_tree(root)
            invalid = {
                "schema_version": cc.WORKER_LIFECYCLE_SCHEMA_VERSION + 1,
                "record_type": cc.WORKER_LIFECYCLE_RECORD_TYPE,
                "state": cc.LIFECYCLE_ACCEPTED,
                "worker_id": "worker-dev",
                "mission_id": mission,
                "queue_item_id": "QI-9",
                "trace_id": trace,
                "parent_trace_id": None,
                "sequence": 1,
                "reason": None,
                "blocker": None,
                "heartbeat_at": "2026-01-01T00:00:00.000000Z",
                "fresh_until": "2026-01-01T00:01:00.000000Z",
                "evidence_refs": [],
                "superseded_by_mission_id": None,
            }
            with self.assertRaisesRegex(cc.ControlStoreError, "unsupported future schema_version"):
                cc.record_worker_lifecycle(root, invalid)
            after = _snapshot_tree(root)
            self.assertEqual(_without_guard(before), _without_guard(after))

    def test_dry_run_reports_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            mission = str(uuid4())
            trace = str(uuid4())
            lifecycle = cc.build_worker_lifecycle(
                state=cc.LIFECYCLE_ACCEPTED,
                worker_id="worker-dev",
                mission_id=mission,
                queue_item_id="QI-2",
                trace_id=trace,
                sequence=1,
                heartbeat_at="2026-01-01T00:00:00.000000Z",
                fresh_until="2026-01-01T00:01:00.000000Z",
            )
            before = _snapshot_tree(root)
            result = cc.record_worker_lifecycle(root, lifecycle, dry_run=True)
            after = _snapshot_tree(root)
            self.assertFalse(result.committed)
            self.assertEqual(result.outcome, cc.LIFECYCLE_APPLIED)
            self.assertEqual(_without_guard(before), _without_guard(after))


if __name__ == "__main__":
    unittest.main()
