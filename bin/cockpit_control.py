#!/usr/bin/env python3
"""Versioned control-store root resolution, schema validation, and durability.

This module is deliberately dependency-free so installed cockpit commands can
share one fail-closed definition of the VP3 control-store boundary, one portable
control-lock protocol, one immutable event-publication protocol, one read-only
readiness diagnosis, and one explicit guarded repair that never deletes anything.
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
from uuid import UUID, uuid4, uuid5

CONTROL_SCHEMA_VERSION = 1
CONTROL_METADATA_NAME = "control.json"
LEDGER_NAME = "ledger.json"
EVENTS_NAME = "events.jsonl"
# Derived projections are replaced through one reusable temporary path each, so
# an interruption can only leave a named non-authoritative file behind.
LEDGER_TEMPORARY_NAME = f"{LEDGER_NAME}.tmp"
EVENTS_VIEW_TEMPORARY_NAME = f"{EVENTS_NAME}.tmp"
EVENTS_DIR_NAME = "events"
PENDING_DIR_NAME = "pending"
EVENT_FILENAME_SUFFIX = ".json"
# Committed event names sort lexicographically in revision order, so the padded
# width is part of the naming contract rather than a display preference.
EVENT_REVISION_DIGITS = 12
DEFAULT_EVENT_ACTOR = "cockpit-control"
DEFAULT_EVENT_COMMAND = "cockpit-control publish-event"
DEFAULT_LEDGER_COMMAND = "cockpit-control replay-ledger"
# The ledger fields a committed event may declare in its payload.  Nothing else
# in an event can move derived state, so the projection is a total function of
# the committed sequence.
LEDGER_PROJECTION_FIELDS = ("active_queue_item_id", "active_mission_id")
# Derived identity is a function of the control root identity, so every replay
# of the same committed sequence rebuilds byte-identical bytes.
LEDGER_ID_DERIVATION_NAME = "cockpit-control/ledger"
COMMANDS_DIR_NAME = "commands"
ESCALATIONS_DIR_NAME = "escalations"
LOCKS_DIR_NAME = "locks"
# The complete set of directories every control protocol requires.  It is one
# tuple so initialization, preflight, and guarded repair cannot disagree.
REQUIRED_STORE_DIRECTORIES = (
    EVENTS_DIR_NAME,
    PENDING_DIR_NAME,
    COMMANDS_DIR_NAME,
    ESCALATIONS_DIR_NAME,
    LOCKS_DIR_NAME,
)
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

# Immutable event-publication outcomes.
EVENT_COMMITTED = "committed"
EVENT_WOULD_COMMIT = "would-commit"

# Derived ledger-projection outcomes.  A projection is never authority, so the
# only outcomes are "already equals the rebuild" and "was replaced by it".
PROJECTION_CURRENT = "current"
PROJECTION_REBUILT = "rebuilt"
PROJECTION_WOULD_REBUILD = "would-rebuild"

# Why a projection did not equal the deterministic rebuild.
PROJECTION_REASON_CURRENT = "current"
PROJECTION_REASON_MISSING = "missing"
PROJECTION_REASON_CORRUPT = "corrupt"
PROJECTION_REASON_AHEAD = "ahead"
PROJECTION_REASON_STALE = "stale"
PROJECTION_REASON_DIVERGENT = "divergent"
PROJECTION_REASON_VIEW = "derived-view"


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
    # A committed event projects derived state through its payload, so an
    # unstructured payload is refused before it can wedge a projection rebuild.
    if "payload" in data and not isinstance(data["payload"], dict):
        raise ControlStoreError(f"{label} requires an object payload")
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


@dataclass(frozen=True)
class CommittedEvent:
    """One immutable authoritative event file and its verified identity."""

    path: Path
    revision: int
    event_id: str
    record: Dict[str, Any]


@dataclass(frozen=True)
class EventHistory:
    """The committed event sequence plus non-authoritative pending debris."""

    root: Path
    events: Tuple[CommittedEvent, ...]
    pending: Tuple[Path, ...]

    @property
    def latest_revision(self) -> int:
        """Return the latest contiguous committed revision, or 0 when empty."""

        return self.events[-1].revision if self.events else 0


def _event_filename(revision: int, event_id: str) -> str:
    """Build the one committed name that encodes this revision and event ID."""

    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ControlStoreError("a committed event requires a positive integer revision")
    if len(str(revision)) > EVENT_REVISION_DIGITS:
        raise ControlStoreError(
            f"revision {revision} exceeds the {EVENT_REVISION_DIGITS}-digit committed event name width"
        )
    return f"{revision:0{EVENT_REVISION_DIGITS}d}-{event_id}{EVENT_FILENAME_SUFFIX}"


def _parse_event_filename(name: str, label: str) -> Tuple[int, str]:
    """Read the exact revision and event UUID a committed filename declares.

    The filename is part of the commit contract, so anything that is not one
    canonical `<zero-padded-revision>-<event-id>.json` name fails closed rather
    than being ignored as unrelated content.
    """

    expected = f"<{EVENT_REVISION_DIGITS}-digit revision>-<event-id>{EVENT_FILENAME_SUFFIX}"
    if not name.endswith(EVENT_FILENAME_SUFFIX):
        raise ControlStoreError(f"{label} is not a committed event; expected {expected}")
    stem = name[: -len(EVENT_FILENAME_SUFFIX)]
    if len(stem) <= EVENT_REVISION_DIGITS or stem[EVENT_REVISION_DIGITS] != "-":
        raise ControlStoreError(f"{label} is not a committed event; expected {expected}")
    digits = stem[:EVENT_REVISION_DIGITS]
    if not digits.isascii() or not digits.isdigit():
        raise ControlStoreError(f"{label} does not name a zero-padded revision; expected {expected}")
    revision = int(digits)
    if revision < 1:
        raise ControlStoreError(f"{label} does not name a positive revision; expected {expected}")
    event_id = stem[EVENT_REVISION_DIGITS + 1 :]
    try:
        parsed = UUID(event_id)
    except (ValueError, AttributeError):
        raise ControlStoreError(f"{label} does not name a UUID event_id; expected {expected}") from None
    if str(parsed) != event_id:
        raise ControlStoreError(
            f"{label} does not name a canonical lowercase UUID event_id; expected {expected}"
        )
    return revision, event_id


def _read_pending_debris(root: Path) -> Tuple[Path, ...]:
    """List every private publication candidate without judging or removing it."""

    pending_path = root / PENDING_DIR_NAME
    _require_directory(pending_path, PENDING_DIR_NAME)
    try:
        entries = sorted(pending_path.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ControlStoreError(f"cannot read {PENDING_DIR_NAME}: {exc}") from None
    return tuple(entries)


def read_committed_sequence(root: Path, control_id: str) -> Tuple[CommittedEvent, ...]:
    """Read the committed event sequence, failing closed on any ambiguity.

    Only files under `events/` are authority.  A malformed name, a declared
    revision or event ID that disagrees with its filename, a duplicate, an
    invalid record, or a gap makes the whole sequence unusable and names the
    offending or missing revision so an operator can repair exactly one file.
    """

    events_path = root / EVENTS_DIR_NAME
    _require_directory(events_path, EVENTS_DIR_NAME)
    try:
        entries = sorted(events_path.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ControlStoreError(f"cannot read {EVENTS_DIR_NAME}: {exc}") from None

    by_revision: Dict[int, CommittedEvent] = {}
    event_ids: Dict[str, int] = {}
    for entry in entries:
        label = f"{EVENTS_DIR_NAME}/{entry.name}"
        revision, event_id = _parse_event_filename(entry.name, label)
        record = validate_event(_load_json(entry, label), control_id, label)
        if record["revision"] != revision:
            raise ControlStoreError(
                f"{label} declares revision {record['revision']} but its filename commits revision {revision}"
            )
        if record["event_id"] != event_id:
            raise ControlStoreError(
                f"{label} declares event_id {record['event_id']} but its filename commits {event_id}"
            )
        duplicate = by_revision.get(revision)
        if duplicate is not None:
            raise ControlStoreError(
                f"{EVENTS_DIR_NAME} duplicates revision {revision} in {duplicate.path.name} and {entry.name}"
            )
        duplicate_revision = event_ids.get(event_id)
        if duplicate_revision is not None:
            raise ControlStoreError(
                f"{label} duplicates event_id {event_id} already committed at revision {duplicate_revision}"
            )
        by_revision[revision] = CommittedEvent(
            path=entry,
            revision=revision,
            event_id=event_id,
            record=record,
        )
        event_ids[event_id] = revision

    committed: List[CommittedEvent] = []
    for expected, revision in enumerate(sorted(by_revision), start=1):
        if revision != expected:
            offending = by_revision[revision]
            raise ControlStoreError(
                f"{EVENTS_DIR_NAME} is missing revision {expected}; committed revision {revision} "
                f"({EVENTS_DIR_NAME}/{offending.path.name}) cannot become authority until the gap is repaired"
            )
        committed.append(by_revision[revision])

    return tuple(committed)


def read_committed_events(root: Path, control_id: str) -> EventHistory:
    """Read committed authority together with its non-authoritative debris."""

    return EventHistory(
        root=root,
        events=read_committed_sequence(root, control_id),
        pending=_read_pending_debris(root),
    )


def inspect_control_events(root: Path) -> Tuple[Dict[str, Any], EventHistory]:
    """Validate root identity and read the committed sequence without mutation."""

    root = _require_absolute_root(str(root), "configured")
    _require_directory(root, "COCKPIT_CONTROL_ROOT")
    metadata = validate_root_metadata(
        _load_json(root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME), root
    )
    return metadata, read_committed_events(root, metadata["control_id"])


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
    read_committed_events(root, metadata["control_id"])
    _validate_record_directory(root / COMMANDS_DIR_NAME, COMMANDS_DIR_NAME, metadata["control_id"])
    _validate_record_directory(root / ESCALATIONS_DIR_NAME, ESCALATIONS_DIR_NAME, metadata["control_id"])
    _require_directory(root / LOCKS_DIR_NAME, LOCKS_DIR_NAME)
    return metadata


def _serialized_record(record: Mapping[str, Any]) -> str:
    """Render the one canonical serialization every writer and validator uses."""

    try:
        return json.dumps(record, indent=2, sort_keys=True) + "\n"
    except (TypeError, ValueError) as exc:
        raise ControlStoreError(f"cannot serialize control record: {exc}") from None


def _write_new_text(path: Path, text: str) -> None:
    """Create one new flushed file, refusing to replace anything that exists."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(str(path), flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ControlStoreError(f"cannot write {path.name}: {exc}") from None


def _write_json(path: Path, record: Mapping[str, Any]) -> None:
    _write_new_text(path, _serialized_record(record))


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
            # Interruption here leaves an owner-less private directory that no
            # reader treats as authority; it is diagnosable debris only.
            _lock_transition_fault("candidate-directory-created", self)
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


def _derived_ledger_id(control_id: str) -> str:
    """Derive the ledger identity from the control root, never from the ledger.

    A rebuilt projection must not depend on the file it replaces, so the
    identifier is a deterministic function of the authoritative control root
    identity.  Initialization and every later replay therefore agree.
    """

    try:
        namespace = UUID(control_id)
    except (ValueError, AttributeError):
        raise ControlStoreError(f"{CONTROL_METADATA_NAME} requires UUID control_id") from None
    return str(uuid5(namespace, LEDGER_ID_DERIVATION_NAME))


