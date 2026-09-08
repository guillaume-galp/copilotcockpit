"""BUG-001 scheduled context regressions using real CLIs and isolated transports.

Every subprocess inherits a private HOME/config/cache and tests/transport/tmux
ahead of system tools. Scheduler commands are private, persistent fakes.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "bin"

FAKE_SCHEDULER = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ["WAKE_TEST_BASE"])
name = Path(sys.argv[0]).name
with (base / "scheduler.log").open("a") as f:
    f.write(json.dumps([name, *sys.argv[1:]]) + "\\n")
if name == "at":
    (base / "at-job").write_text(sys.stdin.read())
    if os.environ.get("WAKE_TEST_AT_FAIL"):
        sys.exit(2)
    print("job 123 at someday", file=sys.stderr)
elif name == "crontab":
    path = base / "crontab"
    if sys.argv[1] == "-l":
        print(path.read_text() if path.exists() else "", end="")
    else:
        path.write_text(sys.stdin.read())
"""

FAKE_OVERSEER = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ["WAKE_TEST_BASE"])
with (base / "ticks.jsonl").open("a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
if os.environ.get("WAKE_TEST_CANCEL_DURING_TICK"):
    import subprocess
    subprocess.run([os.environ["WAKE_TEST_CLI"], "stop", os.environ["COCKPIT_WAKE_ID"]], check=True)
sys.exit(int(os.environ.get("WAKE_TEST_TICK_EXIT", "0")))
"""


class WakeCLIFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wake-runtime-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.home = self.base / "home with spaces"
        self.control = self.base / "control"
        self.queue = self.base / "queue"
        self.planning = self.base / "planning"
        self.implementation = self.base / "implementation"
        self.fake = self.base / "fake"
        for path in (self.home, self.queue, self.planning, self.implementation, self.fake):
            path.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("COCKPIT_", "TMUX", "XDG_", "WAKE_TEST_", "GO"))}
        self.env.update(
            HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"),
            XDG_CACHE_HOME=str(self.home / ".cache"),
            PATH=os.pathsep.join((str(ROOT / "tests/transport"), str(self.fake),
                                  str(BIN), os.environ["PATH"])),
            COCKPIT_CONTROL_ROOT=str(self.control), COCKPIT_QUEUE_ROOT=str(self.queue),
            WAKE_TEST_BASE=str(self.base), WAKE_TEST_CLI=str(BIN / "cockpit-wake"),
        )
        for name in ("at", "atrm", "crontab", "notify-send"):
            self.write_executable(self.fake / name, FAKE_SCHEDULER)
        self.write_executable(self.fake / "cockpit-overseer", FAKE_OVERSEER)
        self.cli("cockpit-control", "init", "--queue-root", str(self.queue),
                 "--planning-root", str(self.planning),
                 "--implementation-root", str(self.implementation))
        self.state_path = self.home / ".config/cockpit-wake/awakenings.json"

    def write_executable(self, path, content):
        path.write_text(content)
        path.chmod(0o755)

    def cli(self, binary, *args, env=None, ok=True):
        return self.run_process([str(BIN / binary), *args], env=env, ok=ok)

    def run_process(self, argv, env=None, ok=True):
        result = subprocess.run(argv, env=self.env if env is None else env, cwd=self.base,
                                text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def wake(self, *args, **kwargs):
        return self.cli("cockpit-wake", *args, **kwargs)

    def schedule_args(self, cron=False):
        return [
            "schedule", "--cron" if cron else "--once",
            "*/5 * * * *" if cron else "23:59 2099-01-01",
            "-s", "stored-session", "-w", "overseer", "-m", "Observe one bounded tick",
            "--mission", "M-1", "--queue-item", "QI-1", "--owner", "operator",
            "--intent", "bounded oversight", "--stop-condition", "mission terminal",
        ]

    def schedule(self, cron=False):
        self.wake(*self.schedule_args(cron))
        return self.state()["awakenings"][-1]["id"]

    def state(self):
        return json.loads(self.state_path.read_text())

    def change_wake(self, mutate):
        state = self.state()
        mutate(state["awakenings"][-1])
        self.state_path.write_text(json.dumps(state))

    def job(self, wake_id):
        return str(self.state_path.parent / "jobs" / f"{wake_id}.sh")

    def ticks(self):
        return [json.loads(line) for line in (self.base / "ticks.jsonl").read_text().splitlines()]

    def poisoned_env(self, unset=False):
        env = dict(self.env)
        for key in ("COCKPIT_CONTROL_ROOT", "COCKPIT_QUEUE_ROOT", "COCKPIT_WAKE_SESSION",
                    "COCKPIT_WAKE_WINDOW", "COCKPIT_WAKE_MISSION", "COCKPIT_WAKE_OWNER",
                    "COCKPIT_WAKE_QUEUE_ITEM", "COCKPIT_WAKE_INTENT", "COCKPIT_WAKE_STOP_CONDITION"):
            if unset:
                env.pop(key, None)
            else:
                env[key] = "wrong"
        return env

    def snapshot(self):
        # Include identities and timestamps, not just bytes, for read-only proofs.
        return [(str(p.relative_to(self.base)), p.stat().st_ino, p.stat().st_mtime_ns,
                 p.read_bytes()) for p in sorted(self.base.rglob("*")) if p.is_file()]

    def assert_blocked(self, result, contains=None):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("operationally-blocked", result.stderr)
        self.assertIn("ADR-017", result.stderr)
        if contains:
            self.assertIn(contains, result.stderr)

    def assert_no_execution(self):
        self.assertFalse((self.base / "ticks.jsonl").exists())
        self.assertFalse((self.state_path.parent / "inbox.md").exists())
        self.assertFalse((self.control / "wake-leases").exists())


class WakeRuntimeTests(WakeCLIFixture):
    def test_schedule_persists_typed_identity_and_distinct_root(self):
        wake_id = self.schedule()
        record = self.state()["awakenings"][0]
        self.assertEqual(record["id"], wake_id)
        self.assertEqual(record["control_root"], str(self.control))
        self.assertNotEqual(record["control_root"], str(self.queue))
        self.assertEqual(record["target"], {"session": "stored-session", "window": "overseer"})
        for key in ("mission", "queue_item", "owner", "intent", "stop_condition", "cadence"):
            self.assertTrue(record[key])
        self.assertEqual(record["at_job_id"], "123")
        self.assertIn("_tick-with-lease", Path(self.job(wake_id)).read_text())
        self.assertIn(self.job(wake_id), (self.base / "at-job").read_text())

    def assert_stored_tick(self):
        tick = self.ticks()[-1]
        self.assertEqual(tick["argv"], ["tick", "-s", "stored-session", "-w", "overseer"])
        record = self.state()["awakenings"][-1]
        for field in ("mission", "queue_item", "owner", "intent", "stop_condition", "cadence",
                      "session", "window", "blocker_threshold"):
            self.assertEqual(tick["env"]["COCKPIT_WAKE_" + field.upper()], str(record[field]))
        self.assertEqual(tick["env"]["COCKPIT_CONTROL_ROOT"], str(self.control))
        self.assertEqual(tick["env"]["COCKPIT_QUEUE_ROOT"], str(self.queue))
        released = list((self.control / "wake-leases/released").glob("*.json"))
        self.assertTrue(released)
        lease = json.loads(released[-1].read_text())
        self.assertEqual(lease["session"], "stored-session")
        self.assertEqual(lease["window"], "overseer")
        self.assertFalse((self.control / "wake-leases/mission-tick-lease.json").exists())

    def test_generated_job_restores_wrong_and_unset_context(self):
        wake_id = self.schedule(cron=True)
        for unset in (False, True):
            env = self.poisoned_env(unset)
            env["HOME"] = str(self.base / "wrong-home")
            self.run_process([self.job(wake_id)], env=env)
            self.assert_stored_tick()
        self.assertEqual(len(self.ticks()), 2)
        self.assertEqual(self.state()["awakenings"][0]["status"], "pending")

    def test_generated_job_executes_with_minimal_scheduler_environment(self):
        wake_id = self.schedule()
        env = {"PATH": "/usr/bin:/bin", "WAKE_TEST_BASE": str(self.base)}
        self.run_process([self.job(wake_id)], env=env)
        self.assert_stored_tick()

    def test_internal_tick_ignores_wrong_arguments_and_ambient_identity(self):
        wake_id = self.schedule()
        other = self.base / "other-control"
        self.cli("cockpit-control", "init", "--queue-root", str(self.queue),
                 "--planning-root", str(self.planning),
                 "--implementation-root", str(self.implementation),
                 env=dict(self.env, COCKPIT_CONTROL_ROOT=str(other)))
        before = [(p, p.read_bytes()) for p in other.rglob("*") if p.is_file()]
        env = dict(self.poisoned_env(), COCKPIT_CONTROL_ROOT=str(other))
        self.wake("_tick-with-lease", wake_id, "wrong-session", "wrong-window",
                  env=env)
        self.assert_stored_tick()
        self.assertEqual(before, [(p, p.read_bytes()) for p in other.rglob("*") if p.is_file()])
        self.assertFalse((other / "wake-leases").exists())
        self.assertEqual(self.state()["awakenings"][0]["status"], "fired")
        self.assert_blocked(self.wake("_tick-with-lease", wake_id, ok=False))
        self.assertEqual(len(self.ticks()), 1)

    def test_public_fire_reloads_state_not_job_shell(self):
        wake_id = self.schedule()
        self.write_executable(Path(self.job(wake_id)), "#!/bin/sh\nexit 99\n")
        self.wake("fire", wake_id, env=self.poisoned_env(unset=True))
        self.assert_stored_tick()

    def test_real_cli_tick_dispatches_once_using_stored_root_and_session(self):
        (self.fake / "cockpit-overseer").unlink()
        item = self.cli("cockpit-queue", "enqueue", "--text",
                        "/the-copilot-build-method implement one bounded change",
                        "--title", "wake integration").stdout.strip()
        self.cli("cockpit-queue", "start-next")
        self.cli("cockpit-queue", "transition", item, "implementing", "--reason", "ready")
        args = self.schedule_args()
        args[args.index("--queue-item") + 1] = item
        self.wake(*args)
        wake_id = self.state()["awakenings"][0]["id"]
        result = self.run_process([self.job(wake_id)], env=self.poisoned_env())
        self.assertIn("target stored-session:worker-dev", result.stdout)
        events = [json.loads(path.read_text()) for path in (self.control / "events").glob("*.json")]
        # The controller owns action selection and durability, not the wake runner.
        self.assertEqual(len(events), 1, events)
        self.assertIn(item, json.dumps(events))
        self.assertIn("mission-dispatch", json.dumps(events))
        self.assertEqual(self.state()["awakenings"][0]["status"], "fired")
        self.assertFalse((self.base / "wrong").exists())

    def test_stop_alias_and_cancel_work_after_recurring_fire_without_root(self):
        for command in ("stop", "cancel"):
            wake_id = self.schedule(cron=True)
            self.run_process([self.job(wake_id)])
            self.wake(command, wake_id, env=self.poisoned_env(unset=True))
            self.assertEqual(self.state()["awakenings"][-1]["status"], "cancelled")
            self.assertNotIn(wake_id, (self.base / "crontab").read_text())
            self.assertFalse(Path(self.job(wake_id)).exists())
            self.assertNotEqual(
                self.wake(command, wake_id, env=self.poisoned_env(), ok=False).returncode, 0
            )
            before = self.snapshot()
            self.assert_blocked(self.wake("_tick-with-lease", wake_id, ok=False))
            self.assertEqual(before, self.snapshot())

    def test_cancel_legacy_fired_cron_and_clean_preserves_active_schedule(self):
        wake_id = self.schedule(cron=True)
        self.change_wake(lambda w: (w.update(status="fired"), w.pop("control_root")))
        self.wake("clean")
        self.assertEqual(len(self.state()["awakenings"]), 1)
        self.wake("list", env=self.poisoned_env())
        self.wake("stop", wake_id, env=self.poisoned_env(unset=True))
        self.assertNotIn(wake_id, (self.base / "crontab").read_text())

    def test_cron_cancel_preserves_unrelated_jobs_with_similar_identity(self):
        wake_id = self.schedule(cron=True)
        path = self.base / "crontab"
        unrelated = f"* * * * * echo keep # cockpit-wake:{wake_id}0\n"
        path.write_text(path.read_text() + unrelated)
        self.wake("cancel", wake_id)
        self.assertEqual(path.read_text(), unrelated)

    def test_cancel_during_tick_is_not_overwritten(self):
        wake_id = self.schedule(cron=True)
        env = dict(self.env, WAKE_TEST_CANCEL_DURING_TICK="1")
        self.wake("fire", wake_id, env=env)
        self.assertEqual(self.state()["awakenings"][0]["status"], "cancelled")
        self.assertNotIn(wake_id, (self.base / "crontab").read_text())

    def start_barrier_process(self, argv):
        child = subprocess.Popen(
            argv, env=self.env, cwd=self.base, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate()

        self.addCleanup(cleanup)
        return child

    def assert_barrier(self, child, expected):
        ready, _, _ = select.select([child.stdout], [], [], 10)
        self.assertTrue(ready, f"child did not reach {expected!r} barrier")
        self.assertEqual(child.stdout.readline().strip(), expected)

    def paused_tick(self, wake_id, hook):
        code = """
import runpy, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
ns = runpy.run_path(sys.argv[1] + "/cockpit-wake", run_name="wake_test")
tick = ns["cmd_tick_with_lease"]
scope = tick.__globals__
hook = sys.argv[3]
def pause():
    print("paused", flush=True)
    assert sys.stdin.readline().strip() == "release"
if hook == "spawn":
    original = scope["subprocess"].Popen
    def paused_spawn(*args, **kwargs):
        pause()
        return original(*args, **kwargs)
    scope["subprocess"].Popen = paused_spawn
else:
    name = "_acquire_tick_lease" if hook == "admission" else "write_inbox"
    original = scope[name]
    def paused_call(*args, **kwargs):
        result = original(*args, **kwargs)
        pause()
        return result
    scope[name] = paused_call
sys.exit(tick(SimpleNamespace(id=sys.argv[2])))
"""
        child = self.start_barrier_process(
            [sys.executable, "-c", code, str(BIN), wake_id, hook]
        )
        self.assert_barrier(child, "paused")
        return child

    def assert_stopped_schedule(self, wake_id):
        record = next(w for w in self.state()["awakenings"] if w["id"] == wake_id)
        self.assertEqual(record["status"], "cancelled")
        self.assertIsNone(record["fired_at"])
        self.assertNotIn(wake_id, (self.base / "crontab").read_text())
        self.assertFalse(Path(self.job(wake_id)).exists())

    def test_stop_before_final_admission_prevents_spawn(self):
        wake_id = self.schedule(cron=True)
        tick = self.paused_tick(wake_id, "admission")
        self.wake("stop", wake_id)
        self.assert_stopped_schedule(wake_id)
        out, err = tick.communicate("release\n", timeout=10)
        self.assertNotEqual(tick.returncode, 0, out + err)
        self.assertIn("wake changed during lease acquisition", err)
        self.assertFalse((self.base / "ticks.jsonl").exists())
        self.assertFalse((self.state_path.parent / "inbox.md").exists())
        self.assertFalse((self.control / "wake-leases/mission-tick-lease.json").exists())
        self.assert_stopped_schedule(wake_id)

    def test_stop_cannot_succeed_in_inbox_or_process_creation_gap(self):
        cancellation = """
import fcntl, runpy, sys
sys.path.insert(0, sys.argv[1])
original = fcntl.flock
observed = False
def observed_lock(fd, operation):
    global observed
    try:
        return original(fd, operation)
    except BlockingIOError:
        if not observed:
            print("contended", flush=True)
            observed = True
        raise
fcntl.flock = observed_lock
script, wake_id = sys.argv[1] + "/cockpit-wake", sys.argv[2]
sys.argv = [script, "stop", wake_id]
runpy.run_path(script, run_name="__main__")
"""
        for hook in ("inbox", "spawn"):
            with self.subTest(hook=hook):
                wake_id = self.schedule(cron=True)
                tick = self.paused_tick(wake_id, hook)
                stop = self.start_barrier_process(
                    [sys.executable, "-c", cancellation, str(BIN), wake_id]
                )
                # No sleeps: observe stop's actual failed lock acquisition while
                # the controller process has not yet been created.
                self.assert_barrier(stop, "contended")
                self.assertIsNone(stop.poll())
                self.assertEqual(self.state()["awakenings"][-1]["status"], "pending")
                self.assertIn(wake_id, (self.base / "crontab").read_text())
                ticks = self.ticks() if (self.base / "ticks.jsonl").exists() else []
                self.assertFalse(any(t["env"]["COCKPIT_WAKE_ID"] == wake_id for t in ticks))
                out, err = tick.communicate("release\n", timeout=10)
                self.assertEqual(tick.returncode, 0, out + err)
                out, err = stop.communicate(timeout=10)
                self.assertEqual(stop.returncode, 0, out + err)
                record = self.state()["awakenings"][-1]
                self.assertEqual(record["status"], "cancelled")
                self.assertNotIn(wake_id, (self.base / "crontab").read_text())
                self.assertFalse(Path(self.job(wake_id)).exists())
                self.assertEqual(
                    sum(t["env"]["COCKPIT_WAKE_ID"] == wake_id for t in self.ticks()), 1
                )
                self.assertFalse(
                    (self.control / "wake-leases/mission-tick-lease.json").exists()
                )

    def test_stop_while_child_running_does_not_wait_or_get_undone(self):
        wake_id = self.schedule(cron=True)
        # The child reads under the same lock before signalling readiness. This
        # deadlocks/times out if the parent holds its lock while waiting.
        self.write_executable(self.fake / "cockpit-overseer", """#!/usr/bin/env python3
import fcntl, json, sys
from pathlib import Path
directory = Path.home() / ".config/cockpit-wake"
lock = directory / "awakenings.lock"
for fd in Path("/proc/self/fd").glob("*"):
    try:
        assert fd.resolve(strict=True) != lock, "inherited parent's wake state lock"
    except FileNotFoundError:
        pass
with lock.open("a") as handle:
    fcntl.flock(handle, fcntl.LOCK_EX)
    state = json.loads((directory / "awakenings.json").read_text())
    assert state["awakenings"][0]["status"] == "pending"
print("running", flush=True)
assert sys.stdin.readline().strip() == "release"
""")
        tick = self.start_barrier_process([str(BIN / "cockpit-wake"), "fire", wake_id])
        self.assert_barrier(tick, "running")
        result = subprocess.run(
            [str(BIN / "cockpit-wake"), "stop", wake_id], env=self.env,
            cwd=self.base, text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(tick.poll(), "stop should complete while the child is still running")
        self.assert_stopped_schedule(wake_id)
        self.assertTrue((self.control / "wake-leases/mission-tick-lease.json").exists())
        out, err = tick.communicate("release\n", timeout=10)
        self.assertEqual(tick.returncode, 0, out + err)
        self.assert_stopped_schedule(wake_id)
        self.assertFalse((self.control / "wake-leases/mission-tick-lease.json").exists())
        self.assert_blocked(self.wake("fire", wake_id, ok=False))

    def test_stop_serializes_with_completion_between_reload_and_save(self):
        wake_id = self.schedule(cron=True)
        completion = """
import runpy, sys
sys.path.insert(0, sys.argv[1])
ns = runpy.run_path(sys.argv[1] + "/cockpit-wake", run_name="wake_test")
mark = ns["mark_wake_fired"]
original = mark.__globals__["load_state"]
def paused_load():
    state = original()
    print("loaded", flush=True)
    assert sys.stdin.readline().strip() == "release"
    return state
mark.__globals__["load_state"] = paused_load
mark(sys.argv[2])
"""
        cancellation = """
import fcntl, runpy, sys
sys.path.insert(0, sys.argv[1])
original = fcntl.flock
def observed_lock(fd, operation):
    try:
        return original(fd, operation)
    except BlockingIOError:
        print("contended", flush=True)
        raise
fcntl.flock = observed_lock
script, wake_id = sys.argv[1] + "/cockpit-wake", sys.argv[2]
sys.argv = [script, "stop", wake_id]
runpy.run_path(script, run_name="__main__")
"""
        children = []
        try:
            for code in (completion, cancellation):
                child = subprocess.Popen(
                    [sys.executable, "-c", code, str(BIN), wake_id],
                    env=self.env, cwd=self.base, stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                children.append(child)
                ready, _, _ = select.select([child.stdout], [], [], 10)
                self.assertTrue(ready, "child did not reach the deterministic barrier")
                line = child.stdout.readline().strip()
                self.assertEqual(line, "loaded" if code == completion else "contended")
            # Stop must wait rather than report success against the stale snapshot.
            self.assertIsNone(children[1].poll())
            out, err = children[0].communicate("release\n", timeout=10)
            self.assertEqual(children[0].returncode, 0, out + err)
            out, err = children[1].communicate(timeout=10)
            self.assertEqual(children[1].returncode, 0, out + err)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
        self.assertEqual(self.state()["awakenings"][0]["status"], "cancelled")
        self.assertNotIn(wake_id, (self.base / "crontab").read_text())
        self.assertFalse(Path(self.job(wake_id)).exists())
        self.assert_blocked(self.wake("fire", wake_id, ok=False))
        self.assert_no_execution()

    def test_concurrent_schedules_preserve_every_intent_and_cron_entry(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.wake(*self.schedule_args(cron=True)), range(4)))
        self.assertEqual(len(results), 4)
        wakes = self.state()["awakenings"]
        self.assertEqual(len(wakes), 4)
        self.assertEqual(len({w["id"] for w in wakes}), 4)
        cron = (self.base / "crontab").read_text()
        for wake in wakes:
            self.assertIn(wake["id"], cron)
            self.assertTrue(Path(self.job(wake["id"])).exists())

    def test_legacy_and_malformed_internal_invocations_fail_closed(self):
        wake_id = self.schedule()
        valid = self.state_path.read_text()
        mutations = [
            lambda w: w.pop("control_root"), lambda w: w.update(control_root="relative"),
            lambda w: w.pop("target"), lambda w: w.update(target={"session": "wrong", "window": "overseer"}),
            lambda w: w.pop("owner"), lambda w: w.pop("mission"),
            lambda w: w.pop("queue_item"), lambda w: w.pop("intent"),
            lambda w: w.pop("stop_condition"), lambda w: w.update(blocker_threshold=0),
            lambda w: w.update(lifecycle_state="human-suspended"),
            lambda w: w.update(stop_condition_fulfilled=True),
        ]
        for mutation in mutations:
            self.state_path.write_text(valid)
            self.change_wake(mutation)
            before = self.snapshot()
            for command in (["fire", wake_id], ["_guard-fire", wake_id],
                            ["_tick-with-lease", wake_id, "wrong", "wrong"],
                            ["_write-inbox", wake_id, "wrong", "/etc/passwd", "now"],
                            ["_mark-fired", wake_id]):
                self.assert_blocked(self.wake(*command, ok=False))
            self.assertEqual(before, self.snapshot())
            self.assert_no_execution()

    def test_schedule_validates_required_intent_and_target_before_any_writes(self):
        for option in ("--mission", "--queue-item", "--owner", "--intent", "--stop-condition"):
            args = self.schedule_args()
            offset = args.index(option)
            del args[offset:offset + 2]
            before = self.snapshot()
            self.assert_blocked(self.wake(*args, ok=False))
            self.assertEqual(before, self.snapshot())
        for option, value in (("-s", ""), ("-w", ""), ("-s", "session:other"),
                              ("--blocker-threshold", "0"), ("--lifecycle", "active")):
            args = self.schedule_args()
            if option in args:
                args[args.index(option) + 1] = value
            else:
                args.extend([option, value])
            before = self.snapshot()
            self.assert_blocked(self.wake(*args, ok=False))
            self.assertEqual(before, self.snapshot())

    def test_schedule_rejects_bad_cadence_without_state_jobs_or_scheduler_calls(self):
        for cron, value in ((True, "* * * * *\necho unsafe"), (True, "61 * * * *"),
                            (True, "*/0 * * * *"), (True, "@reboot"),
                            (False, ""), (False, "25:00"), (False, "01:00 2000-01-01"),
                            (False, "23:59 2099-01-01 extra")):
            args = self.schedule_args(cron)
            args[2] = value
            before = self.snapshot()
            self.assert_blocked(self.wake(*args, ok=False))
            self.assertEqual(before, self.snapshot())

    def test_schedule_dry_run_is_read_only_for_at_and_cron(self):
        for cron in (False, True):
            before = self.snapshot()
            result = self.wake(*self.schedule_args(cron), "--dry-run")
            preview = json.loads(result.stdout)
            self.assertEqual(preview["outcome"], "would-schedule")
            self.assertEqual(preview["wake"]["control_root"], str(self.control))
            self.assertEqual(before, self.snapshot())
        self.assertFalse(self.state_path.parent.exists())
        self.assertFalse((self.base / "scheduler.log").exists())
        self.schedule(cron=True)
        before = self.snapshot()
        self.wake(*self.schedule_args(), "--dry-run")
        self.assertEqual(before, self.snapshot())

    def test_cron_rejects_scheduler_path_line_breaks_before_writes(self):
        env = dict(self.env, HOME=str(self.base / "home\nunsafe"))
        before = self.snapshot()
        self.assert_blocked(self.wake(*self.schedule_args(cron=True), env=env, ok=False),
                            "line breaks")
        self.assertEqual(before, self.snapshot())

    def test_schedule_requires_root_and_preflight_boundaries_and_capability(self):
        metadata_path = self.control / "control.json"
        original = metadata_path.read_text()
        for key, value in (("queue_root", None), ("planning_root", None),
                           ("implementation_roots", []), ("queue_root", str(self.control))):
            # Test a structurally coherent store via supported fresh init where possible;
            # malformed metadata must also fail before any side effects.
            metadata = json.loads(original)
            metadata["canonical_roots"][key] = value
            metadata_path.write_text(json.dumps(metadata))
            before = self.snapshot()
            self.assert_blocked(self.wake(*self.schedule_args(), "--dry-run", ok=False))
            self.assertEqual(before, self.snapshot())
        metadata = json.loads(original)
        metadata["capabilities"]["worker_lifecycle"] = 0
        metadata_path.write_text(json.dumps(metadata))
        before = self.snapshot()
        self.assert_blocked(self.wake(*self.schedule_args(), ok=False))
        self.assertEqual(before, self.snapshot())
        metadata_path.write_text(original)
        self.assert_blocked(self.wake(*self.schedule_args(), env=self.poisoned_env(unset=True), ok=False))
        self.assertFalse(self.state_path.exists())

    def test_supported_preflight_refuses_missing_work_directory_before_schedule(self):
        self.implementation.rmdir()
        before = self.snapshot()
        self.assert_blocked(self.wake(*self.schedule_args(), ok=False), "preflight blocked")
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.state_path.exists())

    def test_internal_fire_rechecks_supported_preflight_before_acquiring_lease(self):
        wake_id = self.schedule()
        self.implementation.rmdir()
        before = self.snapshot()
        self.assert_blocked(self.wake("_tick-with-lease", wake_id, ok=False), "preflight blocked")
        self.assertEqual(before, self.snapshot())
        self.assert_no_execution()

    def test_unconfigured_valid_store_is_operationally_blocked_without_mutation(self):
        root = self.base / "unconfigured"
        env = dict(self.env, COCKPIT_CONTROL_ROOT=str(root))
        self.cli("cockpit-control", "init", env=env)
        before = self.snapshot()
        self.assert_blocked(self.wake(*self.schedule_args(), env=env, ok=False), "boundaries")
        self.assertEqual(before, self.snapshot())

    def test_generated_job_revalidates_missing_corrupt_future_control_metadata(self):
        wake_id = self.schedule()
        path = self.control / "control.json"
        original = path.read_text()
        for value in (None, "{malformed", '{"schema_version":99}'):
            if value is None:
                path.unlink()
            else:
                path.write_text(value)
            before = self.snapshot()
            self.assert_blocked(self.run_process([self.job(wake_id)], ok=False))
            self.assertEqual(before, self.snapshot())
            self.assert_no_execution()
        path.write_text(original)

    def test_tick_failure_propagates_and_releases_lease_without_marking_fired(self):
        wake_id = self.schedule()
        result = self.wake("_tick-with-lease", wake_id,
                           env=dict(self.env, WAKE_TEST_TICK_EXIT="7"), ok=False)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.state()["awakenings"][0]["status"], "pending")
        self.assertIsNone(self.state()["awakenings"][0]["fired_at"])
        self.assert_stored_tick()

    def test_inbox_failure_prevents_tick_and_releases_lease(self):
        wake_id = self.schedule()
        (self.state_path.parent / "inbox.md").mkdir()
        self.assert_blocked(self.run_process([self.job(wake_id)], ok=False), "inbox.md")
        self.assertFalse((self.base / "ticks.jsonl").exists())
        self.assertFalse((self.control / "wake-leases/mission-tick-lease.json").exists())
        self.assertEqual(self.state()["awakenings"][0]["status"], "pending")

    def test_duplicate_lease_skips_without_marking_or_inbox_and_expired_lease_recovers(self):
        wake_id = self.schedule()
        directory = self.control / "wake-leases"
        directory.mkdir()
        active = directory / "mission-tick-lease.json"
        record = dict(schema_version=1, lease_id="other", wake_id="other", pid=999999,
                      session="other", window="other", acquired_at="2000-01-01T00:00:00Z",
                      expires_at="2099-01-01T00:00:00Z")
        active.write_text(json.dumps(record))
        result = self.run_process([self.job(wake_id)], env=self.poisoned_env())
        self.assertIn("duplicate lease holder", result.stderr)
        self.assertFalse((self.base / "ticks.jsonl").exists())
        self.assertFalse((self.state_path.parent / "inbox.md").exists())
        self.assertIsNone(self.state()["awakenings"][0]["fired_at"])
        events = [json.loads(path.read_text()) for path in (self.control / "events").glob("*.json")]
        self.assertEqual([e["event_type"] for e in events], ["wake-duplicate-skipped"])
        record["expires_at"] = "2000-01-01T00:01:00Z"
        active.write_text(json.dumps(record))
        self.run_process([self.job(wake_id)], env=self.poisoned_env())
        self.assert_stored_tick()
        events = [json.loads(path.read_text()) for path in (self.control / "events").glob("*.json")]
        self.assertEqual(len([e for e in events if e["event_type"] == "wake-lease-recovered"]), 1)

    def test_metadata_is_data_not_shell_code(self):
        args = self.schedule_args()
        marker = self.base / "injected"
        payload = f"literal ' ; touch {marker} ; # $HOME"
        args[args.index("--owner") + 1] = payload
        args.extend(["--label", payload])
        self.wake(*args)
        wake_id = self.state()["awakenings"][0]["id"]
        self.run_process([self.job(wake_id)])
        self.assertFalse(marker.exists())
        self.assertEqual(self.ticks()[0]["env"]["COCKPIT_WAKE_OWNER"], payload)

    def test_malformed_state_has_safe_diagnosis_not_a_traceback(self):
        self.schedule()
        for data in ({"awakenings": [None]}, {"awakenings": [{}]}, {"awakenings": "bad"}):
            self.state_path.write_text(json.dumps(data))
            before = self.snapshot()
            result = self.wake("fire", "wake-1", ok=False)
            self.assert_blocked(result, "retain")
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(before, self.snapshot())
            self.assert_no_execution()

    def test_at_refusal_reports_failure_retaining_cancellable_evidence(self):
        self.assert_blocked(self.wake(*self.schedule_args(),
                                     env=dict(self.env, WAKE_TEST_AT_FAIL="1"), ok=False))
        record = self.state()["awakenings"][0]
        self.assertIn("scheduler_error", record)
        self.wake("stop", record["id"], env=self.poisoned_env(unset=True))
        self.assertEqual(self.state()["awakenings"][0]["status"], "cancelled")


class WakeMigrationTests(WakeCLIFixture):
    def test_migrate_corrupt_future_missing_authority_is_non_destructive(self):
        self.schedule()
        path = self.control / "control.json"
        for value in ("{malformed", '{"schema_version":99}', None):
            if value is None:
                path.unlink()
            else:
                path.write_text(value)
            before = self.snapshot()
            result = self.wake("migrate", ok=False)
            self.assert_blocked(result, "retain all live metadata")
            self.assertNotIn("repair-store", result.stderr)
            self.assertEqual(before, self.snapshot())

    def test_migrate_absent_store_diagnoses_supported_bootstrap_without_creating_it(self):
        root = self.base / "absent"
        env = dict(self.env, COCKPIT_CONTROL_ROOT=str(root))
        before = self.snapshot()
        result = self.wake("migrate", env=env, ok=False)
        self.assert_blocked(result, "cockpit-control init --queue-root")
        self.assertFalse(root.exists())
        self.assertEqual(before, self.snapshot())

    def test_migrate_legacy_wake_diagnoses_reschedule_without_rewriting_history(self):
        self.schedule(cron=True)
        self.change_wake(lambda w: w.pop("control_root"))
        before = self.snapshot()
        self.assert_blocked(self.wake("migrate", ok=False), "explicitly reschedule")
        self.assertEqual(before, self.snapshot())

    def test_migrate_current_is_read_only_and_idempotent(self):
        self.schedule()
        before = self.snapshot()
        for _ in range(2):
            self.assertIn("already current", self.wake("migrate").stdout)
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
