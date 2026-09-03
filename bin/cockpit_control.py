#!/usr/bin/env python3
"""Versioned control-store root resolution and schema validation.

This module is deliberately dependency-free so installed cockpit commands can
share one fail-closed definition of the VP3 control-store boundary.
"""

from __future__ import annotations

import errno
import fcntl
import json
import math
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from uuid import UUID, uuid4

CONTROL_SCHEMA_VERSION = 1
CONTROL_METADATA_NAME = "control.json"
LEDGER_NAME = "ledger.json"
EVENTS_NAME = "events.jsonl"
COMMANDS_DIR_NAME = "commands"
ESCALATIONS_DIR_NAME = "escalations"
LOCKS_DIR_NAME = "locks"
CONTROL_GUARD_NAME = "control.guard"
CONTROL_LOCK_NAME = "control.lock"
LOCK_OWNER_NAME = "owner.json"
LOCK_CANDIDATE_PREFIX = f".{CONTROL_LOCK_NAME}.candidate-"
LOCK_RELEASED_PREFIX = f"{CONTROL_LOCK_NAME}.released-"
LOCK_REPAIRED_PREFIX = f"{CONTROL_LOCK_NAME}.repaired-"
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_LOCK_POLL_SECONDS = 0.05

# Owner-fate classification.  Only LOCK_OWNER_DEAD is positive proof that the
# publisher of a lock can no longer be running; everything else is refused by
# automatic repair.
LOCK_OWNER_DEAD = "dead"
LOCK_OWNER_ALIVE = "alive"
LOCK_OWNER_UNPROVEN = "unproven"

# Guarded stale-lock repair outcomes.
LOCK_REPAIR_ABSENT = "absent"
LOCK_REPAIR_QUARANTINED = "quarantined"
LOCK_REPAIR_WOULD_QUARANTINE = "would-quarantine"


class ControlStoreError(RuntimeError):
    """Raised when a control-store configuration is unsafe to use."""


@dataclass(frozen=True)
class ResolvedControlRoot:
    """The configured root and the sole source from which it was resolved."""

    path: Path
    source: str


@dataclass(frozen=True)
class StoreInitialization:
    """Result of an idempotent control-store initialization attempt."""

    root: Path
    source: str
    created: bool


