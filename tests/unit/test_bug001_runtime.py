"""BUG-001 public protocol and bounded recovery regression proofs.

Run with tests/transport first in PATH and an isolated HOME/config/cache.
No scheduler, real tmux server, or third-party service is used.
"""
import json
import os
import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from tests.unit import test_dispatch_acceptance as acceptance

cc = acceptance.cc
overseer = acceptance.overseer


class Bug001RuntimeTests(unittest.TestCase):
    setUp = acceptance.DispatchAcceptanceTests.setUp
    at = acceptance.DispatchAcceptanceTests.at
    run_cli = acceptance.DispatchAcceptanceTests.run_cli
    receipt_args = acceptance.DispatchAcceptanceTests.receipt_args
    accept = acceptance.DispatchAcceptanceTests.accept
    cli_args = acceptance.DispatchAcceptanceTests.cli_args
    history = acceptance.DispatchAcceptanceTests.history
    snapshot = acceptance.DispatchAcceptanceTests.snapshot
    empty_control = acceptance.DispatchAcceptanceTests.empty_control

    def protocol(self, *args, check=True):
        return self.run_cli("cockpit-protocol", *args, check=check)

    def status(self):
        return json.loads(self.protocol("status", "--session", "isolated",
                                       "--workers", "worker-dev", "--json").stdout)["workers"]["worker-dev"]

    def prompt(self, verb="access-prompt"):
        command = str(uuid4())
        self.protocol(
            verb, "--command-id", command, "--worker", "worker-dev",
            "--mission", self.envelope["mission_id"], "--queue-item", self.item,
            "--trace", self.envelope["trace_id"], "--category", "permission",
            "--body-ref", "file:inspection/prompt", "--payload", '{"prompt":"digest only"}',
        )
        return command

    def answer(self, prompt, verb="reply", command=None):
        command = command or str(uuid4())
        result = self.protocol(
            verb, "--command-id", command, "--answers", prompt, "--by", "operator",
            "--trace", str(uuid4()), "--category", "explicit-decision",
            "--body-ref", "file:inspection/decision", "--payload", '{"decision":"explicit"}',
            check=False,
        )
        return command, result

    def worker_ack(self, command, outcome="applied", worker="worker-dev", check=True):
        digest = cc.read_command_slots(self.root)[command]["envelope"]["payload_digest"]
        if outcome == "applied":
            self.protocol("acknowledge-command", "--command-id", command, "--digest", digest,
                          "--outcome", "accepted", "--by", worker, check=check)
        return self.protocol("acknowledge-command", "--command-id", command, "--digest", digest,
                             "--outcome", outcome, "--by", worker,
                             "--result", "file:worker/cooperative-receipt", check=check)

    def cancel_or_replace(self, replace_mission=False):
        command = str(uuid4())
        args = [
            "replace-mission" if replace_mission else "cancel-mission",
            "--command-id", command, "--worker", "worker-dev",
            "--mission", self.envelope["mission_id"], "--queue-item", self.item,
            "--trace", self.envelope["trace_id"], "--reason", "operator decision",
            "--payload", "{}",
        ]
        replacement = str(uuid4())
        args += ["--replacement-mission", replacement] if replace_mission else ["--acknowledge-within", "300"]
        self.protocol(*args)
        return command, replacement

    def recovery(self, command=None, **kwargs):
        return cc.recover_dispatch(
            self.root, self.envelope["command_id"], command or str(uuid4()), "worker-dev",
            inspected_safe=kwargs.pop("inspected_safe", True), operator="operator",
            reason=kwargs.pop("reason", "pane and process inspected; no execution"),
            evidence_refs=["file:inspection/record"], **kwargs,
        )

    def test_dispatch_has_one_honest_pending_lifecycle_before_any_transport(self):
        event, = self.history()
        self.assertEqual(event.record["payload"]["worker_lifecycle"]["state"], "pending-dispatch")
        self.assertNotIn("command_acknowledgement", event.record["payload"])
        state = cc.read_mission_state(self.root)
        self.assertEqual(len(state.missions), 1)
        lifecycle = state.missions[self.envelope["mission_id"]]["lifecycle"]
        self.assertEqual(lifecycle["sequence"], 0)
        self.assertIsNone(lifecycle["heartbeat_at"])
        self.assertIsNone(lifecycle["fresh_until"])
        with patch.object(overseer, "tmux") as tmux:
            def observe(*args):
                self.assertEqual(cc.read_mission_state(self.root).missions, state.missions)
                return ""
            tmux.side_effect = observe
            overseer.deliver_controller_dispatch(self.tick, "isolated", as_of=self.at(1))
            self.assertTrue(tmux.called)
        self.assertEqual(self.status()["status"], "working")
        self.assertEqual(self.status()["lifecycle"], "pending-dispatch")

    def test_public_access_hold_response_heartbeat_and_cancel(self):
        result = json.loads(self.protocol(*self.cli_args()).stdout)
        self.assertTrue(result["start_work"])
        prompt = self.prompt()
        self.assertEqual(self.status()["status"], "awaiting-approval")
        pending = json.loads(self.protocol("pending", "--worker", "worker-dev").stdout)
        self.assertEqual(pending["dialogs"][0]["state"], "pending")
        hold, result = self.answer(prompt, "hold")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()["status"], "held")
        self.worker_ack(hold)
        tick = cc.controller_tick(self.root, as_of=self.at(10000))
        self.assertNotEqual(tick.action.kind, cc.CONTROLLER_ACTION_RECOVER)
        reply, result = self.answer(prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.worker_ack(reply)
        self.assertEqual(self.status()["status"], "working")
        self.protocol(
            "heartbeat", "--state", "running", "--mission", self.envelope["mission_id"],
            "--worker", "worker-dev", "--queue-item", self.item,
            "--trace", self.envelope["trace_id"], "--sequence", "2", "--fresh-for", "300",
        )
        self.assertEqual(self.status()["lifecycle"], "running")
        cancel, _ = self.cancel_or_replace()
        self.assertEqual(cc.read_mission_state(self.root).worker_slots["worker-dev"]["state"], "active")
        self.worker_ack(cancel)
        self.assertEqual(self.status()["status"], "available")
        self.assertEqual(cc.read_mission_state(self.root).missions[self.envelope["mission_id"]]["lifecycle"]["state"], "cancelled")

    def test_pending_question_replacement_waits_for_worker_then_fifo_dispatches(self):
        self.accept()
        prompt = self.prompt("ask")
        command, replacement = self.cancel_or_replace(True)
        pending = json.loads(self.protocol("pending", "--worker", "worker-dev").stdout)
        self.assertEqual(pending["replacements"][0]["replacement"]["replacement_mission_id"], replacement)
        self.assertEqual(pending["replacements"][0]["observation"], "awaiting-acknowledgement")
        self.assertEqual(self.status()["status"], "awaiting-approval")
        self.assertEqual(cc.read_mission_state(self.root).worker_slots["worker-dev"]["mission_id"], self.envelope["mission_id"])
        rejected = self.worker_ack(command, "accepted", worker="worker-test", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.worker_ack(command, "accepted")
        self.assertEqual(cc.read_mission_state(self.root).worker_slots["worker-dev"]["state"], "active")
        digest = cc.read_command_slots(self.root)[command]["envelope"]["payload_digest"]
        self.protocol("acknowledge-command", "--command-id", command, "--digest", digest,
                      "--outcome", "applied", "--by", "worker-dev",
                      "--result", "file:worker/cooperative-receipt")
        self.assertEqual(cc.read_mission_state(self.root).worker_slots["worker-dev"]["mission_id"], replacement)
        pending = json.loads(self.protocol("pending", "--worker", "worker-dev").stdout)
        self.assertEqual(pending["replacements"][0]["observation"], "applied")
        _, reply = self.answer(prompt)
        self.assertNotEqual(reply.returncode, 0)
        before = len(self.history())
        tick = cc.controller_tick(self.root)
        self.assertEqual(tick.action.mission_id, replacement)
        self.assertEqual(tick.action.kind, cc.CONTROLLER_ACTION_DISPATCH)
        self.assertEqual(len(self.history()), before + 1)
        e = cc.read_command_slots(self.root)[tick.action.command_id]["envelope"]
        accepted = cc.accept_dispatch(
            self.root, e["command_id"], replacement, "worker-dev", self.item,
            e["trace_id"], e["payload_digest"], fresh_for="300",
        )
        self.assertTrue(accepted.start_work)
        with self.assertRaises(cc.ControlStoreError):
            self.accept()

    def test_operator_recovery_is_explicit_idempotent_and_fences_late_acceptance(self):
        before = self.snapshot()
        with self.assertRaisesRegex(cc.ControlStoreError, "inspect-safe"):
            self.recovery(inspected_safe=False)
        self.assertEqual(before, self.snapshot())
        command = str(uuid4())
        self.assertEqual(self.recovery(command, dry_run=True)["outcome"], "would-release")
        self.assertEqual(before, self.snapshot())
        result = self.recovery(command)
        self.assertEqual(result["outcome"], "released")
        self.assertFalse(result["worker_stopped"])
        self.assertNotIn("command_acknowledgement", self.history()[-1].record["payload"])
        count = len(self.history())
        self.assertEqual(self.recovery(command)["outcome"], "duplicate")
        self.assertEqual(len(self.history()), count)
        with self.assertRaisesRegex(cc.ControlStoreError, "conflict"):
            self.recovery(command, reason="changed decision")
        with self.assertRaises(cc.ControlStoreError):
            self.accept()
        # Even a backdated joint event cannot bypass the released slot fence.
        e = self.envelope
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", e["mission_id"], self.item, e["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        ack = cc.build_command_acknowledgement(e["command_id"], e["payload_digest"], "accepted",
                                               "worker-dev", acknowledged_at=self.at(1))
        cc.publish_control_event(self.root, "worker-lifecycle-accepted",
                                 payload={"worker_lifecycle": lifecycle, "command_acknowledgement": ack})
        self.assertEqual(cc.read_mission_state(self.root).missions[e["mission_id"]]["lifecycle"]["state"], "cancelled")
        self.assertEqual(cc.read_command_slots(self.root)[e["command_id"]]["status"], "registered")
        tick = cc.controller_tick(self.root)
        self.assertNotEqual(tick.action.mission_id, e["mission_id"])

    def test_recovery_cli_legacy_reservation_preserves_history(self):
        root = self.empty_control("legacy-reservation")
        envelope = dict(self.envelope, deadline_at=None,
                        boundaries=dict(self.envelope["boundaries"], control_root=str(root)))
        # Additive fixture: legacy dispatch events have no worker lifecycle payload.
        cc.publish_control_event(root, "command-registered", payload={
            "command_envelope": envelope,
            "controller_dispatch": self.history()[0].record["payload"]["controller_dispatch"],
        })
        original = cc.inspect_control_events(root)[1].events[0].path.read_bytes()
        self.env["COCKPIT_CONTROL_ROOT"] = str(root)
        result = self.protocol(
            "recover-dispatch", "--dispatch-command", envelope["command_id"],
            "--command-id", str(uuid4()), "--worker", "worker-dev", "--inspect-safe",
            "--by", "operator", "--reason", "inspected legacy reservation",
            "--evidence", "file:inspection/legacy",
        )
        self.assertEqual(json.loads(result.stdout)["outcome"], "released")
        self.assertEqual(cc.inspect_control_events(root)[1].events[0].path.read_bytes(), original)
        self.assertEqual(cc.read_mission_state(root).worker_slots["worker-dev"]["state"], "released")
        lifecycle = cc.build_worker_lifecycle(
            "accepted", "worker-dev", envelope["mission_id"], self.item, envelope["trace_id"], 1,
            heartbeat_at=self.at(1), fresh_until=self.at(301),
        )
        cc.publish_control_event(root, "worker-lifecycle-accepted", payload={"worker_lifecycle": lifecycle})
        self.assertEqual(cc.read_mission_state(root).missions[envelope["mission_id"]]["lifecycle"]["state"], "cancelled")

    def test_recovery_racing_acceptance_has_exactly_one_winner(self):
        barrier = Barrier(2)
        def run(operation):
            barrier.wait(timeout=10)
            try:
                return operation()
            except cc.ControlStoreError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, operation) for operation in (self.accept, self.recovery)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.history()), 2)

    def test_status_precedence_and_preflight_diagnosis_are_read_only(self):
        self.assertEqual(cc.protocol_worker_status(self.root, ["worker-dev"], self.at(300))["workers"]["worker-dev"]["status"], "blocked")
        self.accept()
        self.assertEqual(cc.protocol_worker_status(self.root, ["worker-dev"], self.at(302))["workers"]["worker-dev"]["status"], "unreachable")
        self.prompt()
        self.assertEqual(cc.protocol_worker_status(self.root, ["worker-dev"], self.at(302))["workers"]["worker-dev"]["status"], "awaiting-approval")
        before = self.snapshot()
        self.status()
        cc.run_control_preflight(self.root)
        self.assertEqual(before, self.snapshot())
        # Build a legacy fixture, not a migration of live immutable events.
        root = self.empty_control("legacy-capability")
        path = root / "control.json"
        record = json.loads(path.read_text())
        del record["capabilities"]["worker_lifecycle"]
        path.write_text(json.dumps(record))
        before = self.snapshot(root)
        self.assertEqual(cc.run_control_preflight(root).status, "operationally-blocked")
        self.assertEqual(before, self.snapshot(root))
        self.assertTrue(cc.controller_tick(root).action.blocking)
        for unsupported in (True, 0, 2):
            record["capabilities"]["worker_lifecycle"] = unsupported
            path.write_text(json.dumps(record))
            self.assertFalse(cc.has_lifecycle_capability(record))
            before = self.snapshot(root)
            self.assertEqual(cc.run_control_preflight(root).status, "operationally-blocked")
            self.assertEqual(before, self.snapshot(root))

    def test_pane_observation_and_transport_loss_never_override_durable_status(self):
        self.env["COCKPIT_TEST_PANE"] = "❯ ready"
        self.assertEqual(self.status()["status"], "working")  # pending reservation
        self.accept()
        self.prompt()
        row = self.status()
        self.assertEqual(row["status"], "awaiting-approval")
        self.assertEqual(row["observation"], "available")
        self.env["COCKPIT_TEST_TMUX_UNREACHABLE"] = "1"
        row = self.status()
        self.assertEqual(row["status"], "awaiting-approval")
        self.assertFalse(row["reachable"])
        # A worker with no durable claim is unreachable, not available, when
        # the adapter cannot observe it. This never changes a claimed mission.
        idle = json.loads(self.protocol("status", "--session", "isolated",
                                       "--workers", "worker-test", "--json").stdout)
        self.assertEqual(idle["workers"]["worker-test"]["status"], "unreachable")

    def test_blocked_heartbeat_is_durable_and_no_prompt_is_inferred_from_pane(self):
        self.accept()
        self.protocol(
            "record-lifecycle", "--state", "running", "--mission", self.envelope["mission_id"],
            "--worker", "worker-dev", "--queue-item", self.item, "--trace", self.envelope["trace_id"],
            "--sequence", "2", "--fresh-for", "300",
        )
        self.protocol(
            "heartbeat", "--state", "blocked", "--mission", self.envelope["mission_id"],
            "--worker", "worker-dev", "--queue-item", self.item, "--trace", self.envelope["trace_id"],
            "--sequence", "3", "--fresh-for", "300", "--reason", "needs operator inspection",
            "--blocker-category", "dependency",
        )
        self.env["COCKPIT_TEST_PANE"] = "Status: available\nMay I read this file? [y/n]"
        self.assertEqual(self.status()["status"], "blocked")
        self.assertFalse(cc.read_mission_state(self.root).dialogs)

    def test_concurrent_recovery_replay_commits_one_inspection(self):
        command = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.recovery(command), range(2)))
        self.assertEqual(sorted(r["outcome"] for r in results), ["duplicate", "released"])
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(results[0]["event_id"], results[1]["event_id"])
        before = self.snapshot()
        cc.replay_control_ledger(self.root)
        self.assertEqual(before, self.snapshot())

    def test_expired_recovery_after_commit_interruption_is_replay_safe(self):
        cc.controller_tick(self.root, as_of=self.at(300))
        command = str(uuid4())
        def interrupt(boundary, publication):
            if boundary == "event-committed":
                raise RuntimeError("simulated process loss")
        with patch.object(cc, "_event_publication_fault", interrupt):
            with self.assertRaisesRegex(RuntimeError, "simulated process loss"):
                self.recovery(command)
        self.assertEqual(self.recovery(command)["outcome"], "duplicate")
        self.assertEqual(len(self.history()), 3)
        cc.replay_control_ledger(self.root)
        self.assertEqual(cc.read_mission_state(self.root).worker_slots["worker-dev"]["state"], "released")
        self.assertEqual(cc.read_command_slots(self.root)[self.envelope["command_id"]]["acknowledgements"], [])
        with self.assertRaises(cc.ControlStoreError):
            self.accept()

    def test_safe_inspection_cannot_cancel_an_accepted_worker(self):
        self.accept()
        before = self.snapshot()
        with self.assertRaisesRegex(cc.ControlStoreError, "cooperative"):
            self.recovery()
        self.assertEqual(before, self.snapshot())

    def test_public_status_and_receipt_replay_do_not_depend_on_derived_ledger(self):
        self.protocol(*self.cli_args())
        self.prompt()
        (self.root / "ledger.json").unlink()
        (self.root / "events.jsonl").unlink()
        before = self.snapshot()
        self.assertEqual(self.status()["status"], "awaiting-approval")
        duplicate = json.loads(self.protocol(*self.cli_args()).stdout)
        self.assertEqual(duplicate["outcome"], "duplicate")
        self.assertFalse(duplicate["start_work"])
        self.assertEqual(before, self.snapshot())

    def test_pending_legacy_boundary_preflight_does_not_rewrite_events(self):
        root = self.empty_control("legacy-boundary")
        envelope = dict(self.envelope, deadline_at=None,
                        boundaries=dict(self.envelope["boundaries"], control_root=str(root), planning_root=None))
        cc.publish_control_event(root, "command-registered", payload={
            "command_envelope": envelope,
            "controller_dispatch": self.history()[0].record["payload"]["controller_dispatch"],
        })
        before = self.snapshot(root)
        report = cc.run_control_preflight(root)
        self.assertEqual(report.status, "operationally-blocked")
        self.assertTrue(any("legacy command boundary" in finding.detail for finding in report.findings))
        self.assertTrue(any("recover-dispatch" in (finding.repair or "") for finding in report.findings))
        self.assertEqual(before, self.snapshot(root))

    def test_concurrent_answers_only_one_decision_and_no_answer_after_cancel(self):
        self.accept()
        prompt = self.prompt()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.answer(prompt)[1], range(2)))
        self.assertEqual(sum(r.returncode == 0 for r in results), 1)
        command, _ = self.cancel_or_replace()
        self.worker_ack(command)
        self.assertNotEqual(self.answer(prompt)[1].returncode, 0)

    def historical_event(self, payload, events):
        event = events[-1]
        event_id = str(uuid4())
        revision = event.revision + 1
        record = dict(event.record, event_id=event_id, revision=revision,
                      event_type="command-registered", payload=payload)
        cc.validate_event(record, record["control_id"])
        return replace(event, event_id=event_id, revision=revision, record=record,
                       path=event.path.parent / cc._event_filename(revision, event_id))

    def test_dialog_and_recovery_pairs_share_publication_and_replay_guard(self):
        self.accept()
        prompt = self.prompt("ask")
        e = self.envelope
        prompt_record = cc.read_mission_state(self.root).dialogs[prompt]["dialog"]
        records = [
            ("mission_dialog", cc.build_mission_dialog(
                str(uuid4()), kind, e["mission_id"], "worker-dev", self.item,
                str(uuid4()), "decision", ["file:dialog/body"],
                parent_trace_id=prompt_record["trace_id"],
                answers_command_id=prompt if kind in ("reply", "hold") else None,
            ))
            for kind in ("question", "access-prompt", "reply", "hold")
        ]
        records.append(("mission_recovery", cc.build_mission_recovery(
            str(uuid4()), cc.MISSION_RECOVERY_NUDGE, e["mission_id"], "worker-dev",
            self.item, str(uuid4()), cc.LIFECYCLE_STALE_REASON, self.at(601),
            parent_trace_id=e["trace_id"], evidence_refs=["file:recovery/inspection"],
            requested_at=self.at(301),
        )))
        history = self.history()
        baseline = cc.fold_mission_state(history)
        for field, record in records:
            valid = dict(e, command_id=record["command_id"], deadline_at=None,
                         command_type=cc._mission_control_command_type(field, record),
                         trace_id=record["trace_id"], parent_trace_id=record["parent_trace_id"])
            for key, value in (
                ("target", {"kind": "worker", "id": "worker-test"}),
                ("mission_id", str(uuid4())), ("queue_item_id", "foreign-queue"),
                ("trace_id", str(uuid4())), ("parent_trace_id", str(uuid4())),
                ("boundaries", dict(e["boundaries"], runtime_boundaries=["foreign:runtime"])),
            ):
                with self.subTest(field=field, kind=record.get("kind"), mismatch=key):
                    envelope = dict(valid, **{key: value})
                    payload = {"command_envelope": envelope, field: record}
                    before = self.snapshot()
                    result = self.run_cli("cockpit-control", "publish-event", "--type",
                                          "command-registered", "--payload", json.dumps(payload),
                                          check=False)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(self.snapshot(), before)
                    old = self.historical_event(payload, history)
                    replayed = cc.fold_mission_state(history + (old,))
                    self.assertEqual(replayed.dialogs, baseline.dialogs)
                    self.assertEqual(replayed.recoveries, baseline.recoveries)
                    self.assertEqual(replayed.worker_slots, baseline.worker_slots)
                    self.assertFalse(replayed.mission_outcomes[old.event_id][0])
                    self.assertNotIn(record["command_id"], cc.fold_commands(history + (old,))[0])
            # Matching child traces remain legitimate, including legacy events.
            valid_event = self.historical_event({"command_envelope": valid, field: record}, history)
            self.assertTrue(cc.fold_mission_state(history + (valid_event,)).mission_outcomes[valid_event.event_id][0])

    def test_recovery_inspection_digest_and_boundary_mismatch_never_release_reservation(self):
        history = self.history()
        e = self.envelope
        record = cc.build_mission_cancellation(
            str(uuid4()), e["mission_id"], "worker-dev", self.item, e["trace_id"],
            "safe inspection", self.at(300), evidence_refs=["file:inspection/record"],
            requested_at=self.at(0),
        )
        record["reservation_recovery"] = {
            "dispatch_command_id": e["command_id"], "payload_digest": e["payload_digest"],
            "inspected_safe": True,
        }
        envelope = dict(e, command_id=record["command_id"], command_type="mission-cancel",
                        deadline_at=None, payload_digest=cc.command_payload_digest({"inspection": True}))
        for mismatch in ("digest", "boundaries"):
            payload = {"command_envelope": deepcopy(envelope), "mission_cancellation": deepcopy(record)}
            if mismatch == "digest":
                payload["mission_cancellation"]["reservation_recovery"]["payload_digest"] = "sha256:" + "0" * 64
            else:
                payload["command_envelope"]["boundaries"]["queue_root"] = str(self.base / "foreign")
            before = self.snapshot()
            result = self.run_cli("cockpit-control", "publish-event", "--type", "command-registered",
                                  "--payload", json.dumps(payload), check=False)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(self.snapshot(), before)
            old = self.historical_event(payload, history)
            state = cc.fold_mission_state(history + (old,))
            self.assertEqual(state.worker_slots["worker-dev"]["state"], "reserved")
            self.assertEqual(state.missions[e["mission_id"]]["lifecycle"]["state"], "pending-dispatch")
            self.assertFalse(state.cancellations)
            self.assertNotIn(record["command_id"], cc.fold_commands(history + (old,))[0])

    def test_registration_paths_reject_new_noncooperative_replacement(self):
        self.accept()
        e = self.envelope
        for path in ("mission", "generic", "declarations"):
            for value in ("missing", False, None):
                with self.subTest(path=path, cooperative=value):
                    record = cc.build_mission_replacement(
                        str(uuid4()), "worker-dev", self.item, e["mission_id"], str(uuid4()),
                        e["trace_id"], "explicit operator decision",
                    )
                    if value == "missing":
                        del record["cooperative"]
                    else:
                        record["cooperative"] = value
                    envelope = dict(e, command_id=record["command_id"], command_type="mission-replace",
                                    deadline_at=None)
                    self.assertNotIn(record["command_id"], cc.read_command_slots(self.root))
                    before = self.snapshot()
                    with self.assertRaisesRegex(cc.ControlStoreError, "cooperative must be true"):
                        if path == "mission":
                            cc.register_mission_command(self.root, envelope, "mission_replacement", record)
                        else:
                            keyword = "correlated_record" if path == "generic" else "declarations"
                            cc.register_command(self.root, envelope, **{keyword: {"mission_replacement": record}})
                    self.assertEqual(self.snapshot(), before)

    def test_valid_legacy_replacement_survives_invalid_historical_pair(self):
        self.accept()
        history = self.history()
        e = self.envelope
        record = cc.build_mission_replacement(
            str(uuid4()), "worker-dev", self.item, e["mission_id"], str(uuid4()),
            str(uuid4()), "legacy operator decision", parent_trace_id=e["trace_id"],
        )
        del record["cooperative"]
        envelope = dict(e, command_id=record["command_id"], command_type="mission-replace",
                        trace_id=record["trace_id"], parent_trace_id=record["parent_trace_id"],
                        deadline_at=None)
        invalid = self.historical_event({
            "command_envelope": dict(envelope, target={"kind": "worker", "id": "worker-test"}),
            "mission_replacement": record,
        }, history)
        history += (invalid,)
        valid = self.historical_event({"command_envelope": envelope, "mission_replacement": record}, history)
        state = cc.fold_mission_state(history + (valid,))
        self.assertFalse(state.mission_outcomes[invalid.event_id][0])
        self.assertTrue(state.mission_outcomes[valid.event_id][0])
        self.assertEqual(state.missions[e["mission_id"]]["lifecycle"]["state"], "replaced")
        self.assertEqual(state.worker_slots["worker-dev"]["mission_id"], record["replacement_mission_id"])


if __name__ == "__main__":
    unittest.main()
