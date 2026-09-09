"""Controller seam for evidence, reconciliation, dispatch, and tick reporting."""

from __future__ import annotations

import json
import hashlib
import os
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID, uuid5
from cockpit_footprint import footprints_conflict, queue_lock, require_footprint_authority, validate_footprint

# Facade overrides these placeholders with authoritative values.
DEFAULT_CONTROLLER_TICK_COMMAND = "cockpit-overseer tick"
DEFAULT_LOCK_POLL_SECONDS = 0.05
CONTROLLER_LEDGER_CURRENT = "current"
CONTROLLER_TICK_ACTOR = "cockpit-overseer"
DISPATCH_ACCEPTANCE_SECONDS = 300


class ControlStoreError(RuntimeError):
    """Facade overrides this class to preserve compatibility of raised errors."""


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


@dataclass(frozen=True)
class ControllerDiagnostic:
    """One worker's ADR-014 rule 7 and rule 8 observations, diagnostic-only.

    Live protocol reachability and pane text are reported so an operator can see
    what the controller saw, and are deliberately absent from
    `ControllerEvidence`: `select_controller_action` has no parameter that could
    carry them, so no pane can advance authoritative state by construction
    rather than by anyone remembering the rule.
    """

    worker_id: str
    live_status: str
    pane_status: str

    @staticmethod
    def build(worker_id: str, live_status: str, pane_status: str) -> "ControllerDiagnostic":
        """Validate one diagnostic before it is rendered into a payload line."""

        label = "controller diagnostic"
        return ControllerDiagnostic(
            worker_id=_require_identifier({"worker_id": worker_id}, "worker_id", label),
            live_status=_require_identifier({"live_status": live_status}, "live_status", label),
            pane_status=_require_identifier({"pane_status": pane_status}, "pane_status", label),
        )


@dataclass(frozen=True)
class ControllerEvidence:
    """Exactly the ADR-014 rule 1-6 sources, and deliberately nothing else.

    Rules 7 and 8 -- live protocol observation and raw pane text -- have no
    field here.  That absence is the enforcement: the only function allowed to
    choose an action takes this record and nothing else, so pane text cannot
    reach a decision even if a future caller wanted it to.
    """

    control_root: str
    control_id: str
    journal_revision: int
    ledger_revision: Optional[int]
    ledger_reason: str
    declared_queue_root: Optional[str]
    exported_queue_root: Optional[str]
    queue_root_fault: Optional[str]
    queue: Optional[QueueObservation]
    state: MissionState
    dispatched_pairs: Tuple[Tuple[str, str], ...]
    correlated_records: int
    uncorrelated_records: int
    as_of: str
    # Freshness of every materialized mission at `as_of`, derived from committed
    # lifecycle events alone.  `stale` is an observation and never a state: it
    # is what makes a bounded recovery action *owed*, and nothing here can fold
    # a silent worker into `failed`.
    observations: Tuple[WorkerLifecycleObservation, ...]
    # Every command ID this control root has committed.  A recovery rung asks
    # whether its own derived identifier is among them, which is how "already
    # attempted for this staleness episode" is answered from committed authority
    # rather than from a counter anybody has to maintain.
    command_ids: Tuple[str, ...]
    ledger_repair: str
    repaired_from: Optional[str]
    # If a lifecycle blocker indicates an undeclared architectural boundary
    # crossing (for example an undeclared deployment image or repository),
    # this field carries a small structured description so the tick may
    # publish an explicit architecture-boundary event when it records the
    # blocked observation.  It is `None` in the normal case.
    boundary_blocker: Optional[Dict[str, Any]]
    commands: Tuple[Dict[str, Any], ...] = ()
    dispatch_phases: Optional[Mapping[str, str]] = None

    def __post_init__(self) -> None:
        """Refuse a derived-state classification outside the closed vocabulary."""

        if self.ledger_repair not in CONTROLLER_LEDGER_OUTCOMES:
            raise ControlStoreError(
                f"unknown derived-state classification {self.ledger_repair!r}; expected "
                f"one of {', '.join(CONTROLLER_LEDGER_OUTCOMES)}"
            )

    @property
    def ledger_undecidable(self) -> bool:
        """True for derived state no tick may go on to decide from.

        A projection that is missing, behind, unreadable, or disagreeing is
        derived state the committed events reproduce exactly, and the tick has
        already rebuilt it before this record was assembled.  Anything else is
        refused earlier, before one piece of evidence exists, so reaching this
        property at all means a caller assembled evidence some other way -- and
        the decision refuses to dispatch from it rather than trusting the shape
        of the record it was handed.
        """

        return self.ledger_repair not in CONTROLLER_LEDGER_DECIDABLE

    @property
    def controller(self) -> Optional[Dict[str, Any]]:
        """Return the one decision a previous tick recorded, if there was one."""

        return dict(self.state.controller) or None

    def dispatch_attempt(self, queue_item_id: str, worker_id: str) -> int:
        """Return which dispatch of this queue item to this worker comes next."""

        return 1 + sum(1 for pair in self.dispatched_pairs if pair == (queue_item_id, worker_id))

    def observation(self, mission_id: str) -> Optional[WorkerLifecycleObservation]:
        """Return the freshness observation of one materialized mission."""

        for observed in self.observations:
            if observed.mission_id == mission_id:
                return observed
        return None

    @property
    def stale_missions(self) -> Tuple[WorkerLifecycleObservation, ...]:
        """Return every materialized mission whose declared freshness expired."""

        return tuple(observed for observed in self.observations if observed.stale)

    def active_item(self, queue_item_id: str) -> Optional[QueueItemObservation]:
        """Return the queue item only while product work still owns it."""

        if self.queue is None:
            return None
        for item in self.queue.active:
            if item.item_id == queue_item_id:
                return item
        return None

    def episode_revision(self, mission_id: str) -> int:
        """Return the committed revision that keys one mission's recovery episode.

        Every recovery identifier is derived from this number, so the episode it
        keys is exactly the run of ticks a rung may be attempted once in.  The
        key comes from the deterministic fold rather than from "the newest
        lifecycle evidence", because those are not the same thing: evidence that
        arrives while the controller is still finding the mission stale did not
        end the staleness, and letting it re-key the episode would move every
        rung onto identifiers no rung has used and restart the walk at `nudge`
        on every wake -- a worker reporting every ten minutes with a two-minute
        freshness window would then never be troubleshot, cancelled, replaced,
        or escalated to a human.  The episode ends when the *controller* records
        that it looked and the mission was fresh; the next staleness is then a
        new episode keyed on the evidence that ended this one.
        """

        episode = self.state.recovery_episodes.get(mission_id)
        if episode is None:
            return self.state.missions[mission_id]["revision"]
        return episode[0]

    def recovery_command_id(self, mission_id: str, rung: str, episode: int) -> str:
        """Return the one command ID this rung of this episode may ever use."""

        return _controller_derived_uuid(
            self.control_id,
            CONTROLLER_RECOVERY_COMMAND_DERIVATION,
            mission_id,
            rung,
            str(episode),
        )

    def recovery_trace_id(self, mission_id: str, rung: str, episode: int) -> str:
        """Return the child trace ADR-016 gives one focused recovery dialog."""

        return _controller_derived_uuid(
            self.control_id,
            CONTROLLER_RECOVERY_TRACE_DERIVATION,
            mission_id,
            rung,
            str(episode),
        )

    def recovery_mission_id(self, mission_id: str, episode: int) -> str:
        """Return the new mission ID a replacement rung of this episode creates."""

        return _controller_derived_uuid(
            self.control_id,
            CONTROLLER_RECOVERY_MISSION_DERIVATION,
            mission_id,
            str(episode),
        )

    def recovery_deadline(self, command_id: str) -> Optional[str]:
        """Return the moment one delivered rung declared a response is due by."""

        entry = self.state.recoveries.get(command_id)
        if entry is not None:
            return entry["recovery"]["respond_deadline_at"]
        entry = self.state.cancellations.get(command_id)
        if entry is not None:
            return entry["cancellation"]["acknowledge_deadline_at"]
        return None


