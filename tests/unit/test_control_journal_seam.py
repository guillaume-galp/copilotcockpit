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
import cockpit_control_journal as control_journal


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


def _initialize_root(root: Path) -> None:
    cc.initialize_control_store(
        cc.ResolvedControlRoot(path=root, source="configured"),
    )


class ControlJournalSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.CommittedEvent, control_journal.CommittedEvent)
        self.assertIs(cc.EventHistory, control_journal.EventHistory)
        self.assertIs(cc.EventPublicationResult, control_journal.EventPublicationResult)
        self.assertIs(cc.ControlEventPublication, control_journal.ControlEventPublication)
        self.assertIs(cc.publish_control_event, control_journal.publish_control_event)
        self.assertIs(cc.read_committed_events, control_journal.read_committed_events)
        self.assertEqual(cc.EVENT_REVISION_DIGITS, control_journal.EVENT_REVISION_DIGITS)

    def test_publish_event_preserves_committed_filename_revision_and_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            published = cc.publish_control_event(
                root,
                "seam-test",
                actor="tester",
                payload={"k": "v"},
                command="test publish",
            )
            self.assertTrue(published.committed)
            self.assertEqual(published.revision, 1)
            self.assertEqual(published.path.name, cc._event_filename(1, published.event_id))
            stored = json.loads(published.path.read_text(encoding="utf-8"))
            self.assertEqual(stored, published.record)
            self.assertEqual(stored["payload"], {"k": "v"})

    def test_committed_sequence_reading_reports_pending_debris_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            first = cc.publish_control_event(root, "seed", command="seed")
            self.assertTrue(first.committed)
            debris = root / cc.PENDING_DIR_NAME / "junk.tmp"
            debris.write_text("leftover", encoding="utf-8")

            metadata = cc.validate_root_metadata(
                cc._load_json(root / cc.CONTROL_METADATA_NAME, cc.CONTROL_METADATA_NAME),
                root,
            )
            history = cc.read_committed_events(root, metadata["control_id"])
            self.assertEqual(history.latest_revision, 1)
            self.assertEqual(len(history.events), 1)
            self.assertEqual(tuple(path.name for path in history.pending), ("junk.tmp",))

    def test_dry_run_publication_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            before = _snapshot_tree(root)
            result = cc.publish_control_event(
                root,
                "dry-run",
                command="dry-run publish",
                dry_run=True,
            )
            after = _snapshot_tree(root)
            self.assertFalse(result.committed)
            self.assertEqual(result.outcome, cc.EVENT_WOULD_COMMIT)
            self.assertEqual(result.revision, 1)
            before_without_guard = tuple(
                row for row in before if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}"
            )
            after_without_guard = tuple(
                row for row in after if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}"
            )
            self.assertEqual(before_without_guard, after_without_guard)

    def test_revision_gap_refuses_mutation_and_commits_nothing_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            first = cc.publish_control_event(root, "seed", command="seed")
            events = root / cc.EVENTS_DIR_NAME
            gapped = events / cc._event_filename(2, first.event_id)
            os.rename(first.path, gapped)
            rewritten = dict(first.record)
            rewritten["revision"] = 2
            gapped.write_text(json.dumps(rewritten, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            before_names = tuple(sorted(path.name for path in events.iterdir()))
            with self.assertRaisesRegex(cc.ControlStoreError, "missing revision 1"):
                cc.publish_control_event(root, "after-gap", command="after-gap")
            after_names = tuple(sorted(path.name for path in events.iterdir()))
            self.assertEqual(before_names, after_names)

    def test_event_committed_boundary_interruption_keeps_committed_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            original_fault = cc._event_publication_fault

            def fail_after_commit(boundary, _publication):
                if boundary == "event-committed":
                    raise cc.ControlStoreError("simulated interruption")

            cc._event_publication_fault = fail_after_commit
            try:
                with self.assertRaisesRegex(cc.ControlStoreError, "simulated interruption"):
                    cc.publish_control_event(root, "boundary", command="boundary")
            finally:
                cc._event_publication_fault = original_fault

            metadata = cc.validate_root_metadata(
                cc._load_json(root / cc.CONTROL_METADATA_NAME, cc.CONTROL_METADATA_NAME),
                root,
            )
            history = cc.read_committed_events(root, metadata["control_id"])
            self.assertEqual(history.latest_revision, 1)
            self.assertEqual(len(history.events), 1)


if __name__ == "__main__":
    unittest.main()
