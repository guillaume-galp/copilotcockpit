"""Final BUG-001 integration proof: every transition uses a public binary.

Only tmux and the host schedulers are faked. Reads of JSON artifacts independently
check persistence/correlation; no control implementation is imported or called.
Legacy input construction and loss of derived projections are explicit fixtures,
not shortcuts for driving lifecycle transitions.
"""
import json
import os
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from tests.unit import test_wake_runtime as wake_fixture


# Observe what existed at the actual load/paste boundary, without acquiring the
# controller lock (the real transport holds it). Fall back only to the repository
# fake; never a host tmux server.
OBSERVING_TMUX = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
base = Path(os.environ["WAKE_TEST_BASE"])
command = sys.argv[1]
if command in ("load-buffer", "paste-buffer", "send-keys"):
    root = Path(os.environ["COCKPIT_CONTROL_ROOT"])
    record = {
        "argv": sys.argv[1:],
        "ledger": json.loads((root / "ledger.json").read_text()),
        "events": [json.loads(p.read_text()) for p in sorted((root / "events").glob("*.json"))],
        "identity": {key: value for key, value in os.environ.items()
                     if key.startswith("COCKPIT_WAKE_") or key in
                     ("COCKPIT_CONTROL_ROOT", "COCKPIT_QUEUE_ROOT")},
    }
    if command == "load-buffer":
        record["brief"] = Path(sys.argv[2]).read_text()
    with (base / "transport.jsonl").open("a") as handle:
        handle.write(json.dumps(record) + "\\n")
    sys.exit(0)