def utc_timestamp() -> str:
    """Return a UTC RFC 3339 timestamp suitable for a canonical record."""

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_absolute_root(value: str, source: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ControlStoreError(f"{source} COCKPIT_CONTROL_ROOT is empty")
    if "\x00" in value:
        raise ControlStoreError(f"{source} COCKPIT_CONTROL_ROOT contains a NUL byte")

    candidate = Path(value)
    if not candidate.is_absolute():
        raise ControlStoreError(
            f"{source} COCKPIT_CONTROL_ROOT must be an absolute path; refusing to infer it from cwd"
        )

    # Lexical normalization makes the effective root explicit without resolving
    # symlinks to an unexpected location.  It also keeps a tmux-exported root
    # stable when the shell is launched from another working directory.
    normalized = Path(os.path.normpath(value))
    if not normalized.is_absolute():
        raise ControlStoreError(f"{source} COCKPIT_CONTROL_ROOT is malformed")
    return normalized


def _tmux_control_root() -> Optional[str]:
    """Read the exact root from the active tmux server, if there is one."""

    if not os.environ.get("TMUX"):
        return None
    try:
        result = subprocess.run(
            ["tmux", "show-environment", "COCKPIT_CONTROL_ROOT"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    output = result.stdout.rstrip("\r\n")
    prefix = "COCKPIT_CONTROL_ROOT="
    if not output.startswith(prefix):
        return None
    return output[len(prefix) :]


def resolve_control_root(environ: Optional[Mapping[str, str]] = None) -> ResolvedControlRoot:
    """Resolve the root from the shell, or solely from the active tmux session.

    A shell value is authoritative even when invalid: it must fail rather than
    silently falling back to a different session's store.  No code path consults
    the current working directory.
    """

    environment = os.environ if environ is None else environ
    if "COCKPIT_CONTROL_ROOT" in environment:
        return ResolvedControlRoot(
            _require_absolute_root(environment["COCKPIT_CONTROL_ROOT"], "shell"),
            "shell",
        )

    # The fallback intentionally consults tmux only after the shell variable is
    # absent.  Use the process environment (rather than `environment`) because
    # tmux invocation needs the active client socket.
    tmux_value = _tmux_control_root()
    if tmux_value is not None:
        return ResolvedControlRoot(_require_absolute_root(tmux_value, "tmux session"), "tmux")

    raise ControlStoreError(
        "COCKPIT_CONTROL_ROOT is required; set an absolute root in the shell or active tmux session"
    )


def _require_object(record: Any, label: str) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ControlStoreError(f"{label} must be a JSON object")
    return record


def _require_schema_version(record: Mapping[str, Any], label: str) -> None:
    version = record.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlStoreError(f"{label} requires integer schema_version")
    if version > CONTROL_SCHEMA_VERSION:
        raise ControlStoreError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit tools before mutation"
        )
    if version != CONTROL_SCHEMA_VERSION:
        raise ControlStoreError(f"{label} uses unsupported schema_version {version}")


def _require_string(record: Mapping[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ControlStoreError(f"{label} requires non-empty {field}")
    return value


def _require_uuid(record: Mapping[str, Any], field: str, label: str) -> str:
    value = _require_string(record, field, label)
    try:
        UUID(value)
    except (ValueError, AttributeError):
        raise ControlStoreError(f"{label} requires UUID {field}") from None
    return value


def _require_timestamp(record: Mapping[str, Any], field: str, label: str) -> str:
    value = _require_string(record, field, label)
    if not value.endswith("Z"):
        raise ControlStoreError(f"{label} requires UTC {field}")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ControlStoreError(f"{label} has malformed {field}") from None
    return value


def _require_record_type(record: Mapping[str, Any], expected: str, label: str) -> None:
    if record.get("record_type") != expected:
        raise ControlStoreError(f"{label} requires record_type {expected!r}")


def _require_root_path(value: Any, field: str, label: str) -> str:
    if not isinstance(value, str):
        raise ControlStoreError(f"{label} requires string {field}")
    normalized = str(_require_absolute_root(value, f"{label} {field}"))
    if value != normalized:
        raise ControlStoreError(f"{label} requires normalized absolute {field}")
    return normalized


def _require_optional_root(record: Mapping[str, Any], field: str, label: str) -> Optional[str]:
    """Require a declared root field, allowing an intentionally unassigned root."""

    if field not in record:
        raise ControlStoreError(f"{label} requires {field}")
    value = record[field]
    if value is None:
        return None
    return _require_root_path(value, field, label)


def _require_optional_uuid(record: Mapping[str, Any], field: str, label: str) -> Optional[str]:
    """Require a nullable UUID field without treating a missing field as null."""

    if field not in record:
        raise ControlStoreError(f"{label} requires {field}")
    if record[field] is None:
        return None
    return _require_uuid(record, field, label)


def _require_optional_string(record: Mapping[str, Any], field: str, label: str) -> Optional[str]:
    """Require a nullable non-empty string field."""

    if field not in record:
        raise ControlStoreError(f"{label} requires {field}")
    if record[field] is None:
        return None
    return _require_string(record, field, label)


def _validate_canonical_roots(
    value: Any,
    label: str,
    expected_control_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate the complete root declaration shared by metadata and ledger.

    A root-level store can be initialized before a mission declares its queue,
    planning, or implementation boundary.  Those two roots are consequently
    nullable and the implementation-root collection may be empty.  Once a
    value is declared, however, it is always a normalized absolute path.  The
    fields themselves are never optional, so malformed or partially declared
    canonical-root records cannot pass as an unconfigured store.
    """

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object canonical_roots")

    control_root = _require_root_path(value.get("control_root"), "canonical_roots.control_root", label)
    if expected_control_root is not None and control_root != str(expected_control_root):
        raise ControlStoreError(f"{label} canonical_roots.control_root must match control_root")

    _require_optional_root(value, "queue_root", f"{label} canonical_roots")
    _require_optional_root(value, "planning_root", f"{label} canonical_roots")

    if "implementation_roots" not in value:
        raise ControlStoreError(f"{label} canonical_roots requires implementation_roots")
    implementation_roots = value["implementation_roots"]
    if not isinstance(implementation_roots, list):
        raise ControlStoreError(f"{label} canonical_roots requires array implementation_roots")
    seen_roots = set()
    for index, implementation_root in enumerate(implementation_roots):
        normalized = _require_root_path(
            implementation_root,
            f"canonical_roots.implementation_roots[{index}]",
            label,
        )
        if normalized in seen_roots:
            raise ControlStoreError(
                f"{label} canonical_roots.implementation_roots duplicates root {normalized}"
            )
        seen_roots.add(normalized)
    return value


def validate_root_metadata(record: Any, root: Path) -> Dict[str, Any]:
    """Validate a versioned control root record and its canonical identity."""

    label = CONTROL_METADATA_NAME
    data = _require_object(record, label)
    _require_schema_version(data, label)
    _require_record_type(data, "control-root", label)
    _require_uuid(data, "control_id", label)
    _require_string(data, "cockpit_id", label)
    _require_string(data, "session_id", label)
    _require_timestamp(data, "created_at", label)
    _require_timestamp(data, "last_migration_at", label)
    configured_root = _require_root_path(data.get("control_root"), "control_root", label)
    if configured_root != str(root):
        raise ControlStoreError(
            f"{label} control_root does not match configured COCKPIT_CONTROL_ROOT"
        )
    canonical_roots = _validate_canonical_roots(data.get("canonical_roots"), label, root)
    for field in ("queue_root", "planning_root"):
        if _require_optional_root(data, field, label) != canonical_roots[field]:
            raise ControlStoreError(f"{label} {field} must match canonical_roots.{field}")
    if not isinstance(data.get("implementation_roots"), list):
        raise ControlStoreError(f"{label} requires array implementation_roots")
    if data["implementation_roots"] != canonical_roots["implementation_roots"]:
        raise ControlStoreError(
            f"{label} implementation_roots must match canonical_roots.implementation_roots"
        )
    if not isinstance(data.get("capabilities"), dict):
        raise ControlStoreError(f"{label} requires object capabilities")
    if not isinstance(data.get("tool_capability_versions"), dict):
        raise ControlStoreError(f"{label} requires object tool_capability_versions")
    return data


def validate_ledger(record: Any, control_id: str, root: Optional[Path] = None) -> Dict[str, Any]:
    """Validate the root-level materialized ledger schema."""

    label = LEDGER_NAME
    data = _require_object(record, label)
    _require_schema_version(data, label)
    _require_record_type(data, "ledger", label)
    _require_uuid(data, "ledger_id", label)
    if _require_uuid(data, "control_id", label) != control_id:
        raise ControlStoreError(f"{label} control_id does not match {CONTROL_METADATA_NAME}")
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ControlStoreError(f"{label} requires non-negative integer revision")
    _require_timestamp(data, "created_at", label)
    _require_timestamp(data, "updated_at", label)
    _validate_canonical_roots(data.get("canonical_roots"), label, root)
    active_mission_id = _require_optional_uuid(data, "active_mission_id", label)
    active_queue_item_id = _require_optional_string(data, "active_queue_item_id", label)
    if active_mission_id is not None and active_queue_item_id is None:
        raise ControlStoreError(f"{label} requires active_queue_item_id with active_mission_id")
    return data


def validate_event(record: Any, control_id: str, label: str = "event") -> Dict[str, Any]:
    """Validate an authoritative journal event before it can affect state."""

    data = _require_object(record, label)
    _require_schema_version(data, label)
    _require_record_type(data, "event", label)
    _require_uuid(data, "event_id", label)
    if _require_uuid(data, "control_id", label) != control_id:
        raise ControlStoreError(f"{label} control_id does not match {CONTROL_METADATA_NAME}")
    _require_timestamp(data, "timestamp", label)
    revision = data.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ControlStoreError(f"{label} requires positive integer revision")
    _require_string(data, "event_type", label)
    _require_string(data, "actor", label)
    return data


def validate_command(record: Any, control_id: str, label: str = "command") -> Dict[str, Any]:
    """Validate a durable command envelope and its correlation identifiers."""

    data = _require_object(record, label)
    _require_schema_version(data, label)
    _require_record_type(data, "command", label)
    _require_uuid(data, "command_id", label)
    if _require_uuid(data, "control_id", label) != control_id:
        raise ControlStoreError(f"{label} control_id does not match {CONTROL_METADATA_NAME}")
    _require_uuid(data, "mission_id", label)
    _require_string(data, "queue_item_id", label)
    _require_string(data, "worker_id", label)
    _require_string(data, "command_type", label)
    _require_timestamp(data, "created_at", label)
    return data


def validate_escalation(record: Any, control_id: str, label: str = "escalation") -> Dict[str, Any]:
    """Validate a bounded human-escalation record and its correlations."""

    data = _require_object(record, label)
    _require_schema_version(data, label)
    _require_record_type(data, "escalation", label)
    _require_uuid(data, "escalation_id", label)
    if _require_uuid(data, "control_id", label) != control_id:
        raise ControlStoreError(f"{label} control_id does not match {CONTROL_METADATA_NAME}")
    _require_uuid(data, "mission_id", label)
    _require_string(data, "queue_item_id", label)
    _require_string(data, "status", label)
    _require_timestamp(data, "created_at", label)
    return data


def _require_regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label}: {exc}") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ControlStoreError(f"{label} must be a regular file, not a symlink or directory")


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    _require_regular_file(path, label)
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlStoreError(f"malformed {label}: {exc}") from None


def _require_directory(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise ControlStoreError(f"missing required {label}: {exc}") from None
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ControlStoreError(f"{label} must be a directory, not a symlink or file")


def _validate_events(path: Path, control_id: str) -> None:
    _require_regular_file(path, EVENTS_NAME)
    event_ids = set()
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ControlStoreError(f"{EVENTS_NAME}:{number} is blank; authoritative records cannot be skipped")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ControlStoreError(f"malformed {EVENTS_NAME}:{number}: {exc}") from None
                event = validate_event(record, control_id, f"{EVENTS_NAME}:{number}")
                if event["event_id"] in event_ids:
                    raise ControlStoreError(f"{EVENTS_NAME}:{number} duplicates event_id {event['event_id']}")
                event_ids.add(event["event_id"])
    except (OSError, UnicodeDecodeError) as exc:
        raise ControlStoreError(f"cannot read {EVENTS_NAME}: {exc}") from None


def _validate_record_directory(
    path: Path,
    record_kind: str,
    control_id: str,
) -> None:
    _require_directory(path, record_kind)
    validator = validate_command if record_kind == COMMANDS_DIR_NAME else validate_escalation
    try:
        entries = sorted(path.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ControlStoreError(f"cannot read {record_kind}: {exc}") from None
    identifier = "command_id" if record_kind == COMMANDS_DIR_NAME else "escalation_id"
    identifiers = set()
    for entry in entries:
        if entry.name.startswith("."):
            raise ControlStoreError(f"{record_kind} contains unsupported hidden entry: {entry.name}")
        if entry.suffix != ".json":
            raise ControlStoreError(f"{record_kind} contains unsupported record: {entry.name}")
        record = validator(
            _load_json(entry, f"{record_kind}/{entry.name}"),
            control_id,
            f"{record_kind}/{entry.name}",
        )
        if record[identifier] != entry.stem:
            raise ControlStoreError(
                f"{record_kind}/{entry.name} filename must match {identifier}"
            )
        if record[identifier] in identifiers:
            raise ControlStoreError(f"{record_kind}/{entry.name} duplicates {identifier} {record[identifier]}")
        identifiers.add(record[identifier])


def validate_control_store(root: Path) -> Dict[str, Any]:
    """Validate every authoritative root-level record without changing state."""

    root = _require_absolute_root(str(root), "configured")
    _require_directory(root, "COCKPIT_CONTROL_ROOT")
    metadata_path = root / CONTROL_METADATA_NAME
    ledger_path = root / LEDGER_NAME
    events_path = root / EVENTS_NAME
    metadata = validate_root_metadata(_load_json(metadata_path, CONTROL_METADATA_NAME), root)
    ledger = validate_ledger(_load_json(ledger_path, LEDGER_NAME), metadata["control_id"], root)
    for field in ("control_root", "queue_root", "planning_root", "implementation_roots"):
        if ledger["canonical_roots"][field] != metadata["canonical_roots"][field]:
            raise ControlStoreError(
                f"{LEDGER_NAME} canonical_roots.{field} does not match {CONTROL_METADATA_NAME}"
            )
    _validate_events(events_path, metadata["control_id"])
    _validate_record_directory(root / COMMANDS_DIR_NAME, COMMANDS_DIR_NAME, metadata["control_id"])
    _validate_record_directory(root / ESCALATIONS_DIR_NAME, ESCALATIONS_DIR_NAME, metadata["control_id"])
    _require_directory(root / LOCKS_DIR_NAME, LOCKS_DIR_NAME)
    return metadata


def _write_json(path: Path, record: Mapping[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ControlStoreError(f"cannot write {path.name}: {exc}") from None


def _write_empty_file(path: Path) -> None:
    try:
        descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    except OSError as exc:
        raise ControlStoreError(f"cannot create {path.name}: {exc}") from None


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability on POSIX filesystems, including macOS."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _validated_seconds(value: Any, label: str, *, allow_zero: bool) -> float:
    """Return one finite lock duration without accepting booleans or infinities."""

    minimum_description = "non-negative" if allow_zero else "positive"
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or (value < 0 if allow_zero else value <= 0)
    ):
        raise ControlStoreError(f"{label} must be a {minimum_description} number of seconds")
    return float(value)


def _configured_lock_timeout(timeout_seconds: Optional[float]) -> float:
    if timeout_seconds is not None:
        return _validated_seconds(
            timeout_seconds,
            "control lock timeout",
            allow_zero=True,
        )
    raw = os.environ.get("COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_LOCK_TIMEOUT_SECONDS
    try:
        configured = float(raw)
    except ValueError:
        raise ControlStoreError(
            "COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS must be a non-negative number of seconds"
        ) from None
    return _validated_seconds(
        configured,
        "COCKPIT_CONTROL_LOCK_TIMEOUT_SECONDS",
        allow_zero=True,
    )


def _same_filesystem_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _lock_transition_fault(
    boundary: str,
    transition: Union["PortableControlLock", "ControlLockRepair"],
) -> None:
    """No-op named transition hook used by deterministic protocol tests."""

    del boundary, transition


def _validate_lock_owner(record: Any, label: str = "control lock owner") -> Dict[str, Any]:
    """Validate the complete identity record published with a control lock."""

    owner = _require_object(record, label)
    _require_schema_version(owner, label)
    _require_record_type(owner, "control-lock", label)
    _require_uuid(owner, "lock_id", label)
    pid = owner.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ControlStoreError(f"{label} requires positive integer pid")
    _require_string(owner, "host", label)
    _require_string(owner, "command", label)
    _require_timestamp(owner, "acquired_at", label)
    return owner


def _new_lock_owner(command: str) -> Dict[str, Any]:
    return _validate_lock_owner(
        {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": "control-lock",
            "lock_id": str(uuid4()),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "command": command,
            "acquired_at": utc_timestamp(),
        }
    )


def _prove_lock_owner_death(owner: Mapping[str, Any]) -> Tuple[str, str]:
    """Classify a validated lock owner as positively dead, alive, or unproven.

    Only a same-host owner whose PID no longer exists is positively dead.  A
    remote owner, a PID that cannot be signalled for an unexpected reason, and
    any other ambiguity are `LOCK_OWNER_UNPROVEN` so that automatic repair
    fails closed.  A PID that still exists is reported alive even when it may
    have been recycled, because refusing repair is always the safe direction.
    """

    local_host = socket.gethostname()
    owner_host = owner["host"]
    if owner_host != local_host:
        return (
            LOCK_OWNER_UNPROVEN,
            f"owner host {owner_host!r} is not this host {local_host!r}; "
            "same-host process death cannot be proven",
        )

    pid = owner["pid"]
    if pid == os.getpid():
        return LOCK_OWNER_ALIVE, f"same-host owner pid {pid} is this running process"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return LOCK_OWNER_DEAD, f"same-host owner pid {pid} no longer exists"
    except PermissionError:
        return (
            LOCK_OWNER_ALIVE,
            f"same-host owner pid {pid} is alive under another user",
        )
    except OSError as exc:
        return (
            LOCK_OWNER_UNPROVEN,
            f"cannot determine the fate of same-host owner pid {pid}: {exc}",
        )
    return LOCK_OWNER_ALIVE, f"same-host owner pid {pid} is alive"


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _open_exact_directory(
    path: Path,
    expected: os.stat_result,
    label: str,
) -> int:
    """Open only the directory inode previously identified by the caller."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise ControlStoreError(
            f"cannot inspect {label}; replacement retained: {exc}"
        ) from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise ControlStoreError(f"{label} is not the expected directory; replacement retained")
    if not _same_filesystem_identity(before, expected):
        raise ControlStoreError(f"{label} changed filesystem identity; replacement retained")

    try:
        descriptor = os.open(str(path), _directory_open_flags())
    except OSError as exc:
        raise ControlStoreError(f"cannot open {label}; replacement retained: {exc}") from None

    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
    except OSError as exc:
        os.close(descriptor)
        raise ControlStoreError(f"cannot verify {label}; replacement retained: {exc}") from None
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not _same_filesystem_identity(opened, expected)
        or not _same_filesystem_identity(current, expected)
    ):
        os.close(descriptor)
        raise ControlStoreError(f"{label} changed filesystem identity; replacement retained")
    return descriptor


def _read_lock_owner_from_directory(descriptor: int, label: str) -> Dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    owner_descriptor: Optional[int] = None
    try:
        owner_descriptor = os.open(LOCK_OWNER_NAME, flags, dir_fd=descriptor)
        owner_stat = os.fstat(owner_descriptor)
        if not stat.S_ISREG(owner_stat.st_mode):
            raise ControlStoreError(f"{label}/{LOCK_OWNER_NAME} must be a regular file")
        with os.fdopen(owner_descriptor, "r", encoding="utf-8") as handle:
            owner_descriptor = None
            record = json.load(handle)
    except ControlStoreError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlStoreError(f"malformed {label}/{LOCK_OWNER_NAME}: {exc}") from None
    finally:
        if owner_descriptor is not None:
            os.close(owner_descriptor)
    return _validate_lock_owner(record, f"{label}/{LOCK_OWNER_NAME}")


def _validate_owned_directory(
    path: Path,
    expected_owner: Mapping[str, Any],
    expected_identity: os.stat_result,
    label: str,
    *,
    require_complete_match: bool = False,
) -> None:
    """Require exact directory identity, one owner file, and the expected UUID."""

    descriptor = _open_exact_directory(path, expected_identity, label)
    try:
        try:
            entries = os.listdir(descriptor)
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {label}; lock retained: {exc}") from None
        if entries != [LOCK_OWNER_NAME]:
            raise ControlStoreError(
                f"{label} contains unexpected evidence; lock retained"
            )
        current_owner = _read_lock_owner_from_directory(descriptor, label)
        if current_owner["lock_id"] != expected_owner.get("lock_id"):
            raise ControlStoreError(f"{label} has another owner UUID; replacement retained")
        if require_complete_match and current_owner != dict(expected_owner):
            raise ControlStoreError(f"{label} owner metadata does not match its publisher")
    finally:
        os.close(descriptor)


def _cleanup_owned_directory(
    path: Path,
    expected_owner: Mapping[str, Any],
    expected_identity: os.stat_result,
    label: str,
    *,
    allow_empty: bool = False,
) -> bool:
    """Remove only one exact private candidate or quarantine owned by the caller."""

    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label}; evidence retained: {exc}") from None

    descriptor = _open_exact_directory(path, expected_identity, label)
    remove_owner = False
    try:
        try:
            entries = os.listdir(descriptor)
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {label}; evidence retained: {exc}") from None
        if not entries and allow_empty:
            pass
        elif entries == [LOCK_OWNER_NAME]:
            current_owner = _read_lock_owner_from_directory(descriptor, label)
            if current_owner["lock_id"] != expected_owner.get("lock_id"):
                raise ControlStoreError(f"{label} has another owner UUID; replacement retained")
            remove_owner = True
        else:
            raise ControlStoreError(f"{label} contains unexpected evidence; evidence retained")

        if remove_owner:
            try:
                os.unlink(LOCK_OWNER_NAME, dir_fd=descriptor)
            except OSError as exc:
                raise ControlStoreError(
                    f"cannot clean {label}/{LOCK_OWNER_NAME}; evidence retained: {exc}"
                ) from None

        try:
            current = path.lstat()
        except OSError as exc:
            raise ControlStoreError(
                f"cannot recheck {label}; replacement retained: {exc}"
            ) from None
        if not _same_filesystem_identity(current, expected_identity):
            raise ControlStoreError(f"{label} changed filesystem identity; replacement retained")
        try:
            path.rmdir()
        except OSError as exc:
            raise ControlStoreError(f"cannot clean {label}; evidence retained: {exc}") from None
        _fsync_directory(path.parent)
        return True
    finally:
        os.close(descriptor)


class ControlTransitionGuard(AbstractContextManager):
    """Bounded process-scoped flock serializing lock ownership transitions."""

    def __init__(
        self,
        locks_path: Path,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    ) -> None:
        self.locks_path = locks_path
        self.path = locks_path / CONTROL_GUARD_NAME
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control guard poll interval",
            allow_zero=False,
        )
        self._descriptor: Optional[int] = None

    def acquire(self) -> "ControlTransitionGuard":
        if self._descriptor is not None:
            raise ControlStoreError("control transition guard is already held by this object")
        _require_directory(self.locks_path, LOCKS_DIR_NAME)

        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(str(self.path), flags, 0o600)
        except OSError as exc:
            raise ControlStoreError(f"cannot open {LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}: {exc}") from None

        acquired = False
        try:
            try:
                opened = os.fstat(descriptor)
                current = self.path.lstat()
            except OSError as exc:
                raise ControlStoreError(
                    f"cannot verify {LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}: {exc}"
                ) from None
            if (
                not stat.S_ISREG(opened.st_mode)
                or not _same_filesystem_identity(opened, current)
            ):
                raise ControlStoreError(
                    f"{LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME} must be one stable regular file"
                )
            _fsync_directory(self.locks_path)

            deadline = time.monotonic() + self.timeout_seconds
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EINTR, errno.EACCES, errno.EAGAIN):
                        raise ControlStoreError(
                            f"cannot acquire {LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}: {exc}"
                        ) from None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ControlStoreError(
                            f"timed out after {self.timeout_seconds:g}s waiting for "
                            f"{LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}"
                        ) from None
                    if exc.errno == errno.EINTR:
                        continue
                    time.sleep(min(self.poll_seconds, remaining))

            try:
                opened = os.fstat(descriptor)
                current = self.path.lstat()
            except OSError as exc:
                raise ControlStoreError(
                    f"cannot recheck {LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}: {exc}"
                ) from None
            if not _same_filesystem_identity(opened, current):
                raise ControlStoreError(
                    f"{LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME} changed while waiting; "
                    "transition refused"
                )
            self._descriptor = descriptor
            return self
        except BaseException:
            if acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)
            raise

    def release(self) -> None:
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        unlock_error: Optional[OSError] = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as exc:
            unlock_error = exc
        finally:
            os.close(descriptor)
        if unlock_error is not None:
            raise ControlStoreError(
                f"cannot release {LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}: {unlock_error}"
            )

    def __enter__(self) -> "ControlTransitionGuard":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        self.release()
        return False


