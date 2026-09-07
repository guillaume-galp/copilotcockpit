import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

import cockpit_control as cc
import cockpit_control_locks as control_locks


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


def _init_lock_root(root: Path):
    for directory in cc.REQUIRED_STORE_DIRECTORIES:
        (root / directory).mkdir()


class ControlLockSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.PortableControlLock, control_locks.PortableControlLock)
        self.assertIs(cc.ControlTransitionGuard, control_locks.ControlTransitionGuard)
        self.assertIs(cc.LockRepairResult, control_locks.LockRepairResult)
        self.assertEqual(cc.LOCK_OWNER_NAME, control_locks.LOCK_OWNER_NAME)

    def test_owner_acquire_and_release_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_lock_root(root)
            lock = cc.PortableControlLock(root, "test-acquire-release", timeout_seconds=0.5)
            lock.acquire()
            owner_path = root / cc.LOCKS_DIR_NAME / cc.CONTROL_LOCK_NAME / cc.LOCK_OWNER_NAME
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            self.assertEqual(owner["record_type"], "control-lock")
            self.assertEqual(owner["command"], "test-acquire-release")
            lock.release()
            self.assertFalse((root / cc.LOCKS_DIR_NAME / cc.CONTROL_LOCK_NAME).exists())

    def test_dry_run_repair_preserves_lock_and_reports_would_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_lock_root(root)
            locks = root / cc.LOCKS_DIR_NAME
            authoritative = locks / cc.CONTROL_LOCK_NAME
            authoritative.mkdir(mode=0o700)
            stale_owner = cc._new_lock_owner("stale-owner")
            stale_owner["pid"] = 999999
            stale_owner["host"] = socket.gethostname()
            (authoritative / cc.LOCK_OWNER_NAME).write_text(
                json.dumps(stale_owner), encoding="utf-8"
            )
            before = _snapshot_tree(root)
            result = cc.repair_stale_control_lock(root, dry_run=True, timeout_seconds=0.5)
            after = _snapshot_tree(root)
            self.assertEqual(result.outcome, cc.LOCK_REPAIR_WOULD_QUARANTINE)
            self.assertEqual(result.lock_id, stale_owner["lock_id"])
            self.assertIsNone(result.quarantine_path)
            before_without_guard = tuple(
                row for row in before if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}"
            )
            after_without_guard = tuple(
                row for row in after if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}"
            )
            self.assertEqual(before_without_guard, after_without_guard)

    def test_guarded_repair_fails_closed_when_lock_replaced_after_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_lock_root(root)
            locks = root / cc.LOCKS_DIR_NAME
            authoritative = locks / cc.CONTROL_LOCK_NAME
            authoritative.mkdir(mode=0o700)
            stale_owner = cc._new_lock_owner("repair-race-owner")
            stale_owner["pid"] = 999999
            stale_owner["host"] = socket.gethostname()
            (authoritative / cc.LOCK_OWNER_NAME).write_text(
                json.dumps(stale_owner), encoding="utf-8"
            )

            replacement = cc._new_lock_owner("replacement-owner")
            original_fault = cc._lock_transition_fault

            def replace(boundary, transition):
                if boundary != "repair-validated":
                    return
                displaced = locks / "control.lock.displaced"
                os.rename(transition.path, displaced)
                transition.path.mkdir(mode=0o700)
                (transition.path / cc.LOCK_OWNER_NAME).write_text(
                    json.dumps(replacement), encoding="utf-8"
                )

            cc._lock_transition_fault = replace
            try:
                with self.assertRaisesRegex(cc.ControlStoreError, "replacement retained"):
                    cc.repair_stale_control_lock(root, timeout_seconds=0.5)
            finally:
                cc._lock_transition_fault = original_fault

            retained = json.loads(
                (authoritative / cc.LOCK_OWNER_NAME).read_text(encoding="utf-8")
            )
            self.assertEqual(retained["lock_id"], replacement["lock_id"])


if __name__ == "__main__":
    unittest.main()
