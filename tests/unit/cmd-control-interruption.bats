#!/usr/bin/env bats
# tests/unit/cmd-control-interruption.bats — TH3.E1.US6 deterministic crash and
# interleaving proof for the control store (architecture section 8.4).
#
# Every test here crashes or parks a real coordinated process at an explicitly
# named protocol boundary, proves safety (no corruption, invariants intact) and
# then proves liveness (a later writer completes a real mutation).  Coverage of
# the full section 8.4 matrix is enforced mechanically by
# tests/unit/check-interruption-matrix.sh, which this suite also runs.

load helper

setup() {
	cc_setup_fake_home
	export CONTROL_BIN="$BATS_TEST_DIRNAME/../../bin/cockpit-control"
	export MODULE_DIR="$BATS_TEST_DIRNAME/../../bin"
	unset COCKPIT_CONTROL_ROOT TMUX TMUX_SESSION COCKPIT_SESSION_ID COCKPIT_ID
}

@test "the declared interruption matrix covers architecture section 8.4 deterministically" {
	run bash "$BATS_TEST_DIRNAME/check-interruption-matrix.sh"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "17 declared interruption boundaries and interleavings are deterministically proven"
}

@test "the deterministic test gate rejects sleep-only concurrency coverage" {
	local fixture="$BATS_TEST_TMPDIR/gate-fixture"
	mkdir -p "$fixture"

	printf 'boundary\tbefore-candidate-owner-write\tsleep-only.bats\ta sleep-only regression pretends to prove a boundary\tcandidate-directory-created\n' \
		>"$fixture/sleep-only.tsv"
	printf '@test "%s" {\n' "a sleep-only regression pretends to prove a boundary" \
		>"$fixture/sleep-only.bats"
	cat >>"$fixture/sleep-only.bats" <<'FIXTURE'
	run python3 -c '
import time
time.sleep(0.5)
print("candidate-directory-created")
print("publish_control_event(root)")
'
	[ "$status" -eq 0 ]
}
FIXTURE

	run bash "$BATS_TEST_DIRNAME/check-interruption-matrix.sh" \
		--entries-only --registry "$fixture/sleep-only.tsv" --suite-dir "$fixture"
	[ "$status" -eq 1 ]
	echo "$output" | grep -Fq 'coordinates only by timing; a barrier or fault hook must target boundary "candidate-directory-created"'

	printf 'boundary\tbefore-candidate-owner-write\tdeterministic.bats\ta deterministic regression crashes at the named boundary\tcandidate-directory-created\n' \
		>"$fixture/deterministic.tsv"
	printf '@test "%s" {\n' "a deterministic regression crashes at the named boundary" \
		>"$fixture/deterministic.bats"
	cat >>"$fixture/deterministic.bats" <<'FIXTURE'
	run python3 -c '
import subprocess
writer = subprocess.Popen(["true"])
writer.stdout.readline()
writer.kill()
print("candidate-directory-created")
print("publish_control_event(root)")
'
	[ "$status" -eq 0 ]
}
FIXTURE

	run bash "$BATS_TEST_DIRNAME/check-interruption-matrix.sh" \
		--entries-only --registry "$fixture/deterministic.tsv" --suite-dir "$fixture"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 declared interruption boundaries and interleavings are deterministically proven"
}