def _quarantine_owned_lock_while_guarded(
    lock_path: Path,
    locks_path: Path,
    owner: Mapping[str, Any],
    observed: os.stat_result,
    *,
    prefix: str = LOCK_RELEASED_PREFIX,
    transition: str = "released",
) -> Tuple[Path, os.stat_result]:
    """Atomically move one exact authoritative owner to a private quarantine.

    Release and repair share this single claim step so neither transition can
    unlink a shared pathname after a separate identity check.  The caller must
    already hold the transition guard; the rename itself is the complete claim.
    """

    _validate_owned_directory(lock_path, owner, observed, f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}")
    quarantine_path = locks_path / (
        f"{prefix}{owner['lock_id']}-{uuid4()}"
    )
    try:
        quarantine_path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ControlStoreError(f"cannot prepare {transition} quarantine: {exc}") from None
    else:
        raise ControlStoreError(f"cannot prepare unique {transition} quarantine")

    try:
        os.rename(str(lock_path), str(quarantine_path))
        quarantined = quarantine_path.lstat()
    except OSError as exc:
        raise ControlStoreError(
            f"cannot quarantine exact control lock; lock retained: {exc}"
        ) from None
    if not _same_filesystem_identity(quarantined, observed):
        raise ControlStoreError(
            f"{transition} quarantine changed filesystem identity; evidence retained"
        )
    _fsync_directory(locks_path)
    return quarantine_path, quarantined


