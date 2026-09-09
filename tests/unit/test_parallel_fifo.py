import copy
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.unit.test_control_controller_seam import BIN, ROOT, _declare_queue_root, _env, _run, _snapshot_tree
import cockpit_control as cc
import cockpit_control_controller as controller
from cockpit_footprint import footprints_conflict, require_footprint_authority, validate_footprint

loader = importlib.machinery.SourceFileLoader("parallel_overseer", str(BIN / "cockpit-overseer"))
spec = importlib.util.spec_from_loader(loader.name, loader)
overseer = importlib.util.module_from_spec(spec)
loader.exec_module(overseer)
NOW = "2026-09-04T10:00:00.000000Z"


class ParallelFIFOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.control = self.base / "control"
        self.queue = self.base / "queue"
        self.implementation = self.base / "implementation"
        self.implementation.mkdir()
        self.queue.mkdir()
        self.env = _env(self.control, self.queue)
        self.env["HOME"] = str(self.base / "home")
        self.env.pop("TMUX", None)
        self.env.pop("TMUX_SESSION", None)
        self.run_cli("cockpit-control", "init")
        _declare_queue_root(self.control, self.queue)
        self.run_cli("cockpit-control", "replay-ledger")

    def run_cli(self, tool, *args, ok=True):
        result = _run([str(BIN / tool), *args], self.env)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def scope(self, name, worker="worker-dev"):
        repo = self.implementation / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, env=self.env)
        return validate_footprint({
            "version": 1, "status": "reviewed", "rationale": "Reviewed independent outputs and runtime",
            "repositories": [{"path": str(repo), "upstream": "example/" + name, "branch": "main"}],
            "write_paths": [str(self.base / "planning" / name)],
            "resources": [], "workers": {"worker-dev": worker},
        }, live=True, normalize=True)

    def enqueue(self, name, footprint=None, activate=True):
        args = ["enqueue", "--id", name, "--text", "/the-copilot-build-method " + name]
        if footprint is not None:
            source = self.base / (name + ".json")
            source.write_text(json.dumps(footprint))
            args += ["--footprint", str(source)]
        self.run_cli("cockpit-queue", *args)
        if activate:
            self.run_cli("cockpit-queue", "start-next")
            self.run_cli("cockpit-queue", "transition", name, "implementing", "--reason", "ready")
        return name

    def tick(self, **kwargs):
        return cc.controller_tick(self.control, environ=self.env, as_of=NOW, **kwargs)

    def envelope(self, result):
        return cc.read_command_slots(self.control)[result.action.command_id]["envelope"]

    def accept(self, envelope, **kwargs):
        return cc.accept_dispatch(
            self.control, envelope["command_id"], envelope["mission_id"], envelope["target"]["id"],
            envelope["queue_item_id"], envelope["trace_id"], envelope["payload_digest"],
            fresh_for="300", as_of=NOW, **kwargs,
        )

    def edit_item(self, item_id, **changes):
        path = self.queue / "items" / (item_id + ".yaml")
        record = json.loads(path.read_text())
        record.update(changes)
        path.write_text(json.dumps(record))

    def complete(self, envelope):
        identity = ["--mission", envelope["mission_id"], "--worker", envelope["target"]["id"],
                    "--queue-item", envelope["queue_item_id"], "--trace", envelope["trace_id"]]
        self.run_cli("cockpit-control", "record-lifecycle", "--state", "running", *identity,
                     "--sequence", "2", "--heartbeat-at", NOW, "--fresh-for", "300")
        self.run_cli("cockpit-control", "record-lifecycle", "--state", "completed", *identity,
                     "--sequence", "3", "--evidence", "queue:" + envelope["queue_item_id"])

    def test_completed_parallel_item_is_not_redispatched_during_queue_handoff(self):
        self.enqueue("QI-a", self.scope("repo-a"))
        self.enqueue("QI-b", self.scope("repo-b", "worker-dev-2"))
        a, b = self.envelope(self.tick()), self.envelope(self.tick())
        self.accept(a)
        self.accept(b)
        self.complete(a)
        b_slot = copy.deepcopy(cc.read_mission_state(self.control).worker_slots["worker-dev-2"])
        for phase in ("implementing", "delivered"):
            self.run_cli("cockpit-queue", "transition", "QI-a", phase, "--reason", "handoff interval")
            for _ in range(2):
                result = self.tick()
                self.assertNotEqual(result.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
                self.assertEqual(len(cc.read_command_slots(self.control)), 2)
                state = cc.read_mission_state(self.control)
                self.assertEqual(state.worker_slots["worker-dev-2"], b_slot)
                self.assertEqual(state.worker_slots["worker-dev"]["state"], cc.MISSION_SLOT_RELEASED)
        # A's completion neither occupies the free worker nor hides independent FIFO work.
        self.enqueue("QI-c", self.scope("repo-c"))
        result = self.tick()
        self.assertEqual(result.action.queue_item_id, "QI-c")
        self.assertEqual(result.action.worker_id, "worker-dev")
        self.assertEqual(result.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(cc.read_mission_state(self.control).worker_slots["worker-dev-2"], b_slot)

    def test_single_completed_legacy_item_and_stale_tick_cannot_dispatch_again(self):
        self.enqueue("QI-a")
        stale_tick = cc.ControllerTick(self.control, environ=self.env, as_of=NOW)
        evidence = stale_tick._gathered_evidence()
        action = cc.select_controller_action(evidence)
        envelope = self.envelope(self.tick())
        self.accept(envelope)
        self.complete(envelope)
        before = _snapshot_tree(self.base)
        with self.assertRaisesRegex(cc.ControlStoreError, "already has terminal lifecycle"):
            stale_tick._commit(action, evidence)
        self.assertEqual(_snapshot_tree(self.base), before)
        result = self.tick()
        self.assertEqual(result.action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.assertEqual(self.tick().action.kind, cc.CONTROLLER_ACTION_NONE)
        self.run_cli("cockpit-control", "replay-ledger")
        self.assertEqual(self.tick().action.kind, cc.CONTROLLER_ACTION_NONE)
        self.assertEqual(len(cc.read_command_slots(self.control)), 1)
        self.assertFalse(cc.read_mission_state(self.control).claimed_slots)

    def test_completed_role_cannot_be_retried_by_changing_instance_but_next_role_can_run(self):
        footprint = self.scope("repo-a")
        self.enqueue("QI-a", footprint)
        envelope = self.envelope(self.tick())
        self.accept(envelope)
        self.complete(envelope)
        footprint["workers"]["worker-dev"] = "worker-dev-3"
        self.edit_item("QI-a", footprint=footprint)
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.assertEqual(len(cc.read_command_slots(self.control)), 1)
        self.run_cli("cockpit-queue", "transition", "QI-a", "testing", "--reason", "next role")
        result = self.tick()
        self.assertEqual(result.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(result.action.worker_id, "worker-test")
        self.assertTrue(self.accept(self.envelope(result)).start_work)

    def test_inspected_reservation_recovery_authorizes_one_new_attempt_not_completed_retries(self):
        self.enqueue("QI-a", self.scope("repo-a"))
        first = self.envelope(self.tick())
        cc.recover_dispatch(
            self.control, first["command_id"], str(uuid4()), "worker-dev",
            inspected_safe=True, operator="operator", reason="fixture confirms no worker started",
            evidence_refs=("test:inspection",),
        )
        retry = self.tick()
        self.assertEqual(retry.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertNotEqual(retry.action.mission_id, first["mission_id"])
        envelope = self.envelope(retry)
        self.assertTrue(self.accept(envelope).start_work)
        self.complete(envelope)
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.assertFalse(cc.read_mission_state(self.control).claimed_slots)

    def assert_fix_phase_handoff(self, legacy=False):
        self.enqueue("QI-a", self.scope("repo-a"))
        self.run_cli("cockpit-queue", "transition", "QI-a", "fixing", "--reason", "first fix phase")
        build_dispatch = controller.build_controller_dispatch
        def build_record(*args, **kwargs):
            if legacy:
                kwargs.pop("queue_item_state", None)
            return build_dispatch(*args, **kwargs)
        with patch.object(controller, "build_controller_dispatch", side_effect=build_record):
            first_result = self.tick()
        first = self.envelope(first_result)
        event = cc.inspect_control_events(self.control)[1].events[0]
        original_bytes = event.path.read_bytes()
        record = event.record["payload"][cc.CONTROLLER_DISPATCH_PAYLOAD_FIELD]
        if legacy:
            self.assertNotIn("queue_item_state", record)
        else:
            self.assertEqual(record["queue_item_state"], "fixing")
            for phase in ("testing", "unknown-future-phase"):
                with self.assertRaises(cc.ControlStoreError):
                    cc.validate_controller_dispatch(dict(record, queue_item_state=phase))
        self.accept(first)
        self.complete(first)
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.assertEqual(len(cc.read_command_slots(self.control)), 1)
        self.run_cli("cockpit-control", "replay-ledger")
        # Old phase authority cannot be inferred from the current mutable brief.
        self.edit_item("QI-a", title="New E2E phase", source_text="Different reviewed brief for E2E fixing")
        self.run_cli("cockpit-queue", "transition", "QI-a", "e2e-related-fixing", "--reason", "E2E findings")
        second_result = self.tick()
        self.assertEqual(second_result.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(second_result.action.worker_id, "worker-fix")
        self.assertNotEqual(second_result.action.mission_id, first["mission_id"])
        second = self.envelope(second_result)
        item = cc.observe_queue(self.queue).active[0]
        self.assertEqual(second["payload_digest"], cc.dispatch_payload_digest(
            item, second["mission_id"], "worker-fix", second["trace_id"],
        ))
        self.accept(second)
        self.complete(second)
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.run_cli("cockpit-queue", "transition", "QI-a", "fixing", "--reason", "old phase still fenced")
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_ROLE_TERMINAL)
        self.assertEqual(len(cc.read_command_slots(self.control)), 2)
        state = cc.read_mission_state(self.control)
        self.assertEqual(state.missions[first["mission_id"]]["lifecycle"]["state"], cc.LIFECYCLE_COMPLETED)
        self.assertEqual(state.missions[second["mission_id"]]["lifecycle"]["state"], cc.LIFECYCLE_COMPLETED)
        self.assertEqual(event.path.read_bytes(), original_bytes)

    def test_completed_fixing_permits_distinct_e2e_fixing_phase_on_same_worker(self):
        self.assert_fix_phase_handoff()

    def test_legacy_dispatch_phase_is_recovered_from_immutable_state_key(self):
        self.assert_fix_phase_handoff(legacy=True)

    def test_two_same_role_reservations_accept_delivery_and_replay(self):
        a, b = self.scope("repo-a"), self.scope("repo-b", "worker-dev-2")
        self.enqueue("QI-a", a)
        self.enqueue("QI-b", b)
        before = _snapshot_tree(self.base)
        preview = self.tick(dry_run=True)
        self.assertEqual(preview.outcome, cc.CONTROLLER_TICK_WOULD_DISPATCH)
        self.assertEqual(before, _snapshot_tree(self.base))
        first = self.tick()
        second = self.tick()
        self.assertEqual(first.action.worker_id, "worker-dev")
        self.assertEqual(second.action.worker_id, "worker-dev-2")
        self.assertNotEqual(first.action.mission_id, second.action.mission_id)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 2)
        # Delivery uses only stubbed transport, never a real pane.
        for result, footprint in ((first, a), (second, b)):
            envelope = self.envelope(result)
            with patch.object(overseer, "tmux") as transport:
                overseer.deliver_controller_dispatch(result, "test-session", as_of=NOW)
                self.assertIn(
                    unittest.mock.call("send-keys", "-t", "test-session:" + result.action.worker_id, "Enter"),
                    transport.call_args_list,
                )
            brief = overseer.controller_dispatch_brief(result, envelope)
            self.assertIn(json.dumps(envelope["boundaries"], sort_keys=True), brief)
            self.assertEqual(envelope["boundaries"]["mission_footprint"], footprint)
            self.assertTrue(self.accept(envelope).start_work)
            self.assertFalse(self.accept(envelope).start_work)
        state = cc.read_mission_state(self.control)
        self.assertTrue(all(slot["state"] == cc.MISSION_SLOT_ACTIVE for _, slot in state.claimed_slots))
        self.run_cli("cockpit-control", "replay-ledger")
        self.assertEqual(state.worker_slots, cc.read_mission_state(self.control).worker_slots)

    def test_unknown_draft_and_fifo_no_bypass_or_transition_bypass(self):
        self.enqueue("QI-a", self.scope("repo-a"))
        draft = self.scope("repo-b", "worker-dev-2")
        draft["status"] = "draft"
        self.enqueue("QI-b", draft, activate=False)
        self.enqueue("QI-c", self.scope("repo-c", "worker-dev-3"), activate=False)
        self.run_cli("cockpit-queue", "start-next", ok=False)
        self.run_cli("cockpit-queue", "transition", "QI-c", "implementing", "--reason", "bypass", ok=False)
        draft["status"] = "reviewed"
        source = self.base / "review.json"
        source.write_text(json.dumps(draft))
        self.run_cli("cockpit-queue", "review-footprint", "QI-b", "--footprint", str(source))
        self.run_cli("cockpit-queue", "start-next")
        self.run_cli("cockpit-queue", "review-footprint", "QI-b", "--footprint", str(source), ok=False)
        # Earlier shaping work is not bypassed by a later implementable item.
        self.run_cli("cockpit-queue", "start-next")
        self.run_cli("cockpit-queue", "transition", "QI-c", "implementing", "--reason", "ready")
        self.tick()
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_NOT_IMPLEMENTABLE)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)
        self.assertTrue(footprints_conflict(None, draft))
        draft["status"] = "draft"
        self.assertTrue(footprints_conflict(draft, draft))

    def test_retained_dispatch_never_resubmits_but_independent_new_work_is_delivered(self):
        self.enqueue("QI-a", self.scope("repo-a"))
        first = self.tick()
        with patch.object(overseer, "tmux"):
            self.assertTrue(overseer.deliver_controller_dispatch(first, "test-session", as_of=NOW))
        retained = self.tick()
        self.assertFalse(retained.applied)
        self.assertEqual(retained.action.command_id, first.action.command_id)
        with patch.object(overseer, "tmux") as transport:
            self.assertFalse(overseer.deliver_controller_dispatch(retained, "test-session", as_of=NOW))
            transport.assert_not_called()
        self.enqueue("QI-b", self.scope("repo-b", "worker-dev-2"))
        second = self.tick()
        self.assertTrue(second.applied)
        self.assertEqual(second.action.worker_id, "worker-dev-2")
        with patch.object(overseer, "tmux") as transport:
            self.assertTrue(overseer.deliver_controller_dispatch(second, "test-session", as_of=NOW))
            submits = [call for call in transport.call_args_list if call.args[0] == "send-keys"]
            self.assertEqual(submits, [
                unittest.mock.call("send-keys", "-t", "test-session:worker-dev-2", "Enter"),
            ])
        state = cc.read_mission_state(self.control)
        self.assertEqual(len(state.claimed_slots), 2)
        self.assertTrue(all(slot["state"] == cc.MISSION_SLOT_RESERVED for _, slot in state.claimed_slots))

    def test_repo_worktree_ancestry_symlink_upstream_and_resource_conflicts(self):
        a, b = self.scope("repo-a"), self.scope("repo-b", "worker-dev-2")
        self.assertFalse(footprints_conflict(a, b))
        variants = []
        same_repo = copy.deepcopy(b)
        same_repo["repositories"] = copy.deepcopy(a["repositories"])
        same_repo["repositories"][0]["branch"] = "other"
        variants.append(same_repo)
        for field, value in (("write_paths", [str(self.base / "planning")]),
                             ("write_paths", [a["repositories"][0]["path"] + "/output"])):
            other = copy.deepcopy(b)
            other[field] = value
            variants.append(other)
        other = copy.deepcopy(b)
        other["repositories"][0]["upstream"] = a["repositories"][0]["upstream"]
        variants.append(other)
        for other in variants:
            self.assertTrue(footprints_conflict(a, other))
        a["resources"] = b["resources"] = ["database:shared", "tcp:127.0.0.1:8080"]
        self.assertTrue(footprints_conflict(a, b))
        a["resources"] = b["resources"] = []
        alias = self.base / "alias"
        alias.symlink_to(a["repositories"][0]["path"], target_is_directory=True)
        other = copy.deepcopy(b)
        other["write_paths"] = [str(alias / "output")]
        other = validate_footprint(other, live=True, normalize=True)
        self.assertTrue(footprints_conflict(a, other))
        repo = a["repositories"][0]["path"]
        subprocess.run(["git", "-C", repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True, env=self.env)
        worktree = self.base / "linked"
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", "-b", "linked", str(worktree)],
                       check=True, env=self.env)
        other = copy.deepcopy(b)
        other["repositories"] = [{"path": str(worktree), "upstream": "other-declaration", "branch": "linked"}]
        other = validate_footprint(other, live=True, normalize=True)
        self.assertTrue(footprints_conflict(a, other))
        subprocess.run(["git", "-C", repo, "remote", "add", "origin", "git@example.test:team/repo.git"],
                       check=True, env=self.env)
        with self.assertRaisesRegex(ValueError, "upstream"):
            validate_footprint(a, live=True)
        a["repositories"][0]["upstream"] = "https://example.test/team/repo.git"
        normalized = validate_footprint(a, live=True, normalize=True)
        self.assertEqual(normalized["repositories"][0]["upstream"], "example.test/team/repo")
        local_origin = self.base / "upstream.git"
        subprocess.run(["git", "-C", repo, "remote", "set-url", "origin", str(local_origin)],
                       check=True, env=self.env)
        a["repositories"][0]["upstream"] = local_origin.as_uri()
        normalized = validate_footprint(a, live=True, normalize=True)
        self.assertEqual(normalized["repositories"][0]["upstream"], str(local_origin))
        self.assertEqual(validate_footprint(normalized, live=True), normalized)

    def test_invalid_future_and_live_alias_drift_fail_closed(self):
        a = self.scope("repo-a")
        for field, value in (("version", 2), ("version", True), ("workers", {"worker-dev": "worker-test"}),
                             ("write_paths", ["relative"]), ("status", "trusted")):
            bad = copy.deepcopy(a)
            bad[field] = value
            with self.assertRaises(ValueError):
                validate_footprint(bad, live=True)
        bad = copy.deepcopy(a)
        bad["extra"] = True
        with self.assertRaises(ValueError):
            validate_footprint(bad)
        self.enqueue("QI-a", a)
        result = self.tick()
        envelope = self.envelope(result)
        bad = copy.deepcopy(envelope)
        bad["boundaries"]["mission_footprint"]["version"] = 2
        with self.assertRaises(cc.ControlStoreError):
            cc.validate_command_envelope(bad)
        # Immutable event replay must not depend on current filesystem aliases.
        output = Path(a["write_paths"][0])
        output.parent.mkdir()
        output.symlink_to(self.base / "elsewhere")
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)
        with self.assertRaises(cc.ControlStoreError):
            self.accept(envelope)

    def test_acceptance_and_delivery_reject_review_drift_without_releasing(self):
        a = self.scope("repo-a")
        self.enqueue("QI-a", a)
        result = self.tick()
        envelope = self.envelope(result)
        a["resources"] = ["deployment:new"]
        self.edit_item("QI-a", footprint=a)
        with self.assertRaises(cc.ControlStoreError):
            self.accept(envelope)
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaises(RuntimeError):
                overseer.deliver_controller_dispatch(result, "test-session", as_of=NOW)
            transport.assert_not_called()
        self.assertNotEqual(self.tick().action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)
        self.assertNotEqual(envelope["boundaries"]["mission_footprint"], a)

    def test_terminal_queue_and_cancel_pending_keep_original_claims(self):
        a = self.scope("repo-a")
        self.enqueue("QI-a", a)
        first = self.tick()
        envelope = self.envelope(first)
        self.accept(envelope)
        self.run_cli("cockpit-control", "cancel-mission", "--command-id", str(uuid4()),
                     "--mission", envelope["mission_id"], "--worker", "worker-dev", "--queue-item", "QI-a",
                     "--trace", envelope["trace_id"], "--reason", "operator-request",
                     "--acknowledge-within", "300", "--requested-at", NOW, "--payload", "{}")
        self.run_cli("cockpit-queue", "reject", "QI-a", "--reason", "queue disposition only")
        changed = copy.deepcopy(a)
        changed["workers"] = {"worker-dev": "worker-dev-2"}
        self.enqueue("QI-b", changed)
        self.assertNotEqual(self.tick().action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)
        for command in cc.read_command_slots(self.control).values():
            self.assertEqual(command["envelope"]["boundaries"]["mission_footprint"], a)
        # A terminal queue does not act as a global lock either.
        self.run_cli("cockpit-queue", "reject", "QI-b", "--reason", "blocked request withdrawn")
        self.enqueue("QI-c", self.scope("repo-c", "worker-dev-3"))
        # Record the outstanding recovery response window before independent work.
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_RECOVERY_AWAITING)
        self.assertEqual(self.tick().action.worker_id, "worker-dev-3")

    def test_cleared_released_history_survives_repository_removal(self):
        a = self.scope("repo-a")
        self.enqueue("QI-a", a)
        envelope = self.envelope(self.tick())
        self.accept(envelope)
        identity = ["--mission", envelope["mission_id"], "--worker", "worker-dev",
                    "--queue-item", "QI-a", "--trace", envelope["trace_id"]]
        self.run_cli("cockpit-control", "record-lifecycle", "--state", "running", *identity,
                     "--sequence", "2", "--heartbeat-at", NOW, "--fresh-for", "300")
        self.run_cli("cockpit-control", "record-lifecycle", "--state", "completed", *identity,
                     "--sequence", "3", "--evidence", "queue:QI-a")
        self.run_cli("cockpit-queue", "transition", "QI-a", "delivered", "--reason", "done")
        self.run_cli("cockpit-queue", "clear-current", "--item", "QI-a", "--waiver", "fixture")
        self.assertFalse(cc.read_mission_state(self.control).claimed_slots)
        Path(a["repositories"][0]["path"]).rename(self.base / "retired-repo-a")
        history_item = cc.observe_queue(self.queue).items[0]
        self.assertTrue(history_item.terminal)
        self.assertEqual(history_item.footprint, a)
        self.enqueue("QI-b", self.scope("repo-b", "worker-dev-2"))
        result = self.tick()
        self.assertEqual(result.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertTrue(self.accept(self.envelope(result)).start_work)
        # Archived declarations still enforce their immutable schema.
        future = dict(a, version=2)
        self.edit_item("QI-a", footprint=future)
        with self.assertRaisesRegex(cc.ControlStoreError, "unsupported footprint"):
            cc.observe_queue(self.queue)

    def test_terminal_claim_revalidates_original_removed_scope_at_dispatch_and_acceptance(self):
        a, b = self.scope("repo-a"), self.scope("repo-b", "worker-dev-2")
        self.enqueue("QI-a", a)
        self.tick()
        self.enqueue("QI-b", b)
        pending_b = self.envelope(self.tick())
        self.run_cli("cockpit-queue", "reject", "QI-a", "--reason", "not worker release")
        Path(a["repositories"][0]["path"]).rename(self.base / "retired-repo-a")
        # Even an edited terminal record cannot hide the immutable occupied scope.
        self.edit_item("QI-a", footprint=b)
        self.assertEqual(len(cc.observe_queue(self.queue).items), 2)
        with self.assertRaisesRegex(cc.ControlStoreError, "conflicts with occupied claims"):
            self.accept(pending_b)
        self.enqueue("QI-c", self.scope("repo-c", "worker-dev-3"))
        self.assertEqual(self.tick().action.reason, cc.CONTROLLER_REASON_FOOTPRINT_CONFLICT)
        state = cc.read_mission_state(self.control)
        self.assertEqual(len(state.claimed_slots), 2)
        self.assertEqual(state.worker_slots["worker-dev"]["state"], cc.MISSION_SLOT_RESERVED)

    def test_new_independent_candidates_cannot_starve_owed_recovery(self):
        for name, worker in (("a", "worker-dev"), ("b", "worker-dev-2")):
            self.enqueue("QI-" + name, self.scope("repo-" + name, worker))
            self.accept(self.envelope(self.tick()))
        self.enqueue("QI-c", self.scope("repo-c", "worker-dev-3"))
        stale_at = "2026-09-04T10:06:00.000000Z"
        for worker in ("worker-dev", "worker-dev-2"):
            result = cc.controller_tick(self.control, environ=self.env, as_of=stale_at)
            self.assertEqual(result.action.kind, cc.CONTROLLER_ACTION_RECOVER)
            self.assertEqual(result.action.worker_id, worker)
            self.assertEqual(result.action.recovery_rung, cc.CONTROLLER_RECOVERY_NUDGE)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 2)

    def test_addressed_clearance_and_legacy_serial(self):
        self.enqueue("QI-a")
        self.enqueue("QI-b", activate=False)
        self.run_cli("cockpit-queue", "start-next", ok=False)
        self.run_cli("cockpit-queue", "reject", "QI-a", "--reason", "test cleanup")
        self.run_cli("cockpit-queue", "reject", "QI-b", "--reason", "test cleanup")
        self.enqueue("QI-c", self.scope("repo-c"))
        self.enqueue("QI-d", self.scope("repo-d", "worker-dev-2"))
        for name in ("QI-c", "QI-d"):
            self.run_cli("cockpit-queue", "transition", name, "delivered", "--reason", "done")
        self.run_cli("cockpit-queue", "clear-current", "--waiver", "review", ok=False)
        self.run_cli("cockpit-queue", "clear-current", "--item", "QI-d", "--waiver", "review")
        self.run_cli("cockpit-queue", "clear-current", "--waiver", "review")

    def test_human_hold_retains_claim_without_starving_independent_dispatch(self):
        a = self.scope("repo-a")
        self.enqueue("QI-a", a)
        first = self.tick()
        envelope = self.envelope(first)
        self.accept(envelope)
        prompt = str(uuid4())
        self.run_cli("cockpit-control", "raise-question", "--command-id", prompt,
                     "--mission", envelope["mission_id"], "--worker", "worker-dev", "--queue-item", "QI-a",
                     "--trace", envelope["trace_id"], "--category", "scope-review",
                     "--body-ref", "report:scope", "--payload", "{}")
        self.run_cli("cockpit-control", "answer-question", "--hold", "--command-id", str(uuid4()),
                     "--answers", prompt, "--by", "operator", "--trace", str(uuid4()),
                     "--category", "scope-review", "--body-ref", "report:hold", "--payload", "{}")
        self.assertEqual(cc.read_mission_state(self.control).dialogs[prompt]["state"], "held")
        self.enqueue("QI-b", self.scope("repo-b", "worker-dev-2"))
        second = self.tick()
        self.assertEqual(second.action.worker_id, "worker-dev-2")
        self.assertEqual(second.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 2)
        # Held work is never automatically recovered, even after freshness expires.
        later = cc.controller_tick(self.control, environ=self.env, as_of="2026-09-04T11:00:00.000000Z")
        self.assertNotEqual(later.action.kind, cc.CONTROLLER_ACTION_RECOVER)
        state = cc.read_mission_state(self.control)
        self.assertEqual(state.dialogs[prompt]["state"], "held")
        self.assertEqual(len(state.claimed_slots), 2)

    def test_replacement_inherits_claims_and_same_role_instance_recovery(self):
        a = self.scope("repo-a", "worker-dev-2")
        self.enqueue("QI-a", a)
        first = self.tick()
        envelope = self.envelope(first)
        self.accept(envelope)
        later = cc.controller_tick(self.control, environ=self.env, as_of="2026-09-04T10:06:00.000000Z")
        self.assertEqual(later.action.kind, cc.CONTROLLER_ACTION_RECOVER)
        self.assertEqual(later.action.worker_id, "worker-dev-2")
        self.assertEqual(later.action.recovery_rung, cc.CONTROLLER_RECOVERY_NUDGE)
        cancel, replacement, mission = str(uuid4()), str(uuid4()), str(uuid4())
        self.run_cli("cockpit-control", "cancel-mission", "--command-id", cancel,
                     "--mission", envelope["mission_id"], "--worker", "worker-dev-2", "--queue-item", "QI-a",
                     "--trace", envelope["trace_id"], "--reason", "operator-request",
                     "--acknowledge-within", "300", "--requested-at", NOW, "--payload", "{}")
        def ack(command, outcome):
            digest = cc.read_command_slots(self.control)[command]["envelope"]["payload_digest"]
            self.run_cli("cockpit-control", "acknowledge-command", "--command-id", command,
                         "--outcome", outcome, "--by", "worker-dev-2", "--digest", digest,
                         "--result", "test:cooperatively-stopped")
        ack(cancel, "accepted")
        self.run_cli("cockpit-control", "replace-mission", "--command-id", replacement,
                     "--mission", envelope["mission_id"], "--replacement-mission", mission,
                     "--worker", "worker-dev-2", "--queue-item", "QI-a", "--trace", str(uuid4()),
                     "--reason", "same reviewed scope", "--evidence", "queue:QI-a", "--payload", "{}")
        ack(replacement, "accepted")
        ack(replacement, "applied")
        self.assertEqual(cc.read_mission_state(self.control).worker_slots["worker-dev-2"]["mission_id"], mission)
        second = self.tick()
        self.assertEqual(second.action.mission_id, mission)
        self.assertTrue(self.accept(self.envelope(second)).start_work)
        for command in cc.read_command_slots(self.control).values():
            self.assertEqual(command["envelope"]["boundaries"]["mission_footprint"], a)

    def test_reviewed_scope_not_widened_by_cockpit_roots_or_mutable_queue(self):
        a, b = self.scope("repo-a"), self.scope("repo-b", "worker-dev-2")
        self.enqueue("QI-a", a)
        first = self.tick()
        envelope = self.envelope(first)
        boundaries = dict(envelope["boundaries"], implementation_roots=[str(self.base)])
        self.assertTrue(controller._is_declared_repository_access(a["repositories"][0]["path"], boundaries))
        self.assertFalse(controller._is_declared_repository_access(b["repositories"][0]["path"], boundaries))
        self.run_cli("cockpit-queue", "reject", "QI-a", "--reason", "not lifecycle release")
        self.edit_item("QI-a", footprint=b)
        a["workers"] = {"worker-dev": "worker-dev-3"}
        self.enqueue("QI-b", a)
        result = self.tick()
        self.assertEqual(result.action.reason, cc.CONTROLLER_REASON_FOOTPRINT_CONFLICT)
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)

    def test_reviewed_footprints_cannot_dispatch_outside_cockpit_roots(self):
        allowed = self.scope("repo-a")
        outside = self.base / "implementation-shadow"
        external = self.scope("repo-external")
        Path(external["repositories"][0]["path"]).rename(outside)
        external["repositories"] = [{
            "path": str(outside), "upstream": "example/external", "branch": "main",
        }]
        external = validate_footprint(external, live=True, normalize=True)
        self.enqueue("QI-a", allowed)
        variants = [external]
        for path in (outside / "output", self.queue / "items", self.control / "events"):
            variants.append(dict(allowed, write_paths=[str(path)]))
        alias = self.implementation / "escape"
        alias.symlink_to(outside, target_is_directory=True)
        variants.append(validate_footprint(
            dict(allowed, write_paths=[str(alias / "output")]), live=True, normalize=True,
        ))
        before = len(cc.read_command_slots(self.control))
        for footprint in variants:
            with self.subTest(footprint=footprint):
                self.edit_item("QI-a", footprint=footprint)
                unchanged = _snapshot_tree(self.base)
                with self.assertRaisesRegex(cc.ControlStoreError, "outside cockpit declared roots"):
                    self.tick(dry_run=True)
                self.assertEqual(_snapshot_tree(self.base), unchanged)
                with self.assertRaisesRegex(cc.ControlStoreError, "outside cockpit declared roots"):
                    self.tick()
                self.assertEqual(len(cc.read_command_slots(self.control)), before)
                self.assertFalse(cc.read_mission_state(self.control).claimed_slots)
        self.edit_item("QI-a", footprint=allowed)
        self.assertEqual(self.tick().action.kind, cc.CONTROLLER_ACTION_DISPATCH)

    def test_acceptance_and_delivery_refuse_preexisting_out_of_bounds_dispatch(self):
        footprint = self.scope("repo-a")
        footprint["write_paths"] = [str(self.base / "outside-output")]
        self.enqueue("QI-a", footprint)
        # Simulate a snapshot committed before upper-bound enforcement existed.
        with patch.object(controller, "_require_footprint_authority"):
            result = self.tick()
        envelope = self.envelope(result)
        with self.assertRaisesRegex(cc.ControlStoreError, "outside cockpit declared roots"):
            self.accept(envelope)
        with patch.object(overseer, "tmux") as transport:
            with self.assertRaisesRegex(cc.ControlStoreError, "outside cockpit declared roots"):
                overseer.deliver_controller_dispatch(result, "test-session", as_of=NOW)
            transport.assert_not_called()
        self.assertEqual(len(cc.read_mission_state(self.control).claimed_slots), 1)

    def test_protected_stores_and_repository_resource_keys_never_widen_authority(self):
        footprint = self.scope("repo-a")
        self.enqueue("QI-a", footprint)
        boundaries = self.envelope(self.tick())["boundaries"]
        broad = dict(boundaries, implementation_roots=[str(self.base)])
        for path in (self.queue, self.control, self.base):
            bad = dict(footprint, write_paths=[str(path)])
            with self.subTest(path=path):
                with self.assertRaisesRegex(ValueError, "protected queue/control"):
                    require_footprint_authority(bad, broad)
                self.assertFalse(controller._is_declared_repository_access(
                    str(path), dict(broad, mission_footprint=bad),
                ))
        outside = str(self.base / "outside-repository")
        footprint["resources"] = ["repo:" + outside]
        declared = dict(boundaries, mission_footprint=footprint)
        self.assertFalse(controller._architecture_blocker_is_declared(
            "repository", "repo:" + outside, (declared,),
        ))
        self.assertFalse(controller._is_declared_repository_access(
            footprint["repositories"][0]["path"] + "/../../outside-repository", declared,
        ))
        # Authority narrowing is opt-in, not a change to legacy serial envelopes.
        require_footprint_authority(None, boundaries)
        require_footprint_authority(dict(footprint, status="draft", write_paths=[outside]), boundaries)

    def test_concurrent_ticks_serialize_reservations_and_revalidate_fifo(self):
        self.enqueue("QI-a", self.scope("repo-a"))
        self.enqueue("QI-b", self.scope("repo-b", "worker-dev-2"))
        def tick_process(_):
            return _run([sys.executable, "-c",
                         "import sys; sys.path.insert(0,sys.argv[1]); import cockpit_control as c; "
                         "from pathlib import Path; c.controller_tick(Path(sys.argv[2]),as_of=sys.argv[3])",
                         str(BIN), str(self.control), NOW], self.env)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(tick_process, range(2)))
        self.assertTrue(any(result.returncode == 0 for result in results))
        for result in results:
            if result.returncode:
                self.assertIn("rerun tick; no command was committed", result.stderr)
        if len(cc.read_mission_state(self.control).claimed_slots) == 1:
            self.tick()
        state = cc.read_mission_state(self.control)
        self.assertEqual(len(state.claimed_slots), 2)
        self.assertFalse(state.contested_slots)
        self.assertEqual(len(cc.read_command_slots(self.control)), 2)

    def test_new_module_cold_managed_install(self):
        result = _run([str(ROOT / "bootstrap.sh"), "global"], self.env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = Path(self.env["HOME"]) / ".local" / "bin"
        self.assertTrue((installed / "cockpit_footprint.py").exists())
        env = dict(self.env)
        env.pop("PYTHONPATH", None)
        result = subprocess.run([str(installed / "cockpit-queue"), "list", "--json"],
                                cwd=self.base, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(installed / "cockpit-overseer"), "tick", "--dry-run", "--as-of", NOW],
                                cwd=self.base, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