@dataclass(frozen=True)
class ControllerAction:
    """The at most one action a tick selected, and the evidence key behind it.

    There is one action, never a list.  A tick that wants to do two things
    cannot express that here, which is why "at most one state-changing action"
    survives every later edit to the decision ladder.
    """

    kind: str
    outcome: str
    reason: str
    state_key: str
    queue_item_id: Optional[str] = None
    worker_id: Optional[str] = None
    mission_id: Optional[str] = None
    command_id: Optional[str] = None
    trace_id: Optional[str] = None
    payload_digest: Optional[str] = None
    evidence_refs: Tuple[str, ...] = ()
    parent_trace_id: Optional[str] = None
    recovery_rung: Optional[str] = None
    deadline_at: Optional[str] = None
    replacement_mission_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Refuse an action outside the closed decision vocabulary.

        The kind, the outcome, and the reason are all rendered into parseable
        positions of the tick payload, so a future branch that invented one
        would put free text where a skill or a test reads a closed token.  This
        is the choke point every decision passes through, so it is refused here
        rather than in each of the places that build one.
        """

        if self.kind not in CONTROLLER_ACTIONS:
            raise ControlStoreError(
                f"a controller tick cannot take unknown action {self.kind!r}; expected one "
                f"of {', '.join(CONTROLLER_ACTIONS)}"
            )
        if self.reason not in CONTROLLER_ACTION_REASONS:
            raise ControlStoreError(
                f"a controller tick cannot decide for unknown reason {self.reason!r}"
            )

    @property
    def blocking(self) -> bool:
        return self.reason in CONTROLLER_BLOCKING_REASONS


def controller_state_key(fields: Mapping[str, Any]) -> str:
    """Digest the exact reconciled evidence one decision was taken on.

    The digest, not the evidence, is what a committed event carries.  That keeps
    configured roots, environment values, and pane content out of an immutable
    record that is never rewritten, while still letting the next tick tell "the
    same situation" from "a new one" byte-exactly.
    """

    return command_payload_digest(dict(fields), "controller state")


def controller_ledger_plan(reason: str) -> str:
    """Say what one tick must do about a derived-projection classification.

    Every classification `_classify_published_ledger` and the compatibility-view
    comparison can produce is read here exactly once, and an unrecognised one is
    a refusal rather than a default.  A future classification that nobody
    consciously placed on one side of this partition therefore stops a tick
    instead of being silently treated as healthy or silently rebuilt.
    """

    if reason == PROJECTION_REASON_CURRENT:
        return CONTROLLER_LEDGER_CURRENT
    if reason in CONTROLLER_PROJECTION_REPAIRABLE_REASONS:
        return CONTROLLER_LEDGER_REPAIRED
    if reason in CONTROLLER_PROJECTION_DIVERGENT_REASONS:
        return CONTROLLER_LEDGER_REFUSED
    raise ControlStoreError(
        f"{LEDGER_NAME} classification {reason!r} is not classified as repairable derived "
        "state or as refused authority loss; a controller tick will not guess which"
    )


def _controller_derived_uuid(control_id: str, derivation: str, *parts: str) -> str:
    """Derive one stable identifier from the control root and explicit parts."""

    try:
        namespace = UUID(control_id)
    except (ValueError, AttributeError):
        raise ControlStoreError(f"{CONTROL_METADATA_NAME} requires UUID control_id") from None
    return str(uuid5(namespace, "/".join((derivation,) + tuple(parts))))


def _controller_observation_action(
    evidence: ControllerEvidence,
    reason: str,
    fields: Mapping[str, Any],
    queue_item_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    command_id: Optional[str] = None,
    evidence_refs: Tuple[str, ...] = (),
) -> ControllerAction:
    """Build the observation a tick records, or nothing at all if it is old news.

    Recording an observation is how a no-action tick terminates with durable
    state instead of silence.  Recording it *again* would be exactly the
    repeated investigation AC3 forbids, so an observation whose state key the
    controller already holds collapses to `none` here, in the deterministic
    path, and is refused a second time by the fold as well.
    """

    outcome = CONTROLLER_BLOCKED if reason in CONTROLLER_BLOCKING_REASONS else CONTROLLER_OBSERVED
    # Include whether a bounded recovery episode is open for the mission the
    # observation is about in the controller state key.  The observation key
    # must change when an episode is open so that a genuine fresh lifecycle
    # event can close that episode even if some of the raw freshness fields
    # (for example `fresh_until`) happen to repeat a previously-seen value.
    # This encodes episode-open as part of the reconciled evidence digest but
    # only when a mission_id is present so other observation kinds are
    # unaffected.
    state_key = controller_state_key(dict(fields, reason=reason))
    recorded = evidence.controller
    kind = CONTROLLER_ACTION_OBSERVE
    # Normally an observation whose state key the controller already holds
    # collapses to `none` to avoid repeated investigation.  However, if a
    # bounded recovery episode for this mission is currently open, a genuine
    # fresh lifecycle event must close that episode even when some of the
    # observed fields happen to repeat a previous value.  In that case record
    # the observation so the episode-reset semantics run.  The presence of a
    # mission_id in the fields is used to detect this special-case only.
    if recorded is not None and recorded["state_key"] == state_key:
        mission_id = dict(fields).get("mission_id")
        # A duplicated observation usually collapses to `none`.  The one
        # exception is mission-in-progress while a bounded recovery episode is
        # still open: that repeated key must still be recordable so a genuinely
        # fresh lifecycle event can close the open episode deterministically.
        episode_open = False
        if mission_id is not None and reason == CONTROLLER_REASON_MISSION_IN_PROGRESS:
            try:
                ep = evidence.state.recovery_episodes.get(mission_id)
            except Exception:
                ep = None
            episode_open = bool(ep and ep[1])
        if not episode_open:
            kind = CONTROLLER_ACTION_NONE

    return ControllerAction(
        kind=kind,
        outcome=outcome,
        reason=reason,
        state_key=state_key,
        queue_item_id=queue_item_id,
        worker_id=worker_id,
        mission_id=mission_id,
        command_id=command_id,
        evidence_refs=evidence_refs,
    )


@dataclass(frozen=True)
class ControllerRecoveryStep:
    """The one rung of the bounded ladder this episode has reached."""

    rung: str
    command_id: Optional[str]
    deadline_at: Optional[str]


def controller_recovery_ladder(
    evidence: ControllerEvidence,
    observation: WorkerLifecycleObservation,
) -> Tuple[str, ...]:
    """Return the fixed ladder that applies to one stale or orphaned mission.

    The ladder is chosen from product-work authority, never from what would be
    convenient: a mission whose queue item left the active set has nothing left
    to be nudged, troubleshot, or redone, so it is asked to cancel and is
    escalated if it will not -- and the queue item is never reopened.  A mission
    whose queue item is active but is no longer implementable by this worker
    loses only the replacement rung, because minting a replacement mission there
    would put work on a worker `cockpit-queue` no longer names for it.
    """

    item = evidence.active_item(observation.queue_item_id)
    if item is None:
        return CONTROLLER_TERMINAL_ITEM_LADDER
    if item.worker_id != observation.worker_id:
        return CONTROLLER_UNREPLACEABLE_LADDER
    return CONTROLLER_RECOVERY_LADDER


def select_recovery_step(
    evidence: ControllerEvidence,
    observation: WorkerLifecycleObservation,
    ladder: Sequence[str],
) -> ControllerRecoveryStep:
    """Return the next rung of one staleness episode, and never a later one.

    The walk is finite by construction.  Each command rung owns exactly one
    derived command ID per episode, so a rung already committed is never
    delivered twice, and the walk can only ever run off the end of a fixed
    ladder into `escalate`, which delivers nothing.  It is finite in wall-clock
    time as well, because the episode a rung is keyed to ends only when the
    controller records that it looked and the mission was fresh: lifecycle
    evidence that is already expired when it arrives cannot restart the walk.  A rung that has been
    delivered but whose declared response window has not elapsed at this
    explicit moment holds the ladder still rather than advancing it, so a worker
    is never cancelled for not answering inside a window it was never given.
    """

    episode = evidence.episode_revision(observation.mission_id)
    now = _parsed_timestamp(evidence.as_of, "as_of", "controller tick")
    for rung in ladder:
        if rung == CONTROLLER_RECOVERY_ESCALATE:
            break
        command_id = evidence.recovery_command_id(observation.mission_id, rung, episode)
        if command_id not in evidence.command_ids:
            return ControllerRecoveryStep(rung, command_id, None)
        deadline = evidence.recovery_deadline(command_id)
        if deadline is None:
            # A replacement declares no response window: it either created the
            # mission that ends this episode or it was refused and recorded as
            # a durable conflict, and neither is waited on.
            continue
        if now <= _parsed_timestamp(deadline, "respond_deadline_at", "controller tick"):
            return ControllerRecoveryStep(CONTROLLER_RECOVERY_AWAIT, command_id, deadline)
    return ControllerRecoveryStep(CONTROLLER_RECOVERY_ESCALATE, None, None)


def _recovery_action(
    evidence: ControllerEvidence,
    worker_id: str,
    slot: Mapping[str, Any],
    observation: WorkerLifecycleObservation,
) -> ControllerAction:
    """Build the one bounded recovery action this mission's episode has reached."""

    ladder = controller_recovery_ladder(evidence, observation)
    terminal_item = ladder is CONTROLLER_TERMINAL_ITEM_LADDER
    step = select_recovery_step(evidence, observation, ladder)
    episode = evidence.episode_revision(observation.mission_id)
    references = (
        f"queue:{observation.queue_item_id}",
        f"mission:{observation.mission_id}",
        f"trace:{observation.trace_id}",
    )
    fields = {
        "worker_id": worker_id,
        "mission_id": observation.mission_id,
        "queue_item_id": observation.queue_item_id,
        "mission_state": observation.state,
        "observation": observation.observation,
        "episode_revision": episode,
        "ladder": list(ladder),
        "rung": step.rung,
    }

    if step.rung == CONTROLLER_RECOVERY_AWAIT:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_RECOVERY_AWAITING,
            dict(fields, command_id=step.command_id, deadline_at=step.deadline_at),
            queue_item_id=observation.queue_item_id,
            worker_id=worker_id,
            mission_id=observation.mission_id,
            command_id=step.command_id,
            evidence_refs=references,
        )
    if step.rung == CONTROLLER_RECOVERY_ESCALATE:
        # Every rung this ladder had has been delivered and answered by nothing.
        # The controller stops here: it has taken every bounded action it is
        # allowed to take, and the next decision is a human's.
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_RECOVERY_ESCALATED,
            fields,
            queue_item_id=observation.queue_item_id,
            worker_id=worker_id,
            mission_id=observation.mission_id,
            command_id=slot["command_id"],
            evidence_refs=references,
        )

    reason = CONTROLLER_RECOVERY_RUNG_REASONS[step.rung]
    if terminal_item and step.rung == CONTROLLER_RECOVERY_CANCEL:
        reason = CONTROLLER_REASON_QUEUE_ITEM_TERMINAL
    command_id = str(step.command_id)
    trace_id = evidence.recovery_trace_id(observation.mission_id, step.rung, episode)
    replacement_mission_id = None
    if step.rung == CONTROLLER_RECOVERY_REPLACE:
        replacement_mission_id = evidence.recovery_mission_id(observation.mission_id, episode)
    seconds = (
        CONTROLLER_RECOVERY_ACKNOWLEDGE_SECONDS
        if step.rung == CONTROLLER_RECOVERY_CANCEL
        else CONTROLLER_RECOVERY_RESPOND_SECONDS
    )
    deadline_at = None
    if step.rung != CONTROLLER_RECOVERY_REPLACE:
        deadline_at = _shifted_timestamp(
            evidence.as_of,
            seconds,
            field="requested_at",
            label="controller recovery",
            option="the bounded recovery response window",
            noun="recovery response deadline",
        )
    return ControllerAction(
        kind=CONTROLLER_ACTION_RECOVER,
        outcome=CONTROLLER_RECOVERED,
        reason=reason,
        state_key=controller_state_key(dict(fields, reason=reason)),
        queue_item_id=observation.queue_item_id,
        worker_id=worker_id,
        mission_id=observation.mission_id,
        command_id=command_id,
        trace_id=trace_id,
        payload_digest=command_payload_digest(
            {
                "command_type": CONTROLLER_RECOVERY_COMMAND_PAYLOAD_TYPES[step.rung],
                "episode_revision": episode,
                "mission_id": observation.mission_id,
                "queue_item_id": observation.queue_item_id,
                "reason": reason,
                "rung": step.rung,
                "worker_id": worker_id,
            },
            "controller recovery payload",
        ),
        evidence_refs=references,
        parent_trace_id=observation.trace_id,
        recovery_rung=step.rung,
        deadline_at=deadline_at,
        replacement_mission_id=replacement_mission_id,
    )


def select_recovery_action(evidence: ControllerEvidence) -> Optional[ControllerAction]:
    """Return the one bounded recovery a stale or orphaned mission is owed.

    Only a worker's own claimed, materialized, still-active mission is
    recoverable, and only one worker is acted on per tick: the slots are walked
    in worker order so two ticks reading the same committed prefix always choose
    the same worker, the same rung, and therefore the same command ID. Owed
    commands take priority over another worker's waiting/escalation observation.
    """

    fallback: Optional[ControllerAction] = None
    for worker_id, slot in evidence.state.claimed_slots:
        mission_id = slot["mission_id"]
        if any(entry["state"] in ("pending", "held")
               and entry["dialog"]["mission_id"] == mission_id
               for entry in evidence.state.dialogs.values()):
            continue
        observation = evidence.observation(mission_id)
        if observation is None or observation.terminal or observation.state == LIFECYCLE_PENDING_DISPATCH:
            # A reserved slot whose worker has not accepted yet has no declared
            # freshness at all, so there is nothing to observe as expired.  The
            # `terminal` half of this guard is deliberately unreachable from a
            # claimed slot -- the fold releases the slot on the same event that
            # makes its mission terminal -- and is kept because it is the
            # invariant this loop depends on, not because a caller relies on it.
            #
            # Note (TH3.E3.US3): this is also why a replacement mission nobody
            # ever accepts is observed as `mission-in-progress` for good rather
            # than recovered again, and why `escalate` is not reachable on the
            # full ladder.  Chasing an unaccepted dispatch is a delivery
            # concern, not a reconciliation one; it is US1-shaped behaviour this
            # story deliberately does not change.
            continue
        if observation.worker_id != worker_id:
            continue
        owned = evidence.active_item(observation.queue_item_id) is not None
        if not observation.stale and owned:
            continue
        action = _recovery_action(evidence, worker_id, slot, observation)
        if action.kind == CONTROLLER_ACTION_RECOVER:
            return action
        if fallback is None or (
            fallback.kind == CONTROLLER_ACTION_NONE and action.kind != CONTROLLER_ACTION_NONE
        ):
            fallback = action
    return fallback


def _normalized_architecture_blocker_category(value: Any) -> Optional[str]:
    """Map one blocker category to the closed architecture-boundary vocabulary."""

    if not isinstance(value, str):
        return None
    return ARCHITECTURE_BLOCKER_CATEGORY_MAP.get(value.strip().lower())


def _typed_reference_parts(reference: Any) -> Optional[Tuple[str, str]]:
    """Return `<type>, <value>` for one typed reference, else None."""

    if not isinstance(reference, str):
        return None
    if reference.split() != [reference]:
        return None
    kind, separator, value = reference.partition(":")
    if not separator or not kind or not value:
        return None
    return kind, value


def _mission_boundaries(
    command_slots: Mapping[str, Mapping[str, Any]], mission_id: str
) -> Tuple[Mapping[str, Any], ...]:
    """Return every command boundary declaration carried for one mission."""

    found: List[Mapping[str, Any]] = []
    for slot in command_slots.values():
        envelope = slot.get("envelope")
        if not isinstance(envelope, Mapping):
            continue
        if envelope.get("mission_id") != mission_id:
            continue
        boundaries = envelope.get("boundaries")
        if isinstance(boundaries, Mapping):
            found.append(boundaries)
    return tuple(found)


def _is_declared_repository_access(detail: str, boundaries: Mapping[str, Any]) -> bool:
    """Say whether one repository need is declared by implementation roots."""

    footprint = boundaries.get("mission_footprint")
    reviewed = isinstance(footprint, Mapping) and footprint.get("status") == "reviewed"
    if reviewed:
        try:
            require_footprint_authority(footprint, boundaries)
        except (ValueError, OSError):
            return False
    runtime = footprint["resources"] if reviewed else boundaries.get("runtime_boundaries")
    if not reviewed and isinstance(runtime, list) and detail in runtime:
        return True
    parsed = _typed_reference_parts(detail)
    repo_path = detail
    if parsed is not None:
        kind, value = parsed
        if kind in ("repo", "repository"):
            repo_path = value
    if reviewed:
        if not Path(repo_path).is_absolute():
            return False
        try:
            repo_path = str(Path(repo_path).resolve())
        except (OSError, RuntimeError):
            return False
    roots = (
        [repo["path"] for repo in footprint["repositories"]] + footprint["write_paths"]
        if reviewed else boundaries.get("implementation_roots")
    )
    if not isinstance(roots, list):
        return False
    for declared in roots:
        if not isinstance(declared, str) or not declared:
            continue
        base = declared.rstrip("/")
        if repo_path == declared or repo_path == base:
            return True
        if repo_path.startswith(f"{base}/"):
            return True
    return False


def _architecture_blocker_is_declared(
    category: str,
    detail: Optional[str],
    declared_boundaries: Sequence[Mapping[str, Any]],
) -> bool:
    """Say whether one architecture blocker is already within mission bounds."""

    if not declared_boundaries:
        return False
    if not detail:
        return False
    expected_types = {
        "image": ("image",),
        "repository": ("repo", "repository"),
        "deployment": ("deploy", "deployment"),
        "cicd": ("ci", "ci-cd", "cicd"),
        "iam": ("iam",),
    }[category]
    for boundaries in declared_boundaries:
        footprint = boundaries.get("mission_footprint")
        runtime = (
            footprint["resources"]
            if isinstance(footprint, Mapping) and footprint.get("status") == "reviewed"
            else boundaries.get("runtime_boundaries")
        )
        runtime_refs: Tuple[str, ...] = ()
        if isinstance(runtime, list):
            runtime_refs = tuple(ref for ref in runtime if isinstance(ref, str))
        if category == "repository" and _is_declared_repository_access(detail, boundaries):
            return True
        if category == "repository" and isinstance(footprint, Mapping) and footprint.get("status") == "reviewed":
            continue
        if detail in runtime_refs:
            return True
        parsed = _typed_reference_parts(detail)
        if parsed is None:
            continue
        kind, value = parsed
        if kind not in expected_types:
            continue
        if f"{kind}:{value}" in runtime_refs:
            return True
    return False