class PortableControlLock(AbstractContextManager):
    """Portable bounded control lock with complete publication and exact release."""

    def __init__(
        self,
        root: Path,
        command: str,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.locks_path = self.root / LOCKS_DIR_NAME
        self.path = self.locks_path / CONTROL_LOCK_NAME
        self.command = command.strip() if isinstance(command, str) else ""
        if not self.command:
            raise ControlStoreError("control lock requires a non-empty command")
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control lock poll interval",
            allow_zero=False,
        )
        self.owner: Optional[Dict[str, Any]] = None
        self.observed: Optional[os.stat_result] = None
        self._candidate_path: Optional[Path] = None
        self._quarantine_path: Optional[Path] = None

    def _prepare_candidate(
        self,
    ) -> Tuple[Dict[str, Any], Path, os.stat_result]:
        owner = _new_lock_owner(self.command)
        candidate_path = self.locks_path / f"{LOCK_CANDIDATE_PREFIX}{owner['lock_id']}"
        try:
            candidate_path.mkdir(mode=0o700)
            candidate_identity = candidate_path.lstat()
        except OSError as exc:
            raise ControlStoreError(f"cannot create private control lock candidate: {exc}") from None

        self._candidate_path = candidate_path
        try:
            _write_json(candidate_path / LOCK_OWNER_NAME, owner)
            _fsync_directory(candidate_path)
            _validate_owned_directory(
                candidate_path,
                owner,
                candidate_identity,
                "private control lock candidate",
                require_complete_match=True,
            )
            _lock_transition_fault("candidate-prepared", self)
            return owner, candidate_path, candidate_identity
        except BaseException as exc:
            try:
                _cleanup_owned_directory(
                    candidate_path,
                    owner,
                    candidate_identity,
                    "private control lock candidate",
                    allow_empty=True,
                )
            except ControlStoreError as cleanup_error:
                raise ControlStoreError(
                    f"control lock candidate preparation failed: {exc}; "
                    f"safe unwind was incomplete: {cleanup_error}"
                ) from exc
            finally:
                self._candidate_path = None
            raise

    def acquire(self) -> "PortableControlLock":
        if self.owner is not None:
            raise ControlStoreError("control lock is already held by this object")
        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        _require_directory(self.locks_path, LOCKS_DIR_NAME)
        owner, candidate_path, candidate_identity = self._prepare_candidate()
        deadline = time.monotonic() + self.timeout_seconds
        candidate_published = False

        try:
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                acquired = False

                with ControlTransitionGuard(
                    self.locks_path,
                    timeout_seconds=remaining,
                    poll_seconds=self.poll_seconds,
                ):
                    _lock_transition_fault("acquire-guard-held", self)
                    try:
                        self.path.lstat()
                    except FileNotFoundError:
                        os.rename(str(candidate_path), str(self.path))
                        candidate_published = True
                        self._candidate_path = None
                        _validate_owned_directory(
                            self.path,
                            owner,
                            candidate_identity,
                            f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}",
                            require_complete_match=True,
                        )
                        _fsync_directory(self.locks_path)
                        _lock_transition_fault(
                            "lock-published-before-observed",
                            self,
                        )
                        self.owner = owner
                        self.observed = candidate_identity
                        _lock_transition_fault("lock-published", self)
                        acquired = True
                    except OSError as exc:
                        raise ControlStoreError(f"cannot inspect control lock: {exc}") from None

                if acquired:
                    return self

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ControlStoreError(
                        f"timed out after {self.timeout_seconds:g}s waiting for "
                        f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}"
                    )
                time.sleep(min(self.poll_seconds, remaining))
        except BaseException as exc:
            cleanup_errors: List[str] = []
            quarantine_path: Optional[Path] = None
            quarantine_identity: Optional[os.stat_result] = None
            self.owner = None
            self.observed = None

            if candidate_published:
                try:
                    with ControlTransitionGuard(
                        self.locks_path,
                        timeout_seconds=self.timeout_seconds,
                        poll_seconds=self.poll_seconds,
                    ):
                        quarantine_path, quarantine_identity = (
                            _quarantine_owned_lock_while_guarded(
                                self.path,
                                self.locks_path,
                                owner,
                                candidate_identity,
                            )
                        )
                        self._quarantine_path = quarantine_path
                except BaseException as unwind_error:
                    cleanup_errors.append(str(unwind_error))

                if (
                    quarantine_path is not None
                    and quarantine_identity is not None
                ):
                    try:
                        _cleanup_owned_directory(
                            quarantine_path,
                            owner,
                            quarantine_identity,
                            "released control lock quarantine",
                        )
                        self._quarantine_path = None
                    except ControlStoreError as cleanup_error:
                        cleanup_errors.append(str(cleanup_error))
            else:
                try:
                    _cleanup_owned_directory(
                        candidate_path,
                        owner,
                        candidate_identity,
                        "private control lock candidate",
                    )
                except ControlStoreError as cleanup_error:
                    cleanup_errors.append(str(cleanup_error))
            self._candidate_path = None
            if cleanup_errors:
                raise ControlStoreError(
                    f"control lock acquisition failed: {exc}; "
                    f"safe unwind was incomplete: {'; '.join(cleanup_errors)}"
                ) from exc
            raise

    def release(self) -> None:
        if self.owner is None:
            return
        if self.observed is None:
            raise ControlStoreError("cannot release control lock without filesystem identity")

        owner = self.owner
        observed = self.observed
        quarantine_path: Optional[Path] = None
        quarantine_identity: Optional[os.stat_result] = None
        with ControlTransitionGuard(
            self.locks_path,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ):
            _lock_transition_fault("release-guard-held", self)
            _validate_owned_directory(
                self.path,
                owner,
                observed,
                f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}",
            )
            _lock_transition_fault("release-validated", self)
            quarantine_path, quarantine_identity = _quarantine_owned_lock_while_guarded(
                self.path,
                self.locks_path,
                owner,
                observed,
            )
            self.owner = None
            self.observed = None
            self._quarantine_path = quarantine_path
            _lock_transition_fault("release-quarantined", self)

        try:
            _cleanup_owned_directory(
                quarantine_path,
                owner,
                quarantine_identity,
                "released control lock quarantine",
            )
        except ControlStoreError as exc:
            raise ControlStoreError(
                f"control lock released; quarantine evidence retained: {exc}"
            ) from None
        self._quarantine_path = None

    def __enter__(self) -> "PortableControlLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        self.release()
        return False