sys.exit(subprocess.run([os.environ["BUG001_SAFE_TMUX"], *sys.argv[1:]]).returncode)
"""


class PublicRuntimeTests(wake_fixture.WakeCLIFixture):
    def setUp(self):
        super().setUp()
        # Wake execution must reach the real overseer, not the unit spy.
        (self.fake / "cockpit-overseer").unlink()
        self.write_executable(self.fake / "tmux", OBSERVING_TMUX)
        self.env["BUG001_SAFE_TMUX"] = str(wake_fixture.ROOT / "tests/transport/tmux")
        self.env["PATH"] = os.pathsep.join((
            str(self.fake), str(wake_fixture.ROOT / "tests/transport"),
            str(wake_fixture.BIN), os.environ["PATH"],
        ))
        self.env["COCKPIT_TEST_PANE"] = "❯ ready"

    def protocol(self, *args, **kwargs):
        return self.cli("cockpit-protocol", *args, **kwargs)

    def ledger(self, root=None):
        return json.loads(((root or self.control) / "ledger.json").read_text())

    def events(self, root=None):
        return {p.name: p.read_bytes()
                for p in sorted(((root or self.control) / "events").glob("*.json"))}

    def transport(self):
        path = self.base / "transport.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def status(self, worker="worker-dev"):
        result = self.protocol("status", "--session", "stored-session",
                               "--workers", worker, "--json")
        return json.loads(result.stdout)["workers"][worker]

    def tick(self, *args):
        return self.cli("cockpit-overseer", "tick", "-s", "stored-session", *args)

    def enqueue(self, label):
        return self.cli("cockpit-queue", "enqueue", "--text",
                        "/the-copilot-build-method " + label, "--title", label).stdout.strip()

    def start(self, item):
        self.cli("cockpit-queue", "start-next")
        self.assertNotEqual(self.queue_item(item)["state"], "queued")
        self.cli("cockpit-queue", "transition", item, "implementing", "--reason", "ready")

    def queue_item(self, item):
        return json.loads((self.queue / "items" / f"{item}.yaml").read_text())

    def envelope(self):
        ledger = self.ledger()
        slot = ledger["mission_slots"]["worker-dev"]
        return ledger["commands"][slot["command_id"]]["envelope"]

    def receipt(self, envelope, *args, **kwargs):
        e = envelope
        return self.protocol(
            "accept-dispatch", "--command-id", e["command_id"], "--mission", e["mission_id"],
            "--worker", e["target"]["id"], "--queue-item", e["queue_item_id"],
            "--trace", e["trace_id"], "--payload-digest", e["payload_digest"],
            "--fresh-for", "300", *args, **kwargs,
        )

    def heartbeat(self, e):
        self.protocol(
            "heartbeat", "--state", "running", "--mission", e["mission_id"],
            "--worker", "worker-dev", "--queue-item", e["queue_item_id"],
            "--trace", e["trace_id"], "--sequence", "2", "--fresh-for", "300",
        )
        self.assertEqual(self.status()["lifecycle"], "running")

    def prompt(self, e, verb):
        command = str(uuid4())
        self.protocol(
            verb, "--command-id", command, "--worker", "worker-dev",
            "--mission", e["mission_id"], "--queue-item", e["queue_item_id"],
            "--trace", e["trace_id"], "--category", "permission",
            "--body-ref", "file:worker/explicit-prompt", "--payload", '{"prompt":"explicit"}',
        )
        self.assertEqual(self.status()["status"], "awaiting-approval")
        pending = json.loads(self.protocol("pending", "--worker", "worker-dev").stdout)
        self.assertTrue(any(d["dialog"]["command_id"] == command for d in pending["dialogs"]))
        return command

    def answer(self, prompt, verb, *, ok=True):
        command = str(uuid4())
        result = self.protocol(
            verb, "--command-id", command, "--answers", prompt, "--by", "operator",
            "--trace", str(uuid4()), "--category", "explicit-decision",
            "--body-ref", "file:operator/decision", "--payload", '{"decision":"explicit"}',
            ok=ok,
        )
        return command, result

    def ack(self, command, outcome, *, worker="worker-dev", digest=None, ok=True):
        e = self.ledger()["commands"][command]["envelope"]
        return self.protocol(
            "acknowledge-command", "--command-id", command,
            "--digest", digest or e["payload_digest"], "--outcome", outcome,
            "--by", worker, "--result", "file:worker/cooperative-ack", ok=ok,
        )

    def applied(self, command):
        self.ack(command, "accepted")
        self.ack(command, "applied")
        record = self.ledger()["commands"][command]
        self.assertEqual(record["status"], "applied")
        self.assertEqual([entry["acknowledgement"]["outcome"] for entry in record["acknowledgements"]],
                         ["accepted", "applied"])
        for entry in record["acknowledgements"]:
            ack = entry["acknowledgement"]
            self.assertEqual(ack["command_id"], command)
            self.assertEqual(ack["payload_digest"], record["envelope"]["payload_digest"])
            self.assertEqual(ack["acknowledged_by"], "worker-dev")

    def request(self, e, *, replacement=None):
        command = str(uuid4())
        args = ["replace-mission" if replacement else "cancel-mission",
                "--command-id", command, "--worker", "worker-dev",
                "--mission", e["mission_id"], "--queue-item", e["queue_item_id"],
                "--trace", e["trace_id"], "--reason", "explicit operator decision", "--payload", "{}"]
        args += ["--replacement-mission", replacement] if replacement else ["--acknowledge-within", "300"]
        self.protocol(*args)
        return command

    def publish(self, payload, event_type="command-registered", *, ok=True):
        return self.cli("cockpit-control", "publish-event", "--type", event_type,
                        "--payload", json.dumps(payload), ok=ok)

    def append_historical(self, payload, event_type="command-registered"):
        # Additive old-writer fixture: bypass today's publication admission only,
        # retaining canonical event identity and all existing committed bytes.
        event = json.loads(next(reversed(self.events().values())))
        event.update(revision=event["revision"] + 1, event_id=str(uuid4()),
                     event_type=event_type, payload=deepcopy(payload))
        path = self.control / "events" / f'{event["revision"]:012d}-{event["event_id"]}.json'
        path.write_text(json.dumps(event))

    def replacement_payload(self):
        item = self.enqueue("replacement admission")
        self.start(item)
        self.tick()
        e = self.envelope()
        self.receipt(e)
        self.request(e, replacement=str(uuid4()))
        payload = json.loads(next(reversed(self.events().values())))["payload"]
        payload["command_envelope"]["command_id"] = payload["mission_replacement"]["command_id"] = str(uuid4())
        payload["mission_replacement"]["replacement_mission_id"] = str(uuid4())
        return e, payload

    def test_new_noncooperative_replacement_is_rejected_without_mutation(self):
        e, original = self.replacement_payload()
        baseline = self.ledger()
        for value in ("missing", False, None):
            with self.subTest(cooperative=value):
                payload = deepcopy(original)
                command = str(uuid4())
                payload["command_envelope"]["command_id"] = payload["mission_replacement"]["command_id"] = command
                if value == "missing":
                    del payload["mission_replacement"]["cooperative"]
                else:
                    payload["mission_replacement"]["cooperative"] = value
                self.assertNotIn(command, baseline["commands"])
                before = self.snapshot()
                result = self.publish(payload, ok=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("cooperative must be true", result.stderr)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.ledger(), baseline)
        self.cli("cockpit-control", "replay-ledger")
        ledger = self.ledger()
        self.assertEqual(ledger["worker_missions"][e["mission_id"]]["lifecycle"]["state"], "accepted")
        self.assertEqual(ledger["mission_slots"], baseline["mission_slots"])
        # The identical fresh pair becomes admissible only with explicit consent
        # to the cooperative protocol, and still cannot replace without an ACK.
        self.publish(original)
        command = original["command_envelope"]["command_id"]
        ledger = self.ledger()
        self.assertEqual(ledger["commands"][command]["status"], "registered")
        self.assertEqual(ledger["commands"][command]["acknowledgements"], [])
        self.assertEqual(ledger["mission_slots"], baseline["mission_slots"])
        self.assertEqual(ledger["worker_missions"][e["mission_id"]]["lifecycle"]["state"], "accepted")
        self.applied(command)
        self.assertEqual(self.ledger()["mission_slots"]["worker-dev"]["mission_id"],
                         original["mission_replacement"]["replacement_mission_id"])

    def test_committed_legacy_replacement_replays_without_ack_or_event_rewrite(self):
        e, payload = self.replacement_payload()
        del payload["mission_replacement"]["cooperative"]
        self.append_historical(payload)
        before = self.events()
        for name in ("ledger.json", "events.jsonl"):
            (self.control / name).unlink()
        self.cli("cockpit-control", "replay-ledger")
        ledger = self.ledger()
        command = payload["command_envelope"]["command_id"]
        self.assertEqual(ledger["commands"][command]["status"], "registered")
        self.assertEqual(ledger["commands"][command]["acknowledgements"], [])
        self.assertEqual(ledger["worker_missions"][e["mission_id"]]["lifecycle"]["state"], "replaced")
        self.assertEqual(ledger["mission_slots"]["worker-dev"]["mission_id"],
                         payload["mission_replacement"]["replacement_mission_id"])
        self.assertEqual(ledger["mission_slots"]["worker-dev"]["state"], "reserved")
        self.assertEqual(self.events(), before)
        self.cli("cockpit-control", "replay-ledger")
        self.assertEqual(self.ledger(), ledger)
        self.assertEqual(self.events(), before)

    def test_foreign_envelopes_cannot_cancel_or_replace_through_public_cli_or_replay(self):
        item = self.enqueue("correlated command admission")
        self.start(item)
        self.tick()
        e = self.envelope()
        self.receipt(e)
        for replacing in (False, True):
            command = self.request(e, replacement=str(uuid4()) if replacing else None)
            field = "mission_replacement" if replacing else "mission_cancellation"
            original = json.loads(next(reversed(self.events().values())))["payload"]
            baseline = self.ledger()
            mutations = {
                "target": {"kind": "worker", "id": "worker-test"},
                "queue-target": {"kind": "queue", "id": "worker-dev"},
                "mission_id": str(uuid4()),
                "queue_item_id": "foreign-queue",
                "trace_id": str(uuid4()),
                "parent_trace_id": str(uuid4()),
                "boundaries": dict(e["boundaries"], planning_root=str(self.base / "foreign")),
            }
            for key, value in mutations.items():
                with self.subTest(replacing=replacing, mismatch=key):
                    payload = deepcopy(original)
                    envelope = payload["command_envelope"]
                    envelope["command_id"] = payload[field]["command_id"] = str(uuid4())
                    envelope["target" if key == "queue-target" else key] = value
                    before = self.events()
                    result = self.publish(payload, ok=False)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertEqual(self.events(), before)
                    # Historical malformed pairs must remain readable audit
                    # evidence, but neither their command nor ACK can fold.
                    self.append_historical(payload)
                    for outcome in ("accepted", "applied"):
                        args = ("acknowledge-command", "--command-id", envelope["command_id"],
                                "--digest", envelope["payload_digest"], "--outcome", outcome,
                                "--by", envelope["target"]["id"],
                                "--result", "file:foreign/receipt")
                        self.assertNotEqual(self.protocol(*args, ok=False).returncode, 0)
                        ack = {
                            "schema_version": 1, "record_type": "command-acknowledgement",
                            "command_id": envelope["command_id"],
                            "payload_digest": envelope["payload_digest"],
                            "outcome": outcome, "acknowledged_by": envelope["target"]["id"],
                            "acknowledged_at": envelope["created_at"], "reason": None,
                            "result_refs": ["file:foreign/receipt"],
                        }
                        self.publish({"command_acknowledgement": ack},
                                     "command-acknowledged-" + outcome)
                    self.cli("cockpit-control", "replay-ledger")
                    ledger = self.ledger()
                    for projection in ("worker_missions", "mission_slots", "mission_cancellations"):
                        self.assertEqual(ledger[projection], baseline[projection])
                    self.assertNotIn(envelope["command_id"], ledger["commands"])
                    self.assertEqual(self.status()["lifecycle"], "accepted")
            self.assertEqual(self.ledger()["commands"][command]["status"], "registered")
        # Invalid old evidence must not prevent a subsequent owning-worker ACK.
        self.applied(command)
        self.cli("cockpit-control", "replay-ledger")
        self.assertEqual(self.ledger()["worker_missions"][e["mission_id"]]["lifecycle"]["state"], "replaced")
        self.assertEqual(self.ledger()["mission_slots"]["worker-dev"]["state"], "reserved")

    def test_status_blocks_lost_authority_but_reads_missing_and_stale_projections(self):
        empty_ledger = (self.control / "ledger.json").read_bytes()
        item = self.enqueue("authority loss")
        self.start(item)
        self.tick()
        self.receipt(self.envelope())
        complete_events = self.events()
        accepted_ledger = (self.control / "ledger.json").read_bytes()
        (self.control / "ledger.json").write_bytes(empty_ledger)
        self.assertEqual(self.status()["status"], "working")
        (self.control / "ledger.json").unlink()
        (self.control / "events.jsonl").unlink()
        self.assertEqual(self.status()["status"], "working")
        self.cli("cockpit-control", "replay-ledger")
        self.assertEqual(self.events(), complete_events)
        self.assertEqual((self.control / "ledger.json").read_bytes(), accepted_ledger)
        # Actual committed suffix loss, not a forged higher ledger revision.
        for name in reversed(complete_events):
            (self.control / "events" / name).unlink()
            preflight = self.cli("cockpit-control", "preflight", ok=False)
            self.assertNotEqual(preflight.returncode, 0)
            for worker in ("worker-dev", "worker-test"):
                row = self.status(worker)
                self.assertEqual(row["status"], "blocked")
                self.assertTrue(row["authoritative"])
            self.assertEqual((self.control / "ledger.json").read_bytes(), accepted_ledger)

    def assert_pending_delivery(self, envelope, mission_count):
        batch = self.transport()[-3:]
        self.assertEqual([r["argv"][0] for r in batch], ["load-buffer", "paste-buffer", "send-keys"])
        self.assertEqual(batch[1]["argv"], ["paste-buffer", "-t", "stored-session:worker-dev"])
        for record in batch:
            ledger = record["ledger"]
            self.assertEqual(len(ledger["worker_missions"]), mission_count)
            slot = ledger["mission_slots"]["worker-dev"]
            self.assertEqual(slot["state"], "reserved")
            self.assertEqual(slot["command_id"], envelope["command_id"])
            self.assertEqual(slot["mission_id"], envelope["mission_id"])
            lifecycle = ledger["worker_missions"][envelope["mission_id"]]["lifecycle"]
            self.assertEqual(lifecycle["state"], "pending-dispatch")
            self.assertEqual(lifecycle["sequence"], 0)
            self.assertIsNone(lifecycle["heartbeat_at"])
            command = ledger["commands"][envelope["command_id"]]
            self.assertEqual(command["envelope"], envelope)
            self.assertEqual(command["acknowledgements"], [])
            dispatches = [e for e in record["events"]
                          if e["payload"].get("controller_dispatch", {}).get("command_id") == envelope["command_id"]]
            self.assertEqual(len(dispatches), 1)
            self.assertEqual(dispatches[0]["payload"]["worker_lifecycle"]["trace_id"], envelope["trace_id"])
            self.assertNotIn("command_acknowledgement", dispatches[0]["payload"])
        brief = batch[0]["brief"]
        for field in ("mission_id", "command_id", "queue_item_id", "trace_id", "payload_digest", "deadline_at"):
            self.assertIn(envelope[field], brief)
        self.assertIn("cockpit-control accept-dispatch", brief)
        self.assertIn("start_work=true", brief)
        self.assertEqual(envelope["boundaries"]["control_root"], str(self.control))
        self.assertEqual(envelope["boundaries"]["queue_root"], str(self.queue))
        self.assertEqual(envelope["boundaries"]["planning_root"], str(self.planning))
        self.assertEqual(envelope["boundaries"]["implementation_roots"], [str(self.implementation)])

    def test_complete_public_lifecycle_and_scheduled_resume(self):
        self.assertIn("preflight ready", self.cli("cockpit-control", "preflight").stdout)
        self.assertEqual(self.ledger()["canonical_roots"],
                         json.loads((self.control / "control.json").read_text())["canonical_roots"])
        self.assertNotEqual(self.control, self.queue)
        first, second = self.enqueue("first FIFO change"), self.enqueue("second FIFO change")
        self.start(first)
        self.assertEqual(self.queue_item(second)["state"], "queued")

        before = self.events()
        self.tick("--dry-run")
        self.assertEqual(self.events(), before)
        self.assertEqual(self.transport(), [])
        self.tick()
        e = self.envelope()
        self.assertEqual(e["queue_item_id"], first)
        self.assertEqual(len(self.ledger()["commands"]), 1)
        self.assertEqual(len(self.events()), 1)
        self.assert_pending_delivery(e, 1)
        self.tick()  # pending redelivery reuses all identity and the fixed deadline
        self.assertEqual(e, self.envelope())
        self.assertEqual(len(self.events()), 1)
        self.assert_pending_delivery(e, 1)

        before = self.events()
        self.assertEqual(json.loads(self.receipt(e, "--dry-run").stdout)["outcome"], "would-accept")
        bad = dict(e, payload_digest="sha256:" + "0" * 64)
        self.assertNotEqual(self.receipt(bad, ok=False).returncode, 0)
        self.assertEqual(self.events(), before)
        # Real independent CLI processes, not internal functions, race the receipt.
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(lambda _: json.loads(self.receipt(e).stdout), range(2)))
        self.assertEqual(sorted(r["outcome"] for r in receipts), ["accepted", "duplicate"])
        self.assertEqual(sum(r["start_work"] for r in receipts), 1)
        self.assertEqual(receipts[0]["event_id"], receipts[1]["event_id"])
        self.assertEqual(len(self.events()), len(before) + 1)
        duplicate = json.loads(self.receipt(e).stdout)
        self.assertFalse(duplicate["start_work"])
        self.assertEqual(duplicate["outcome"], "duplicate")
        self.heartbeat(e)

        # Pane text cannot infer a prompt, nor erase an explicit prompt.
        self.env["COCKPIT_TEST_PANE"] = "❯ ready\nMay I read this file? [y/n]"
        self.assertEqual(self.status()["status"], "working")
        prompt = self.prompt(e, "access-prompt")
        self.env["COCKPIT_TEST_TMUX_UNREACHABLE"] = "1"
        self.assertEqual(self.status()["status"], "awaiting-approval")
        self.assertFalse(self.status()["reachable"])
        self.assertEqual(self.status("worker-test")["status"], "unreachable")
        self.env.pop("COCKPIT_TEST_TMUX_UNREACHABLE")
        hold, _ = self.answer(prompt, "hold")
        self.assertEqual(self.status()["status"], "held")
        self.applied(hold)
        transport_count = len(self.transport())
        self.tick()
        self.assertEqual(len(self.transport()), transport_count)
        self.assertEqual(self.status()["status"], "held")
        reply, _ = self.answer(prompt, "reply")
        self.applied(reply)
        self.assertEqual(self.status()["status"], "working")
        question = self.prompt(e, "ask")
        reply, _ = self.answer(question, "reply")
        self.applied(reply)

        cancel = self.request(e)
        self.assertEqual(self.ledger()["mission_slots"]["worker-dev"]["state"], "active")
        self.applied(cancel)
        self.assertEqual(self.status()["status"], "available")
        self.assertEqual(self.ledger()["worker_missions"][e["mission_id"]]["lifecycle"]["state"], "cancelled")
        self.assertNotEqual(self.receipt(e, ok=False).returncode, 0)
        self.cli("cockpit-queue", "reject", first, "--reason", "operator cancelled")
        self.start(second)
        self.tick()
        second_e = self.envelope()
        self.assertEqual(second_e["queue_item_id"], second)
        self.assert_pending_delivery(second_e, 2)
        self.assertTrue(json.loads(self.receipt(second_e).stdout)["start_work"])
        self.heartbeat(second_e)
        pending_question = self.prompt(second_e, "ask")
        replacement = str(uuid4())
        replace_command = self.request(second_e, replacement=replacement)
        before = self.events()
        self.assertNotEqual(self.ack(replace_command, "accepted", worker="worker-test", ok=False).returncode, 0)
        self.assertNotEqual(self.ack(replace_command, "accepted", digest="sha256:" + "0" * 64, ok=False).returncode, 0)
        self.assertEqual(self.events(), before)
        self.ack(replace_command, "accepted")
        self.assertEqual(self.ledger()["mission_slots"]["worker-dev"]["mission_id"], second_e["mission_id"])
        self.ack(replace_command, "applied")
        self.assertEqual(self.ledger()["mission_slots"]["worker-dev"]["mission_id"], replacement)
        self.assertNotEqual(self.answer(pending_question, "reply", ok=False)[1].returncode, 0)
        self.assertNotEqual(self.receipt(second_e, ok=False).returncode, 0)

        # The scheduled public fire drives the *real* controller to dispatch the
        # replacement, despite poisoned ambient roots/target/mission identity.
        args = self.schedule_args(cron=True)
        for flag, value in (("--mission", replacement), ("--queue-item", second)):
            args[args.index(flag) + 1] = value
        self.wake(*args)
        wake_id = self.state()["awakenings"][-1]["id"]
        stored = self.state()["awakenings"][-1]
        self.assertEqual(stored["control_root"], str(self.control))
        self.assertEqual(stored["target"], {"session": "stored-session", "window": "overseer"})
        self.wake("fire", wake_id, env=self.poisoned_env())
        replacement_e = self.envelope()
        self.assertEqual(replacement_e["mission_id"], replacement)
        self.assertNotEqual(replacement_e["command_id"], second_e["command_id"])
        self.assertNotEqual(replacement_e["trace_id"], second_e["trace_id"])
        self.assert_pending_delivery(replacement_e, 3)
        identity = self.transport()[-1]["identity"]
        self.assertEqual(identity["COCKPIT_CONTROL_ROOT"], str(self.control))
        self.assertEqual(identity["COCKPIT_QUEUE_ROOT"], str(self.queue))
        for field in ("mission", "queue_item", "owner", "intent", "stop_condition", "session", "window"):
            self.assertEqual(identity["COCKPIT_WAKE_" + field.upper()], stored[field])
        before = self.events()
        self.wake("fire", wake_id, env=self.poisoned_env(unset=True))
        self.assertEqual(self.events(), before)
        self.assertEqual(self.envelope(), replacement_e)
        self.assert_pending_delivery(replacement_e, 3)
        self.assertTrue(json.loads(self.receipt(replacement_e).stdout)["start_work"])
        self.heartbeat(replacement_e)
        self.wake("stop", wake_id, env=self.poisoned_env(unset=True))
        self.assertEqual(self.state()["awakenings"][-1]["status"], "cancelled")
        self.assertNotIn(wake_id, (self.base / "crontab").read_text())
        self.assertFalse(Path(self.job(wake_id)).exists())
        transport_count = len(self.transport())
        self.assertNotEqual(self.wake("fire", wake_id, ok=False).returncode, 0)
        self.assertEqual(len(self.transport()), transport_count)
        self.assertFalse((self.control / "wake-leases/mission-tick-lease.json").exists())
        self.assertFalse((self.base / "wrong").exists())

        # Occupied work is never dispatched twice; removing derived caches does
        # not erase status or permit another accepted receipt.
        before = self.events()
        self.tick()
        self.assertEqual(len(self.transport()), transport_count)
        self.assertEqual(self.envelope(), replacement_e)
        authoritative = self.events()
        ledger = self.ledger()
        (self.control / "ledger.json").unlink()
        (self.control / "events.jsonl").unlink()
        self.assertEqual(self.status()["status"], "working")
        self.assertFalse(json.loads(self.receipt(replacement_e).stdout)["start_work"])
        self.assertFalse((self.control / "ledger.json").exists())
        self.cli("cockpit-control", "replay-ledger")
        self.assertEqual(self.events(), authoritative)
        self.assertEqual(self.ledger(), ledger)

    def test_legacy_pending_handoff_is_diagnosed_without_rewriting_history(self):
        item = self.enqueue("legacy pending fixture")
        self.start(item)
        self.tick()
        envelope = self.envelope()
        event = json.loads(next(iter(self.events().values())))
        legacy = self.base / "legacy-control"
        env = dict(self.env, COCKPIT_CONTROL_ROOT=str(legacy))
        self.cli("cockpit-control", "init", "--queue-root", str(self.queue),
                 "--planning-root", str(self.planning),
                 "--implementation-root", str(self.implementation), env=env)
        # Add an old-format immutable event to a *separate* store. Never rewrite
        # the live dispatch to manufacture legacy input.
        envelope = dict(envelope, deadline_at=None,
                        boundaries=dict(envelope["boundaries"], control_root=str(legacy)))
        self.cli("cockpit-control", "publish-event", "--type", "command-registered",
                 "--payload", json.dumps({
                     "command_envelope": envelope,
                     "controller_dispatch": event["payload"]["controller_dispatch"],
                 }), env=env)
        before = self.events(legacy)
        snapshot = [(p, p.read_bytes()) for p in sorted(legacy.rglob("*")) if p.is_file()]
        result = self.cli("cockpit-control", "preflight", env=env, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("operationally-blocked", result.stdout + result.stderr)
        self.assertIn("recover-dispatch", result.stdout + result.stderr)
        self.assertEqual(snapshot, [(p, p.read_bytes()) for p in sorted(legacy.rglob("*")) if p.is_file()])
        result = self.cli("cockpit-overseer", "tick", "-s", "stored-session", env=env, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dispatch-acceptance-unsupported", result.stdout + result.stderr)
        self.assertTrue(all(self.events(legacy)[name] == content for name, content in before.items()))
        count = len(self.events(legacy))
        self.cli("cockpit-overseer", "tick", "-s", "stored-session", env=env, ok=False)
        self.assertEqual(len(self.events(legacy)), count)
        self.assertEqual(len(self.transport()), 3)  # only the original supported dispatch
        self.cli("cockpit-control", "list-events", env=env)
        self.cli("cockpit-control", "replay-ledger", env=env)
        self.assertTrue(all(self.events(legacy)[name] == content for name, content in before.items()))