@test "a writer killed before the candidate owner write leaves diagnosable private debris" {
	local root="$BATS_TEST_TMPDIR/candidate-before-owner-write"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "candidate-directory-created":
        print("CANDIDATE_DIRECTORY " + lock._candidate_path.name, flush=True)
        sys.stdin.readline()
        raise AssertionError("the candidate barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "writer-killed-before-owner-write",
    timeout_seconds=5,
    poll_seconds=0.01,
).acquire()
"""

writer = subprocess.Popen(
    [sys.executable, "-c", writer_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = writer.stdout.readline().split()
    assert parked and parked[0] == "CANDIDATE_DIRECTORY", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

debris = locks / parked[1]
assert debris.name.startswith(cockpit_control.LOCK_CANDIDATE_PREFIX)
assert debris.is_dir(), "the killed writer left exactly its private candidate directory"
debris_identity = debris.lstat()
assert list(debris.iterdir()) == [], "no owner record existed when the writer died"
assert not authoritative.exists(), "an owner-less candidate is never authoritative"
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
assert list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*")) == [debris]

published = cockpit_control.publish_control_event(
    root,
    "liveness-after-candidate-debris",
    command="writer-after-candidate-debris",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1, "owner-less debris never consumed a revision"
assert not authoritative.exists(), "the later writer released the lock it published"
assert debris.is_dir(), "abandoned candidate debris is retained for diagnosis"
assert cockpit_control._same_filesystem_identity(debris.lstat(), debris_identity)
assert list(debris.iterdir()) == []
print("the owner-less candidate stayed non-authoritative and diagnosable")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the owner-less candidate stayed non-authoritative and diagnosable"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
}

@test "a writer killed after the candidate flush leaves no authoritative lock" {
	local root="$BATS_TEST_TMPDIR/candidate-after-flush"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

writer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "candidate-prepared":
        owner_bytes = (
            lock._candidate_path / cockpit_control.LOCK_OWNER_NAME
        ).read_bytes()
        print(
            "CANDIDATE_PREPARED "
            + lock._candidate_path.name
            + " "
            + owner_bytes.hex(),
            flush=True,
        )
        sys.stdin.readline()
        raise AssertionError("the candidate barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "writer-killed-after-candidate-flush",
    timeout_seconds=5,
    poll_seconds=0.01,
).acquire()
"""

writer = subprocess.Popen(
    [sys.executable, "-c", writer_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = writer.stdout.readline().split()
    assert parked and parked[0] == "CANDIDATE_PREPARED", writer.stderr.read()
finally:
    writer.kill()
    writer.wait(timeout=5)
assert writer.returncode != 0

debris = locks / parked[1]
assert debris.is_dir()
debris_identity = debris.lstat()
owner_path = debris / cockpit_control.LOCK_OWNER_NAME
assert owner_path.read_bytes() == bytes.fromhex(parked[2]), (
    "the flushed candidate owner is byte-exact after the kill"
)
owner = cockpit_control._validate_lock_owner(json.loads(owner_path.read_text()))
assert owner["command"] == "writer-killed-after-candidate-flush"
assert debris.name == cockpit_control.LOCK_CANDIDATE_PREFIX + owner["lock_id"]
assert not authoritative.exists(), "a complete candidate is still not authoritative"
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

with cockpit_control.PortableControlLock(
    root, "writer-after-abandoned-candidate", timeout_seconds=2, poll_seconds=0.01
) as later:
    assert authoritative.is_dir(), "another writer acquired without any repair"
    assert later.owner["lock_id"] != owner["lock_id"]
    assert not cockpit_control._same_filesystem_identity(
        authoritative.lstat(), debris_identity
    )

published = cockpit_control.publish_control_event(
    root,
    "liveness-after-abandoned-candidate",
    command="writer-after-abandoned-candidate",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1
assert not authoritative.exists()
assert cockpit_control._same_filesystem_identity(debris.lstat(), debris_identity)
assert owner_path.read_bytes() == bytes.fromhex(parked[2])
print("the abandoned candidate never became authoritative and blocked nobody")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the abandoned candidate never became authoritative and blocked nobody"

	run "$CONTROL_BIN" list-events
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "1 committed events in $root (latest revision 1)"
}

@test "a releaser killed during release validation leaves the exact lock unchanged" {
	local root="$BATS_TEST_TMPDIR/release-validation-crash"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

releaser_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "releaser-killed-during-validation",
    timeout_seconds=5,
    poll_seconds=0.01,
).acquire()
print("LOCK_HELD " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the releaser barrier was not received")
def park(boundary, transition):
    if boundary == "release-validated":
        print("RELEASE_VALIDATED " + transition.owner["lock_id"], flush=True)
        sys.stdin.readline()
        raise AssertionError("the release barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
lock.release()
"""

releaser = subprocess.Popen(
    [sys.executable, "-c", releaser_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    held = releaser.stdout.readline().split()
    assert held and held[0] == "LOCK_HELD", releaser.stderr.read()
    owner_identity = authoritative.lstat()
    owner_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    releaser.stdin.write("release\n")
    releaser.stdin.flush()
    parked = releaser.stdout.readline().split()
    assert parked and parked[0] == "RELEASE_VALIDATED", releaser.stderr.read()
    assert parked[1] == held[1]
finally:
    releaser.kill()
    releaser.wait(timeout=5)
assert releaser.returncode != 0

assert authoritative.is_dir(), "validation made no filesystem claim before the kill"
assert cockpit_control._same_filesystem_identity(authoritative.lstat(), owner_identity)
assert (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes() == owner_bytes
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))

with cockpit_control.ControlTransitionGuard(
    locks, timeout_seconds=0.5, poll_seconds=0.01
):
    assert authoritative.is_dir(), "the kernel released the dead releaser guard"

try:
    cockpit_control.PortableControlLock(
        root, "blocked-by-dead-releaser", timeout_seconds=0.15, poll_seconds=0.01
    ).acquire()
except cockpit_control.ControlStoreError as error:
    assert "timed out after 0.15s waiting for locks/control.lock" in str(error), str(error)
else:
    raise AssertionError("the dead releaser lock was silently taken over")

repaired = cockpit_control.repair_stale_control_lock(
    root, timeout_seconds=5, poll_seconds=0.01
)
assert repaired.repaired
assert repaired.lock_id == held[1]
assert cockpit_control._same_filesystem_identity(
    repaired.quarantine_path.lstat(), owner_identity
)
assert (
    repaired.quarantine_path / cockpit_control.LOCK_OWNER_NAME
).read_bytes() == owner_bytes

published = cockpit_control.publish_control_event(
    root,
    "liveness-after-release-validation-crash",
    command="writer-after-release-validation-crash",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1
assert not authoritative.exists()
print("the release validation crash left the exact lock recoverable")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the release validation crash left the exact lock recoverable"
}

@test "a releaser killed after the release quarantine rename preserves evidence" {
	local root="$BATS_TEST_TMPDIR/release-quarantine-crash"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

releaser_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]),
    "releaser-killed-after-quarantine",
    timeout_seconds=5,
    poll_seconds=0.01,
).acquire()
print("LOCK_HELD " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the releaser barrier was not received")
def park(boundary, transition):
    if boundary == "release-quarantined":
        print(
            "RELEASE_QUARANTINED " + transition._quarantine_path.name, flush=True
        )
        sys.stdin.readline()
        raise AssertionError("the quarantine barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
lock.release()
"""

releaser = subprocess.Popen(
    [sys.executable, "-c", releaser_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    held = releaser.stdout.readline().split()
    assert held and held[0] == "LOCK_HELD", releaser.stderr.read()
    owner_identity = authoritative.lstat()
    owner_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    releaser.stdin.write("release\n")
    releaser.stdin.flush()
    parked = releaser.stdout.readline().split()
    assert parked and parked[0] == "RELEASE_QUARANTINED", releaser.stderr.read()
finally:
    releaser.kill()
    releaser.wait(timeout=5)
assert releaser.returncode != 0

quarantine = locks / parked[1]
assert quarantine.name.startswith(cockpit_control.LOCK_RELEASED_PREFIX)
assert quarantine.is_dir(), "the quarantine rename preserved the released evidence"
assert not authoritative.exists(), "the quarantined lock is no longer authoritative"
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), owner_identity), (
    "the rename preserved the exact filesystem identity"
)
assert (
    quarantine / cockpit_control.LOCK_OWNER_NAME
).read_bytes() == owner_bytes
assert list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*")) == [quarantine]
assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))
assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))

published = cockpit_control.publish_control_event(
    root,
    "liveness-after-release-quarantine-crash",
    command="writer-after-release-quarantine-crash",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1, "an abandoned quarantine blocked no later writer"
assert not authoritative.exists()
assert quarantine.is_dir(), "quarantine evidence is retained for diagnosis"
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), owner_identity)
assert (quarantine / cockpit_control.LOCK_OWNER_NAME).read_bytes() == owner_bytes
print("the abandoned release quarantine preserved evidence and freed the path")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the abandoned release quarantine preserved evidence and freed the path"
}

