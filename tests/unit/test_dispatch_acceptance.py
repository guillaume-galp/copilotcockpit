import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))
import cockpit_control as cc

loader = importlib.machinery.SourceFileLoader("acceptance_overseer", str(BIN / "cockpit-overseer"))
spec = importlib.util.spec_from_loader(loader.name, loader)
overseer = importlib.util.module_from_spec(spec)
loader.exec_module(overseer)


class DispatchAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "control"
        self.queue = self.base / "queue"
        self.queue.mkdir()
        self.env = dict(os.environ, COCKPIT_CONTROL_ROOT=str(self.root),
                        COCKPIT_QUEUE_ROOT=str(self.queue))
        self.env["PATH"] = str(ROOT / "tests/transport") + os.pathsep + self.env["PATH"]
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.start = cc.utc_timestamp()
        cc.initialize_control_store(
            cc.resolve_control_root(), queue_root=str(self.queue),
            planning_root=str(self.base / "planning"),
            implementation_roots=(str(self.base / "implementation"),),
        )
        self.item = self.run_cli("cockpit-queue", "enqueue", "--text",
                                 "/the-copilot-build-method implement one change",
                                 "--title", "quoted ' title").stdout.strip()
        self.run_cli("cockpit-queue", "start-next")
        self.run_cli("cockpit-queue", "transition", self.item, "implementing",
                     "--reason", "ready")
        self.tick = cc.controller_tick(self.root, as_of=self.start)
        self.envelope = cc.read_command_slots(self.root)[self.tick.action.command_id]["envelope"]

    def at(self, seconds):
        return (datetime.fromisoformat(self.start.replace("Z", "+00:00")) +
                timedelta(seconds=seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def run_cli(self, binary, *args, check=True):
        result = subprocess.run([str(BIN / binary), *args], env=self.env,
                                text=True, capture_output=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def receipt_args(self):
        e = self.envelope
        return [str(self.root), e["command_id"], e["mission_id"], e["target"]["id"],
                e["queue_item_id"], e["trace_id"], e["payload_digest"]]

    def accept(self, **kwargs):
        return cc.accept_dispatch(*self.receipt_args(), fresh_for="300",
                                  as_of=kwargs.pop("as_of", self.at(1)), **kwargs)

    def cli_args(self):
        e = self.envelope
        return ["accept-dispatch", "--command-id", e["command_id"], "--mission", e["mission_id"],
                "--worker", e["target"]["id"], "--queue-item", e["queue_item_id"],
                "--trace", e["trace_id"], "--payload-digest", e["payload_digest"],
                "--fresh-for", "300"]

    def history(self):
        return cc.inspect_control_events(self.root)[1].events

    def snapshot(self, root=None):
        root = self.root if root is None else root
        return [(str(p.relative_to(root)), p.read_bytes())
                for p in sorted(root.rglob("*")) if p.is_file()]

    def empty_control(self, name):
        root = self.base / name
        cc.initialize_control_store(
            cc.resolve_control_root({"COCKPIT_CONTROL_ROOT": str(root)}),
            queue_root=str(self.queue), planning_root=str(self.base / "planning"),
            implementation_roots=(str(self.base / "implementation"),),
        )
        return root

    def register_action(self, root, action, created_at):
        envelope = cc.build_command_envelope(
            action.command_id, "mission-dispatch", action.mission_id, action.queue_item_id,
            "worker", action.worker_id, action.trace_id, action.payload_digest, str(root),
            queue_root=str(self.queue), planning_root=str(self.base / "planning"),
            implementation_roots=(str(self.base / "implementation"),),
            created_at=created_at, deadline_at=action.deadline_at,
        )
        dispatch = cc.build_controller_dispatch(
            action.command_id, action.mission_id, action.worker_id, action.queue_item_id,
            action.trace_id, action.reason, action.state_key,
            evidence_refs=action.evidence_refs, decided_at=created_at,
        )
        return cc.register_mission_command(root, envelope, "controller_dispatch", dispatch)

    def test_interleaved_initial_ticks_reuse_winners_complete_envelope(self):
        root = self.empty_control("interleaved")
        barrier = Barrier(2)

        def run_tick(seconds):
            tick = cc.ControllerTick(root, as_of=self.at(seconds))
            evidence = tick._gathered_evidence()
            self.assertFalse(evidence.state.claimed_slots)
            action = cc.select_controller_action(evidence)
            barrier.wait(timeout=10)
            return tick._commit(action, evidence)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_tick, seconds) for seconds in (0, 17)]
            results = [future.result(timeout=20) for future in futures]
        events = cc.inspect_control_events(root)[1].events
        self.assertEqual(len(events), 1)
        command = cc.read_command_slots(root)[results[0].action.command_id]
        envelope = events[0].record["payload"]["command_envelope"]
        self.assertEqual(command["envelope"], envelope)
        self.assertFalse(command["conflicts"])
        self.assertEqual(sum(result.applied for result in results), 1)
        for result in results:
            self.assertEqual(result.action.deadline_at, envelope["deadline_at"])
        self.assertEqual(
            cc._parsed_timestamp(envelope["deadline_at"], "deadline", "test") -
            cc._parsed_timestamp(envelope["created_at"], "created", "test"),
            timedelta(minutes=5),
        )
        receipt = cc.accept_dispatch(
            root, envelope["command_id"], envelope["mission_id"], "worker-dev",
            self.item, envelope["trace_id"], envelope["payload_digest"],
            fresh_for="300", as_of=self.at(18),
        )
        self.assertTrue(receipt.start_work)

    def test_interleaved_dispatch_digest_mismatch_is_still_a_conflict(self):
        root = self.empty_control("digest-race")
        first = cc.ControllerTick(root, as_of=self.at(0))
        first_evidence = first._gathered_evidence()
        first_action = cc.select_controller_action(first_evidence)
        path = self.queue / "items" / (self.item + ".yaml")
        item = json.loads(path.read_text())
        item["title"] = "changed brief"
        path.write_text(json.dumps(item))
        second = cc.ControllerTick(root, as_of=self.at(17))
        second_evidence = second._gathered_evidence()
        second_action = cc.select_controller_action(second_evidence)
        self.register_action(root, first_action, self.at(0))
        result = second._commit(second_action, second_evidence)
        self.assertTrue(result.command.conflicted)
        command = cc.read_command_slots(root)[first_action.command_id]
        self.assertEqual(command["envelope"]["payload_digest"], first_action.payload_digest)
        self.assertEqual(command["conflicts"][0]["reason"], cc.COMMAND_CONFLICT_DIGEST)

    def test_stale_initial_tick_cannot_extend_expired_winner_deadline(self):
        root = self.empty_control("expired-winner")
        loser = cc.ControllerTick(root, as_of=self.at(300))
        evidence = loser._gathered_evidence()
        action = cc.select_controller_action(evidence)
        cc.controller_tick(root, as_of=self.at(0))
        before = self.snapshot(root)
        with self.assertRaisesRegex(cc.ControlStoreError, "deadline changed or expired"):
            loser._commit(action, evidence)
        self.assertEqual(self.snapshot(root), before)
        command = cc.read_command_slots(root)[action.command_id]
        self.assertEqual(command["envelope"]["deadline_at"], self.at(300))
        self.assertFalse(command["conflicts"])
        blocked = cc.controller_tick(root, as_of=self.at(300))
        self.assertEqual(blocked.action.reason, cc.CONTROLLER_REASON_ACCEPTANCE_EXPIRED)

    def test_stale_dispatch_commit_cannot_publish_after_worker_acceptance(self):
        tick = cc.ControllerTick(self.root, as_of=self.at(2))
        evidence = tick._gathered_evidence()
        action = cc.select_controller_action(evidence)
        self.accept()
        before = self.snapshot()
        with self.assertRaisesRegex(cc.ControlStoreError, "claim changed"):
            tick._commit(action, evidence)
        self.assertEqual(self.snapshot(), before)

    def test_refused_dispatch_standalone_acceptance_cannot_materialize_or_poison(self):
        refused = replace(self.tick.action, command_id=str(uuid4()), mission_id=str(uuid4()))
        result = self.register_action(self.root, refused, self.at(0))
        self.assertFalse(result.applied)
        before = cc.read_mission_state(self.root)
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", refused.mission_id, self.item, refused.trace_id, 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        publication = cc.publish_control_event(
            self.root, "worker-lifecycle-accepted", payload={"worker_lifecycle": lifecycle},
        )
        state = cc.read_mission_state(self.root)
        self.assertFalse(state.lifecycle_outcomes[publication.event_id][0])
        self.assertEqual(state.missions, before.missions)
        self.assertEqual(state.worker_slots, before.worker_slots)
        self.assertFalse(state.contested_slots)
        self.assertEqual(cc.read_command_slots(self.root)[refused.command_id]["status"], "registered")
        self.assertTrue(self.accept().start_work)

    def test_wrong_worker_standalone_acceptance_cannot_poison_rightful_receipt(self):
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-test", self.envelope["mission_id"], self.item,
            self.envelope["trace_id"], 1, heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        before = cc.read_mission_state(self.root)
        publication = cc.publish_control_event(
            self.root, "worker-lifecycle-accepted", payload={"worker_lifecycle": lifecycle},
        )
        state = cc.read_mission_state(self.root)
        self.assertFalse(state.lifecycle_outcomes[publication.event_id][0])
        self.assertEqual(state.worker_slots, before.worker_slots)
        self.assertFalse(state.missions)
        self.assertFalse(state.contested_slots)
        self.assertTrue(self.accept().start_work)

    def overlapping_phase_actions(self, root):
        dev = cc.ControllerTick(root, as_of=self.at(0))
        dev_evidence = dev._gathered_evidence()
        dev_action = cc.select_controller_action(dev_evidence)
        self.run_cli("cockpit-queue", "transition", self.item, "testing", "--reason", "ready")
        test = cc.ControllerTick(root, as_of=self.at(17))
        test_evidence = test._gathered_evidence()
        test_action = cc.select_controller_action(test_evidence)
        self.assertEqual(dev_action.worker_id, "worker-dev")
        self.assertEqual(test_action.worker_id, "worker-test")
        return (dev, dev_evidence, dev_action), (test, test_evidence, test_action)

    def test_overlapping_phase_ticks_cannot_publish_a_stale_queue_claim(self):
        root = self.empty_control("phase-race")
        dev, test = self.overlapping_phase_actions(root)
        winner = test[0]._commit(test[2], test[1])
        self.assertTrue(winner.applied)
        before = self.snapshot(root)
        with self.assertRaisesRegex(cc.ControlStoreError, "changed.*rerun tick"):
            dev[0]._commit(dev[2], dev[1])
        self.assertEqual(self.snapshot(root), before)
        self.assertEqual(len(cc.read_mission_state(root).claimed_slots), 1)

    def test_stale_tick_cannot_add_another_claim_for_already_reserved_queue(self):
        root = self.empty_control("queue-claim-race")
        dev, test = self.overlapping_phase_actions(root)
        self.register_action(root, dev[2], self.at(0))
        before = self.snapshot(root)
        with self.assertRaisesRegex(cc.ControlStoreError, "changed.*rerun tick"):
            test[0]._commit(test[2], test[1])
        self.assertEqual(self.snapshot(root), before)
        self.assertEqual(cc.read_mission_state(root).worker_slots["worker-dev"]["state"], "reserved")

    def test_existing_old_reservation_expiry_cannot_mask_current_accepted_work(self):
        root = self.empty_control("historical-phase-race")
        dev, test = self.overlapping_phase_actions(root)
        self.register_action(root, dev[2], self.at(0))
        self.register_action(root, test[2], self.at(17))
        self.assertEqual(len(cc.read_mission_state(root).claimed_slots), 2)
        action = test[2]
        self.assertTrue(cc.accept_dispatch(
            root, action.command_id, action.mission_id, action.worker_id, self.item,
            action.trace_id, action.payload_digest, fresh_for="600", as_of=self.at(18),
        ).start_work)
        result = cc.controller_tick(root, as_of=self.at(300))
        self.assertEqual(result.action.reason, cc.CONTROLLER_REASON_MISSION_IN_PROGRESS)
        self.assertEqual(result.action.worker_id, "worker-test")
        self.assertEqual(result.action.mission_id, action.mission_id)
        self.assertEqual(cc.read_mission_state(root).worker_slots["worker-dev"]["state"], "reserved")
        self.assertEqual(cc.controller_tick(root, as_of=self.at(301)).action.kind,
                         cc.CONTROLLER_ACTION_NONE)
        self.assertEqual(len(cc.inspect_control_events(root)[1].events), 4)

    def test_wrong_worker_event_cannot_poison_that_workers_separate_reservation(self):
        root = self.empty_control("wrong-worker-reserved")
        dev, test = self.overlapping_phase_actions(root)
        self.register_action(root, dev[2], self.at(0))
        self.register_action(root, test[2], self.at(17))
        before = cc.read_mission_state(root)
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-test", dev[2].mission_id, self.item, dev[2].trace_id, 1,
            heartbeat_at=self.at(18), fresh_until=self.at(318),
        )
        publication = cc.publish_control_event(
            root, "worker-lifecycle-accepted", payload={"worker_lifecycle": lifecycle},
        )
        state = cc.read_mission_state(root)
        self.assertFalse(state.lifecycle_outcomes[publication.event_id][0])
        self.assertFalse(state.missions)
        self.assertEqual(state.worker_slots, before.worker_slots)
        action = test[2]
        self.assertTrue(cc.accept_dispatch(
            root, action.command_id, action.mission_id, action.worker_id, self.item,
            action.trace_id, action.payload_digest, fresh_for="300", as_of=self.at(19),
        ).start_work)

    def test_atomic_receipt_cold_restart_and_duplicate_do_not_start_twice(self):
        first = self.run_cli("cockpit-control", *self.cli_args())
        receipt = json.loads(first.stdout)
        self.assertEqual(receipt["outcome"], "accepted")
        self.assertTrue(receipt["start_work"])
        events = self.history()
        self.assertEqual(len(events), 2)
        payload = events[-1].record["payload"]
        self.assertEqual(set(payload), {"command_acknowledgement", "worker_lifecycle"})
        self.assertEqual(payload["worker_lifecycle"]["sequence"], 1)
        commands, _ = cc.fold_commands(events)
        state = cc.fold_mission_state(events)
        command = commands[self.envelope["command_id"]]
        mission = state.missions[self.envelope["mission_id"]]
        self.assertEqual(command["status"], "accepted")
        self.assertEqual(command["acknowledgements"][0]["event_id"], mission["event_id"])
        self.assertEqual(state.worker_slots["worker-dev"]["state"], "active")
        before = self.snapshot()
        duplicate = json.loads(self.run_cli("cockpit-control", *self.cli_args()).stdout)
        self.assertEqual(duplicate["outcome"], "duplicate")
        self.assertFalse(duplicate["start_work"])
        self.assertEqual(duplicate["event_id"], receipt["event_id"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.accept(as_of=self.at(301)).outcome, "duplicate")

    def test_bad_identities_and_digests_fail_closed(self):
        for index, bad in ((1, str(uuid4())), (2, str(uuid4())), (3, "worker-fix"),
                           (4, "foreign-item"), (5, str(uuid4())),
                           (6, "sha256:" + "0" * 64), (1, "not-a-uuid"),
                           (3, "worker-dev\nforged"), (6, "garbage")):
            with self.subTest(index=index, bad=bad):
                args = self.receipt_args()
                args[index] = bad
                with self.assertRaises(cc.ControlStoreError):
                    cc.accept_dispatch(*args, fresh_for="300", as_of=self.at(1))
                self.assertEqual(len(self.history()), 1)

    def test_cli_malformed_input_and_dry_run_are_read_only(self):
        before = self.snapshot()
        result = self.run_cli("cockpit-control", *self.cli_args(), "--dry-run")
        self.assertEqual(json.loads(result.stdout)["outcome"], "would-accept")
        self.assertFalse(json.loads(result.stdout)["start_work"])
        self.assertEqual(self.snapshot(), before)
        for value in ("nan", "inf", "-1", "0", "garbage", "1e300"):
            args = self.cli_args()
            args[-1] = value
            result = self.run_cli("cockpit-control", *args, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(len(self.history()), 1)

    def test_deadline_is_fixed_and_tick_stops_delivery_forever(self):
        retry = cc.controller_tick(self.root, as_of=self.at(299))
        self.assertEqual(retry.action.command_id, self.envelope["command_id"])
        self.assertEqual(retry.action.deadline_at, self.at(300))
        self.assertEqual(len(self.history()), 1)
        before = self.snapshot()
        dry = cc.controller_tick(self.root, as_of=self.at(300), dry_run=True)
        self.assertEqual(dry.action.reason, cc.CONTROLLER_REASON_ACCEPTANCE_EXPIRED)
        self.assertEqual(self.snapshot(), before)
        expired = cc.controller_tick(self.root, as_of=self.at(300))
        self.assertTrue(expired.action.blocking)
        self.assertIn("explicit", cc.controller_blocking_repair(expired))
        for seconds in (301, 600, 3600, 86400):
            tick = cc.controller_tick(self.root, as_of=self.at(seconds))
            self.assertEqual(tick.action.kind, cc.CONTROLLER_ACTION_NONE)
            self.assertEqual(len(self.history()), 2)
        with self.assertRaises(cc.ControlStoreError):
            self.accept(as_of=self.at(300))
        state = cc.fold_mission_state(self.history())
        self.assertFalse(state.missions)
        self.assertEqual(state.worker_slots["worker-dev"]["state"], "reserved")

    def test_accept_just_before_deadline_wins_against_stale_retry(self):
        retry = cc.controller_tick(self.root, as_of=self.at(299))
        self.assertTrue(self.accept(as_of=self.at(299)).start_work)
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaisesRegex(RuntimeError, "no longer awaiting delivery"):
                overseer.deliver_controller_dispatch(retry, "isolated", as_of=self.at(299))
            transport.assert_not_called()
        tick = cc.controller_tick(self.root, as_of=self.at(300))
        self.assertEqual(tick.action.reason, cc.CONTROLLER_REASON_MISSION_IN_PROGRESS)

    def test_delayed_transport_rechecks_deadline_and_brief(self):
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaisesRegex(RuntimeError, "expired"):
                overseer.deliver_controller_dispatch(self.tick, "isolated", as_of=self.at(300))
            transport.assert_not_called()
        self.run_cli("cockpit-queue", "reject", self.item, "--reason", "not wanted")
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaisesRegex(RuntimeError, "authority changed"):
                overseer.deliver_controller_dispatch(self.tick, "isolated", as_of=self.at(1))
            transport.assert_not_called()
        with self.assertRaises(cc.ControlStoreError):
            self.accept()

    def test_brief_contains_exact_executable_receipt_and_boundaries(self):
        brief = overseer.controller_dispatch_brief(self.tick, self.envelope)
        self.assertIn("start_work=true", brief)
        self.assertIn("duplicate/start_work=false", brief)
        self.assertIn(self.at(300), brief)
        command = next(line for line in brief.splitlines() if line.startswith("env "))
        import shlex
        argv = shlex.split(command)
        argv[2] = str(BIN / "cockpit-control")
        result = subprocess.run(argv, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["start_work"])

    def test_foreign_joint_event_cannot_apply_either_half(self):
        e = self.envelope
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-fix", e["mission_id"], self.item, e["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        ack = cc.build_command_acknowledgement(e["command_id"], e["payload_digest"],
                                               "accepted", "worker-fix", acknowledged_at=self.at(1))
        cc.publish_control_event(self.root, "worker-lifecycle-accepted", actor="worker-fix",
                                 payload={"worker_lifecycle": lifecycle, "command_acknowledgement": ack})
        self.assertFalse(cc.fold_mission_state(self.history()).missions)
        self.assertEqual(cc.read_command_slots(self.root)[e["command_id"]]["status"], "registered")
        self.assertTrue(self.accept().start_work)

    def test_generic_ack_and_lifecycle_cannot_bypass_joint_acceptance(self):
        e = self.envelope
        ack = cc.build_command_acknowledgement(e["command_id"], e["payload_digest"],
                                               "accepted", "worker-dev", acknowledged_at=self.at(1))
        with self.assertRaisesRegex(cc.ControlStoreError, "accept-dispatch"):
            cc.acknowledge_command(self.root, ack)
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", e["mission_id"], self.item, e["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        cc.publish_control_event(self.root, "worker-lifecycle-accepted",
                                 payload={"worker_lifecycle": lifecycle})
        cc.publish_control_event(self.root, "command-acknowledged-accepted",
                                 payload={"command_acknowledgement": ack})
        self.assertFalse(cc.fold_mission_state(self.history()).missions)
        self.assertEqual(cc.read_command_slots(self.root)[e["command_id"]]["status"], "registered")
        self.assertTrue(self.accept().start_work)

    def test_changed_brief_and_boundaries_refuse_receipt_without_events(self):
        item_path = self.queue / "items" / (self.item + ".yaml")
        original = item_path.read_text()
        data = json.loads(original)
        data["title"] = "different title"
        item_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(cc.ControlStoreError, "digest"):
            self.accept()
        item_path.write_text(original)
        metadata_path = self.root / "control.json"
        metadata = json.loads(metadata_path.read_text())
        metadata["planning_root"] = str(self.base / "foreign-planning")
        metadata["canonical_roots"]["planning_root"] = metadata["planning_root"]
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(cc.ControlStoreError, "boundaries"):
            self.accept()
        self.assertEqual(len(self.history()), 1)

    def test_terminal_receipts_cannot_restart_work(self):
        self.accept()
        e = self.envelope
        for sequence, state in ((2, "running"), (3, "cancelled")):
            lifecycle = cc.build_worker_lifecycle(
                state, "worker-dev", e["mission_id"], self.item, e["trace_id"], sequence,
                heartbeat_at=self.at(2) if state == "running" else None,
                fresh_until=self.at(302) if state == "running" else None,
                reason="operator cancelled" if state == "cancelled" else None,
            )
            cc.record_worker_lifecycle(self.root, lifecycle)
        with self.assertRaises(cc.ControlStoreError):
            self.accept()
        replacement = str(uuid4())
        altered = replace(self.tick.action, mission_id=replacement)
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaises(RuntimeError):
                overseer.deliver_controller_dispatch(replace(self.tick, action=altered),
                                                     "isolated", as_of=self.at(3))
            transport.assert_not_called()

    def test_replaced_mission_receipt_cannot_claim_replacement_slot(self):
        self.accept()
        e = self.envelope
        self.run_cli(
            "cockpit-control", "replace-mission", "--command-id", str(uuid4()),
            "--worker", "worker-dev", "--mission", e["mission_id"],
            "--replacement-mission", str(uuid4()), "--queue-item", self.item,
            "--trace", e["trace_id"], "--reason", "explicit replacement",
            "--payload", "{}",
        )
        self.assertEqual(cc.read_mission_state(self.root).missions[e["mission_id"]]["lifecycle"]["state"],
                         "replaced")
        with self.assertRaises(cc.ControlStoreError):
            self.accept()

    def test_old_deadline_free_reservation_is_blocked_not_migrated(self):
        root = self.base / "legacy"
        cc.initialize_control_store(
            cc.resolve_control_root({"COCKPIT_CONTROL_ROOT": str(root)}),
            queue_root=str(self.queue), planning_root=str(self.base / "planning"),
            implementation_roots=(str(self.base / "implementation"),),
        )
        e = dict(self.envelope, deadline_at=None,
                 boundaries=dict(self.envelope["boundaries"], control_root=str(root)))
        dispatch = self.history()[0].record["payload"]["controller_dispatch"]
        cc.register_mission_command(root, e, "controller_dispatch", dispatch)
        tick = cc.controller_tick(root, as_of=self.at(1))
        self.assertEqual(tick.action.reason, cc.CONTROLLER_REASON_ACCEPTANCE_UNSUPPORTED)
        args = self.receipt_args()
        args[0] = root
        before = self.snapshot(root)
        with self.assertRaisesRegex(cc.ControlStoreError, "no acceptance deadline"):
            cc.accept_dispatch(*args, fresh_for="300", as_of=self.at(1))
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", e["mission_id"], self.item, e["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(cc.ControlStoreError, "explicit operator recovery"):
                    cc.record_worker_lifecycle(root, lifecycle, dry_run=dry_run)
                self.assertEqual(self.snapshot(root), before)
        self.assertEqual(cc.controller_tick(root, as_of=self.at(600)).action.kind,
                         cc.CONTROLLER_ACTION_NONE)
        self.assertEqual(len(cc.inspect_control_events(root)[1].events), 2)

    def test_already_committed_deadline_free_acceptance_history_still_replays(self):
        root = self.empty_control("legacy-history")
        envelope = dict(
            self.envelope, deadline_at=None,
            boundaries=dict(self.envelope["boundaries"], control_root=str(root)),
        )
        dispatch = self.history()[0].record["payload"]["controller_dispatch"]
        cc.register_mission_command(root, envelope, "controller_dispatch", dispatch)
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", envelope["mission_id"], self.item, envelope["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        committed = cc.publish_control_event(
            root, "worker-lifecycle-accepted", payload={"worker_lifecycle": lifecycle},
        )
        state = cc.read_mission_state(root)
        self.assertTrue(state.lifecycle_outcomes[committed.event_id][0])
        self.assertEqual(state.worker_slots["worker-dev"]["state"], "active")
        cc.replay_control_ledger(root)
        self.assertEqual(cc.read_mission_state(root), state)

    def test_concurrent_receipts_authorize_exactly_one_worker_execution(self):
        processes = [
            subprocess.Popen([str(BIN / "cockpit-control"), *self.cli_args()],
                             env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sorted(result["outcome"] for result in results), ["accepted", "duplicate"])
        self.assertEqual(sum(result["start_work"] for result in results), 1)
        self.assertEqual(len(self.history()), 2)

    def test_transport_holds_lock_until_input_finishes_before_acceptance(self):
        processes = []
        def transport(operation, *args):
            self.assertTrue((self.root / "locks/control.lock").exists())
            if operation == "load-buffer":
                code = (
                    "import json,sys; sys.path.insert(0,sys.argv[1]); import cockpit_control as cc;"
                    "print('ready',flush=True);"
                    "r=cc.accept_dispatch(*json.loads(sys.argv[2]),fresh_for='300');"
                    "print(r.outcome,flush=True)"
                )
                process = subprocess.Popen(
                    [sys.executable, "-c", code, str(BIN), json.dumps(self.receipt_args())],
                    env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                processes.append(process)
                self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertEqual(len(self.history()), 1)
        with patch.object(overseer, "tmux", side_effect=transport):
            overseer.deliver_controller_dispatch(self.tick, "isolated")
        stdout, stderr = processes[0].communicate(timeout=15)
        self.assertEqual(processes[0].returncode, 0, stderr)
        self.assertEqual(stdout.strip(), "accepted")
        self.assertEqual(len(self.history()), 2)

    def test_expiry_observation_cannot_overwrite_racing_acceptance(self):
        tick = cc.ControllerTick(self.root, as_of=self.at(300))
        evidence = tick._gathered_evidence()
        action = cc.select_controller_action(evidence)
        self.accept(as_of=self.at(299))
        with self.assertRaisesRegex(cc.ControlStoreError, "reservation changed"):
            tick._commit(action, evidence)
        self.assertEqual(len(self.history()), 2)

    def test_interruption_before_rename_commits_neither_half(self):
        def interrupt(stage, _publication):
            if stage == "candidate-validated":
                raise cc.ControlStoreError("simulated interruption before commit")
        with patch.object(cc, "_event_publication_fault", interrupt):
            with self.assertRaisesRegex(cc.ControlStoreError, "simulated"):
                self.accept()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(cc.read_command_slots(self.root)[self.envelope["command_id"]]["status"],
                         "registered")
        self.assertFalse(cc.read_mission_state(self.root).missions)

    def test_missing_committed_receipt_is_not_reaccepted_from_a_shortened_journal(self):
        self.accept()
        self.history()[-1].path.unlink()
        with self.assertRaisesRegex(cc.ControlStoreError, "missing committed history"):
            self.accept()
        self.assertEqual(len(self.history()), 1)

    def test_interruption_after_rename_replays_both_and_never_restarts(self):
        def interrupt(stage, _publication):
            if stage == "event-committed":
                raise cc.ControlStoreError("simulated lost receipt after commit")
        with patch.object(cc, "_event_publication_fault", interrupt):
            with self.assertRaisesRegex(cc.ControlStoreError, "simulated"):
                self.accept()
        self.assertEqual(len(self.history()), 2)
        cc.replay_control_ledger(self.root)
        self.assertEqual(self.accept().outcome, "duplicate")
        self.assertEqual(len(self.history()), 2)


if __name__ == "__main__":
    unittest.main()
