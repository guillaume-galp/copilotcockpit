import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import cockpit_control as cc
import cockpit_control_wake as control_wake


class ControlWakeSeamTests(unittest.TestCase):
    def test_import_compatibility_through_facade(self):
        self.assertIs(cc.WakeIntent, control_wake.WakeIntent)
        self.assertIs(cc.build_wake_intent, control_wake.build_wake_intent)
        self.assertIs(cc.wake_intent_to_dict, control_wake.wake_intent_to_dict)
        self.assertIs(cc.guard_wake_fire, control_wake.guard_wake_fire)
        self.assertIs(cc.acquire_tick_lease, control_wake.acquire_tick_lease)
        self.assertIs(cc.release_tick_lease, control_wake.release_tick_lease)

    def test_wake_intent_and_stop_condition_guard_preserve_contract(self):
        wake = control_wake.build_wake_intent(
            "wake-1",
            "session-a",
            "window-a",
            "Wake message",
            label="wake-label",
            wake_type="once",
            scheduled_at="2099-01-01T23:59:00",
            mission="M-1",
            owner="owner-1",
            queue_item="QI-1",
            intent="run",
            stop_condition="none",
            cadence="2099-01-01T23:59:00",
            blocker_threshold=5,
            lifecycle_state="pending",
            control_root="/absolute/control",
        )
        record = control_wake.wake_intent_to_dict(wake)
        self.assertEqual(record["id"], "wake-1")
        self.assertEqual(record["mission"], "M-1")
        self.assertEqual(record["owner"], "owner-1")
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["control_root"], "/absolute/control")
        self.assertEqual(record["target"], {"session": "session-a", "window": "window-a"})

        allowed, message = control_wake.guard_wake_fire(record, "wake-1", Path("/dev/null"))
        self.assertTrue(allowed)
        self.assertEqual(message, "")

        suspended = dict(record)
        suspended["lifecycle_state"] = "human-suspended"
        allowed, message = control_wake.guard_wake_fire(suspended, "wake-1", Path("/dev/null"))
        self.assertFalse(allowed)
        self.assertIn("human-suspended/pending", message)

        stopped = dict(record)
        stopped["stop_condition_fulfilled"] = True
        allowed, message = control_wake.guard_wake_fire(stopped, "wake-1", Path("/dev/null"))
        self.assertFalse(allowed)
        self.assertIn("stop condition already fulfilled", message)

    def test_duplicate_and_stale_lease_paths_preserve_fail_closed_behavior(self):
        events = []
        self.addCleanup(control_wake.bind_facade, {"publish_control_event": cc.publish_control_event})

        def _publish(root, event_type, actor, payload, command):
            events.append((event_type, payload))

        control_wake.bind_facade(
            {
                "publish_control_event": _publish,
                "ControlStoreError": cc.ControlStoreError,
                "CONTROL_SCHEMA_VERSION": cc.CONTROL_SCHEMA_VERSION,
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lease_id, identity = control_wake.acquire_tick_lease(root, "wake-A", "s", "w")
            self.assertTrue(lease_id)
            self.assertIsNotNone(identity)

            duplicate_id, duplicate_identity = control_wake.acquire_tick_lease(root, "wake-B", "s", "w")
            self.assertEqual(duplicate_id, "")
            self.assertIsNone(duplicate_identity)
            self.assertTrue(any(event[0] == "wake-duplicate-skipped" for event in events))

            control_wake.release_tick_lease(root, lease_id, identity)

            active = root / control_wake.WAKE_LEASE_DIR / control_wake.WAKE_LEASE_FILE
            active.write_text(
                (
                    '{"schema_version":1,"lease_id":"expired","wake_id":"wake-old","pid":999999,'
                    '"session":"s","window":"w","acquired_at":"2000-01-01T00:00:00Z",'
                    '"expires_at":"2000-01-01T00:01:00Z"}\n'
                )
            )

            recovered_id, recovered_identity = control_wake.acquire_tick_lease(root, "wake-C", "s", "w")
            self.assertTrue(recovered_id)
            self.assertIsNotNone(recovered_identity)
            self.assertTrue(any(event[0] == "wake-lease-recovered" for event in events))


if __name__ == "__main__":
    unittest.main()
