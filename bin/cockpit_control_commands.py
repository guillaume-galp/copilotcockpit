"""Command seam for envelopes, acknowledgements, digesting, and idempotency."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# Command protocol vocabulary.
COMMAND_SCHEMA_VERSION = 1
COMMAND_ENVELOPE_RECORD_TYPE = "command-envelope"
COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE = "command-acknowledgement"
COMMAND_ENVELOPE_PAYLOAD_FIELD = "command_envelope"
COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD = "command_acknowledgement"
COMMAND_REGISTERED_EVENT_TYPE = "command-registered"
COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX = "command-acknowledged-"
COMMAND_DIGEST_ALGORITHM = "sha256"
COMMAND_DIGEST_PREFIX = f"{COMMAND_DIGEST_ALGORITHM}:"
COMMAND_DIGEST_HEX_DIGITS = 64
COMMAND_DIGEST_HEX_ALPHABET = "0123456789abcdef"
COMMAND_TARGET_WORKER = "worker"
COMMAND_TARGET_QUEUE = "queue"
COMMAND_TARGET_KINDS = (COMMAND_TARGET_WORKER, COMMAND_TARGET_QUEUE)
COMMAND_TARGET_FIELDS = ("kind", "id")
COMMAND_BOUNDARY_FIELDS = (
    "control_root",
    "queue_root",
    "planning_root",
    "implementation_roots",
    "runtime_boundaries",
)
COMMAND_ENVELOPE_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "command_type",
    "mission_id",
    "queue_item_id",
    "target",
    "trace_id",
    "parent_trace_id",
    "payload_digest",
    "boundaries",
    "created_at",
    "deadline_at",
)
COMMAND_ENVELOPE_IDENTITY_FIELDS = tuple(
    field for field in COMMAND_ENVELOPE_FIELDS if field != "created_at"
)
COMMAND_ACKNOWLEDGEMENT_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "payload_digest",
    "outcome",
    "acknowledged_by",
    "acknowledged_at",
    "reason",
    "result_refs",
)
COMMAND_ACCEPTED = "accepted"
COMMAND_APPLIED = "applied"
COMMAND_REJECTED = "rejected"
COMMAND_DUPLICATE = "duplicate"
COMMAND_ACKNOWLEDGEMENT_OUTCOMES = (
    COMMAND_ACCEPTED,
    COMMAND_APPLIED,
    COMMAND_REJECTED,
    COMMAND_DUPLICATE,
)
COMMAND_ACKNOWLEDGEMENT_REASON_REQUIRED = (COMMAND_REJECTED, COMMAND_DUPLICATE)
COMMAND_STATUS_REGISTERED = "registered"
COMMAND_ACKNOWLEDGEMENT_TRANSITIONS = {
    COMMAND_STATUS_REGISTERED: {
        COMMAND_ACCEPTED: COMMAND_ACCEPTED,
        COMMAND_REJECTED: COMMAND_REJECTED,
        COMMAND_DUPLICATE: COMMAND_STATUS_REGISTERED,
    },
    COMMAND_ACCEPTED: {
        COMMAND_APPLIED: COMMAND_APPLIED,
        COMMAND_REJECTED: COMMAND_REJECTED,
        COMMAND_DUPLICATE: COMMAND_ACCEPTED,
    },
    COMMAND_APPLIED: {COMMAND_DUPLICATE: COMMAND_APPLIED},
    COMMAND_REJECTED: {COMMAND_DUPLICATE: COMMAND_REJECTED},
}
COMMAND_CONFLICT_DIGEST = "digest-conflict"
COMMAND_CONFLICT_ENVELOPE = "envelope-conflict"
COMMAND_CONFLICT_DELIVERY = "delivery"
COMMAND_CONFLICT_ACKNOWLEDGEMENT = "acknowledgement"
COMMAND_FOLD_REGISTERED = "registered"
COMMAND_FOLD_ACKNOWLEDGED = "acknowledged"
COMMAND_FOLD_CONFLICT_RECORDED = "conflict-recorded"
COMMAND_FOLD_RETAINED_DUPLICATE = "retained-duplicate-delivery"
COMMAND_FOLD_RETAINED_UNKNOWN = "retained-unknown-command"
COMMAND_FOLD_RETAINED_DIGEST = "retained-digest-mismatch"
COMMAND_FOLD_RETAINED_ORDER = "retained-invalid-outcome"
COMMAND_DUPLICATE_REASON = (
    "redelivery of a command already registered with the same payload digest"
)
DEFAULT_EVENT_ACTOR = "cockpit-control"
DEFAULT_COMMAND_REGISTER_COMMAND = "cockpit-control register-command"
DEFAULT_COMMAND_ACKNOWLEDGE_COMMAND = "cockpit-control acknowledge-command"
DEFAULT_LOCK_POLL_SECONDS = 0.05
EVENTS_DIR_NAME = "events"


class ControlStoreError(RuntimeError):
    """Facade overrides this class to keep compatibility of raised errors."""


# Facade-bound dependencies.
_require_object: Callable[..., Dict[str, Any]]
_require_record_type: Callable[..., str]
_require_string: Callable[..., str]
_require_identifier: Callable[..., str]
_require_optional_identifier: Callable[..., Optional[str]]
_require_uuid: Callable[..., str]
_require_optional_uuid: Callable[..., Optional[str]]
_require_timestamp: Callable[..., str]
_require_optional_string: Callable[..., Optional[str]]
_require_typed_references: Callable[..., List[str]]
_require_root_path: Callable[..., str]
_parsed_timestamp: Callable[..., Any]
_require_absolute_root: Callable[..., Path]
publish_control_event: Callable[..., Any]
inspect_control_events: Callable[..., Any]
fold_mission_state: Callable[..., Any]
mission_command_correlation_fault: Callable[..., Optional[str]]
utc_timestamp: Callable[[], str]
CommittedEvent: Any
EventPublicationResult: Any
PortableControlLock: Any


@dataclass(frozen=True)
class CommandRecordResult:
    """Outcome of committing one command delivery or one acknowledgement."""

    root: Path
    publication: Any
    command_id: str
    envelope: Dict[str, Any]
    acknowledgement: Optional[Dict[str, Any]]
    applied: bool
    outcome: str
    materialized: Optional[Dict[str, Any]]

    @property
    def committed(self) -> bool:
        return self.publication.committed

    @property
    def conflicted(self) -> bool:
        return self.outcome == COMMAND_FOLD_CONFLICT_RECORDED


def _require_string_keys(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise ControlStoreError(f"{label} requires string field names")
            _require_string_keys(value[key], label)
    elif isinstance(value, list):
        for item in value:
            _require_string_keys(item, label)


def canonical_command_payload(payload: Any, label: str = "command payload") -> str:
    if not isinstance(payload, dict):
        raise ControlStoreError(f"{label} must be a JSON object of structured metadata")
    try:
        _require_string_keys(payload, label)
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except RecursionError:
        raise ControlStoreError(
            f"{label} is nested too deeply to serialize canonically"
        ) from None
    except (TypeError, ValueError) as exc:
        raise ControlStoreError(f"cannot serialize {label}: {exc}") from None


def command_payload_digest(payload: Any, label: str = "command payload") -> str:
    canonical = canonical_command_payload(payload, label).encode("utf-8")
    return f"{COMMAND_DIGEST_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


def _require_digest(record: Mapping[str, Any], field: str, label: str) -> str:
    expected = f"'{COMMAND_DIGEST_PREFIX}<{COMMAND_DIGEST_HEX_DIGITS} lowercase hex digits>'"
    value = _require_string(record, field, label)
    if not value.startswith(COMMAND_DIGEST_PREFIX):
        raise ControlStoreError(f"{label} requires a {expected} {field}")
    digits = value[len(COMMAND_DIGEST_PREFIX) :]
    if len(digits) != COMMAND_DIGEST_HEX_DIGITS or not all(
        character in COMMAND_DIGEST_HEX_ALPHABET for character in digits
    ):
        raise ControlStoreError(f"{label} requires a {expected} {field}")
    return value


def _require_command_target(record: Mapping[str, Any], label: str) -> Dict[str, Any]:
    if "target" not in record:
        raise ControlStoreError(f"{label} requires target")
    value = record["target"]
    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object target")
    unknown = sorted(set(value) - set(COMMAND_TARGET_FIELDS))
    if unknown:
        raise ControlStoreError(f"{label} target declares unknown field(s) {', '.join(unknown)}")
    missing = [field for field in COMMAND_TARGET_FIELDS if field not in value]
    if missing:
        raise ControlStoreError(f"{label} target requires {', '.join(missing)}")
    kind = _require_string(value, "kind", f"{label} target")
    if kind not in COMMAND_TARGET_KINDS:
        raise ControlStoreError(
            f"{label} declares unknown target kind {kind!r}; expected one of "
            f"{', '.join(COMMAND_TARGET_KINDS)}"
        )
    _require_identifier(value, "id", f"{label} target")
    return value


def _require_boundary_root(value: Any, field: str, label: str) -> str:
    root = _require_root_path(value, field, label)
    for character in root:
        if not character.isprintable():
            raise ControlStoreError(
                f"{label} requires a printable {field}; {root!r} contains a control "
                "character that could forge a record boundary"
            )
    return root


def _require_command_boundaries(record: Mapping[str, Any], label: str) -> Dict[str, Any]:
    if "boundaries" not in record:
        raise ControlStoreError(f"{label} requires boundaries")
    value = record["boundaries"]
    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object boundaries")
    unknown = sorted(set(value) - set(COMMAND_BOUNDARY_FIELDS))
    if unknown:
        raise ControlStoreError(
            f"{label} boundaries declares unknown field(s) {', '.join(unknown)}"
        )
    missing = [field for field in COMMAND_BOUNDARY_FIELDS if field not in value]
    if missing:
        raise ControlStoreError(f"{label} boundaries requires {', '.join(missing)}")

    sub = f"{label} boundaries"
    _require_boundary_root(value["control_root"], "control_root", sub)
    for field in ("queue_root", "planning_root"):
        if value[field] is not None:
            _require_boundary_root(value[field], field, sub)
    implementation_roots = value["implementation_roots"]
    if not isinstance(implementation_roots, list):
        raise ControlStoreError(f"{sub} requires array implementation_roots")
    seen: List[str] = []
    for index, implementation_root in enumerate(implementation_roots):
        normalized = _require_boundary_root(
            implementation_root, f"implementation_roots[{index}]", sub
        )
        if normalized in seen:
            raise ControlStoreError(
                f"{sub} duplicates implementation root {normalized}"
            )
        seen.append(normalized)
    _require_typed_references(
        value, "runtime_boundaries", sub, required=False, noun="boundary"
    )
    return value


def _require_command_schema_version(record: Mapping[str, Any], label: str) -> None:
    version = record.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlStoreError(f"{label} requires integer schema_version")
    if version > COMMAND_SCHEMA_VERSION:
        raise ControlStoreError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit "
            "tools before mutation"
        )
    if version != COMMAND_SCHEMA_VERSION:
        raise ControlStoreError(f"{label} uses unsupported schema_version {version}")


def _require_closed_fields(record: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    unknown = sorted(set(record) - set(fields))
    if unknown:
        raise ControlStoreError(f"{label} declares unknown field(s) {', '.join(unknown)}")
    missing = [field for field in fields if field not in record]
    if missing:
        raise ControlStoreError(f"{label} requires {', '.join(missing)}")


def validate_command_envelope(record: Any, label: str = "command envelope") -> Dict[str, Any]:
    data = _require_object(record, label)
    _require_command_schema_version(data, label)
    _require_record_type(data, COMMAND_ENVELOPE_RECORD_TYPE, label)
    _require_closed_fields(data, COMMAND_ENVELOPE_FIELDS, label)
    _require_uuid(data, "command_id", label)
    _require_identifier(data, "command_type", label)
    _require_uuid(data, "mission_id", label)
    _require_identifier(data, "queue_item_id", label)
    _require_command_target(data, label)
    trace_id = _require_uuid(data, "trace_id", label)
    parent_trace_id = _require_optional_uuid(data, "parent_trace_id", label)
    if parent_trace_id is not None and parent_trace_id == trace_id:
        raise ControlStoreError(f"{label} requires parent_trace_id to name a different trace")
    _require_digest(data, "payload_digest", label)
    _require_command_boundaries(data, label)
    created_at = _require_timestamp(data, "created_at", label)
    if data["deadline_at"] is not None:
        deadline = _parsed_timestamp(data["deadline_at"], "deadline_at", label)
        if deadline <= _parsed_timestamp(created_at, "created_at", label):
            raise ControlStoreError(f"{label} requires deadline_at after created_at")
    return data


def validate_command_acknowledgement(
    record: Any,
    label: str = "command acknowledgement",
) -> Dict[str, Any]:
    data = _require_object(record, label)
    _require_command_schema_version(data, label)
    _require_record_type(data, COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE, label)
    _require_closed_fields(data, COMMAND_ACKNOWLEDGEMENT_FIELDS, label)
    _require_uuid(data, "command_id", label)
    _require_digest(data, "payload_digest", label)
    outcome = _require_string(data, "outcome", label)
    if outcome not in COMMAND_ACKNOWLEDGEMENT_OUTCOMES:
        raise ControlStoreError(
            f"{label} declares unknown outcome {outcome!r}; expected one of "
            f"{', '.join(COMMAND_ACKNOWLEDGEMENT_OUTCOMES)}"
        )
    _require_identifier(data, "acknowledged_by", label)
    _require_timestamp(data, "acknowledged_at", label)
    if _require_optional_string(data, "reason", label) is None:
        if outcome in COMMAND_ACKNOWLEDGEMENT_REASON_REQUIRED:
            raise ControlStoreError(f"{label} requires a reason for outcome {outcome!r}")
    _require_typed_references(
        data, "result_refs", label, required=outcome == COMMAND_APPLIED, noun="result"
    )
    return data


def event_command_envelope(record: Mapping[str, Any], label: str) -> Optional[Dict[str, Any]]:
    event_type = _require_string(record, "event_type", label)
    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if COMMAND_ENVELOPE_PAYLOAD_FIELD not in payload:
        if event_type == COMMAND_REGISTERED_EVENT_TYPE:
            raise ControlStoreError(
                f"{label} declares event_type {event_type!r} without a "
                f"payload.{COMMAND_ENVELOPE_PAYLOAD_FIELD} record"
            )
        return None
    envelope = validate_command_envelope(
        payload[COMMAND_ENVELOPE_PAYLOAD_FIELD],
        f"{label} payload.{COMMAND_ENVELOPE_PAYLOAD_FIELD}",
    )
    if event_type != COMMAND_REGISTERED_EVENT_TYPE:
        raise ControlStoreError(
            f"{label} carries a {COMMAND_ENVELOPE_PAYLOAD_FIELD} record but event_type "
            f"{event_type!r}; expected event_type {COMMAND_REGISTERED_EVENT_TYPE!r}"
        )
    return envelope


def event_command_acknowledgement(
    record: Mapping[str, Any],
    label: str,
) -> Optional[Dict[str, Any]]:
    event_type = _require_string(record, "event_type", label)
    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD not in payload:
        if event_type.startswith(COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX):
            raise ControlStoreError(
                f"{label} declares acknowledgement event_type {event_type!r} without a "
                f"payload.{COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD} record"
            )
        return None
    acknowledgement = validate_command_acknowledgement(
        payload[COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD],
        f"{label} payload.{COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD}",
    )
    expected = f"{COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX}{acknowledgement['outcome']}"
    joint_acceptance = (
        event_type == "worker-lifecycle-accepted"
        and acknowledgement["outcome"] == COMMAND_ACCEPTED
        and "worker_lifecycle" in payload
    )
    if event_type != expected and not joint_acceptance:
        raise ControlStoreError(
            f"{label} declares acknowledgement outcome {acknowledgement['outcome']!r} but "
            f"event_type {event_type!r}; expected event_type {expected!r}"
        )
    return acknowledgement


def _command_conflict_reason(
    stored: Mapping[str, Any],
    offered: Mapping[str, Any],
) -> Optional[str]:
    if stored["payload_digest"] != offered["payload_digest"]:
        return COMMAND_CONFLICT_DIGEST
    for field in COMMAND_ENVELOPE_IDENTITY_FIELDS:
        if stored[field] != offered[field]:
            return COMMAND_CONFLICT_ENVELOPE
    return None


def _command_conflict_entry(
    payload_digest: str,
    reason: str,
    conflict_source: str,
    event: Any,
) -> Dict[str, Any]:
    return {
        "payload_digest": payload_digest,
        "reason": reason,
        "source": conflict_source,
        "revision": event.revision,
        "event_id": event.event_id,
        "recorded_at": event.record["timestamp"],
    }


def apply_command_envelope(
    slots: Dict[str, Dict[str, Any]],
    envelope: Mapping[str, Any],
    event: Any,
) -> Tuple[bool, str]:
    command_id = envelope["command_id"]
    delivery = {
        "revision": event.revision,
        "event_id": event.event_id,
        "recorded_at": event.record["timestamp"],
    }
    slot = slots.get(command_id)
    if slot is None:
        slots[command_id] = {
            "envelope": dict(envelope),
            "status": COMMAND_STATUS_REGISTERED,
            "deliveries": [delivery],
            "acknowledgements": [],
            "conflicts": [],
            "revision": event.revision,
            "event_id": event.event_id,
            "recorded_at": event.record["timestamp"],
        }
        return True, COMMAND_FOLD_REGISTERED

    reason = _command_conflict_reason(slot["envelope"], envelope)
    if reason is not None:
        slot["conflicts"].append(
            _command_conflict_entry(
                envelope["payload_digest"], reason, COMMAND_CONFLICT_DELIVERY, event
            )
        )
        return True, COMMAND_FOLD_CONFLICT_RECORDED
    slot["deliveries"].append(delivery)
    return False, COMMAND_FOLD_RETAINED_DUPLICATE


def apply_command_acknowledgement(
    slots: Dict[str, Dict[str, Any]],
    acknowledgement: Mapping[str, Any],
    event: Any,
) -> Tuple[bool, str]:
    command_id = acknowledgement["command_id"]
    slot = slots.get(command_id)
    if slot is None:
        return False, COMMAND_FOLD_RETAINED_UNKNOWN
    if (slot["envelope"]["target"]["kind"] == "worker"
        and acknowledgement["acknowledged_by"] != slot["envelope"]["target"]["id"]):
        return False, COMMAND_FOLD_RETAINED_ORDER

    entry = {
        "acknowledgement": dict(acknowledgement),
        "revision": event.revision,
        "event_id": event.event_id,
        "recorded_at": event.record["timestamp"],
    }
    if acknowledgement["payload_digest"] != slot["envelope"]["payload_digest"]:
        if acknowledgement["outcome"] != COMMAND_REJECTED:
            return False, COMMAND_FOLD_RETAINED_DIGEST
        slot["acknowledgements"].append(entry)
        slot["conflicts"].append(
            _command_conflict_entry(
                acknowledgement["payload_digest"],
                COMMAND_CONFLICT_DIGEST,
                COMMAND_CONFLICT_ACKNOWLEDGEMENT,
                event,
            )
        )
        return True, COMMAND_FOLD_CONFLICT_RECORDED

    allowed = COMMAND_ACKNOWLEDGEMENT_TRANSITIONS.get(slot["status"], {})
    next_status = allowed.get(acknowledgement["outcome"])
    if next_status is None:
        return False, COMMAND_FOLD_RETAINED_ORDER
    slot["acknowledgements"].append(entry)
    slot["status"] = next_status
    return True, COMMAND_FOLD_ACKNOWLEDGED


def fold_commands(
    events: Sequence[Any] = (),
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    slots: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, Tuple[bool, str]] = {}
    lifecycle_outcomes = None
    for event in events:
        label = f"{EVENTS_DIR_NAME}/{event.path.name}"
        envelope = event_command_envelope(event.record, label)
        if envelope is not None:
            if mission_command_correlation_fault(event.record, slots) is not None:
                outcomes[event.event_id] = (False, COMMAND_FOLD_RETAINED_ORDER)
                continue
            outcomes[event.event_id] = apply_command_envelope(slots, envelope, event)
            continue
        acknowledgement = event_command_acknowledgement(event.record, label)
        if acknowledgement is not None:
            slot = slots.get(acknowledgement["command_id"])
            joint = "worker_lifecycle" in event.record["payload"]
            managed_acceptance = (
                slot is not None
                and slot["envelope"]["command_type"] == "mission-dispatch"
                and slot["envelope"]["deadline_at"] is not None
                and acknowledgement["outcome"] == COMMAND_ACCEPTED
            )
            if joint or managed_acceptance:
                if lifecycle_outcomes is None:
                    lifecycle_outcomes = fold_mission_state(events).lifecycle_outcomes
                if not joint or not lifecycle_outcomes.get(event.event_id, (False, ""))[0]:
                    outcomes[event.event_id] = (False, COMMAND_FOLD_RETAINED_ORDER)
                    continue
            outcomes[event.event_id] = apply_command_acknowledgement(
                slots, acknowledgement, event
            )
    return slots, outcomes


def dispatch_acceptance_fault(
    command: Optional[Mapping[str, Any]],
    acknowledgement: Optional[Mapping[str, Any]],
    lifecycle: Mapping[str, Any],
    slot: Optional[Mapping[str, Any]],
    missions: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    """Validate both halves of a first dispatch receipt before either folds."""

    if command is None or acknowledgement is None:
        return "dispatch acceptance requires its registered command and acknowledgement"
    envelope = command["envelope"]
    if (
        envelope["command_type"] != "mission-dispatch"
        or envelope["target"]["kind"] != COMMAND_TARGET_WORKER
        or command["status"] != COMMAND_STATUS_REGISTERED
        or command["conflicts"]
        or acknowledgement["outcome"] != COMMAND_ACCEPTED
        or acknowledgement["command_id"] != envelope["command_id"]
        or acknowledgement["payload_digest"] != envelope["payload_digest"]
        or acknowledgement["acknowledged_by"] != envelope["target"]["id"]
        or lifecycle["worker_id"] != envelope["target"]["id"]
        or any(lifecycle[field] != envelope[field] for field in (
            "mission_id", "queue_item_id", "trace_id", "parent_trace_id"
        ))
        or lifecycle["state"] != "accepted"
        or lifecycle["sequence"] != 1
    ):
        return "dispatch acceptance has mismatched identity, digest, or command state"
    if (
        slot is None or slot["state"] != "reserved"
        or any(
            entry["revision"] >= slot["revision"]
            and entry["mission_id"] in missions
            and missions[entry["mission_id"]]["lifecycle"]["state"] in ("accepted", "running", "blocked")
            for entry in slot["conflicts"]
        )
        or slot["mission_id"] != envelope["mission_id"]
        or slot["queue_item_id"] != envelope["queue_item_id"]
        or slot["command_id"] != envelope["command_id"]
        or (envelope["mission_id"] in missions and
            missions[envelope["mission_id"]]["lifecycle"]["state"] != "pending-dispatch")
    ):
        return "dispatch acceptance requires its own reserved, unaccepted mission slot"
    deadline = envelope["deadline_at"]
    if deadline is None:
        return "dispatch has no acceptance deadline; explicit operator recovery is required"
    accepted_at = _parsed_timestamp(acknowledgement["acknowledged_at"], "acknowledged_at", "acceptance")
    if (
        accepted_at < _parsed_timestamp(envelope["created_at"], "created_at", "dispatch")
        or accepted_at >= _parsed_timestamp(deadline, "deadline_at", "dispatch")
        or lifecycle["heartbeat_at"] != acknowledgement["acknowledged_at"]
    ):
        return "dispatch acceptance is outside its acceptance deadline"
    return None


def build_command_envelope(
    command_id: str,
    command_type: str,
    mission_id: str,
    queue_item_id: str,
    target_kind: str,
    target_id: str,
    trace_id: str,
    payload_digest: str,
    control_root: str,
    parent_trace_id: Optional[str] = None,
    queue_root: Optional[str] = None,
    planning_root: Optional[str] = None,
    implementation_roots: Sequence[str] = (),
    runtime_boundaries: Sequence[str] = (),
    created_at: Optional[str] = None,
    deadline_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "record_type": COMMAND_ENVELOPE_RECORD_TYPE,
        "command_id": command_id,
        "command_type": command_type,
        "mission_id": mission_id,
        "queue_item_id": queue_item_id,
        "target": {"kind": target_kind, "id": target_id},
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "payload_digest": payload_digest,
        "boundaries": {
            "control_root": control_root,
            "queue_root": queue_root,
            "planning_root": planning_root,
            "implementation_roots": list(implementation_roots),
            "runtime_boundaries": list(runtime_boundaries),
        },
        "created_at": created_at if created_at is not None else utc_timestamp(),
        "deadline_at": deadline_at,
    }
    return validate_command_envelope(record)


def build_command_acknowledgement(
    command_id: str,
    payload_digest: str,
    outcome: str,
    acknowledged_by: str,
    reason: Optional[str] = None,
    result_refs: Sequence[str] = (),
    acknowledged_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": COMMAND_SCHEMA_VERSION,
        "record_type": COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE,
        "command_id": command_id,
        "payload_digest": payload_digest,
        "outcome": outcome,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": (
            acknowledged_at if acknowledged_at is not None else utc_timestamp()
        ),
        "reason": reason,
        "result_refs": list(result_refs),
    }
    return validate_command_acknowledgement(record)


def read_command_slots(root: Path) -> Dict[str, Dict[str, Any]]:
    _metadata, history = inspect_control_events(root)
    slots, _outcomes = fold_commands(history.events)
    return slots


def _committed_events_after_publication(
    publication: Any,
) -> Tuple[Any, ...]:
    try:
        _metadata, history = inspect_control_events(publication.root)
    except ControlStoreError as exc:
        if not publication.committed:
            raise
        raise ControlStoreError(
            f"{EVENTS_DIR_NAME}/{publication.path.name} is committed at revision "
            f"{publication.revision}; only its materialization could not be reported: {exc}"
        ) from None
    events = history.events
    if not publication.committed:
        events = events + (
            CommittedEvent(
                path=publication.path,
                revision=history.latest_revision + 1,
                event_id=publication.event_id,
                record=publication.record,
            ),
        )
    return events


def _folded_commands_after_publication(
    publication: Any,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    return fold_commands(_committed_events_after_publication(publication))


def register_command(
    root: Path,
    envelope: Mapping[str, Any],
    actor: str = DEFAULT_EVENT_ACTOR,
    command: str = DEFAULT_COMMAND_REGISTER_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
    correlated_record: Optional[Mapping[str, Any]] = None,
    declarations: Optional[Mapping[str, Any]] = None,
    held_lock: Optional[Any] = None,
) -> CommandRecordResult:
    validated = validate_command_envelope(dict(envelope))
    actor = _require_identifier({"actor": actor}, "actor", "command delivery")
    root = _require_absolute_root(str(root), "configured")
    stored_slot = read_command_slots(root).get(validated["command_id"])

    acknowledgement: Optional[Dict[str, Any]] = None
    if stored_slot is None:
        event_type = COMMAND_REGISTERED_EVENT_TYPE
        payload: Dict[str, Any] = {COMMAND_ENVELOPE_PAYLOAD_FIELD: validated}
    elif _command_conflict_reason(stored_slot["envelope"], validated) is not None:
        event_type = COMMAND_REGISTERED_EVENT_TYPE
        payload = {COMMAND_ENVELOPE_PAYLOAD_FIELD: validated}
    else:
        acknowledgement = build_command_acknowledgement(
            validated["command_id"],
            stored_slot["envelope"]["payload_digest"],
            COMMAND_DUPLICATE,
            acknowledged_by=stored_slot["envelope"]["target"]["id"],
            reason=COMMAND_DUPLICATE_REASON,
        )
        event_type = f"{COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX}{COMMAND_DUPLICATE}"
        payload = {COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD: acknowledgement}

    if event_type == COMMAND_REGISTERED_EVENT_TYPE:
        if correlated_record is not None:
            payload = dict(payload)
            payload.update({key: dict(value) for key, value in correlated_record.items()})
        if declarations is not None:
            payload = dict(payload)
            payload.update(dict(declarations))

    publication = publish_control_event(
        root,
        event_type,
        actor=actor,
        payload=payload,
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
        held_lock=held_lock,
    )
    slots, outcomes = _folded_commands_after_publication(publication)
    applied, outcome = outcomes.get(
        publication.event_id, (False, COMMAND_FOLD_RETAINED_UNKNOWN)
    )
    materialized = slots.get(validated["command_id"])
    return CommandRecordResult(
        root=publication.root,
        publication=publication,
        command_id=validated["command_id"],
        envelope=dict(validated),
        acknowledgement=acknowledgement,
        applied=applied,
        outcome=outcome,
        materialized=materialized,
    )


def acknowledge_command(
    root: Path,
    acknowledgement: Mapping[str, Any],
    actor: Optional[str] = None,
    command: str = DEFAULT_COMMAND_ACKNOWLEDGE_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> CommandRecordResult:
    # Reject malformed/non-applicable receipts without creating lock debris.
    # Revalidate under the lock before publication to close the race.
    preview = _acknowledge_command_locked(
        root, acknowledgement, actor, command, timeout_seconds, poll_seconds, True, None,
    )
    if dry_run:
        return preview
    with PortableControlLock(
        root, command=command, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
    ) as lock:
        return _acknowledge_command_locked(
            root, acknowledgement, actor, command, timeout_seconds, poll_seconds, dry_run, lock,
        )


def _acknowledge_command_locked(
    root: Path, acknowledgement: Mapping[str, Any], actor: Optional[str],
    command: str, timeout_seconds: Optional[float], poll_seconds: float,
    dry_run: bool, lock: Optional[Any],
) -> CommandRecordResult:
    validated = validate_command_acknowledgement(dict(acknowledgement))
    actor = _require_identifier(
        {"actor": actor if actor is not None else validated["acknowledged_by"]},
        "actor",
        "command acknowledgement",
    )
    root = _require_absolute_root(str(root), "configured")
    command_id = validated["command_id"]
    outcome = validated["outcome"]
    slot = read_command_slots(root).get(command_id)
    if slot is None:
        raise ControlStoreError(
            f"command acknowledgement names command {command_id}, which this control "
            "root has never registered"
        )
    if (slot["envelope"]["target"]["kind"] == "worker"
        and validated["acknowledged_by"] != slot["envelope"]["target"]["id"]):
        raise ControlStoreError("only the correlated target worker can acknowledge this command")
    if slot["envelope"]["command_type"] == "mission-dispatch" and outcome == COMMAND_ACCEPTED:
        raise ControlStoreError("use cockpit-control accept-dispatch for atomic worker acceptance")
    stored_digest = slot["envelope"]["payload_digest"]
    if validated["payload_digest"] != stored_digest and outcome != COMMAND_REJECTED:
        raise ControlStoreError(
            f"command acknowledgement declares payload digest {validated['payload_digest']} "
            f"but command {command_id} was registered with {stored_digest}; only a "
            f"{COMMAND_REJECTED!r} acknowledgement may name a different digest"
        )
    if validated["payload_digest"] == stored_digest:
        allowed = COMMAND_ACKNOWLEDGEMENT_TRANSITIONS.get(slot["status"], {})
        if outcome not in allowed:
            raise ControlStoreError(
                f"command {command_id} is {slot['status']}; a {outcome!r} acknowledgement "
                f"is not allowed from that status (allowed: "
                f"{', '.join(sorted(allowed)) or 'none'})"
            )

    publication = publish_control_event(
        root,
        f"{COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX}{outcome}",
        actor=actor,
        payload={COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD: validated},
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
        held_lock=lock,
    )
    slots, outcomes = _folded_commands_after_publication(publication)
    applied, fold_outcome = outcomes.get(
        publication.event_id, (False, COMMAND_FOLD_RETAINED_UNKNOWN)
    )
    materialized = slots.get(command_id)
    return CommandRecordResult(
        root=publication.root,
        publication=publication,
        command_id=command_id,
        envelope=dict(slot["envelope"]),
        acknowledgement=dict(validated),
        applied=applied,
        outcome=fold_outcome,
        materialized=materialized,
    )


def observe_commands(
    root: Path,
    command_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], ...]:
    root = _require_absolute_root(str(root), "configured")
    if command_id is not None:
        command_id = _require_uuid({"command_id": command_id}, "command_id", "command status")
    if mission_id is not None:
        mission_id = _require_uuid({"mission_id": mission_id}, "mission_id", "command status")
    if target_id is not None:
        target_id = _require_identifier({"target": target_id}, "target", "command status")

    slots = read_command_slots(root)
    observed: List[Dict[str, Any]] = []
    for key in sorted(slots):
        envelope = slots[key]["envelope"]
        if command_id is not None and envelope["command_id"] != command_id:
            continue
        if mission_id is not None and envelope["mission_id"] != mission_id:
            continue
        if target_id is not None and envelope["target"]["id"] != target_id:
            continue
        observed.append(slots[key])
    return tuple(observed)