@test "two real acquisitions serialize on the shared transition guard and exactly one publishes" {
	local root="$BATS_TEST_TMPDIR/acquire-acquire"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "candidate-prepared":
        print("CANDIDATE " + lock._candidate_path.name, flush=True)
    elif boundary == "acquire-guard-held":
        print("ACQUIRE_GUARD_HELD", flush=True)
        if sys.stdin.readline().strip() != "publish":
            raise RuntimeError("the acquisition guard barrier was not received")
    elif boundary == "lock-published":
        print("ACQUIRED " + lock.owner["lock_id"], flush=True)
cockpit_control._lock_transition_fault = park
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "first-acquirer", timeout_seconds=30, poll_seconds=0.01
).acquire()
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the holder release barrier was not received")
cockpit_control._lock_transition_fault = lambda boundary, transition: None
lock.release()
print("RELEASED", flush=True)
"""

blocked_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
try:
    cockpit_control.PortableControlLock(
        Path(sys.argv[1]), sys.argv[3], timeout_seconds=0.2, poll_seconds=0.01
    ).acquire()
except cockpit_control.ControlStoreError as error:
    print("BLOCKED " + str(error), flush=True)
else:
    print("PUBLISHED", flush=True)
"""

def second_acquirer(label):
    finished = subprocess.run(
        [sys.executable, "-c", blocked_code, str(root), module_dir, label],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout.strip()

holder = subprocess.Popen(
    [sys.executable, "-c", holder_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    candidate = holder.stdout.readline().split()
    assert candidate and candidate[0] == "CANDIDATE", holder.stderr.read()
    parked = holder.stdout.readline().strip()
    assert parked == "ACQUIRE_GUARD_HELD", holder.stderr.read()
    assert not authoritative.exists(), "no lock is published while the guard is held"

    blocked = second_acquirer("second-acquirer-under-guard")
    assert blocked.startswith("BLOCKED "), blocked
    assert blocked.endswith("waiting for locks/control.guard"), blocked
    assert not authoritative.exists()
    assert [
        found.name for found in locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*")
    ] == [candidate[1]], "the blocked acquirer removed only its own candidate"

    holder.stdin.write("publish\n")
    holder.stdin.flush()
    acquired = holder.stdout.readline().split()
    assert acquired and acquired[0] == "ACQUIRED", holder.stderr.read()
    published_identity = authoritative.lstat()
    published_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    assert json.loads(published_bytes)["lock_id"] == acquired[1]
    assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))

    contended = second_acquirer("second-acquirer-after-publication")
    assert contended.startswith("BLOCKED "), contended
    assert contended.endswith("waiting for locks/control.lock"), contended
    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), published_identity
    ), "exactly one owner survived two real acquisitions"
    assert (
        authoritative / cockpit_control.LOCK_OWNER_NAME
    ).read_bytes() == published_bytes

    holder.stdin.write("release\n")
    holder.stdin.flush()
    assert holder.stdout.readline().strip() == "RELEASED", holder.stderr.read()
