import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

import cockpit_control as cc
import cockpit_control_projection as control_projection


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


def _initialize_root(root: Path):
    cc.initialize_control_store(
        cc.ResolvedControlRoot(path=root, source="configured"),
    )


class ControlProjectionSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.build_ledger_projection, control_projection.build_ledger_projection)
        self.assertIs(cc.build_events_view, control_projection.build_events_view)
        self.assertIs(cc.ControlLedgerProjection, control_projection.ControlLedgerProjection)
        self.assertIs(cc.LedgerProjectionResult, control_projection.LedgerProjectionResult)
        self.assertIs(cc.replay_control_ledger, control_projection.replay_control_ledger)

    def test_dry_run_replay_is_byte_inert_and_replay_rebuilds_derived_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            cc.publish_control_event(
                root,
                "seed",
                payload={"active_queue_item_id": "QI-42"},
                command="seed",
            )
            metadata, history = cc.inspect_control_events(root)
            expected_ledger = cc._serialized_record(
                cc.build_ledger_projection(metadata, history.events)
            ).encode("utf-8")
            expected_view = cc.build_events_view(history.events).encode("utf-8")

            (root / cc.LEDGER_NAME).unlink()
            (root / cc.EVENTS_NAME).unlink()
            before = _snapshot_tree(root)
            dry_run = cc.replay_control_ledger(root, dry_run=True)
            after = _snapshot_tree(root)
            self.assertEqual(dry_run.outcome, cc.PROJECTION_WOULD_REBUILD)
            self.assertEqual(dry_run.reason, cc.PROJECTION_REASON_MISSING)
            self.assertEqual(_without_guard(before), _without_guard(after))

            rebuilt = cc.replay_control_ledger(root)
            self.assertTrue(rebuilt.rebuilt)
            self.assertEqual((root / cc.LEDGER_NAME).read_bytes(), expected_ledger)
            self.assertEqual((root / cc.EVENTS_NAME).read_bytes(), expected_view)

    def test_malformed_authoritative_event_fails_closed_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            published = cc.publish_control_event(root, "seed", command="seed")
            ledger_before = (root / cc.LEDGER_NAME).read_bytes()
            event_before = published.path.read_bytes()
            published.path.write_text("{broken", encoding="utf-8")
            before = _snapshot_tree(root)
            with self.assertRaises(cc.ControlStoreError):
                cc.replay_control_ledger(root)
            after = _snapshot_tree(root)
            self.assertEqual(_without_guard(before), _without_guard(after))
            self.assertEqual((root / cc.LEDGER_NAME).read_bytes(), ledger_before)
            self.assertNotEqual(published.path.read_bytes(), event_before)


if __name__ == "__main__":
    unittest.main()