@dataclass(frozen=True)
class LockRepairResult:
    """Outcome of one guarded stale-lock repair attempt."""

    root: Path
    outcome: str
    reason: str
    repaired: bool
    lock_id: Optional[str]
    quarantine_path: Optional[Path]


class ControlLockRepair:
    """Guarded stale-lock repair whose only claim is one quarantine rename.

    Repair holds exactly the same `locks/control.guard` transition guard as
    acquisition and release, so no acquisition can publish a replacement lock
    between owner validation and the rename.  There is no shared repair marker:
    interruption before the rename leaves the authoritative lock untouched, and
    interruption after it leaves a unique non-authoritative quarantine while the
    authoritative path is free for a new writer.
    """

    def __init__(
        self,
        root: Path,
        authorized_lock_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.locks_path = self.root / LOCKS_DIR_NAME
        self.path = self.locks_path / CONTROL_LOCK_NAME
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control lock poll interval",
            allow_zero=False,
        )
        self.dry_run = bool(dry_run)
        self.authorized_lock_id = self._validated_authorization(authorized_lock_id)
        self.owner: Optional[Dict[str, Any]] = None
        self.observed: Optional[os.stat_result] = None
        self._quarantine_path: Optional[Path] = None

    @staticmethod
    def _validated_authorization(value: Optional[str]) -> Optional[str]:
        """Require an explicit authorization to name one exact owner UUID."""

        if value is None:
            return None
        return _require_uuid(
            {"authorized_lock_id": value},
            "authorized_lock_id",
            "control lock repair",
        )

    def _observe_owner_while_guarded(
        self,
    ) -> Optional[Tuple[Dict[str, Any], os.stat_result]]:
        """Read the exact published owner, or report that no lock is published."""

        label = f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}"
        try:
            observed = self.path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {label}: {exc}") from None

        descriptor = _open_exact_directory(self.path, observed, label)
        try:
            try:
                entries = os.listdir(descriptor)
            except OSError as exc:
                raise ControlStoreError(f"cannot inspect {label}; lock retained: {exc}") from None
            if entries != [LOCK_OWNER_NAME]:
                raise ControlStoreError(
                    f"malformed {label}: expected exactly {LOCK_OWNER_NAME}; lock retained"
                )
            owner = _read_lock_owner_from_directory(descriptor, label)
        finally:
            os.close(descriptor)

        self.owner = owner
        self.observed = observed
        return owner, observed

    def _authorize_while_guarded(self, owner: Mapping[str, Any]) -> str:
        """Decide, under the guard, whether this exact owner may be repaired."""

        label = f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}"
        lock_id = owner["lock_id"]
        if self.authorized_lock_id is not None and self.authorized_lock_id != lock_id:
            raise ControlStoreError(
                f"repair authorization names lock {self.authorized_lock_id}, but {label} "
                f"is owned by {lock_id}; lock retained"
            )

        state, reason = _prove_lock_owner_death(owner)
        if state == LOCK_OWNER_ALIVE:
            raise ControlStoreError(
                f"refusing to repair a live {label}: {reason}; lock retained"
            )
        if state == LOCK_OWNER_DEAD:
            return f"proven same-host owner death: {reason}"
        if self.authorized_lock_id is None:
            raise ControlStoreError(
                f"refusing to repair {label} without proof of owner death: {reason}; "
                f"lock retained; re-run with explicit authorization for lock {lock_id}"
            )
        return f"explicit guarded authorization for lock {lock_id}: {reason}"

    def _result(
        self,
        outcome: str,
        reason: str,
        repaired: bool,
        lock_id: Optional[str],
        quarantine_path: Optional[Path],
    ) -> LockRepairResult:
        return LockRepairResult(
            root=self.root,
            outcome=outcome,
            reason=reason,
            repaired=repaired,
            lock_id=lock_id,
            quarantine_path=quarantine_path,
        )

    def run(self) -> LockRepairResult:
        """Perform at most one guarded repair claim and report what happened."""

        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        _require_directory(self.locks_path, LOCKS_DIR_NAME)
        self.owner = None
        self.observed = None
        self._quarantine_path = None

        with ControlTransitionGuard(
            self.locks_path,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ):
            _lock_transition_fault("repair-guard-held", self)
            published = self._observe_owner_while_guarded()
            if published is None:
                return self._result(
                    LOCK_REPAIR_ABSENT,
                    f"no {LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME} is published",
                    False,
                    None,
                    None,
                )

            owner, observed = published
            reason = self._authorize_while_guarded(owner)
            # Interruption at this boundary must leave the lock unchanged: the
            # repair has made no filesystem claim yet.
            _lock_transition_fault("repair-validated", self)
            if self.dry_run:
                return self._result(
                    LOCK_REPAIR_WOULD_QUARANTINE,
                    reason,
                    False,
                    owner["lock_id"],
                    None,
                )

            quarantine_path, _identity = _quarantine_owned_lock_while_guarded(
                self.path,
                self.locks_path,
                owner,
                observed,
                prefix=LOCK_REPAIRED_PREFIX,
                transition="repaired",
            )
            self._quarantine_path = quarantine_path
            # The rename is the whole claim.  Nothing is deleted, so interruption
            # here leaves recoverable evidence and a free authoritative path.
            _lock_transition_fault("repair-quarantined", self)
            return self._result(
                LOCK_REPAIR_QUARANTINED,
                reason,
                True,
                owner["lock_id"],
                quarantine_path,
            )


