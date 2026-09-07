"""Mission-control seam for dialogs, transitions, and mission-slot observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

MISSION_CONTROL_SCHEMA_VERSION = 1
MISSION_DIALOG_RECORD_TYPE = "mission-dialog"
MISSION_CANCELLATION_RECORD_TYPE = "mission-cancellation"
MISSION_REPLACEMENT_RECORD_TYPE = "mission-replacement"
MISSION_RECOVERY_RECORD_TYPE = "mission-recovery"
MISSION_DIALOG_PAYLOAD_FIELD = "mission_dialog"
MISSION_CANCELLATION_PAYLOAD_FIELD = "mission_cancellation"
MISSION_REPLACEMENT_PAYLOAD_FIELD = "mission_replacement"
MISSION_RECOVERY_PAYLOAD_FIELD = "mission_recovery"
MISSION_DIALOG_PENDING = "pending"
MISSION_DIALOG_OBSERVATION_ANSWERABLE = "answerable"
MISSION_DIALOG_OBSERVATION_ORPHANED = "orphaned"
MISSION_DIALOG_OBSERVATION_SETTLED = "settled"
MISSION_DIALOG_QUESTION = "question"
MISSION_DIALOG_ACCESS_PROMPT = "access-prompt"
MISSION_DIALOG_PROMPT_KINDS = (MISSION_DIALOG_QUESTION, MISSION_DIALOG_ACCESS_PROMPT)
MISSION_DIALOG_ANSWERS = {
    "reply": MISSION_DIALOG_QUESTION,
    "access-response": MISSION_DIALOG_ACCESS_PROMPT,
}
MISSION_FOLD_RETAINED_DUPLICATE = "retained-duplicate-delivery"
CANCELLATION_AWAITING = "awaiting-acknowledgement"
CANCELLATION_ACKNOWLEDGED = "acknowledged"
CANCELLATION_TIMED_OUT = "timed-out"
CANCELLATION_TIMEOUT_REASON = "deadline-expired-without-acknowledgement"
CANCELLATION_TIMEOUT_RECOVERY = "awaiting-bounded-recovery"
COMMAND_DUPLICATE = "duplicate"
DEFAULT_EVENT_ACTOR = "cockpit-control"
DEFAULT_COMMAND_REGISTER_COMMAND = "cockpit-control register-command"
DEFAULT_LOCK_POLL_SECONDS = 0.05
WORKER_LIFECYCLE_ACTIVE_STATES = ("accepted", "running", "blocked")


class ControlStoreError(RuntimeError):
    """Facade overrides this class to preserve compatibility."""


validate_mission_dialog: Callable[..., Dict[str, Any]]
validate_mission_cancellation: Callable[..., Dict[str, Any]]
validate_mission_replacement: Callable[..., Dict[str, Any]]
validate_mission_recovery: Callable[..., Dict[str, Any]]
inspect_control_events: Callable[..., Any]
fold_mission_state: Callable[..., Any]
register_command: Callable[..., Any]
fold_commands: Callable[..., Any]
_committed_events_after_publication: Callable[..., Any]
_parsed_timestamp: Callable[..., datetime]
_require_absolute_root: Callable[..., Path]
_require_uuid: Callable[..., str]
_require_identifier: Callable[..., str]
utc_timestamp: Callable[[], str]


@dataclass(frozen=True)
class MissionControlResult:
    command: Any
    record_field: str
    record: Dict[str, Any]
    applied: bool
    outcome: str
    state: Any

    @property
    def committed(self) -> bool:
        return self.command.committed

    @property
    def command_id(self) -> str:
        return self.command.command_id

    @property
    def conflicted(self) -> bool:
        return self.outcome == "conflict-recorded"

    @property
    def redelivered(self) -> bool:
        return self.outcome == MISSION_FOLD_RETAINED_DUPLICATE

    @property
    def worker_slot(self) -> Optional[Dict[str, Any]]:
        worker_id = self.record.get("worker_id")
        if worker_id is None:
            return None
        return self.state.worker_slots.get(worker_id)

    @property
    def recorded_conflict(self) -> Optional[Dict[str, Any]]:
        slot = self.worker_slot
        if slot is None:
            return None
        for conflict in slot["conflicts"]:
            if conflict["event_id"] == self.command.publication.event_id:
                return conflict
        return None


@dataclass(frozen=True)
class CancellationObservation:
    command_id: str
    mission_id: str
    worker_id: str
    queue_item_id: str
    trace_id: str
    parent_trace_id: Optional[str]
    requested_at: str
    acknowledge_deadline_at: str
    observation: str
    reason: Optional[str]
    recovery: Optional[str]
    acknowledgement: Optional[str]
    acknowledged_by: Optional[str]
    acknowledged_at: Optional[str]
    command_status: str
    mission_state: Optional[str]
    revision: int
    event_id: str
    as_of: str

    @property
    def timed_out(self) -> bool:
        return self.observation == CANCELLATION_TIMED_OUT


@dataclass(frozen=True)
class MissionControlReport:
    root: Path
    as_of: str
    dialogs: Tuple[Dict[str, Any], ...]
    cancellations: Tuple[CancellationObservation, ...]
    worker_slots: Tuple[Tuple[str, Dict[str, Any]], ...]
    mission_states: Mapping[str, str]

    def dialog_observation(self, entry: Mapping[str, Any]) -> str:
        if entry["state"] != MISSION_DIALOG_PENDING:
            return MISSION_DIALOG_OBSERVATION_SETTLED
        state = self.mission_states.get(entry["dialog"]["mission_id"])
        if state in WORKER_LIFECYCLE_ACTIVE_STATES:
            return MISSION_DIALOG_OBSERVATION_ANSWERABLE
        return MISSION_DIALOG_OBSERVATION_ORPHANED

    @property
    def pending_prompts(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(
            entry
            for entry in self.dialogs
            if self.dialog_observation(entry) == MISSION_DIALOG_OBSERVATION_ANSWERABLE
        )

    @property
    def orphaned_prompts(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(
            entry
            for entry in self.dialogs
            if self.dialog_observation(entry) == MISSION_DIALOG_OBSERVATION_ORPHANED
        )

    @property
    def timed_out(self) -> Tuple[CancellationObservation, ...]:
        return tuple(entry for entry in self.cancellations if entry.timed_out)

    @property
    def conflicts(self) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
        found: List[Tuple[str, Dict[str, Any]]] = []
        for worker_id, slot in self.worker_slots:
            for conflict in slot["conflicts"]:
                found.append((worker_id, conflict))
        return tuple(found)


def build_mission_dialog(
    command_id: str,
    kind: str,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    trace_id: str,
    category: str,
    body_refs: Sequence[str] = (),
    parent_trace_id: Optional[str] = None,
    answers_command_id: Optional[str] = None,
    raised_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": MISSION_CONTROL_SCHEMA_VERSION,
        "record_type": MISSION_DIALOG_RECORD_TYPE,
        "command_id": command_id,
        "kind": kind,
        "mission_id": mission_id,
        "worker_id": worker_id,
        "queue_item_id": queue_item_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "answers_command_id": answers_command_id,
        "category": category,
        "body_refs": list(body_refs),
        "raised_at": raised_at if raised_at is not None else utc_timestamp(),
    }
    return validate_mission_dialog(record)


def build_mission_cancellation(
    command_id: str,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    trace_id: str,
    reason: str,
    acknowledge_deadline_at: str,
    parent_trace_id: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    requested_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": MISSION_CONTROL_SCHEMA_VERSION,
        "record_type": MISSION_CANCELLATION_RECORD_TYPE,
        "command_id": command_id,
        "mission_id": mission_id,
        "worker_id": worker_id,
        "queue_item_id": queue_item_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "requested_at": requested_at if requested_at is not None else utc_timestamp(),
        "acknowledge_deadline_at": acknowledge_deadline_at,
    }
    return validate_mission_cancellation(record)


def build_mission_replacement(
    command_id: str,
    worker_id: str,
    queue_item_id: str,
    replaced_mission_id: str,
    replacement_mission_id: str,
    trace_id: str,
    reason: str,
    parent_trace_id: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    requested_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": MISSION_CONTROL_SCHEMA_VERSION,
        "record_type": MISSION_REPLACEMENT_RECORD_TYPE,
        "command_id": command_id,
        "worker_id": worker_id,
        "queue_item_id": queue_item_id,
        "replaced_mission_id": replaced_mission_id,
        "replacement_mission_id": replacement_mission_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "requested_at": requested_at if requested_at is not None else utc_timestamp(),
    }
    return validate_mission_replacement(record)


def build_mission_recovery(
    command_id: str,
    action: str,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    trace_id: str,
    reason: str,
    respond_deadline_at: str,
    parent_trace_id: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    requested_at: Optional[str] = None,
) -> Dict[str, Any]:
    record = {
        "schema_version": MISSION_CONTROL_SCHEMA_VERSION,
        "record_type": MISSION_RECOVERY_RECORD_TYPE,
        "command_id": command_id,
        "action": action,
        "mission_id": mission_id,
        "worker_id": worker_id,
        "queue_item_id": queue_item_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "reason": reason,
        "evidence_refs": list(evidence_refs),
        "requested_at": requested_at if requested_at is not None else utc_timestamp(),
        "respond_deadline_at": respond_deadline_at,
    }
    return validate_mission_recovery(record)


def read_mission_state(root: Path) -> Any:
    _metadata, history = inspect_control_events(root)
    return fold_mission_state(history.events)


def require_active_mission(
    state: Any,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    label: str,
) -> Dict[str, Any]:
    slot = state.missions.get(mission_id)
    if slot is None:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, which this control root has never materialized"
        )
    lifecycle = slot["lifecycle"]
    if lifecycle["worker_id"] != worker_id or lifecycle["queue_item_id"] != queue_item_id:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, which belongs to a different worker or queue item"
        )
    if lifecycle["state"] not in WORKER_LIFECYCLE_ACTIVE_STATES:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, whose materialized state is "
            f"{lifecycle['state']!r} and is no longer active"
        )
    return dict(lifecycle)


def require_pending_prompt(
    state: Any,
    answers_command_id: str,
    kind: str,
    label: str,
) -> Dict[str, Any]:
    entry = state.dialogs.get(answers_command_id)
    if entry is None:
        raise ControlStoreError(
            f"{label} answers command {answers_command_id}, which this control root has "
            "never raised as a mission prompt"
        )
    prompt = entry["dialog"]
    if prompt["kind"] not in MISSION_DIALOG_PROMPT_KINDS:
        raise ControlStoreError(
            f"{label} answers command {answers_command_id}, which is a "
            f"{prompt['kind']!r} record rather than a prompt"
        )
    if prompt["kind"] != MISSION_DIALOG_ANSWERS[kind]:
        raise ControlStoreError(
            f"{label} of kind {kind!r} may only answer a {MISSION_DIALOG_ANSWERS[kind]!r}; "
            f"command {answers_command_id} is a {prompt['kind']!r}"
        )
    if entry["state"] != MISSION_DIALOG_PENDING:
        raise ControlStoreError(
            f"{label} answers command {answers_command_id}, which is already "
            f"{entry['state']} and cannot be answered again"
        )
    return dict(prompt)


def register_mission_command(
    root: Path,
    envelope: Mapping[str, Any],
    record_field: str,
    record: Mapping[str, Any],
    actor: str = DEFAULT_EVENT_ACTOR,
    command: str = DEFAULT_COMMAND_REGISTER_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
    declarations: Optional[Mapping[str, Any]] = None,
) -> MissionControlResult:
    result = register_command(
        root,
        envelope,
        actor=actor,
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
        correlated_record={record_field: dict(record)},
        declarations=declarations,
    )
    state = fold_mission_state(_committed_events_after_publication(result.publication))
    applied, outcome = state.mission_outcomes.get(
        result.publication.event_id, (False, MISSION_FOLD_RETAINED_DUPLICATE)
    )
    return MissionControlResult(
        command=result,
        record_field=record_field,
        record=dict(record),
        applied=applied,
        outcome=outcome,
        state=state,
    )


def _cancellation_acknowledgement(
    slot: Optional[Mapping[str, Any]],
    digest: str,
) -> Optional[Dict[str, Any]]:
    if slot is None:
        return None
    answered: Optional[Dict[str, Any]] = None
    for entry in slot["acknowledgements"]:
        acknowledgement = entry["acknowledgement"]
        if acknowledgement["payload_digest"] != digest:
            continue
        if acknowledgement["outcome"] == COMMAND_DUPLICATE:
            continue
        answered = dict(acknowledgement)
    return answered


def observe_cancellation(
    entry: Mapping[str, Any],
    command_slot: Optional[Mapping[str, Any]],
    mission_slot: Optional[Mapping[str, Any]],
    now: datetime,
    as_of: str,
) -> CancellationObservation:
    cancellation = entry["cancellation"]
    digest = "" if command_slot is None else command_slot["envelope"]["payload_digest"]
    acknowledgement = _cancellation_acknowledgement(command_slot, digest)
    observation = CANCELLATION_AWAITING
    reason: Optional[str] = None
    recovery: Optional[str] = None
    if acknowledgement is not None:
        observation = CANCELLATION_ACKNOWLEDGED
    else:
        deadline = _parsed_timestamp(
            cancellation["acknowledge_deadline_at"],
            "acknowledge_deadline_at",
            "mission cancellation",
        )
        if now > deadline:
            observation = CANCELLATION_TIMED_OUT
            reason = CANCELLATION_TIMEOUT_REASON
            recovery = CANCELLATION_TIMEOUT_RECOVERY
    return CancellationObservation(
        command_id=cancellation["command_id"],
        mission_id=cancellation["mission_id"],
        worker_id=cancellation["worker_id"],
        queue_item_id=cancellation["queue_item_id"],
        trace_id=cancellation["trace_id"],
        parent_trace_id=cancellation["parent_trace_id"],
        requested_at=cancellation["requested_at"],
        acknowledge_deadline_at=cancellation["acknowledge_deadline_at"],
        observation=observation,
        reason=reason,
        recovery=recovery,
        acknowledgement=None if acknowledgement is None else acknowledgement["outcome"],
        acknowledged_by=None if acknowledgement is None else acknowledgement["acknowledged_by"],
        acknowledged_at=None if acknowledgement is None else acknowledgement["acknowledged_at"],
        command_status="-" if command_slot is None else command_slot["status"],
        mission_state=None if mission_slot is None else mission_slot["lifecycle"]["state"],
        revision=entry["revision"],
        event_id=entry["event_id"],
        as_of=as_of,
    )


def observe_mission_control(
    root: Path,
    as_of: Optional[str] = None,
    mission_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    command_id: Optional[str] = None,
) -> MissionControlReport:
    root = _require_absolute_root(str(root), "configured")
    moment = as_of if as_of is not None else utc_timestamp()
    now = _parsed_timestamp(moment, "as_of", "mission status")
    if mission_id is not None:
        mission_id = _require_uuid({"mission_id": mission_id}, "mission_id", "mission status")
    if command_id is not None:
        command_id = _require_uuid({"command_id": command_id}, "command_id", "mission status")
    if worker_id is not None:
        worker_id = _require_identifier({"worker": worker_id}, "worker", "mission status")

    _metadata, history = inspect_control_events(root)
    state = fold_mission_state(history.events)
    command_slots, _outcomes = fold_commands(history.events)

    dialogs: List[Dict[str, Any]] = []
    for key in sorted(state.dialogs):
        entry = state.dialogs[key]
        dialog = entry["dialog"]
        if mission_id is not None and dialog["mission_id"] != mission_id:
            continue
        if worker_id is not None and dialog["worker_id"] != worker_id:
            continue
        if command_id is not None and dialog["command_id"] != command_id:
            continue
        dialogs.append(entry)

    cancellations: List[CancellationObservation] = []
    for key in sorted(state.cancellations):
        entry = state.cancellations[key]
        cancellation = entry["cancellation"]
        if mission_id is not None and cancellation["mission_id"] != mission_id:
            continue
        if worker_id is not None and cancellation["worker_id"] != worker_id:
            continue
        if command_id is not None and cancellation["command_id"] != command_id:
            continue
        cancellations.append(
            observe_cancellation(
                entry,
                command_slots.get(key),
                state.missions.get(cancellation["mission_id"]),
                now,
                moment,
            )
        )

    slots: List[Tuple[str, Dict[str, Any]]] = []
    for key in sorted(state.worker_slots):
        slot = state.worker_slots[key]
        if worker_id is not None and key != worker_id:
            continue
        if mission_id is not None and slot["mission_id"] != mission_id:
            continue
        if command_id is not None and slot["command_id"] != command_id:
            continue
        slots.append((key, slot))

    return MissionControlReport(
        root=root,
        as_of=moment,
        dialogs=tuple(dialogs),
        cancellations=tuple(cancellations),
        worker_slots=tuple(slots),
        mission_states={key: entry["lifecycle"]["state"] for key, entry in state.missions.items()},
    )