finally:
    holder.stdin.close()
    try:
        holder.wait(timeout=10)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=5)
assert holder.returncode == 0, holder.stderr.read()

assert not authoritative.exists()
assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))
published = cockpit_control.publish_control_event(
    root,
    "liveness-after-acquire-acquire",
    command="writer-after-acquire-acquire",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1
print("the transition guard serialized two real acquisitions")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the transition guard serialized two real acquisitions"
}

@test "an acquisition holding the transition guard blocks repair until it publishes a live owner" {
	local root="$BATS_TEST_TMPDIR/acquire-repair"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "acquire-guard-held":
        print("ACQUIRE_GUARD_HELD", flush=True)
        if sys.stdin.readline().strip() != "publish":
            raise RuntimeError("the acquisition guard barrier was not received")
    elif boundary == "lock-published":
        print("ACQUIRED " + lock.owner["lock_id"], flush=True)
cockpit_control._lock_transition_fault = park
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "acquirer-under-guard", timeout_seconds=30, poll_seconds=0.01
).acquire()
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the holder release barrier was not received")
cockpit_control._lock_transition_fault = lambda boundary, transition: None
lock.release()
print("RELEASED", flush=True)
"""

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
try:
    result = cockpit_control.repair_stale_control_lock(
        Path(sys.argv[1]), timeout_seconds=0.2, poll_seconds=0.01
    )
except cockpit_control.ControlStoreError as error:
    print("REFUSED " + str(error), flush=True)
else:
    print("REPAIRED " + result.outcome, flush=True)
"""