def repair_stale_control_lock(
    root: Path,
    authorized_lock_id: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> LockRepairResult:
    """Quarantine one provably stale or explicitly authorized control lock."""

    return ControlLockRepair(
        root,
        authorized_lock_id=authorized_lock_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
    ).run()


def _session_identity() -> str:
    """Return an explicit cockpit session identity without consulting cwd."""

    configured = os.environ.get("TMUX_SESSION") or os.environ.get("COCKPIT_SESSION_ID")
    if configured:
        return configured
    if os.environ.get("TMUX"):
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#S"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            result = None
        if result is not None and result.returncode == 0:
            session = result.stdout.rstrip("\r\n")
            if session:
                return session
    return "shell"


def _initial_records(root: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    created_at = utc_timestamp()
    control_id = str(uuid4())
    session_id = _session_identity()
    cockpit_id = os.environ.get("COCKPIT_ID") or session_id
    canonical_roots = {
        "control_root": str(root),
        "queue_root": None,
        "planning_root": None,
        "implementation_roots": [],
    }
    metadata = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "record_type": "control-root",
        "control_id": control_id,
        "cockpit_id": cockpit_id,
        "session_id": session_id,
        "control_root": str(root),
        "canonical_roots": canonical_roots,
        "queue_root": None,
        "planning_root": None,
        "implementation_roots": list(canonical_roots["implementation_roots"]),
        "capabilities": {"control_store": CONTROL_SCHEMA_VERSION},
        "tool_capability_versions": {"cockpit-control": CONTROL_SCHEMA_VERSION},
        "created_at": created_at,
        "last_migration_at": created_at,
    }
    ledger = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "record_type": "ledger",
        "ledger_id": str(uuid4()),
        "control_id": control_id,
        "revision": 0,
        "active_queue_item_id": None,
        "active_mission_id": None,
        "canonical_roots": canonical_roots,
        "created_at": created_at,
        "updated_at": created_at,
    }
    return metadata, ledger