def _mission_slot_command_id(state: MissionState, mission_id: str) -> Optional[str]:
    """Return the command ID of the slot currently carrying this mission."""

    for slot in state.worker_slots.values():
        if slot.get("mission_id") != mission_id:
            continue
        command_id = slot.get("command_id")
        return command_id if isinstance(command_id, str) and command_id else None
    return None


def _controller_dispatch_phases(
    events: Sequence[CommittedEvent], state: MissionState,
) -> Dict[str, str]:
    """Recover phase authority from immutable dispatches, never current queue text."""
    phases = {}
    for event in events:
        if not state.mission_outcomes.get(event.event_id, (False, ""))[0]:
            continue
        record = event.record["payload"].get(CONTROLLER_DISPATCH_PAYLOAD_FIELD)
        if record is None:
            continue
        phase = record.get("queue_item_state")
        if phase is None:
            # Historical initial dispatch keys bind phase and identity without brief text.
            phase = next((
                candidate for candidate in CONTROLLER_WORKER_BY_QUEUE_STATE
                if record["state_key"] == controller_state_key({
                    "reason": record["reason"], "queue_item_id": record["queue_item_id"],
                    "queue_item_state": candidate, "worker_id": record["worker_id"],
                    "mission_id": record["mission_id"],
                })
            ), None)
        if phase is not None:
            phases[record["mission_id"]] = phase
    return phases


def _terminal_phase_mission(
    item: QueueItemObservation, state: MissionState,
    dispatch_phases: Optional[Mapping[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """Fence completed phases; unknown historical phases retain a conservative role fence."""
    if any(slot["queue_item_id"] == item.item_id for _worker, slot in state.claimed_slots):
        # In particular, an applied replacement owns an explicit reservation.
        return None
    role = CONTROLLER_WORKER_BY_QUEUE_STATE.get(item.state)
    ended = []
    for mission in state.missions.values():
        lifecycle = mission["lifecycle"]
        worker = lifecycle["worker_id"]
        same_role = (
            role is None or worker == role
            or (worker.startswith(role + "-") and worker[len(role) + 1:].isdigit())
        )
        phase = (dispatch_phases or {}).get(lifecycle["mission_id"])
        same_phase = role is None or (item.state == phase if phase is not None else same_role)
        if (lifecycle["queue_item_id"] == item.item_id and same_phase
            and lifecycle["state"] in WORKER_LIFECYCLE_TERMINAL_STATES):
            ended.append(mission)
    if not ended:
        return None
    latest = max(ended, key=lambda mission: mission["revision"])
    if latest["lifecycle"]["state"] == LIFECYCLE_CANCELLED and any(
        entry["event_id"] == latest["event_id"]
        and "reservation_recovery" in entry["cancellation"]
        and state.mission_outcomes.get(entry["event_id"], (False, ""))[0]
        for entry in state.cancellations.values()
    ):
        return None
    return latest


def _fifo_candidate(
    queue: QueueObservation, state: MissionState,
    dispatch_phases: Optional[Mapping[str, str]] = None,
) -> Optional[QueueItemObservation]:
    """Prefer new FIFO work to retries, without bypassing a waiting admission."""
    claimed = {slot["queue_item_id"] for _worker, slot in state.claimed_slots}
    settled = {item.item_id for item in queue.active
               if _terminal_phase_mission(item, state, dispatch_phases) is not None}
    waiting = [item for item in queue.items
               if item.state in ("queued", "blocked")
               or (item.active and item.item_id not in claimed and item.item_id not in settled)]
    if waiting and waiting[0].active:
        return waiting[0]
    existing = [item for item in queue.active if item.item_id in claimed]
    reserved = {slot["queue_item_id"] for _worker, slot in state.claimed_slots
                if slot["state"] == MISSION_SLOT_RESERVED}
    return next((item for item in existing if item.item_id in reserved),
                existing[0] if existing else next(
                    (item for item in queue.active if item.item_id in settled), None,
                ))


def _scope_conflict(
    item: QueueItemObservation, state: MissionState, commands: Mapping[str, Mapping[str, Any]],
) -> Optional[str]:
    for _worker, slot in state.claimed_slots:
        command = commands.get(slot["command_id"])
        footprint = None if command is None else command["envelope"]["boundaries"].get("mission_footprint")
        if footprint is not None:
            _require_footprint_authority(footprint, command["envelope"]["boundaries"])
            try:
                validate_footprint(footprint, live=True)
            except (ValueError, OSError):
                # The occupied claim is uncertain, not released or replaced by queue history.
                return slot["queue_item_id"]
        if slot["queue_item_id"] == item.item_id:
            continue
        if footprints_conflict(item.footprint, footprint):
            return slot["queue_item_id"]
    return None


def _require_footprint_authority(
    footprint: Optional[Dict[str, Any]], roots: Mapping[str, Any],
) -> None:
    try:
        require_footprint_authority(footprint, roots)
    except (ValueError, OSError) as exc:
        raise ControlStoreError(f"footprint authority refused: {exc}") from exc


def select_controller_action(evidence: ControllerEvidence) -> ControllerAction:
    """Choose the at most one action ADR-014 precedence allows, and no more.

    The ladder is read top to bottom in precedence order and returns at the
    first source that answers:

    * root agreement is a precondition, because without agreeing roots there is
      no product-work authority to consult at all;
    * a divergent derived ledger blocks rather than being trusted -- rule 5 is a
      rebuildable projection, so a projection no writer could have produced is
      repaired explicitly and never quietly obeyed, while a projection that is
      merely behind the committed journal is the published window of a
      concurrent write and is left for the next writer to replace;
    * rule 1, an explicit human stop, outranks everything the queue and the
      workers say;
    * rule 2, `cockpit-queue`, decides what product work exists and what state
      it is in; the controller never invents or reopens a queue state;
    * rules 3 and 4, the durable lifecycle, command, and slot evidence replayed
      from committed events, decide whether a worker is already carrying work.

    Rules 7 and 8 are unreachable from here: this function cannot see them.
    """

    # If a boundary blocker was detected during evidence gathering,
    # refuse to dispatch and record an explicit blocked observation so
    # the operator sees an architecture-boundary event.
    if getattr(evidence, "boundary_blocker", None) is not None:
        bb = evidence.boundary_blocker
        references: List[str] = []
        mission_id = bb.get("mission_id")
        queue_item_id = bb.get("queue_item_id")
        command_id = bb.get("command_id")
        trace_id = bb.get("trace_id")
        blocker = bb.get("blocker") if isinstance(bb.get("blocker"), dict) else {}
        category = _normalized_architecture_blocker_category(blocker.get("category"))
        if isinstance(mission_id, str) and mission_id:
            references.append(f"mission:{mission_id}")
        if isinstance(queue_item_id, str) and queue_item_id:
            references.append(f"queue:{queue_item_id}")
        if isinstance(command_id, str) and command_id:
            references.append(f"command:{command_id}")
        if isinstance(trace_id, str) and trace_id:
            references.append(f"trace:{trace_id}")
        if category is not None:
            references.append(f"boundary:{category}")
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_ROOT_UNDECLARED,
            {"architecture_blocker": bb},
            mission_id=mission_id if isinstance(mission_id, str) else None,
            queue_item_id=queue_item_id if isinstance(queue_item_id, str) else None,
            worker_id=bb.get("worker_id") if isinstance(bb.get("worker_id"), str) else None,
            command_id=command_id if isinstance(command_id, str) else None,
            evidence_refs=tuple(references),
        )


    if evidence.queue_root_fault is not None:
        fault = evidence.queue_root_fault
        return _controller_observation_action(
            evidence,
            fault,
            {
                "declared_queue_root": evidence.declared_queue_root,
                "exported_queue_root": evidence.exported_queue_root,
            },
        )

    if evidence.ledger_undecidable:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_LEDGER_DIVERGENT,
            {
                "ledger_reason": evidence.ledger_reason,
                "journal_revision": evidence.journal_revision,
                "ledger_revision": evidence.ledger_revision,
            },
        )

    queue = evidence.queue
    if queue is None:
        # A tick that cannot read product work always carries the root fault
        # answered above, so this is the structural half of the same rule: no
        # evidence without product-work authority can reach a dispatch, however
        # the record reached this function.
        return _controller_observation_action(
            evidence, CONTROLLER_REASON_NO_ROOT, {"control_id": evidence.control_id}
        )

    if queue.paused:
        return _controller_observation_action(
            evidence, CONTROLLER_REASON_QUEUE_PAUSED, {"queue_root": queue.root}
        )

    # Rule 3 again, and before any product-work observation: two missions
    # claiming one worker is a split in the control plane's own durable state.
    # The fold has already kept the mission that claimed the slot first in
    # committed revision order; what is left is to stop dispatching while the
    # cockpit is in that state and to escalate it exactly once.
    contested = evidence.state.contested_slots
    if contested:
        worker_id, slot, conflict = contested[0]
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_WORKER_CONFLICT,
            {
                "worker_id": worker_id,
                "retained_mission_id": slot["mission_id"],
                "retained_at_revision": conflict["revision"],
                "refused_mission_id": conflict["mission_id"],
                "conflict_reason": conflict["reason"],
                "conflict_source": conflict["source"],
            },
            queue_item_id=slot["queue_item_id"],
            worker_id=worker_id,
            mission_id=slot["mission_id"],
            command_id=slot["command_id"],
            evidence_refs=(
                f"mission:{slot['mission_id']}",
                f"mission:{conflict['mission_id']}",
            ),
        )

    # Owed recovery commands/observations outrank new work. Held dialogs are
    # excluded by the recovery selector; unchanged observations need not monopolize a tick.
    recovery = select_recovery_action(evidence)
    if recovery is not None and recovery.kind != CONTROLLER_ACTION_NONE:
        return recovery

    active = queue.active
    for index, left in enumerate(active):
        if any(footprints_conflict(left.footprint, right.footprint) for right in active[index + 1:]):
            return _controller_observation_action(
                evidence, CONTROLLER_REASON_QUEUE_AMBIGUOUS,
                {"active_items": [item.item_id for item in active], "footprint_conflict": True},
                evidence_refs=tuple(f"queue:{item.item_id}" for item in active),
            )
    item = _fifo_candidate(queue, evidence.state, evidence.dispatch_phases)
    terminal = None if item is None else _terminal_phase_mission(item, evidence.state, evidence.dispatch_phases)
    if terminal is not None:
        lifecycle = terminal["lifecycle"]
        return _controller_observation_action(
            evidence, CONTROLLER_REASON_ROLE_TERMINAL,
            {"queue_item_id": item.item_id, "queue_item_state": item.state,
             "mission_id": lifecycle["mission_id"], "terminal_state": lifecycle["state"],
             "terminal_revision": terminal["revision"]},
            queue_item_id=item.item_id, worker_id=lifecycle["worker_id"],
            mission_id=lifecycle["mission_id"],
            evidence_refs=(f"queue:{item.item_id}", f"mission:{lifecycle['mission_id']}"),
        )
    new_candidate = item is not None and not any(
        slot["queue_item_id"] == item.item_id for _holder, slot in evidence.state.claimed_slots
    )
    commands = {entry["envelope"]["command_id"]: entry for entry in evidence.commands}
    conflict = None if item is None else _scope_conflict(item, evidence.state, commands)
    independent_candidate = (
        new_candidate and conflict is None and item.worker_id is not None
        and not any(holder == item.worker_id for holder, _slot in evidence.state.claimed_slots)
    )

    # An explicit unanswered dialog is not worker silence. In particular an
    # operator hold outranks heartbeat-based recovery and never authorizes an
    # automatic answer, permission grant, cancellation, or replacement.
    for entry in evidence.state.dialogs.values():
        dialog = entry["dialog"]
        slot = evidence.state.worker_slots.get(dialog["worker_id"])
        if (not independent_candidate and (item is None or item.item_id == dialog["queue_item_id"])
            and entry["state"] in ("pending", "held") and slot is not None
            and slot["state"] == MISSION_SLOT_ACTIVE and slot["mission_id"] == dialog["mission_id"]
            and (entry["state"] == "held" or evidence.active_item(dialog["queue_item_id"]) is not None)):
            return _controller_observation_action(
                evidence, CONTROLLER_REASON_MISSION_IN_PROGRESS,
                {"prompt_command_id": dialog["command_id"], "dialog_state": entry["state"]},
                mission_id=dialog["mission_id"], queue_item_id=dialog["queue_item_id"],
                worker_id=dialog["worker_id"], command_id=dialog["command_id"],
            )

    # Rules 3 and 6 again: a mission the control plane still believes is running
    # while its declared freshness has expired, or whose product work has left
    # the active set, is owed exactly one bounded recovery action.  It is never
    # marked failed here or anywhere else -- `stale` is an observation, and only
    # the worker's own lifecycle event or a human decision ends a mission.
    if recovery is not None and not independent_candidate:
        return recovery

    active = queue.active
    if not active:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_QUEUE_EMPTY,
            {"queue_root": queue.root, "queued": len(queue.queued)},
        )
    worker_busy = item is not None and any(
        holder == item.worker_id for holder, _slot in evidence.state.claimed_slots
    )
    if item is None or (conflict is not None and not worker_busy):
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_FIFO_BLOCKED if item is None else CONTROLLER_REASON_FOOTPRINT_CONFLICT,
            {"active_items": [item.item_id for item in active], "blocking_claim": conflict,
             "fifo_waiting": item is None},
            queue_item_id=None if item is None else item.item_id,
            evidence_refs=tuple(f"queue:{item.item_id}" for item in active)
            + (() if conflict is None else (f"queue:{conflict}",)),
        )

    references = (f"queue:{item.item_id}",)
    worker_id = item.worker_id
    if worker_id is None:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_NOT_IMPLEMENTABLE,
            {"queue_item_id": item.item_id, "queue_item_state": item.state},
            queue_item_id=item.item_id,
            evidence_refs=references,
        )

    # Older phase reservations remain held, but cannot mask accepted work on
    # the worker the queue currently calls for. Recovery was considered above.
    claims = sorted(
        evidence.state.claimed_slots,
        key=lambda claim: (
            not (claim[0] == worker_id and claim[1]["state"] == MISSION_SLOT_ACTIVE),
            claim[0],
        ),
    )
    for holder, slot in claims:
        if slot["queue_item_id"] == item.item_id:
            observed = evidence.observation(slot["mission_id"])
            if observed is not None and observed.state == LIFECYCLE_PENDING_DISPATCH:
                observed = None
            controller = evidence.controller
            stored = next(
                (entry for entry in evidence.commands
                 if entry["envelope"]["command_id"] == slot["command_id"]),
                None,
            )
            if (holder == worker_id and slot["state"] == MISSION_SLOT_RESERVED
                and slot["replaces"] is not None and stored is not None
                and stored["envelope"]["command_type"] == COMMAND_TYPE_MISSION_REPLACE
                and stored["status"] == COMMAND_APPLIED):
                mission_id = slot["mission_id"]
                trace_id = _controller_derived_uuid(evidence.control_id, CONTROLLER_TRACE_DERIVATION, mission_id)
                return ControllerAction(
                    kind=CONTROLLER_ACTION_DISPATCH, outcome=CONTROLLER_DISPATCHED,
                    reason=CONTROLLER_REASON_IMPLEMENTABLE,
                    state_key=controller_state_key({"replacement_mission_id": mission_id}),
                    queue_item_id=item.item_id, worker_id=worker_id, mission_id=mission_id,
                    command_id=_controller_derived_uuid(evidence.control_id, CONTROLLER_COMMAND_DERIVATION, mission_id),
                    trace_id=trace_id,
                    payload_digest=dispatch_payload_digest(item, mission_id, worker_id, trace_id),
                    deadline_at=_shifted_timestamp(evidence.as_of, DISPATCH_ACCEPTANCE_SECONDS),
                    evidence_refs=references,
                )
            dispatch_reservation = (
                stored is None
                or stored["envelope"]["command_type"] == COMMAND_TYPE_MISSION_DISPATCH
            )
            if slot["state"] == MISSION_SLOT_RESERVED and observed is None and dispatch_reservation:
                envelope = None if stored is None else stored["envelope"]
                deadline = None if envelope is None else envelope["deadline_at"]
                reason = None
                if envelope is not None and item.footprint != envelope["boundaries"].get("mission_footprint"):
                    reason = CONTROLLER_REASON_ACCEPTANCE_UNSUPPORTED
                if (
                    deadline is None or envelope["command_type"] != COMMAND_TYPE_MISSION_DISPATCH
                    or stored["status"] != COMMAND_STATUS_REGISTERED or stored["conflicts"]
                ):
                    reason = CONTROLLER_REASON_ACCEPTANCE_UNSUPPORTED
                elif _parsed_timestamp(evidence.as_of, "as_of", "tick") >= _parsed_timestamp(
                    deadline, "deadline_at", "dispatch"
                ):
                    reason = CONTROLLER_REASON_ACCEPTANCE_EXPIRED
                if reason is not None:
                    return _controller_observation_action(
                        evidence, reason,
                        {"command_id": slot["command_id"], "deadline_at": deadline},
                        queue_item_id=item.item_id, worker_id=holder,
                        mission_id=slot["mission_id"], command_id=slot["command_id"],
                        evidence_refs=references + (f"command:{slot['command_id']}",),
                    )
            if (
                holder == worker_id
                and slot["state"] == MISSION_SLOT_RESERVED
                and observed is None
                and controller is not None
                and dispatch_reservation
            ):
                # A reservation with no worker acceptance is an uncertain
                # delivery. Reissue the same deterministic command; the fold
                # retains it as a duplicate, so retries never mint a second
                # event, mission, or slot claim.
                trace_id = _controller_derived_uuid(
                    evidence.control_id,
                    CONTROLLER_TRACE_DERIVATION,
                    slot["mission_id"],
                )
                return ControllerAction(
                    kind=CONTROLLER_ACTION_DISPATCH,
                    outcome=CONTROLLER_DISPATCHED,
                    reason=CONTROLLER_REASON_IMPLEMENTABLE,
                    state_key=controller["state_key"],
                    queue_item_id=item.item_id,
                    worker_id=worker_id,
                    mission_id=slot["mission_id"],
                    command_id=slot["command_id"],
                    trace_id=trace_id,
                    payload_digest=dispatch_payload_digest(item, slot["mission_id"], worker_id, trace_id),
                    deadline_at=deadline,
                    evidence_refs=references,
                )
            # The freshness the worker has itself declared is part of *which*
            # situation this is, not decoration on it.  Reaching here at all
            # means the mission was fresh at this moment, so a worker that went
            # stale and then came back has necessarily declared a window it had
            # not declared before: including it is what makes "the controller
            # looked and the mission was fresh" a recorded observation rather
            # than one that collapses into an older, expired look and is never
            # written down.  Evidence that does not restore freshness never
            # reaches this branch, so it can never re-key an episode either.
            return _controller_observation_action(
                evidence,
                CONTROLLER_REASON_MISSION_IN_PROGRESS,
                {
                    "queue_item_id": item.item_id,
                    "queue_item_state": item.state,
                    "worker_id": holder,
                    "mission_id": slot["mission_id"],
                    "slot_state": slot["state"],
                    "fresh_until": None if observed is None else observed.fresh_until,
                },
                queue_item_id=item.item_id,
                worker_id=holder,
                mission_id=slot["mission_id"],
                command_id=slot["command_id"],
                evidence_refs=references,
            )

    busy = evidence.state.worker_slots.get(worker_id)
    if busy is not None and busy["state"] in MISSION_SLOT_CLAIMED_STATES:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_WORKER_BUSY,
            {
                "queue_item_id": item.item_id,
                "worker_id": worker_id,
                "mission_id": busy["mission_id"],
                "held_queue_item_id": busy["queue_item_id"],
            },
            queue_item_id=item.item_id,
            worker_id=worker_id,
            mission_id=busy["mission_id"],
            evidence_refs=references,
        )

    attempt = evidence.dispatch_attempt(item.item_id, worker_id)
    mission_id = _controller_derived_uuid(
        evidence.control_id,
        CONTROLLER_MISSION_DERIVATION,
        item.item_id,
        worker_id,
        str(attempt),
    )
    command_id = _controller_derived_uuid(
        evidence.control_id, CONTROLLER_COMMAND_DERIVATION, mission_id
    )
    trace_id = _controller_derived_uuid(
        evidence.control_id, CONTROLLER_TRACE_DERIVATION, mission_id
    )
    return ControllerAction(
        kind=CONTROLLER_ACTION_DISPATCH,
        outcome=CONTROLLER_DISPATCHED,
        reason=CONTROLLER_REASON_IMPLEMENTABLE,
        state_key=controller_state_key(
            {
                "reason": CONTROLLER_REASON_IMPLEMENTABLE,
                "queue_item_id": item.item_id,
                "queue_item_state": item.state,
                "worker_id": worker_id,
                "mission_id": mission_id,
            }
        ),
        queue_item_id=item.item_id,
        worker_id=worker_id,
        mission_id=mission_id,
        command_id=command_id,
        trace_id=trace_id,
        payload_digest=dispatch_payload_digest(item, mission_id, worker_id, trace_id),
        deadline_at=_shifted_timestamp(
            evidence.as_of, DISPATCH_ACCEPTANCE_SECONDS,
            label="dispatch", noun="acceptance deadline",
        ),
        evidence_refs=references,
    )


