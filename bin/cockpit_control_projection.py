"""Projection and replay seam for derived ledger and compatibility view.

This runtime module owns deterministic projection from authoritative committed
events into derived state (`ledger.json` and `events.jsonl`) plus replay/repair
classification. The cockpit_control facade binds this seam's dependencies so
public symbols and command behavior stay compatible while behavior moves behind
this module.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

import cockpit_control_root_schema as control_root_schema

CONTROL_SCHEMA_VERSION = control_root_schema.CONTROL_SCHEMA_VERSION
CONTROL_METADATA_NAME = control_root_schema.CONTROL_METADATA_NAME
LEDGER_NAME = control_root_schema.LEDGER_NAME
EVENTS_NAME = control_root_schema.EVENTS_NAME

# Facade-bound constants.
LEDGER_TEMPORARY_NAME = f"{LEDGER_NAME}.tmp"
EVENTS_VIEW_TEMPORARY_NAME = f"{EVENTS_NAME}.tmp"
EVENTS_DIR_NAME = "events"
DEFAULT_LEDGER_COMMAND = "cockpit-control replay-ledger"
DEFAULT_LOCK_POLL_SECONDS = 0.05
PROJECTION_CURRENT = "current"
PROJECTION_REBUILT = "rebuilt"
PROJECTION_WOULD_REBUILD = "would-rebuild"
PROJECTION_REASON_CURRENT = "current"
PROJECTION_REASON_MISSING = "missing"
PROJECTION_REASON_CORRUPT = "corrupt"
PROJECTION_REASON_AHEAD = "ahead"
PROJECTION_REASON_STALE = "stale"
PROJECTION_REASON_DIVERGENT = "divergent"
PROJECTION_REASON_VIEW = "derived-view"
LEDGER_PROJECTION_FIELDS = ("active_queue_item_id", "active_mission_id")
LEDGER_WORKER_MISSIONS_FIELD = "worker_missions"
LEDGER_COMMANDS_FIELD = "commands"
LEDGER_MISSION_DIALOGS_FIELD = "mission_dialogs"
LEDGER_MISSION_CANCELLATIONS_FIELD = "mission_cancellations"
LEDGER_MISSION_RECOVERIES_FIELD = "mission_recoveries"
LEDGER_MISSION_SLOTS_FIELD = "worker_slots"
LEDGER_CONTROLLER_FIELD = "controller"


class ControlStoreError(RuntimeError):
    """Facade overrides this class to keep compatibility of raised errors."""


# Facade-bound dependencies.
_require_absolute_root: Callable[..., Path]
_require_directory: Callable[..., None]
_configured_lock_timeout: Callable[[Optional[float]], float]
_validated_seconds: Callable[..., float]
_same_filesystem_identity: Callable[[os.stat_result, os.stat_result], bool]
_fsync_directory: Callable[[Path], None]
_serialized_record: Callable[[Mapping[str, Any]], str]
_require_uuid: Callable[..., str]
_require_timestamp: Callable[..., str]
_validate_canonical_roots: Callable[..., Dict[str, Any]]
validate_ledger: Callable[..., Dict[str, Any]]
_event_ledger_declarations: Callable[..., Dict[str, Any]]
_derived_ledger_id: Callable[[str], str]
fold_mission_state: Callable[..., Any]
fold_commands: Callable[..., Any]
inspect_control_events: Callable[..., Any]
PortableControlLock: Any


def build_ledger_projection(
    metadata: Mapping[str, Any],
    events: Sequence[Any] = (),
) -> Dict[str, Any]:
    """Fold the committed event sequence into the one derived ledger record."""

    control_id = _require_uuid(metadata, "control_id", CONTROL_METADATA_NAME)
    created_at = _require_timestamp(metadata, "created_at", CONTROL_METADATA_NAME)
    roots = _validate_canonical_roots(metadata.get("canonical_roots"), CONTROL_METADATA_NAME)

    revision = 0
    updated_at = created_at
    state: Dict[str, Any] = {field: None for field in LEDGER_PROJECTION_FIELDS}
    mission_state = fold_mission_state(events)
    commands, _command_outcomes = fold_commands(events)
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
        LEDGER_WORKER_MISSIONS_FIELD: mission_state.missions,
        LEDGER_COMMANDS_FIELD: commands,
        LEDGER_MISSION_DIALOGS_FIELD: mission_state.dialogs,
        LEDGER_MISSION_CANCELLATIONS_FIELD: mission_state.cancellations,
        LEDGER_MISSION_RECOVERIES_FIELD: mission_state.recoveries,
        LEDGER_MISSION_SLOTS_FIELD: mission_state.worker_slots,
        LEDGER_CONTROLLER_FIELD: dict(mission_state.controller) or None,
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


def build_events_view(events: Sequence[Any] = ()) -> str:
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
    """Write, flush, and verify the exact bytes of one projection temporary."""

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


def _classify_published_ledger(
    root: Path,
    control_id: str,
    expected: bytes,
) -> Tuple[Optional[int], str]:
    """Say why the published projection does or does not equal the rebuild."""

    ledger_path = root / LEDGER_NAME
    try:
        mode = ledger_path.lstat().st_mode
    except FileNotFoundError:
        return None, PROJECTION_REASON_MISSING
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {LEDGER_NAME}: {exc}") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return None, PROJECTION_REASON_CORRUPT
    try:
        published = ledger_path.read_bytes()
    except OSError:
        return None, PROJECTION_REASON_CORRUPT
    if published == expected:
        return json.loads(expected.decode("utf-8"))["revision"], PROJECTION_REASON_CURRENT
    try:
        existing = validate_ledger(json.loads(published.decode("utf-8")), control_id, root)
    except (ControlStoreError, UnicodeDecodeError, ValueError, RecursionError):
        return None, PROJECTION_REASON_CORRUPT
    observed = existing["revision"]
    rebuilt_revision = json.loads(expected.decode("utf-8"))["revision"]
    if observed > rebuilt_revision:
        return observed, PROJECTION_REASON_AHEAD
    if observed < rebuilt_revision:
        return observed, PROJECTION_REASON_STALE
    return observed, PROJECTION_REASON_DIVERGENT


def _published_view_matches(root: Path, expected: str) -> bool:
    """Report whether the derived compatibility view equals the rebuild."""

    view_path = root / EVENTS_NAME
    try:
        mode = view_path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {EVENTS_NAME}: {exc}") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return False
    try:
        return view_path.read_bytes() == expected.encode("utf-8")
    except OSError:
        return False


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
    """Rebuild the derived ledger from committed events and replace it atomically."""

    def __init__(
        self,
        root: Path,
        command: str = DEFAULT_LEDGER_COMMAND,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
        held_lock: Optional[Any] = None,
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
        self.lock: Optional[Any] = None
        self.record: Optional[Dict[str, Any]] = None
        self.revision: Optional[int] = None
        self.observed_revision: Optional[int] = None
        self.reason: Optional[str] = None
        self.interrupted_temporaries: Tuple[Path, ...] = ()
        self.pending_debris: Tuple[Path, ...] = ()

    def _interrupted_temporaries(self) -> Tuple[Path, ...]:
        found = []
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
        return _classify_published_ledger(self.root, control_id, expected)

    def _view_matches(self, expected: str) -> bool:
        return _published_view_matches(self.root, expected)

    def _result(self, outcome: str) -> LedgerProjectionResult:
        return LedgerProjectionResult(
            root=self.root,
            outcome=outcome,
            reason=self.reason or PROJECTION_REASON_CURRENT,
            revision=int(self.revision or 0),
            observed_revision=self.observed_revision,
            path=self.ledger_path,
            record=dict(self.record or {}),
            interrupted_temporaries=self.interrupted_temporaries,
            pending_debris=self.pending_debris,
        )

    def _project(self) -> LedgerProjectionResult:
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

        view_identity = _write_projection_temporary(
            self.view_temporary_path, view_text, EVENTS_VIEW_TEMPORARY_NAME
        )
        _replace_projection(self.view_temporary_path, self.view_path, view_identity, EVENTS_NAME)
        _fsync_directory(self.root)
        _ledger_projection_fault("view-replaced", self)

        ledger_identity = _write_projection_temporary(
            self.ledger_temporary_path, ledger_text, LEDGER_TEMPORARY_NAME
        )
        _ledger_projection_fault("ledger-temporary-written", self)
        _replace_projection(self.ledger_temporary_path, self.ledger_path, ledger_identity, LEDGER_NAME)
        _fsync_directory(self.root)
        self.interrupted_temporaries = self._interrupted_temporaries()
        _ledger_projection_fault("ledger-replaced", self)
        return self._result(PROJECTION_REBUILT)

    def run(self) -> LedgerProjectionResult:
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
