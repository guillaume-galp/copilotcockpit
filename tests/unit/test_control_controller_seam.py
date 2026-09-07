import json
import os
import re
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
import cockpit_control_controller as control_controller


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
    return tuple(row for row in rows if row[0] != f"{cc.LOCKS_DIR_NAME}/{cc.CONTROL_GUARD_NAME}")


def _env(control_root: Path, queue_root: Path) -> dict:
    env = dict(os.environ)
    env["COCKPIT_CONTROL_ROOT"] = str(control_root)
    env["COCKPIT_QUEUE_ROOT"] = str(queue_root)
    return env


def _run(cmd, env):
    return subprocess.run(cmd, check=False, capture_output=True, text=True, env=env)


def _declare_queue_root(control_root: Path, queue_root: Path):
    path = control_root / cc.CONTROL_METADATA_NAME
    record = json.loads(path.read_text())
    record["canonical_roots"]["queue_root"] = str(queue_root)
    record["queue_root"] = str(queue_root)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


class ControlControllerSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.ControllerDiagnostic, control_controller.ControllerDiagnostic)
        self.assertIs(cc.ControllerEvidence, control_controller.ControllerEvidence)
        self.assertIs(cc.ControllerAction, control_controller.ControllerAction)
        self.assertIs(cc.ControllerTickResult, control_controller.ControllerTickResult)
        self.assertIs(cc.controller_tick, control_controller.controller_tick)
        self.assertIs(cc.select_controller_action, control_controller.select_controller_action)
        self.assertIs(cc.build_controller_dispatch, control_controller.build_controller_dispatch)
        self.assertIs(cc.build_controller_observation, control_controller.build_controller_observation)
        self.assertIs(cc.controller_precedence_lines, control_controller.controller_precedence_lines)
        self.assertIs(cc.report_controller_tick, control_controller.report_controller_tick)

    def test_dispatch_redelivery_then_no_action_output_stays_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            queue_root = Path(tmp) / "queue"
            queue_root.mkdir()
            env = _env(control_root, queue_root)

            self.assertEqual(_run([str(BIN / "cockpit-control"), "init"], env).returncode, 0)
            _declare_queue_root(control_root, queue_root)
            self.assertEqual(_run([str(BIN / "cockpit-control"), "replay-ledger"], env).returncode, 0)

            item = _run(
                [
                    str(BIN / "cockpit-queue"),
                    "enqueue",
                    "--text",
                    "/the-copilot-build-method deliver a change",
                    "--title",
                    "change",
                ],
                env,
            ).stdout.strip()
            _run([str(BIN / "cockpit-queue"), "start-next"], env)
            _run([str(BIN / "cockpit-queue"), "transition", item, "implementing", "--reason", "ready"], env)

            first = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:00:00.000000Z"], env
            )
            self.assertEqual(first.returncode, 0)
            self.assertIn("tick dispatched action dispatch-mission", first.stdout)
            self.assertIn("events-committed 1", first.stdout)
            self.assertIn("precedence 1 ", first.stdout)

            second = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:01:00.000000Z"], env
            )
            self.assertEqual(second.returncode, 0)
            self.assertIn("action dispatch-mission", second.stdout)
            self.assertIn("redelivery of command", second.stdout)
            self.assertIn("events-committed 0", second.stdout)

            mission_id = re.search(r"mission ([0-9a-f-]{36}) queue-item", first.stdout).group(1)
            trace_id = re.search(r"trace ([0-9a-f-]{36}) digest", first.stdout).group(1)
            accepted = _run(
                [
                    str(BIN / "cockpit-control"),
                    "record-lifecycle",
                    "--worker",
                    "worker-dev",
                    "--state",
                    "accepted",
                    "--mission",
                    mission_id,
                    "--queue-item",
                    item,
                    "--trace",
                    trace_id,
                    "--sequence",
                    "1",
                    "--heartbeat-at",
                    "2026-09-04T10:01:30.000000Z",
                    "--fresh-until",
                    "2026-09-04T10:07:00.000000Z",
                ],
                env,
            )
            self.assertEqual(accepted.returncode, 0)

            third = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:02:00.000000Z"], env
            )
            self.assertEqual(third.returncode, 0)
            self.assertIn("action record-observation", third.stdout)
            self.assertIn("precedence 6 worker-reports", third.stdout)

            fourth = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:03:00.000000Z"], env
            )
            self.assertEqual(fourth.returncode, 0)
            self.assertIn("tick unchanged action none", fourth.stdout)
            self.assertIn("events-committed 0", fourth.stdout)

    def test_stale_recovery_terminal_conflict_and_dry_run_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            control_root = Path(tmp) / "control"
            queue_root = Path(tmp) / "queue"
            queue_root.mkdir()
            env = _env(control_root, queue_root)

            self.assertEqual(_run([str(BIN / "cockpit-control"), "init"], env).returncode, 0)
            _declare_queue_root(control_root, queue_root)
            self.assertEqual(_run([str(BIN / "cockpit-control"), "replay-ledger"], env).returncode, 0)

            item = _run(
                [
                    str(BIN / "cockpit-queue"),
                    "enqueue",
                    "--text",
                    "/the-copilot-build-method deliver a change",
                    "--title",
                    "change",
                ],
                env,
            ).stdout.strip()
            _run([str(BIN / "cockpit-queue"), "start-next"], env)
            _run([str(BIN / "cockpit-queue"), "transition", item, "implementing", "--reason", "ready"], env)

            dry_before = _snapshot_tree(control_root)
            dry = _run(
                [
                    str(BIN / "cockpit-overseer"),
                    "tick",
                    "--dry-run",
                    "--as-of",
                    "2026-09-04T09:59:00.000000Z",
                ],
                env,
            )
            dry_after = _snapshot_tree(control_root)
            self.assertEqual(dry.returncode, 0)
            self.assertIn("tick would take action dispatch-mission", dry.stdout)
            self.assertEqual(_without_guard(dry_before), _without_guard(dry_after))

            dispatched = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:00:00.000000Z"], env
            )
            mission_match = re.search(r"mission ([0-9a-f-]{36}) queue-item", dispatched.stdout)
            self.assertIsNotNone(mission_match, dispatched.stdout)
            mission_id = mission_match.group(1)
            trace_id = str(uuid4())

            lifecycle_base = [str(BIN / "cockpit-control"), "record-lifecycle", "--worker", "worker-dev"]
            accepted = _run(
                lifecycle_base
                + [
                    "--state",
                    "accepted",
                    "--mission",
                    mission_id,
                    "--queue-item",
                    item,
                    "--trace",
                    trace_id,
                    "--sequence",
                    "1",
                    "--heartbeat-at",
                    "2026-09-04T10:01:00.000000Z",
                    "--fresh-until",
                    "2026-09-04T10:07:00.000000Z",
                ],
                env,
            )
            running = _run(
                lifecycle_base
                + [
                    "--state",
                    "running",
                    "--mission",
                    mission_id,
                    "--queue-item",
                    item,
                    "--trace",
                    trace_id,
                    "--sequence",
                    "2",
                    "--heartbeat-at",
                    "2026-09-04T10:02:00.000000Z",
                    "--fresh-until",
                    "2026-09-04T10:07:00.000000Z",
                ],
                env,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual(running.returncode, 0, running.stderr)

            stale = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:30:00.000000Z"], env
            )
            self.assertEqual(stale.returncode, 0)
            self.assertIn("action recover-mission outcome recovered reason stale-mission-nudged", stale.stdout)

            _run([str(BIN / "cockpit-queue"), "reject", item, "--reason", "abandoned"], env)
            terminal = _run(
                [str(BIN / "cockpit-overseer"), "tick", "--as-of", "2026-09-04T10:40:00.000000Z"], env
            )
            self.assertEqual(terminal.returncode, 0)
            self.assertIn("reason queue-item-terminal", terminal.stdout)


if __name__ == "__main__":
    unittest.main()