def attempt_repair():
    finished = subprocess.run(
        [sys.executable, "-c", repair_code, str(root), module_dir],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert finished.returncode == 0, finished.stderr
    return finished.stdout.strip()

holder = subprocess.Popen(
    [sys.executable, "-c", holder_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    assert (
        holder.stdout.readline().strip() == "ACQUIRE_GUARD_HELD"
    ), holder.stderr.read()
    guarded = attempt_repair()
    assert guarded.startswith("REFUSED "), guarded
    assert guarded.endswith("waiting for locks/control.guard"), guarded
    assert not authoritative.exists()
    assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

    holder.stdin.write("publish\n")
    holder.stdin.flush()
    acquired = holder.stdout.readline().split()
    assert acquired and acquired[0] == "ACQUIRED", holder.stderr.read()
    live_identity = authoritative.lstat()
    live_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()

    live = attempt_repair()
    assert live.startswith("REFUSED "), live
    assert "refusing to repair a live locks/control.lock" in live, live
    assert "lock retained" in live, live
    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), live_identity
    )
    assert (
        authoritative / cockpit_control.LOCK_OWNER_NAME
    ).read_bytes() == live_bytes
    assert not list(locks.glob(cockpit_control.LOCK_REPAIRED_PREFIX + "*"))

    holder.stdin.write("release\n")
    holder.stdin.flush()
    assert holder.stdout.readline().strip() == "RELEASED", holder.stderr.read()
finally:
    holder.stdin.close()
    try:
        holder.wait(timeout=10)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.wait(timeout=5)
assert holder.returncode == 0, holder.stderr.read()

assert not authoritative.exists()
absent = attempt_repair()
assert absent == "REPAIRED " + cockpit_control.LOCK_REPAIR_ABSENT, absent
published = cockpit_control.publish_control_event(
    root,
    "liveness-after-acquire-repair",
    command="writer-after-acquire-repair",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published.committed
assert published.revision == 1
print("repair never interleaved with a guarded acquisition or a live owner")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "repair never interleaved with a guarded acquisition or a live owner"
}

@test "a blocked acquirer publishes the moment the holder releases" {
	local root="$BATS_TEST_TMPDIR/release-acquire"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

holder_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "releasing-holder", timeout_seconds=5, poll_seconds=0.01
).acquire()
print("HOLDER_READY " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the holder release barrier was not received")
lock.release()
print("HOLDER_RELEASED", flush=True)
"""

acquirer_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "candidate-prepared":
        print("CANDIDATE_PREPARED " + lock._candidate_path.name, flush=True)
        if sys.stdin.readline().strip() != "go":
            raise RuntimeError("the acquirer candidate barrier was not received")
cockpit_control._lock_transition_fault = park
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "blocked-acquirer", timeout_seconds=30, poll_seconds=0.01
).acquire()
print("ACQUIRER_PUBLISHED " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the acquirer release barrier was not received")
lock.release()
print("ACQUIRER_RELEASED", flush=True)
"""

holder = subprocess.Popen(
    [sys.executable, "-c", holder_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
acquirer = None
try:
    ready = holder.stdout.readline().split()
    assert ready and ready[0] == "HOLDER_READY", holder.stderr.read()
    holder_identity = authoritative.lstat()
    holder_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()

    acquirer = subprocess.Popen(
        [sys.executable, "-c", acquirer_code, str(root), module_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    candidate = acquirer.stdout.readline().split()
    assert candidate and candidate[0] == "CANDIDATE_PREPARED", acquirer.stderr.read()
    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), holder_identity
    ), "a prepared candidate never replaces a published owner"
    assert (
        authoritative / cockpit_control.LOCK_OWNER_NAME
    ).read_bytes() == holder_bytes

    acquirer.stdin.write("go\n")
    acquirer.stdin.flush()
    holder.stdin.write("release\n")
    holder.stdin.flush()
    assert holder.stdout.readline().strip() == "HOLDER_RELEASED", holder.stderr.read()

    published = acquirer.stdout.readline().split()
    assert published and published[0] == "ACQUIRER_PUBLISHED", acquirer.stderr.read()
    assert published[1] != ready[1]
    acquirer_identity = authoritative.lstat()
    assert not cockpit_control._same_filesystem_identity(
        acquirer_identity, holder_identity
    ), "the released lock was replaced by a new private candidate"
    assert (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes() != holder_bytes
    assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*")), (
        "the holder cleaned up its own released quarantine"
    )
    assert not list(locks.glob(cockpit_control.LOCK_CANDIDATE_PREFIX + "*"))

    acquirer.stdin.write("release\n")
    acquirer.stdin.flush()
    assert (
        acquirer.stdout.readline().strip() == "ACQUIRER_RELEASED"
    ), acquirer.stderr.read()
finally:
    for child in (holder, acquirer):
        if child is None:
            continue
        child.stdin.close()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
assert holder.returncode == 0, holder.stderr.read()
assert acquirer.returncode == 0, acquirer.stderr.read()

assert not authoritative.exists()
published_event = cockpit_control.publish_control_event(
    root,
    "liveness-after-release-acquire",
    command="writer-after-release-acquire",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert published_event.committed
assert published_event.revision == 1
print("the blocked acquirer took the lock exactly when the holder released it")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the blocked acquirer took the lock exactly when the holder released it"
}

@test "a stale handle cannot remove the replacement lock published after owner death" {
	local root="$BATS_TEST_TMPDIR/replacement-preservation"
	export COCKPIT_CONTROL_ROOT="$root"
	"$CONTROL_BIN" init >/dev/null

	run python3 -c '
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
module_dir = sys.argv[2]
sys.path.insert(0, module_dir)
import cockpit_control

locks = root / cockpit_control.LOCKS_DIR_NAME
authoritative = locks / cockpit_control.CONTROL_LOCK_NAME

owner_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
def park(boundary, lock):
    if boundary == "lock-published":
        print("LOCK_PUBLISHED " + lock.owner["lock_id"], flush=True)
        sys.stdin.readline()
        raise AssertionError("the publication barrier was released instead of killed")
cockpit_control._lock_transition_fault = park
cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "owner-killed-under-guard", timeout_seconds=5, poll_seconds=0.01
).acquire()
"""

repair_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
result = cockpit_control.repair_stale_control_lock(
    Path(sys.argv[1]), timeout_seconds=10, poll_seconds=0.01
)
assert result.repaired, result.outcome
print("REPAIRED " + result.quarantine_path.name, flush=True)
"""

replacement_code = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
lock = cockpit_control.PortableControlLock(
    Path(sys.argv[1]), "replacement-owner", timeout_seconds=10, poll_seconds=0.01
).acquire()
print("REPLACEMENT " + lock.owner["lock_id"], flush=True)
if sys.stdin.readline().strip() != "release":
    raise RuntimeError("the replacement release barrier was not received")
lock.release()
print("REPLACEMENT_RELEASED", flush=True)
"""

survivor_code = r"""
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import cockpit_control
root = Path(sys.argv[1])
quarantine = root / cockpit_control.LOCKS_DIR_NAME / sys.argv[3]
stale = cockpit_control.PortableControlLock(
    root, "survivor-with-stale-handle", timeout_seconds=1, poll_seconds=0.01
)
stale.owner = cockpit_control._validate_lock_owner(
    json.loads((quarantine / cockpit_control.LOCK_OWNER_NAME).read_text())
)
stale.observed = quarantine.lstat()
try:
    stale.release()
except cockpit_control.ControlStoreError as error:
    print("PRESERVED " + str(error), flush=True)
else:
    print("REMOVED", flush=True)
"""

killed = subprocess.Popen(
    [sys.executable, "-c", owner_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    parked = killed.stdout.readline().split()
    assert parked and parked[0] == "LOCK_PUBLISHED", killed.stderr.read()
    original_identity = authoritative.lstat()
    original_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
finally:
    killed.kill()
    killed.wait(timeout=5)
assert killed.returncode != 0

repair = subprocess.run(
    [sys.executable, "-c", repair_code, str(root), module_dir],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    timeout=30,
)
assert repair.returncode == 0, repair.stderr
repaired = repair.stdout.split()
assert repaired and repaired[0] == "REPAIRED", repair.stdout
quarantine = locks / repaired[1]
assert cockpit_control._same_filesystem_identity(quarantine.lstat(), original_identity)
assert (quarantine / cockpit_control.LOCK_OWNER_NAME).read_bytes() == original_bytes
assert not authoritative.exists()

replacement = subprocess.Popen(
    [sys.executable, "-c", replacement_code, str(root), module_dir],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)
try:
    published = replacement.stdout.readline().split()
    assert published and published[0] == "REPLACEMENT", replacement.stderr.read()
    assert published[1] != parked[1]
    replacement_identity = authoritative.lstat()
    replacement_bytes = (authoritative / cockpit_control.LOCK_OWNER_NAME).read_bytes()
    assert not cockpit_control._same_filesystem_identity(
        replacement_identity, original_identity
    )

    survivor = subprocess.run(
        [sys.executable, "-c", survivor_code, str(root), module_dir, quarantine.name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    assert survivor.returncode == 0, survivor.stderr
    outcome = survivor.stdout.strip()
    assert outcome.startswith("PRESERVED "), outcome
    assert "replacement retained" in outcome, outcome

    assert cockpit_control._same_filesystem_identity(
        authoritative.lstat(), replacement_identity
    ), "the stale handle did not touch the replacement lock"
    assert (
        authoritative / cockpit_control.LOCK_OWNER_NAME
    ).read_bytes() == replacement_bytes
    assert quarantine.is_dir()
    assert (quarantine / cockpit_control.LOCK_OWNER_NAME).read_bytes() == original_bytes
    assert not list(locks.glob(cockpit_control.LOCK_RELEASED_PREFIX + "*"))

    replacement.stdin.write("release\n")
    replacement.stdin.flush()
    assert (
        replacement.stdout.readline().strip() == "REPLACEMENT_RELEASED"
    ), replacement.stderr.read()
finally:
    replacement.stdin.close()
    try:
        replacement.wait(timeout=10)
    except subprocess.TimeoutExpired:
        replacement.kill()
        replacement.wait(timeout=5)
assert replacement.returncode == 0, replacement.stderr.read()

assert not authoritative.exists()
committed = cockpit_control.publish_control_event(
    root,
    "liveness-after-replacement-preservation",
    command="writer-after-replacement-preservation",
    timeout_seconds=5,
    poll_seconds=0.01,
)
assert committed.committed
assert committed.revision == 1
assert quarantine.is_dir(), "repaired quarantine evidence survived every transition"
print("the stale handle preserved the replacement lock published after repair")
' "$root" "$MODULE_DIR"
	[ "$status" -eq 0 ]
	echo "$output" | grep -Fq "the stale handle preserved the replacement lock published after repair"
}
