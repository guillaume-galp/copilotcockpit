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
import cockpit_control_mission_control as control_mission


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


class ControlMissionControlSeamTests(unittest.TestCase):
    def _activate(self, root: Path, mission_id: str, worker: str, queue_item: str, trace: str) -> None:
        accepted = cc.build_worker_lifecycle(
            state=cc.LIFECYCLE_ACCEPTED,
            worker_id=worker,
            mission_id=mission_id,
            queue_item_id=queue_item,
            trace_id=trace,
            sequence=1,
            heartbeat_at="2026-01-01T00:00:00.000000Z",
            fresh_until="2026-01-01T00:10:00.000000Z",
        )
        result = cc.record_worker_lifecycle(root, accepted)
        self.assertTrue(result.applied)

    def _envelope(
        self,
        command_id: str,
        command_type: str,
        mission_id: str,
        queue_item_id: str,
        worker_id: str,
        trace_id: str,
        digest_payload: dict,
    ) -> dict:
        return cc.build_command_envelope(
            command_id=command_id,
            command_type=command_type,
            mission_id=mission_id,
            queue_item_id=queue_item_id,
            target_kind=cc.COMMAND_TARGET_WORKER,
            target_id=worker_id,
            trace_id=trace_id,
            payload_digest=cc.command_payload_digest(digest_payload),
            control_root="/control",
            queue_root="/queue",
            planning_root="/planning",
            implementation_roots=["/impl"],
            runtime_boundaries=["runtime:tmux"],
            created_at="2026-01-01T00:00:00.000000Z",
        )

    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.build_mission_dialog, control_mission.build_mission_dialog)
        self.assertIs(cc.build_mission_cancellation, control_mission.build_mission_cancellation)
        self.assertIs(cc.build_mission_replacement, control_mission.build_mission_replacement)
        self.assertIs(cc.build_mission_recovery, control_mission.build_mission_recovery)
        self.assertIs(cc.read_mission_state, control_mission.read_mission_state)
        self.assertIs(cc.require_active_mission, control_mission.require_active_mission)
        self.assertIs(cc.require_pending_prompt, control_mission.require_pending_prompt)
        self.assertIs(cc.register_mission_command, control_mission.register_mission_command)
        self.assertIs(cc.observe_mission_control, control_mission.observe_mission_control)
        self.assertIs(cc.MissionControlResult, control_mission.MissionControlResult)
        self.assertIs(cc.MissionControlReport, control_mission.MissionControlReport)

    def test_question_answer_access_cancel_replace_recovery_and_cli_report_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            mission_id = str(uuid4())
            worker = "worker-dev"
            queue_item = "QI-33"
            self._activate(root, mission_id, worker, queue_item, str(uuid4()))

            prompt_id = str(uuid4())
            prompt = cc.build_mission_dialog(
                command_id=prompt_id,
                kind=cc.MISSION_DIALOG_QUESTION,
                mission_id=mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                category="architecture",
                body_refs=["trace:prompt"],
            )
            prompt_result = cc.register_mission_command(
                root,
                self._envelope(
                    prompt_id,
                    cc.MISSION_DIALOG_COMMAND_TYPES[prompt["kind"]],
                    mission_id,
                    queue_item,
                    worker,
                    prompt["trace_id"],
                    {"kind": "question"},
                ),
                cc.MISSION_DIALOG_PAYLOAD_FIELD,
                prompt,
                actor=worker,
            )
            self.assertTrue(prompt_result.applied)

            access_id = str(uuid4())
            access = cc.build_mission_dialog(
                command_id=access_id,
                kind=cc.MISSION_DIALOG_ACCESS_PROMPT,
                mission_id=mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                category="filesystem",
                body_refs=["trace:access"],
            )
            cc.register_mission_command(
                root,
                self._envelope(
                    access_id,
                    cc.MISSION_DIALOG_COMMAND_TYPES[access["kind"]],
                    mission_id,
                    queue_item,
                    worker,
                    access["trace_id"],
                    {"kind": "access-prompt"},
                ),
                cc.MISSION_DIALOG_PAYLOAD_FIELD,
                access,
                actor=worker,
            )

            reply_id = str(uuid4())
            reply = cc.build_mission_dialog(
                command_id=reply_id,
                kind=cc.MISSION_DIALOG_REPLY,
                mission_id=mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                category="architecture",
                answers_command_id=prompt_id,
                body_refs=["trace:reply"],
            )
            reply_result = cc.register_mission_command(
                root,
                self._envelope(
                    reply_id,
                    cc.MISSION_DIALOG_COMMAND_TYPES[reply["kind"]],
                    mission_id,
                    queue_item,
                    worker,
                    reply["trace_id"],
                    {"kind": "reply"},
                ),
                cc.MISSION_DIALOG_PAYLOAD_FIELD,
                reply,
                actor="overseer",
            )
            self.assertTrue(reply_result.applied)

            cancel_id = str(uuid4())
            cancellation = cc.build_mission_cancellation(
                command_id=cancel_id,
                mission_id=mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                reason="bounded recovery",
                requested_at="2026-01-01T00:00:00.000000Z",
                acknowledge_deadline_at="2026-01-01T00:15:00.000000Z",
            )
            cc.register_mission_command(
                root,
                self._envelope(
                    cancel_id,
                    cc.COMMAND_TYPE_MISSION_CANCEL,
                    mission_id,
                    queue_item,
                    worker,
                    cancellation["trace_id"],
                    {"kind": "cancel"},
                ),
                cc.MISSION_CANCELLATION_PAYLOAD_FIELD,
                cancellation,
                actor="overseer",
            )

            replace_id = str(uuid4())
            replacement_mission_id = str(uuid4())
            replacement = cc.build_mission_replacement(
                command_id=replace_id,
                worker_id=worker,
                queue_item_id=queue_item,
                replaced_mission_id=mission_id,
                replacement_mission_id=replacement_mission_id,
                trace_id=str(uuid4()),
                reason="replace bounded mission",
            )
            replace_result = cc.register_mission_command(
                root,
                self._envelope(
                    replace_id,
                    cc.COMMAND_TYPE_MISSION_REPLACE,
                    mission_id,
                    queue_item,
                    worker,
                    replacement["trace_id"],
                    {"kind": "replace"},
                ),
                cc.MISSION_REPLACEMENT_PAYLOAD_FIELD,
                replacement,
                actor="overseer",
            )
            self.assertEqual(replace_result.record["replacement_mission_id"], replacement_mission_id)

            recovery_id = str(uuid4())
            recovery = cc.build_mission_recovery(
                command_id=recovery_id,
                action=cc.MISSION_RECOVERY_NUDGE,
                mission_id=replacement_mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                reason=cc.LIFECYCLE_STALE_REASON,
                respond_deadline_at="2026-01-01T00:20:00.000000Z",
                evidence_refs=["trace:recovery"],
                requested_at="2026-01-01T00:19:00.000000Z",
            )
            recovery_result = cc.register_mission_command(
                root,
                self._envelope(
                    recovery_id,
                    cc.MISSION_RECOVERY_COMMAND_TYPES[recovery["action"]],
                    replacement_mission_id,
                    queue_item,
                    worker,
                    recovery["trace_id"],
                    {"kind": "recovery"},
                ),
                cc.MISSION_RECOVERY_PAYLOAD_FIELD,
                recovery,
                actor="cockpit-overseer",
            )
            self.assertTrue(recovery_result.committed)

            env = dict(os.environ)
            env["COCKPIT_CONTROL_ROOT"] = str(root)
            report = subprocess.run(
                [str(BIN / "cockpit-control"), "mission-status", "--worker", worker],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(report.returncode, 0)
            self.assertIn("mission dialog(s)", report.stdout)
            self.assertIn(worker, report.stdout)

    def test_fail_closed_conflicts_and_future_schema_are_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            worker = "worker-dev"
            queue_item = "QI-99"
            original_mission = str(uuid4())
            other_mission = str(uuid4())
            self._activate(root, original_mission, worker, queue_item, str(uuid4()))
            self._activate(root, other_mission, "worker-ops", "QI-98", str(uuid4()))

            before = _snapshot_tree(root)
            bad_replace = cc.build_mission_replacement(
                command_id=str(uuid4()),
                worker_id=worker,
                queue_item_id=queue_item,
                replaced_mission_id=original_mission,
                replacement_mission_id=other_mission,
                trace_id=str(uuid4()),
                reason="conflict",
            )
            replace_result = cc.register_mission_command(
                root,
                self._envelope(
                    bad_replace["command_id"],
                    cc.COMMAND_TYPE_MISSION_REPLACE,
                    original_mission,
                    queue_item,
                    worker,
                    bad_replace["trace_id"],
                    {"replace": "conflict"},
                ),
                cc.MISSION_REPLACEMENT_PAYLOAD_FIELD,
                bad_replace,
                actor="overseer",
            )
            self.assertTrue(replace_result.conflicted)
            after_conflict = _snapshot_tree(root)
            self.assertNotEqual(_without_guard(before), _without_guard(after_conflict))

            future = dict(
                cc.build_mission_recovery(
                    command_id=str(uuid4()),
                    action=cc.MISSION_RECOVERY_NUDGE,
                    mission_id=original_mission,
                    worker_id=worker,
                    queue_item_id=queue_item,
                    trace_id=str(uuid4()),
                    reason=cc.LIFECYCLE_STALE_REASON,
                    respond_deadline_at="2026-01-01T01:00:00.000000Z",
                    evidence_refs=["trace:future"],
                    requested_at="2026-01-01T00:59:00.000000Z",
                )
            )
            future["schema_version"] = cc.MISSION_CONTROL_SCHEMA_VERSION + 1
            with self.assertRaisesRegex(cc.ControlStoreError, "unsupported future schema_version"):
                cc.validate_mission_recovery(future)
            after = _snapshot_tree(root)
            self.assertEqual(_without_guard(after_conflict), _without_guard(after))

    def test_dry_run_mission_command_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _initialize_root(root)
            mission_id = str(uuid4())
            worker = "worker-dev"
            queue_item = "QI-5"
            self._activate(root, mission_id, worker, queue_item, str(uuid4()))
            prompt_id = str(uuid4())
            prompt = cc.build_mission_dialog(
                command_id=prompt_id,
                kind=cc.MISSION_DIALOG_QUESTION,
                mission_id=mission_id,
                worker_id=worker,
                queue_item_id=queue_item,
                trace_id=str(uuid4()),
                category="architecture",
                body_refs=["trace:dry-run"],
            )
            before = _snapshot_tree(root)
            result = cc.register_mission_command(
                root,
                self._envelope(
                    prompt_id,
                    cc.MISSION_DIALOG_COMMAND_TYPES[prompt["kind"]],
                    mission_id,
                    queue_item,
                    worker,
                    prompt["trace_id"],
                    {"dry": True},
                ),
                cc.MISSION_DIALOG_PAYLOAD_FIELD,
                prompt,
                actor=worker,
                dry_run=True,
            )
            after = _snapshot_tree(root)
            self.assertFalse(result.committed)
            self.assertEqual(_without_guard(before), _without_guard(after))


if __name__ == "__main__":
    unittest.main()