def _root_has_entries(root: Path) -> bool:
    try:
        next(root.iterdir())
    except StopIteration:
        return False
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect COCKPIT_CONTROL_ROOT: {exc}") from None
    return True


def _create_store_atomically(root: Path) -> None:
    parent = root.parent
    if not parent.is_dir():
        raise ControlStoreError(f"parent directory does not exist for COCKPIT_CONTROL_ROOT: {parent}")
    if parent.is_symlink():
        raise ControlStoreError(f"parent directory for COCKPIT_CONTROL_ROOT must not be a symlink: {parent}")

    try:
        temporary_root = Path(tempfile.mkdtemp(prefix=".cockpit-control-", dir=str(parent)))
    except OSError as exc:
        raise ControlStoreError(f"cannot create control store under {parent}: {exc}") from None

    try:
        temporary_root.chmod(0o700)
        for name in (COMMANDS_DIR_NAME, ESCALATIONS_DIR_NAME, LOCKS_DIR_NAME):
            (temporary_root / name).mkdir(mode=0o700)
        metadata, ledger = _initial_records(root)
        _write_json(temporary_root / CONTROL_METADATA_NAME, metadata)
        _write_json(temporary_root / LEDGER_NAME, ledger)
        _write_empty_file(temporary_root / EVENTS_NAME)
        _fsync_directory(temporary_root)
        os.replace(str(temporary_root), str(root))
        _fsync_directory(parent)
    except OSError as exc:
        raise ControlStoreError(f"cannot atomically initialize COCKPIT_CONTROL_ROOT: {exc}") from None
    finally:
        if temporary_root.exists():
            shutil.rmtree(str(temporary_root), ignore_errors=True)


