"""Worker lifecycle seam for vocabulary, validation, fold, and freshness.

This runtime module owns the versioned worker lifecycle vocabulary, lifecycle
record validation, event extraction/building, deterministic transition folding,
and heartbeat freshness observation.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# Lifecycle schema vocabulary.
WORKER_LIFECYCLE_SCHEMA_VERSION = 1
WORKER_LIFECYCLE_RECORD_TYPE = "worker-lifecycle"
WORKER_LIFECYCLE_PAYLOAD_FIELD = "worker_lifecycle"
WORKER_LIFECYCLE_EVENT_PREFIX = "worker-lifecycle-"

LIFECYCLE_PENDING_DISPATCH = "pending-dispatch"
LIFECYCLE_ACCEPTED = "accepted"
LIFECYCLE_RUNNING = "running"
LIFECYCLE_BLOCKED = "blocked"
LIFECYCLE_COMPLETED = "completed"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_CANCELLED = "cancelled"
LIFECYCLE_REPLACED = "replaced"

WORKER_LIFECYCLE_STATES = (
    LIFECYCLE_PENDING_DISPATCH,
    LIFECYCLE_ACCEPTED,
    LIFECYCLE_RUNNING,
    LIFECYCLE_BLOCKED,
    LIFECYCLE_COMPLETED,
    LIFECYCLE_FAILED,
    LIFECYCLE_CANCELLED,
    LIFECYCLE_REPLACED,
)
WORKER_LIFECYCLE_ACTIVE_STATES = (
    LIFECYCLE_ACCEPTED,
    LIFECYCLE_RUNNING,
    LIFECYCLE_BLOCKED,
)
WORKER_LIFECYCLE_TERMINAL_STATES = (
    LIFECYCLE_COMPLETED,
    LIFECYCLE_FAILED,
    LIFECYCLE_CANCELLED,
    LIFECYCLE_REPLACED,
)
WORKER_LIFECYCLE_REASON_REQUIRED = (
    LIFECYCLE_BLOCKED,
    LIFECYCLE_FAILED,
    LIFECYCLE_CANCELLED,
    LIFECYCLE_REPLACED,
)
WORKER_LIFECYCLE_TRANSITIONS = {
    LIFECYCLE_PENDING_DISPATCH: (LIFECYCLE_ACCEPTED,),
    LIFECYCLE_ACCEPTED: (
        LIFECYCLE_ACCEPTED,
        LIFECYCLE_RUNNING,
        LIFECYCLE_REPLACED,
    ),
    LIFECYCLE_RUNNING: (
        LIFECYCLE_RUNNING,
        LIFECYCLE_BLOCKED,
        LIFECYCLE_COMPLETED,
        LIFECYCLE_FAILED,
        LIFECYCLE_CANCELLED,
        LIFECYCLE_REPLACED,
    ),
    LIFECYCLE_BLOCKED: (
        LIFECYCLE_BLOCKED,
        LIFECYCLE_RUNNING,
        LIFECYCLE_FAILED,
        LIFECYCLE_CANCELLED,
        LIFECYCLE_REPLACED,
    ),
}

WORKER_LIFECYCLE_FIELDS = (
    "schema_version",
    "record_type",
    "state",
    "worker_id",
    "mission_id",
    "queue_item_id",
    "trace_id",
    "parent_trace_id",
    "sequence",
    "reason",
    "blocker",
    "heartbeat_at",
    "fresh_until",
    "evidence_refs",
    "superseded_by_mission_id",
)
WORKER_LIFECYCLE_BLOCKER_FIELDS = ("category", "detail")

LIFECYCLE_APPLIED = "applied"
LIFECYCLE_RETAINED_TERMINAL = "retained-terminal-state"
LIFECYCLE_RETAINED_STALE_SEQUENCE = "retained-stale-sequence"
LIFECYCLE_RETAINED_INVALID_TRANSITION = "retained-invalid-transition"
LIFECYCLE_RETAINED_UNMATCHED = "retained-unmatched-correlation"

LIFECYCLE_OBSERVATION_FRESH = "fresh"
LIFECYCLE_OBSERVATION_STALE = "stale"
LIFECYCLE_OBSERVATION_TERMINAL = "terminal"
LIFECYCLE_STALE_REASON = "heartbeat-expired"
LIFECYCLE_STALE_RECOVERY = "awaiting-bounded-recovery"

DEFAULT_LIFECYCLE_COMMAND = "cockpit-control record-lifecycle"
DEFAULT_LOCK_POLL_SECONDS = 0.05
EVENTS_DIR_NAME = "events"


class ControlStoreError(RuntimeError):
    """Facade overrides this class to keep compatibility of raised errors."""


# Facade-bound dependencies.
_require_typed_references: Callable[..., List[str]]
_require_object: Callable[..., Dict[str, Any]]
_require_record_type: Callable[..., str]
_require_string: Callable[..., str]
_require_identifier: Callable[..., str]
_require_uuid: Callable[..., str]
_require_optional_uuid: Callable[..., Optional[str]]
_require_positive_integer: Callable[..., int]
_require_optional_string: Callable[..., Optional[str]]
_parsed_timestamp: Callable[..., datetime]
_require_absolute_root: Callable[..., Path]
publish_control_event: Callable[..., Any]
inspect_control_events: Callable[..., Any]
fold_mission_state: Callable[..., Any]
PortableControlLock: Callable[..., Any]
utc_timestamp: Callable[[], str]
CommittedEvent: Any
EventPublicationResult: Any


@dataclass(frozen=True)
class LifecycleRecordResult:
    """Outcome of committing one versioned worker lifecycle event."""

    root: Path
    publication: Any
    lifecycle: Dict[str, Any]
    applied: bool
    outcome: str
    materialized: Optional[Dict[str, Any]]

    @property
    def committed(self) -> bool:
        return self.publication.committed

    @property
    def retained_for_audit(self) -> bool:
        return not self.applied


@dataclass(frozen=True)
class WorkerLifecycleObservation:
    """One freshness observation of a materialized worker mission slot."""

    mission_id: str
    worker_id: str
    queue_item_id: str
    trace_id: str
    state: str
    sequence: int
    terminal: bool
    observation: str
    reason: Optional[str]
    recoverable: bool
    recovery: Optional[str]
    heartbeat_at: Optional[str]
    fresh_until: Optional[str]
    revision: int
    event_id: str
    as_of: str

    @property
    def stale(self) -> bool:
        return self.observation == LIFECYCLE_OBSERVATION_STALE


def has_lifecycle_capability(metadata: Mapping[str, Any]) -> bool:
    """Require explicit supported integer versions, never truthy legacy flags."""

    capabilities = metadata.get("capabilities", {})
    return all(
        type(capabilities.get(key)) is int and capabilities[key] == 1
        for key in ("worker_lifecycle", "command_protocol")
    )


def _require_evidence_refs(record: Mapping[str, Any], label: str, *, required: bool) -> List[str]:
    return _require_typed_references(
        record, "evidence_refs", label, required=required, noun="evidence"
    )


def _require_blocker(record: Mapping[str, Any], label: str, state: str) -> Optional[Dict[str, Any]]:
    if "blocker" not in record:
        raise ControlStoreError(f"{label} requires blocker")
    value = record["blocker"]
    if state != LIFECYCLE_BLOCKED:
        if value is not None:
            raise ControlStoreError(f"{label} must not declare a blocker for state {state!r}")
        return None
    if not isinstance(value, dict):
        raise ControlStoreError(
            f"{label} requires an object blocker for state {LIFECYCLE_BLOCKED!r}"
        )
    unknown = sorted(set(value) - set(WORKER_LIFECYCLE_BLOCKER_FIELDS))
    if unknown:
        raise ControlStoreError(f"{label} blocker declares unknown field(s) {', '.join(unknown)}")
    _require_string(value, "category", f"{label} blocker")
    _require_optional_string(value, "detail", f"{label} blocker")
    return value


def validate_worker_lifecycle(record: Any, label: str = "worker lifecycle") -> Dict[str, Any]:
    data = _require_object(record, label)
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlStoreError(f"{label} requires integer schema_version")
    if version > WORKER_LIFECYCLE_SCHEMA_VERSION:
        raise ControlStoreError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit "
            "tools before mutation"
        )
    if version != WORKER_LIFECYCLE_SCHEMA_VERSION:
        raise ControlStoreError(f"{label} uses unsupported schema_version {version}")
    _require_record_type(data, WORKER_LIFECYCLE_RECORD_TYPE, label)

    unknown = sorted(set(data) - set(WORKER_LIFECYCLE_FIELDS))
    if unknown:
        raise ControlStoreError(f"{label} declares unknown field(s) {', '.join(unknown)}")
    missing = [field for field in WORKER_LIFECYCLE_FIELDS if field not in data]
    if missing:
        raise ControlStoreError(f"{label} requires {', '.join(missing)}")

    state = _require_string(data, "state", label)
    if state not in WORKER_LIFECYCLE_STATES:
        raise ControlStoreError(
            f"{label} declares unknown state {state!r}; expected one of "
            f"{', '.join(WORKER_LIFECYCLE_STATES)}"
        )
    _require_identifier(data, "worker_id", label)
    mission_id = _require_uuid(data, "mission_id", label)
    _require_identifier(data, "queue_item_id", label)
    _require_uuid(data, "trace_id", label)
    _require_optional_uuid(data, "parent_trace_id", label)
    if state == LIFECYCLE_PENDING_DISPATCH:
        if type(data["sequence"]) is not int or data["sequence"] != 0:
            raise ControlStoreError(f"{label} pending-dispatch requires sequence 0")
    else:
        _require_positive_integer(data, "sequence", label)

    if _require_optional_string(data, "reason", label) is None:
        if state in WORKER_LIFECYCLE_REASON_REQUIRED:
            raise ControlStoreError(f"{label} requires a reason for state {state!r}")
    _require_blocker(data, label, state)
    _require_evidence_refs(data, label, required=state == LIFECYCLE_COMPLETED)

    if state in WORKER_LIFECYCLE_ACTIVE_STATES:
        heartbeat = _parsed_timestamp(
            _require_string(data, "heartbeat_at", label), "heartbeat_at", label
        )
        expiry = _parsed_timestamp(_require_string(data, "fresh_until", label), "fresh_until", label)
        if expiry <= heartbeat:
            raise ControlStoreError(f"{label} requires fresh_until after heartbeat_at")
    else:
        for field in ("heartbeat_at", "fresh_until"):
            if data[field] is not None:
                raise ControlStoreError(
                    f"{label} must not declare {field} for terminal state {state!r}"
                )

    if state == LIFECYCLE_REPLACED:
        if _require_uuid(data, "superseded_by_mission_id", label) == mission_id:
            raise ControlStoreError(
                f"{label} requires superseded_by_mission_id to name a different mission"
            )
    elif data["superseded_by_mission_id"] is not None:
        raise ControlStoreError(
            f"{label} must not declare superseded_by_mission_id for state {state!r}"
        )
    return data


def event_worker_lifecycle(record: Mapping[str, Any], label: str) -> Optional[Dict[str, Any]]:
    event_type = _require_string(record, "event_type", label)
    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    if WORKER_LIFECYCLE_PAYLOAD_FIELD not in payload:
        if event_type.startswith(WORKER_LIFECYCLE_EVENT_PREFIX):
            raise ControlStoreError(
                f"{label} declares lifecycle event_type {event_type!r} without a "
                f"payload.{WORKER_LIFECYCLE_PAYLOAD_FIELD} record"
            )
        return None
    lifecycle = validate_worker_lifecycle(
        payload[WORKER_LIFECYCLE_PAYLOAD_FIELD], f"{label} payload.{WORKER_LIFECYCLE_PAYLOAD_FIELD}"
    )
    expected = f"{WORKER_LIFECYCLE_EVENT_PREFIX}{lifecycle['state']}"
    if lifecycle["state"] == LIFECYCLE_PENDING_DISPATCH:
        dispatch = payload.get("controller_dispatch")
        if event_type != "command-registered" or not isinstance(dispatch, dict) or any(
            lifecycle[key] != dispatch.get(key)
            for key in ("mission_id", "worker_id", "queue_item_id", "trace_id", "parent_trace_id")
        ):
            raise ControlStoreError(f"{label} pending-dispatch requires its controller dispatch")
        return lifecycle
    if event_type != expected:
        raise ControlStoreError(
            f"{label} declares lifecycle state {lifecycle['state']!r} but event_type "
            f"{event_type!r}; expected event_type {expected!r}"
        )
    return lifecycle


def apply_worker_lifecycle(
    slots: Dict[str, Dict[str, Any]],
    lifecycle: Mapping[str, Any],
    event: Any,
) -> Tuple[bool, str]:
    mission_id = lifecycle["mission_id"]
    slot = slots.get(mission_id)
    current_state = LIFECYCLE_PENDING_DISPATCH
    if slot is not None:
        materialized = slot["lifecycle"]
        current_state = materialized["state"]
        if (
            materialized["worker_id"] != lifecycle["worker_id"]
            or materialized["queue_item_id"] != lifecycle["queue_item_id"]
        ):
            return False, LIFECYCLE_RETAINED_UNMATCHED
        if current_state in WORKER_LIFECYCLE_TERMINAL_STATES:
            return False, LIFECYCLE_RETAINED_TERMINAL
        if lifecycle["sequence"] <= materialized["sequence"]:
            return False, LIFECYCLE_RETAINED_STALE_SEQUENCE
    if lifecycle["state"] not in WORKER_LIFECYCLE_TRANSITIONS.get(current_state, ()):
        return False, LIFECYCLE_RETAINED_INVALID_TRANSITION

    slots[mission_id] = {
        "lifecycle": dict(lifecycle),
        "revision": event.revision,
        "event_id": event.event_id,
        "recorded_at": event.record["timestamp"],
    }
    return True, LIFECYCLE_APPLIED


def fold_worker_missions(events: Sequence[Any] = ()) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    state = fold_mission_state(events)
    return state.missions, state.lifecycle_outcomes


def build_worker_lifecycle(
    state: str,
    worker_id: str,
    mission_id: str,
    queue_item_id: str,
    trace_id: str,
    sequence: Any,
    parent_trace_id: Optional[str] = None,
    reason: Optional[str] = None,
    blocker: Optional[Mapping[str, Any]] = None,
    heartbeat_at: Optional[str] = None,
    fresh_until: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    superseded_by_mission_id: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": WORKER_LIFECYCLE_SCHEMA_VERSION,
        "record_type": WORKER_LIFECYCLE_RECORD_TYPE,
        "state": state,
        "worker_id": worker_id,
        "mission_id": mission_id,
        "queue_item_id": queue_item_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "sequence": sequence,
        "reason": reason,
        "blocker": None if blocker is None else dict(blocker),
        "heartbeat_at": heartbeat_at,
        "fresh_until": fresh_until,
        "evidence_refs": list(evidence_refs),
        "superseded_by_mission_id": superseded_by_mission_id,
    }
    return validate_worker_lifecycle(record, "worker lifecycle")


def record_worker_lifecycle(
    root: Path,
    lifecycle: Mapping[str, Any],
    actor: Optional[str] = None,
    command: str = DEFAULT_LIFECYCLE_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> LifecycleRecordResult:
    validated = validate_worker_lifecycle(dict(lifecycle), "worker lifecycle")
    if validated["state"] == LIFECYCLE_PENDING_DISPATCH:
        raise ControlStoreError("pending-dispatch is published only by the FIFO controller")
    accepting = validated["state"] == LIFECYCLE_ACCEPTED
    with (PortableControlLock(
        root, command=command, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
    ) if accepting and not dry_run else nullcontext(None)) as lock:
        if accepting:
            _metadata, history = inspect_control_events(root)
            for event in history.events:
                envelope = event.record["payload"].get("command_envelope")
                if (
                    envelope is not None and envelope["command_type"] == "mission-dispatch"
                    and envelope["mission_id"] == validated["mission_id"]
                ):
                    if envelope["deadline_at"] is None:
                        raise ControlStoreError(
                            "dispatch has no acceptance deadline; explicit operator recovery is required"
                        )
                    raise ControlStoreError(
                        "use cockpit-control accept-dispatch for atomic worker acceptance"
                    )
        publication = publish_control_event(
            root,
            f"{WORKER_LIFECYCLE_EVENT_PREFIX}{validated['state']}",
            actor=actor or validated["worker_id"],
            payload={WORKER_LIFECYCLE_PAYLOAD_FIELD: validated},
            command=command,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            dry_run=dry_run,
            held_lock=lock,
        )

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
    slots, outcomes = fold_worker_missions(events)
    applied, outcome = outcomes.get(publication.event_id, (False, LIFECYCLE_RETAINED_UNMATCHED))
    return LifecycleRecordResult(
        root=publication.root,
        publication=publication,
        lifecycle=dict(validated),
        applied=applied,
        outcome=outcome,
        materialized=slots.get(validated["mission_id"]),
    )


def _observed_slot(slot: Mapping[str, Any], now: datetime, as_of: str) -> WorkerLifecycleObservation:
    lifecycle = slot["lifecycle"]
    state = lifecycle["state"]
    terminal = state in WORKER_LIFECYCLE_TERMINAL_STATES
    observation = LIFECYCLE_OBSERVATION_TERMINAL
    reason: Optional[str] = None
    recovery: Optional[str] = None
    recoverable = False
    if state == LIFECYCLE_PENDING_DISPATCH:
        observation = LIFECYCLE_PENDING_DISPATCH
    elif not terminal:
        expiry = _parsed_timestamp(lifecycle["fresh_until"], "fresh_until", "worker lifecycle")
        if now > expiry:
            observation = LIFECYCLE_OBSERVATION_STALE
            reason = LIFECYCLE_STALE_REASON
            recovery = LIFECYCLE_STALE_RECOVERY
            recoverable = True
        else:
            observation = LIFECYCLE_OBSERVATION_FRESH
    return WorkerLifecycleObservation(
        mission_id=lifecycle["mission_id"],
        worker_id=lifecycle["worker_id"],
        queue_item_id=lifecycle["queue_item_id"],
        trace_id=lifecycle["trace_id"],
        state=state,
        sequence=lifecycle["sequence"],
        terminal=terminal,
        observation=observation,
        reason=reason,
        recoverable=recoverable,
        recovery=recovery,
        heartbeat_at=lifecycle["heartbeat_at"],
        fresh_until=lifecycle["fresh_until"],
        revision=slot["revision"],
        event_id=slot["event_id"],
        as_of=as_of,
    )


def observe_worker_lifecycle(
    root: Path,
    as_of: Optional[str] = None,
    mission_id: Optional[str] = None,
    worker_id: Optional[str] = None,
) -> Tuple[WorkerLifecycleObservation, ...]:
    root = _require_absolute_root(str(root), "configured")
    moment = as_of if as_of is not None else utc_timestamp()
    now = _parsed_timestamp(moment, "as_of", "lifecycle status")
    if mission_id is not None:
        mission_id = _require_uuid({"mission_id": mission_id}, "mission_id", "lifecycle status")
    if worker_id is not None:
        worker_id = _require_string({"worker_id": worker_id}, "worker_id", "lifecycle status")

    _metadata, history = inspect_control_events(root)
    slots, _outcomes = fold_worker_missions(history.events)
    observations: List[WorkerLifecycleObservation] = []
    for key in sorted(slots):
        lifecycle = slots[key]["lifecycle"]
        if mission_id is not None and lifecycle["mission_id"] != mission_id:
            continue
        if worker_id is not None and lifecycle["worker_id"] != worker_id:
            continue
        observations.append(_observed_slot(slots[key], now, moment))
    return tuple(observations)