def _event_ledger_declarations(record: Mapping[str, Any], label: str) -> Dict[str, Any]:
    """Return the derived ledger fields one committed event declares."""

    payload = record.get("payload")
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ControlStoreError(f"{label} requires an object payload")
    declared: Dict[str, Any] = {}
    if "active_queue_item_id" in payload:
        declared["active_queue_item_id"] = _require_optional_string(
            payload, "active_queue_item_id", f"{label} payload"
        )
    if "active_mission_id" in payload:
        declared["active_mission_id"] = _require_optional_uuid(
            payload, "active_mission_id", f"{label} payload"
        )
    return declared


def build_ledger_projection(
    metadata: Mapping[str, Any],
    events: Sequence[CommittedEvent] = (),
) -> Dict[str, Any]:
    """Fold the committed event sequence into the one derived ledger record.

    Every input is authoritative and non-derived: the validated control root
    metadata and the contiguous committed events.  The ledger it returns is
    therefore reproducible, so replaying the same committed sequence any number
    of times yields byte-identical bytes and can never invent a revision that no
    committed event represents.
    """

    control_id = _require_uuid(metadata, "control_id", CONTROL_METADATA_NAME)
    created_at = _require_timestamp(metadata, "created_at", CONTROL_METADATA_NAME)
    roots = _validate_canonical_roots(metadata.get("canonical_roots"), CONTROL_METADATA_NAME)

    revision = 0
    updated_at = created_at
    state: Dict[str, Any] = {field: None for field in LEDGER_PROJECTION_FIELDS}
    for event in events:
        label = f"{EVENTS_DIR_NAME}/{event.path.name}"
        state.update(_event_ledger_declarations(event.record, label))
        if state["active_mission_id"] is not None and state["active_queue_item_id"] is None:
            raise ControlStoreError(
                f"{label} projects active_mission_id without active_queue_item_id"
            )
        revision = event.revision
        updated_at = event.record["timestamp"]

    record = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "record_type": "ledger",
        "ledger_id": _derived_ledger_id(control_id),
        "control_id": control_id,
        "revision": revision,
        "active_queue_item_id": state["active_queue_item_id"],
        "active_mission_id": state["active_mission_id"],
        "canonical_roots": {
            "control_root": roots["control_root"],
            "queue_root": roots["queue_root"],
            "planning_root": roots["planning_root"],
            "implementation_roots": list(roots["implementation_roots"]),
        },
        "created_at": created_at,
        "updated_at": updated_at,
    }
    validate_ledger(record, control_id)
    return record


def build_events_view(events: Sequence[CommittedEvent] = ()) -> str:
    """Render the derived `events.jsonl` compatibility view of committed events."""

    lines = []
    for event in events:
        try:
            lines.append(json.dumps(event.record, sort_keys=True, separators=(",", ":")))
        except (TypeError, ValueError) as exc:
            raise ControlStoreError(
                f"cannot render {EVENTS_DIR_NAME}/{event.path.name} into {EVENTS_NAME}: {exc}"
            ) from None
    return "".join(f"{line}\n" for line in lines)