def initialize_control_store(
    resolved_root: Optional[ResolvedControlRoot] = None,
) -> StoreInitialization:
    """Create a complete store atomically, or validate an existing one unchanged."""

    resolved = resolved_root or resolve_control_root()
    root = resolved.path
    if root.exists() or root.is_symlink():
        if root.is_symlink() or not root.is_dir():
            raise ControlStoreError("COCKPIT_CONTROL_ROOT must be a directory, not a symlink or file")
        if _root_has_entries(root):
            validate_control_store(root)
            return StoreInitialization(root=root, source=resolved.source, created=False)

    _create_store_atomically(root)
    # Re-read the just-published files through the normal fail-closed validator.
    validate_control_store(root)
    return StoreInitialization(root=root, source=resolved.source, created=True)


def _print_error(error: Exception) -> int:
    print(f"cockpit-control: {error}", file=os.sys.stderr)
    return 1


def _report_lock_repair(result: LockRepairResult) -> int:
    """Print one guarded repair outcome on stdout and succeed."""

    if result.outcome == LOCK_REPAIR_ABSENT:
        print(
            f"cockpit-control: no {LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME} to repair "
            f"in {result.root}"
        )
    elif result.outcome == LOCK_REPAIR_WOULD_QUARANTINE:
        print(
            f"cockpit-control: would quarantine control lock {result.lock_id} "
            f"({result.reason}); no state changed"
        )
    else:
        quarantine = result.quarantine_path
        name = "" if quarantine is None else quarantine.name
        print(
            f"cockpit-control: quarantined control lock {result.lock_id} as "
            f"{LOCKS_DIR_NAME}/{name} ({result.reason})"
        )
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the small control-store CLI used by humans and controller commands."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="cockpit-control",
        description=(
            "initialize, validate, and guardedly repair the versioned cockpit control store"
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init", help="atomically initialize an explicit control root")
    subcommands.add_parser("validate", help="validate an explicit control root without mutation")
    repair = subcommands.add_parser(
        "repair-lock",
        help="quarantine one provably stale control lock under the transition guard",
    )
    repair.add_argument(
        "--authorize",
        metavar="LOCK_ID",
        default=None,
        help=(
            "explicitly authorize repairing this exact owner UUID when same-host "
            "owner death cannot be proven"
        ),
    )
    repair.add_argument(
        "--dry-run",
        action="store_true",
        help="report the guarded repair decision without changing any state",
    )
    args = parser.parse_args(argv)

    try:
        resolved = resolve_control_root()
        if args.command == "init":
            result = initialize_control_store(resolved)
            verb = "initialized" if result.created else "validated"
            print(f"cockpit-control: {verb} {result.root} (source: {result.source})")
            return 0
        if args.command == "repair-lock":
            return _report_lock_repair(
                repair_stale_control_lock(
                    resolved.path,
                    authorized_lock_id=args.authorize,
                    dry_run=args.dry_run,
                )
            )
        validate_control_store(resolved.path)
        print(f"cockpit-control: valid {resolved.path} (source: {resolved.source})")
        return 0
    except ControlStoreError as exc:
        return _print_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
