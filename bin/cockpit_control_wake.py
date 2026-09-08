"""Wake/scheduling seam for wake intent, lease safety, and tick integration."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from uuid import uuid4

# Facade overrides these placeholders with authoritative values.
CONTROL_SCHEMA_VERSION = 1


class ControlStoreError(RuntimeError):
    """Facade overrides this class to preserve compatibility of raised errors."""


publish_control_event: Optional[Callable[..., Any]] = None


def bind_facade(namespace: Mapping[str, Any]) -> None:
    """Bind facade symbols into this seam module without clobbering seam code."""

    module_name = __name__
    for name, value in namespace.items():
        if name.startswith("__"):
            continue
        current = globals().get(name)
        if current is not None:
            current_module = getattr(current, "__module__", None)
            if (
                name != "ControlStoreError"
                and current_module == module_name
                and (callable(current) or isinstance(current, type))
            ):
                continue
        globals()[name] = value


WAKE_LEASE_DIR = "wake-leases"
WAKE_LEASE_RECOVERED_DIR = "recovered"
WAKE_LEASE_RELEASED_DIR = "released"
WAKE_LEASE_FILE = "mission-tick-lease.json"
WAKE_LEASE_TTL_SECONDS = 120
CRON_TAG = "# cockpit-wake:"


@dataclass(frozen=True)
class WakeIntent:
    """One persisted wake intent record, preserving the existing on-wire shape."""

    wake_id: str
    label: str
    wake_type: str
    cron_expr: Optional[str]
    scheduled_at: Optional[str]
    at_job_id: Optional[str]
    session: str
    window: str
    message: str
    status: str
    created_at: str
    fired_at: Optional[str]
    mission: str
    queue_item: str
    owner: str
    intent: str
    stop_condition: str
    cadence: str
    blocker_threshold: int
    lifecycle_state: str
    control_root: str = ""


def read_crontab() -> Sequence[str]:
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True,
        env=dict(os.environ, LC_ALL="C"),
    )
    if result.returncode == 0:
        return result.stdout.splitlines()
    if result.returncode == 1 and "no crontab for" in result.stderr.lower():
        return []
    raise ControlStoreError(
        f"cannot read crontab; refusing to replace scheduler state: {result.stderr.strip()}"
    )


def write_crontab(lines: Sequence[str]) -> None:
    content = "\n".join(lines) + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def add_cron_line(cron_expr: str, job_script: str, wake_id: str) -> None:
    lines = list(read_crontab())
    # cron treats percent as stdin/newline even inside shell quotes.
    command = shlex.quote(job_script).replace("%", r"\%")
    lines.append(f"{cron_expr} {command}  {CRON_TAG}{wake_id}")
    write_crontab(lines)


def remove_cron_line(wake_id: str) -> None:
    lines = list(read_crontab())
    filtered = [line for line in lines if not line.rstrip().endswith(f"{CRON_TAG}{wake_id}")]
    write_crontab(filtered)


def schedule_at(at_bin: str, timespec: str, job_script: str) -> Optional[str]:
    """Submit one job script through `at`; return job number when available."""

    result = subprocess.run(
        [at_bin, timespec],
        input=job_script,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ControlStoreError(f"at refused wake schedule: {result.stderr.strip()}")
    for line in result.stderr.splitlines():
        if line.startswith("job "):
            return line.split()[1]
    raise ControlStoreError("at returned no job identity; inspect scheduler before retrying")


def remove_at_job(job_number: str) -> None:
    if not str(job_number).isdigit():
        raise ControlStoreError("stored at job identity must be numeric; inspect scheduler manually")
    result = subprocess.run(["atrm", str(job_number)], capture_output=True, text=True)
    if result.returncode:
        raise ControlStoreError(f"atrm refused cancellation: {result.stderr.strip()}")


def build_wake_intent(
    wake_id: str,
    session: str,
    window: str,
    message: str,
    *,
    label: str = "",
    wake_type: str,
    cron_expr: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    at_job_id: Optional[str] = None,
    created_at: Optional[str] = None,
    mission: str = "",
    queue_item: str = "",
    owner: str = "",
    intent: str = "",
    stop_condition: str = "",
    cadence: str = "",
    blocker_threshold: int = 0,
    lifecycle_state: str = "pending",
    control_root: str = "",
) -> WakeIntent:
    return WakeIntent(
        wake_id=wake_id,
        label=label or "",
        wake_type=wake_type,
        cron_expr=cron_expr,
        scheduled_at=scheduled_at,
        at_job_id=at_job_id,
        session=session,
        window=window,
        message=message,
        status="pending",
        created_at=created_at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        fired_at=None,
        mission=mission or "",
        queue_item=queue_item or "",
        owner=owner or "",
        intent=intent or "",
        stop_condition=stop_condition or "",
        cadence=cadence or "",
        blocker_threshold=int(blocker_threshold),
        lifecycle_state=lifecycle_state or "pending",
        control_root=control_root,
    )


def wake_intent_to_dict(wake: WakeIntent) -> Dict[str, Any]:
    return {
        "id": wake.wake_id,
        "label": wake.label,
        "type": wake.wake_type,
        "cron_expr": wake.cron_expr,
        "scheduled_at": wake.scheduled_at,
        "at_job_id": wake.at_job_id,
        "session": wake.session,
        "window": wake.window,
        "target": {"session": wake.session, "window": wake.window},
        "control_root": wake.control_root,
        "message": wake.message,
        "status": wake.status,
        "created_at": wake.created_at,
        "fired_at": wake.fired_at,
        "mission": wake.mission,
        "queue_item": wake.queue_item,
        "owner": wake.owner,
        "intent": wake.intent,
        "stop_condition": wake.stop_condition,
        "cadence": wake.cadence,
        "blocker_threshold": wake.blocker_threshold,
        "lifecycle_state": wake.lifecycle_state,
    }


def validate_wake_identity(wake: Mapping[str, Any]) -> None:
    """Reject legacy/ambiguous identity rather than deriving it from ambient context."""

    for field in ("id", "mission", "queue_item", "owner", "intent", "stop_condition",
                  "cadence", "control_root", "session", "window", "lifecycle_state"):
        value = wake.get(field)
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ControlStoreError(f"wake requires stored {field}")
    if not re.fullmatch(r"wake-[A-Za-z0-9_-]+", wake["id"]):
        raise ControlStoreError("wake id is not a safe scheduler identity")
    if not Path(wake["control_root"]).is_absolute():
        raise ControlStoreError("stored control_root must be an absolute path")
    target = wake.get("target")
    if not isinstance(target, dict) or target != {
        "session": wake["session"], "window": wake["window"]
    }:
        raise ControlStoreError("wake requires unambiguous stored target session/window")
    for field in ("session", "window"):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*" if field == "session"
                            else r"[A-Za-z0-9_][A-Za-z0-9_.-]*", target[field]):
            raise ControlStoreError(f"wake target {field} must be a literal tmux name")
    if type(wake.get("blocker_threshold")) is not int or wake["blocker_threshold"] <= 0:
        raise ControlStoreError("wake blocker_threshold must be a positive integer")
    if wake.get("type") not in ("once", "cron"):
        raise ControlStoreError("wake type must be once or cron")


def wake_environment(wake: Mapping[str, Any], queue_root: str) -> Dict[str, str]:
    """Build a child environment from authoritative state, never inherited wake hints."""

    validate_wake_identity(wake)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("COCKPIT_WAKE_")}
    env["COCKPIT_CONTROL_ROOT"] = wake["control_root"]
    env["COCKPIT_QUEUE_ROOT"] = queue_root
    for field in ("id", "label", "session", "window", "mission", "queue_item",
                  "owner", "intent", "stop_condition", "cadence",
                  "blocker_threshold", "lifecycle_state"):
        env[f"COCKPIT_WAKE_{field.upper()}"] = str(wake.get(field, ""))
    return env


def guard_wake_fire(wake: Mapping[str, Any], wake_id: str, state_file: Path) -> Tuple[bool, str]:
    """Return whether a generated wake may run, or why it must stop."""

    try:
        validate_wake_identity(wake)
    except ControlStoreError as exc:
        return False, (
            f"operationally-blocked (ADR-017): legacy/malformed wake {wake_id}: {exc}; "
            "inspect retained state, stop/cancel the old schedule, then explicitly schedule "
            "a new controller wake with full intent and configured distinct roots. "
            "No direct-message execution or automatic history migration is supported."
        )

    status = wake.get("status", "pending")
    lifecycle = wake.get("lifecycle_state", "")
    terminal_lifecycles = {"completed", "cancelled", "superseded", "terminal", "human-suspended"}
    if (status not in ("pending", "fired")
            or (status == "fired" and wake["type"] != "cron")
            or lifecycle in terminal_lifecycles
            or lifecycle not in ("pending", "active", "running")):
        return False, f"skipped: wake {wake_id} has lifecycle/status {lifecycle}/{status}"

    if wake.get("stop_condition_fulfilled"):
        return False, f"skipped: wake {wake_id} stop condition already fulfilled"

    return True, ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_timestamp(moment: Optional[datetime] = None) -> str:
    stamp = (moment or _utc_now()).isoformat(timespec="seconds")
    return stamp.replace("+00:00", "Z")


def _parse_utc_timestamp(value: str, label: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ControlStoreError(f"wake lease {label} must be an RFC3339 timestamp") from None


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_wake_event(root: Path, event_type: str, payload: Dict[str, Any]) -> None:
    if publish_control_event is None:
        raise ControlStoreError("wake seam is not bound to publish_control_event")
    publish_control_event(
        root,
        event_type,
        actor="cockpit-wake",
        payload=payload,
        command="cockpit-wake lease",
    )


def _load_lease_record(path: Path) -> Tuple[Dict[str, Any], os.stat_result]:
    label = f"{WAKE_LEASE_DIR}/{WAKE_LEASE_FILE}"
    try:
        first_identity = path.lstat()
        raw = path.read_text()
        second_identity = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ControlStoreError(f"cannot read {label}: {exc}") from None
    if first_identity.st_dev != second_identity.st_dev or first_identity.st_ino != second_identity.st_ino:
        raise ControlStoreError(f"{label} changed while being read; retry acquisition")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ControlStoreError(f"{label} is malformed JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ControlStoreError(f"{label} must be a JSON object")
    return data, second_identity


def lease_paths(root: Path) -> Tuple[Path, Path, Path, Path]:
    lease_dir = root / WAKE_LEASE_DIR
    recovered_dir = lease_dir / WAKE_LEASE_RECOVERED_DIR
    released_dir = lease_dir / WAKE_LEASE_RELEASED_DIR
    active = lease_dir / WAKE_LEASE_FILE
    return lease_dir, recovered_dir, released_dir, active


def _ensure_lease_dirs(root: Path) -> Tuple[Path, Path, Path, Path]:
    lease_dir, recovered_dir, released_dir, active = lease_paths(root)
    for directory in (lease_dir, recovered_dir, released_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return lease_dir, recovered_dir, released_dir, active


def _write_owned_lease(path: Path, record: Dict[str, Any]) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _is_expired_lease(record: Dict[str, Any], now: datetime) -> bool:
    expires = record.get("expires_at")
    if not isinstance(expires, str):
        raise ControlStoreError("wake lease expires_at must be a timestamp string")
    return _parse_utc_timestamp(expires, "expires_at") <= now


def _reconcile_expired_lease(
    root: Path,
    wake_id: str,
    reason: str,
    record: Dict[str, Any],
    identity: os.stat_result,
    recovered_dir: Path,
    active_path: Path,
) -> None:
    label = f"{WAKE_LEASE_DIR}/{WAKE_LEASE_FILE}"
    try:
        current = active_path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label} before reconciliation: {exc}") from None
    if current.st_dev != identity.st_dev or current.st_ino != identity.st_ino:
        return

    recovered_path = recovered_dir / f"{active_path.stem}.recovered-{int(time.time())}-{uuid4().hex}.json"
    try:
        os.rename(str(active_path), str(recovered_path))
        _fsync_directory(recovered_dir)
        _fsync_directory(active_path.parent)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControlStoreError(f"cannot reconcile expired {label}: {exc}") from None
    _publish_wake_event(
        root,
        "wake-lease-recovered",
        {
            "wake_id": wake_id,
            "reason": reason,
            "lease_path": str(Path(WAKE_LEASE_DIR) / WAKE_LEASE_FILE),
            "recovered_path": str(Path(WAKE_LEASE_DIR) / WAKE_LEASE_RECOVERED_DIR / recovered_path.name),
            "prior_lease": record,
        },
    )


def acquire_tick_lease(root: Path, wake_id: str, session: str, window: str) -> Tuple[str, Optional[os.stat_result]]:
    """Acquire the active tick lease or report duplicate ownership."""

    now = _utc_now()
    _, recovered_dir, _, active = _ensure_lease_dirs(root)
    lease_id = str(uuid4())
    record = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "lease_id": lease_id,
        "wake_id": wake_id,
        "pid": os.getpid(),
        "session": session,
        "window": window,
        "acquired_at": _utc_timestamp(now),
        "expires_at": _utc_timestamp(now + timedelta(seconds=WAKE_LEASE_TTL_SECONDS)),
    }

    for _attempt in range(8):
        try:
            _write_owned_lease(active, record)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ControlStoreError(f"cannot create {WAKE_LEASE_DIR}/{WAKE_LEASE_FILE}: {exc}") from None
        else:
            _fsync_directory(active.parent)
            return lease_id, active.lstat()

        try:
            existing, identity = _load_lease_record(active)
            expired = _is_expired_lease(existing, _utc_now())
        except FileNotFoundError:
            continue
        except ControlStoreError as exc:
            try:
                identity = active.lstat()
            except FileNotFoundError:
                continue
            _reconcile_expired_lease(
                root,
                wake_id,
                f"invalid-lease:{exc}",
                {"error": str(exc)},
                identity,
                recovered_dir,
                active,
            )
            continue

        if not expired:
            _publish_wake_event(
                root,
                "wake-duplicate-skipped",
                {
                    "wake_id": wake_id,
                    "lease_path": str(Path(WAKE_LEASE_DIR) / WAKE_LEASE_FILE),
                    "held_by": existing,
                },
            )
            return "", None

        _reconcile_expired_lease(root, wake_id, "expired", existing, identity, recovered_dir, active)

    raise ControlStoreError(f"unable to acquire {WAKE_LEASE_DIR}/{WAKE_LEASE_FILE} after bounded retries")


def release_tick_lease(root: Path, lease_id: str, identity: os.stat_result) -> None:
    _, _, released_dir, active = _ensure_lease_dirs(root)
    label = f"{WAKE_LEASE_DIR}/{WAKE_LEASE_FILE}"
    try:
        current = active.lstat()
    except FileNotFoundError:
        raise ControlStoreError(f"cannot release {label}; lease disappeared") from None
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label} during release: {exc}") from None
    if current.st_dev != identity.st_dev or current.st_ino != identity.st_ino:
        raise ControlStoreError(f"cannot release {label}; lease owner changed")

    record, _ = _load_lease_record(active)
    if record.get("lease_id") != lease_id:
        raise ControlStoreError(f"cannot release {label}; lease id mismatch")

    released = released_dir / f"{active.stem}.released-{int(time.time())}-{uuid4().hex}.json"
    try:
        os.rename(str(active), str(released))
        _fsync_directory(released_dir)
        _fsync_directory(active.parent)
    except OSError as exc:
        raise ControlStoreError(f"cannot release {label}: {exc}") from None


def run_wake_controller_tick(
    overseer_bin: str, session: str, window: str, *,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [overseer_bin, "tick", "-s", session, "-w", window], check=False, env=env
    )