def _reusable_temporary_flags() -> int:
    """Open a reusable projection temporary without following or blocking."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    for name in ("O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return flags


def _write_projection_temporary(path: Path, text: str, label: str) -> os.stat_result:
    """Write, flush, and verify the exact bytes of one projection temporary.

    The temporary path is reused by name because the protocol names it, and any
    debris left there by an interrupted predecessor is non-authoritative.  The
    file is only ever truncated and rewritten in place while the control lock is
    held; it is never unlinked by pathname after a separate identity check.
    """

    data = text.encode("utf-8")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label}: {exc}") from None
    else:
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ControlStoreError(
                f"{label} must be a regular file, not a symlink or directory"
            )

    try:
        descriptor = os.open(str(path), _reusable_temporary_flags(), 0o600)
    except OSError as exc:
        raise ControlStoreError(f"cannot write {label}: {exc}") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            identity = os.fstat(handle.fileno())
            if not stat.S_ISREG(identity.st_mode):
                raise ControlStoreError(
                    f"{label} must be a regular file, not a symlink or directory"
                )
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            identity = os.fstat(handle.fileno())
    except OSError as exc:
        raise ControlStoreError(f"cannot write {label}: {exc}") from None

    try:
        written_identity = path.lstat()
        written = path.read_bytes()
    except OSError as exc:
        raise ControlStoreError(f"cannot verify {label}: {exc}") from None
    if not _same_filesystem_identity(written_identity, identity) or written != data:
        raise ControlStoreError(f"{label} is not the exact projection that was serialized")
    return identity


def _replace_projection(
    temporary_path: Path,
    target_path: Path,
    identity: os.stat_result,
    label: str,
) -> None:
    """Atomically replace one derived projection with its verified temporary."""

    try:
        os.replace(str(temporary_path), str(target_path))
    except OSError as exc:
        raise ControlStoreError(f"cannot atomically replace {label}: {exc}") from None
    try:
        replaced = target_path.lstat()
    except OSError as exc:
        raise ControlStoreError(f"{label} was replaced but cannot be verified: {exc}") from None
    if not _same_filesystem_identity(replaced, identity):
        raise ControlStoreError(f"{label} changed filesystem identity during replacement")


def _ledger_projection_fault(boundary: str, projection: "ControlLedgerProjection") -> None:
    """No-op named projection hook used by deterministic protocol tests."""

    del boundary, projection


@dataclass(frozen=True)
class LedgerProjectionResult:
    """Outcome of one derived ledger projection or replay attempt."""

    root: Path
    outcome: str
    reason: str
    revision: int
    observed_revision: Optional[int]
    path: Path
    record: Dict[str, Any]
    interrupted_temporaries: Tuple[Path, ...]
    pending_debris: Tuple[Path, ...]

    @property
    def current(self) -> bool:
        return self.outcome == PROJECTION_CURRENT

    @property
    def rebuilt(self) -> bool:
        return self.outcome == PROJECTION_REBUILT


class ControlLedgerProjection:
    """Rebuild the derived ledger from committed events and replace it atomically.

    Committed event files are the only authority.  This step reads them under
    the control lock, folds them into one deterministic ledger record, writes
    that record to `ledger.json.tmp`, flushes it, and atomically replaces
    `ledger.json`.  The rebuilt bytes always win: a missing, stale, corrupt, or
    revision-ahead ledger is discarded as derived corruption rather than
    consulted, and an interrupted temporary never overrides the rebuild.

    Because the rebuild is a pure function of committed events, replay is
    idempotent.  Running it repeatedly commits no event and, once the projection
    equals the rebuild, changes nothing at all.
    """

    def __init__(
        self,
        root: Path,
        command: str = DEFAULT_LEDGER_COMMAND,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
        held_lock: Optional[PortableControlLock] = None,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.ledger_path = self.root / LEDGER_NAME
        self.ledger_temporary_path = self.root / LEDGER_TEMPORARY_NAME
        self.view_path = self.root / EVENTS_NAME
        self.view_temporary_path = self.root / EVENTS_VIEW_TEMPORARY_NAME
        self.command = command.strip() if isinstance(command, str) else ""
        if not self.command:
            raise ControlStoreError("ledger projection requires a non-empty command")
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control lock poll interval",
            allow_zero=False,
        )
        self.dry_run = bool(dry_run)
        self.held_lock = held_lock
        self.lock: Optional[PortableControlLock] = None
        self.record: Optional[Dict[str, Any]] = None
        self.revision: Optional[int] = None
        self.observed_revision: Optional[int] = None
        self.reason: Optional[str] = None
        self.interrupted_temporaries: Tuple[Path, ...] = ()
        self.pending_debris: Tuple[Path, ...] = ()

    def _interrupted_temporaries(self) -> Tuple[Path, ...]:
        """List every interrupted projection temporary without judging it."""

        found: List[Path] = []
        for path in (self.view_temporary_path, self.ledger_temporary_path):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ControlStoreError(f"cannot inspect {path.name}: {exc}") from None
            found.append(path)
        return tuple(found)

    def _classify_ledger(self, control_id: str, expected: bytes) -> Tuple[Optional[int], str]:
        """Say why the published projection does or does not equal the rebuild."""

        try:
            mode = self.ledger_path.lstat().st_mode
        except FileNotFoundError:
            return None, PROJECTION_REASON_MISSING
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {LEDGER_NAME}: {exc}") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            return None, PROJECTION_REASON_CORRUPT
        try:
            published = self.ledger_path.read_bytes()
        except OSError:
            return None, PROJECTION_REASON_CORRUPT
        if published == expected:
            return json.loads(expected.decode("utf-8"))["revision"], PROJECTION_REASON_CURRENT
        try:
            existing = validate_ledger(
                json.loads(published.decode("utf-8")), control_id, self.root
            )
        except (ControlStoreError, UnicodeDecodeError, json.JSONDecodeError):
            return None, PROJECTION_REASON_CORRUPT
        observed = existing["revision"]
        rebuilt_revision = json.loads(expected.decode("utf-8"))["revision"]
        if observed > rebuilt_revision:
            return observed, PROJECTION_REASON_AHEAD
        if observed < rebuilt_revision:
            return observed, PROJECTION_REASON_STALE
        return observed, PROJECTION_REASON_DIVERGENT

    def _view_matches(self, expected: str) -> bool:
        """Report whether the derived compatibility view equals the rebuild."""

        try:
            mode = self.view_path.lstat().st_mode
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {EVENTS_NAME}: {exc}") from None
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            return False
        try:
            return self.view_path.read_bytes() == expected.encode("utf-8")
        except OSError:
            return False

    def _result(self, outcome: str) -> LedgerProjectionResult:
        record = dict(self.record or {})
        return LedgerProjectionResult(
            root=self.root,
            outcome=outcome,
            reason=self.reason or PROJECTION_REASON_CURRENT,
            revision=int(self.revision or 0),
            observed_revision=self.observed_revision,
            path=self.ledger_path,
            record=record,
            interrupted_temporaries=self.interrupted_temporaries,
            pending_debris=self.pending_debris,
        )

    def _project(self) -> LedgerProjectionResult:
        """Rebuild and, when it differs, atomically replace the derived state."""

        metadata, history = inspect_control_events(self.root)
        control_id = metadata["control_id"]
        self.pending_debris = history.pending
        record = build_ledger_projection(metadata, history.events)
        if record["revision"] != history.latest_revision:
            raise ControlStoreError(
                f"{LEDGER_NAME} projection revision {record['revision']} does not match the "
                f"committed revision {history.latest_revision}"
            )
        self.record = record
        self.revision = record["revision"]
        ledger_text = _serialized_record(record)
        view_text = build_events_view(history.events)
        self.interrupted_temporaries = self._interrupted_temporaries()

        observed, reason = self._classify_ledger(control_id, ledger_text.encode("utf-8"))
        if reason == PROJECTION_REASON_CURRENT and not self._view_matches(view_text):
            reason = PROJECTION_REASON_VIEW
        self.observed_revision = observed
        self.reason = reason
        _ledger_projection_fault("projection-built", self)

        if reason == PROJECTION_REASON_CURRENT:
            return self._result(PROJECTION_CURRENT)
        if self.dry_run:
            return self._result(PROJECTION_WOULD_REBUILD)

        # The compatibility view is replaced first so that `ledger.json` stays
        # the single watermark of a complete projection: an interruption between
        # the two replacements leaves a stale ledger that the next replay
        # rebuilds, never a ledger that claims state its view does not show.
        view_identity = _write_projection_temporary(
            self.view_temporary_path, view_text, EVENTS_VIEW_TEMPORARY_NAME
        )
        _replace_projection(
            self.view_temporary_path, self.view_path, view_identity, EVENTS_NAME
        )
        _fsync_directory(self.root)
        _ledger_projection_fault("view-replaced", self)

        ledger_identity = _write_projection_temporary(
            self.ledger_temporary_path, ledger_text, LEDGER_TEMPORARY_NAME
        )
        # Interruption here leaves the flushed temporary behind; committed
        # events remain the only authority and `ledger.json` is untouched.
        _ledger_projection_fault("ledger-temporary-written", self)
        _replace_projection(
            self.ledger_temporary_path, self.ledger_path, ledger_identity, LEDGER_NAME
        )
        _fsync_directory(self.root)
        self.interrupted_temporaries = self._interrupted_temporaries()
        _ledger_projection_fault("ledger-replaced", self)
        return self._result(PROJECTION_REBUILT)

    def run(self) -> LedgerProjectionResult:
        """Project committed events into the ledger under the control lock."""

        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        if self.held_lock is not None:
            if self.held_lock.owner is None or self.held_lock.root != self.root:
                raise ControlStoreError(
                    "ledger projection requires the held control lock for this control root"
                )
            self.lock = self.held_lock
            return self._project()

        with PortableControlLock(
            self.root,
            self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ) as lock:
            self.lock = lock
            return self._project()


def replay_control_ledger(
    root: Path,
    command: str = DEFAULT_LEDGER_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> LedgerProjectionResult:
    """Replay committed events into the derived ledger, committing no event."""

    return ControlLedgerProjection(
        root,
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
    ).run()


def _event_publication_fault(boundary: str, publication: "ControlEventPublication") -> None:
    """No-op named publication hook used by deterministic protocol tests."""

    del boundary, publication


@dataclass(frozen=True)
class EventPublicationResult:
    """Outcome of one immutable event-publication attempt."""

    root: Path
    outcome: str
    revision: int
    event_id: str
    path: Path
    record: Dict[str, Any]
    pending_debris: Tuple[Path, ...]
    projection: Optional[LedgerProjectionResult] = None

    @property
    def committed(self) -> bool:
        return self.outcome == EVENT_COMMITTED


class ControlEventPublication:
    """Publish one immutable event whose only commit step is an atomic rename.

    While holding `control.lock` the publisher reads the latest contiguous
    committed revision, writes and flushes one complete private candidate under
    `pending/`, validates its exact serialized bytes, and then renames that
    verified file into `events/`.  Nothing before the rename is authority and
    nothing after it is rewritten, so interruption can only leave a diagnosable
    pending candidate or one complete committed event.

    Once the event is committed, the same held lock is used to rebuild the
    derived ledger projection from committed events and replace it atomically.
    The projection never changes event authority: the rename already committed
    the event, and an interruption before or during the replacement leaves a
    projection that the next replay rebuilds exactly once.
    """

    def __init__(
        self,
        root: Path,
        event_type: str,
        actor: str = DEFAULT_EVENT_ACTOR,
        payload: Optional[Mapping[str, Any]] = None,
        command: str = DEFAULT_EVENT_COMMAND,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.events_path = self.root / EVENTS_DIR_NAME
        self.pending_path = self.root / PENDING_DIR_NAME
        self.event_type = _require_string({"event_type": event_type}, "event_type", "event")
        self.actor = _require_string({"actor": actor}, "actor", "event")
        self.payload = self._validated_payload(payload)
        self.command = command.strip() if isinstance(command, str) else ""
        if not self.command:
            raise ControlStoreError("event publication requires a non-empty command")
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control lock poll interval",
            allow_zero=False,
        )
        self.dry_run = bool(dry_run)
        self.lock: Optional[PortableControlLock] = None
        self.record: Optional[Dict[str, Any]] = None
        self.revision: Optional[int] = None
        self.candidate_path: Optional[Path] = None
        self.committed_path: Optional[Path] = None
        self.pending_debris: Tuple[Path, ...] = ()
        self.projection: Optional[LedgerProjectionResult] = None

    @staticmethod
    def _validated_payload(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """Require structured metadata rather than free-form or unserializable data."""

        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ControlStoreError("event payload must be a JSON object of structured metadata")
        payload = dict(value)
        for key in payload:
            if not isinstance(key, str):
                raise ControlStoreError("event payload requires string field names")
        _serialized_record(payload)
        return payload

    def _build_record(self, control_id: str, revision: int) -> Dict[str, Any]:
        record = {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_type": "event",
            "event_id": str(uuid4()),
            "control_id": control_id,
            "timestamp": utc_timestamp(),
            "revision": revision,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload": self.payload,
        }
        validate_event(record, control_id, "event")
        return record

    def _validate_private_candidate(
        self,
        control_id: str,
        candidate_path: Path,
        record: Mapping[str, Any],
        revision: int,
    ) -> os.stat_result:
        """Prove the flushed candidate holds exactly the bytes that were intended."""

        label = f"{PENDING_DIR_NAME}/{candidate_path.name}"
        _require_regular_file(candidate_path, label)
        try:
            identity = candidate_path.lstat()
            written = candidate_path.read_bytes()
        except OSError as exc:
            raise ControlStoreError(f"cannot verify {label}; candidate retained: {exc}") from None

        if written != _serialized_record(record).encode("utf-8"):
            raise ControlStoreError(
                f"{label} is not the exact event that was serialized; candidate retained"
            )
        stored = validate_event(_load_json(candidate_path, label), control_id, label)
        named_revision, named_event_id = _parse_event_filename(candidate_path.name, label)
        if (
            stored != dict(record)
            or named_revision != revision
            or named_revision != stored["revision"]
            or named_event_id != stored["event_id"]
        ):
            raise ControlStoreError(
                f"{label} does not agree with its allocated revision {revision}; candidate retained"
            )
        return identity

    def _commit_by_rename(
        self,
        candidate_path: Path,
        committed_path: Path,
        candidate_identity: os.stat_result,
    ) -> None:
        """Perform the single atomic rename that turns a candidate into authority."""

        label = f"{EVENTS_DIR_NAME}/{committed_path.name}"
        try:
            committed_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {label}; candidate retained: {exc}") from None
        else:
            raise ControlStoreError(
                f"refusing to replace already committed {label}; candidate retained"
            )

        try:
            os.rename(str(candidate_path), str(committed_path))
        except OSError as exc:
            raise ControlStoreError(f"cannot commit {label}; candidate retained: {exc}") from None
        self.candidate_path = None

        try:
            committed_identity = committed_path.lstat()
        except OSError as exc:
            raise ControlStoreError(f"{label} is committed but cannot be verified: {exc}") from None
        if not _same_filesystem_identity(committed_identity, candidate_identity):
            raise ControlStoreError(
                f"{label} changed filesystem identity during commit; committed evidence retained"
            )
        _fsync_directory(self.events_path)
        _fsync_directory(self.pending_path)

    def _confirm_committed_tip(
        self,
        control_id: str,
        committed_path: Path,
        record: Mapping[str, Any],
        revision: int,
    ) -> None:
        """Re-read committed authority so the new tip is proven, not assumed."""

        history = read_committed_events(self.root, control_id)
        tip = history.events[-1] if history.events else None
        if (
            tip is None
            or history.latest_revision != revision
            or tip.event_id != record["event_id"]
            or tip.record != dict(record)
            or tip.path != committed_path
        ):
            raise ControlStoreError(
                f"{EVENTS_DIR_NAME}/{committed_path.name} did not become the committed tip "
                f"at revision {revision}"
            )

    def _result(
        self,
        outcome: str,
        record: Mapping[str, Any],
        revision: int,
        committed_path: Path,
    ) -> EventPublicationResult:
        return EventPublicationResult(
            root=self.root,
            outcome=outcome,
            revision=revision,
            event_id=record["event_id"],
            path=committed_path,
            record=dict(record),
            pending_debris=self.pending_debris,
            projection=self.projection,
        )

    def run(self) -> EventPublicationResult:
        """Publish at most one immutable event and report exactly what happened."""

        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        _require_directory(self.events_path, EVENTS_DIR_NAME)
        _require_directory(self.pending_path, PENDING_DIR_NAME)

        with PortableControlLock(
            self.root,
            self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ) as lock:
            self.lock = lock
            metadata, history = inspect_control_events(self.root)
            control_id = metadata["control_id"]
            self.pending_debris = history.pending
            revision = history.latest_revision + 1
            record = self._build_record(control_id, revision)
            filename = _event_filename(revision, record["event_id"])
            candidate_path = self.pending_path / filename
            committed_path = self.events_path / filename
            self.revision = revision
            self.record = record
            self.candidate_path = candidate_path
            self.committed_path = committed_path
            # Refuse an event whose derived contribution could never be
            # projected, before any private candidate exists to clean up.
            build_ledger_projection(
                metadata,
                history.events
                + (
                    CommittedEvent(
                        path=committed_path,
                        revision=revision,
                        event_id=record["event_id"],
                        record=record,
                    ),
                ),
            )
            _event_publication_fault("revision-allocated", self)

            if self.dry_run:
                self.candidate_path = None
                return self._result(EVENT_WOULD_COMMIT, record, revision, committed_path)

            _write_json(candidate_path, record)
            _fsync_directory(self.pending_path)
            # Interruption here leaves a private candidate that no reader treats
            # as authority; it is retained as diagnosable evidence.
            _event_publication_fault("candidate-written", self)
            candidate_identity = self._validate_private_candidate(
                control_id, candidate_path, record, revision
            )
            _event_publication_fault("candidate-validated", self)
            self._commit_by_rename(candidate_path, committed_path, candidate_identity)
            self._confirm_committed_tip(control_id, committed_path, record, revision)
            # The rename is the whole commit.  Interruption at this boundary
            # leaves a committed event and an unadvanced projection, which the
            # next replay repairs exactly once without committing anything.
            _event_publication_fault("event-committed", self)
            self.projection = self._project_committed_events(lock, committed_path, revision)
            return self._result(EVENT_COMMITTED, record, revision, committed_path)

    def _project_committed_events(
        self,
        lock: PortableControlLock,
        committed_path: Path,
        revision: int,
    ) -> LedgerProjectionResult:
        """Advance the derived ledger under the lock that committed the event."""

        try:
            return ControlLedgerProjection(
                self.root,
                command=self.command,
                timeout_seconds=self.timeout_seconds,
                poll_seconds=self.poll_seconds,
                held_lock=lock,
            ).run()
        except ControlStoreError as exc:
            raise ControlStoreError(
                f"{EVENTS_DIR_NAME}/{committed_path.name} is committed at revision {revision}; "
                f"only the derived ledger projection failed and must be replayed: {exc}"
            ) from None


def publish_control_event(
    root: Path,
    event_type: str,
    actor: str = DEFAULT_EVENT_ACTOR,
    payload: Optional[Mapping[str, Any]] = None,
    command: str = DEFAULT_EVENT_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> EventPublicationResult:
    """Commit one immutable event under the held control lock."""

    return ControlEventPublication(
        root,
        event_type,
        actor=actor,
        payload=payload,
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
    ).run()


# --- read-only preflight and explicit guarded store repair -------------------

PREFLIGHT_COMMAND = "cockpit-control preflight"
DEFAULT_STORE_REPAIR_COMMAND = "cockpit-control repair-store"

# Every explicit repair moves debris here instead of deleting it, so a repair
# only ever renames non-authoritative evidence into retained evidence.
QUARANTINE_DIR_NAME = "quarantine"

# Preflight dimension states.  A dimension is `undeterminable` when a strictly
# read-only check cannot answer it; preflight never mutates to find out.
PREFLIGHT_READY = "ready"
PREFLIGHT_ADVISORY = "advisory"
PREFLIGHT_UNDETERMINABLE = "undeterminable"
PREFLIGHT_BLOCKED = "blocked"

# What a finding is about.  This classification is the whole point of the
# read-only diagnosis: derived state is rebuildable, debris is retainable, and
# authority is never rewritten automatically.
FINDING_OK = "ok"
FINDING_AUTHORITATIVE = "authoritative"
FINDING_DERIVED = "derived"
FINDING_DEBRIS = "debris"
FINDING_CONFIGURATION = "configuration"

PREFLIGHT_STATUS_READY = "ready"
PREFLIGHT_STATUS_DEGRADED = "degraded"
PREFLIGHT_STATUS_BLOCKED = "blocked"

# The complete set of control-plane dimensions preflight always reports, in the
# order an operator reads them.  Every dimension emits at least one line even
# when an earlier dimension made it undeterminable.
PREFLIGHT_DIMENSIONS = (
    "root",
    "schema",
    "transition-guard",
    "authoritative-lock",
    "quarantines",
    "pending-events",
    "committed-revisions",
    "ledger",
    "queue",
    "worker-capability",
    "declared-paths",
)

# Non-authoritative debris classes this control store can produce.
DEBRIS_PENDING = "pending-candidate"
DEBRIS_PROJECTION = "projection-temporary"
DEBRIS_LOCK_QUARANTINE = "lock-quarantine"
DEBRIS_LOCK_CANDIDATE = "lock-candidate"
DEBRIS_LOCK_UNEXPECTED = "lock-unexpected"

# A private lock candidate may still belong to a live acquirer, so it is only
# ever quarantined under an explicit authorization naming its exact owner UUID.
DEBRIS_REQUIRING_AUTHORIZATION = (DEBRIS_LOCK_CANDIDATE,)
# Content no control protocol publishes is never claimed automatically.
DEBRIS_NEVER_AUTOMATIC = (DEBRIS_LOCK_UNEXPECTED,)

# Guarded store-repair outcomes.
STORE_REPAIR_CURRENT = "current"
STORE_REPAIR_REPAIRED = "repaired"
STORE_REPAIR_WOULD_REPAIR = "would-repair"

REPAIR_ACTION_INIT = "cockpit-control init"
REPAIR_ACTION_REPLAY = "cockpit-control replay-ledger"
REPAIR_ACTION_REPAIR_LOCK = "cockpit-control repair-lock"
REPAIR_ACTION_REPAIR_STORE = DEFAULT_STORE_REPAIR_COMMAND
REPAIR_ACTION_UPGRADE = (
    "upgrade the installed cockpit tools with `bootstrap.sh global`; no mutation is attempted"
)


def _existing_mode(path: Path, label: str) -> Optional[int]:
    """Return the mode of an existing path, or None, without following symlinks."""

    try:
        return path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {label}: {exc}") from None


def _is_plain_directory(path: Path, label: str) -> bool:
    mode = _existing_mode(path, label)
    return mode is not None and not stat.S_ISLNK(mode) and stat.S_ISDIR(mode)


@dataclass(frozen=True)
class StoreDebris:
    """One non-authoritative item an explicit guarded repair may quarantine."""

    path: Path
    relative: str
    kind: str
    lock_id: Optional[str]
    detail: str

    @property
    def requires_authorization(self) -> bool:
        return self.kind in DEBRIS_REQUIRING_AUTHORIZATION

    @property
    def never_automatic(self) -> bool:
        return self.kind in DEBRIS_NEVER_AUTOMATIC

    @property
    def repair_action(self) -> str:
        """Name the exact command an operator runs to claim this exact item."""

        if self.never_automatic:
            return (
                f"move it aside yourself, for example "
                f"`mv {self.path} {self.path}.unexpected`; "
                "cockpit-control never claims content no control protocol publishes"
            )
        if self.requires_authorization:
            return f"{REPAIR_ACTION_REPAIR_STORE} --authorize {self.lock_id}"
        return REPAIR_ACTION_REPAIR_STORE


def _lock_directory_debris(entry: Path) -> Optional[StoreDebris]:
    """Classify one entry of `locks/` as authority, debris, or unexpected content."""

    name = entry.name
    if name in (CONTROL_GUARD_NAME, CONTROL_LOCK_NAME):
        return None
    relative = f"{LOCKS_DIR_NAME}/{name}"
    if name.startswith(LOCK_CANDIDATE_PREFIX):
        lock_id = name[len(LOCK_CANDIDATE_PREFIX) :]
        try:
            parsed: Optional[UUID] = UUID(lock_id)
        except (ValueError, AttributeError):
            parsed = None
        if parsed is not None and str(parsed) == lock_id:
            return StoreDebris(
                entry,
                relative,
                DEBRIS_LOCK_CANDIDATE,
                lock_id,
                "an abandoned private lock candidate that is not authority; a live "
                "acquirer could still publish it, so it is never claimed automatically",
            )
        return StoreDebris(
            entry,
            relative,
            DEBRIS_LOCK_UNEXPECTED,
            None,
            "a candidate name that does not carry a canonical owner UUID",
        )
    for prefix, transition in (
        (LOCK_RELEASED_PREFIX, "released"),
        (LOCK_REPAIRED_PREFIX, "repaired"),
    ):
        if name.startswith(prefix):
            return StoreDebris(
                entry,
                relative,
                DEBRIS_LOCK_QUARANTINE,
                None,
                f"a non-authoritative {transition} lock quarantine left by an "
                "interrupted cleanup; it never blocks a new valid owner",
            )
    return StoreDebris(
        entry,
        relative,
        DEBRIS_LOCK_UNEXPECTED,
        None,
        "unexpected content that no control protocol publishes",
    )


def _sorted_entries(path: Path, label: str) -> List[Path]:
    try:
        return sorted(path.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ControlStoreError(f"cannot read {label}: {exc}") from None


def collect_store_debris(root: Path) -> Tuple[StoreDebris, ...]:
    """List every non-authoritative item in one control store without judging it.

    Preflight and the explicit guarded repair share this single definition so a
    diagnosis can never name debris the repair would refuse to recognize.
    """

    found: List[StoreDebris] = []
    pending_path = root / PENDING_DIR_NAME
    if _is_plain_directory(pending_path, PENDING_DIR_NAME):
        for entry in _sorted_entries(pending_path, PENDING_DIR_NAME):
            found.append(
                StoreDebris(
                    entry,
                    f"{PENDING_DIR_NAME}/{entry.name}",
                    DEBRIS_PENDING,
                    None,
                    "a non-authoritative private publication candidate left by an "
                    "interrupted writer; replay never treats it as a committed event",
                )
            )
    for name in (LEDGER_TEMPORARY_NAME, EVENTS_VIEW_TEMPORARY_NAME):
        path = root / name
        if _existing_mode(path, name) is not None:
            found.append(
                StoreDebris(
                    path,
                    name,
                    DEBRIS_PROJECTION,
                    None,
                    "a non-authoritative interrupted projection temporary; it never "
                    "overrides the projection rebuilt from committed events",
                )
            )
    locks_path = root / LOCKS_DIR_NAME
    if _is_plain_directory(locks_path, LOCKS_DIR_NAME):
        for entry in _sorted_entries(locks_path, LOCKS_DIR_NAME):
            item = _lock_directory_debris(entry)
            if item is not None:
                found.append(item)
    return tuple(found)


@dataclass(frozen=True)
class PreflightFinding:
    """One dimension observation, its class, and its exact repair action."""

    dimension: str
    state: str
    kind: str
    detail: str
    repair: Optional[str] = None


@dataclass(frozen=True)
class PreflightReport:
    """The complete read-only readiness report for one control root."""

    root: Path
    source: str
    status: str
    findings: Tuple[PreflightFinding, ...]

    @property
    def ready(self) -> bool:
        return self.status == PREFLIGHT_STATUS_READY

    @property
    def blocked(self) -> bool:
        return self.status == PREFLIGHT_STATUS_BLOCKED

    @property
    def actionable(self) -> Tuple[PreflightFinding, ...]:
        """Return every finding that is not a clean dimension observation."""

        return tuple(finding for finding in self.findings if finding.state != PREFLIGHT_READY)


class ControlPreflight:
    """Strictly read-only control-plane readiness diagnosis.

    Preflight opens nothing for writing, acquires neither `control.lock` nor
    `locks/control.guard`, and creates no temporary file.  It therefore cannot
    repair anything: every problem it finds is reported with the exact explicit
    command an operator runs next.  Where a dimension genuinely cannot be
    answered without changing shared state — the current holder of the advisory
    transition guard is the only such case — the dimension reports what it can
    and says plainly that the rest is not probed.
    """

    def __init__(self, root: Path, source: str = "configured") -> None:
        self.root = _require_absolute_root(str(root), source)
        self.source = source
        self.findings: List[PreflightFinding] = []
        self.metadata: Optional[Dict[str, Any]] = None
        self.history: Optional[EventHistory] = None
        self.debris: Tuple[StoreDebris, ...] = ()
        self.root_ready = False
        self.present_directories: Dict[str, bool] = {}

    # -- finding helpers ----------------------------------------------------
    def _add(
        self,
        dimension: str,
        state: str,
        kind: str,
        detail: str,
        repair: Optional[str] = None,
    ) -> None:
        self.findings.append(PreflightFinding(dimension, state, kind, detail, repair))

    def _ready(self, dimension: str, detail: str) -> None:
        self._add(dimension, PREFLIGHT_READY, FINDING_OK, detail)

    def _undeterminable(self, dimension: str, detail: str, repair: Optional[str] = None) -> None:
        self._add(dimension, PREFLIGHT_UNDETERMINABLE, FINDING_OK, detail, repair)

    def _has_finding(self, dimension: str) -> bool:
        return any(finding.dimension == dimension for finding in self.findings)

    def _directory_available(self, name: str) -> bool:
        return bool(self.present_directories.get(name))

    # -- dimensions ---------------------------------------------------------
    def _check_root(self) -> None:
        dimension = "root"
        mode = _existing_mode(self.root, "COCKPIT_CONTROL_ROOT")
        if mode is None:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{self.root} does not exist (source: {self.source})",
                REPAIR_ACTION_INIT,
            )
            return
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{self.root} is not a directory; a control root is never a symlink or file",
                f"export COCKPIT_CONTROL_ROOT to the correct absolute root, or replace "
                f"{self.root} with a real directory yourself",
            )
            return

        missing: List[str] = []
        wrong_type: List[str] = []
        for name in REQUIRED_STORE_DIRECTORIES:
            entry_mode = _existing_mode(self.root / name, name)
            available = (
                entry_mode is not None
                and not stat.S_ISLNK(entry_mode)
                and stat.S_ISDIR(entry_mode)
            )
            self.present_directories[name] = available
            if entry_mode is None:
                missing.append(name)
            elif not available:
                wrong_type.append(name)
        for name in missing:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{name}/ is missing from {self.root}; the control protocols require it",
                REPAIR_ACTION_REPAIR_STORE,
            )
        for name in wrong_type:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{name} is not a directory; the control protocols require {name}/",
                f"move it aside yourself, for example `mv {self.root / name} "
                f"{self.root / name}.unexpected`, then run {REPAIR_ACTION_REPAIR_STORE}",
            )
        if missing or wrong_type:
            return
        self.root_ready = True
        self._ready(
            dimension,
            f"{self.root} is a complete control root with "
            f"{', '.join(name + '/' for name in REQUIRED_STORE_DIRECTORIES)} "
            f"(source: {self.source})",
        )

    def _check_schema(self) -> None:
        dimension = "schema"
        if not self.root_ready:
            self._undeterminable(
                dimension,
                f"{CONTROL_METADATA_NAME} is not read until the control root is complete",
            )
            return
        try:
            record = _load_json(self.root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME)
        except ControlStoreError as exc:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                str(exc),
                f"restore {CONTROL_METADATA_NAME} from a backup; cockpit-control never "
                "rewrites authoritative root metadata",
            )
            return
        declared = record.get("schema_version") if isinstance(record, dict) else None
        if (
            not isinstance(declared, bool)
            and isinstance(declared, int)
            and declared > CONTROL_SCHEMA_VERSION
        ):
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                f"{CONTROL_METADATA_NAME} declares future schema_version {declared}; this "
                f"build supports schema_version {CONTROL_SCHEMA_VERSION} and refuses mutation",
                REPAIR_ACTION_UPGRADE,
            )
            return
        try:
            self.metadata = validate_root_metadata(record, self.root)
        except ControlStoreError as exc:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                str(exc),
                f"restore {CONTROL_METADATA_NAME} from a backup; cockpit-control never "
                "rewrites authoritative root metadata",
            )
            return
        self._ready(
            dimension,
            f"{CONTROL_METADATA_NAME} is schema_version {CONTROL_SCHEMA_VERSION} for control "
            f"{self.metadata['control_id']} (cockpit {self.metadata['cockpit_id']}, session "
            f"{self.metadata['session_id']})",
        )

    def _check_transition_guard(self) -> None:
        dimension = "transition-guard"
        if not self._directory_available(LOCKS_DIR_NAME):
            self._undeterminable(
                dimension,
                f"{LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME} is not inspected until "
                f"{LOCKS_DIR_NAME}/ is a directory",
            )
            return
        path = self.root / LOCKS_DIR_NAME / CONTROL_GUARD_NAME
        label = f"{LOCKS_DIR_NAME}/{CONTROL_GUARD_NAME}"
        unprobed = (
            "its current holder is not probed because taking the advisory lock would "
            "change shared kernel lock state, and the kernel releases it when a holder exits"
        )
        mode = _existing_mode(path, label)
        if mode is None:
            self._ready(
                dimension,
                f"{label} is absent and the next guarded transition creates it; {unprobed}",
            )
            return
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{label} must be one regular file; every acquisition, release, and repair "
                "would refuse to serialize",
                f"move it aside yourself, for example `mv {path} {path}.unexpected`; the "
                "next guarded transition recreates it",
            )
            return
        self._ready(dimension, f"{label} is one regular file; {unprobed}")

    def _check_authoritative_lock(self) -> None:
        dimension = "authoritative-lock"
        if not self._directory_available(LOCKS_DIR_NAME):
            self._undeterminable(
                dimension,
                f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME} is not inspected until "
                f"{LOCKS_DIR_NAME}/ is a directory",
            )
            return
        label = f"{LOCKS_DIR_NAME}/{CONTROL_LOCK_NAME}"
        try:
            # The repair observer is the one reader of a published owner; it opens
            # the exact directory read-only and never creates the transition guard.
            published = ControlLockRepair(self.root)._observe_owner_while_guarded()
        except ControlStoreError as exc:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                f"{exc}",
                f"after proving no owner process is running, move it aside yourself with "
                f"`mv {self.root / LOCKS_DIR_NAME / CONTROL_LOCK_NAME} "
                f"{self.root / LOCKS_DIR_NAME / (LOCK_REPAIRED_PREFIX + 'manual')}`; "
                "guarded repair refuses a lock whose owner it cannot read",
            )
            return
        if published is None:
            self._ready(dimension, f"no {label} is published; a writer can acquire it")
            return

        owner, _observed = published
        lock_id = owner["lock_id"]
        state, reason = _prove_lock_owner_death(owner)
        described = (
            f"{label} is owned by lock {lock_id} (pid {owner['pid']} on {owner['host']}, "
            f"command {owner['command']!r}, acquired at {owner['acquired_at']})"
        )
        if state == LOCK_OWNER_ALIVE:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_OK,
                f"{described}: {reason}; mutation waits for its release",
                f"wait for pid {owner['pid']} to release it; {REPAIR_ACTION_REPAIR_LOCK} "
                "refuses a live owner",
            )
            return
        if state == LOCK_OWNER_DEAD:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                f"{described}: {reason}; every writer blocks until it is repaired",
                REPAIR_ACTION_REPAIR_LOCK,
            )
            return
        self._add(
            dimension,
            PREFLIGHT_BLOCKED,
            FINDING_AUTHORITATIVE,
            f"{described}: {reason}; owner death cannot be proven, so repair fails closed",
            f"{REPAIR_ACTION_REPAIR_LOCK} --authorize {lock_id}, only after you have "
            "proven that owner is not running",
        )

    def _check_quarantines(self) -> None:
        dimension = "quarantines"
        if not self._directory_available(LOCKS_DIR_NAME):
            self._undeterminable(
                dimension,
                f"lock candidates and quarantines are not listed until "
                f"{LOCKS_DIR_NAME}/ is a directory",
            )
            return
        lock_debris = [
            item
            for item in self.debris
            if item.kind
            in (DEBRIS_LOCK_QUARANTINE, DEBRIS_LOCK_CANDIDATE, DEBRIS_LOCK_UNEXPECTED)
        ]
        quarantine_path = self.root / QUARANTINE_DIR_NAME
        retained = 0
        mode = _existing_mode(quarantine_path, QUARANTINE_DIR_NAME)
        if mode is not None and (stat.S_ISLNK(mode) or not stat.S_ISDIR(mode)):
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"{QUARANTINE_DIR_NAME} is not a directory, so guarded repair has nowhere "
                "to retain evidence",
                f"move it aside yourself, for example `mv {quarantine_path} "
                f"{quarantine_path}.unexpected`",
            )
        elif mode is not None:
            retained = len(_sorted_entries(quarantine_path, QUARANTINE_DIR_NAME))
        for item in lock_debris:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_DEBRIS,
                f"{item.relative} is {item.detail}",
                item.repair_action,
            )
        if self._has_finding(dimension):
            return
        self._ready(
            dimension,
            f"{LOCKS_DIR_NAME}/ holds no lock candidates or quarantines and "
            f"{QUARANTINE_DIR_NAME}/ retains {retained} archived item(s) of "
            "non-authoritative evidence",
        )

    def _check_pending_events(self) -> None:
        dimension = "pending-events"
        if not self._directory_available(PENDING_DIR_NAME):
            self._undeterminable(
                dimension,
                f"{PENDING_DIR_NAME}/ is not inspected until it is a directory",
            )
            return
        candidates = [item for item in self.debris if item.kind == DEBRIS_PENDING]
        for item in candidates:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_DEBRIS,
                f"{item.relative} is {item.detail}",
                item.repair_action,
            )
        if candidates:
            return
        self._ready(
            dimension,
            f"{PENDING_DIR_NAME}/ holds no interrupted publication candidates",
        )

    def _check_committed_revisions(self) -> None:
        dimension = "committed-revisions"
        if self.metadata is None or not self._directory_available(EVENTS_DIR_NAME):
            return
        try:
            self.history = read_committed_events(self.root, self.metadata["control_id"])
        except ControlStoreError as exc:
            message = str(exc)
            repair = (
                REPAIR_ACTION_UPGRADE
                if "future schema_version" in message
                else (
                    "restore or correct exactly the named file under "
                    f"{EVENTS_DIR_NAME}/ yourself; cockpit-control never rewrites, "
                    "reorders, or removes committed authority"
                )
            )
            self._add(dimension, PREFLIGHT_BLOCKED, FINDING_AUTHORITATIVE, message, repair)

    def _finalize_committed_revisions(self) -> None:
        """Report a clean committed sequence only after continuity was compared.

        The ledger comparison is what proves the committed tip was not removed,
        so the clean observation is withheld until that comparison has run.
        """

        dimension = "committed-revisions"
        if self._has_finding(dimension):
            return
        if self.history is None:
            self._undeterminable(
                dimension,
                f"{EVENTS_DIR_NAME}/ is not replayed until "
                f"{CONTROL_METADATA_NAME} and {EVENTS_DIR_NAME}/ are readable",
            )
            return
        self._ready(
            dimension,
            f"{len(self.history.events)} contiguous committed revision(s) in "
            f"{EVENTS_DIR_NAME}/ (latest revision {self.history.latest_revision}) and "
            f"{LEDGER_NAME} records no revision they do not represent",
        )

    def _check_ledger(self) -> None:
        dimension = "ledger"
        temporaries = [item for item in self.debris if item.kind == DEBRIS_PROJECTION]
        for item in temporaries:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_DEBRIS,
                f"{item.relative} is {item.detail}",
                item.repair_action,
            )
        if self.metadata is None or self.history is None:
            self._undeterminable(
                dimension,
                f"the committed rebuild of {LEDGER_NAME} cannot be computed until "
                f"{CONTROL_METADATA_NAME} and {EVENTS_DIR_NAME}/ are readable",
            )
            return

        try:
            record = build_ledger_projection(self.metadata, self.history.events)
            expected_ledger = _serialized_record(record).encode("utf-8")
            expected_view = build_events_view(self.history.events)
        except ControlStoreError as exc:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                f"the committed events cannot be projected into {LEDGER_NAME}: {exc}",
                "restore or correct exactly the named committed event yourself; "
                "cockpit-control never rewrites committed authority",
            )
            return

        # The projection object performs no I/O until it is run; these classifiers
        # are the same read-only comparisons replay itself uses.
        projection = ControlLedgerProjection(self.root, command=PREFLIGHT_COMMAND)
        observed, reason = projection._classify_ledger(self.metadata["control_id"], expected_ledger)
        view_current = projection._view_matches(expected_view)

        if reason == PROJECTION_REASON_AHEAD:
            self._add(
                "committed-revisions",
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                f"{LEDGER_NAME} records revision {observed} but {EVENTS_DIR_NAME}/ ends at "
                f"revision {self.history.latest_revision}; committed events appear to have "
                "been removed and replay would rewind derived state",
                f"restore the missing committed event file(s) under {EVENTS_DIR_NAME}/; if "
                f"the removal was intended, run `{REPAIR_ACTION_REPLAY}` to rewind "
                f"{LEDGER_NAME} to revision {self.history.latest_revision}",
            )

        if reason != PROJECTION_REASON_CURRENT:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_DERIVED,
                f"{LEDGER_NAME} is derived state that does not equal the committed rebuild "
                f"at revision {record['revision']}: {_ledger_reason_detail(reason, observed)}",
                REPAIR_ACTION_REPLAY,
            )
        if not view_current:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_DERIVED,
                f"{EVENTS_NAME} is a rebuildable compatibility view that does not equal the "
                f"committed rebuild at revision {record['revision']}; `cockpit-control "
                "validate` fails closed on it, but replay rebuilds it without touching "
                "committed authority",
                REPAIR_ACTION_REPLAY,
            )
        if self._has_finding(dimension):
            return
        self._ready(
            dimension,
            f"{LEDGER_NAME} and {EVENTS_NAME} equal the committed rebuild at revision "
            f"{record['revision']}",
        )

    def _check_queue(self) -> None:
        dimension = "queue"
        if self.metadata is None:
            self._undeterminable(
                dimension,
                f"the declared queue root is not read until {CONTROL_METADATA_NAME} is valid",
            )
            return
        declared = self.metadata["canonical_roots"]["queue_root"]
        exported = os.environ.get("COCKPIT_QUEUE_ROOT")
        if declared is None:
            detail = (
                "no queue root is declared yet, so this root-level store carries no "
                "product-work authority; a mission must declare one before dispatch"
            )
            if exported:
                self._add(
                    dimension,
                    PREFLIGHT_ADVISORY,
                    FINDING_CONFIGURATION,
                    f"{detail}; the shell exports COCKPIT_QUEUE_ROOT={exported}, which "
                    f"{CONTROL_METADATA_NAME} does not declare",
                    f"declare canonical_roots.queue_root as {exported} when the mission is "
                    "created, or unset COCKPIT_QUEUE_ROOT in this shell",
                )
                return
            self._ready(dimension, detail)
            return

        self._check_declared_root(dimension, "queue root", declared)
        if exported is None:
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_CONFIGURATION,
                f"{CONTROL_METADATA_NAME} declares queue root {declared} but this shell "
                "exports no COCKPIT_QUEUE_ROOT",
                f"export COCKPIT_QUEUE_ROOT={declared}",
            )
        elif exported != declared:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"the exported COCKPIT_QUEUE_ROOT={exported} disagrees with the declared "
                f"queue root {declared}; dispatch is blocked until the roots agree",
                f"export COCKPIT_QUEUE_ROOT={declared}",
            )
        if self._has_finding(dimension):
            return
        self._ready(
            dimension,
            f"the declared queue root {declared} exists, is writable, and matches the "
            "exported COCKPIT_QUEUE_ROOT",
        )

    def _check_worker_capability(self) -> None:
        dimension = "worker-capability"
        if self.metadata is None:
            self._undeterminable(
                dimension,
                f"declared capabilities are not read until {CONTROL_METADATA_NAME} is valid",
            )
            return
        declarations = (
            ("capabilities", self.metadata["capabilities"], "control_store"),
            (
                "tool_capability_versions",
                self.metadata["tool_capability_versions"],
                "cockpit-control",
            ),
        )
        summary: List[str] = []
        for field, declared, required in declarations:
            if required not in declared:
                self._add(
                    dimension,
                    PREFLIGHT_ADVISORY,
                    FINDING_CONFIGURATION,
                    f"{CONTROL_METADATA_NAME} declares no {field}.{required}; this cockpit "
                    "is legacy-observed and cannot be given VP3 lifecycle work",
                    f"initialize a current control root with `{REPAIR_ACTION_INIT}` under a "
                    "fresh COCKPIT_CONTROL_ROOT; cockpit-control never rewrites authoritative "
                    "root metadata in place",
                )
                continue
            value = declared[required]
            if isinstance(value, bool) or not isinstance(value, int):
                self._add(
                    dimension,
                    PREFLIGHT_BLOCKED,
                    FINDING_AUTHORITATIVE,
                    f"{CONTROL_METADATA_NAME} declares non-integer {field}.{required} "
                    f"{value!r}, so the capability cannot be compared",
                    f"restore {CONTROL_METADATA_NAME} from a backup; cockpit-control never "
                    "rewrites authoritative root metadata",
                )
                continue
            if value > CONTROL_SCHEMA_VERSION:
                self._add(
                    dimension,
                    PREFLIGHT_BLOCKED,
                    FINDING_AUTHORITATIVE,
                    f"{CONTROL_METADATA_NAME} requires {field}.{required} {value}, which "
                    f"this build (capability {CONTROL_SCHEMA_VERSION}) cannot satisfy",
                    REPAIR_ACTION_UPGRADE,
                )
                continue
            if value < CONTROL_SCHEMA_VERSION:
                self._add(
                    dimension,
                    PREFLIGHT_ADVISORY,
                    FINDING_CONFIGURATION,
                    f"{CONTROL_METADATA_NAME} declares legacy {field}.{required} {value}; "
                    f"this build provides capability {CONTROL_SCHEMA_VERSION}, so the "
                    "cockpit is legacy-observed",
                    f"initialize a current control root with `{REPAIR_ACTION_INIT}` under a "
                    "fresh COCKPIT_CONTROL_ROOT; cockpit-control never rewrites authoritative "
                    "root metadata in place",
                )
                continue
            summary.append(f"{field}.{required} {value}")
        if self._has_finding(dimension):
            return
        self._ready(
            dimension,
            f"{CONTROL_METADATA_NAME} declares {' and '.join(summary)}, both satisfied by "
            f"this build; worker lifecycle capability is not declared, so workers remain "
            "legacy-observed until a mission declares it",
        )

    def _check_declared_root(self, dimension: str, label: str, declared: str) -> None:
        """Report existence and writability of one declared canonical root."""

        path = Path(declared)
        if _existing_mode(path, f"declared {label}") is None:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"the declared {label} {declared} does not exist",
                f"mkdir -p {declared}",
            )
            return
        if not path.is_dir():
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_CONFIGURATION,
                f"the declared {label} {declared} is not a directory",
                f"replace {declared} with a real directory yourself",
            )
            return
        if not os.access(str(path), os.W_OK):
            self._add(
                dimension,
                PREFLIGHT_ADVISORY,
                FINDING_CONFIGURATION,
                f"the declared {label} {declared} is not writable by this user",
                f"chmod u+w {declared}",
            )

    def _check_declared_paths(self) -> None:
        dimension = "declared-paths"
        if self.metadata is None:
            self._undeterminable(
                dimension,
                f"declared roots are not read until {CONTROL_METADATA_NAME} is valid",
            )
            return
        roots = self.metadata["canonical_roots"]
        declared_count = 0
        planning_root = roots["planning_root"]
        if planning_root is not None:
            declared_count += 1
            self._check_declared_root(dimension, "planning root", planning_root)
        for implementation_root in roots["implementation_roots"]:
            declared_count += 1
            self._check_declared_root(dimension, "implementation root", implementation_root)
        if self._has_finding(dimension):
            return
        if declared_count == 0:
            self._ready(
                dimension,
                "no planning root or implementation roots are declared yet, so no mission "
                "boundary can be crossed",
            )
            return
        self._ready(
            dimension,
            f"{declared_count} declared root(s) exist and are writable; discovery outside "
            "them stays blocked pending an amended mission",
        )

    # -- report -------------------------------------------------------------
    def _collect_debris(self) -> None:
        self.debris = collect_store_debris(self.root) if self.root_ready else ()

    def run(self) -> PreflightReport:
        """Diagnose every dimension without opening one path for writing."""

        checks = (
            ("root", self._check_root),
            ("root", self._collect_debris),
            ("schema", self._check_schema),
            ("transition-guard", self._check_transition_guard),
            ("authoritative-lock", self._check_authoritative_lock),
            ("quarantines", self._check_quarantines),
            ("pending-events", self._check_pending_events),
            ("committed-revisions", self._check_committed_revisions),
            ("ledger", self._check_ledger),
            ("committed-revisions", self._finalize_committed_revisions),
            ("queue", self._check_queue),
            ("worker-capability", self._check_worker_capability),
            ("declared-paths", self._check_declared_paths),
        )
        for dimension, check in checks:
            try:
                check()
            except ControlStoreError as exc:
                # A read-only diagnosis reports what it could not determine; it
                # never aborts the remaining dimensions and never repairs.
                self._undeterminable(dimension, f"this dimension cannot be read: {exc}")

        ordered: List[PreflightFinding] = []
        for dimension in PREFLIGHT_DIMENSIONS:
            found = [finding for finding in self.findings if finding.dimension == dimension]
            if not found:
                raise ControlStoreError(f"preflight produced no finding for dimension {dimension}")
            ordered.extend(found)
        if len(ordered) != len(self.findings):
            raise ControlStoreError("preflight produced a finding outside its declared dimensions")

        status = PREFLIGHT_STATUS_READY
        for finding in ordered:
            if finding.state == PREFLIGHT_BLOCKED:
                status = PREFLIGHT_STATUS_BLOCKED
                break
            if finding.state != PREFLIGHT_READY:
                status = PREFLIGHT_STATUS_DEGRADED
        return PreflightReport(
            root=self.root,
            source=self.source,
            status=status,
            findings=tuple(ordered),
        )


def _ledger_reason_detail(reason: str, observed: Optional[int]) -> str:
    """Describe one derived-projection classification for an operator."""

    if reason == PROJECTION_REASON_MISSING:
        return "it is missing"
    if reason == PROJECTION_REASON_CORRUPT:
        return "it is unreadable derived corruption"
    if reason == PROJECTION_REASON_AHEAD:
        return f"it claims revision {observed}, which no committed event represents"
    if reason == PROJECTION_REASON_STALE:
        return f"it is stale at revision {observed}"
    if reason == PROJECTION_REASON_DIVERGENT:
        return f"it disagrees with the committed events at revision {observed}"
    return "it equals the committed rebuild"


def run_control_preflight(root: Path, source: str = "configured") -> PreflightReport:
    """Report control-plane readiness without changing one byte of the store."""

    return ControlPreflight(root, source=source).run()


def _store_repair_fault(boundary: str, repair: "ControlStoreRepair") -> None:
    """No-op named store-repair hook used by deterministic protocol tests."""

    del boundary, repair


@dataclass(frozen=True)
class StoreRepairResult:
    """Outcome of one explicit guarded control-store repair attempt."""

    root: Path
    outcome: str
    created: Tuple[str, ...]
    quarantined: Tuple[Tuple[str, str], ...]
    retained: Tuple[Tuple[str, str], ...]

    @property
    def repaired(self) -> bool:
        return self.outcome == STORE_REPAIR_REPAIRED


class ControlStoreRepair:
    """Explicit guarded repair that quarantines debris and never deletes anything.

    The repair fails closed before it touches the filesystem: unreadable or
    future-versioned root metadata, and a committed sequence that cannot be read
    as contiguous authority, both refuse the whole operation.  What it then does
    is deliberately small:

    1. recreate exactly the required store directories that are missing;
    2. under `control.lock`, rename every abandoned publication candidate and
       interrupted projection temporary into `quarantine/`;
    3. under the same transition guard every lock transition uses, rename every
       abandoned lock quarantine — and every explicitly authorized private lock
       candidate — into `quarantine/`.

    Every step is a rename, so nothing is ever unlinked by pathname after a
    separate identity check and an interruption can only leave the item in one
    of its two named places.  Re-running the repair therefore finds nothing left
    to claim and changes nothing at all.
    """

    def __init__(
        self,
        root: Path,
        authorized_lock_ids: Sequence[str] = (),
        command: str = DEFAULT_STORE_REPAIR_COMMAND,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.quarantine_path = self.root / QUARANTINE_DIR_NAME
        self.locks_path = self.root / LOCKS_DIR_NAME
        self.command = command.strip() if isinstance(command, str) else ""
        if not self.command:
            raise ControlStoreError("store repair requires a non-empty command")
        self.timeout_seconds = _configured_lock_timeout(timeout_seconds)
        self.poll_seconds = _validated_seconds(
            poll_seconds,
            "control lock poll interval",
            allow_zero=False,
        )
        self.dry_run = bool(dry_run)
        self.authorized_lock_ids = self._validated_authorizations(authorized_lock_ids)
        self.created: List[str] = []
        self.quarantined: List[Tuple[str, str]] = []
        self.retained: List[Tuple[str, str]] = []

    @staticmethod
    def _validated_authorizations(values: Sequence[str]) -> Tuple[str, ...]:
        """Require every authorization to name one exact owner UUID exactly once."""

        authorized: List[str] = []
        for value in values or ():
            lock_id = _require_uuid(
                {"authorized_lock_id": value},
                "authorized_lock_id",
                "control store repair",
            )
            if lock_id in authorized:
                raise ControlStoreError(
                    f"control store repair authorization repeats lock {lock_id}"
                )
            authorized.append(lock_id)
        return tuple(authorized)

    def _require_supported_metadata(self) -> Dict[str, Any]:
        """Refuse before any mutation when root metadata is unreadable or ahead."""

        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        record = _load_json(self.root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME)
        return validate_root_metadata(record, self.root)

    def _missing_directories(self) -> List[str]:
        missing: List[str] = []
        for name in REQUIRED_STORE_DIRECTORIES:
            path = self.root / name
            mode = _existing_mode(path, name)
            if mode is None:
                missing.append(name)
            elif stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ControlStoreError(
                    f"{name} is not a directory; move it aside yourself before repairing"
                )
        return missing

    def _create_missing_directories(self) -> None:
        """Recreate exactly the required directories, replacing nothing."""

        for name in self._missing_directories():
            try:
                (self.root / name).mkdir(mode=0o700)
            except FileExistsError:
                continue
            except OSError as exc:
                raise ControlStoreError(f"cannot create {name}/: {exc}") from None
            self.created.append(f"{name}/")
        if self.created:
            _fsync_directory(self.root)

    def _prepared_quarantine_root(self) -> Path:
        """Create the retained-evidence directory once, replacing nothing."""

        mode = _existing_mode(self.quarantine_path, QUARANTINE_DIR_NAME)
        if mode is not None:
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ControlStoreError(
                    f"{QUARANTINE_DIR_NAME} is not a directory; move it aside yourself "
                    "before repairing"
                )
            return self.quarantine_path
        try:
            self.quarantine_path.mkdir(mode=0o700)
        except FileExistsError:
            return self.quarantine_path
        except OSError as exc:
            raise ControlStoreError(f"cannot create {QUARANTINE_DIR_NAME}/: {exc}") from None
        self.created.append(f"{QUARANTINE_DIR_NAME}/")
        _fsync_directory(self.root)
        return self.quarantine_path

    def _quarantine(self, item: StoreDebris) -> None:
        """Atomically rename one exact debris item into retained evidence.

        Checking identity *after* the rename is sufficient because the rename is
        never an unlink: the destination is a fresh UUID-stamped name that is
        proven absent first, so a losing race can only move an item this repair
        did not observe, and the mismatch is reported with the evidence left in
        place.  Every claimable source is either itself uniquely UUID-named or is
        reachable only while this process holds `control.lock`.
        """

        destination_root = self._prepared_quarantine_root()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = destination_root / f"{stamp}-{uuid4()}-{item.path.name}"
        if _existing_mode(destination, f"{QUARANTINE_DIR_NAME}/{destination.name}") is not None:
            raise ControlStoreError(
                f"cannot prepare unique {QUARANTINE_DIR_NAME}/{destination.name}"
            )
        try:
            observed = item.path.lstat()
        except FileNotFoundError:
            # A concurrent owner finished its own cleanup; nothing is claimed.
            return
        except OSError as exc:
            raise ControlStoreError(f"cannot inspect {item.relative}: {exc}") from None
        try:
            os.rename(str(item.path), str(destination))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ControlStoreError(
                f"cannot quarantine {item.relative}; evidence retained: {exc}"
            ) from None
        try:
            quarantined = destination.lstat()
        except OSError as exc:
            raise ControlStoreError(
                f"{item.relative} was quarantined but cannot be verified: {exc}"
            ) from None
        if not _same_filesystem_identity(quarantined, observed):
            raise ControlStoreError(
                f"{QUARANTINE_DIR_NAME}/{destination.name} changed filesystem identity; "
                "evidence retained"
            )
        _fsync_directory(destination_root)
        _fsync_directory(item.path.parent)
        self.quarantined.append((item.relative, f"{QUARANTINE_DIR_NAME}/{destination.name}"))
        _store_repair_fault("item-quarantined", self)

    def _plan(self, debris: Sequence[StoreDebris]) -> Tuple[List[StoreDebris], List[StoreDebris]]:
        """Split debris into what this invocation may claim and what it retains."""

        claimable: List[StoreDebris] = []
        retained: List[StoreDebris] = []
        for item in debris:
            if item.never_automatic:
                retained.append(item)
            elif item.requires_authorization and item.lock_id not in self.authorized_lock_ids:
                retained.append(item)
            else:
                claimable.append(item)
        return claimable, retained

    def _require_authorizations_match(self, debris: Sequence[StoreDebris]) -> None:
        """Fail closed when an authorization names no private candidate at all."""

        available = [
            item.lock_id for item in debris if item.requires_authorization and item.lock_id
        ]
        for lock_id in self.authorized_lock_ids:
            if lock_id not in available:
                raise ControlStoreError(
                    f"repair authorization names lock {lock_id}, but no private lock "
                    f"candidate under {LOCKS_DIR_NAME}/ carries that owner UUID; "
                    "no state changed"
                )

    def _require_committed_continuity(
        self,
        metadata: Mapping[str, Any],
        history: EventHistory,
    ) -> None:
        """Refuse when the derived ledger records revisions no event represents.

        A projection that is ahead of committed authority means the committed tip
        was removed, so committed-event continuity cannot be proven.  The repair
        claims nothing at all until an operator decides which side is right.
        """

        expected = _serialized_record(build_ledger_projection(metadata, history.events))
        projection = ControlLedgerProjection(self.root, command=self.command)
        observed, reason = projection._classify_ledger(
            metadata["control_id"], expected.encode("utf-8")
        )
        if reason != PROJECTION_REASON_AHEAD:
            return
        raise ControlStoreError(
            f"{LEDGER_NAME} records revision {observed} but {EVENTS_DIR_NAME}/ ends at "
            f"revision {history.latest_revision}; committed-event continuity cannot be "
            "proven, so nothing is claimed; restore the missing committed event file(s), "
            f"or run `{REPAIR_ACTION_REPLAY}` if the removal was intended"
        )

    def _claimable_history(
        self,
        metadata: Mapping[str, Any],
        recreated: Sequence[str] = (),
    ) -> EventHistory:
        """Read committed authority exactly as the locked repair reads it.

        `recreated` names the required directories this repair creates before it
        reads, so the lock-free preview models them as the empty directories the
        real run would have made instead of failing on a read it never attempts.
        Only `events/` carries authority, so a recreated `pending/` changes the
        debris this repair claims but never the committed sequence it proves.
        """

        if EVENTS_DIR_NAME in recreated:
            return EventHistory(root=self.root, events=(), pending=())
        events = read_committed_sequence(self.root, metadata["control_id"])
        pending: Tuple[Path, ...] = ()
        if PENDING_DIR_NAME not in recreated:
            pending = _read_pending_debris(self.root)
        return EventHistory(root=self.root, events=events, pending=pending)

    def _require_claimable_state(
        self,
        metadata: Mapping[str, Any],
        recreated: Sequence[str] = (),
    ) -> None:
        """Refuse before anything is claimed when committed authority is unusable.

        Committed authority must be readable and provably continuous before any
        debris is claimed, so an ambiguous history refuses the repair instead of
        tidying around it.  Both gates only read, which is why the lock-free
        preview runs exactly these refusals without taking `control.lock` or the
        transition guard.
        """

        self._require_committed_continuity(
            metadata, self._claimable_history(metadata, recreated)
        )

    def _outcome(self) -> StoreRepairResult:
        changed = bool(self.created or self.quarantined)
        if not changed:
            outcome = STORE_REPAIR_CURRENT
        elif self.dry_run:
            outcome = STORE_REPAIR_WOULD_REPAIR
        else:
            outcome = STORE_REPAIR_REPAIRED
        return StoreRepairResult(
            root=self.root,
            outcome=outcome,
            created=tuple(self.created),
            quarantined=tuple(self.quarantined),
            retained=tuple(self.retained),
        )

    def _plan_only(self, metadata: Mapping[str, Any]) -> StoreRepairResult:
        """Describe the exact repair without acquiring a lock or writing a byte.

        The preview refuses whatever the locked repair refuses: it runs the same
        read-only pre-mutation gates first, so it can never advertise a repair
        this tool would go on to reject.
        """

        missing = self._missing_directories()
        self._require_claimable_state(metadata, missing)
        self.created = [f"{name}/" for name in missing]
        debris = collect_store_debris(self.root)
        self._require_authorizations_match(debris)
        claimable, retained = self._plan(debris)
        if claimable and _existing_mode(self.quarantine_path, QUARANTINE_DIR_NAME) is None:
            self.created.append(f"{QUARANTINE_DIR_NAME}/")
        self.quarantined = [(item.relative, f"{QUARANTINE_DIR_NAME}/") for item in claimable]
        self.retained = [(item.relative, item.repair_action) for item in retained]
        return self._outcome()

    def run(self) -> StoreRepairResult:
        """Perform at most one explicit guarded repair and report what changed."""

        metadata = self._require_supported_metadata()
        if self.dry_run:
            return self._plan_only(metadata)

        self._create_missing_directories()
        with PortableControlLock(
            self.root,
            self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ):
            metadata = self._require_supported_metadata()
            self._require_claimable_state(metadata)
            debris = collect_store_debris(self.root)
            self._require_authorizations_match(debris)
            claimable, retained = self._plan(debris)
            self.retained = [(item.relative, item.repair_action) for item in retained]

            for item in claimable:
                if item.kind in (DEBRIS_PENDING, DEBRIS_PROJECTION):
                    self._quarantine(item)
            lock_items = [
                item
                for item in claimable
                if item.kind in (DEBRIS_LOCK_QUARANTINE, DEBRIS_LOCK_CANDIDATE)
            ]
            if lock_items:
                with ControlTransitionGuard(
                    self.locks_path,
                    timeout_seconds=self.timeout_seconds,
                    poll_seconds=self.poll_seconds,
                ):
                    for item in lock_items:
                        self._quarantine(item)
        return self._outcome()


def repair_control_store(
    root: Path,
    authorized_lock_ids: Sequence[str] = (),
    command: str = DEFAULT_STORE_REPAIR_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> StoreRepairResult:
    """Quarantine non-authoritative debris and restore required directories."""

    return ControlStoreRepair(
        root,
        authorized_lock_ids=authorized_lock_ids,
        command=command,
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
    # The initial ledger is the projection of an empty committed sequence, so
    # a first replay finds it already current instead of rewriting it.
    return metadata, build_ledger_projection(metadata)


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
        for name in REQUIRED_STORE_DIRECTORIES:
            (temporary_root / name).mkdir(mode=0o700)
        metadata, ledger = _initial_records(root)
        _write_json(temporary_root / CONTROL_METADATA_NAME, metadata)
        _write_json(temporary_root / LEDGER_NAME, ledger)
        _write_new_text(temporary_root / EVENTS_NAME, build_events_view())
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


def _report_preflight(report: PreflightReport) -> int:
    """Print one complete readiness report; the exit code carries the verdict."""

    actionable = report.actionable
    print(
        f"cockpit-control: preflight {report.status} in {report.root} "
        f"(source: {report.source}); {len(PREFLIGHT_DIMENSIONS)} dimensions checked, "
        f"{len(actionable)} finding(s)"
    )
    for finding in report.findings:
        line = f"{finding.dimension} {finding.state} {finding.kind}: {finding.detail}"
        if finding.repair is not None:
            line = f"{line} [repair: {finding.repair}]"
        print(line)
    return 1 if report.blocked else 0


def _report_store_repair(result: StoreRepairResult) -> int:
    """Print one guarded repair outcome and report retained evidence on stderr."""

    planned = result.outcome == STORE_REPAIR_WOULD_REPAIR
    if result.outcome == STORE_REPAIR_CURRENT and result.retained:
        print(
            f"cockpit-control: claimed nothing in {result.root}; {len(result.retained)} "
            "non-authoritative item(s) are retained pending an explicit authorization; "
            "no state changed"
        )
    elif result.outcome == STORE_REPAIR_CURRENT:
        print(
            f"cockpit-control: no repairable control-store debris in {result.root}; "
            "no state changed"
        )
    else:
        actions: List[str] = []
        if result.quarantined:
            actions.append(
                f"{'would repair' if planned else 'repaired'} "
                f"{len(result.quarantined)} non-authoritative item(s)"
            )
        if result.created:
            actions.append(
                f"{'create' if planned else 'created'} "
                f"{len(result.created)} store directory(ies)"
            )
        summary = " and ".join(actions)
        if planned and not result.quarantined:
            summary = f"would {summary}"
        print(
            f"cockpit-control: {summary} in {result.root}"
            + ("; no state changed" if planned else "")
        )
    for name in result.created:
        print(f"{'would-create' if planned else 'created'} {name}")
    for source, destination in result.quarantined:
        if planned:
            print(f"would-quarantine {source}")
        else:
            print(f"quarantined {source} -> {destination}")
    for source, action in result.retained:
        print(
            f"cockpit-control: {source} is retained non-authoritative evidence that this "
            f"repair does not claim; run: {action}",
            file=os.sys.stderr,
        )
    return 0


def _report_pending_debris(debris: Sequence[Path]) -> None:
    """Report every private candidate as retained, non-authoritative evidence."""

    for path in debris:
        print(
            f"cockpit-control: {PENDING_DIR_NAME}/{path.name} is non-authoritative debris "
            "from an interrupted publication; it is retained for diagnosis",
            file=os.sys.stderr,
        )


def _report_projection_debris(temporaries: Sequence[Path]) -> None:
    """Report every interrupted projection temporary as non-authoritative."""

    for path in temporaries:
        print(
            f"cockpit-control: {path.name} is a non-authoritative interrupted projection "
            "temporary; it never overrides the ledger rebuilt from committed events",
            file=os.sys.stderr,
        )


def _projection_reason(result: LedgerProjectionResult) -> str:
    """Describe why a derived projection did not equal the committed rebuild."""

    if result.reason == PROJECTION_REASON_MISSING:
        return f"the derived {LEDGER_NAME} was missing"
    if result.reason == PROJECTION_REASON_CORRUPT:
        return f"the derived {LEDGER_NAME} was unreadable derived corruption"
    if result.reason == PROJECTION_REASON_AHEAD:
        return (
            f"the derived {LEDGER_NAME} claimed revision {result.observed_revision}, which no "
            "committed event represents"
        )
    if result.reason == PROJECTION_REASON_STALE:
        return f"the derived {LEDGER_NAME} was stale at revision {result.observed_revision}"
    if result.reason == PROJECTION_REASON_DIVERGENT:
        return (
            f"the derived {LEDGER_NAME} disagreed with the committed events at revision "
            f"{result.observed_revision}"
        )
    if result.reason == PROJECTION_REASON_VIEW:
        return f"the derived {EVENTS_NAME} view was out of date"
    return "the derived projection already matched the committed events"


def _report_ledger_projection(result: LedgerProjectionResult) -> int:
    """Print one projection or replay outcome and report debris on stderr."""

    if result.current:
        print(
            f"cockpit-control: {LEDGER_NAME} projection is current at revision "
            f"{result.revision} in {result.root}"
        )
    elif result.outcome == PROJECTION_WOULD_REBUILD:
        print(
            f"cockpit-control: would rebuild {LEDGER_NAME} at revision {result.revision} "
            f"({_projection_reason(result)}); no state changed"
        )
    else:
        print(
            f"cockpit-control: rebuilt {LEDGER_NAME} at revision {result.revision} "
            f"({_projection_reason(result)})"
        )
    _report_projection_debris(result.interrupted_temporaries)
    _report_pending_debris(result.pending_debris)
    return 0


def _report_event_publication(result: EventPublicationResult) -> int:
    """Print one publication outcome on stdout and report debris on stderr."""

    location = f"{EVENTS_DIR_NAME}/{result.path.name}"
    if result.committed:
        print(f"cockpit-control: committed {location} at revision {result.revision}")
    else:
        print(
            f"cockpit-control: would commit {location} at revision {result.revision}; "
            "no state changed"
        )
    if result.projection is not None:
        print(
            f"cockpit-control: projected {LEDGER_NAME} at revision "
            f"{result.projection.revision}"
        )
        _report_projection_debris(result.projection.interrupted_temporaries)
    _report_pending_debris(result.pending_debris)
    return 0


def _report_event_history(root: Path, history: EventHistory) -> int:
    """Print the committed sequence and any retained private candidates."""

    print(
        f"cockpit-control: {len(history.events)} committed events in {root} "
        f"(latest revision {history.latest_revision})"
    )
    for event in history.events:
        print(
            f"{EVENTS_DIR_NAME}/{event.path.name} revision {event.revision} "
            f"type {event.record['event_type']} actor {event.record['actor']}"
        )
    _report_pending_debris(history.pending)
    return 0


def _parsed_payload(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse an explicit payload argument as one JSON object of metadata."""

    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ControlStoreError(f"event payload is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ControlStoreError("event payload must be a JSON object of structured metadata")
    return parsed


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the small control-store CLI used by humans and controller commands."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="cockpit-control",
        description=(
            "initialize, validate, publish immutable events into, replay the derived "
            "ledger of, preflight, and guardedly repair the versioned cockpit control store"
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
    publish = subcommands.add_parser(
        "publish-event",
        help="commit one immutable event into events/ under the held control lock",
    )
    publish.add_argument(
        "--type",
        dest="event_type",
        required=True,
        metavar="EVENT_TYPE",
        help="the structured event type to commit",
    )
    publish.add_argument(
        "--actor",
        default=DEFAULT_EVENT_ACTOR,
        help="the actor recorded as the author of the event",
    )
    publish.add_argument(
        "--payload",
        default=None,
        metavar="JSON",
        help="structured metadata object recorded with the event",
    )
    publish.add_argument(
        "--dry-run",
        action="store_true",
        help="report the revision that would be committed without changing any state",
    )
    subcommands.add_parser(
        "list-events",
        help="list committed events and report retained private candidates",
    )
    subcommands.add_parser(
        "preflight",
        help=(
            "report control-plane readiness for every dimension without changing "
            "one byte of state"
        ),
    )
    repair_store = subcommands.add_parser(
        "repair-store",
        help=(
            "restore required directories and quarantine non-authoritative debris "
            "under the control lock; nothing is ever deleted"
        ),
    )
    repair_store.add_argument(
        "--authorize",
        dest="authorized_lock_ids",
        action="append",
        default=None,
        metavar="LOCK_ID",
        help=(
            "explicitly authorize quarantining the private lock candidate owned by "
            "this exact UUID; may be repeated"
        ),
    )
    repair_store.add_argument(
        "--dry-run",
        action="store_true",
        help="report the repair plan without changing any state",
    )
    replay = subcommands.add_parser(
        "replay-ledger",
        help=(
            "rebuild the derived ledger projection from committed events without "
            "committing any event"
        ),
    )
    replay.add_argument(
        "--dry-run",
        action="store_true",
        help="report the projection decision without changing any state",
    )
    args = parser.parse_args(argv)

    try:
        resolved = resolve_control_root()
        if args.command == "init":
            result = initialize_control_store(resolved)
            verb = "initialized" if result.created else "validated"
            print(f"cockpit-control: {verb} {result.root} (source: {result.source})")
            return 0
        if args.command == "publish-event":
            return _report_event_publication(
                publish_control_event(
                    resolved.path,
                    args.event_type,
                    actor=args.actor,
                    payload=_parsed_payload(args.payload),
                    dry_run=args.dry_run,
                )
            )
        if args.command == "replay-ledger":
            return _report_ledger_projection(
                replay_control_ledger(resolved.path, dry_run=args.dry_run)
            )
        if args.command == "preflight":
            return _report_preflight(run_control_preflight(resolved.path, resolved.source))
        if args.command == "repair-store":
            return _report_store_repair(
                repair_control_store(
                    resolved.path,
                    authorized_lock_ids=args.authorized_lock_ids or (),
                    dry_run=args.dry_run,
                )
            )
        if args.command == "list-events":
            _metadata, history = inspect_control_events(resolved.path)
            return _report_event_history(resolved.path, history)
        if args.command == "repair-lock":
            return _report_lock_repair(
                repair_stale_control_lock(
                    resolved.path,
                    authorized_lock_id=args.authorize,
                    dry_run=args.dry_run,
                )
            )
        metadata = validate_control_store(resolved.path)
        print(f"cockpit-control: valid {resolved.path} (source: {resolved.source})")
        _report_pending_debris(
            read_committed_events(resolved.path, metadata["control_id"]).pending
        )
        return 0
    except ControlStoreError as exc:
        return _print_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
