"""Immutable journal seam for committed-event reading and publication.

This runtime module owns committed event naming, committed sequence reading,
pending debris observation, and immutable event publication boundaries.
The cockpit_control facade binds this seam's dependencies so public symbols
and error semantics stay compatible while behavior moves behind this module.
"""

from __future__ import annotations

import os
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from uuid import UUID, uuid4

import cockpit_control_root_schema as control_root_schema

CONTROL_SCHEMA_VERSION = control_root_schema.CONTROL_SCHEMA_VERSION
CONTROL_METADATA_NAME = control_root_schema.CONTROL_METADATA_NAME

EVENTS_DIR_NAME = "events"
PENDING_DIR_NAME = "pending"
EVENT_FILENAME_SUFFIX = ".json"
EVENT_REVISION_DIGITS = 12
DEFAULT_EVENT_ACTOR = "cockpit-control"
DEFAULT_EVENT_COMMAND = "cockpit-control publish-event"
DEFAULT_LOCK_POLL_SECONDS = 0.05

EVENT_COMMITTED = "committed"
EVENT_WOULD_COMMIT = "would-commit"


class ControlStoreError(RuntimeError):
    """Facade overrides this class to keep compatibility of raised errors."""


# Facade-bound dependencies.
_require_absolute_root: Callable[..., Path]
_require_directory: Callable[..., None]
_require_regular_file: Callable[..., None]
_load_json: Callable[..., Dict[str, Any]]
_serialized_record: Callable[[Mapping[str, Any]], str]
_write_json: Callable[[Path, Mapping[str, Any]], None]
_fsync_directory: Callable[[Path], None]
_same_filesystem_identity: Callable[[os.stat_result, os.stat_result], bool]
_configured_lock_timeout: Callable[[Optional[float]], float]
_validated_seconds: Callable[..., float]
_require_string: Callable[..., str]
validate_root_metadata: Callable[..., Dict[str, Any]]
validate_event: Callable[..., Dict[str, Any]]
validate_mission_command_publication: Callable[..., None]
build_ledger_projection: Callable[..., Dict[str, Any]]
ControlLedgerProjection: Any
PortableControlLock: Any
utc_timestamp: Callable[[], str]


def _event_publication_fault(boundary: str, publication: "ControlEventPublication") -> None:
    """No-op named publication hook used by deterministic protocol tests."""

    del boundary, publication


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
    """Read the exact revision and event UUID a committed filename declares."""

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
    """Read the committed event sequence, failing closed on any ambiguity."""

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

    committed = []
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
    projection: Optional[Any] = None

    @property
    def committed(self) -> bool:
        return self.outcome == EVENT_COMMITTED


class ControlEventPublication:
    """Publish one immutable event whose only commit step is an atomic rename."""

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
        held_lock: Optional[Any] = None,
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
        self.held_lock = held_lock
        self.lock: Optional[Any] = None
        self.record: Optional[Dict[str, Any]] = None
        self.revision: Optional[int] = None
        self.candidate_path: Optional[Path] = None
        self.committed_path: Optional[Path] = None
        self.pending_debris: Tuple[Path, ...] = ()
        self.projection: Optional[Any] = None

    @staticmethod
    def _validated_payload(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
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
        _require_directory(self.root, "COCKPIT_CONTROL_ROOT")
        _require_directory(self.events_path, EVENTS_DIR_NAME)
        _require_directory(self.pending_path, PENDING_DIR_NAME)

        if self.held_lock is not None and (
            self.held_lock.owner is None or self.held_lock.root != self.root
        ):
            raise ControlStoreError("event publication requires the held control lock for this root")
        with (nullcontext(self.held_lock) if self.held_lock is not None else PortableControlLock(
            self.root,
            self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        )) as lock:
            self.lock = lock
            metadata, history = inspect_control_events(self.root)
            control_id = metadata["control_id"]
            self.pending_debris = history.pending
            revision = history.latest_revision + 1
            record = self._build_record(control_id, revision)
            validate_mission_command_publication(record, history)
            filename = _event_filename(revision, record["event_id"])
            candidate_path = self.pending_path / filename
            committed_path = self.events_path / filename
            self.revision = revision
            self.record = record
            self.candidate_path = candidate_path
            self.committed_path = committed_path
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
            _event_publication_fault("candidate-written", self)
            candidate_identity = self._validate_private_candidate(
                control_id, candidate_path, record, revision
            )
            _event_publication_fault("candidate-validated", self)
            self._commit_by_rename(candidate_path, committed_path, candidate_identity)
            self._confirm_committed_tip(control_id, committed_path, record, revision)
            _event_publication_fault("event-committed", self)
            self.projection = self._project_committed_events(lock, committed_path, revision)
            return self._result(EVENT_COMMITTED, record, revision, committed_path)

    def _project_committed_events(
        self,
        lock: Any,
        committed_path: Path,
        revision: int,
    ) -> Any:
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
    held_lock: Optional[Any] = None,
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
        held_lock=held_lock,
    ).run()