def dispatch_payload_digest(
    item: QueueItemObservation, mission_id: str, worker_id: str, trace_id: str,
) -> str:
    return command_payload_digest(
        {
            "brief_digest": "sha256:" + hashlib.sha256(
                json.dumps(
                    {"source_text": item.source_text, "title": item.title},
                    sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "command_type": COMMAND_TYPE_MISSION_DISPATCH,
            "mission_id": mission_id, "queue_item_id": item.item_id,
            "queue_item_state": item.state, "trace_id": trace_id, "worker_id": worker_id,
            **({"mission_footprint": item.footprint} if item.footprint is not None else {}),
        },
        "controller dispatch payload",
    )


@dataclass(frozen=True)
class DispatchAcceptanceResult:
    outcome: str
    command_id: str
    mission_id: str
    start_work: bool
    event_id: Optional[str]


def accept_dispatch(
    root: Path, command_id: str, mission_id: str, worker_id: str,
    queue_item_id: str, trace_id: str, payload_digest: str,
    *, fresh_for: str, dry_run: bool = False, as_of: Optional[str] = None,
) -> DispatchAcceptanceResult:
    """Claim work once, with a joint receipt under the transport's control lock."""

    root = _require_absolute_root(str(root), "configured")
    # Validate even duplicate requests before looking up or changing any state.
    now = as_of if as_of is not None else utc_timestamp()
    fresh_seconds = _parsed_freshness_seconds(fresh_for)
    fresh_until = _shifted_timestamp(now, fresh_seconds, label="acceptance", noun="freshness")
    lifecycle = build_worker_lifecycle(
        LIFECYCLE_ACCEPTED, worker_id, mission_id, queue_item_id, trace_id, 1,
        heartbeat_at=now, fresh_until=fresh_until,
    )
    acknowledgement = build_command_acknowledgement(
        command_id, payload_digest, COMMAND_ACCEPTED, worker_id, acknowledged_at=now,
    )
    with (nullcontext(None) if dry_run else PortableControlLock(
        root, command="cockpit-control accept-dispatch"
    )) as lock, ExitStack() as queue_locks:
        if as_of is None:
            now = utc_timestamp()
            lifecycle["heartbeat_at"] = now
            lifecycle["fresh_until"] = _shifted_timestamp(now, fresh_seconds)
            acknowledgement["acknowledged_at"] = now
        validate_control_authority(root, CONTROLLER_JOURNAL_REPAIR)
        metadata, history = inspect_control_events(root)
        _revision, projection_reason = _classify_published_ledger(
            root, metadata["control_id"],
            _serialized_record(build_ledger_projection(metadata, history.events)).encode("utf-8"),
        )
        if projection_reason == PROJECTION_REASON_AHEAD:
            raise ControlStoreError(
                "dispatch acceptance cannot rewind missing committed history [repair: "
                + CONTROLLER_LEDGER_AHEAD_REPAIR.format(
                    ledger=LEDGER_NAME, journal=history.latest_revision,
                ) + "]"
            )
        state = fold_mission_state(history.events)
        commands, _outcomes = fold_commands(history.events)
        command = commands.get(command_id)
        if command is None:
            raise ControlStoreError("dispatch command is not registered in this control root")
        envelope = command["envelope"]
        slot = state.worker_slots.get(worker_id)
        roots = metadata["canonical_roots"]
        boundaries = envelope["boundaries"]
        if (
            envelope["command_type"] != COMMAND_TYPE_MISSION_DISPATCH
            or envelope["target"] != {"kind": COMMAND_TARGET_WORKER, "id": worker_id}
            or envelope["payload_digest"] != payload_digest
            or any(envelope[key] != lifecycle[key] for key in (
                "mission_id", "queue_item_id", "trace_id", "parent_trace_id"
            ))
            or boundaries["control_root"] != str(root)
            or any(boundaries[key] != roots[key] for key in (
                "queue_root", "planning_root", "implementation_roots"
            ))
            or boundaries["runtime_boundaries"]
            or not roots["queue_root"] or not roots["planning_root"]
            or not roots["implementation_roots"]
            or not has_lifecycle_capability(metadata)
            or slot is None or slot["mission_id"] != mission_id
            or slot["command_id"] != command_id or slot["queue_item_id"] != queue_item_id
            or any(holder == worker_id for holder, _slot, _conflict in state.contested_slots)
            or command["conflicts"]
        ):
            raise ControlStoreError("dispatch receipt mismatches its identity, digest, slot, or boundaries")
        if not dry_run:
            queue_locks.enter_context(queue_lock(roots["queue_root"]))
        queue = observe_queue(Path(roots["queue_root"]))
        item = next((item for item in queue.active if item.item_id == queue_item_id), None)
        if queue.paused or item is None or item.worker_id != worker_id:
            raise ControlStoreError("dispatch queue item is paused, terminal, or no longer implementable")
        if dispatch_payload_digest(item, mission_id, worker_id, trace_id) != payload_digest:
            raise ControlStoreError("dispatch queue brief or state no longer matches the command digest")
        if (item.footprint != boundaries.get("mission_footprint")
            or _scope_conflict(item, state, commands) is not None):
            raise ControlStoreError("dispatch footprint changed or conflicts with occupied claims")
        if command["status"] == COMMAND_ACCEPTED:
            mission = state.missions.get(mission_id)
            receipt = next(
                (entry for entry in command["acknowledgements"]
                 if entry["acknowledgement"]["outcome"] == COMMAND_ACCEPTED),
                None,
            )
            if (
                slot["state"] != MISSION_SLOT_ACTIVE or mission is None
                or mission["lifecycle"]["state"] not in WORKER_LIFECYCLE_ACTIVE_STATES
                or receipt is None
                or not state.lifecycle_outcomes.get(receipt["event_id"], (False, ""))[0]
            ):
                raise ControlStoreError("dispatch has no live, correlated acceptance; explicit recovery required")
            return DispatchAcceptanceResult("duplicate", command_id, mission_id, False, receipt["event_id"])
        fault = dispatch_acceptance_fault(command, acknowledgement, lifecycle, slot, state.missions)
        if fault is not None:
            raise ControlStoreError(fault)
        if dry_run:
            return DispatchAcceptanceResult("would-accept", command_id, mission_id, False, None)
        publication = publish_control_event(
            root, f"{WORKER_LIFECYCLE_EVENT_PREFIX}{LIFECYCLE_ACCEPTED}",
            actor=worker_id,
            payload={
                WORKER_LIFECYCLE_PAYLOAD_FIELD: lifecycle,
                COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD: acknowledgement,
            },
            command="cockpit-control accept-dispatch", held_lock=lock,
        )
        return DispatchAcceptanceResult("accepted", command_id, mission_id, True, publication.event_id)


def build_controller_dispatch(
    command_id: str,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    trace_id: str,
    reason: str,
    state_key: str,
    parent_trace_id: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    decided_at: Optional[str] = None,
    queue_item_state: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble one complete controller dispatch and validate it before use."""

    record = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "record_type": CONTROLLER_DISPATCH_RECORD_TYPE,
        "command_id": command_id,
        "mission_id": mission_id,
        "worker_id": worker_id,
        "queue_item_id": queue_item_id,
        "trace_id": trace_id,
        "parent_trace_id": parent_trace_id,
        "reason": reason,
        "state_key": state_key,
        "evidence_refs": list(evidence_refs),
        "decided_at": decided_at if decided_at is not None else utc_timestamp(),
    }
    if queue_item_state is not None:
        record["queue_item_state"] = queue_item_state
    return validate_controller_dispatch(record)


def build_controller_observation(
    outcome: str,
    reason: str,
    state_key: str,
    queue_item_id: Optional[str] = None,
    worker_id: Optional[str] = None,
    mission_id: Optional[str] = None,
    command_id: Optional[str] = None,
    evidence_refs: Sequence[str] = (),
    observed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble one complete controller observation and validate it before use."""

    record = {
        "schema_version": CONTROLLER_SCHEMA_VERSION,
        "record_type": CONTROLLER_OBSERVATION_RECORD_TYPE,
        "outcome": outcome,
        "reason": reason,
        "state_key": state_key,
        "queue_item_id": queue_item_id,
        "worker_id": worker_id,
        "mission_id": mission_id,
        "command_id": command_id,
        "evidence_refs": list(evidence_refs),
        "observed_at": observed_at if observed_at is not None else utc_timestamp(),
    }
    return validate_controller_observation(record)


@dataclass(frozen=True)
class ControllerTickResult:
    """Everything one tick reconciled, decided, and durably recorded."""

    root: Path
    outcome: str
    action: ControllerAction
    evidence: ControllerEvidence
    diagnostics: Tuple[ControllerDiagnostic, ...]
    applied: bool
    fold_outcome: str
    controller: Optional[Dict[str, Any]]
    publication: Optional[EventPublicationResult]
    command: Optional[CommandRecordResult]
    conflict: Optional[Dict[str, Any]]
    queue_fault: Optional[str]
    dry_run: bool

    @property
    def blocked(self) -> bool:
        return self.action.outcome == CONTROLLER_BLOCKED

    @property
    def dispatched(self) -> bool:
        return self.outcome == CONTROLLER_TICK_DISPATCHED

    @property
    def committed_events(self) -> int:
        """Report how many events this tick actually committed: never above one."""

        if self.publication is None or not self.publication.committed:
            return 0
        return 1


def _controller_dispatched_pairs(
    events: Sequence[CommittedEvent] = (),
) -> Tuple[Tuple[str, str], ...]:
    """List every committed dispatch as the (queue item, worker) pair it named.

    The next mission identifier is derived from how many of these already exist,
    so two concurrent ticks reading the same committed prefix mint one command
    rather than two, and a queue item that is legitimately dispatched again
    after a terminal mission gets a new identity instead of a reuse conflict.
    """

    pairs: List[Tuple[str, str]] = []
    for event in events:
        field, record = event_mission_control(
            event.record, f"{EVENTS_DIR_NAME}/{event.path.name}"
        )
        if field == CONTROLLER_DISPATCH_PAYLOAD_FIELD and record is not None:
            pairs.append((record["queue_item_id"], record["worker_id"]))
    return tuple(pairs)


def _correlation_counts(state: MissionState) -> Tuple[int, int]:
    """Count durable records that correlate with a mission, and those that do not.

    ADR-014 rule 6 admits a worker report as evidence only when it matches the
    mission it claims.  The same test is what the committed fold already applies
    to every lifecycle event and every managed record, so this reports the fold's
    own answer rather than inventing a second notion of "matching".
    """

    correlated = 0
    uncorrelated = 0
    for outcomes in (state.lifecycle_outcomes, state.mission_outcomes):
        for applied, _outcome in outcomes.values():
            if applied:
                correlated += 1
            else:
                uncorrelated += 1
    return correlated, uncorrelated


@dataclass(frozen=True)
class ControllerJournalObservation:
    """One reading of the committed journal and the projection derived from it.

    The four values belong together: the ledger classification only means
    anything against the exact replay it was compared with, so they are read and
    carried as a single observation rather than as four independent reads.
    """

    metadata: Dict[str, Any]
    history: EventHistory
    ledger_revision: Optional[int]
    ledger_reason: str
    repair: str = CONTROLLER_LEDGER_CURRENT
    repaired_from: Optional[str] = None

    @property
    def settled(self) -> bool:
        """True when the projection equals the rebuild of this exact replay."""

        return self.ledger_reason == PROJECTION_REASON_CURRENT

    def with_repair(self, repair: str, repaired_from: str) -> "ControllerJournalObservation":
        """Return this reading annotated with what the tick did about it."""

        return ControllerJournalObservation(
            metadata=self.metadata,
            history=self.history,
            ledger_revision=self.ledger_revision,
            ledger_reason=self.ledger_reason,
            repair=repair,
            repaired_from=repaired_from,
        )


class ControllerTick:
    """Reconcile durable evidence, take at most one action, and terminate.

    The bounded loop of architecture section 11 runs exactly once per invocation:
    validate the control and queue roots, replay the committed journal and check
    the derived ledger against it, read product-work and worker state, apply
    ADR-014 precedence, choose at most one state-changing action, persist it, and
    exit.  There is no retry, no polling, and no second pass, so a tick that can
    make no progress terminates with a recorded observation instead of looking
    again.

    The lease acquisition and release that section 11 also lists are deliberately
    absent: they are wake-model concerns, and this class is shaped so a lease can
    wrap `run()` without changing how a decision is reached.
    """

    def __init__(
        self,
        root: Path,
        diagnostics: Sequence[ControllerDiagnostic] = (),
        as_of: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        command: str = DEFAULT_CONTROLLER_TICK_COMMAND,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
        dry_run: bool = False,
    ) -> None:
        self.root = _require_absolute_root(str(root), "configured")
        self.diagnostics = tuple(diagnostics)
        for diagnostic in self.diagnostics:
            if not isinstance(diagnostic, ControllerDiagnostic):
                raise ControlStoreError(
                    "controller diagnostics must be ControllerDiagnostic records"
                )
        self.as_of = (
            _require_timestamp({"as_of": as_of}, "as_of", "controller tick")
            if as_of is not None
            else utc_timestamp()
        )
        self.environ = os.environ if environ is None else environ
        self.command = command.strip() if isinstance(command, str) else ""
        if not self.command:
            raise ControlStoreError("a controller tick requires a non-empty command")
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.dry_run = bool(dry_run)
        self.queue_fault: Optional[str] = None
        self._acted = False

    def _resolved_queue_root(
        self,
        metadata: Mapping[str, Any],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Agree the one queue root, or name exactly how the two roots disagree.

        `control.json` declares the queue boundary the mission was created with
        and the shell exports the one this process would actually read.  When
        they name different cockpits there is no safe reading of product work at
        all, so dispatch is blocked instead of one of them being preferred.
        """

        declared = metadata["canonical_roots"]["queue_root"]
        exported = self.environ.get(QUEUE_ROOT_VARIABLE)
        if exported is not None:
            exported = str(_require_absolute_root(exported, "shell", QUEUE_ROOT_VARIABLE))
        if declared is None:
            if exported is None:
                return None, None, CONTROLLER_REASON_NO_ROOT
            return declared, exported, CONTROLLER_REASON_ROOT_UNDECLARED
        if exported is not None and exported != declared:
            return declared, exported, CONTROLLER_REASON_ROOT_CONFLICT
        return declared, exported, None

    def _observed_journal(self) -> ControllerJournalObservation:
        """Replay the committed events once and classify every derived record.

        The compatibility view is classified beside the ledger rather than
        separately: both are pure functions of the same committed events, one
        replay rebuilds both, and a tick that repaired only half of the derived
        state would leave a store `cockpit-control validate` still refuses.
        """

        metadata, history = inspect_control_events(self.root)
        rebuilt = build_ledger_projection(metadata, history.events)
        ledger_revision, ledger_reason = _classify_published_ledger(
            self.root, metadata["control_id"], _serialized_record(rebuilt).encode("utf-8")
        )
        if ledger_reason == PROJECTION_REASON_CURRENT and not _published_view_matches(
            self.root, build_events_view(history.events)
        ):
            ledger_reason = PROJECTION_REASON_VIEW
        return ControllerJournalObservation(
            metadata=metadata,
            history=history,
            ledger_revision=ledger_revision,
            ledger_reason=ledger_reason,
        )

    def _settled_journal(self) -> ControllerJournalObservation:
        """Read the journal and its projection as one settled observation.

        A reader holding nothing can land inside the window architecture section
        8.3 opens between step 4, where the event becomes committed authority,
        and step 8, where the projection derived from it is replaced.  Inside
        that window the store is entirely healthy and the two disagree anyway, so
        a lock-free reading that is not settled says nothing about the store: it
        may equally be a concurrent writer or a hand-edited ledger.

        One reading is therefore taken again while holding `control.lock`, where
        no writer can be part-way through the sequence and the answer is a fact
        about the store rather than about this process's timing.  The lock is
        taken only for the reading that was unsettled, it commits nothing, and it
        is released before any action is chosen, so a tick that finds a healthy
        store still reads it without ever excluding a writer.
        """

        observation = self._observed_journal()
        if observation.settled:
            return observation
        with PortableControlLock(
            self.root,
            self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ) as lock:
            observation = self._observed_journal()
            if observation.settled:
                return observation
            return self._repaired_projection(observation, lock)

    def _repaired_projection(
        self,
        observation: ControllerJournalObservation,
        lock: Optional[PortableControlLock],
    ) -> ControllerJournalObservation:
        """Rebuild derived state from the committed journal, or refuse to.

        This is step 3 of the bounded loop -- "replay new events and validate the
        ledger revision" -- and not the tick's one state-changing action: replay
        commits no event, invents no revision, and is a pure function of the
        committed authority this method has just read under the same lock, so
        running it twice changes nothing the second time.

        Exactly one classification is refused instead of repaired.  A projection
        *ahead* of the committed journal records revisions `events/` no longer
        holds, so rebuilding it would rewind derived state and quietly settle the
        disappearance of committed authority.  ADR-014 keeps authoritative repair
        explicit, so that one is named, blocked, and left untouched.
        """

        journal = observation.history.latest_revision
        if controller_ledger_plan(observation.ledger_reason) == CONTROLLER_LEDGER_REFUSED:
            raise ControlStoreError(
                f"{LEDGER_NAME} records revision {observation.ledger_revision} but "
                f"{EVENTS_DIR_NAME}/ ends at revision {journal}; committed events appear "
                "to have been removed and replay would rewind derived state, so no "
                "decision was taken and not one byte was changed [repair: "
                + CONTROLLER_LEDGER_AHEAD_REPAIR.format(ledger=LEDGER_NAME, journal=journal)
                + "]"
            )
        if self.dry_run:
            return observation.with_repair(
                CONTROLLER_LEDGER_WOULD_REPAIR, observation.ledger_reason
            )
        ControlLedgerProjection(self.root, command=self.command, held_lock=lock).run()
        rebuilt = self._observed_journal()
        if not rebuilt.settled:
            # Committing anything now would replace the projection and destroy
            # the evidence of whatever is rewriting it, so the tick stops here.
            raise ControlStoreError(
                f"{LEDGER_NAME} still does not equal the committed rebuild after replay "
                f"({rebuilt.ledger_reason}); no decision was taken and no event was "
                "committed [repair: "
                + CONTROLLER_LEDGER_UNREPAIRED_REPAIR.format(
                    ledger=LEDGER_NAME, journal=journal
                )
                + "]"
            )
        return rebuilt.with_repair(CONTROLLER_LEDGER_REPAIRED, observation.ledger_reason)

    def _gathered_evidence(self) -> ControllerEvidence:
        """Perform steps 1, 3, and 4 of the bounded loop, mutating nothing."""

        # Step 1: every authoritative record must be complete and valid before
        # any of its content is treated as authority.  The derived projection is
        # deliberately not validated here: it is rebuilt below from the very
        # events this call has just proved readable, so refusing it first would
        # turn a repairable projection into a dead end.
        self._validated_authority()
        # Step 3: committed events are replayed, the derived ledger and view are
        # checked against that replay rather than read as authority, and anything
        # replay can settle is settled before one decision is derived.
        observation = self._settled_journal()
        metadata = observation.metadata
        history = observation.history
        ledger_revision = observation.ledger_revision
        ledger_reason = observation.ledger_reason
        declared, exported, fault = self._resolved_queue_root(metadata)
        roots = metadata["canonical_roots"]
        if (not roots["planning_root"] or not roots["implementation_roots"]
            or not has_lifecycle_capability(metadata)):
            fault = CONTROLLER_REASON_ROOT_UNDECLARED

        # Step 4: read product-work authority and worker state.
        queue: Optional[QueueObservation] = None
        if fault is None and declared is not None:
            try:
                queue = observe_queue(Path(declared))
            except ControlStoreError as exc:
                # A queue this controller cannot read is reported, never
                # repaired: `cockpit-queue` owns every byte under that root.
                self.queue_fault = str(exc)
                fault = CONTROLLER_REASON_QUEUE_UNREADABLE
        state = fold_mission_state(history.events)
        correlated, uncorrelated = _correlation_counts(state)
        now = _parsed_timestamp(self.as_of, "as_of", "controller tick")
        observations = tuple(
            _observed_slot(state.missions[mission_id], now, self.as_of)
            for mission_id in sorted(state.missions)
        )
        commands, _outcomes = fold_commands(history.events)
        # Detect blocked lifecycle records that report architecture-boundary
        # needs (image, repository, CI/CD, IAM, deployment). A need is blocked
        # only when it is outside the mission's declared boundaries; declared
        # repository roots and declared runtime boundaries are allowed.
        boundary_blocker = None
        if fault is None:
            for mission_id, entry in state.missions.items():
                lifecycle = entry.get("lifecycle", {})
                if lifecycle.get("state") == LIFECYCLE_BLOCKED:
                    blocker = lifecycle.get("blocker")
                    if isinstance(blocker, dict):
                        category = _normalized_architecture_blocker_category(
                            blocker.get("category")
                        )
                        if category is None:
                            continue
                        declared_boundaries = _mission_boundaries(commands, mission_id)
                        detail = blocker.get("detail")
                        declared = _architecture_blocker_is_declared(
                            category,
                            detail if isinstance(detail, str) else None,
                            declared_boundaries,
                        )
                        if not declared:
                            fault = CONTROLLER_REASON_ROOT_UNDECLARED
                            boundary_blocker = {
                                "mission_id": mission_id,
                                "worker_id": lifecycle.get("worker_id"),
                                "queue_item_id": lifecycle.get("queue_item_id"),
                                "trace_id": lifecycle.get("trace_id"),
                                "command_id": _mission_slot_command_id(state, mission_id),
                                "blocker": dict(blocker),
                            }
                            break
        return ControllerEvidence(
            dispatch_phases=_controller_dispatch_phases(history.events, state),
            control_root=str(self.root),
            control_id=metadata["control_id"],
            journal_revision=history.latest_revision,
            ledger_revision=ledger_revision,
            ledger_reason=ledger_reason,
            declared_queue_root=declared,
            exported_queue_root=exported,
            queue_root_fault=fault,
            queue=queue,
            state=state,
            dispatched_pairs=_controller_dispatched_pairs(history.events),
            correlated_records=correlated,
            uncorrelated_records=uncorrelated,
            as_of=self.as_of,
            observations=observations,
            command_ids=tuple(sorted(commands)),
            ledger_repair=observation.repair,
            repaired_from=observation.repaired_from,
            boundary_blocker=boundary_blocker,
            commands=tuple(commands.values()),
        )

    def _validated_authority(self) -> Dict[str, Any]:
        """Validate committed authority, naming the explicit repair if it fails.

        A malformed committed event, a gap, a duplicate revision, or a record
        whose declared identity disagrees with its filename is authoritative
        corruption.  Nothing here rebuilds, reorders, quarantines, or guesses
        past it: the tick stops before it has read one fact from the store, says
        which file is at fault, and names the one explicit repair that exists.
        """

        return validate_control_authority(self.root, CONTROLLER_JOURNAL_REPAIR)

    def _recover(
        self,
        action: ControllerAction,
        evidence: ControllerEvidence,
    ) -> ControllerTickResult:
        """Deliver the one bounded recovery command this tick's rung selected.

        Every rung rides the ordinary managed-command path, so recovery inherits
        durable command IDs, canonical payload digests, idempotent redelivery,
        and the one-slot-per-worker fold unchanged.  `cancel` and `replace` are
        the very records `cockpit-control cancel-mission` and `replace-mission`
        commit, so the ladder has no private way to end a mission that an
        operator's own command does not already have.
        """

        roots = validate_root_metadata(
            _load_json(self.root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME), self.root
        )["canonical_roots"]
        rung = action.recovery_rung
        declarations: Optional[Dict[str, Any]] = None
        if rung in MISSION_RECOVERY_ACTIONS:
            field = MISSION_RECOVERY_PAYLOAD_FIELD
            command_type = MISSION_RECOVERY_COMMAND_TYPES[str(rung)]
            record = build_mission_recovery(
                command_id=str(action.command_id),
                action=str(rung),
                mission_id=str(action.mission_id),
                worker_id=str(action.worker_id),
                queue_item_id=str(action.queue_item_id),
                trace_id=str(action.trace_id),
                reason=LIFECYCLE_STALE_REASON,
                respond_deadline_at=str(action.deadline_at),
                parent_trace_id=action.parent_trace_id,
                evidence_refs=action.evidence_refs,
                requested_at=self.as_of,
            )
        elif rung == CONTROLLER_RECOVERY_CANCEL:
            field = MISSION_CANCELLATION_PAYLOAD_FIELD
            command_type = COMMAND_TYPE_MISSION_CANCEL
            record = build_mission_cancellation(
                command_id=str(action.command_id),
                mission_id=str(action.mission_id),
                worker_id=str(action.worker_id),
                queue_item_id=str(action.queue_item_id),
                trace_id=str(action.trace_id),
                reason=CONTROLLER_CANCEL_REASONS[action.reason],
                acknowledge_deadline_at=str(action.deadline_at),
                parent_trace_id=action.parent_trace_id,
                evidence_refs=action.evidence_refs,
                requested_at=self.as_of,
            )
        else:
            field = MISSION_REPLACEMENT_PAYLOAD_FIELD
            command_type = COMMAND_TYPE_MISSION_REPLACE
            record = build_mission_replacement(
                command_id=str(action.command_id),
                worker_id=str(action.worker_id),
                queue_item_id=str(action.queue_item_id),
                replaced_mission_id=str(action.mission_id),
                replacement_mission_id=str(action.replacement_mission_id),
                trace_id=str(action.trace_id),
                reason=CONTROLLER_REPLACE_REASON,
                parent_trace_id=action.parent_trace_id,
                evidence_refs=action.evidence_refs,
                requested_at=self.as_of,
            )
        envelope = build_command_envelope(
            command_id=str(action.command_id),
            command_type=command_type,
            mission_id=str(action.mission_id),
            queue_item_id=str(action.queue_item_id),
            target_kind=COMMAND_TARGET_WORKER,
            target_id=str(action.worker_id),
            trace_id=str(action.trace_id),
            payload_digest=str(action.payload_digest),
            control_root=str(self.root),
            parent_trace_id=action.parent_trace_id,
            queue_root=roots["queue_root"],
            planning_root=roots["planning_root"],
            implementation_roots=tuple(roots["implementation_roots"]),
            deadline_at=action.deadline_at,
            created_at=self.as_of,
            mission_footprint=next(
                (entry["envelope"]["boundaries"].get("mission_footprint")
                 for entry in evidence.commands
                 if entry["envelope"]["command_id"] == _mission_slot_command_id(evidence.state, str(action.mission_id))),
                None,
            ),
        )
        result = register_mission_command(
            self.root,
            envelope,
            field,
            record,
            actor=CONTROLLER_TICK_ACTOR,
            command=self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
            dry_run=self.dry_run,
            declarations=declarations,
        )
        outcome = CONTROLLER_TICK_WOULD_RECOVER if self.dry_run else CONTROLLER_TICK_RECOVERED
        if not result.applied:
            outcome = CONTROLLER_TICK_UNCHANGED
        return ControllerTickResult(
            root=self.root,
            outcome=outcome,
            action=action,
            evidence=evidence,
            diagnostics=self.diagnostics,
            applied=result.applied,
            fold_outcome=result.outcome,
            controller=dict(result.state.controller) or None,
            publication=result.command.publication,
            command=result.command,
            conflict=result.recorded_conflict,
            queue_fault=self.queue_fault,
            dry_run=self.dry_run,
        )

    def _dispatch(
        self,
        action: ControllerAction,
        evidence: ControllerEvidence,
    ) -> ControllerTickResult:
        """Persist the one dispatch command this tick selected, exactly once."""

        with (nullcontext(None) if self.dry_run else PortableControlLock(
            self.root, self.command, timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        )) as lock:
            with (nullcontext() if self.dry_run else queue_lock(evidence.declared_queue_root)):
                return self._dispatch_locked(action, evidence, lock)

    def _dispatch_locked(
        self,
        action: ControllerAction,
        evidence: ControllerEvidence,
        lock: Optional[PortableControlLock],
    ) -> ControllerTickResult:
        self._validated_authority()
        observation = self._observed_journal()
        if not observation.settled:
            observation = self._repaired_projection(observation, lock)
        metadata = observation.metadata
        roots = metadata["canonical_roots"]
        if (not roots["planning_root"] or not roots["implementation_roots"]
            or not has_lifecycle_capability(metadata)):
            raise ControlStoreError("operationally-blocked: dispatch requires declared lifecycle capability and boundaries")
        state = fold_mission_state(observation.history.events)
        dispatch_phases = _controller_dispatch_phases(observation.history.events, state)
        commands, _outcomes = fold_commands(observation.history.events)
        stored_command = commands.get(str(action.command_id))
        declared, _exported, fault = self._resolved_queue_root(metadata)
        if fault is not None or declared is None:
            raise ControlStoreError("dispatch queue authority changed; rerun tick; no command was committed")
        queue = observe_queue(Path(declared))
        item = next((item for item in queue.active if item.item_id == action.queue_item_id), None)
        if item is not None:
            if _terminal_phase_mission(item, state, dispatch_phases) is not None:
                raise ControlStoreError(
                    "dispatch refused: queue phase already has terminal lifecycle; "
                    "explicit recovery/replacement or a new queue mission is required"
                )
            _require_footprint_authority(item.footprint, roots)
        candidate = _fifo_candidate(queue, state, dispatch_phases)
        if (
            queue.paused or item is None or candidate is None
            or candidate.item_id != action.queue_item_id
            or item.worker_id != action.worker_id
            or dispatch_payload_digest(
                item, str(action.mission_id), str(action.worker_id), str(action.trace_id)
            ) != action.payload_digest
            or _scope_conflict(item, state, commands) is not None
            or any(footprints_conflict(item.footprint, other.footprint)
                   for other in queue.active if other.item_id != item.item_id)
        ):
            raise ControlStoreError("dispatch queue evidence changed; rerun tick; no command was committed")

        existing = state.worker_slots.get(str(action.worker_id))
        same_reservation = (
            existing is not None
            and existing["state"] == MISSION_SLOT_RESERVED
            and existing["mission_id"] == action.mission_id
            and existing["command_id"] == action.command_id
            and existing["queue_item_id"] == action.queue_item_id
        )
        replacement_reservation = (
            existing is not None and existing["state"] == MISSION_SLOT_RESERVED
            and existing["mission_id"] == action.mission_id and existing["replaces"] is not None
            and commands.get(existing["command_id"], {}).get("status") == COMMAND_APPLIED
            and commands[existing["command_id"]]["envelope"]["command_type"] == COMMAND_TYPE_MISSION_REPLACE
        )
        if (
            any(
                (holder == action.worker_id or slot["queue_item_id"] == action.queue_item_id)
                and not (holder == action.worker_id and (same_reservation or replacement_reservation))
                for holder, slot in state.claimed_slots
            )
            or state.contested_slots
            or (action.mission_id in state.missions and
                state.missions[action.mission_id]["lifecycle"]["state"] != LIFECYCLE_PENDING_DISPATCH)
            or (stored_command is not None and (
                not same_reservation or stored_command["status"] != COMMAND_STATUS_REGISTERED
                or stored_command["conflicts"]
            ))
        ):
            raise ControlStoreError("dispatch claim changed; rerun tick; no command was committed")
        owner = stored_command
        if replacement_reservation:
            owner = commands[existing["command_id"]]
        if owner is not None and owner["envelope"]["boundaries"].get("mission_footprint") != item.footprint:
            raise ControlStoreError("dispatch immutable footprint changed; explicit recovery required")

        # Concurrent initial ticks derive the same identity, not the same
        # clock. Only the first publication owns the immutable time window.
        if stored_command is not None:
            action = replace(action, deadline_at=stored_command["envelope"]["deadline_at"])
        if action.deadline_at is None or _parsed_timestamp(
            self.as_of, "as_of", "tick"
        ) >= _parsed_timestamp(action.deadline_at, "deadline_at", "dispatch"):
            raise ControlStoreError(
                "dispatch acceptance deadline changed or expired; rerun tick; no command was committed"
            )
        envelope = build_command_envelope(
            command_id=str(action.command_id),
            command_type=COMMAND_TYPE_MISSION_DISPATCH,
            mission_id=str(action.mission_id),
            queue_item_id=str(action.queue_item_id),
            target_kind=COMMAND_TARGET_WORKER,
            target_id=str(action.worker_id),
            trace_id=str(action.trace_id),
            payload_digest=str(action.payload_digest),
            control_root=str(self.root),
            queue_root=roots["queue_root"],
            planning_root=roots["planning_root"],
            implementation_roots=tuple(roots["implementation_roots"]),
            mission_footprint=item.footprint,
            deadline_at=action.deadline_at,
            created_at=(
                self.as_of
                if stored_command is None
                else stored_command["envelope"]["created_at"]
            ),
        )
        if (
            same_reservation
            and stored_command is not None
            and _command_conflict_reason(stored_command["envelope"], envelope) is None
        ):
            return ControllerTickResult(
                root=self.root,
                outcome=CONTROLLER_TICK_UNCHANGED,
                action=action,
                evidence=evidence,
                diagnostics=self.diagnostics,
                applied=False,
                fold_outcome=MISSION_FOLD_RETAINED_DUPLICATE,
                controller=dict(state.controller) or None,
                publication=None,
                command=None,
                conflict=None,
                queue_fault=self.queue_fault,
                dry_run=self.dry_run,
            )
        record = build_controller_dispatch(
            command_id=str(action.command_id),
            mission_id=str(action.mission_id),
            worker_id=str(action.worker_id),
            queue_item_id=str(action.queue_item_id),
            trace_id=str(action.trace_id),
            reason=action.reason,
            state_key=action.state_key,
            evidence_refs=action.evidence_refs,
            decided_at=self.as_of,
            queue_item_state=item.state,
        )
        result = register_mission_command(
            self.root,
            envelope,
            CONTROLLER_DISPATCH_PAYLOAD_FIELD,
            record,
            actor=CONTROLLER_TICK_ACTOR,
            command=self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
            dry_run=self.dry_run,
            declarations={
                "active_queue_item_id": action.queue_item_id,
                "active_mission_id": action.mission_id,
            },
            held_lock=lock,
        )
        outcome = CONTROLLER_TICK_WOULD_DISPATCH if self.dry_run else CONTROLLER_TICK_DISPATCHED
        if not result.applied:
            # The command is durably committed either way; only the fold decides
            # whether it also claimed the worker's single mission slot.
            outcome = CONTROLLER_TICK_UNCHANGED
        return ControllerTickResult(
            root=self.root,
            outcome=outcome,
            action=action,
            evidence=evidence,
            diagnostics=self.diagnostics,
            applied=result.applied,
            fold_outcome=result.outcome,
            controller=dict(result.state.controller) or None,
            publication=result.command.publication,
            command=result.command,
            conflict=result.recorded_conflict,
            queue_fault=self.queue_fault,
            dry_run=self.dry_run,
        )

    def _observe(
        self,
        action: ControllerAction,
        evidence: ControllerEvidence,
    ) -> ControllerTickResult:
        """Persist the observation or escalation state this tick reconciled to."""

        record = build_controller_observation(
            outcome=action.outcome,
            reason=action.reason,
            state_key=action.state_key,
            queue_item_id=action.queue_item_id,
            worker_id=action.worker_id,
            mission_id=action.mission_id,
            command_id=action.command_id,
            evidence_refs=action.evidence_refs,
            observed_at=self.as_of,
        )
        acceptance_block = action.reason in (
            CONTROLLER_REASON_ACCEPTANCE_EXPIRED, CONTROLLER_REASON_ACCEPTANCE_UNSUPPORTED,
        )
        with (PortableControlLock(
            self.root, self.command, timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
        ) if acceptance_block and not self.dry_run else nullcontext(None)) as lock:
            if lock is not None:
                _metadata, history = inspect_control_events(self.root)
                current = fold_mission_state(history.events)
                slot = current.worker_slots.get(str(action.worker_id))
                if (
                    slot is None or slot["state"] != MISSION_SLOT_RESERVED
                    or slot["mission_id"] != action.mission_id
                    or slot["command_id"] != action.command_id
                    or (action.mission_id in current.missions and
                        current.missions[action.mission_id]["lifecycle"]["state"] != LIFECYCLE_PENDING_DISPATCH)
                ):
                    raise ControlStoreError(
                        "dispatch reservation changed during acceptance observation; "
                        "rerun tick; no blocked observation was committed"
                    )
                if current.controller.get("state_key") == action.state_key:
                    return ControllerTickResult(
                        root=self.root, outcome=CONTROLLER_TICK_UNCHANGED,
                        action=action, evidence=evidence, diagnostics=self.diagnostics,
                        applied=False, fold_outcome=CONTROLLER_FOLD_RETAINED_UNCHANGED,
                        controller=dict(current.controller), publication=None,
                        command=None, conflict=None, queue_fault=self.queue_fault, dry_run=False,
                    )
            publication = publish_control_event(
                self.root,
                f"{CONTROLLER_OBSERVATION_EVENT_PREFIX}{action.outcome}",
                actor=CONTROLLER_TICK_ACTOR,
                payload={CONTROLLER_OBSERVATION_PAYLOAD_FIELD: record},
                command=self.command,
                timeout_seconds=self.timeout_seconds,
                poll_seconds=self.poll_seconds,
                dry_run=self.dry_run,
                held_lock=lock,
            )
        state = fold_mission_state(_committed_events_after_publication(publication))
        applied, fold_outcome = state.mission_outcomes.get(
            publication.event_id, (False, CONTROLLER_FOLD_RETAINED_UNCHANGED)
        )
        outcome = CONTROLLER_TICK_WOULD_RECORD if self.dry_run else CONTROLLER_TICK_RECORDED
        if not applied:
            outcome = CONTROLLER_TICK_UNCHANGED
        # Manage escalation records for repeated blocking observations.
        # Let errors propagate so callers fail-closed if persistence fails.
        self._manage_escalation_record(action, evidence)

        return ControllerTickResult(
            root=self.root,
            outcome=outcome,
            action=action,
            evidence=evidence,
            diagnostics=self.diagnostics,
            applied=applied,
            fold_outcome=fold_outcome,
            controller=dict(state.controller) or None,
            publication=publication,
            command=None,
            conflict=None,
            queue_fault=self.queue_fault,
            dry_run=self.dry_run,
        )

    def _manage_escalation_record(self, action: ControllerAction, evidence: ControllerEvidence) -> None:
        """Create or update an escalation record for repeated blocking observations.

        This minimal mechanism tracks consecutive blocked observations that
        share the same controller state key and mission, and persists an
        escalation record under ESCALATIONS_DIR_NAME. The schema is the
        validated minimal one plus free-form fields for evidence, impact,
        attempted recovery, options, and the pending human decision so tests
        can assert their presence. This is intentionally small and auditable.
        """

        # Only mission-scoped bounded-recovery escalation is tracked here.
        if (
            not action.blocking
            or action.reason != CONTROLLER_REASON_RECOVERY_ESCALATED
            or not action.mission_id
            or not action.queue_item_id
        ):
            return

        # Load control identity to validate any existing escalation files.
        metadata = validate_root_metadata(
            _load_json(self.root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME), self.root
        )
        control_id = metadata["control_id"]
        esc_dir = self.root / ESCALATIONS_DIR_NAME
        try:
            esc_dir.mkdir(exist_ok=True)
        except OSError:
            return

        # Find existing escalations for this mission only.
        matches = []
        try:
            for entry in sorted(esc_dir.iterdir(), key=lambda e: e.name):
                if entry.name.startswith('.') or not entry.name.endswith('.json'):
                    continue
                try:
                    doc = _load_json(entry, f"{ESCALATIONS_DIR_NAME}/{entry.name}")
                except ControlStoreError:
                    continue
                if doc.get("control_id") != control_id:
                    continue
                if doc.get("mission_id") != action.mission_id:
                    continue
                if doc.get("state_key") != action.state_key:
                    continue
                matches.append((entry, doc))
        except OSError:
            matches = []

        # Determine new count and status.
        count = len(matches)
        from uuid import uuid4
        now = utc_timestamp()

        if count == 0:
            # First escalation record for this blocked situation.
            escalation_id = str(uuid4())
            record = {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "record_type": "escalation",
                "escalation_id": escalation_id,
                "control_id": control_id,
                "mission_id": action.mission_id,
                "queue_item_id": action.queue_item_id,
                "status": "raised",
                "created_at": now,
                "state_key": action.state_key,
                "evidence_refs": list(action.evidence_refs),
                "impact": {"worker_id": action.worker_id, "queue_item_id": action.queue_item_id},
                "attempted_recovery": [],
                "options": ["cancel-mission", "replace-mission"],
                "pending_decision": "decide mission",
                "count": 1,
            }
            _write_json(esc_dir / f"{escalation_id}.json", record)
            _fsync_directory(esc_dir)
        else:
            # Update the latest matching escalation: increment count and progress
            # the status: raised -> escalated -> decision-requested.
            entry, doc = matches[-1]
            updated = dict(doc)
            updated_count = (updated.get("count") or 0) + 1
            updated["count"] = updated_count
            if updated.get("status") == "raised":
                updated["status"] = "escalated"
                updated.setdefault("attempted_recovery", []).append({
                    "when": now,
                    "action": "troubleshoot",
                    "evidence_refs": list(action.evidence_refs),
                })
            else:
                updated["status"] = "decision-requested"
                updated.setdefault("attempted_recovery", []).append({
                    "when": now,
                    "action": "escalation-recorded",
                    "evidence_refs": list(action.evidence_refs),
                })
            # Overwrite the file atomically.
            tmp_path = entry.with_suffix(".json.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(updated, indent=2, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp_path), str(entry))
            _fsync_directory(esc_dir)


    def _commit(
        self,
        action: ControllerAction,
        evidence: ControllerEvidence,
    ) -> ControllerTickResult:
        """Perform the at most one action a tick may take, and refuse a second.

        This is the only place in the module that can turn a controller decision
        into a committed event, and it can run once per tick.  A second call --
        from a future edit, an extra branch, or any other surface reaching in --
        is refused before it can publish anything.
        """

        if self._acted:
            raise ControlStoreError(
                "a controller tick takes at most one state-changing action; this tick "
                "already acted"
            )
        self._acted = True
        if action.kind == CONTROLLER_ACTION_DISPATCH:
            return self._dispatch(action, evidence)
        if action.kind == CONTROLLER_ACTION_RECOVER:
            return self._recover(action, evidence)
        if action.kind == CONTROLLER_ACTION_OBSERVE:
            return self._observe(action, evidence)
        if (
            not self.dry_run
            and action.blocking
            and action.reason == CONTROLLER_REASON_RECOVERY_ESCALATED
            and action.mission_id is not None
        ):
            # Escalation bookkeeping advances on recurrent identical blocked
            # ticks without requiring duplicate controller-observation events.
            self._manage_escalation_record(action, evidence)
        return ControllerTickResult(
            root=self.root,
            outcome=CONTROLLER_TICK_UNCHANGED,
            action=action,
            evidence=evidence,
            diagnostics=self.diagnostics,
            applied=False,
            fold_outcome=CONTROLLER_FOLD_RETAINED_UNCHANGED,
            controller=evidence.controller,
            publication=None,
            command=None,
            conflict=None,
            queue_fault=self.queue_fault,
            dry_run=self.dry_run,
        )

    def run(self) -> ControllerTickResult:
        """Run the bounded loop once and exit, whatever it found."""

        evidence = self._gathered_evidence()
        return self._commit(select_controller_action(evidence), evidence)


def controller_tick(
    root: Path,
    diagnostics: Sequence[ControllerDiagnostic] = (),
    as_of: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
    command: str = DEFAULT_CONTROLLER_TICK_COMMAND,
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> ControllerTickResult:
    """Run one deterministic controller tick over one explicit control root."""

    return ControllerTick(
        root,
        diagnostics=diagnostics,
        as_of=as_of,
        environ=environ,
        command=command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        dry_run=dry_run,
    ).run()


def _precedence_verdicts(result: ControllerTickResult) -> Dict[int, str]:
    """Render one closed-vocabulary verdict for each ADR-014 precedence rule.

    Every value is a validated identifier, an integer, a closed token, or `-`.
    Nothing an operator, a worker, or a pane wrote is rendered verbatim, so a
    verdict line cannot be forged into an extra line or an extra field.
    """

    evidence = result.evidence
    queue = evidence.queue
    slots = evidence.state.claimed_slots
    only_slot = slots[0] if len(slots) == 1 else None
    if queue is None:
        human = f"queue-pause {CONTROLLER_PANE_UNOBSERVED}"
        product = f"queue {CONTROLLER_PANE_UNOBSERVED}"
    else:
        active = queue.active
        only_item = active[0] if len(active) == 1 else None
        human = f"queue-pause {'yes' if queue.paused else 'no'}"
        product = (
            f"items {len(queue.items)} active {len(active)} queued {len(queue.queued)} "
            f"item {only_item.item_id if only_item else '-'} "
            f"state {only_item.state if only_item else '-'}"
        )
    return {
        1: human,
        2: product,
        3: (
            f"claimed-slots {len(slots)} "
            f"worker {only_slot[0] if only_slot else '-'} "
            f"mission {only_slot[1]['mission_id'] if only_slot else '-'} "
            f"slot-state {only_slot[1]['state'] if only_slot else '-'} "
            f"stale {len(evidence.stale_missions)} "
            f"contested {len(evidence.state.contested_slots)}"
        ),
        4: f"revision {evidence.journal_revision}",
        5: (
            f"revision "
            f"{evidence.ledger_revision if evidence.ledger_revision is not None else '-'} "
            f"{evidence.ledger_reason} repair {evidence.ledger_repair}"
        ),
        6: (
            f"correlated {evidence.correlated_records} "
            f"uncorrelated {evidence.uncorrelated_records}"
        ),
    }


def controller_precedence_lines(result: ControllerTickResult) -> List[str]:
    """Render the ADR-014 ladder the tick applied, rule by rule, in order.

    Rules 7 and 8 are printed with `advances-state no` on every line.  They are
    reported because an operator needs to see what the controller saw; they are
    marked because nothing they say ever changed what it did.
    """

    verdicts = _precedence_verdicts(result)
    lines: List[str] = []
    for rule, name, role in CONTROLLER_PRECEDENCE:
        if rule in CONTROLLER_DIAGNOSTIC_RULES:
            continue
        lines.append(f"precedence {rule} {name} {role} {verdicts[rule]}")
    for rule, name, role in CONTROLLER_PRECEDENCE:
        if rule not in CONTROLLER_DIAGNOSTIC_RULES:
            continue
        if not result.diagnostics:
            lines.append(
                f"precedence {rule} {name} {role} - {CONTROLLER_PANE_UNOBSERVED} "
                "advances-state no"
            )
            continue
        for diagnostic in result.diagnostics:
            observed = diagnostic.live_status if rule == 7 else diagnostic.pane_status
            lines.append(
                f"precedence {rule} {name} {role} {diagnostic.worker_id} {observed} "
                "advances-state no"
            )
    return lines


def controller_tick_lines(result: ControllerTickResult) -> List[str]:
    """Render the decision, its evidence ladder, and what was persisted."""

    action = result.action
    lines = [
        f"tick {result.outcome} action {action.kind} outcome {action.outcome} "
        f"reason {action.reason} state-key {action.state_key} "
        f"journal-revision {result.evidence.journal_revision} "
        f"events-committed {result.committed_events} as-of {result.evidence.as_of}"
    ]
    lines.extend(controller_precedence_lines(result))
    evidence = result.evidence
    observed_revision = evidence.ledger_revision
    lines.append(
        f"ledger repair {evidence.ledger_repair} "
        f"from {evidence.repaired_from or '-'} "
        f"ledger-revision {observed_revision if observed_revision is not None else '-'} "
        f"journal-revision {evidence.journal_revision}"
    )
    for observed in evidence.stale_missions:
        lines.append(
            f"stale-mission {observed.mission_id} worker {observed.worker_id} "
            f"queue-item {observed.queue_item_id} state {observed.state} "
            f"reason {observed.reason or '-'} recovery {observed.recovery or '-'} "
            f"fresh-until {observed.fresh_until or '-'} as-of {observed.as_of}"
        )
    for worker_id, slot, conflict in evidence.state.contested_slots:
        lines.append(
            f"mission-contest {worker_id} retained {slot['mission_id']} "
            f"refused {conflict['mission_id']} reason {conflict['reason']} "
            f"source {conflict['source']} revision {conflict['revision']}"
        )
    if action.kind == CONTROLLER_ACTION_DISPATCH:
        lines.append(
            f"dispatch command {action.command_id} worker {action.worker_id} "
            f"mission {action.mission_id} queue-item {action.queue_item_id} "
            f"trace {action.trace_id} digest {action.payload_digest}"
        )
    if action.kind == CONTROLLER_ACTION_RECOVER:
        lines.append(
            f"recovery rung {action.recovery_rung} command {action.command_id} "
            f"worker {action.worker_id} mission {action.mission_id} "
            f"queue-item {action.queue_item_id} trace {action.trace_id} "
            f"parent-trace {action.parent_trace_id or '-'} "
            f"replacement {action.replacement_mission_id or '-'} "
            f"deadline {action.deadline_at or '-'} digest {action.payload_digest}"
        )
    controller = result.controller
    if controller is not None:
        lines.append(
            f"controller outcome {controller['outcome']} reason {controller['reason']} "
            f"state-key {controller['state_key']} "
            f"queue-item {controller['queue_item_id'] or '-'} "
            f"worker {controller['worker_id'] or '-'} "
            f"mission {controller['mission_id'] or '-'} "
            f"command {controller['command_id'] or '-'} "
            f"revision {controller['revision']} at {controller['recorded_at']}"
        )
    if result.conflict is not None:
        lines.append(_mission_conflict_line(str(action.worker_id), result.conflict))
    return lines


def _contested_duplicate(
    evidence: ControllerEvidence, worker_id: Optional[str]
) -> Optional[Mapping[str, Any]]:
    """Return the live duplicate claim one worker's contest refused, if any.

    A refusal has to name the mission an operator is being asked to end, and it
    must be the one the tick is actually blocking on, so it is read back off the
    same contested slot the decision was taken from rather than assembled from
    anything a surface reported.
    """

    for contested_worker, _slot, conflict in evidence.state.contested_slots:
        if contested_worker == worker_id:
            return conflict
    return None


def worker_lifecycle_cancellation_route(state: Optional[str]) -> Tuple[str, ...]:
    """Return the lifecycle states a worker must record to reach `cancelled`.

    The architecture section 9 table is not fully connected: `cancelled` has no
    edge from `accepted`, so a mission a worker has only accepted has to be
    recorded `running` before it can be cancelled at all.  The route is walked
    over the same table the fold enforces, shortest first, so a repair sentence
    can never name a step the fold will retain for audit and refuse to apply.
    """

    if state is None or state in WORKER_LIFECYCLE_TERMINAL_STATES:
        return ()
    frontier: List[Tuple[str, Tuple[str, ...]]] = [(state, ())]
    seen = {state}
    while frontier:
        current, route = frontier.pop(0)
        for successor in WORKER_LIFECYCLE_TRANSITIONS.get(current, ()):
            if successor == LIFECYCLE_CANCELLED:
                return route + (successor,)
            if successor in seen:
                continue
            seen.add(successor)
            frontier.append((successor, route + (successor,)))
    return ()


def controller_duplicate_repair_route(state: Optional[str]) -> str:
    """Say exactly which lifecycle events end one duplicate claim, in order."""

    route = worker_lifecycle_cancellation_route(state)
    if not route:
        return "the terminal lifecycle event that ends it"
    if len(route) == 1:
        return f"the terminal lifecycle event `{route[0]}`"
    return (
        "lifecycle " + ", then ".join(f"`{step}`" for step in route)
        + f", because `{route[-1]}` is not reachable from `{state}` in one step"
    )


def controller_blocking_repair(result: ControllerTickResult) -> str:
    """Return the exact repair the operator must perform to unblock this tick."""

    evidence = result.evidence
    action = result.action
    template = CONTROLLER_BLOCKING_REPAIRS.get(action.reason, PREFLIGHT_COMMAND)
    conflict = _contested_duplicate(evidence, action.worker_id)
    duplicate = None if conflict is None else str(conflict["mission_id"])
    materialized = None if duplicate is None else evidence.state.missions.get(duplicate)
    return template.format(
        declared=evidence.declared_queue_root or "-",
        exported=evidence.exported_queue_root or "-",
        worker=action.worker_id or "-",
        command=action.command_id or "-",
        mission=action.mission_id or "-",
        queue_item=action.queue_item_id or "-",
        duplicate=duplicate or "-",
        terminal=controller_duplicate_repair_route(
            None if materialized is None else materialized["lifecycle"]["state"]
        ),
    )


def report_controller_tick(
    result: ControllerTickResult,
    prefix: str = CONTROLLER_TICK_ACTOR,
) -> int:
    """Print the tick payload on stdout and any refusal on stderr, then exit.

    A tick that decided nothing new is a success: it terminated cleanly with
    durable state, which is exactly what a recurrent wake must do.  A blocked
    tick is a refusal to guess, so it names the exact repair and exits non-zero.
    """

    verb = "would take" if result.dry_run else "took"
    print(
        f"{prefix}: tick {verb} action {result.action.kind} "
        f"({result.action.outcome}/{result.action.reason}) in {result.root}"
    )
    for line in controller_tick_lines(result):
        print(line)
    if result.queue_fault is not None:
        print(
            f"{prefix}: the declared queue root cannot be read: {result.queue_fault}",
            file=os.sys.stderr,
        )
    if result.blocked:
        print(
            f"{prefix}: dispatch is blocked ({result.action.reason}); one conflict "
            f"observation is recorded and no mission state changed "
            f"[repair: {controller_blocking_repair(result)}]",
            file=os.sys.stderr,
        )
        return 1
    if result.action.kind == CONTROLLER_ACTION_RECOVER and not result.applied:
        if result.fold_outcome == MISSION_FOLD_RETAINED_DUPLICATE:
            # One rung owns exactly one command ID per staleness episode, so a
            # concurrent tick reaching the same rung redelivers that command
            # instead of adding a second one; the stored result stands.
            print(
                f"{prefix}: the {result.action.recovery_rung} recovery of mission "
                f"{result.action.mission_id} is a redelivery of command "
                f"{result.action.command_id}; the stored request stands and no second "
                "recovery was created"
            )
            return 0
        print(
            f"{prefix}: the {result.action.recovery_rung} recovery command is committed "
            f"but did not move worker {result.action.worker_id}'s mission slot "
            f"({result.fold_outcome}); no mission state changed",
            file=os.sys.stderr,
        )
        return 1
    if result.action.kind == CONTROLLER_ACTION_DISPATCH and not result.applied:
        if result.command is not None and result.command.conflicted:
            print(
                f"{prefix}: the dispatch command {result.action.command_id} conflicts "
                "with the stored command envelope; the conflict is durable and no "
                "brief was delivered",
                file=os.sys.stderr,
            )
            return 1
        if result.fold_outcome == MISSION_FOLD_RETAINED_DUPLICATE:
            # Retrying an uncertain delivery of the same command ID is the
            # architecture section 17 rule, not a failure: the stored dispatch is
            # returned and the worker keeps the one mission it already has.
            print(
                f"{prefix}: the dispatch of mission {result.action.mission_id} to worker "
                f"{result.action.worker_id} is a redelivery of command "
                f"{result.action.command_id}; the stored dispatch stands and no second "
                "mission was created"
            )
            return 0
        print(
            f"{prefix}: the dispatch command is committed but did not claim worker "
            f"{result.action.worker_id}'s mission slot ({result.fold_outcome}); no "
            "second mission was created",
            file=os.sys.stderr,
        )
        return 1
    return 0
