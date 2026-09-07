#!/usr/bin/env python3
"""Versioned control-store root resolution, schema validation, and durability.

This module is deliberately dependency-free so installed cockpit commands can
share one fail-closed definition of the VP3 control-store boundary, one portable
control-lock protocol, one immutable event-publication protocol, one versioned
worker lifecycle vocabulary with monotonic sequence and freshness semantics, one
versioned command envelope and acknowledgement protocol whose redelivery is
idempotent and whose identifier reuse is a recorded conflict, one managed
mission-control layer in which questions, replies, access-prompt responses,
cooperative cancellations, and replacements are correlated records carried by
those same command envelopes and folded into one mission slot per worker, one
read-only readiness diagnosis, and one explicit guarded repair that never
deletes anything.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from uuid import UUID, uuid4, uuid5

import cockpit_control_locks as control_locks
import cockpit_control_journal as control_journal
import cockpit_control_projection as control_projection
import cockpit_control_lifecycle as control_lifecycle
import cockpit_control_commands as control_commands
import cockpit_control_mission_control as control_mission_control
import cockpit_control_controller as control_controller
import cockpit_control_wake as control_wake
import cockpit_control_root_schema as control_root_schema
import cockpit_control_cli as control_cli
import cockpit_control_rendering as control_rendering
import cockpit_control_queue_adapter as control_queue_adapter
import cockpit_control_tmux_adapter as control_tmux_adapter

CONTROL_SCHEMA_VERSION = control_root_schema.CONTROL_SCHEMA_VERSION
CONTROL_METADATA_NAME = control_root_schema.CONTROL_METADATA_NAME
LEDGER_NAME = control_root_schema.LEDGER_NAME
EVENTS_NAME = control_root_schema.EVENTS_NAME
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
CONTROL_GUARD_NAME = control_locks.CONTROL_GUARD_NAME
CONTROL_LOCK_NAME = control_locks.CONTROL_LOCK_NAME
LOCK_OWNER_NAME = control_locks.LOCK_OWNER_NAME
LOCK_CANDIDATE_PREFIX = control_locks.LOCK_CANDIDATE_PREFIX
LOCK_RELEASED_PREFIX = control_locks.LOCK_RELEASED_PREFIX
LOCK_REPAIRED_PREFIX = control_locks.LOCK_REPAIRED_PREFIX
DEFAULT_LOCK_TIMEOUT_SECONDS = control_locks.DEFAULT_LOCK_TIMEOUT_SECONDS
DEFAULT_LOCK_POLL_SECONDS = control_locks.DEFAULT_LOCK_POLL_SECONDS

# Owner-fate classification.  Only LOCK_OWNER_DEAD is positive proof that the
# publisher of a lock can no longer be running; everything else is refused by
# automatic repair.
LOCK_OWNER_DEAD = control_locks.LOCK_OWNER_DEAD
LOCK_OWNER_ALIVE = control_locks.LOCK_OWNER_ALIVE
LOCK_OWNER_UNPROVEN = control_locks.LOCK_OWNER_UNPROVEN

# Guarded stale-lock repair outcomes.
LOCK_REPAIR_ABSENT = control_locks.LOCK_REPAIR_ABSENT
LOCK_REPAIR_QUARANTINED = control_locks.LOCK_REPAIR_QUARANTINED
LOCK_REPAIR_WOULD_QUARANTINE = control_locks.LOCK_REPAIR_WOULD_QUARANTINE

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

# --- versioned worker lifecycle vocabulary (ADR-013, architecture section 9) --

# The worker lifecycle contract is versioned independently of the control-store
# schema so a worker speaking an older or newer vocabulary is refused explicitly
# instead of being partially understood.
WORKER_LIFECYCLE_SCHEMA_VERSION = 1
WORKER_LIFECYCLE_RECORD_TYPE = "worker-lifecycle"
# One committed event carries at most one lifecycle record, always under this
# payload field, and its event type is derived from the declared state.  Neither
# half can exist without the other, so a lifecycle record cannot be smuggled in
# under an unrelated event type and a lifecycle event type cannot be empty.
WORKER_LIFECYCLE_PAYLOAD_FIELD = "worker_lifecycle"
WORKER_LIFECYCLE_EVENT_PREFIX = "worker-lifecycle-"

# The materialized state a mission holds before any lifecycle event applies.
LIFECYCLE_PENDING_DISPATCH = "pending-dispatch"
LIFECYCLE_ACCEPTED = "accepted"
LIFECYCLE_RUNNING = "running"
LIFECYCLE_BLOCKED = "blocked"
LIFECYCLE_COMPLETED = "completed"
LIFECYCLE_FAILED = "failed"
LIFECYCLE_CANCELLED = "cancelled"
LIFECYCLE_REPLACED = "replaced"

# The complete, closed lifecycle vocabulary a worker may emit.
WORKER_LIFECYCLE_STATES = (
    LIFECYCLE_ACCEPTED,
    LIFECYCLE_RUNNING,
    LIFECYCLE_BLOCKED,
    LIFECYCLE_COMPLETED,
    LIFECYCLE_FAILED,
    LIFECYCLE_CANCELLED,
    LIFECYCLE_REPLACED,
)
# Active states carry a heartbeat and an explicit freshness deadline; terminal
# states carry neither, because nothing is expected to refresh them again.
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
# States that must explain themselves: a mission never blocks or ends silently.
WORKER_LIFECYCLE_REASON_REQUIRED = (
    LIFECYCLE_BLOCKED,
    LIFECYCLE_FAILED,
    LIFECYCLE_CANCELLED,
    LIFECYCLE_REPLACED,
)

# Architecture section 9 transition table.  Re-emitting the same active state is
# the heartbeat-refresh path, so freshness is renewed through the same versioned
# record rather than a second, divergent one.  Terminal states have no successor.
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

# The complete, closed field set of one lifecycle record.  Every field is always
# present, so a partially written record is malformed rather than defaulted.
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

# Materialized worker mission slots live in the derived ledger under this field.
LEDGER_WORKER_MISSIONS_FIELD = "worker_missions"
LEDGER_WORKER_MISSION_FIELDS = ("lifecycle", "revision", "event_id", "recorded_at")

# How the deterministic fold treated one committed lifecycle event.  Every
# committed event is retained forever; these outcomes only say whether it also
# moved the materialized mission state.
LIFECYCLE_APPLIED = "applied"
LIFECYCLE_RETAINED_TERMINAL = "retained-terminal-state"
LIFECYCLE_RETAINED_STALE_SEQUENCE = "retained-stale-sequence"
LIFECYCLE_RETAINED_INVALID_TRANSITION = "retained-invalid-transition"
LIFECYCLE_RETAINED_UNMATCHED = "retained-unmatched-correlation"

# Freshness observations.  An observation says what the control plane can
# currently see; it is deliberately not a mission state and never becomes one.
LIFECYCLE_OBSERVATION_FRESH = "fresh"
LIFECYCLE_OBSERVATION_STALE = "stale"
LIFECYCLE_OBSERVATION_TERMINAL = "terminal"
# Expiry is a recoverable observation with an explicit reason.  It is never
# `failed`: only a worker event or an explicit decision ends a mission.
LIFECYCLE_STALE_REASON = "heartbeat-expired"
LIFECYCLE_STALE_RECOVERY = "awaiting-bounded-recovery"

DEFAULT_LIFECYCLE_COMMAND = "cockpit-control record-lifecycle"
DEFAULT_LIFECYCLE_STATUS_COMMAND = "cockpit-control lifecycle-status"


# --- versioned mission commands and acknowledgements (ADR-013, section 10) ---

# The command contract is versioned independently of the control-store schema
# and of the worker lifecycle vocabulary, so a controller or worker speaking an
# older or newer command protocol is refused explicitly instead of being
# partially understood.
COMMAND_SCHEMA_VERSION = 1
COMMAND_ENVELOPE_RECORD_TYPE = "command-envelope"
COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE = "command-acknowledgement"
# One committed event carries at most one command record, always under one of
# these payload fields, and its event type is fixed by that record.  Neither
# half can exist without the other, so a command record cannot be smuggled in
# under an unrelated event type and a command event type cannot be empty.
COMMAND_ENVELOPE_PAYLOAD_FIELD = "command_envelope"
COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD = "command_acknowledgement"
COMMAND_REGISTERED_EVENT_TYPE = "command-registered"
COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX = "command-acknowledged-"

# A payload digest is computed over one canonical serialization, so the same
# logical payload always yields the same digest in every process and any
# difference in the payload yields a different one.
COMMAND_DIGEST_ALGORITHM = "sha256"
COMMAND_DIGEST_PREFIX = f"{COMMAND_DIGEST_ALGORITHM}:"
COMMAND_DIGEST_HEX_DIGITS = 64
COMMAND_DIGEST_HEX_ALPHABET = "0123456789abcdef"

# A command names exactly one target: the worker that must apply it, or the
# queue surface it addresses.  The vocabulary is closed so an unknown target
# kind is refused rather than delivered somewhere unintended.
COMMAND_TARGET_WORKER = "worker"
COMMAND_TARGET_QUEUE = "queue"
COMMAND_TARGET_KINDS = (COMMAND_TARGET_WORKER, COMMAND_TARGET_QUEUE)
COMMAND_TARGET_FIELDS = ("kind", "id")

# ADR-016 mission boundaries: every command declares the roots it may work in
# and the runtime boundaries it may not cross.  US2 persists the declaration on
# the envelope; enforcing it against observed evidence is a later story.
COMMAND_BOUNDARY_FIELDS = (
    "control_root",
    "queue_root",
    "planning_root",
    "implementation_roots",
    "runtime_boundaries",
)

# The complete, closed field set of one command envelope.  Every field is always
# present, so a partially written envelope is malformed rather than defaulted.
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
# One command ID always describes exactly one command.  Everything except the
# delivery timestamp is therefore part of command identity: a redelivery that
# changes any of it is a conflict rather than a retry.
COMMAND_ENVELOPE_IDENTITY_FIELDS = tuple(
    field for field in COMMAND_ENVELOPE_FIELDS if field != "created_at"
)

# The complete, closed field set of one acknowledgement.  Acknowledgements carry
# no sequence of their own: the committed revision that published them is the
# only ordering the control plane trusts.
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

# The complete, closed acknowledgement vocabulary of ADR-013.
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
# Outcomes that must explain themselves: nothing is refused or deduplicated
# silently.
COMMAND_ACKNOWLEDGEMENT_REASON_REQUIRED = (COMMAND_REJECTED, COMMAND_DUPLICATE)

# Materialized command status.  A command is registered when its envelope is
# committed and then moves only through acknowledgements.
COMMAND_STATUS_REGISTERED = "registered"
COMMAND_STATUSES = (
    COMMAND_STATUS_REGISTERED,
    COMMAND_ACCEPTED,
    COMMAND_APPLIED,
    COMMAND_REJECTED,
)

# The closed acknowledgement order.  `applied` is reachable exactly once and
# only from `accepted`, so no redelivery, retry, or concurrent acknowledgement
# can apply one command twice.  `duplicate` is always recordable and never
# advances anything, which is precisely what makes redelivery safe.
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

# Materialized command slots live in the derived ledger under this field.
LEDGER_COMMANDS_FIELD = "commands"
LEDGER_COMMAND_FIELDS = (
    "envelope",
    "status",
    "deliveries",
    "acknowledgements",
    "conflicts",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_COMMAND_DELIVERY_FIELDS = ("revision", "event_id", "recorded_at")
LEDGER_COMMAND_ACKNOWLEDGEMENT_FIELDS = (
    "acknowledgement",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_COMMAND_CONFLICT_FIELDS = (
    "payload_digest",
    "reason",
    "source",
    "revision",
    "event_id",
    "recorded_at",
)

# Why one delivery or acknowledgement conflicted with the stored command, and
# which half of the protocol observed it.  Both vocabularies are closed, so a
# conflict is a structured fact rather than a sentence to be parsed.
COMMAND_CONFLICT_DIGEST = "digest-conflict"
COMMAND_CONFLICT_ENVELOPE = "envelope-conflict"
COMMAND_CONFLICT_REASONS = (COMMAND_CONFLICT_DIGEST, COMMAND_CONFLICT_ENVELOPE)
COMMAND_CONFLICT_DELIVERY = "delivery"
COMMAND_CONFLICT_ACKNOWLEDGEMENT = "acknowledgement"
COMMAND_CONFLICT_SOURCES = (COMMAND_CONFLICT_DELIVERY, COMMAND_CONFLICT_ACKNOWLEDGEMENT)

# How the deterministic fold treated one committed command event.  Every
# committed event is retained forever; these outcomes only say whether it also
# moved the materialized command state.
COMMAND_FOLD_REGISTERED = "registered"
COMMAND_FOLD_ACKNOWLEDGED = "acknowledged"
COMMAND_FOLD_CONFLICT_RECORDED = "conflict-recorded"
COMMAND_FOLD_RETAINED_DUPLICATE = "retained-duplicate-delivery"
COMMAND_FOLD_RETAINED_UNKNOWN = "retained-unknown-command"
COMMAND_FOLD_RETAINED_DIGEST = "retained-digest-mismatch"
COMMAND_FOLD_RETAINED_ORDER = "retained-invalid-outcome"

# Protocol-authored acknowledgement and conflict explanations.  They are fixed
# strings so a redelivery or a conflict always reads the same way.
COMMAND_DUPLICATE_REASON = (
    "redelivery of a command already registered with the same payload digest"
)
COMMAND_CONFLICT_EXPLANATIONS = {
    COMMAND_CONFLICT_DIGEST: (
        "the command ID already belongs to a different payload digest"
    ),
    COMMAND_CONFLICT_ENVELOPE: (
        "the command ID already belongs to a different command envelope"
    ),
}

DEFAULT_COMMAND_REGISTER_COMMAND = "cockpit-control register-command"
DEFAULT_COMMAND_ACKNOWLEDGE_COMMAND = "cockpit-control acknowledge-command"


# --- managed mission control: dialog, cancellation, replacement (section 10) --

# Managed mission control is versioned independently of the control-store
# schema, of the worker lifecycle vocabulary, and of the command protocol, so a
# controller or worker speaking an older or newer mission-control contract is
# refused explicitly instead of being partially understood.
MISSION_CONTROL_SCHEMA_VERSION = 1

# Every managed interaction rides in the same committed event as the ordinary
# US2 command envelope that carries it.  There is deliberately no second
# delivery, digest, or acknowledgement mechanism: a question, a reply, an
# access-prompt response, a cancellation, and a replacement are all commands,
# and these records only add the correlation the command alone cannot express.
MISSION_DIALOG_RECORD_TYPE = "mission-dialog"
MISSION_CANCELLATION_RECORD_TYPE = "mission-cancellation"
MISSION_REPLACEMENT_RECORD_TYPE = "mission-replacement"
# A controller dispatch is the fourth managed interaction: the one command the
# reconciling controller mints itself.  It is deliberately the same kind of
# record as a cancellation or a replacement rather than a new mechanism, so a
# dispatch inherits the durable command ID, the canonical payload digest,
# idempotent redelivery, and the one-slot-per-worker fold unchanged.
CONTROLLER_DISPATCH_RECORD_TYPE = "controller-dispatch"
MISSION_DIALOG_PAYLOAD_FIELD = "mission_dialog"
MISSION_CANCELLATION_PAYLOAD_FIELD = "mission_cancellation"
MISSION_REPLACEMENT_PAYLOAD_FIELD = "mission_replacement"
CONTROLLER_DISPATCH_PAYLOAD_FIELD = "controller_dispatch"
# A bounded recovery request is the fifth managed interaction: the correlated
# command a controller sends one mission whose heartbeat expired.  ADR-014 says
# stale observation "triggers bounded recovery but does not automatically mean
# mission failure", so recovery is a *command* exactly like a question or a
# cancellation and never a lifecycle state: only a worker's own lifecycle event
# or an explicit human decision ever ends a mission.
MISSION_RECOVERY_RECORD_TYPE = "mission-recovery"
MISSION_RECOVERY_PAYLOAD_FIELD = "mission_recovery"
# The two ladder rungs that need a record of their own.  `cancel` and `replace`
# are already complete managed interactions with their own records, so recovery
# deliberately reuses those rather than restating them under a second name.
MISSION_RECOVERY_NUDGE = "nudge"
MISSION_RECOVERY_TROUBLESHOOT = "troubleshoot"
MISSION_RECOVERY_ACTIONS = (MISSION_RECOVERY_NUDGE, MISSION_RECOVERY_TROUBLESHOOT)
# One event carries at most one mission-control record.  The tuple is ordered so
# a record is always looked for under exactly one closed set of payload fields.
MISSION_CONTROL_PAYLOAD_FIELDS = (
    MISSION_DIALOG_PAYLOAD_FIELD,
    MISSION_CANCELLATION_PAYLOAD_FIELD,
    MISSION_REPLACEMENT_PAYLOAD_FIELD,
    MISSION_RECOVERY_PAYLOAD_FIELD,
    CONTROLLER_DISPATCH_PAYLOAD_FIELD,
)

# The closed dialog vocabulary.  A worker raises a prompt; the overseer answers
# it.  There is no third shape, so an unknown dialog kind is refused rather than
# delivered as something the control plane does not model.
MISSION_DIALOG_QUESTION = "question"
MISSION_DIALOG_ACCESS_PROMPT = "access-prompt"
MISSION_DIALOG_REPLY = "reply"
MISSION_DIALOG_ACCESS_PROMPT_RESPONSE = "access-prompt-response"
MISSION_DIALOG_PROMPT_KINDS = (MISSION_DIALOG_QUESTION, MISSION_DIALOG_ACCESS_PROMPT)
MISSION_DIALOG_RESPONSE_KINDS = (
    MISSION_DIALOG_REPLY,
    MISSION_DIALOG_ACCESS_PROMPT_RESPONSE,
)
MISSION_DIALOG_KINDS = MISSION_DIALOG_PROMPT_KINDS + MISSION_DIALOG_RESPONSE_KINDS
# Exactly which prompt each response kind may answer.  An access-prompt response
# can never resolve an ordinary question and vice versa, so an operator cannot
# accidentally close a filesystem-access prompt with an architectural answer.
MISSION_DIALOG_ANSWERS = {
    MISSION_DIALOG_REPLY: MISSION_DIALOG_QUESTION,
    MISSION_DIALOG_ACCESS_PROMPT_RESPONSE: MISSION_DIALOG_ACCESS_PROMPT,
}

# The complete, closed field set of one dialog record.  The question and answer
# bodies are deliberately absent: ADR-016 keeps canonical records metadata-only,
# so a dialog carries the digest of its body on the command envelope and typed
# references to wherever that body actually lives.  A prompt body can quote a
# secret, a credential, or a source file, and none of those belong in an
# immutable event that is never rewritten.
MISSION_DIALOG_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "kind",
    "mission_id",
    "worker_id",
    "queue_item_id",
    "trace_id",
    "parent_trace_id",
    "answers_command_id",
    "category",
    "body_refs",
    "raised_at",
)

# The complete, closed field set of one cooperative cancellation request.  The
# acknowledgement deadline is always explicit: a cancellation that declared no
# deadline could never be observed as timed out without inventing a policy the
# control plane has not been told.
MISSION_CANCELLATION_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "mission_id",
    "worker_id",
    "queue_item_id",
    "trace_id",
    "parent_trace_id",
    "reason",
    "evidence_refs",
    "requested_at",
    "acknowledge_deadline_at",
)

# The complete, closed field set of one replacement request.  It names both the
# mission it terminates and the mission it creates, so the projection can link
# them without consulting anything derived.
MISSION_REPLACEMENT_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "worker_id",
    "queue_item_id",
    "replaced_mission_id",
    "replacement_mission_id",
    "trace_id",
    "parent_trace_id",
    "reason",
    "evidence_refs",
    "requested_at",
)

# The complete, closed field set of one bounded recovery request.  Like a
# cancellation it always declares the explicit moment a response is expected by,
# because a rung whose response window was never declared could not be observed
# as elapsed without inventing a policy the control plane has not been told, and
# the ladder could then only advance by guessing.  There is no free prose: the
# reason comes from the closed observation vocabulary that made recovery owed.
MISSION_RECOVERY_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "action",
    "mission_id",
    "worker_id",
    "queue_item_id",
    "trace_id",
    "parent_trace_id",
    "reason",
    "evidence_refs",
    "requested_at",
    "respond_deadline_at",
)

# Why a bounded recovery was owed at all.  The vocabulary is closed and is the
# same token `lifecycle-status` already reports, so a recovery record can never
# claim a cause the freshness observation cannot produce.
MISSION_RECOVERY_REASONS = (LIFECYCLE_STALE_REASON,)

# The complete, closed field set of one controller dispatch.  It carries no
# mission brief and no prose: the brief is digested by the carrying envelope,
# `evidence_refs` says which queue item authorized the work, and `state_key`
# digests the exact reconciled evidence the controller acted on, so a later tick
# can tell "the same situation" from "a new one" without re-investigating.
CONTROLLER_DISPATCH_FIELDS = (
    "schema_version",
    "record_type",
    "command_id",
    "mission_id",
    "worker_id",
    "queue_item_id",
    "trace_id",
    "parent_trace_id",
    "reason",
    "state_key",
    "evidence_refs",
    "decided_at",
)

# The managed command types.  These are the only command types this tool mints
# itself, and the generic `register-command` surface refuses them: a managed
# command without its correlated record would look managed while being
# correlated to nothing, which is exactly the unscoped interaction AC1 removes.
COMMAND_TYPE_MISSION_QUESTION = "mission-question"
COMMAND_TYPE_MISSION_ACCESS_PROMPT = "mission-access-prompt"
COMMAND_TYPE_MISSION_REPLY = "mission-reply"
COMMAND_TYPE_MISSION_ACCESS_PROMPT_RESPONSE = "mission-access-prompt-response"
COMMAND_TYPE_MISSION_CANCEL = "mission-cancel"
COMMAND_TYPE_MISSION_REPLACE = "mission-replace"
COMMAND_TYPE_MISSION_DISPATCH = "mission-dispatch"
COMMAND_TYPE_MISSION_NUDGE = "mission-nudge"
COMMAND_TYPE_MISSION_TROUBLESHOOT = "mission-troubleshoot"
MISSION_RECOVERY_COMMAND_TYPES = {
    MISSION_RECOVERY_NUDGE: COMMAND_TYPE_MISSION_NUDGE,
    MISSION_RECOVERY_TROUBLESHOOT: COMMAND_TYPE_MISSION_TROUBLESHOOT,
}
MISSION_DIALOG_COMMAND_TYPES = {
    MISSION_DIALOG_QUESTION: COMMAND_TYPE_MISSION_QUESTION,
    MISSION_DIALOG_ACCESS_PROMPT: COMMAND_TYPE_MISSION_ACCESS_PROMPT,
    MISSION_DIALOG_REPLY: COMMAND_TYPE_MISSION_REPLY,
    MISSION_DIALOG_ACCESS_PROMPT_RESPONSE: COMMAND_TYPE_MISSION_ACCESS_PROMPT_RESPONSE,
}
MISSION_DIALOG_RESPONSES = {
    prompt: response for response, prompt in MISSION_DIALOG_ANSWERS.items()
}
MANAGED_COMMAND_TYPES = (
    tuple(sorted(MISSION_DIALOG_COMMAND_TYPES.values()))
    + tuple(sorted(MISSION_RECOVERY_COMMAND_TYPES.values()))
    + (
        COMMAND_TYPE_MISSION_CANCEL,
        COMMAND_TYPE_MISSION_DISPATCH,
        COMMAND_TYPE_MISSION_REPLACE,
    )
)

# Materialized dialog state.  A prompt is `pending` until exactly one correlated
# response answers it; a response is `delivered` and answers nothing itself.
MISSION_DIALOG_PENDING = "pending"
MISSION_DIALOG_ANSWERED = "answered"
MISSION_DIALOG_DELIVERED = "delivered"
MISSION_DIALOG_STATES = (
    MISSION_DIALOG_PENDING,
    MISSION_DIALOG_ANSWERED,
    MISSION_DIALOG_DELIVERED,
)

# Dialog observations.  Exactly like lifecycle freshness and cancellation
# deadlines, an observation is not a state: the fold records that a prompt is
# still unanswered, and this says what the control plane can currently *do*
# about it.  A prompt whose mission is no longer active is `orphaned`, because
# `cockpit-control answer-question` fails closed on a mission that ended:
# counting it as an actionable pending prompt would tell an operator to run a
# command that cannot succeed and would leave the count permanently inflated.
MISSION_DIALOG_OBSERVATION_ANSWERABLE = "answerable"
MISSION_DIALOG_OBSERVATION_ORPHANED = "orphaned"
MISSION_DIALOG_OBSERVATION_SETTLED = "settled"
# An orphaned prompt is a recoverable observation with an explicit reason, never
# a failure: the mission ended, and only a bounded recovery action can close the
# question the worker asked.
MISSION_DIALOG_ORPHAN_REASON = "mission-no-longer-active"

# Materialized mission dialogs, cancellations, and per-worker mission slots live
# in the derived ledger under these fields.
LEDGER_MISSION_DIALOGS_FIELD = "mission_dialogs"
LEDGER_MISSION_DIALOG_FIELDS = (
    "dialog",
    "state",
    "answered_by_command_id",
    "answered_at",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_MISSION_CANCELLATIONS_FIELD = "mission_cancellations"
LEDGER_MISSION_CANCELLATION_FIELDS = (
    "cancellation",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_MISSION_RECOVERIES_FIELD = "mission_recoveries"
LEDGER_MISSION_RECOVERY_FIELDS = (
    "recovery",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_MISSION_SLOTS_FIELD = "mission_slots"
LEDGER_MISSION_SLOT_FIELDS = (
    "mission_id",
    "queue_item_id",
    "state",
    "command_id",
    "replaces",
    "conflicts",
    "revision",
    "event_id",
    "recorded_at",
)
LEDGER_MISSION_SLOT_CONFLICT_FIELDS = (
    "mission_id",
    "reason",
    "source",
    "command_id",
    "revision",
    "event_id",
    "recorded_at",
)

# ADR-013: "Each worker retains one active mission slot."  A slot is `active`
# once a lifecycle event materializes its mission, `reserved` between a
# replacement and the worker accepting the mission it created, `released` once
# the mission it held reached a terminal state, and `unclaimed` when the worker
# holds nothing at all and the entry exists only to carry recorded conflicts.
MISSION_SLOT_ACTIVE = "active"
MISSION_SLOT_RESERVED = "reserved"
MISSION_SLOT_RELEASED = "released"
MISSION_SLOT_UNCLAIMED = "unclaimed"
MISSION_SLOT_STATES = (
    MISSION_SLOT_ACTIVE,
    MISSION_SLOT_RESERVED,
    MISSION_SLOT_RELEASED,
    MISSION_SLOT_UNCLAIMED,
)
# The states in which the worker's one slot is occupied and a second claim would
# be a second active slot.
MISSION_SLOT_CLAIMED_STATES = (MISSION_SLOT_ACTIVE, MISSION_SLOT_RESERVED)

# Why one claim could not take the worker's single slot, and which half of the
# protocol observed it.  Both vocabularies are closed, so a slot conflict is a
# structured fact rather than a sentence to be parsed.
MISSION_SLOT_CONFLICT_SECOND_ACTIVE = "second-active-slot"
MISSION_SLOT_CONFLICT_UNMATCHED = "unmatched-mission-slot"
MISSION_SLOT_CONFLICT_REUSED = "reused-mission-id"
MISSION_SLOT_CONFLICT_REASONS = (
    MISSION_SLOT_CONFLICT_SECOND_ACTIVE,
    MISSION_SLOT_CONFLICT_UNMATCHED,
    MISSION_SLOT_CONFLICT_REUSED,
)
# The two refusals that mean one worker was claimed by *two* missions at once.
# `unmatched-mission-slot` is deliberately absent: it refuses a claim for lack of
# correlation, which leaves exactly one mission on the slot rather than two.
MISSION_SLOT_DUPLICATE_CLAIM_REASONS = (
    MISSION_SLOT_CONFLICT_SECOND_ACTIVE,
    MISSION_SLOT_CONFLICT_REUSED,
)
MISSION_SLOT_CONFLICT_LIFECYCLE = "lifecycle"
MISSION_SLOT_CONFLICT_REPLACEMENT = "replacement"
MISSION_SLOT_CONFLICT_DISPATCH = "dispatch"
MISSION_SLOT_CONFLICT_SOURCES = (
    MISSION_SLOT_CONFLICT_DISPATCH,
    MISSION_SLOT_CONFLICT_LIFECYCLE,
    MISSION_SLOT_CONFLICT_REPLACEMENT,
)
# The human sentence each recorded reason deserves.  The structured reason on
# stdout is the contract; this only has to be accurate about which of the three
# refusals actually happened, because naming the wrong one would misdescribe a
# durable refusal to the operator reading stderr.
MISSION_SLOT_CONFLICT_EXPLANATIONS = {
    MISSION_SLOT_CONFLICT_SECOND_ACTIVE: (
        "it would leave worker {worker_id} holding more than one active mission slot"
    ),
    MISSION_SLOT_CONFLICT_UNMATCHED: (
        "it does not name worker {worker_id}'s own claimed, correlated, still-active "
        "mission"
    ),
    MISSION_SLOT_CONFLICT_REUSED: (
        "mission ID {mission_id} is already in use and a replacement must create one "
        "new mission"
    ),
}

# How the deterministic fold treated one committed mission-control record.
# Every committed record is retained forever; these outcomes only say whether it
# also moved the materialized dialog, cancellation, or mission-slot state.
MISSION_FOLD_RAISED = "raised"
MISSION_FOLD_ANSWERED = "answered"
MISSION_FOLD_REQUESTED = "requested"
MISSION_FOLD_RECOVERY_REQUESTED = "recovery-requested"
MISSION_FOLD_REPLACED = "replaced"
MISSION_FOLD_CONFLICT_RECORDED = "conflict-recorded"
MISSION_FOLD_RETAINED_DUPLICATE = "retained-duplicate-record"
MISSION_FOLD_RETAINED_UNKNOWN_PROMPT = "retained-unknown-prompt"
MISSION_FOLD_RETAINED_ANSWERED = "retained-already-answered"
MISSION_FOLD_RETAINED_KIND = "retained-mismatched-prompt-kind"
MISSION_FOLD_RETAINED_UNMATCHED = "retained-unmatched-correlation"

# Cancellation observations.  An observation says what the control plane can
# currently see about one cooperative cancellation; like lifecycle freshness it
# is deliberately not a mission state and never becomes one.
CANCELLATION_ACKNOWLEDGED = "acknowledged"
CANCELLATION_AWAITING = "awaiting-acknowledgement"
CANCELLATION_TIMED_OUT = "timed-out"
# An expired acknowledgement deadline is a recoverable observation with an
# explicit reason.  It is never `failed`: only a worker lifecycle event or an
# explicit decision ends a mission, and a silent worker has not ended anything.
CANCELLATION_TIMEOUT_REASON = "acknowledgement-deadline-expired"
CANCELLATION_TIMEOUT_RECOVERY = "awaiting-bounded-recovery"

DEFAULT_MISSION_QUESTION_COMMAND = "cockpit-control raise-question"
DEFAULT_MISSION_ANSWER_COMMAND = "cockpit-control answer-question"
DEFAULT_MISSION_CANCEL_COMMAND = "cockpit-control cancel-mission"
DEFAULT_MISSION_REPLACE_COMMAND = "cockpit-control replace-mission"


# --- deterministic controller reconciliation (ADR-014, sections 6 and 11) ----

# The reconciliation contract is versioned independently of the control-store
# schema, the lifecycle vocabulary, the command protocol, and the mission-control
# contract, so a controller speaking an older or newer reconciliation vocabulary
# is refused explicitly instead of being partially understood.
CONTROLLER_SCHEMA_VERSION = 1
CONTROLLER_OBSERVATION_RECORD_TYPE = "controller-observation"
CONTROLLER_OBSERVATION_PAYLOAD_FIELD = "controller_observation"
# An observation event type is derived from its outcome exactly as a lifecycle
# event type is derived from its state, so neither half can exist without the
# other and an observation cannot be smuggled in under an unrelated event type.
CONTROLLER_OBSERVATION_EVENT_PREFIX = "controller-observation-"

# The complete, closed field set of one observation record.  Identity fields are
# nullable because a tick can legitimately observe something that names no queue
# item, worker, mission, or command at all, but they are never absent, so a
# partially written observation is malformed rather than defaulted.
CONTROLLER_OBSERVATION_FIELDS = (
    "schema_version",
    "record_type",
    "outcome",
    "reason",
    "state_key",
    "queue_item_id",
    "worker_id",
    "mission_id",
    "command_id",
    "evidence_refs",
    "observed_at",
)

# The closed outcome vocabulary of one controller tick.  `dispatched` is only
# ever carried by the command envelope that performs the dispatch; a tick that
# changes no mission state records what it saw instead, which is what stops the
# next tick from investigating the same situation again.
CONTROLLER_DISPATCHED = "dispatched"
CONTROLLER_OBSERVED = "observed"
CONTROLLER_BLOCKED = "blocked"
CONTROLLER_OUTCOMES = (CONTROLLER_DISPATCHED, CONTROLLER_OBSERVED, CONTROLLER_BLOCKED)
CONTROLLER_OBSERVATION_OUTCOMES = (CONTROLLER_OBSERVED, CONTROLLER_BLOCKED)

# The closed reason vocabulary.  Every controller decision explains itself with
# one of these tokens and never with free text, so the reason is safe to render
# into a parseable position and can be asserted exactly by a test or a skill.
CONTROLLER_REASON_IMPLEMENTABLE = "queue-item-implementable"
CONTROLLER_REASON_ROOT_CONFLICT = "queue-root-disagreement"
CONTROLLER_REASON_ROOT_UNDECLARED = "queue-root-undeclared"
CONTROLLER_REASON_NO_ROOT = "no-queue-root-declared"
CONTROLLER_REASON_QUEUE_UNREADABLE = "queue-root-unreadable"
CONTROLLER_REASON_LEDGER_DIVERGENT = "ledger-projection-divergent"
CONTROLLER_REASON_QUEUE_PAUSED = "queue-paused"
CONTROLLER_REASON_QUEUE_EMPTY = "no-active-queue-item"
CONTROLLER_REASON_QUEUE_AMBIGUOUS = "multiple-active-queue-items"
CONTROLLER_REASON_NOT_IMPLEMENTABLE = "queue-item-not-implementable"
CONTROLLER_REASON_MISSION_IN_PROGRESS = "mission-in-progress"
CONTROLLER_REASON_WORKER_BUSY = "worker-busy"
CONTROLLER_REASON_WORKER_CONFLICT = "worker-mission-conflict"
CONTROLLER_REASON_RECOVERY_AWAITING = "awaiting-recovery-response"
CONTROLLER_REASON_RECOVERY_ESCALATED = "bounded-recovery-escalated"
CONTROLLER_DISPATCH_REASONS = (CONTROLLER_REASON_IMPLEMENTABLE,)
CONTROLLER_OBSERVATION_REASONS = (
    CONTROLLER_REASON_LEDGER_DIVERGENT,
    CONTROLLER_REASON_MISSION_IN_PROGRESS,
    CONTROLLER_REASON_NOT_IMPLEMENTABLE,
    CONTROLLER_REASON_NO_ROOT,
    CONTROLLER_REASON_QUEUE_AMBIGUOUS,
    CONTROLLER_REASON_QUEUE_EMPTY,
    CONTROLLER_REASON_QUEUE_PAUSED,
    CONTROLLER_REASON_QUEUE_UNREADABLE,
    CONTROLLER_REASON_RECOVERY_AWAITING,
    CONTROLLER_REASON_RECOVERY_ESCALATED,
    CONTROLLER_REASON_ROOT_CONFLICT,
    CONTROLLER_REASON_ROOT_UNDECLARED,
    CONTROLLER_REASON_WORKER_BUSY,
    CONTROLLER_REASON_WORKER_CONFLICT,
)
CONTROLLER_REASONS = CONTROLLER_DISPATCH_REASONS + CONTROLLER_OBSERVATION_REASONS

# Why a tick sent one bounded recovery command.  These reasons ride on the
# managed record the rung committed -- a recovery request, a cooperative
# cancellation, or a replacement -- and never on a controller observation, so
# they are deliberately outside `CONTROLLER_REASONS`: the observation vocabulary
# stays exactly what a tick that changed no mission state may record.
CONTROLLER_REASON_RECOVERY_NUDGE = "stale-mission-nudged"
CONTROLLER_REASON_RECOVERY_TROUBLESHOOT = "stale-mission-troubleshot"
CONTROLLER_REASON_RECOVERY_CANCEL = "stale-mission-cancelled"
CONTROLLER_REASON_RECOVERY_REPLACE = "stale-mission-replaced"
CONTROLLER_REASON_QUEUE_ITEM_TERMINAL = "queue-item-terminal"
CONTROLLER_RECOVERY_REASONS = (
    CONTROLLER_REASON_QUEUE_ITEM_TERMINAL,
    CONTROLLER_REASON_RECOVERY_CANCEL,
    CONTROLLER_REASON_RECOVERY_NUDGE,
    CONTROLLER_REASON_RECOVERY_REPLACE,
    CONTROLLER_REASON_RECOVERY_TROUBLESHOOT,
)
# Every reason one tick may decide on, whichever half of the contract records it.
CONTROLLER_ACTION_REASONS = CONTROLLER_REASONS + CONTROLLER_RECOVERY_REASONS

# Worker lifecycle blockers are free-text categories, so architectural boundary
# enforcement must map only a narrow explicit category set rather than substring
# matching. Anything outside this set is not treated as an architecture-boundary
# signal by the controller tick.
ARCHITECTURE_BLOCKER_CATEGORY_MAP = {
    "image": "image",
    "repo": "repository",
    "repository": "repository",
    "deploy": "deployment",
    "deployment": "deployment",
    "ci": "cicd",
    "ci-cd": "cicd",
    "cicd": "cicd",
    "iam": "iam",
}

# The reasons a committed controller record carries *because* it is walking one
# bounded recovery episode.  A record carrying one of them about a mission is
# the controller continuing that mission's episode, so it may never be read back
# as evidence that the episode ended -- otherwise a mission the ladder is
# deliberately still recovering could close its own episode and start again.
CONTROLLER_EPISODE_REASONS = CONTROLLER_RECOVERY_REASONS + (
    CONTROLLER_REASON_RECOVERY_AWAITING,
    CONTROLLER_REASON_RECOVERY_ESCALATED,
)

# Reasons that stop dispatch until a human or another tool changes something.
# They are still recorded exactly once and still terminate the tick cleanly;
# `blocked` is a refusal to guess, never a loop and never a retry.
CONTROLLER_BLOCKING_REASONS = (
    CONTROLLER_REASON_LEDGER_DIVERGENT,
    CONTROLLER_REASON_QUEUE_AMBIGUOUS,
    CONTROLLER_REASON_QUEUE_UNREADABLE,
    CONTROLLER_REASON_RECOVERY_ESCALATED,
    CONTROLLER_REASON_ROOT_CONFLICT,
    CONTROLLER_REASON_ROOT_UNDECLARED,
    CONTROLLER_REASON_WORKER_CONFLICT,
)

# The exact repair each blocking reason asks a human for.  A refusal that does
# not name its repair is not fail-closed, it is just a dead end.
CONTROLLER_BLOCKING_REPAIRS = {
    CONTROLLER_REASON_ROOT_CONFLICT: (
        "export COCKPIT_QUEUE_ROOT to the queue root {declared} that "
        "control.json declares, or create a control root for {exported}"
    ),
    CONTROLLER_REASON_ROOT_UNDECLARED: (
        "declare canonical_roots.queue_root as {exported} when the mission is "
        "created, or unset COCKPIT_QUEUE_ROOT in this shell"
    ),
    CONTROLLER_REASON_QUEUE_UNREADABLE: (
        "repair the queue root {declared} with cockpit-queue before the "
        "controller can read product-work authority from it"
    ),
    # `ledger-projection-divergent` is deliberately absent.  Every derived
    # disagreement replay can settle is repaired by the tick itself, and the one
    # it must not settle is refused before any evidence is assembled, so this
    # reason is now only ever read back off a store an earlier build wrote.  Its
    # repair is therefore whatever `cockpit-control preflight` currently
    # diagnoses rather than a sentence that may since have gone stale.
    # The repair names the *duplicate*, because that is the only claim ending
    # which clears this block: the retained mission is the one being kept, and
    # a mission that does not hold the worker's slot can only be ended by its
    # own worker's terminal lifecycle event, which `cancel-mission` requests
    # cooperatively.  It also names the exact lifecycle events that end it,
    # computed from the section 9 transition table the fold enforces: `cancelled`
    # is not reachable from `accepted`, so a mission a worker has only accepted
    # must be run before it can be cancelled, and a sentence that said otherwise
    # could be followed to the letter and leave the cockpit blocked -- the dead
    # end this table exists to prevent.
    CONTROLLER_REASON_WORKER_CONFLICT: (
        "worker {worker} keeps its earliest accepted mission {mission}; dispatch "
        "resumes when the duplicate mission {duplicate} ends -- request that with "
        "`cockpit-control cancel-mission` and have its worker record {terminal} "
        "-- and never by ending {mission}, which this control root is keeping"
    ),
    CONTROLLER_REASON_RECOVERY_ESCALATED: (
        "decide mission {mission} on worker {worker} yourself: bounded recovery "
        "is exhausted, so end it with `cockpit-control cancel-mission` or "
        "`cockpit-control replace-mission`, or transition queue item {queue_item} "
        "with cockpit-queue"
    ),
    CONTROLLER_REASON_QUEUE_AMBIGUOUS: (
        "keep the earliest valid active queue item and transition the others "
        "with cockpit-queue before dispatch"
    ),
}

# What a tick must do about each authoritative-journal refusal.  Committed
# events are the only authority this control plane has, so nothing here is a
# command that rewrites them: the operator is told exactly where to look and
# that the tool will not guess on their behalf.
CONTROLLER_JOURNAL_REPAIR = (
    "run `cockpit-control preflight` to name the exact file under "
    f"{EVENTS_DIR_NAME}/, then restore or correct that one file yourself; "
    "cockpit-control never rewrites, reorders, or removes committed authority"
)

# A projection recording revisions the committed journal does not hold is not a
# derived fault at all: it is the visible half of committed authority having
# disappeared.  Rebuilding it would rewind derived state and settle that
# disappearance silently, so the tick refuses before it assembles one piece of
# evidence, commits nothing -- committing anything would itself replace the
# projection and destroy the evidence -- and names the explicit repair.
CONTROLLER_LEDGER_AHEAD_REPAIR = (
    "restore the committed event file(s) that {ledger} names but "
    f"{EVENTS_DIR_NAME}/ no longer holds; run `cockpit-control preflight` to "
    "name them, and only if the removal was intended run `cockpit-control "
    "replay-ledger` to rewind the derived ledger to revision {journal}"
)
CONTROLLER_LEDGER_UNREPAIRED_REPAIR = (
    "repair {ledger} yourself and run `cockpit-control preflight`; replaying "
    "the committed journal did not make the derived projection equal the "
    "rebuild at revision {journal}"
)

# How each derived-ledger classification is read by a decision, and why they are
# not read the same way.  Architecture section 8.3 commits an event into
# `events/` (step 4) *before* it replaces `ledger.json` (step 8), so any reader
# outside `control.lock` can legitimately observe a journal one revision ahead of
# a projection that is about to be rewritten.  A projection that is merely behind
# the committed journal is therefore the ordinary consequence of a concurrent
# writer, and sections 11 and 17 repair one by replaying the journal, never by
# refusing to decide: the tick derives every authoritative fact from the
# committed events themselves, so a lagging projection changes nothing it knows.
# A projection ahead of the committed journal, one that disagrees at the very
# same revision, one that cannot be parsed, or one that is absent is different in
# kind: no writer following section 8.3 can produce it, so it is evidence that
# something outside this protocol edited derived state and it refuses a decision.
# A projection that is absent or unparseable is refused earlier still, by the
# store validation that opens every tick; both are classified here anyway so this
# is a complete reading of the vocabulary rather than of what happens to reach it.
# A missing, stale, unreadable, or disagreeing projection is *derived* state
# that the committed events reproduce exactly, so section 17's "corrupt ledger:
# replay the journal and atomically rebuild it" applies without guessing one
# byte: the rebuild is a pure function of authority the tick has already read.
# A projection *ahead* of the committed journal is the single exception, because
# rebuilding it would rewind derived state past revisions `events/` no longer
# holds and would quietly settle the disappearance of committed authority.
CONTROLLER_PROJECTION_REPAIRABLE_REASONS = (
    PROJECTION_REASON_CORRUPT,
    PROJECTION_REASON_DIVERGENT,
    PROJECTION_REASON_MISSING,
    PROJECTION_REASON_STALE,
    PROJECTION_REASON_VIEW,
)
CONTROLLER_PROJECTION_DIVERGENT_REASONS = (PROJECTION_REASON_AHEAD,)

# What one tick did about the derived projection it read.  Repair is part of
# step 3 of the bounded loop -- replaying the journal and validating the derived
# revision -- and commits no event, so it is never the tick's one state-changing
# action and never competes with the decision that follows it.
CONTROLLER_LEDGER_CURRENT = "current"
CONTROLLER_LEDGER_REPAIRED = "repaired"
CONTROLLER_LEDGER_WOULD_REPAIR = "would-repair"
# A projection replay ran against and still did not settle.  It is a
# classification a tick may hold but never one it decides from: reaching it
# raises before any evidence exists, exactly as `refused` does.
CONTROLLER_LEDGER_UNREPAIRED = "unrepaired"
CONTROLLER_LEDGER_REFUSED = "refused"
CONTROLLER_LEDGER_OUTCOMES = (
    CONTROLLER_LEDGER_CURRENT,
    CONTROLLER_LEDGER_REFUSED,
    CONTROLLER_LEDGER_REPAIRED,
    CONTROLLER_LEDGER_UNREPAIRED,
    CONTROLLER_LEDGER_WOULD_REPAIR,
)
# The classifications one tick may go on to decide from.  Everything else is a
# refusal raised before any evidence exists, so this is the structural guard the
# decision keeps for itself rather than the path a healthy store takes.
CONTROLLER_LEDGER_DECIDABLE = (
    CONTROLLER_LEDGER_CURRENT,
    CONTROLLER_LEDGER_REPAIRED,
    CONTROLLER_LEDGER_WOULD_REPAIR,
)

# ADR-014 precedence, in rule order, with the role each source plays.  The order
# is data rather than control flow so it can be reported verbatim and asserted
# by a test: rules 1-4 own state, rule 5 is a rebuildable projection, rule 6 is
# correlated evidence, and rules 7-8 are observations that never advance state.
PRECEDENCE_AUTHORITATIVE = "authoritative"
PRECEDENCE_DERIVED = "derived"
PRECEDENCE_EVIDENCE = "evidence"
PRECEDENCE_DIAGNOSTIC = "diagnostic"
CONTROLLER_PRECEDENCE = (
    (1, "human-decision", PRECEDENCE_AUTHORITATIVE),
    (2, "queue-state", PRECEDENCE_AUTHORITATIVE),
    (3, "durable-events", PRECEDENCE_AUTHORITATIVE),
    (4, "control-journal", PRECEDENCE_AUTHORITATIVE),
    (5, "ledger-projection", PRECEDENCE_DERIVED),
    (6, "worker-reports", PRECEDENCE_EVIDENCE),
    (7, "live-status", PRECEDENCE_DIAGNOSTIC),
    (8, "pane-text", PRECEDENCE_DIAGNOSTIC),
)
# The rules a decision may never read.  `select_controller_action` takes only a
# `ControllerEvidence`, which has no field for either of them, so this tuple
# documents a structural fact rather than asking anyone to remember a rule.
CONTROLLER_DIAGNOSTIC_RULES = (7, 8)

# The product-work vocabulary `cockpit-queue` owns (ADR-010).  It is mirrored
# here because precedence rule 2 requires the controller to *read* product state
# it must never own; `tests/unit/cmd-overseer-tick.bats` fails if the two ever
# drift apart, so this copy cannot silently become a second state machine.
QUEUE_ITEMS_DIR_NAME = "items"
QUEUE_EVENTS_NAME = "events.jsonl"
QUEUE_ITEM_SUFFIX = ".yaml"
QUEUE_STATE_QUEUED = "queued"
QUEUE_STATE_BLOCKED = "blocked"
QUEUE_ACTIVE_STATES = (
    "delivered",
    "e2e-related-fixing",
    "e2e-testing-runbooks",
    "fixing",
    "implementing",
    "planned",
    "shaping",
    "testing",
)
QUEUE_TERMINAL_STATES = ("cleared", "rejected")
QUEUE_STATES = (
    (QUEUE_STATE_QUEUED, QUEUE_STATE_BLOCKED) + QUEUE_ACTIVE_STATES + QUEUE_TERMINAL_STATES
)
QUEUE_PAUSED_EVENT = "queue-paused"
QUEUE_RESUMED_EVENT = "queue-resumed"

# Which worker a queue item in each product state is implementable by.  A state
# that is absent is deliberately not dispatchable: shaping, planning, delivery,
# and runbook execution are overseer or human work, so the controller reports
# `queue-item-not-implementable` instead of inventing a mission for them.
CONTROLLER_WORKER_BY_QUEUE_STATE = {
    "implementing": "worker-dev",
    "testing": "worker-test",
    "fixing": "worker-fix",
    "e2e-related-fixing": "worker-fix",
}

# How the deterministic fold treated one committed controller record.  Every
# committed event is retained forever; these outcomes only say whether it also
# moved the materialized controller state.
CONTROLLER_FOLD_DISPATCHED = "dispatched"
CONTROLLER_FOLD_OBSERVED = "observed"
CONTROLLER_FOLD_RETAINED_UNCHANGED = "retained-unchanged-observation"
CONTROLLER_FOLD_CONFLICT_RECORDED = "conflict-recorded"

# The fixed bounded recovery ladder of architecture section 9: "The controller
# must reconcile and either nudge, troubleshoot, cancel, replace, or escalate."
# It is data rather than control flow so it can be reported verbatim, asserted by
# a test, and counted: a staleness episode attempts each command rung at most
# once and then escalates, and an episode ends only when the controller records
# that it looked and the mission was fresh, so recovery is finite by
# construction and can never nudge forever however often a worker reports
# freshness windows that have already expired.
CONTROLLER_RECOVERY_NUDGE = MISSION_RECOVERY_NUDGE
CONTROLLER_RECOVERY_TROUBLESHOOT = MISSION_RECOVERY_TROUBLESHOOT
CONTROLLER_RECOVERY_CANCEL = "cancel"
CONTROLLER_RECOVERY_REPLACE = "replace"
CONTROLLER_RECOVERY_ESCALATE = "escalate"
# Not a rung: the state between a delivered rung and its declared deadline, in
# which the ladder deliberately does nothing rather than advancing on a worker
# that was never given the response window its own record declared.
CONTROLLER_RECOVERY_AWAIT = "await"
CONTROLLER_RECOVERY_LADDER = (
    CONTROLLER_RECOVERY_NUDGE,
    CONTROLLER_RECOVERY_TROUBLESHOOT,
    CONTROLLER_RECOVERY_CANCEL,
    CONTROLLER_RECOVERY_REPLACE,
    CONTROLLER_RECOVERY_ESCALATE,
)
# The rungs that deliver a correlated command; `escalate` is the terminal rung
# and delivers nothing at all, which is why the ladder terminates.
CONTROLLER_RECOVERY_COMMAND_RUNGS = CONTROLLER_RECOVERY_LADDER[:-1]
# Product work owned by `cockpit-queue` that has left the active set cannot be
# redone by a replacement mission, and nudging or troubleshooting a mission whose
# reason to exist is gone would be recovery for its own sake.  The controller
# therefore asks for cooperative cancellation and, if that is not acknowledged by
# its declared deadline, escalates -- and it never reopens the queue item.
CONTROLLER_TERMINAL_ITEM_LADDER = (
    CONTROLLER_RECOVERY_CANCEL,
    CONTROLLER_RECOVERY_ESCALATE,
)
# Product work is still active but `cockpit-queue` no longer names this worker
# for it, so cooperative recovery is allowed and minting a replacement mission
# on this worker is not.
CONTROLLER_UNREPLACEABLE_LADDER = (
    CONTROLLER_RECOVERY_NUDGE,
    CONTROLLER_RECOVERY_TROUBLESHOOT,
    CONTROLLER_RECOVERY_CANCEL,
    CONTROLLER_RECOVERY_ESCALATE,
)
# The managed command type each command rung delivers, which is what its
# canonical payload digest is bound to.
CONTROLLER_RECOVERY_COMMAND_PAYLOAD_TYPES = {
    CONTROLLER_RECOVERY_NUDGE: COMMAND_TYPE_MISSION_NUDGE,
    CONTROLLER_RECOVERY_TROUBLESHOOT: COMMAND_TYPE_MISSION_TROUBLESHOOT,
    CONTROLLER_RECOVERY_CANCEL: COMMAND_TYPE_MISSION_CANCEL,
    CONTROLLER_RECOVERY_REPLACE: COMMAND_TYPE_MISSION_REPLACE,
}
# The closed reason each command rung records, by rung.
CONTROLLER_RECOVERY_RUNG_REASONS = {
    CONTROLLER_RECOVERY_NUDGE: CONTROLLER_REASON_RECOVERY_NUDGE,
    CONTROLLER_RECOVERY_TROUBLESHOOT: CONTROLLER_REASON_RECOVERY_TROUBLESHOOT,
    CONTROLLER_RECOVERY_CANCEL: CONTROLLER_REASON_RECOVERY_CANCEL,
    CONTROLLER_RECOVERY_REPLACE: CONTROLLER_REASON_RECOVERY_REPLACE,
}
# The fixed sentence each cooperative cancellation the controller mints carries.
# It is a constant rather than assembled prose so nothing an operator, a worker,
# or a pane wrote can reach a committed record through this path.
CONTROLLER_CANCEL_REASONS = {
    CONTROLLER_REASON_RECOVERY_CANCEL: (
        "bounded recovery: the mission heartbeat expired and neither a nudge nor "
        "focused troubleshooting produced a fresh lifecycle event"
    ),
    CONTROLLER_REASON_QUEUE_ITEM_TERMINAL: (
        "bounded recovery: the queue item this mission implements reached a "
        "terminal product state, which the controller never reopens"
    ),
}
CONTROLLER_REPLACE_REASON = (
    "bounded recovery: the mission heartbeat expired and cooperative "
    "cancellation was not acknowledged by its declared deadline"
)
# How long a recovery rung waits for the worker before the ladder may advance.
# Both windows are declared on the committed record itself, so the ladder is a
# pure function of committed evidence and one explicit as-of moment and never of
# the wall clock the tick happens to run on.
CONTROLLER_RECOVERY_RESPOND_SECONDS = 300.0
CONTROLLER_RECOVERY_ACKNOWLEDGE_SECONDS = 900.0

# The one action a tick may take, and what actually happened when it tried.
CONTROLLER_ACTION_DISPATCH = "dispatch-mission"
CONTROLLER_ACTION_OBSERVE = "record-observation"
CONTROLLER_ACTION_NONE = "none"
CONTROLLER_ACTION_RECOVER = "recover-mission"
CONTROLLER_ACTIONS = (
    CONTROLLER_ACTION_DISPATCH,
    CONTROLLER_ACTION_OBSERVE,
    CONTROLLER_ACTION_RECOVER,
    CONTROLLER_ACTION_NONE,
)
CONTROLLER_TICK_DISPATCHED = "dispatched"
CONTROLLER_TICK_RECORDED = "recorded"
CONTROLLER_TICK_RECOVERED = "recovered"
CONTROLLER_TICK_UNCHANGED = "unchanged"
CONTROLLER_TICK_WOULD_DISPATCH = "would-dispatch"
CONTROLLER_TICK_WOULD_RECORD = "would-record"
CONTROLLER_TICK_WOULD_RECOVER = "would-recover"

# The tick-level outcome of one bounded recovery command.  It is deliberately
# absent from `CONTROLLER_OUTCOMES`: that vocabulary is what a *controller
# record* may declare, and a recovery rung is carried by a managed
# mission-control record instead, so no controller observation can claim it.
CONTROLLER_RECOVERED = "recovered"

# Live-status and pane observations, both diagnostic-only (ADR-014 rules 7-8).
CONTROLLER_PANE_UNOBSERVED = "unobserved"
CONTROLLER_LIVE_REACHABLE = "reachable"
CONTROLLER_LIVE_UNREACHABLE = "unreachable"
CONTROLLER_LIVE_UNOBSERVED = "unobserved"

# Deterministic identity derivation.  Every identifier one dispatch needs is a
# pure function of the control root, the queue item, the worker, and how many
# dispatches this control root already recorded for that pair, so two concurrent
# ticks reading the same committed prefix mint the *same* command rather than
# two, and architecture section 17's "retry the same command ID" is structural.
CONTROLLER_MISSION_DERIVATION = "cockpit-overseer/mission"
CONTROLLER_COMMAND_DERIVATION = "cockpit-overseer/dispatch-command"
CONTROLLER_TRACE_DERIVATION = "cockpit-overseer/mission-trace"
# The same derivation for every recovery rung.  A rung identifier is a pure
# function of the mission, the rung, and the committed revision that keys the
# mission's recovery episode, so "has this rung already been attempted for this
# episode?" is answered by asking whether that exact command ID is already
# committed -- and an episode the controller has recorded as over moves the next
# one onto identifiers no rung has ever used, which is how the ladder resets.
CONTROLLER_RECOVERY_COMMAND_DERIVATION = "cockpit-overseer/recovery-command"
CONTROLLER_RECOVERY_TRACE_DERIVATION = "cockpit-overseer/recovery-trace"
CONTROLLER_RECOVERY_MISSION_DERIVATION = "cockpit-overseer/recovery-mission"
CONTROLLER_TICK_ACTOR = "cockpit-overseer"
DEFAULT_CONTROLLER_TICK_COMMAND = "cockpit-overseer tick"

# The derived ledger projection every controller decision folds into.
LEDGER_CONTROLLER_FIELD = "controller"
LEDGER_CONTROLLER_FIELDS = (
    "outcome",
    "reason",
    "state_key",
    "queue_item_id",
    "worker_id",
    "mission_id",
    "command_id",
    "revision",
    "event_id",
    "recorded_at",
)


class ControlStoreError(RuntimeError):
    """Raised when a control-store configuration is unsafe to use."""


ResolvedControlRoot = control_root_schema.ResolvedControlRoot


@dataclass(frozen=True)
class StoreInitialization:
    """Result of an idempotent control-store initialization attempt."""

    root: Path
    source: str
    created: bool


def utc_timestamp() -> str:
    """Return a UTC RFC 3339 timestamp suitable for a canonical record."""

    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


# The shell variable each refusal about a root must name.  A diagnostic that
# names the wrong variable sends an operator to repair a setting that is already
# correct, so the boundary being checked is passed in rather than assumed.
CONTROL_ROOT_VARIABLE = control_root_schema.CONTROL_ROOT_VARIABLE
QUEUE_ROOT_VARIABLE = control_root_schema.QUEUE_ROOT_VARIABLE


def _require_absolute_root(
    value: str,
    source: str,
    variable: str = CONTROL_ROOT_VARIABLE,
) -> Path:
    try:
        return control_root_schema.require_absolute_root(value, source, variable)
    except control_root_schema.ControlRootSchemaError as exc:
        raise ControlStoreError(str(exc)) from None


def _tmux_control_root() -> Optional[str]:
    """Read the exact root from the active tmux server, if there is one."""

    return control_root_schema.tmux_control_root()


def resolve_control_root(environ: Optional[Mapping[str, str]] = None) -> ResolvedControlRoot:
    """Resolve the root from the shell, or solely from the active tmux session.

    A shell value is authoritative even when invalid: it must fail rather than
    silently falling back to a different session's store.  No code path consults
    the current working directory.
    """

    try:
        return control_root_schema.resolve_control_root(environ=environ)
    except control_root_schema.ControlRootSchemaError as exc:
        raise ControlStoreError(str(exc)) from None


def _require_object(record: Any, label: str) -> Dict[str, Any]:
    if not isinstance(record, dict):
        raise ControlStoreError(f"{label} must be a JSON object")
    return record


def _require_schema_version(record: Mapping[str, Any], label: str) -> None:
    try:
        control_root_schema.require_schema_version(record, label)
    except control_root_schema.ControlRootSchemaError as exc:
        raise ControlStoreError(str(exc)) from None


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


def validate_worker_missions(value: Any, label: str) -> Dict[str, Any]:
    """Validate the materialized worker mission slots the derived ledger holds.

    Each slot is keyed by its mission ID and embeds the exact lifecycle record
    that last advanced it, together with the committed revision and event that
    published it.  The projection is therefore auditable back to one immutable
    event file, and a hand-edited ledger fails closed instead of being trusted.
    """

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_WORKER_MISSIONS_FIELD}")
    for mission_id in sorted(value):
        slot_label = f"{label} {LEDGER_WORKER_MISSIONS_FIELD}[{mission_id}]"
        slot = value[mission_id]
        if not isinstance(slot, dict):
            raise ControlStoreError(f"{slot_label} must be a JSON object")
        unknown = sorted(set(slot) - set(LEDGER_WORKER_MISSION_FIELDS))
        if unknown:
            raise ControlStoreError(f"{slot_label} declares unknown field(s) {', '.join(unknown)}")
        missing = [field for field in LEDGER_WORKER_MISSION_FIELDS if field not in slot]
        if missing:
            raise ControlStoreError(f"{slot_label} requires {', '.join(missing)}")
        lifecycle = validate_worker_lifecycle(slot["lifecycle"], f"{slot_label} lifecycle")
        if lifecycle["mission_id"] != mission_id:
            raise ControlStoreError(
                f"{slot_label} holds lifecycle for mission {lifecycle['mission_id']}"
            )
        _require_positive_integer(slot, "revision", slot_label)
        _require_uuid(slot, "event_id", slot_label)
        _require_timestamp(slot, "recorded_at", slot_label)
    return value


def validate_commands(value: Any, label: str) -> Dict[str, Any]:
    """Validate the materialized command slots the derived ledger holds.

    Each slot is keyed by its command ID and embeds the exact envelope that
    registered it, every delivery of it, every acknowledgement of it, and every
    conflict recorded against it, each with the committed revision and event
    that published it.  The projection is therefore auditable back to immutable
    event files, and a hand-edited ledger fails closed instead of being trusted.
    """

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_COMMANDS_FIELD}")
    for command_id in sorted(value):
        slot_label = f"{label} {LEDGER_COMMANDS_FIELD}[{command_id}]"
        slot = value[command_id]
        if not isinstance(slot, dict):
            raise ControlStoreError(f"{slot_label} must be a JSON object")
        _require_closed_fields(slot, LEDGER_COMMAND_FIELDS, slot_label)
        envelope = validate_command_envelope(slot["envelope"], f"{slot_label} envelope")
        if envelope["command_id"] != command_id:
            raise ControlStoreError(
                f"{slot_label} holds the envelope of command {envelope['command_id']}"
            )
        status = _require_string(slot, "status", slot_label)
        if status not in COMMAND_STATUSES:
            raise ControlStoreError(
                f"{slot_label} declares unknown status {status!r}; expected one of "
                f"{', '.join(COMMAND_STATUSES)}"
            )
        _require_positive_integer(slot, "revision", slot_label)
        _require_uuid(slot, "event_id", slot_label)
        _require_timestamp(slot, "recorded_at", slot_label)

        deliveries = slot["deliveries"]
        if not isinstance(deliveries, list) or not deliveries:
            raise ControlStoreError(f"{slot_label} requires at least one delivery")
        for index, delivery in enumerate(deliveries):
            entry_label = f"{slot_label} deliveries[{index}]"
            if not isinstance(delivery, dict):
                raise ControlStoreError(f"{entry_label} must be a JSON object")
            _require_closed_fields(delivery, LEDGER_COMMAND_DELIVERY_FIELDS, entry_label)
            _require_positive_integer(delivery, "revision", entry_label)
            _require_uuid(delivery, "event_id", entry_label)
            _require_timestamp(delivery, "recorded_at", entry_label)

        acknowledgements = slot["acknowledgements"]
        if not isinstance(acknowledgements, list):
            raise ControlStoreError(f"{slot_label} requires array acknowledgements")
        for index, entry in enumerate(acknowledgements):
            entry_label = f"{slot_label} acknowledgements[{index}]"
            if not isinstance(entry, dict):
                raise ControlStoreError(f"{entry_label} must be a JSON object")
            _require_closed_fields(
                entry, LEDGER_COMMAND_ACKNOWLEDGEMENT_FIELDS, entry_label
            )
            acknowledgement = validate_command_acknowledgement(
                entry["acknowledgement"], f"{entry_label} acknowledgement"
            )
            if acknowledgement["command_id"] != command_id:
                raise ControlStoreError(
                    f"{entry_label} acknowledges command {acknowledgement['command_id']}"
                )
            _require_positive_integer(entry, "revision", entry_label)
            _require_uuid(entry, "event_id", entry_label)
            _require_timestamp(entry, "recorded_at", entry_label)

        conflicts = slot["conflicts"]
        if not isinstance(conflicts, list):
            raise ControlStoreError(f"{slot_label} requires array conflicts")
        for index, conflict in enumerate(conflicts):
            entry_label = f"{slot_label} conflicts[{index}]"
            if not isinstance(conflict, dict):
                raise ControlStoreError(f"{entry_label} must be a JSON object")
            _require_closed_fields(conflict, LEDGER_COMMAND_CONFLICT_FIELDS, entry_label)
            _require_digest(conflict, "payload_digest", entry_label)
            reason = _require_string(conflict, "reason", entry_label)
            if reason not in COMMAND_CONFLICT_REASONS:
                raise ControlStoreError(
                    f"{entry_label} declares unknown reason {reason!r}; expected one of "
                    f"{', '.join(COMMAND_CONFLICT_REASONS)}"
                )
            conflict_source = _require_string(conflict, "source", entry_label)
            if conflict_source not in COMMAND_CONFLICT_SOURCES:
                raise ControlStoreError(
                    f"{entry_label} declares unknown source {conflict_source!r}; expected "
                    f"one of {', '.join(COMMAND_CONFLICT_SOURCES)}"
                )
            _require_positive_integer(conflict, "revision", entry_label)
            _require_uuid(conflict, "event_id", entry_label)
            _require_timestamp(conflict, "recorded_at", entry_label)
    return value


def validate_mission_dialogs(value: Any, label: str) -> Dict[str, Any]:
    """Validate the materialized mission dialogs the derived ledger holds.

    Each dialog is keyed by the command ID that carried it and embeds the exact
    record that was committed, so the projection is auditable back to one
    immutable event file and a hand-edited ledger fails closed instead of being
    trusted.  A prompt is `pending` until exactly one correlated response
    answers it, and only an answered prompt names its answering command.
    """

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_MISSION_DIALOGS_FIELD}")
    for command_id in sorted(value):
        slot_label = f"{label} {LEDGER_MISSION_DIALOGS_FIELD}[{command_id}]"
        slot = value[command_id]
        if not isinstance(slot, dict):
            raise ControlStoreError(f"{slot_label} must be a JSON object")
        _require_closed_fields(slot, LEDGER_MISSION_DIALOG_FIELDS, slot_label)
        dialog = validate_mission_dialog(slot["dialog"], f"{slot_label} dialog")
        if dialog["command_id"] != command_id:
            raise ControlStoreError(
                f"{slot_label} holds the dialog of command {dialog['command_id']}"
            )
        state = _require_string(slot, "state", slot_label)
        if state not in MISSION_DIALOG_STATES:
            raise ControlStoreError(
                f"{slot_label} declares unknown state {state!r}; expected one of "
                f"{', '.join(MISSION_DIALOG_STATES)}"
            )
        if state == MISSION_DIALOG_ANSWERED:
            _require_uuid(slot, "answered_by_command_id", slot_label)
            _require_timestamp(slot, "answered_at", slot_label)
        else:
            for field in ("answered_by_command_id", "answered_at"):
                if slot[field] is not None:
                    raise ControlStoreError(
                        f"{slot_label} must not declare {field} for state {state!r}"
                    )
        _require_positive_integer(slot, "revision", slot_label)
        _require_uuid(slot, "event_id", slot_label)
        _require_timestamp(slot, "recorded_at", slot_label)
    return value


def validate_mission_cancellations(value: Any, label: str) -> Dict[str, Any]:
    """Validate the materialized cancellation requests the derived ledger holds."""

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_MISSION_CANCELLATIONS_FIELD}")
    for command_id in sorted(value):
        slot_label = f"{label} {LEDGER_MISSION_CANCELLATIONS_FIELD}[{command_id}]"
        slot = value[command_id]
        if not isinstance(slot, dict):
            raise ControlStoreError(f"{slot_label} must be a JSON object")
        _require_closed_fields(slot, LEDGER_MISSION_CANCELLATION_FIELDS, slot_label)
        cancellation = validate_mission_cancellation(
            slot["cancellation"], f"{slot_label} cancellation"
        )
        if cancellation["command_id"] != command_id:
            raise ControlStoreError(
                f"{slot_label} holds the cancellation of command {cancellation['command_id']}"
            )
        _require_positive_integer(slot, "revision", slot_label)
        _require_uuid(slot, "event_id", slot_label)
        _require_timestamp(slot, "recorded_at", slot_label)
    return value


def validate_mission_recoveries(value: Any, label: str) -> Dict[str, Any]:
    """Validate every committed bounded recovery request the ledger projects."""

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_MISSION_RECOVERIES_FIELD}")
    for command_id in sorted(value):
        entry_label = f"{label} {LEDGER_MISSION_RECOVERIES_FIELD}[{command_id}]"
        entry = value[command_id]
        if not isinstance(entry, dict):
            raise ControlStoreError(f"{entry_label} must be a JSON object")
        _require_closed_fields(entry, LEDGER_MISSION_RECOVERY_FIELDS, entry_label)
        recovery = validate_mission_recovery(entry["recovery"], f"{entry_label} recovery")
        if recovery["command_id"] != command_id:
            raise ControlStoreError(f"{entry_label} is keyed by a different command_id")
        _require_positive_integer(entry, "revision", entry_label)
        _require_uuid(entry, "event_id", entry_label)
        _require_timestamp(entry, "recorded_at", entry_label)
    return value


def validate_mission_slots(value: Any, label: str) -> Dict[str, Any]:
    """Validate the one mission slot each worker holds in the derived ledger.

    ADR-013 gives every worker exactly one mission slot, so this projection is
    keyed by worker and holds at most one claimed mission each.  Every claim that
    could not take an occupied slot is retained here as a durable conflict rather
    than being discarded.
    """

    if not isinstance(value, dict):
        raise ControlStoreError(f"{label} requires object {LEDGER_MISSION_SLOTS_FIELD}")
    for worker_id in sorted(value):
        slot_label = f"{label} {LEDGER_MISSION_SLOTS_FIELD}[{worker_id}]"
        slot = value[worker_id]
        if not isinstance(slot, dict):
            raise ControlStoreError(f"{slot_label} must be a JSON object")
        _require_closed_fields(slot, LEDGER_MISSION_SLOT_FIELDS, slot_label)
        _require_identifier({"worker_id": worker_id}, "worker_id", slot_label)
        state = _require_string(slot, "state", slot_label)
        if state not in MISSION_SLOT_STATES:
            raise ControlStoreError(
                f"{slot_label} declares unknown state {state!r}; expected one of "
                f"{', '.join(MISSION_SLOT_STATES)}"
            )
        if state == MISSION_SLOT_UNCLAIMED:
            for field in ("mission_id", "queue_item_id", "command_id", "replaces"):
                if slot[field] is not None:
                    raise ControlStoreError(
                        f"{slot_label} must not declare {field} for state {state!r}"
                    )
        else:
            _require_uuid(slot, "mission_id", slot_label)
            _require_identifier(slot, "queue_item_id", slot_label)
            _require_optional_uuid(slot, "command_id", slot_label)
            if _require_optional_uuid(slot, "replaces", slot_label) == slot["mission_id"]:
                raise ControlStoreError(
                    f"{slot_label} requires replaces to name a different mission"
                )
        _require_positive_integer(slot, "revision", slot_label)
        _require_uuid(slot, "event_id", slot_label)
        _require_timestamp(slot, "recorded_at", slot_label)

        conflicts = slot["conflicts"]
        if not isinstance(conflicts, list):
            raise ControlStoreError(f"{slot_label} requires array conflicts")
        for index, conflict in enumerate(conflicts):
            entry_label = f"{slot_label} conflicts[{index}]"
            if not isinstance(conflict, dict):
                raise ControlStoreError(f"{entry_label} must be a JSON object")
            _require_closed_fields(conflict, LEDGER_MISSION_SLOT_CONFLICT_FIELDS, entry_label)
            _require_uuid(conflict, "mission_id", entry_label)
            reason = _require_string(conflict, "reason", entry_label)
            if reason not in MISSION_SLOT_CONFLICT_REASONS:
                raise ControlStoreError(
                    f"{entry_label} declares unknown reason {reason!r}; expected one of "
                    f"{', '.join(MISSION_SLOT_CONFLICT_REASONS)}"
                )
            conflict_source = _require_string(conflict, "source", entry_label)
            if conflict_source not in MISSION_SLOT_CONFLICT_SOURCES:
                raise ControlStoreError(
                    f"{entry_label} declares unknown source {conflict_source!r}; expected "
                    f"one of {', '.join(MISSION_SLOT_CONFLICT_SOURCES)}"
                )
            _require_optional_uuid(conflict, "command_id", entry_label)
            _require_positive_integer(conflict, "revision", entry_label)
            _require_uuid(conflict, "event_id", entry_label)
            _require_timestamp(conflict, "recorded_at", entry_label)
    return value


def validate_controller_projection(value: Any, label: str) -> Optional[Dict[str, Any]]:
    """Validate the one materialized controller decision the ledger carries.

    A control root that has never been ticked holds an explicit `null` here
    rather than an absent field, so "no controller has ever run" and "the
    projection lost its controller state" are different, diagnosable facts.
    """

    if value is None:
        return None
    entry_label = f"{label} {LEDGER_CONTROLLER_FIELD}"
    if not isinstance(value, dict):
        raise ControlStoreError(f"{entry_label} must be a JSON object or null")
    _require_closed_fields(value, LEDGER_CONTROLLER_FIELDS, entry_label)
    outcome = _require_string(value, "outcome", entry_label)
    if outcome not in CONTROLLER_OUTCOMES:
        raise ControlStoreError(
            f"{entry_label} declares unknown outcome {outcome!r}; expected one of "
            f"{', '.join(CONTROLLER_OUTCOMES)}"
        )
    reason = _require_controller_reason(value, entry_label, CONTROLLER_REASONS)
    if (reason in CONTROLLER_DISPATCH_REASONS) != (outcome == CONTROLLER_DISPATCHED):
        raise ControlStoreError(
            f"{entry_label} declares outcome {outcome!r} for reason {reason!r}; only a "
            "dispatch reason is ever recorded as dispatched"
        )
    _require_digest(value, "state_key", entry_label)
    _require_optional_identifier(value, "queue_item_id", entry_label)
    _require_optional_identifier(value, "worker_id", entry_label)
    _require_optional_uuid(value, "mission_id", entry_label)
    _require_optional_uuid(value, "command_id", entry_label)
    _require_positive_integer(value, "revision", entry_label)
    _require_uuid(value, "event_id", entry_label)
    _require_timestamp(value, "recorded_at", entry_label)
    return value


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
    if LEDGER_WORKER_MISSIONS_FIELD not in data:
        raise ControlStoreError(f"{label} requires {LEDGER_WORKER_MISSIONS_FIELD}")
    validate_worker_missions(data[LEDGER_WORKER_MISSIONS_FIELD], label)
    if LEDGER_COMMANDS_FIELD not in data:
        raise ControlStoreError(f"{label} requires {LEDGER_COMMANDS_FIELD}")
    validate_commands(data[LEDGER_COMMANDS_FIELD], label)
    for field, validator in (
        (LEDGER_MISSION_DIALOGS_FIELD, validate_mission_dialogs),
        (LEDGER_MISSION_CANCELLATIONS_FIELD, validate_mission_cancellations),
        (LEDGER_MISSION_RECOVERIES_FIELD, validate_mission_recoveries),
        (LEDGER_MISSION_SLOTS_FIELD, validate_mission_slots),
        (LEDGER_CONTROLLER_FIELD, validate_controller_projection),
    ):
        if field not in data:
            raise ControlStoreError(f"{label} requires {field}")
        validator(data[field], label)
    return data


def _require_positive_integer(record: Mapping[str, Any], field: str, label: str) -> int:
    """Require a counting field that a boolean or a float can never satisfy."""

    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ControlStoreError(f"{label} requires positive integer {field}")
    return value


def _require_identifier(record: Mapping[str, Any], field: str, label: str) -> str:
    """Require a correlation identifier that can never forge a record boundary.

    Worker-controlled identity strings are rendered verbatim into the
    line-oriented `record-lifecycle` and `lifecycle-status` stdout payload, so a
    value carrying a newline, a tab, or any other control character could forge
    an entire additional, fully-formed status record and break the
    machine-readable correlation guarantee.  ADR-013 and architecture section 6
    reject text-as-protocol, so an identifier is restricted to printable
    non-whitespace ASCII: nothing outside that set is a legitimate correlation
    key, and nothing inside it can create a field or record boundary.
    """

    value = _require_string(record, field, label)
    for character in value:
        if not character.isascii() or character.isspace() or not character.isprintable():
            raise ControlStoreError(
                f"{label} requires printable non-whitespace ASCII {field}; {value!r} could "
                "forge a record boundary in the line-oriented lifecycle payload"
            )
    return value


def _require_optional_identifier(
    record: Mapping[str, Any],
    field: str,
    label: str,
) -> Optional[str]:
    """Require a nullable correlation identifier without defaulting a missing one."""

    if field not in record:
        raise ControlStoreError(f"{label} requires {field}")
    if record[field] is None:
        return None
    return _require_identifier(record, field, label)


def _parsed_timestamp(value: Any, field: str, label: str) -> datetime:
    """Parse one validated UTC timestamp into a comparable aware datetime."""

    _require_timestamp({field: value}, field, label)
    return datetime.fromisoformat(str(value)[:-1] + "+00:00")


def _shifted_timestamp(
    base: str,
    seconds: float,
    field: str = "heartbeat_at",
    label: str = "worker lifecycle",
    option: str = "--fresh-for",
    noun: str = "freshness deadline",
) -> str:
    """Return the canonical UTC timestamp `seconds` after a validated base."""

    moment = _parsed_timestamp(base, field, label)
    # A window large enough to leave the representable timestamp range is a
    # refusal with a diagnostic, never an uncaught OverflowError traceback that
    # would leak internal paths and escape the fail-closed contract.
    try:
        shifted = moment + timedelta(seconds=seconds)
    except (OverflowError, ValueError, OSError):
        raise ControlStoreError(
            f"{option} produces a {noun} outside the representable timestamp range"
        ) from None
    return shifted.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_typed_references(
    record: Mapping[str, Any],
    name: str,
    label: str,
    *,
    required: bool,
    noun: str,
) -> List[str]:
    """Require typed `<type>:<value>` references rather than free text.

    ADR-016 keeps canonical records metadata-only, so evidence, declared runtime
    boundaries, and stored results are all carried as typed references an
    operator can resolve later.  Anything shapeless, blank, whitespace-bearing,
    or duplicated is refused before it can be committed, which also keeps every
    such value safe to render into a line-oriented payload.
    """

    if name not in record:
        raise ControlStoreError(f"{label} requires {name}")
    value = record[name]
    if not isinstance(value, list):
        raise ControlStoreError(f"{label} requires array {name}")
    if required and not value:
        raise ControlStoreError(f"{label} requires at least one {noun} reference")
    accepted: List[str] = []
    for index, reference in enumerate(value):
        field = f"{name}[{index}]"
        if not isinstance(reference, str) or not reference:
            raise ControlStoreError(f"{label} requires non-empty string {field}")
        if reference.split() != [reference]:
            raise ControlStoreError(f"{label} {field} must not contain whitespace")
        kind, separator, identifier = reference.partition(":")
        if not separator or not identifier:
            raise ControlStoreError(
                f"{label} {field} must be a typed '<type>:<value>' {noun} reference"
            )
        if not kind[:1].isascii() or not kind[:1].isalpha() or not all(
            character.isascii() and (character.islower() or character.isdigit() or character == "-")
            for character in kind
        ):
            raise ControlStoreError(
                f"{label} {field} must name a lowercase {noun} type before ':'"
            )
        if reference in accepted:
            raise ControlStoreError(f"{label} duplicates {noun} reference {reference}")
        accepted.append(reference)
    return accepted


def _require_evidence_refs(record: Mapping[str, Any], label: str, *, required: bool) -> List[str]:
    """Require the typed evidence references a lifecycle record carries."""

    return _require_typed_references(
        record, "evidence_refs", label, required=required, noun="evidence"
    )


def _require_blocker(record: Mapping[str, Any], label: str, state: str) -> Optional[Dict[str, Any]]:
    """Require a categorized blocker for `blocked` and nothing at all elsewhere."""

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
        raise ControlStoreError(
            f"{label} blocker declares unknown field(s) {', '.join(unknown)}"
        )
    _require_string(value, "category", f"{label} blocker")
    _require_optional_string(value, "detail", f"{label} blocker")
    return value


def validate_worker_lifecycle(record: Any, label: str = "worker lifecycle") -> Dict[str, Any]:
    """Validate one versioned worker lifecycle record, failing closed throughout.

    The record is a closed vocabulary: an unknown or missing field, an unknown
    state, a missing correlation identifier, an unversioned or future-versioned
    record, freshness on a terminal state, or a terminal state without a reason
    is refused rather than partially understood.  Nothing here consults derived
    state, so the same validation applies to a record about to be published and
    to one already committed.
    """

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
    # `worker_id` and `queue_item_id` are rendered into a parseable position of
    # the line-oriented lifecycle payload, so they are constrained to identifier
    # characters.  `reason`, `blocker.category`, and `blocker.detail` are prose
    # that no command renders into a parseable position, so they stay free text
    # rather than being over-constrained; `evidence_refs` are already required to
    # be whitespace-free typed references.
    _require_identifier(data, "worker_id", label)
    mission_id = _require_uuid(data, "mission_id", label)
    _require_identifier(data, "queue_item_id", label)
    _require_uuid(data, "trace_id", label)
    _require_optional_uuid(data, "parent_trace_id", label)
    _require_positive_integer(data, "sequence", label)

    if _require_optional_string(data, "reason", label) is None:
        if state in WORKER_LIFECYCLE_REASON_REQUIRED:
            raise ControlStoreError(f"{label} requires a reason for state {state!r}")
    _require_blocker(data, label, state)
    # A completion that references no evidence cannot be reviewed later, so the
    # happy path is required to carry its own references.
    _require_evidence_refs(data, label, required=state == LIFECYCLE_COMPLETED)

    if state in WORKER_LIFECYCLE_ACTIVE_STATES:
        heartbeat = _parsed_timestamp(
            _require_string(data, "heartbeat_at", label), "heartbeat_at", label
        )
        expiry = _parsed_timestamp(
            _require_string(data, "fresh_until", label), "fresh_until", label
        )
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
    """Return the lifecycle record one event declares, or None, failing closed.

    The lifecycle event type and the lifecycle payload are two halves of one
    contract.  A lifecycle event type without a record, a record without the
    matching event type, or a record whose declared state disagrees with its
    event type is refused, so `list-events` and the projection can never read a
    different state from the same committed file.
    """

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
        payload[WORKER_LIFECYCLE_PAYLOAD_FIELD],
        f"{label} payload.{WORKER_LIFECYCLE_PAYLOAD_FIELD}",
    )
    expected = f"{WORKER_LIFECYCLE_EVENT_PREFIX}{lifecycle['state']}"
    if event_type != expected:
        raise ControlStoreError(
            f"{label} declares lifecycle state {lifecycle['state']!r} but event_type "
            f"{event_type!r}; expected event_type {expected!r}"
        )
    return lifecycle


def _require_string_keys(value: Any, label: str) -> None:
    """Refuse any payload whose object keys are not strings.

    A non-string key would either be refused by the serializer or silently
    coerced, and a coerced key would let two different payloads share one
    digest.  Refusing it keeps the digest a total, injective-by-construction
    function of the canonical serialization.
    """

    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise ControlStoreError(f"{label} requires string field names")
            _require_string_keys(value[key], label)
    elif isinstance(value, list):
        for item in value:
            _require_string_keys(item, label)


def canonical_command_payload(payload: Any, label: str = "command payload") -> str:
    """Render the one canonical serialization a command payload is digested from.

    Keys are sorted, separators are fixed, non-ASCII characters are escaped, and
    non-finite numbers are refused, so two processes on two hosts serialize the
    same logical payload into exactly the same bytes.
    """

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
    # A payload nested more deeply than the interpreter can walk is refused with
    # a diagnostic instead of escaping as a RecursionError traceback that would
    # leak internal paths and break the fail-closed contract.
    except RecursionError:
        raise ControlStoreError(
            f"{label} is nested too deeply to serialize canonically"
        ) from None
    except (TypeError, ValueError) as exc:
        raise ControlStoreError(f"cannot serialize {label}: {exc}") from None


def command_payload_digest(payload: Any, label: str = "command payload") -> str:
    """Return the deterministic `sha256:<hex>` digest of one command payload."""

    canonical = canonical_command_payload(payload, label).encode("utf-8")
    return f"{COMMAND_DIGEST_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


def _require_digest(record: Mapping[str, Any], field: str, label: str) -> str:
    """Require one exactly-shaped payload digest and nothing else."""

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
    """Require one closed, identifier-safe delivery target."""

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
    # The target identifier is rendered into a parseable position of the
    # line-oriented command payload, so it is constrained exactly like every
    # other correlation identifier.
    _require_identifier(value, "id", f"{label} target")
    return value


def _require_boundary_root(value: Any, field: str, label: str) -> str:
    """Require one declared mission root that cannot forge a record boundary.

    A declared boundary is operator- or worker-supplied, so besides being a
    normalized absolute path it must contain no control character: a newline in
    a declared root would otherwise be able to forge a whole additional record
    in a line-oriented payload.
    """

    root = _require_root_path(value, field, label)
    for character in root:
        if not character.isprintable():
            raise ControlStoreError(
                f"{label} requires a printable {field}; {root!r} contains a control "
                "character that could forge a record boundary"
            )
    return root


def _require_command_boundaries(record: Mapping[str, Any], label: str) -> Dict[str, Any]:
    """Require the complete ADR-016 mission boundary declaration.

    The declaration itself is never optional: a command that declares no
    boundaries at all is refused.  Individual roots may be explicitly
    unassigned, because a store can be initialized before a mission declares a
    queue, planning, or implementation boundary, but an unassigned root is an
    explicit `null` rather than an absent field.
    """

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
    """Require the exact command-protocol version this tool implements."""

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
    """Require exactly the closed field set a versioned record declares."""

    unknown = sorted(set(record) - set(fields))
    if unknown:
        raise ControlStoreError(f"{label} declares unknown field(s) {', '.join(unknown)}")
    missing = [field for field in fields if field not in record]
    if missing:
        raise ControlStoreError(f"{label} requires {', '.join(missing)}")


def validate_command_envelope(record: Any, label: str = "command envelope") -> Dict[str, Any]:
    """Validate one versioned command envelope, failing closed throughout.

    The envelope is the durable record of one state-changing worker operation:
    its command ID, mission and queue item, delivery target, trace and parent
    trace, schema version, payload digest, and declared mission boundaries.  An
    unknown or missing field, an unversioned or future-versioned record, a
    malformed digest, an unknown target kind, or a boundary declaration that is
    not a normalized absolute path is refused rather than partially understood.
    """

    data = _require_object(record, label)
    _require_command_schema_version(data, label)
    _require_record_type(data, COMMAND_ENVELOPE_RECORD_TYPE, label)
    _require_closed_fields(data, COMMAND_ENVELOPE_FIELDS, label)

    _require_uuid(data, "command_id", label)
    # `command_type`, `queue_item_id`, and the target identifier are rendered
    # into parseable positions of the line-oriented command payload, so they are
    # constrained to identifier characters that cannot forge a record boundary.
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
    """Validate one versioned command acknowledgement, failing closed throughout.

    An acknowledgement names the command it answers, the exact payload digest
    the acknowledger observed, one outcome from the closed ADR-013 vocabulary,
    and who acknowledged it.  A refusal or a deduplication must explain itself,
    and an application must carry at least one typed reference to the result it
    stored, so a later redelivery has a stored result to return.
    """

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
    # The acknowledger identity is rendered into a parseable position of the
    # line-oriented command payload; `reason` is prose that no command renders
    # into a parseable position, so it stays free text.
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
    """Return the command envelope one event declares, or None, failing closed."""

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
    """Return the acknowledgement one event declares, or None, failing closed."""

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
    if event_type != expected:
        raise ControlStoreError(
            f"{label} declares acknowledgement outcome {acknowledgement['outcome']!r} but "
            f"event_type {event_type!r}; expected event_type {expected!r}"
        )
    return acknowledgement


def _require_mission_control_schema_version(record: Mapping[str, Any], label: str) -> None:
    """Require the exact mission-control protocol version this tool implements."""

    version = record.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlStoreError(f"{label} requires integer schema_version")
    if version > MISSION_CONTROL_SCHEMA_VERSION:
        raise ControlStoreError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit "
            "tools before mutation"
        )
    if version != MISSION_CONTROL_SCHEMA_VERSION:
        raise ControlStoreError(f"{label} uses unsupported schema_version {version}")


def _require_mission_correlation(record: Mapping[str, Any], label: str) -> None:
    """Require the correlation identity every managed mission record carries.

    `worker_id` and `queue_item_id` are rendered into parseable positions of the
    line-oriented mission payload, so they are constrained exactly like every
    other correlation identifier this protocol accepts, and a child trace may
    never claim to be its own parent.
    """

    _require_identifier(record, "worker_id", label)
    _require_identifier(record, "queue_item_id", label)
    trace_id = _require_uuid(record, "trace_id", label)
    parent_trace_id = _require_optional_uuid(record, "parent_trace_id", label)
    if parent_trace_id is not None and parent_trace_id == trace_id:
        raise ControlStoreError(f"{label} requires parent_trace_id to name a different trace")


def validate_mission_dialog(record: Any, label: str = "mission dialog") -> Dict[str, Any]:
    """Validate one versioned mission dialog record, failing closed throughout.

    A dialog is the durable correlation between one managed command and the
    mission it belongs to: a worker-raised question or access prompt, or the
    overseer response that resolves exactly one of them.  The body of the
    question or the answer is never part of the record; the carrying command
    envelope digests it and `body_refs` says, as typed references, where it
    lives.  An unknown or missing field, an unknown dialog kind, an unversioned
    or future-versioned record, a prompt claiming to answer something, or a
    response answering nothing is refused rather than partially understood.
    """

    data = _require_object(record, label)
    _require_mission_control_schema_version(data, label)
    _require_record_type(data, MISSION_DIALOG_RECORD_TYPE, label)
    _require_closed_fields(data, MISSION_DIALOG_FIELDS, label)

    command_id = _require_uuid(data, "command_id", label)
    kind = _require_string(data, "kind", label)
    if kind not in MISSION_DIALOG_KINDS:
        raise ControlStoreError(
            f"{label} declares unknown kind {kind!r}; expected one of "
            f"{', '.join(MISSION_DIALOG_KINDS)}"
        )
    _require_uuid(data, "mission_id", label)
    _require_mission_correlation(data, label)
    # The category is rendered into a parseable position of the line-oriented
    # mission payload, so a worker- or operator-chosen category is constrained
    # to identifier characters that cannot forge a field or record boundary.
    _require_identifier(data, "category", label)
    _require_typed_references(data, "body_refs", label, required=True, noun="dialog body")
    _require_timestamp(data, "raised_at", label)

    if kind in MISSION_DIALOG_PROMPT_KINDS:
        if data["answers_command_id"] is not None:
            raise ControlStoreError(
                f"{label} must not declare answers_command_id for prompt kind {kind!r}"
            )
    elif _require_uuid(data, "answers_command_id", label) == command_id:
        raise ControlStoreError(
            f"{label} requires answers_command_id to name a different command"
        )
    return data


def validate_mission_cancellation(
    record: Any,
    label: str = "mission cancellation",
) -> Dict[str, Any]:
    """Validate one versioned cooperative cancellation request, failing closed.

    A cancellation always explains itself and always declares the moment by
    which an acknowledgement is expected, because an undeclared deadline could
    never be observed as timed out without inventing an unstated policy.
    """

    data = _require_object(record, label)
    _require_mission_control_schema_version(data, label)
    _require_record_type(data, MISSION_CANCELLATION_RECORD_TYPE, label)
    _require_closed_fields(data, MISSION_CANCELLATION_FIELDS, label)

    _require_uuid(data, "command_id", label)
    _require_uuid(data, "mission_id", label)
    _require_mission_correlation(data, label)
    # `reason` is prose that no command renders into a parseable position, so it
    # stays free text rather than being over-constrained.
    _require_string(data, "reason", label)
    _require_evidence_refs(data, label, required=False)
    requested_at = _require_timestamp(data, "requested_at", label)
    deadline = _parsed_timestamp(
        _require_string(data, "acknowledge_deadline_at", label),
        "acknowledge_deadline_at",
        label,
    )
    if deadline <= _parsed_timestamp(requested_at, "requested_at", label):
        raise ControlStoreError(f"{label} requires acknowledge_deadline_at after requested_at")
    return data


def validate_mission_replacement(
    record: Any,
    label: str = "mission replacement",
) -> Dict[str, Any]:
    """Validate one versioned mission replacement request, failing closed.

    A replacement names the mission it terminates and the new mission ID it
    creates.  Reusing the terminated mission ID is refused here rather than
    discovered later, because architecture section 10 forbids `replace-mission`
    from reusing the cancelled mission ID at all.
    """

    data = _require_object(record, label)
    _require_mission_control_schema_version(data, label)
    _require_record_type(data, MISSION_REPLACEMENT_RECORD_TYPE, label)
    _require_closed_fields(data, MISSION_REPLACEMENT_FIELDS, label)

    _require_uuid(data, "command_id", label)
    _require_mission_correlation(data, label)
    replaced = _require_uuid(data, "replaced_mission_id", label)
    if _require_uuid(data, "replacement_mission_id", label) == replaced:
        raise ControlStoreError(
            f"{label} requires replacement_mission_id to name a different mission"
        )
    _require_string(data, "reason", label)
    _require_evidence_refs(data, label, required=False)
    _require_timestamp(data, "requested_at", label)
    return data


def validate_mission_recovery(
    record: Any,
    label: str = "mission recovery",
) -> Dict[str, Any]:
    """Validate one versioned bounded recovery request, failing closed.

    A recovery request is the controller asking one worker to prove it is still
    carrying the mission the control plane believes it has.  It declares which
    rung of the fixed ladder it is, why recovery was owed, and the explicit
    moment a response is expected by -- exactly like a cooperative cancellation,
    and for the same reason: a rung whose response window was never declared
    could not be observed as elapsed, so the ladder could only advance by
    guessing.  Nothing here claims a mission state: `stale` is an observation,
    and a mission ends only through a worker lifecycle event or a human decision.
    """

    data = _require_object(record, label)
    _require_mission_control_schema_version(data, label)
    _require_record_type(data, MISSION_RECOVERY_RECORD_TYPE, label)
    _require_closed_fields(data, MISSION_RECOVERY_FIELDS, label)

    _require_uuid(data, "command_id", label)
    action = _require_string(data, "action", label)
    if action not in MISSION_RECOVERY_ACTIONS:
        raise ControlStoreError(
            f"{label} declares unknown action {action!r}; expected one of "
            f"{', '.join(MISSION_RECOVERY_ACTIONS)}"
        )
    _require_uuid(data, "mission_id", label)
    _require_mission_correlation(data, label)
    reason = _require_string(data, "reason", label)
    if reason not in MISSION_RECOVERY_REASONS:
        raise ControlStoreError(
            f"{label} declares unknown reason {reason!r}; expected one of "
            f"{', '.join(MISSION_RECOVERY_REASONS)}"
        )
    _require_evidence_refs(data, label, required=True)
    requested_at = _require_timestamp(data, "requested_at", label)
    deadline = _parsed_timestamp(
        _require_string(data, "respond_deadline_at", label),
        "respond_deadline_at",
        label,
    )
    if deadline <= _parsed_timestamp(requested_at, "requested_at", label):
        raise ControlStoreError(f"{label} requires respond_deadline_at after requested_at")
    return data


def _require_controller_schema_version(record: Mapping[str, Any], label: str) -> None:
    """Require the exact reconciliation contract version this tool implements."""

    version = record.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlStoreError(f"{label} requires integer schema_version")
    if version > CONTROLLER_SCHEMA_VERSION:
        raise ControlStoreError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit "
            "tools before mutation"
        )
    if version != CONTROLLER_SCHEMA_VERSION:
        raise ControlStoreError(f"{label} uses unsupported schema_version {version}")


def _require_controller_reason(
    record: Mapping[str, Any],
    label: str,
    allowed: Sequence[str],
) -> str:
    """Require one reason from the closed reconciliation vocabulary."""

    reason = _require_string(record, "reason", label)
    if reason not in allowed:
        raise ControlStoreError(
            f"{label} declares unknown reason {reason!r}; expected one of "
            f"{', '.join(sorted(allowed))}"
        )
    return reason


def validate_controller_dispatch(
    record: Any,
    label: str = "controller dispatch",
) -> Dict[str, Any]:
    """Validate one versioned controller dispatch record, failing closed.

    A dispatch is the single state-changing action a tick is allowed to take, so
    it must be fully self-describing: which mission it creates, on which worker,
    for which queue item, under which trace, and on the strength of which
    reconciled evidence.  The mission brief is deliberately absent, exactly as a
    question body is: the carrying envelope digests it and `evidence_refs` names
    the queue item that authorized the work.
    """

    data = _require_object(record, label)
    _require_controller_schema_version(data, label)
    _require_record_type(data, CONTROLLER_DISPATCH_RECORD_TYPE, label)
    _require_closed_fields(data, CONTROLLER_DISPATCH_FIELDS, label)

    _require_uuid(data, "command_id", label)
    _require_uuid(data, "mission_id", label)
    _require_mission_correlation(data, label)
    _require_controller_reason(data, label, CONTROLLER_DISPATCH_REASONS)
    _require_digest(data, "state_key", label)
    _require_evidence_refs(data, label, required=True)
    _require_timestamp(data, "decided_at", label)
    return data


def validate_controller_observation(
    record: Any,
    label: str = "controller observation",
) -> Dict[str, Any]:
    """Validate one versioned controller observation record, failing closed.

    An observation is what a tick that changed no mission state durably knows:
    the closed outcome and reason it reconciled to, the identity that reason is
    about, and the `state_key` digest of the exact evidence it saw.  Recording it
    is what lets the next tick recognise the same situation and terminate
    without investigating it a second time.

    An observation carries no environment values and no prose.  Roots, panes,
    and prompts are contributed to `state_key` as a digest only, so an immutable
    event that is never rewritten cannot become the place a secret lives.
    """

    data = _require_object(record, label)
    _require_controller_schema_version(data, label)
    _require_record_type(data, CONTROLLER_OBSERVATION_RECORD_TYPE, label)
    _require_closed_fields(data, CONTROLLER_OBSERVATION_FIELDS, label)

    outcome = _require_string(data, "outcome", label)
    if outcome not in CONTROLLER_OBSERVATION_OUTCOMES:
        raise ControlStoreError(
            f"{label} declares unknown outcome {outcome!r}; expected one of "
            f"{', '.join(CONTROLLER_OBSERVATION_OUTCOMES)}"
        )
    reason = _require_controller_reason(data, label, CONTROLLER_OBSERVATION_REASONS)
    if (reason in CONTROLLER_BLOCKING_REASONS) != (outcome == CONTROLLER_BLOCKED):
        raise ControlStoreError(
            f"{label} declares outcome {outcome!r} for reason {reason!r}; a blocking "
            "reason is always recorded as blocked and never the other way around"
        )
    _require_digest(data, "state_key", label)
    _require_optional_identifier(data, "queue_item_id", label)
    _require_optional_identifier(data, "worker_id", label)
    _require_optional_uuid(data, "mission_id", label)
    _require_optional_uuid(data, "command_id", label)
    _require_evidence_refs(data, label, required=False)
    _require_timestamp(data, "observed_at", label)
    return data


MISSION_CONTROL_VALIDATORS = {
    MISSION_DIALOG_PAYLOAD_FIELD: validate_mission_dialog,
    MISSION_CANCELLATION_PAYLOAD_FIELD: validate_mission_cancellation,
    MISSION_REPLACEMENT_PAYLOAD_FIELD: validate_mission_replacement,
    MISSION_RECOVERY_PAYLOAD_FIELD: validate_mission_recovery,
    CONTROLLER_DISPATCH_PAYLOAD_FIELD: validate_controller_dispatch,
}


def _mission_control_command_type(field: str, record: Mapping[str, Any]) -> str:
    """Return the one managed command type a mission-control record requires."""

    if field == MISSION_DIALOG_PAYLOAD_FIELD:
        return MISSION_DIALOG_COMMAND_TYPES[record["kind"]]
    if field == MISSION_CANCELLATION_PAYLOAD_FIELD:
        return COMMAND_TYPE_MISSION_CANCEL
    if field == MISSION_RECOVERY_PAYLOAD_FIELD:
        return MISSION_RECOVERY_COMMAND_TYPES[record["action"]]
    if field == CONTROLLER_DISPATCH_PAYLOAD_FIELD:
        return COMMAND_TYPE_MISSION_DISPATCH
    return COMMAND_TYPE_MISSION_REPLACE


def event_controller_observation(
    record: Mapping[str, Any],
    label: str,
) -> Optional[Dict[str, Any]]:
    """Return the controller observation one event declares, failing closed.

    An observation and its event type are two halves of one contract, exactly as
    a lifecycle record and its event type are: the type is always
    `controller-observation-<outcome>` for the outcome the record declares, and
    an observation may never share an event with a command envelope, so no
    single committed file can be read as both a decision and a delivery.
    """

    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    declared = record.get("event_type")
    declared = declared if isinstance(declared, str) else ""
    carried = CONTROLLER_OBSERVATION_PAYLOAD_FIELD in payload
    typed = declared.startswith(CONTROLLER_OBSERVATION_EVENT_PREFIX)
    if not carried:
        if typed:
            raise ControlStoreError(
                f"{label} declares event_type {declared!r} without its "
                f"payload.{CONTROLLER_OBSERVATION_PAYLOAD_FIELD} record"
            )
        return None
    if not typed:
        raise ControlStoreError(
            f"{label} carries a payload.{CONTROLLER_OBSERVATION_PAYLOAD_FIELD} record "
            f"under event_type {declared!r}; expected "
            f"{CONTROLLER_OBSERVATION_EVENT_PREFIX}<outcome>"
        )
    if COMMAND_ENVELOPE_PAYLOAD_FIELD in payload:
        raise ControlStoreError(
            f"{label} carries a payload.{CONTROLLER_OBSERVATION_PAYLOAD_FIELD} record "
            "beside a command envelope; a controller observation takes no action and "
            "delivers no command"
        )
    validated = validate_controller_observation(
        payload[CONTROLLER_OBSERVATION_PAYLOAD_FIELD],
        f"{label} payload.{CONTROLLER_OBSERVATION_PAYLOAD_FIELD}",
    )
    expected = f"{CONTROLLER_OBSERVATION_EVENT_PREFIX}{validated['outcome']}"
    if declared != expected:
        raise ControlStoreError(
            f"{label} declares event_type {declared!r} for outcome "
            f"{validated['outcome']!r}; expected {expected!r}"
        )
    return validated


def event_mission_control(
    record: Mapping[str, Any],
    label: str,
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return the mission-control record one event declares, failing closed.

    A managed command and its correlation record are two halves of one contract
    and neither half can exist without the other:

    * a mission-control record may only ride in the `command-registered` event
      of the command envelope that carries it, and must name that exact command
      ID and the exact managed command type its kind requires;
    * a command envelope declaring a managed command type must carry the
      matching record, so the generic delivery surface cannot mint a command
      that looks managed while being correlated to nothing at all.

    One event carries at most one such record, so a single committed file can
    never be read as two different managed operations.
    """

    payload = record.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    present = [field for field in MISSION_CONTROL_PAYLOAD_FIELDS if field in payload]
    if len(present) > 1:
        raise ControlStoreError(
            f"{label} declares {len(present)} mission-control records "
            f"({', '.join(present)}); one event carries at most one"
        )
    envelope = event_command_envelope(record, label)
    if not present:
        if envelope is not None and envelope["command_type"] in MANAGED_COMMAND_TYPES:
            raise ControlStoreError(
                f"{label} declares managed command_type {envelope['command_type']!r} "
                "without its correlated mission-control record"
            )
        return None, None

    field = present[0]
    if envelope is None:
        raise ControlStoreError(
            f"{label} carries a payload.{field} record without a "
            f"payload.{COMMAND_ENVELOPE_PAYLOAD_FIELD} command envelope"
        )
    validated = MISSION_CONTROL_VALIDATORS[field](payload[field], f"{label} payload.{field}")
    if validated["command_id"] != envelope["command_id"]:
        raise ControlStoreError(
            f"{label} payload.{field} names command {validated['command_id']} but its "
            f"envelope registers command {envelope['command_id']}"
        )
    expected = _mission_control_command_type(field, validated)
    if envelope["command_type"] != expected:
        raise ControlStoreError(
            f"{label} payload.{field} requires command_type {expected!r} but its envelope "
            f"declares {envelope['command_type']!r}"
        )
    return field, validated


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
    # A lifecycle, envelope, or acknowledgement record is validated as part of
    # the event, so a malformed, unversioned, or future-versioned record can
    # never be committed and can never be read back as if it were understood.
    event_worker_lifecycle(data, label)
    event_command_envelope(data, label)
    event_command_acknowledgement(data, label)
    event_mission_control(data, label)
    event_controller_observation(data, label)
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
    # `json.JSONDecodeError` subclasses `ValueError`, so naming `ValueError`
    # covers every malformed document plus the numeric literals CPython refuses
    # to convert at all (an integer longer than its 4300-digit `int` conversion
    # limit).  A pathologically nested document exhausts the interpreter stack
    # instead.  Both are corruption of a stored record, so both are stated as
    # the same fail-closed diagnostic rather than a traceback naming internal
    # filesystem paths.
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ControlStoreError(f"malformed {label}: {exc}") from None
    except RecursionError:
        raise ControlStoreError(f"malformed {label}: nested too deeply to parse") from None


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
                except ValueError as exc:
                    raise ControlStoreError(f"malformed {EVENTS_NAME}:{number}: {exc}") from None
                except RecursionError:
                    raise ControlStoreError(
                        f"malformed {EVENTS_NAME}:{number}: nested too deeply to parse"
                    ) from None
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


def validate_control_authority(
    root: Path,
    journal_repair: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate every *authoritative* record without reading derived state.

    `control.json`, the committed events, and the stored command and escalation
    records are the store's authority: nothing regenerates them, so a fault in
    any of them stops mutation and is repaired only by an explicit human action.
    `ledger.json` and `events.jsonl` are deliberately absent here because they
    are pure functions of the events above and are therefore rebuilt rather than
    refused; the one caller that must tell those two cases apart is the
    controller tick, which repairs the derived half itself.
    """

    root = _require_absolute_root(str(root), "configured")
    _require_directory(root, "COCKPIT_CONTROL_ROOT")
    metadata = validate_root_metadata(
        _load_json(root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME), root
    )
    try:
        read_committed_events(root, metadata["control_id"])
    except ControlStoreError as exc:
        # A caller that owns a repair for authoritative corruption says so here.
        # Nothing rebuilds, reorders, quarantines, or guesses past a malformed
        # committed event: the refusal names the one file and the one repair.
        if journal_repair is None:
            raise
        raise ControlStoreError(
            f"the committed control journal cannot be replayed: {exc}; no mission state "
            f"was derived and nothing was guessed [repair: {journal_repair}]"
        ) from None
    _validate_record_directory(root / COMMANDS_DIR_NAME, COMMANDS_DIR_NAME, metadata["control_id"])
    _validate_record_directory(root / ESCALATIONS_DIR_NAME, ESCALATIONS_DIR_NAME, metadata["control_id"])
    _require_directory(root / LOCKS_DIR_NAME, LOCKS_DIR_NAME)
    return metadata


def validate_control_store(root: Path) -> Dict[str, Any]:
    """Validate every authoritative root-level record without changing state.

    The derived projection and compatibility view are validated here too, so
    every surface that is not able to rebuild them keeps failing closed on
    derived corruption exactly as it did before.
    """

    metadata = validate_control_authority(root)
    root = _require_absolute_root(str(root), "configured")
    ledger = validate_ledger(
        _load_json(root / LEDGER_NAME, LEDGER_NAME), metadata["control_id"], root
    )
    for field in ("control_root", "queue_root", "planning_root", "implementation_roots"):
        if ledger["canonical_roots"][field] != metadata["canonical_roots"][field]:
            raise ControlStoreError(
                f"{LEDGER_NAME} canonical_roots.{field} does not match {CONTROL_METADATA_NAME}"
            )
    _validate_events(root / EVENTS_NAME, metadata["control_id"])
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
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ControlStoreError(f"malformed {label}/{LOCK_OWNER_NAME}: {exc}") from None
    except RecursionError:
        raise ControlStoreError(
            f"malformed {label}/{LOCK_OWNER_NAME}: nested too deeply to parse"
        ) from None
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


control_locks.ControlStoreError = ControlStoreError
control_locks._lock_transition_fault = (
    lambda boundary, transition: _lock_transition_fault(boundary, transition)
)
_configured_lock_timeout = control_locks._configured_lock_timeout
_validate_lock_owner = control_locks._validate_lock_owner
_new_lock_owner = control_locks._new_lock_owner
_prove_lock_owner_death = control_locks._prove_lock_owner_death
_open_exact_directory = control_locks._open_exact_directory
_read_lock_owner_from_directory = control_locks._read_lock_owner_from_directory
_validate_owned_directory = control_locks._validate_owned_directory
_cleanup_owned_directory = control_locks._cleanup_owned_directory
ControlTransitionGuard = control_locks.ControlTransitionGuard
_quarantine_owned_lock_while_guarded = control_locks._quarantine_owned_lock_while_guarded
PortableControlLock = control_locks.PortableControlLock
LockRepairResult = control_locks.LockRepairResult
ControlLockRepair = control_locks.ControlLockRepair
repair_stale_control_lock = control_locks.repair_stale_control_lock


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


def apply_worker_lifecycle(
    slots: Dict[str, Dict[str, Any]],
    lifecycle: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed lifecycle event into the materialized mission slots.

    Every committed event is durable audit evidence regardless of this outcome.
    The fold only decides whether it also *moves* the materialized state, and it
    never raises for an out-of-order or superseded record: refusing to project a
    committed event would wedge the whole store.  A record is applied only when
    it correlates with the same worker and queue item, the mission is not yet
    terminal, its sequence is strictly newer than the materialized one, and the
    transition is in the architecture section 9 table.
    """

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
        # A terminal state is absolute: no sequence number, however new, reopens
        # a mission that already completed, failed, was cancelled, or replaced.
        if current_state in WORKER_LIFECYCLE_TERMINAL_STATES:
            return False, LIFECYCLE_RETAINED_TERMINAL
        # Equal sequences are duplicates and lower ones are late deliveries;
        # neither may regress or re-apply the newer materialized state.
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


def fold_worker_missions(
    events: Sequence["CommittedEvent"] = (),
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    """Replay committed events into mission slots and per-event fold outcomes."""

    state = fold_mission_state(events)
    return state.missions, state.lifecycle_outcomes


def _mission_event_stamp(event: "CommittedEvent") -> Dict[str, Any]:
    """Return the committed provenance every materialized mission entry carries."""

    return {
        "revision": event.revision,
        "event_id": event.event_id,
        "recorded_at": event.record["timestamp"],
    }


def _mission_slot_conflict(
    mission_id: str,
    reason: str,
    conflict_source: str,
    command_id: Optional[str],
    event: "CommittedEvent",
) -> Dict[str, Any]:
    """Build one durable mission-slot conflict from the event that recorded it."""

    entry = {"mission_id": mission_id, "reason": reason, "source": conflict_source,
             "command_id": command_id}
    entry.update(_mission_event_stamp(event))
    return entry


def _mission_slot_entry(worker_slots: Dict[str, Dict[str, Any]], worker_id: str,
                        event: "CommittedEvent") -> Dict[str, Any]:
    """Return the worker's slot entry, creating an unclaimed one to hold evidence.

    A conflict must always have somewhere durable to live, including for a
    worker that holds no mission at all, so an entry that exists only to carry
    recorded conflicts is explicitly `unclaimed` rather than a partial claim.
    """

    entry = worker_slots.get(worker_id)
    if entry is None:
        entry = {
            "mission_id": None,
            "queue_item_id": None,
            "state": MISSION_SLOT_UNCLAIMED,
            "command_id": None,
            "replaces": None,
            "conflicts": [],
        }
        entry.update(_mission_event_stamp(event))
        worker_slots[worker_id] = entry
    return entry


def _mission_slot_claim_fault(
    worker_slots: Mapping[str, Mapping[str, Any]],
    lifecycle: Mapping[str, Any],
) -> Optional[str]:
    """Say why one lifecycle event may not materialize the mission it names.

    `apply_worker_lifecycle` can only correlate an event against a mission it
    has already materialized, so a replacement's *reserved* mission is invisible
    to it: that mission ID exists only in the worker's slot until the worker
    accepts it.  Without this check a second worker could materialize the
    reserved mission and hold it at the same time as the reservation, leaving
    two claimed slots naming one mission, and the worker that owns the
    reservation could silently re-point it at a different queue item than the
    replacement declared.  Both are mission bleed, so both are refused here and
    recorded as durable conflicts instead of being folded into state.
    """

    mission_id = lifecycle["mission_id"]
    worker_id = lifecycle["worker_id"]
    for holder in sorted(worker_slots):
        entry = worker_slots[holder]
        if entry["state"] not in MISSION_SLOT_CLAIMED_STATES:
            continue
        if entry["mission_id"] != mission_id:
            continue
        if holder != worker_id:
            # One mission ID is held by at most one slot, whichever half of the
            # protocol claimed it first.
            return MISSION_SLOT_CONFLICT_REUSED
        if (
            entry["state"] == MISSION_SLOT_RESERVED
            and entry["queue_item_id"] != lifecycle["queue_item_id"]
        ):
            # A replacement reserves the slot for one mission on one queue item;
            # accepting it against another queue item is uncorrelated work.
            return MISSION_SLOT_CONFLICT_UNMATCHED
    return None


def apply_lifecycle_mission_slot(
    worker_slots: Dict[str, Dict[str, Any]],
    lifecycle: Mapping[str, Any],
    event: "CommittedEvent",
) -> None:
    """Fold one applied lifecycle event into the worker's single mission slot.

    ADR-013 gives each worker exactly one mission slot.  The earliest claim
    keeps it, a terminal state releases it, and any later claim for a different
    mission is retained as a durable `second-active-slot` conflict instead of
    silently giving one worker two active missions.
    """

    worker_id = lifecycle["worker_id"]
    mission_id = lifecycle["mission_id"]
    entry = worker_slots.get(worker_id)
    if lifecycle["state"] in WORKER_LIFECYCLE_TERMINAL_STATES:
        if (
            entry is not None
            and entry["state"] in MISSION_SLOT_CLAIMED_STATES
            and entry["mission_id"] == mission_id
        ):
            entry["state"] = MISSION_SLOT_RELEASED
            entry.update(_mission_event_stamp(event))
        return

    if entry is not None and entry["state"] in MISSION_SLOT_CLAIMED_STATES:
        if entry["mission_id"] != mission_id:
            entry["conflicts"].append(
                _mission_slot_conflict(
                    mission_id,
                    MISSION_SLOT_CONFLICT_SECOND_ACTIVE,
                    MISSION_SLOT_CONFLICT_LIFECYCLE,
                    None,
                    event,
                )
            )
            return
        # The worker accepted the mission its slot already holds, which is how a
        # reserved replacement becomes an ordinary active mission.
        entry["state"] = MISSION_SLOT_ACTIVE
        entry["queue_item_id"] = lifecycle["queue_item_id"]
        entry.update(_mission_event_stamp(event))
        return

    entry = _mission_slot_entry(worker_slots, worker_id, event)
    entry["mission_id"] = mission_id
    entry["queue_item_id"] = lifecycle["queue_item_id"]
    entry["state"] = MISSION_SLOT_ACTIVE
    entry["command_id"] = None
    entry["replaces"] = None
    entry.update(_mission_event_stamp(event))


def apply_mission_dialog(
    dialogs: Dict[str, Dict[str, Any]],
    dialog: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed dialog record into the materialized dialog state.

    A prompt claims its command ID once and becomes `pending`.  A response
    resolves exactly one pending prompt of the matching kind that is correlated
    to the same mission, worker, and queue item; anything else is retained as
    durable audit evidence and answers nothing, so a reply can never resolve an
    unknown, already-answered, or unrelated question.
    """

    command_id = dialog["command_id"]
    if command_id in dialogs:
        return False, MISSION_FOLD_RETAINED_DUPLICATE

    entry = {
        "dialog": dict(dialog),
        "state": MISSION_DIALOG_PENDING,
        "answered_by_command_id": None,
        "answered_at": None,
    }
    entry.update(_mission_event_stamp(event))
    if dialog["kind"] in MISSION_DIALOG_PROMPT_KINDS:
        dialogs[command_id] = entry
        return True, MISSION_FOLD_RAISED

    prompt = dialogs.get(dialog["answers_command_id"])
    if prompt is None:
        return False, MISSION_FOLD_RETAINED_UNKNOWN_PROMPT
    if prompt["dialog"]["kind"] != MISSION_DIALOG_ANSWERS[dialog["kind"]]:
        return False, MISSION_FOLD_RETAINED_KIND
    if prompt["state"] != MISSION_DIALOG_PENDING:
        return False, MISSION_FOLD_RETAINED_ANSWERED
    for field in ("mission_id", "worker_id", "queue_item_id"):
        if prompt["dialog"][field] != dialog[field]:
            return False, MISSION_FOLD_RETAINED_UNMATCHED

    prompt["state"] = MISSION_DIALOG_ANSWERED
    prompt["answered_by_command_id"] = command_id
    prompt["answered_at"] = event.record["timestamp"]
    entry["state"] = MISSION_DIALOG_DELIVERED
    dialogs[command_id] = entry
    return True, MISSION_FOLD_ANSWERED


def apply_mission_cancellation(
    cancellations: Dict[str, Dict[str, Any]],
    cancellation: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed cooperative cancellation request into the projection.

    The request is the durable half; whether the worker acknowledged it and
    whether its declared deadline has passed are observations derived later from
    the command acknowledgements and an explicit as-of moment.
    """

    command_id = cancellation["command_id"]
    if command_id in cancellations:
        return False, MISSION_FOLD_RETAINED_DUPLICATE
    entry = {"cancellation": dict(cancellation)}
    entry.update(_mission_event_stamp(event))
    cancellations[command_id] = entry
    return True, MISSION_FOLD_REQUESTED


def apply_mission_recovery(
    recoveries: Dict[str, Dict[str, Any]],
    recovery: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed bounded recovery request into the projection.

    The request is the durable half and is all this fold records: whether the
    worker answered it is derived later from the correlated lifecycle events and
    command acknowledgements, at one explicit moment.  A recovery request never
    moves a mission state, so a silent worker can never be folded into `failed`.
    """

    command_id = recovery["command_id"]
    if command_id in recoveries:
        return False, MISSION_FOLD_RETAINED_DUPLICATE
    entry = {"recovery": dict(recovery)}
    entry.update(_mission_event_stamp(event))
    recoveries[command_id] = entry
    return True, MISSION_FOLD_RECOVERY_REQUESTED


def apply_mission_replacement(
    missions: Dict[str, Dict[str, Any]],
    worker_slots: Dict[str, Dict[str, Any]],
    replacement: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed replacement into the mission and slot projections.

    A replacement terminates the prior mission with the existing `replaced`
    lifecycle state, links it to the mission it creates through
    `superseded_by_mission_id`, and reserves the worker's one slot for that new
    mission ID.  It applies only when the prior mission is the worker's own
    claimed, correlated, still-active mission and the new mission ID has never
    been used; every other outcome is refused and recorded as a durable
    conflict, which is what stops a second active slot from ever existing.
    """

    worker_id = replacement["worker_id"]
    prior_id = replacement["replaced_mission_id"]
    replacement_id = replacement["replacement_mission_id"]
    command_id = replacement["command_id"]

    def refuse(mission_id: str, reason: str) -> Tuple[bool, str]:
        entry = _mission_slot_entry(worker_slots, worker_id, event)
        entry["conflicts"].append(
            _mission_slot_conflict(
                mission_id, reason, MISSION_SLOT_CONFLICT_REPLACEMENT, command_id, event
            )
        )
        return False, MISSION_FOLD_CONFLICT_RECORDED

    prior = missions.get(prior_id)
    if (
        prior is None
        or prior["lifecycle"]["state"] not in WORKER_LIFECYCLE_ACTIVE_STATES
        or prior["lifecycle"]["worker_id"] != worker_id
        or prior["lifecycle"]["queue_item_id"] != replacement["queue_item_id"]
    ):
        return refuse(prior_id, MISSION_SLOT_CONFLICT_UNMATCHED)

    slot = worker_slots.get(worker_id)
    if slot is None or slot["state"] not in MISSION_SLOT_CLAIMED_STATES:
        return refuse(prior_id, MISSION_SLOT_CONFLICT_UNMATCHED)
    if slot["mission_id"] != prior_id:
        # The worker already owns another valid active mission, so replacing
        # this one would leave it holding two active slots at once.
        return refuse(replacement_id, MISSION_SLOT_CONFLICT_SECOND_ACTIVE)
    if replacement_id in missions or any(
        entry["state"] in MISSION_SLOT_CLAIMED_STATES
        and entry["mission_id"] == replacement_id
        for entry in worker_slots.values()
    ):
        return refuse(replacement_id, MISSION_SLOT_CONFLICT_REUSED)

    lifecycle = prior["lifecycle"]
    replaced = build_worker_lifecycle(
        state=LIFECYCLE_REPLACED,
        worker_id=worker_id,
        mission_id=prior_id,
        queue_item_id=lifecycle["queue_item_id"],
        trace_id=lifecycle["trace_id"],
        sequence=lifecycle["sequence"] + 1,
        parent_trace_id=lifecycle["parent_trace_id"],
        reason=replacement["reason"],
        evidence_refs=tuple(replacement["evidence_refs"]),
        superseded_by_mission_id=replacement_id,
    )
    terminated = {"lifecycle": replaced}
    terminated.update(_mission_event_stamp(event))
    missions[prior_id] = terminated

    slot["mission_id"] = replacement_id
    slot["queue_item_id"] = replacement["queue_item_id"]
    slot["state"] = MISSION_SLOT_RESERVED
    slot["command_id"] = command_id
    slot["replaces"] = prior_id
    slot.update(_mission_event_stamp(event))
    return True, MISSION_FOLD_REPLACED


def _record_controller_decision(
    controller: Dict[str, Any],
    outcome: str,
    reason: str,
    state_key: str,
    queue_item_id: Optional[str],
    worker_id: Optional[str],
    mission_id: Optional[str],
    command_id: Optional[str],
    event: "CommittedEvent",
) -> None:
    """Replace the one materialized controller decision the ledger projects.

    There is exactly one such decision at a time on purpose: the controller
    holds "what I last decided and on what evidence", never a growing history of
    everything it ever thought.  The immutable committed events are the history.
    """

    controller.clear()
    controller.update(
        {
            "outcome": outcome,
            "reason": reason,
            "state_key": state_key,
            "queue_item_id": queue_item_id,
            "worker_id": worker_id,
            "mission_id": mission_id,
            "command_id": command_id,
        }
    )
    controller.update(_mission_event_stamp(event))


def apply_controller_dispatch(
    missions: Dict[str, Dict[str, Any]],
    worker_slots: Dict[str, Dict[str, Any]],
    controller: Dict[str, Any],
    dispatch: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed controller dispatch into the slot and controller state.

    This is where "one worker, one mission" stops being a convention.  A dispatch
    reserves the worker's single slot for exactly one new mission ID, in the same
    deterministic fold every other claim goes through, so a second dispatch to a
    busy worker and a dispatch that reuses a mission ID are both refused and
    durably recorded as conflicts no matter which surface committed them --
    including the raw `publish-event` primitive.  The reservation becomes an
    ordinary active mission the moment the worker emits `accepted`.
    """

    worker_id = dispatch["worker_id"]
    mission_id = dispatch["mission_id"]
    command_id = dispatch["command_id"]

    def refuse(reason: str) -> Tuple[bool, str]:
        entry = _mission_slot_entry(worker_slots, worker_id, event)
        entry["conflicts"].append(
            _mission_slot_conflict(
                mission_id, reason, MISSION_SLOT_CONFLICT_DISPATCH, command_id, event
            )
        )
        return False, MISSION_FOLD_CONFLICT_RECORDED

    slot = worker_slots.get(worker_id)
    if slot is not None and slot["state"] in MISSION_SLOT_CLAIMED_STATES:
        if slot["mission_id"] == mission_id and slot["command_id"] == command_id:
            # Delivery is uncertain, so the same dispatch arriving twice is a
            # redelivery of one command rather than a second mission.  It is
            # retained as durable evidence and re-applies nothing, exactly as an
            # idempotent command redelivery must.
            return False, MISSION_FOLD_RETAINED_DUPLICATE
        return refuse(MISSION_SLOT_CONFLICT_SECOND_ACTIVE)
    if mission_id in missions or any(
        entry["state"] in MISSION_SLOT_CLAIMED_STATES and entry["mission_id"] == mission_id
        for entry in worker_slots.values()
    ):
        return refuse(MISSION_SLOT_CONFLICT_REUSED)

    entry = _mission_slot_entry(worker_slots, worker_id, event)
    entry["mission_id"] = mission_id
    entry["queue_item_id"] = dispatch["queue_item_id"]
    entry["state"] = MISSION_SLOT_RESERVED
    entry["command_id"] = command_id
    entry["replaces"] = None
    entry.update(_mission_event_stamp(event))
    _record_controller_decision(
        controller,
        CONTROLLER_DISPATCHED,
        dispatch["reason"],
        dispatch["state_key"],
        queue_item_id=dispatch["queue_item_id"],
        worker_id=worker_id,
        mission_id=mission_id,
        command_id=command_id,
        event=event,
    )
    return True, CONTROLLER_FOLD_DISPATCHED


def apply_controller_observation(
    controller: Dict[str, Any],
    observation: Mapping[str, Any],
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed controller observation into the controller state.

    An observation that repeats the state key already recorded is retained as
    durable audit evidence and moves nothing.  That is the whole of "a tick does
    not investigate the same situation twice": the *fold*, not the tick, decides
    that the situation is unchanged, so no surface can make a stalled cockpit
    grow one event per wake forever.
    """

    if controller and controller["state_key"] == observation["state_key"]:
        return False, CONTROLLER_FOLD_RETAINED_UNCHANGED
    _record_controller_decision(
        controller,
        observation["outcome"],
        observation["reason"],
        observation["state_key"],
        queue_item_id=observation["queue_item_id"],
        worker_id=observation["worker_id"],
        mission_id=observation["mission_id"],
        command_id=observation["command_id"],
        event=event,
    )
    return True, CONTROLLER_FOLD_OBSERVED


@dataclass(frozen=True)
class MissionState:
    """One deterministic replay of every mission-shaped committed event.

    Worker missions, mission dialogs, cooperative cancellations, the single slot
    each worker holds, and the one decision the controller last took are folded
    in one pass over the same committed sequence, so they can never disagree
    about which mission is active.
    """

    missions: Dict[str, Dict[str, Any]]
    dialogs: Dict[str, Dict[str, Any]]
    cancellations: Dict[str, Dict[str, Any]]
    recoveries: Dict[str, Dict[str, Any]]
    worker_slots: Dict[str, Dict[str, Any]]
    controller: Dict[str, Any]
    lifecycle_outcomes: Dict[str, Tuple[bool, str]]
    mission_outcomes: Dict[str, Tuple[bool, str]]
    # For every worker, the committed revision at which its slot's *current*
    # mission first claimed it, and which mission that is.  This is derived from
    # commit order alone and is deliberately not projected into `ledger.json`:
    # it exists so a durable conflict can be told from a historical one without
    # anything having to remember, and without a second schema to keep honest.
    slot_claims: Dict[str, Tuple[int, str]]
    # For every materialized mission, the committed revision that keys its
    # bounded recovery episode and whether that episode is still open.  It is
    # derived from commit order and the moments committed controller records
    # declare, and, like `slot_claims`, is deliberately not projected into
    # `ledger.json`: it exists so "is the ladder still walking the same
    # staleness?" is answered from committed authority rather than from a
    # projection that would have to be migrated and kept honest.
    recovery_episodes: Dict[str, Tuple[int, bool]]

    @property
    def claimed_slots(self) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
        """Return every worker slot that currently holds a mission, by worker."""

        return tuple(
            (worker_id, self.worker_slots[worker_id])
            for worker_id in sorted(self.worker_slots)
            if self.worker_slots[worker_id]["state"] in MISSION_SLOT_CLAIMED_STATES
        )

    def duplicate_claim_is_live(self, conflict: Mapping[str, Any]) -> bool:
        """Say whether the claim one conflict refused is still a live mission.

        A duplicate claim is live exactly while the mission it named is
        materialized and has not reached a terminal state.  Nothing else counts:
        a claim the fold refused before it ever materialized a mission never put
        a second mission on the worker, and a mission an operator or its own
        worker has since ended is evidence about a finished episode.  This is
        what makes the refusal repairable -- ending the duplicate is an action
        an operator can actually take, and taking it clears the block -- rather
        than a dead end that names a repair which can never satisfy it.
        """

        duplicate = self.missions.get(conflict["mission_id"])
        if duplicate is None:
            return False
        return duplicate["lifecycle"]["state"] not in WORKER_LIFECYCLE_TERMINAL_STATES

    @property
    def contested_slots(self) -> Tuple[Tuple[str, Dict[str, Any], Dict[str, Any]], ...]:
        """Return every worker two live missions claimed, with the refused claim.

        The mission the worker keeps is the one the deterministic fold admitted
        first *in committed revision order*, never the one with the newest
        timestamp: ADR-014 rejects "latest timestamp wins" because clocks and
        late observations do not establish ownership.  A conflict counts only
        while the claim it was refused against is still the one the slot holds
        *and* the duplicate claim it refused is still a live mission, so a worker
        whose contested mission ended, and which was later claimed again, is not
        blocked forever by evidence about a finished episode -- and neither is a
        worker whose duplicate claim an operator has ended exactly as the
        refusal told them to.

        A worker whose *retained* mission ends releases its slot, and the next
        tick may dispatch to it while the refused duplicate is still live.  That
        is deliberate and predates this story: the conflict is evidence about
        two claims on one slot, and once the slot is free there is no longer a
        second claim on it -- the duplicate is then an uncorrelated mission its
        own worker still owns, which the lifecycle surface reports and nothing
        here may end on its behalf.
        """

        found: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        for worker_id in sorted(self.worker_slots):
            slot = self.worker_slots[worker_id]
            if slot["state"] not in MISSION_SLOT_CLAIMED_STATES:
                continue
            claim = self.slot_claims.get(worker_id)
            if claim is None or claim[1] != slot["mission_id"]:
                continue
            for conflict in slot["conflicts"]:
                if conflict["reason"] not in MISSION_SLOT_DUPLICATE_CLAIM_REASONS:
                    continue
                if conflict["revision"] < claim[0]:
                    continue
                if not self.duplicate_claim_is_live(conflict):
                    continue
                found.append((worker_id, slot, conflict))
                break
        return tuple(found)


def _episode_moment(value: Any) -> Optional[datetime]:
    """Parse one declared moment, or nothing at all if it cannot be read.

    A fold may never raise on a committed record: refusing to project committed
    authority would wedge the whole store.  An unreadable moment therefore
    proves nothing, and proving nothing keeps an open recovery episode open,
    which is the bounded direction to fail in.
    """

    try:
        return _parsed_timestamp(value, "moment", "recovery episode")
    except ControlStoreError:
        return None


def _record_episode_evidence(
    episodes: Dict[str, Tuple[int, bool]],
    mission_id: str,
    event: "CommittedEvent",
) -> None:
    """Key one mission's next recovery episode on its newest lifecycle evidence.

    Evidence that arrives while no episode is open keys the episode that any
    later staleness will be recovered under.  Evidence that arrives *while an
    episode is open* deliberately does not: a worker whose declared freshness
    window is shorter than the interval it reports on is stale at every moment
    the controller ever looks, and if such evidence re-keyed the episode it
    would move every rung onto identifiers no rung has used and restart the walk
    at `nudge` forever.  Only being seen fresh by the controller ends an
    episode, which is what makes the ladder finite in wall-clock time and not
    merely finite per key.
    """

    episode = episodes.get(mission_id)
    if episode is None or not episode[1]:
        episodes[mission_id] = (event.revision, False)


def _open_recovery_episode(
    episodes: Dict[str, Tuple[int, bool]],
    mission_id: str,
    revision: int,
) -> None:
    """Record that the controller has begun recovering one mission."""

    episode = episodes.get(mission_id)
    episodes[mission_id] = (episode[0] if episode is not None else revision, True)


def _close_observed_episodes(
    episodes: Dict[str, Tuple[int, bool]],
    missions: Mapping[str, Mapping[str, Any]],
    moment: Any,
    mission_id: Any,
    reason: Any,
) -> None:
    """End every open episode whose mission was fresh when the controller looked.

    A committed controller record is the durable evidence that the controller
    read the whole cockpit at the moment it declares.  A mission whose declared
    freshness still covered that moment was not stale when it was looked at, so
    its episode is over and the next staleness is a new one -- which is exactly
    the reset the ladder documents.  A record the controller wrote *because* it
    is walking that mission's own episode is excluded: an `awaiting-recovery-
    response` observation about a mission whose queue item left the active set
    would otherwise close and reopen its own episode on every wake.

    Only a *recorded* look ends an episode, and the observation that reports a
    mission in progress carries the freshness its worker declared exactly so
    that rule costs nothing: a mission that went stale and came back has
    necessarily declared a window it had not declared before, so restored
    freshness is always a new situation and is always recorded.  A look whose
    observation repeats the controller's last recorded decision saw the same
    declared freshness that look already closed on, and re-recording it would be
    the repeated investigation a stalled cockpit must not grow one event per
    wake for.  Evidence that does not restore freshness never reaches an
    in-progress observation at all, so it can never end an episode either: the
    ladder stays finite in wall-clock time for a worker whose every report is
    already expired, and restarts at `nudge` for one that answers.
    """

    if not episodes:
        return
    looked_at = _episode_moment(moment)
    if looked_at is None:
        return
    for observed_mission in sorted(episodes):
        revision, opened = episodes[observed_mission]
        if not opened:
            continue
        if observed_mission == mission_id and reason in CONTROLLER_EPISODE_REASONS:
            continue
        slot = missions.get(observed_mission)
        if slot is None:
            continue
        lifecycle = slot["lifecycle"]
        if lifecycle["state"] in WORKER_LIFECYCLE_TERMINAL_STATES:
            # Deliberately redundant with the `expiry is None` arm below: a
            # terminal state declares no freshness at all, so an ended mission
            # is refused twice rather than once.  It is kept because it states
            # the invariant -- an episode is about a mission that is still
            # running -- and neither guard alone can be removed by anyone
            # reading only the other.
            continue
        expiry = _episode_moment(lifecycle["fresh_until"])
        if expiry is None or looked_at > expiry:
            continue
        episodes[observed_mission] = (slot["revision"], False)


def _fold_one_event(
    event: "CommittedEvent",
    label: str,
    missions: Dict[str, Dict[str, Any]],
    dialogs: Dict[str, Dict[str, Any]],
    cancellations: Dict[str, Dict[str, Any]],
    recoveries: Dict[str, Dict[str, Any]],
    worker_slots: Dict[str, Dict[str, Any]],
    controller: Dict[str, Any],
    lifecycle_outcomes: Dict[str, Tuple[bool, str]],
    mission_outcomes: Dict[str, Tuple[bool, str]],
    episodes: Dict[str, Tuple[int, bool]],
) -> None:
    """Fold exactly one committed event into every materialized projection."""

    observation = event_controller_observation(event.record, label)
    if observation is not None:
        mission_outcomes[event.event_id] = apply_controller_observation(
            controller, observation, event
        )
        _close_observed_episodes(
            episodes,
            missions,
            observation["observed_at"],
            observation["mission_id"],
            observation["reason"],
        )
        return
    lifecycle = event_worker_lifecycle(event.record, label)
    if lifecycle is not None:
        fault = _mission_slot_claim_fault(worker_slots, lifecycle)
        if fault is not None:
            # The event stays committed audit evidence; it simply may not
            # take a mission slot another claim already holds.
            lifecycle_outcomes[event.event_id] = (False, LIFECYCLE_RETAINED_UNMATCHED)
            entry = _mission_slot_entry(worker_slots, lifecycle["worker_id"], event)
            entry["conflicts"].append(
                _mission_slot_conflict(
                    lifecycle["mission_id"],
                    fault,
                    MISSION_SLOT_CONFLICT_LIFECYCLE,
                    None,
                    event,
                )
            )
            return
        applied, outcome = apply_worker_lifecycle(missions, lifecycle, event)
        lifecycle_outcomes[event.event_id] = (applied, outcome)
        # Only an event that moved the materialized mission may move the
        # slot with it; audit-only evidence claims nothing.
        if applied:
            apply_lifecycle_mission_slot(worker_slots, lifecycle, event)
            _record_episode_evidence(episodes, lifecycle["mission_id"], event)
        return
    field, record = event_mission_control(event.record, label)
    if record is None:
        return
    if field == MISSION_DIALOG_PAYLOAD_FIELD:
        mission_outcomes[event.event_id] = apply_mission_dialog(dialogs, record, event)
    elif field == MISSION_CANCELLATION_PAYLOAD_FIELD:
        mission_outcomes[event.event_id] = apply_mission_cancellation(
            cancellations, record, event
        )
        # A cooperative cancellation the *controller* minted is a rung of a
        # bounded ladder, and the terminal-item ladder opens with it, so the
        # episode it belongs to starts here.  An operator's own cancellation
        # carries their reason and opens nothing.
        if record["reason"] in CONTROLLER_CANCEL_REASONS.values():
            _open_recovery_episode(episodes, record["mission_id"], event.revision)
    elif field == MISSION_RECOVERY_PAYLOAD_FIELD:
        mission_outcomes[event.event_id] = apply_mission_recovery(recoveries, record, event)
        _open_recovery_episode(episodes, record["mission_id"], event.revision)
    elif field == CONTROLLER_DISPATCH_PAYLOAD_FIELD:
        mission_outcomes[event.event_id] = apply_controller_dispatch(
            missions, worker_slots, controller, record, event
        )
        _close_observed_episodes(
            episodes, missions, record["decided_at"], record["mission_id"], record["reason"]
        )
    else:
        mission_outcomes[event.event_id] = apply_mission_replacement(
            missions, worker_slots, record, event
        )


def fold_mission_state(events: Sequence["CommittedEvent"] = ()) -> MissionState:
    """Replay committed events into every materialized mission projection."""

    missions: Dict[str, Dict[str, Any]] = {}
    dialogs: Dict[str, Dict[str, Any]] = {}
    cancellations: Dict[str, Dict[str, Any]] = {}
    recoveries: Dict[str, Dict[str, Any]] = {}
    worker_slots: Dict[str, Dict[str, Any]] = {}
    controller: Dict[str, Any] = {}
    lifecycle_outcomes: Dict[str, Tuple[bool, str]] = {}
    mission_outcomes: Dict[str, Tuple[bool, str]] = {}
    slot_claims: Dict[str, Tuple[int, str]] = {}
    recovery_episodes: Dict[str, Tuple[int, bool]] = {}

    def record_slot_claims(event: "CommittedEvent") -> None:
        """Stamp the revision at which each worker's current claim began.

        This is observed from the folded slots rather than threaded through
        every applier on purpose: any future claim path is covered without a
        second place to keep in step, and reserving a slot and later accepting
        the same mission on it stay one claim rather than two.
        """

        for worker_id in worker_slots:
            entry = worker_slots[worker_id]
            if entry["state"] not in MISSION_SLOT_CLAIMED_STATES:
                continue
            claim = slot_claims.get(worker_id)
            if claim is None or claim[1] != entry["mission_id"]:
                slot_claims[worker_id] = (event.revision, entry["mission_id"])

    for event in events:
        label = f"{EVENTS_DIR_NAME}/{event.path.name}"
        _fold_one_event(
            event,
            label,
            missions=missions,
            dialogs=dialogs,
            cancellations=cancellations,
            recoveries=recoveries,
            worker_slots=worker_slots,
            controller=controller,
            lifecycle_outcomes=lifecycle_outcomes,
            mission_outcomes=mission_outcomes,
            episodes=recovery_episodes,
        )
        record_slot_claims(event)

    return MissionState(
        missions=missions,
        dialogs=dialogs,
        cancellations=cancellations,
        recoveries=recoveries,
        worker_slots=worker_slots,
        controller=controller,
        lifecycle_outcomes=lifecycle_outcomes,
        mission_outcomes=mission_outcomes,
        slot_claims=slot_claims,
        recovery_episodes=recovery_episodes,
    )


def _command_conflict_reason(
    stored: Mapping[str, Any],
    offered: Mapping[str, Any],
) -> Optional[str]:
    """Say whether an offered envelope conflicts with the stored one, and how."""

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
    event: "CommittedEvent",
) -> Dict[str, Any]:
    """Build one materialized conflict entry from the event that recorded it."""

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
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed command delivery into the materialized command slots.

    A command ID is claimed exactly once.  The first committed envelope for an
    ID registers it; a later delivery of the identical envelope is a redelivery
    that is retained as evidence and applies nothing; a delivery that changes the
    payload digest or any other identity field is a hard conflict that is
    recorded against the stored command and never replaces it.
    """

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
    event: "CommittedEvent",
) -> Tuple[bool, str]:
    """Fold one committed acknowledgement into the materialized command slots.

    An acknowledgement of an unknown command, of a payload the stored command
    never carried, or of an outcome the closed order does not allow is retained
    as durable audit evidence and moves nothing.  A rejection that names a
    different payload digest is the worker-side half of a reuse conflict and is
    recorded as one.
    """

    command_id = acknowledgement["command_id"]
    slot = slots.get(command_id)
    if slot is None:
        return False, COMMAND_FOLD_RETAINED_UNKNOWN

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
    events: Sequence["CommittedEvent"] = (),
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    """Replay committed events into command slots and per-event fold outcomes."""

    slots: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, Tuple[bool, str]] = {}
    for event in events:
        label = f"{EVENTS_DIR_NAME}/{event.path.name}"
        envelope = event_command_envelope(event.record, label)
        if envelope is not None:
            outcomes[event.event_id] = apply_command_envelope(slots, envelope, event)
            continue
        acknowledgement = event_command_acknowledgement(event.record, label)
        if acknowledgement is not None:
            outcomes[event.event_id] = apply_command_acknowledgement(
                slots, acknowledgement, event
            )
    return slots, outcomes


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


def _classify_published_ledger(
    root: Path,
    control_id: str,
    expected: bytes,
) -> Tuple[Optional[int], str]:
    """Say why the published projection does or does not equal the rebuild.

    This is a pure read: it never mutates, never locks, and never trusts the
    published bytes.  Both the locked projection and the read-only controller
    observation classify the ledger through it, so a projection this build would
    rebuild can never be described one way by the repair path and another way by
    the reconciler.
    """

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
    """Report whether the derived compatibility view equals the rebuild.

    Like the ledger classification beside it this is a pure read: it never
    mutates, never locks, and never trusts the published bytes, so the locked
    projection and the read-only controller observation cannot describe the same
    derived view two different ways.
    """

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

        return _classify_published_ledger(self.root, control_id, expected)

    def _view_matches(self, expected: str) -> bool:
        """Report whether the derived compatibility view equals the rebuild."""

        return _published_view_matches(self.root, expected)

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


control_projection.ControlStoreError = ControlStoreError
control_projection.CONTROL_SCHEMA_VERSION = CONTROL_SCHEMA_VERSION
control_projection.CONTROL_METADATA_NAME = CONTROL_METADATA_NAME
control_projection.LEDGER_NAME = LEDGER_NAME
control_projection.EVENTS_NAME = EVENTS_NAME
control_projection.LEDGER_TEMPORARY_NAME = LEDGER_TEMPORARY_NAME
control_projection.EVENTS_VIEW_TEMPORARY_NAME = EVENTS_VIEW_TEMPORARY_NAME
control_projection.EVENTS_DIR_NAME = EVENTS_DIR_NAME
control_projection.DEFAULT_LEDGER_COMMAND = DEFAULT_LEDGER_COMMAND
control_projection.DEFAULT_LOCK_POLL_SECONDS = DEFAULT_LOCK_POLL_SECONDS
control_projection.PROJECTION_CURRENT = PROJECTION_CURRENT
control_projection.PROJECTION_REBUILT = PROJECTION_REBUILT
control_projection.PROJECTION_WOULD_REBUILD = PROJECTION_WOULD_REBUILD
control_projection.PROJECTION_REASON_CURRENT = PROJECTION_REASON_CURRENT
control_projection.PROJECTION_REASON_MISSING = PROJECTION_REASON_MISSING
control_projection.PROJECTION_REASON_CORRUPT = PROJECTION_REASON_CORRUPT
control_projection.PROJECTION_REASON_AHEAD = PROJECTION_REASON_AHEAD
control_projection.PROJECTION_REASON_STALE = PROJECTION_REASON_STALE
control_projection.PROJECTION_REASON_DIVERGENT = PROJECTION_REASON_DIVERGENT
control_projection.PROJECTION_REASON_VIEW = PROJECTION_REASON_VIEW
control_projection.LEDGER_PROJECTION_FIELDS = LEDGER_PROJECTION_FIELDS
control_projection.LEDGER_WORKER_MISSIONS_FIELD = LEDGER_WORKER_MISSIONS_FIELD
control_projection.LEDGER_COMMANDS_FIELD = LEDGER_COMMANDS_FIELD
control_projection.LEDGER_MISSION_DIALOGS_FIELD = LEDGER_MISSION_DIALOGS_FIELD
control_projection.LEDGER_MISSION_CANCELLATIONS_FIELD = LEDGER_MISSION_CANCELLATIONS_FIELD
control_projection.LEDGER_MISSION_RECOVERIES_FIELD = LEDGER_MISSION_RECOVERIES_FIELD
control_projection.LEDGER_MISSION_SLOTS_FIELD = LEDGER_MISSION_SLOTS_FIELD
control_projection.LEDGER_CONTROLLER_FIELD = LEDGER_CONTROLLER_FIELD
control_projection._require_absolute_root = (
    lambda *args, **kwargs: _require_absolute_root(*args, **kwargs)
)
control_projection._require_directory = (
    lambda *args, **kwargs: _require_directory(*args, **kwargs)
)
control_projection._configured_lock_timeout = (
    lambda *args, **kwargs: _configured_lock_timeout(*args, **kwargs)
)
control_projection._validated_seconds = (
    lambda *args, **kwargs: _validated_seconds(*args, **kwargs)
)
control_projection._same_filesystem_identity = (
    lambda *args, **kwargs: _same_filesystem_identity(*args, **kwargs)
)
control_projection._fsync_directory = (
    lambda *args, **kwargs: _fsync_directory(*args, **kwargs)
)
control_projection._serialized_record = (
    lambda *args, **kwargs: _serialized_record(*args, **kwargs)
)
control_projection._require_uuid = lambda *args, **kwargs: _require_uuid(*args, **kwargs)
control_projection._require_timestamp = (
    lambda *args, **kwargs: _require_timestamp(*args, **kwargs)
)
control_projection._validate_canonical_roots = (
    lambda *args, **kwargs: _validate_canonical_roots(*args, **kwargs)
)
control_projection.validate_ledger = lambda *args, **kwargs: validate_ledger(*args, **kwargs)
control_projection._event_ledger_declarations = (
    lambda *args, **kwargs: _event_ledger_declarations(*args, **kwargs)
)
control_projection._derived_ledger_id = lambda *args, **kwargs: _derived_ledger_id(*args, **kwargs)
control_projection.fold_mission_state = (
    lambda *args, **kwargs: fold_mission_state(*args, **kwargs)
)
control_projection.fold_commands = lambda *args, **kwargs: fold_commands(*args, **kwargs)
control_projection.inspect_control_events = (
    lambda *args, **kwargs: inspect_control_events(*args, **kwargs)
)
control_projection.PortableControlLock = (
    lambda *args, **kwargs: PortableControlLock(*args, **kwargs)
)
control_projection._ledger_projection_fault = (
    lambda boundary, projection: _ledger_projection_fault(boundary, projection)
)

build_ledger_projection = control_projection.build_ledger_projection
build_events_view = control_projection.build_events_view
_write_projection_temporary = control_projection._write_projection_temporary
_replace_projection = control_projection._replace_projection
_classify_published_ledger = control_projection._classify_published_ledger
_published_view_matches = control_projection._published_view_matches
LedgerProjectionResult = control_projection.LedgerProjectionResult
ControlLedgerProjection = control_projection.ControlLedgerProjection
replay_control_ledger = control_projection.replay_control_ledger


control_journal.ControlStoreError = ControlStoreError
control_journal.CONTROL_SCHEMA_VERSION = CONTROL_SCHEMA_VERSION
control_journal.CONTROL_METADATA_NAME = CONTROL_METADATA_NAME
control_journal.EVENTS_DIR_NAME = EVENTS_DIR_NAME
control_journal.PENDING_DIR_NAME = PENDING_DIR_NAME
control_journal.EVENT_FILENAME_SUFFIX = EVENT_FILENAME_SUFFIX
control_journal.EVENT_REVISION_DIGITS = EVENT_REVISION_DIGITS
control_journal.DEFAULT_EVENT_ACTOR = DEFAULT_EVENT_ACTOR
control_journal.DEFAULT_EVENT_COMMAND = DEFAULT_EVENT_COMMAND
control_journal.DEFAULT_LOCK_POLL_SECONDS = DEFAULT_LOCK_POLL_SECONDS
control_journal.EVENT_COMMITTED = EVENT_COMMITTED
control_journal.EVENT_WOULD_COMMIT = EVENT_WOULD_COMMIT
control_journal._require_absolute_root = lambda *args, **kwargs: _require_absolute_root(*args, **kwargs)
control_journal._require_directory = lambda *args, **kwargs: _require_directory(*args, **kwargs)
control_journal._require_regular_file = lambda *args, **kwargs: _require_regular_file(*args, **kwargs)
control_journal._load_json = lambda *args, **kwargs: _load_json(*args, **kwargs)
control_journal._serialized_record = lambda *args, **kwargs: _serialized_record(*args, **kwargs)
control_journal._write_json = lambda *args, **kwargs: _write_json(*args, **kwargs)
control_journal._fsync_directory = lambda *args, **kwargs: _fsync_directory(*args, **kwargs)
control_journal._same_filesystem_identity = (
    lambda *args, **kwargs: _same_filesystem_identity(*args, **kwargs)
)
control_journal._configured_lock_timeout = (
    lambda *args, **kwargs: _configured_lock_timeout(*args, **kwargs)
)
control_journal._validated_seconds = lambda *args, **kwargs: _validated_seconds(*args, **kwargs)
control_journal._require_string = lambda *args, **kwargs: _require_string(*args, **kwargs)
control_journal.validate_root_metadata = (
    lambda *args, **kwargs: validate_root_metadata(*args, **kwargs)
)
control_journal.validate_event = lambda *args, **kwargs: validate_event(*args, **kwargs)
control_journal.build_ledger_projection = (
    lambda *args, **kwargs: build_ledger_projection(*args, **kwargs)
)
control_journal.ControlLedgerProjection = lambda *args, **kwargs: ControlLedgerProjection(*args, **kwargs)
control_journal.PortableControlLock = lambda *args, **kwargs: PortableControlLock(*args, **kwargs)
control_journal.utc_timestamp = lambda: utc_timestamp()
control_journal._event_publication_fault = (
    lambda boundary, publication: _event_publication_fault(boundary, publication)
)

CommittedEvent = control_journal.CommittedEvent
EventHistory = control_journal.EventHistory
_event_filename = control_journal._event_filename
_parse_event_filename = control_journal._parse_event_filename
_read_pending_debris = control_journal._read_pending_debris
read_committed_sequence = control_journal.read_committed_sequence
read_committed_events = control_journal.read_committed_events
inspect_control_events = control_journal.inspect_control_events
EventPublicationResult = control_journal.EventPublicationResult
ControlEventPublication = control_journal.ControlEventPublication
publish_control_event = control_journal.publish_control_event


# --- versioned worker lifecycle recording and freshness observation ----------

control_lifecycle.ControlStoreError = ControlStoreError
control_lifecycle.WORKER_LIFECYCLE_SCHEMA_VERSION = WORKER_LIFECYCLE_SCHEMA_VERSION
control_lifecycle.WORKER_LIFECYCLE_RECORD_TYPE = WORKER_LIFECYCLE_RECORD_TYPE
control_lifecycle.WORKER_LIFECYCLE_PAYLOAD_FIELD = WORKER_LIFECYCLE_PAYLOAD_FIELD
control_lifecycle.WORKER_LIFECYCLE_EVENT_PREFIX = WORKER_LIFECYCLE_EVENT_PREFIX
control_lifecycle.LIFECYCLE_PENDING_DISPATCH = LIFECYCLE_PENDING_DISPATCH
control_lifecycle.LIFECYCLE_ACCEPTED = LIFECYCLE_ACCEPTED
control_lifecycle.LIFECYCLE_RUNNING = LIFECYCLE_RUNNING
control_lifecycle.LIFECYCLE_BLOCKED = LIFECYCLE_BLOCKED
control_lifecycle.LIFECYCLE_COMPLETED = LIFECYCLE_COMPLETED
control_lifecycle.LIFECYCLE_FAILED = LIFECYCLE_FAILED
control_lifecycle.LIFECYCLE_CANCELLED = LIFECYCLE_CANCELLED
control_lifecycle.LIFECYCLE_REPLACED = LIFECYCLE_REPLACED
control_lifecycle.WORKER_LIFECYCLE_STATES = WORKER_LIFECYCLE_STATES
control_lifecycle.WORKER_LIFECYCLE_ACTIVE_STATES = WORKER_LIFECYCLE_ACTIVE_STATES
control_lifecycle.WORKER_LIFECYCLE_TERMINAL_STATES = WORKER_LIFECYCLE_TERMINAL_STATES
control_lifecycle.WORKER_LIFECYCLE_REASON_REQUIRED = WORKER_LIFECYCLE_REASON_REQUIRED
control_lifecycle.WORKER_LIFECYCLE_TRANSITIONS = WORKER_LIFECYCLE_TRANSITIONS
control_lifecycle.WORKER_LIFECYCLE_FIELDS = WORKER_LIFECYCLE_FIELDS
control_lifecycle.WORKER_LIFECYCLE_BLOCKER_FIELDS = WORKER_LIFECYCLE_BLOCKER_FIELDS
control_lifecycle.LIFECYCLE_APPLIED = LIFECYCLE_APPLIED
control_lifecycle.LIFECYCLE_RETAINED_TERMINAL = LIFECYCLE_RETAINED_TERMINAL
control_lifecycle.LIFECYCLE_RETAINED_STALE_SEQUENCE = LIFECYCLE_RETAINED_STALE_SEQUENCE
control_lifecycle.LIFECYCLE_RETAINED_INVALID_TRANSITION = LIFECYCLE_RETAINED_INVALID_TRANSITION
control_lifecycle.LIFECYCLE_RETAINED_UNMATCHED = LIFECYCLE_RETAINED_UNMATCHED
control_lifecycle.LIFECYCLE_OBSERVATION_FRESH = LIFECYCLE_OBSERVATION_FRESH
control_lifecycle.LIFECYCLE_OBSERVATION_STALE = LIFECYCLE_OBSERVATION_STALE
control_lifecycle.LIFECYCLE_OBSERVATION_TERMINAL = LIFECYCLE_OBSERVATION_TERMINAL
control_lifecycle.LIFECYCLE_STALE_REASON = LIFECYCLE_STALE_REASON
control_lifecycle.LIFECYCLE_STALE_RECOVERY = LIFECYCLE_STALE_RECOVERY
control_lifecycle.DEFAULT_LIFECYCLE_COMMAND = DEFAULT_LIFECYCLE_COMMAND
control_lifecycle.DEFAULT_LOCK_POLL_SECONDS = DEFAULT_LOCK_POLL_SECONDS
control_lifecycle.EVENTS_DIR_NAME = EVENTS_DIR_NAME
control_lifecycle._require_typed_references = (
    lambda *args, **kwargs: _require_typed_references(*args, **kwargs)
)
control_lifecycle._require_object = lambda *args, **kwargs: _require_object(*args, **kwargs)
control_lifecycle._require_record_type = (
    lambda *args, **kwargs: _require_record_type(*args, **kwargs)
)
control_lifecycle._require_string = lambda *args, **kwargs: _require_string(*args, **kwargs)
control_lifecycle._require_identifier = (
    lambda *args, **kwargs: _require_identifier(*args, **kwargs)
)
control_lifecycle._require_uuid = lambda *args, **kwargs: _require_uuid(*args, **kwargs)
control_lifecycle._require_optional_uuid = (
    lambda *args, **kwargs: _require_optional_uuid(*args, **kwargs)
)
control_lifecycle._require_positive_integer = (
    lambda *args, **kwargs: _require_positive_integer(*args, **kwargs)
)
control_lifecycle._require_optional_string = (
    lambda *args, **kwargs: _require_optional_string(*args, **kwargs)
)
control_lifecycle._parsed_timestamp = (
    lambda *args, **kwargs: _parsed_timestamp(*args, **kwargs)
)
control_lifecycle._require_absolute_root = (
    lambda *args, **kwargs: _require_absolute_root(*args, **kwargs)
)
control_lifecycle.publish_control_event = (
    lambda *args, **kwargs: publish_control_event(*args, **kwargs)
)
control_lifecycle.inspect_control_events = (
    lambda *args, **kwargs: inspect_control_events(*args, **kwargs)
)
control_lifecycle.fold_mission_state = (
    lambda *args, **kwargs: fold_mission_state(*args, **kwargs)
)
control_lifecycle.utc_timestamp = lambda: utc_timestamp()
control_lifecycle.CommittedEvent = CommittedEvent
control_lifecycle.EventPublicationResult = EventPublicationResult

validate_worker_lifecycle = control_lifecycle.validate_worker_lifecycle
event_worker_lifecycle = control_lifecycle.event_worker_lifecycle
apply_worker_lifecycle = control_lifecycle.apply_worker_lifecycle
fold_worker_missions = control_lifecycle.fold_worker_missions
LifecycleRecordResult = control_lifecycle.LifecycleRecordResult
WorkerLifecycleObservation = control_lifecycle.WorkerLifecycleObservation
build_worker_lifecycle = control_lifecycle.build_worker_lifecycle
record_worker_lifecycle = control_lifecycle.record_worker_lifecycle
_observed_slot = control_lifecycle._observed_slot
observe_worker_lifecycle = control_lifecycle.observe_worker_lifecycle

# --- versioned command envelopes, acknowledgements, and idempotent delivery --


@dataclass(frozen=True)
class CommandRecordResult:
    """Outcome of committing one command delivery or one acknowledgement."""

    root: Path
    publication: EventPublicationResult
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
        """Report a delivery or acknowledgement recorded as a durable conflict."""

        return self.outcome == COMMAND_FOLD_CONFLICT_RECORDED


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
    """Assemble one complete command envelope and validate it before use."""

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
    """Assemble one complete acknowledgement and validate it before use."""

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
    """Fold the committed events of a control root into command slots.

    Every reader starts from the committed event files on disk, so a command,
    its acknowledgements, and its conflicts are answerable by any cold process
    and never depend on memory retained by the process that recorded them.
    """

    _metadata, history = inspect_control_events(root)
    slots, _outcomes = fold_commands(history.events)
    return slots


def _committed_events_after_publication(
    publication: EventPublicationResult,
) -> Tuple[CommittedEvent, ...]:
    """Return committed authority after publication, plus a dry run's candidate.

    A real publication is answered by the committed sequence alone.  A dry run
    committed nothing, so its candidate event is appended in memory only, which
    is what lets `--dry-run` report the decision it would have made without
    changing one byte of the store.
    """

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
    publication: EventPublicationResult,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Tuple[bool, str]]]:
    """Re-derive the command fold from committed authority after publication."""

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
) -> CommandRecordResult:
    """Deliver one command envelope exactly once, whatever the delivery count.

    Delivery is uncertain by nature, so this operation is defined for retries
    rather than for first attempts only:

    * an unknown command ID commits the envelope and registers the command;
    * a redelivery of the identical envelope commits a `duplicate`
      acknowledgement and returns the stored result without applying anything
      again;
    * a delivery that reuses the ID for a different payload digest or a
      different envelope is committed and recorded as a durable conflict, and
      the caller is refused.

    The committed fold, not this pre-read, is authoritative: two concurrent
    deliveries of the same new command both commit, and exactly one of them
    registers it.

    A managed mission operation passes its correlated mission-control record as
    `correlated_record`.  It rides in the very same committed event as the
    envelope that carries it, so a question, reply, access-prompt response,
    cancellation, or replacement and its command are one atomic fact rather than
    two records that could disagree.  A redelivery commits only the stored
    duplicate acknowledgement, so the correlated record is never applied twice.
    """

    validated = validate_command_envelope(dict(envelope))
    # The actor is rendered verbatim into the line-oriented `list-events`
    # payload, so a command deliverer is constrained exactly like every other
    # identifier this protocol accepts.
    actor = _require_identifier({"actor": actor}, "actor", "command delivery")
    root = _require_absolute_root(str(root), "configured")
    stored_slot = read_command_slots(root).get(validated["command_id"])

    acknowledgement: Optional[Dict[str, Any]] = None
    if stored_slot is None:
        event_type = COMMAND_REGISTERED_EVENT_TYPE
        payload: Dict[str, Any] = {COMMAND_ENVELOPE_PAYLOAD_FIELD: validated}
    elif _command_conflict_reason(stored_slot["envelope"], validated) is not None:
        # A reused ID is committed as the delivery it actually is, so the
        # conflict is recorded against the stored command by the same
        # deterministic fold that would resolve a concurrent race.
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
            # The derived ledger's active pointers are declared by the very event
            # that creates the mission, so `ledger.json` can never claim an active
            # mission no committed event authorized.  A redelivery commits only a
            # duplicate acknowledgement and therefore re-declares nothing.
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
    """Commit one acknowledgement of a registered command, failing closed first.

    An acknowledgement of a command this control root never registered, of a
    payload the stored command never carried, or of an outcome the closed
    acknowledgement order does not allow is refused before any candidate event
    exists.  A rejection naming a different digest is allowed exactly because it
    is how a worker reports a reused command ID.
    """

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
    """Report materialized commands from committed events without mutation.

    The report is derived from committed event files rather than from the
    published projection, so a stale, corrupt, or hand-edited `ledger.json`
    can never answer for a command.
    """

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


# --- versioned command envelopes, acknowledgements, and idempotent delivery --

control_commands.ControlStoreError = ControlStoreError
control_commands.COMMAND_SCHEMA_VERSION = COMMAND_SCHEMA_VERSION
control_commands.COMMAND_ENVELOPE_RECORD_TYPE = COMMAND_ENVELOPE_RECORD_TYPE
control_commands.COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE = COMMAND_ACKNOWLEDGEMENT_RECORD_TYPE
control_commands.COMMAND_ENVELOPE_PAYLOAD_FIELD = COMMAND_ENVELOPE_PAYLOAD_FIELD
control_commands.COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD = COMMAND_ACKNOWLEDGEMENT_PAYLOAD_FIELD
control_commands.COMMAND_REGISTERED_EVENT_TYPE = COMMAND_REGISTERED_EVENT_TYPE
control_commands.COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX = COMMAND_ACKNOWLEDGEMENT_EVENT_PREFIX
control_commands.COMMAND_DIGEST_ALGORITHM = COMMAND_DIGEST_ALGORITHM
control_commands.COMMAND_DIGEST_PREFIX = COMMAND_DIGEST_PREFIX
control_commands.COMMAND_DIGEST_HEX_DIGITS = COMMAND_DIGEST_HEX_DIGITS
control_commands.COMMAND_DIGEST_HEX_ALPHABET = COMMAND_DIGEST_HEX_ALPHABET
control_commands.COMMAND_TARGET_WORKER = COMMAND_TARGET_WORKER
control_commands.COMMAND_TARGET_QUEUE = COMMAND_TARGET_QUEUE
control_commands.COMMAND_TARGET_KINDS = COMMAND_TARGET_KINDS
control_commands.COMMAND_TARGET_FIELDS = COMMAND_TARGET_FIELDS
control_commands.COMMAND_BOUNDARY_FIELDS = COMMAND_BOUNDARY_FIELDS
control_commands.COMMAND_ENVELOPE_FIELDS = COMMAND_ENVELOPE_FIELDS
control_commands.COMMAND_ENVELOPE_IDENTITY_FIELDS = COMMAND_ENVELOPE_IDENTITY_FIELDS
control_commands.COMMAND_ACKNOWLEDGEMENT_FIELDS = COMMAND_ACKNOWLEDGEMENT_FIELDS
control_commands.COMMAND_ACCEPTED = COMMAND_ACCEPTED
control_commands.COMMAND_APPLIED = COMMAND_APPLIED
control_commands.COMMAND_REJECTED = COMMAND_REJECTED
control_commands.COMMAND_DUPLICATE = COMMAND_DUPLICATE
control_commands.COMMAND_ACKNOWLEDGEMENT_OUTCOMES = COMMAND_ACKNOWLEDGEMENT_OUTCOMES
control_commands.COMMAND_ACKNOWLEDGEMENT_REASON_REQUIRED = COMMAND_ACKNOWLEDGEMENT_REASON_REQUIRED
control_commands.COMMAND_STATUS_REGISTERED = COMMAND_STATUS_REGISTERED
control_commands.COMMAND_ACKNOWLEDGEMENT_TRANSITIONS = COMMAND_ACKNOWLEDGEMENT_TRANSITIONS
control_commands.COMMAND_CONFLICT_DIGEST = COMMAND_CONFLICT_DIGEST
control_commands.COMMAND_CONFLICT_ENVELOPE = COMMAND_CONFLICT_ENVELOPE
control_commands.COMMAND_CONFLICT_DELIVERY = COMMAND_CONFLICT_DELIVERY
control_commands.COMMAND_CONFLICT_ACKNOWLEDGEMENT = COMMAND_CONFLICT_ACKNOWLEDGEMENT
control_commands.COMMAND_FOLD_REGISTERED = COMMAND_FOLD_REGISTERED
control_commands.COMMAND_FOLD_ACKNOWLEDGED = COMMAND_FOLD_ACKNOWLEDGED
control_commands.COMMAND_FOLD_CONFLICT_RECORDED = COMMAND_FOLD_CONFLICT_RECORDED
control_commands.COMMAND_FOLD_RETAINED_DUPLICATE = COMMAND_FOLD_RETAINED_DUPLICATE
control_commands.COMMAND_FOLD_RETAINED_UNKNOWN = COMMAND_FOLD_RETAINED_UNKNOWN
control_commands.COMMAND_FOLD_RETAINED_DIGEST = COMMAND_FOLD_RETAINED_DIGEST
control_commands.COMMAND_FOLD_RETAINED_ORDER = COMMAND_FOLD_RETAINED_ORDER
control_commands.COMMAND_DUPLICATE_REASON = COMMAND_DUPLICATE_REASON
control_commands.DEFAULT_EVENT_ACTOR = DEFAULT_EVENT_ACTOR
control_commands.DEFAULT_COMMAND_REGISTER_COMMAND = DEFAULT_COMMAND_REGISTER_COMMAND
control_commands.DEFAULT_COMMAND_ACKNOWLEDGE_COMMAND = DEFAULT_COMMAND_ACKNOWLEDGE_COMMAND
control_commands.DEFAULT_LOCK_POLL_SECONDS = DEFAULT_LOCK_POLL_SECONDS
control_commands.EVENTS_DIR_NAME = EVENTS_DIR_NAME
control_commands._require_object = lambda *args, **kwargs: _require_object(*args, **kwargs)
control_commands._require_record_type = lambda *args, **kwargs: _require_record_type(*args, **kwargs)
control_commands._require_string = lambda *args, **kwargs: _require_string(*args, **kwargs)
control_commands._require_identifier = lambda *args, **kwargs: _require_identifier(*args, **kwargs)
control_commands._require_optional_identifier = (
    lambda *args, **kwargs: _require_optional_identifier(*args, **kwargs)
)
control_commands._require_uuid = lambda *args, **kwargs: _require_uuid(*args, **kwargs)
control_commands._require_optional_uuid = (
    lambda *args, **kwargs: _require_optional_uuid(*args, **kwargs)
)
control_commands._require_timestamp = lambda *args, **kwargs: _require_timestamp(*args, **kwargs)
control_commands._require_optional_string = (
    lambda *args, **kwargs: _require_optional_string(*args, **kwargs)
)
control_commands._require_typed_references = (
    lambda *args, **kwargs: _require_typed_references(*args, **kwargs)
)
control_commands._require_root_path = lambda *args, **kwargs: _require_root_path(*args, **kwargs)
control_commands._parsed_timestamp = lambda *args, **kwargs: _parsed_timestamp(*args, **kwargs)
control_commands._require_absolute_root = (
    lambda *args, **kwargs: _require_absolute_root(*args, **kwargs)
)
control_commands.publish_control_event = (
    lambda *args, **kwargs: publish_control_event(*args, **kwargs)
)
control_commands.inspect_control_events = (
    lambda *args, **kwargs: inspect_control_events(*args, **kwargs)
)
control_commands.utc_timestamp = lambda: utc_timestamp()
control_commands.CommittedEvent = CommittedEvent
control_commands.EventPublicationResult = EventPublicationResult

canonical_command_payload = control_commands.canonical_command_payload
command_payload_digest = control_commands.command_payload_digest
_require_digest = control_commands._require_digest
_require_command_target = control_commands._require_command_target
_require_boundary_root = control_commands._require_boundary_root
_require_command_boundaries = control_commands._require_command_boundaries
_require_command_schema_version = control_commands._require_command_schema_version
validate_command_envelope = control_commands.validate_command_envelope
validate_command_acknowledgement = control_commands.validate_command_acknowledgement
event_command_envelope = control_commands.event_command_envelope
event_command_acknowledgement = control_commands.event_command_acknowledgement
CommandRecordResult = control_commands.CommandRecordResult
_command_conflict_reason = control_commands._command_conflict_reason
_command_conflict_entry = control_commands._command_conflict_entry
apply_command_envelope = control_commands.apply_command_envelope
apply_command_acknowledgement = control_commands.apply_command_acknowledgement
fold_commands = control_commands.fold_commands
build_command_envelope = control_commands.build_command_envelope
build_command_acknowledgement = control_commands.build_command_acknowledgement
read_command_slots = control_commands.read_command_slots
_committed_events_after_publication = control_commands._committed_events_after_publication
_folded_commands_after_publication = control_commands._folded_commands_after_publication
register_command = control_commands.register_command
acknowledge_command = control_commands.acknowledge_command
observe_commands = control_commands.observe_commands


# --- managed mission dialogs, cancellation, and replacement -------------------


@dataclass(frozen=True)
class MissionControlResult:
    """Outcome of one managed mission command and its correlated record.

    Delivery and correlation are separate answers exactly as publication and
    materialization are: the command half reports whether the envelope was
    registered, duplicated, or conflicted, and the mission half reports whether
    the dialog, cancellation, or slot projection actually moved.
    """

    command: CommandRecordResult
    record_field: str
    record: Dict[str, Any]
    applied: bool
    outcome: str
    state: MissionState

    @property
    def committed(self) -> bool:
        return self.command.committed

    @property
    def command_id(self) -> str:
        return self.command.command_id

    @property
    def conflicted(self) -> bool:
        """Report a mission operation refused and recorded as a durable conflict."""

        return self.outcome == MISSION_FOLD_CONFLICT_RECORDED

    @property
    def redelivered(self) -> bool:
        """Report an idempotent redelivery that correctly applied nothing again."""

        return self.outcome == MISSION_FOLD_RETAINED_DUPLICATE

    @property
    def worker_slot(self) -> Optional[Dict[str, Any]]:
        worker_id = self.record.get("worker_id")
        if worker_id is None:
            return None
        return self.state.worker_slots.get(worker_id)

    @property
    def recorded_conflict(self) -> Optional[Dict[str, Any]]:
        """Return the slot conflict this operation's own event recorded, if any.

        A worker slot accumulates every conflict ever recorded against it, so a
        refusal may only be described by the one its own committed event added.
        """

        slot = self.worker_slot
        if slot is None:
            return None
        for conflict in slot["conflicts"]:
            if conflict["event_id"] == self.command.publication.event_id:
                return conflict
        return None


@dataclass(frozen=True)
class CancellationObservation:
    """One observation of a cooperative cancellation at an explicit moment.

    An observation is not a mission state.  `timed-out` says only that the
    declared acknowledgement deadline passed with no acknowledgement recorded,
    names a recoverable reason, and records that a bounded recovery action is
    owed.  It never claims `failed`: a silent worker has ended nothing.
    """

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
    """Every materialized mission dialog, cancellation, and slot at one moment."""

    root: Path
    as_of: str
    dialogs: Tuple[Dict[str, Any], ...]
    cancellations: Tuple[CancellationObservation, ...]
    worker_slots: Tuple[Tuple[str, Dict[str, Any]], ...]
    # The materialized lifecycle state of every mission a dialog names, so a
    # prompt can be observed against the mission it belongs to rather than being
    # reported as actionable long after that mission ended.
    mission_states: Mapping[str, str]

    def dialog_observation(self, entry: Mapping[str, Any]) -> str:
        """Classify one committed dialog at this report's moment."""

        if entry["state"] != MISSION_DIALOG_PENDING:
            return MISSION_DIALOG_OBSERVATION_SETTLED
        state = self.mission_states.get(entry["dialog"]["mission_id"])
        if state in WORKER_LIFECYCLE_ACTIVE_STATES:
            return MISSION_DIALOG_OBSERVATION_ANSWERABLE
        return MISSION_DIALOG_OBSERVATION_ORPHANED

    @property
    def pending_prompts(self) -> Tuple[Dict[str, Any], ...]:
        """Return the prompts an operator can still answer through the protocol."""

        return tuple(
            entry
            for entry in self.dialogs
            if self.dialog_observation(entry) == MISSION_DIALOG_OBSERVATION_ANSWERABLE
        )

    @property
    def orphaned_prompts(self) -> Tuple[Dict[str, Any], ...]:
        """Return the prompts whose mission ended before anyone answered them.

        `answer-question` fails closed on a mission that is no longer active, so
        these are unanswerable and owed a bounded recovery action instead.
        """

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
    """Assemble one complete mission dialog record and validate it before use."""

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
    """Assemble one complete cancellation request and validate it before use."""

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
    """Assemble one complete replacement request and validate it before use."""

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
    """Assemble one complete bounded recovery request and validate it before use."""

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


def read_mission_state(root: Path) -> MissionState:
    """Fold the committed events of a control root into every mission projection.

    Every reader starts from the committed event files on disk, so a pending
    question, a cancellation outcome, and the one slot each worker holds are
    answerable by any cold process and never depend on memory retained by the
    process that recorded them, nor on the derived `ledger.json`.
    """

    _metadata, history = inspect_control_events(root)
    return fold_mission_state(history.events)


def require_active_mission(
    state: MissionState,
    mission_id: str,
    worker_id: str,
    queue_item_id: str,
    label: str,
) -> Dict[str, Any]:
    """Require one materialized, correlated, still-active mission, failing closed.

    This is what makes a managed interaction correlated rather than uncorrelated
    pane input: a question, a reply, or a cancellation that does not name a
    mission this control root actually holds active for that exact worker and
    queue item is refused before any candidate event exists.
    """

    slot = state.missions.get(mission_id)
    if slot is None:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, which this control root has never "
            "materialized"
        )
    lifecycle = slot["lifecycle"]
    if lifecycle["worker_id"] != worker_id or lifecycle["queue_item_id"] != queue_item_id:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, which belongs to a different worker "
            "or queue item"
        )
    if lifecycle["state"] not in WORKER_LIFECYCLE_ACTIVE_STATES:
        raise ControlStoreError(
            f"{label} names mission {mission_id}, whose materialized state is "
            f"{lifecycle['state']!r} and is no longer active"
        )
    return dict(lifecycle)


def require_pending_prompt(
    state: MissionState,
    answers_command_id: str,
    kind: str,
    label: str,
) -> Dict[str, Any]:
    """Require one pending prompt of the matching kind, failing closed.

    A reply is only ever the resolution of a question this control root is
    actually holding open.  An unknown command, a command that is not a prompt,
    a prompt of the other kind, and an already-answered prompt are each refused
    before any candidate event exists, so an answer can never arrive from
    outside the protocol or resolve something twice.
    """

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
            f"{label} of kind {kind!r} may only answer a "
            f"{MISSION_DIALOG_ANSWERS[kind]!r}; command {answers_command_id} is a "
            f"{prompt['kind']!r}"
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
    """Deliver one managed mission command with its correlated record atomically.

    The envelope and its record are committed as one immutable event through the
    ordinary US2 delivery path, so every managed interaction inherits durable
    command IDs, canonical payload digests, idempotent redelivery, and conflict
    recording instead of growing a second mechanism beside them.  The committed
    fold, not any pre-read, then decides what the record actually moved.
    """

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
    # A redelivery commits only the stored duplicate acknowledgement, so its
    # event carries no mission record at all; that is exactly what idempotent
    # redelivery of a managed interaction has to look like.
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
    """Return the latest acknowledgement a cancellation command actually carries.

    Only an acknowledgement naming the exact digest the command was registered
    with answers for it: a rejection of some other digest is the worker-side
    half of a reuse conflict and says nothing about the cancellation itself.
    """

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
    """Classify one cooperative cancellation as acknowledged, awaited, or timed out.

    The classification is a pure function of committed evidence and one explicit
    as-of moment: nothing here sleeps, polls, or expires anything by itself, so
    two processes evaluating the same store at the same declared moment always
    agree.  An expired deadline is `timed-out`, which is a recoverable
    observation the controller owes a bounded action for; it is deliberately not
    `failed`, and it never becomes a mission state.
    """

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
    """Report every managed dialog, cancellation, and slot without changing a byte.

    The report is derived from committed event files rather than from the
    published projection, so a stale, corrupt, or hand-edited `ledger.json` can
    never answer for a pending question, a cancellation outcome, or which
    mission a worker's one slot holds.
    """

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
        mission_states={
            key: entry["lifecycle"]["state"] for key, entry in state.missions.items()
        },
    )


# --- managed mission dialogs, cancellation, replacement, and recovery ---------

control_mission_control.ControlStoreError = ControlStoreError
control_mission_control.MISSION_CONTROL_SCHEMA_VERSION = MISSION_CONTROL_SCHEMA_VERSION
control_mission_control.MISSION_DIALOG_RECORD_TYPE = MISSION_DIALOG_RECORD_TYPE
control_mission_control.MISSION_CANCELLATION_RECORD_TYPE = MISSION_CANCELLATION_RECORD_TYPE
control_mission_control.MISSION_REPLACEMENT_RECORD_TYPE = MISSION_REPLACEMENT_RECORD_TYPE
control_mission_control.MISSION_RECOVERY_RECORD_TYPE = MISSION_RECOVERY_RECORD_TYPE
control_mission_control.MISSION_DIALOG_PAYLOAD_FIELD = MISSION_DIALOG_PAYLOAD_FIELD
control_mission_control.MISSION_CANCELLATION_PAYLOAD_FIELD = MISSION_CANCELLATION_PAYLOAD_FIELD
control_mission_control.MISSION_REPLACEMENT_PAYLOAD_FIELD = MISSION_REPLACEMENT_PAYLOAD_FIELD
control_mission_control.MISSION_RECOVERY_PAYLOAD_FIELD = MISSION_RECOVERY_PAYLOAD_FIELD
control_mission_control.MISSION_DIALOG_PENDING = MISSION_DIALOG_PENDING
control_mission_control.MISSION_DIALOG_OBSERVATION_ANSWERABLE = (
    MISSION_DIALOG_OBSERVATION_ANSWERABLE
)
control_mission_control.MISSION_DIALOG_OBSERVATION_ORPHANED = MISSION_DIALOG_OBSERVATION_ORPHANED
control_mission_control.MISSION_DIALOG_OBSERVATION_SETTLED = MISSION_DIALOG_OBSERVATION_SETTLED
control_mission_control.MISSION_DIALOG_QUESTION = MISSION_DIALOG_QUESTION
control_mission_control.MISSION_DIALOG_ACCESS_PROMPT = MISSION_DIALOG_ACCESS_PROMPT
control_mission_control.MISSION_DIALOG_PROMPT_KINDS = MISSION_DIALOG_PROMPT_KINDS
control_mission_control.MISSION_DIALOG_ANSWERS = MISSION_DIALOG_ANSWERS
control_mission_control.MISSION_FOLD_RETAINED_DUPLICATE = MISSION_FOLD_RETAINED_DUPLICATE
control_mission_control.CANCELLATION_AWAITING = CANCELLATION_AWAITING
control_mission_control.CANCELLATION_ACKNOWLEDGED = CANCELLATION_ACKNOWLEDGED
control_mission_control.CANCELLATION_TIMED_OUT = CANCELLATION_TIMED_OUT
control_mission_control.CANCELLATION_TIMEOUT_REASON = CANCELLATION_TIMEOUT_REASON
control_mission_control.CANCELLATION_TIMEOUT_RECOVERY = CANCELLATION_TIMEOUT_RECOVERY
control_mission_control.COMMAND_DUPLICATE = COMMAND_DUPLICATE
control_mission_control.DEFAULT_EVENT_ACTOR = DEFAULT_EVENT_ACTOR
control_mission_control.DEFAULT_COMMAND_REGISTER_COMMAND = DEFAULT_COMMAND_REGISTER_COMMAND
control_mission_control.DEFAULT_LOCK_POLL_SECONDS = DEFAULT_LOCK_POLL_SECONDS
control_mission_control.WORKER_LIFECYCLE_ACTIVE_STATES = WORKER_LIFECYCLE_ACTIVE_STATES
control_mission_control.validate_mission_dialog = (
    lambda *args, **kwargs: validate_mission_dialog(*args, **kwargs)
)
control_mission_control.validate_mission_cancellation = (
    lambda *args, **kwargs: validate_mission_cancellation(*args, **kwargs)
)
control_mission_control.validate_mission_replacement = (
    lambda *args, **kwargs: validate_mission_replacement(*args, **kwargs)
)
control_mission_control.validate_mission_recovery = (
    lambda *args, **kwargs: validate_mission_recovery(*args, **kwargs)
)
control_mission_control.inspect_control_events = (
    lambda *args, **kwargs: inspect_control_events(*args, **kwargs)
)
control_mission_control.fold_mission_state = lambda *args, **kwargs: fold_mission_state(*args, **kwargs)
control_mission_control.register_command = lambda *args, **kwargs: register_command(*args, **kwargs)
control_mission_control.fold_commands = lambda *args, **kwargs: fold_commands(*args, **kwargs)
control_mission_control._committed_events_after_publication = (
    lambda *args, **kwargs: _committed_events_after_publication(*args, **kwargs)
)
control_mission_control._parsed_timestamp = (
    lambda *args, **kwargs: _parsed_timestamp(*args, **kwargs)
)
control_mission_control._require_absolute_root = (
    lambda *args, **kwargs: _require_absolute_root(*args, **kwargs)
)
control_mission_control._require_uuid = lambda *args, **kwargs: _require_uuid(*args, **kwargs)
control_mission_control._require_identifier = (
    lambda *args, **kwargs: _require_identifier(*args, **kwargs)
)
control_mission_control.utc_timestamp = lambda: utc_timestamp()

MissionControlResult = control_mission_control.MissionControlResult
CancellationObservation = control_mission_control.CancellationObservation
MissionControlReport = control_mission_control.MissionControlReport
build_mission_dialog = control_mission_control.build_mission_dialog
build_mission_cancellation = control_mission_control.build_mission_cancellation
build_mission_replacement = control_mission_control.build_mission_replacement
build_mission_recovery = control_mission_control.build_mission_recovery
read_mission_state = control_mission_control.read_mission_state
require_active_mission = control_mission_control.require_active_mission
require_pending_prompt = control_mission_control.require_pending_prompt
register_mission_command = control_mission_control.register_mission_command
_cancellation_acknowledgement = control_mission_control._cancellation_acknowledgement
observe_cancellation = control_mission_control.observe_cancellation
observe_mission_control = control_mission_control.observe_mission_control


# --- deterministic controller reconciliation (ADR-014, sections 6 and 11) ----


@dataclass(frozen=True)
class QueueItemObservation:
    """One durable queue item as the controller may read it, never own it."""

    item_id: str
    state: str
    created_at: str

    @property
    def active(self) -> bool:
        return self.state in QUEUE_ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return self.state in QUEUE_TERMINAL_STATES

    @property
    def worker_id(self) -> Optional[str]:
        """Return the worker this product state is implementable by, if any."""

        return CONTROLLER_WORKER_BY_QUEUE_STATE.get(self.state)


@dataclass(frozen=True)
class QueueObservation:
    """Everything precedence rule 2 says about product work at one moment.

    `cockpit-queue` owns this state; the controller only reads it.  Nothing here
    writes a queue file, appends a queue event, or repairs a queue item: a queue
    the controller cannot read is reported as unreadable rather than corrected.
    """

    root: str
    paused: bool
    items: Tuple[QueueItemObservation, ...]

    @property
    def active(self) -> Tuple[QueueItemObservation, ...]:
        return tuple(item for item in self.items if item.active)

    @property
    def queued(self) -> Tuple[QueueItemObservation, ...]:
        return tuple(item for item in self.items if item.state == QUEUE_STATE_QUEUED)


def _queue_item_observation(path: Path, label: str) -> QueueItemObservation:
    """Read and validate one durable queue item, failing closed."""

    record = _require_object(_load_json(path, label), label)
    item_id = _require_identifier(record, "id", label)
    if item_id != path.name[: -len(QUEUE_ITEM_SUFFIX)]:
        raise ControlStoreError(f"{label} declares id {item_id}, which is not its own file name")
    state = _require_string(record, "state", label)
    if state not in QUEUE_STATES:
        raise ControlStoreError(
            f"{label} declares unknown state {state!r}; expected one of "
            f"{', '.join(sorted(QUEUE_STATES))}"
        )
    return QueueItemObservation(
        item_id=item_id,
        state=state,
        created_at=_require_string(record, "created_at", label),
    )


def _queue_paused(root: Path) -> bool:
    """Replay the queue's own event log for the latest explicit human pause.

    A pause is an explicit human stop, which ADR-014 rule 1 puts above every
    other source, so it is read from the queue journal the human actually wrote
    to rather than mirrored into control state that could disagree with it.
    """

    path = root / QUEUE_EVENTS_NAME
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {QUEUE_EVENTS_NAME}: {exc}") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ControlStoreError(
            f"{QUEUE_EVENTS_NAME} must be a regular file, not a symlink or directory"
        )
    paused = False
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise ControlStoreError(
                        f"malformed {QUEUE_EVENTS_NAME}:{number}: {exc}"
                    ) from None
                except RecursionError:
                    raise ControlStoreError(
                        f"malformed {QUEUE_EVENTS_NAME}:{number}: nested too deeply to parse"
                    ) from None
                if not isinstance(record, dict):
                    raise ControlStoreError(
                        f"malformed {QUEUE_EVENTS_NAME}:{number}: not a JSON object"
                    )
                if record.get("type") == QUEUE_PAUSED_EVENT:
                    paused = True
                elif record.get("type") == QUEUE_RESUMED_EVENT:
                    paused = False
    except (OSError, UnicodeDecodeError) as exc:
        raise ControlStoreError(f"cannot read {QUEUE_EVENTS_NAME}: {exc}") from None
    return paused


def observe_queue(root: Path) -> QueueObservation:
    """Read the durable product-work authority without changing one byte of it."""

    root = _require_absolute_root(str(root), "declared", QUEUE_ROOT_VARIABLE)
    _require_directory(root, QUEUE_ROOT_VARIABLE)
    items_path = root / QUEUE_ITEMS_DIR_NAME
    items: List[QueueItemObservation] = []
    try:
        mode: Optional[int] = items_path.lstat().st_mode
    except FileNotFoundError:
        mode = None
    except OSError as exc:
        raise ControlStoreError(f"cannot inspect {QUEUE_ITEMS_DIR_NAME}: {exc}") from None
    if mode is not None:
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ControlStoreError(
                f"{QUEUE_ITEMS_DIR_NAME} must be a directory, not a symlink or file"
            )
        try:
            entries = sorted(items_path.iterdir())
        except OSError as exc:
            raise ControlStoreError(f"cannot read {QUEUE_ITEMS_DIR_NAME}: {exc}") from None
        for entry in entries:
            if not entry.name.endswith(QUEUE_ITEM_SUFFIX):
                continue
            items.append(
                _queue_item_observation(entry, f"{QUEUE_ITEMS_DIR_NAME}/{entry.name}")
            )
    # FIFO order is the queue's, not the controller's: creation order first, then
    # the item identifier, exactly as `cockpit-queue` itself enumerates items.
    items.sort(key=lambda item: (item.created_at, item.item_id))
    return QueueObservation(root=str(root), paused=_queue_paused(root), items=tuple(items))


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
    if CONTROLLER_WORKER_BY_QUEUE_STATE.get(item.state) != observation.worker_id:
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
    the same worker, the same rung, and therefore the same command ID.
    """

    for worker_id, slot in evidence.state.claimed_slots:
        mission_id = slot["mission_id"]
        observation = evidence.observation(mission_id)
        if observation is None or observation.terminal:
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
        return _recovery_action(evidence, worker_id, slot, observation)
    return None


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

    runtime = boundaries.get("runtime_boundaries")
    if isinstance(runtime, list) and detail in runtime:
        return True
    parsed = _typed_reference_parts(detail)
    repo_path = detail
    if parsed is not None:
        kind, value = parsed
        if kind in ("repo", "repository"):
            repo_path = value
    roots = boundaries.get("implementation_roots")
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
        runtime = boundaries.get("runtime_boundaries")
        runtime_refs: Tuple[str, ...] = ()
        if isinstance(runtime, list):
            runtime_refs = tuple(ref for ref in runtime if isinstance(ref, str))
        if category == "repository" and _is_declared_repository_access(detail, boundaries):
            return True
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

    # Rules 3 and 6 again: a mission the control plane still believes is running
    # while its declared freshness has expired, or whose product work has left
    # the active set, is owed exactly one bounded recovery action.  It is never
    # marked failed here or anywhere else -- `stale` is an observation, and only
    # the worker's own lifecycle event or a human decision ends a mission.
    recovery = select_recovery_action(evidence)
    if recovery is not None:
        return recovery

    active = queue.active
    if not active:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_QUEUE_EMPTY,
            {"queue_root": queue.root, "queued": len(queue.queued)},
        )
    if len(active) > 1:
        return _controller_observation_action(
            evidence,
            CONTROLLER_REASON_QUEUE_AMBIGUOUS,
            {"active_items": [item.item_id for item in active]},
            evidence_refs=tuple(f"queue:{item.item_id}" for item in active),
        )

    item = active[0]
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

    for holder, slot in evidence.state.claimed_slots:
        if slot["queue_item_id"] == item.item_id:
            # The freshness the worker has itself declared is part of *which*
            # situation this is, not decoration on it.  Reaching here at all
            # means the mission was fresh at this moment, so a worker that went
            # stale and then came back has necessarily declared a window it had
            # not declared before: including it is what makes "the controller
            # looked and the mission was fresh" a recorded observation rather
            # than one that collapses into an older, expired look and is never
            # written down.  Evidence that does not restore freshness never
            # reaches this branch, so it can never re-key an episode either.
            observed = evidence.observation(slot["mission_id"])
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
        payload_digest=command_payload_digest(
            {
                "command_type": COMMAND_TYPE_MISSION_DISPATCH,
                "mission_id": mission_id,
                "queue_item_id": item.item_id,
                "queue_item_state": item.state,
                "trace_id": trace_id,
                "worker_id": worker_id,
            },
            "controller dispatch payload",
        ),
        evidence_refs=references,
    )


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
        lock: PortableControlLock,
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
            declarations = {
                "active_queue_item_id": action.queue_item_id,
                "active_mission_id": action.replacement_mission_id,
            }
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

        metadata = validate_root_metadata(
            _load_json(self.root / CONTROL_METADATA_NAME, CONTROL_METADATA_NAME), self.root
        )
        roots = metadata["canonical_roots"]
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
            created_at=self.as_of,
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
        publication = publish_control_event(
            self.root,
            f"{CONTROLLER_OBSERVATION_EVENT_PREFIX}{action.outcome}",
            actor=CONTROLLER_TICK_ACTOR,
            payload={CONTROLLER_OBSERVATION_PAYLOAD_FIELD: record},
            command=self.command,
            timeout_seconds=self.timeout_seconds,
            poll_seconds=self.poll_seconds,
            dry_run=self.dry_run,
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
PREFLIGHT_STATUS_OPERATIONALLY_BLOCKED = "operationally-blocked"
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
REPAIR_ACTION_BIND_ROOTS = (
    "cockpit-control bind-roots --queue-root <absolute queue root> "
    "--planning-root <absolute planning root> --implementation-root <absolute implementation root>"
)
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
        return self.status in (
            PREFLIGHT_STATUS_BLOCKED,
            PREFLIGHT_STATUS_OPERATIONALLY_BLOCKED,
        )

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

    def _operationally_blocked(
        self,
        dimension: str,
        detail: str,
        repair: str = REPAIR_ACTION_BIND_ROOTS,
    ) -> None:
        self._add(
            dimension,
            PREFLIGHT_STATUS_OPERATIONALLY_BLOCKED,
            FINDING_CONFIGURATION,
            detail,
            repair,
        )

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
        declared = control_root_schema.declared_future_schema_version(record)
        if declared is not None:
            self._add(
                dimension,
                PREFLIGHT_BLOCKED,
                FINDING_AUTHORITATIVE,
                control_root_schema.metadata_future_schema_diagnostic(
                    declared,
                    metadata_name=CONTROL_METADATA_NAME,
                    supported_version=CONTROL_SCHEMA_VERSION,
                ),
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
                "product-work authority; bind canonical roots before dispatch"
            )
            if exported:
                self._operationally_blocked(
                    dimension,
                    f"{detail}; the shell exports COCKPIT_QUEUE_ROOT={exported}, which "
                    f"{CONTROL_METADATA_NAME} does not declare",
                    f"cockpit-control bind-roots --queue-root {exported} "
                    "--planning-root <absolute planning root> "
                    "--implementation-root <absolute implementation root>",
                )
                return
            self._operationally_blocked(dimension, detail)
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
            self._operationally_blocked(
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
        operationally_blocked = False
        for finding in ordered:
            if finding.state == PREFLIGHT_BLOCKED:
                status = PREFLIGHT_STATUS_BLOCKED
                break
            if finding.state == PREFLIGHT_STATUS_OPERATIONALLY_BLOCKED:
                operationally_blocked = True
            if finding.state != PREFLIGHT_READY:
                status = PREFLIGHT_STATUS_DEGRADED
        if status != PREFLIGHT_STATUS_BLOCKED and operationally_blocked:
            status = PREFLIGHT_STATUS_OPERATIONALLY_BLOCKED
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


def _canonical_roots_record(
    control_root: Path,
    queue_root: Optional[str] = None,
    planning_root: Optional[str] = None,
    implementation_roots: Sequence[str] = (),
) -> Dict[str, Any]:
    """Build one normalized canonical-root declaration."""

    roots = {
        "control_root": str(control_root),
        "queue_root": queue_root,
        "planning_root": planning_root,
        "implementation_roots": list(implementation_roots),
    }
    return _validate_canonical_roots(roots, "canonical root binding", control_root)


def _declared_roots_from_values(
    control_root: Path,
    queue_root: str,
    planning_root: str,
    implementation_roots: Sequence[str],
) -> Dict[str, Any]:
    """Validate a complete operational root binding from CLI arguments."""

    if not implementation_roots:
        raise ControlStoreError("at least one --implementation-root is required")
    roots = _canonical_roots_record(
        control_root,
        queue_root=_require_root_path(queue_root, "queue_root", "root binding"),
        planning_root=_require_root_path(planning_root, "planning_root", "root binding"),
        implementation_roots=[
            _require_root_path(value, f"implementation_roots[{index}]", "root binding")
            for index, value in enumerate(implementation_roots)
        ],
    )
    seen: Dict[str, str] = {}
    for label, value in (
        ("control_root", roots["control_root"]),
        ("queue_root", roots["queue_root"]),
        ("planning_root", roots["planning_root"]),
    ):
        if value in seen:
            raise ControlStoreError(
                f"root binding requires distinct paths; {label} duplicates {seen[value]}"
            )
        seen[value] = label
    for index, value in enumerate(roots["implementation_roots"]):
        label = f"implementation_roots[{index}]"
        if value in seen:
            raise ControlStoreError(
                f"root binding requires distinct paths; {label} duplicates {seen[value]}"
            )
        seen[value] = label
    return roots


def _metadata_with_canonical_roots(
    metadata: Mapping[str, Any],
    roots: Mapping[str, Any],
    *,
    migrated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Return metadata with canonical and compatibility root fields synchronized."""

    updated = dict(metadata)
    canonical = {
        "control_root": roots["control_root"],
        "queue_root": roots["queue_root"],
        "planning_root": roots["planning_root"],
        "implementation_roots": list(roots["implementation_roots"]),
    }
    updated["canonical_roots"] = canonical
    updated["queue_root"] = canonical["queue_root"]
    updated["planning_root"] = canonical["planning_root"]
    updated["implementation_roots"] = list(canonical["implementation_roots"])
    if migrated_at is not None:
        updated["last_migration_at"] = migrated_at
    return updated


def _initial_records(
    root: Path,
    queue_root: Optional[str] = None,
    planning_root: Optional[str] = None,
    implementation_roots: Sequence[str] = (),
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    created_at = utc_timestamp()
    control_id = str(uuid4())
    session_id = _session_identity()
    cockpit_id = os.environ.get("COCKPIT_ID") or session_id
    canonical_roots = _canonical_roots_record(
        root,
        queue_root=queue_root,
        planning_root=planning_root,
        implementation_roots=implementation_roots,
    )
    metadata = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "record_type": "control-root",
        "control_id": control_id,
        "cockpit_id": cockpit_id,
        "session_id": session_id,
        "control_root": str(root),
        "canonical_roots": canonical_roots,
        "queue_root": canonical_roots["queue_root"],
        "planning_root": canonical_roots["planning_root"],
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


def _create_store_atomically(root: Path, roots: Optional[Mapping[str, Any]] = None) -> None:
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
        if roots is None:
            metadata, ledger = _initial_records(root)
        else:
            metadata, ledger = _initial_records(
                root,
                queue_root=roots["queue_root"],
                planning_root=roots["planning_root"],
                implementation_roots=roots["implementation_roots"],
            )
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
    queue_root: Optional[str] = None,
    planning_root: Optional[str] = None,
    implementation_roots: Sequence[str] = (),
) -> StoreInitialization:
    """Create a complete store atomically, or validate an existing one unchanged."""

    resolved = resolved_root or resolve_control_root()
    root = resolved.path
    if any(value is not None for value in (queue_root, planning_root)) or implementation_roots:
        if queue_root is None or planning_root is None or not implementation_roots:
            raise ControlStoreError(
                "init root binding requires --queue-root, --planning-root, and at least "
                "one --implementation-root together"
            )
        roots = _declared_roots_from_values(root, queue_root, planning_root, implementation_roots)
    else:
        roots = _canonical_roots_record(root)
    if root.exists() or root.is_symlink():
        if root.is_symlink() or not root.is_dir():
            raise ControlStoreError("COCKPIT_CONTROL_ROOT must be a directory, not a symlink or file")
        if _root_has_entries(root):
            validate_control_store(root)
            return StoreInitialization(root=root, source=resolved.source, created=False)

    if roots["queue_root"] is not None:
        for label, value in (
            ("queue root", roots["queue_root"]),
            ("planning root", roots["planning_root"]),
        ):
            _ensure_declared_directory(value, label)
        for index, value in enumerate(roots["implementation_roots"]):
            _ensure_declared_directory(value, f"implementation root {index + 1}")
    _create_store_atomically(root, roots if roots["queue_root"] is not None else None)
    # Re-read the just-published files through the normal fail-closed validator.
    validate_control_store(root)
    return StoreInitialization(root=root, source=resolved.source, created=True)


@dataclass(frozen=True)
class RootBindingResult:
    """Outcome of binding canonical work roots into an existing control store."""

    root: Path
    queue_root: str
    planning_root: str
    implementation_roots: Tuple[str, ...]
    changed: bool


def _ensure_declared_directory(path: str, label: str) -> None:
    """Create or validate one declared work root without following symlinks."""

    root = Path(path)
    mode = _existing_mode(root, label)
    if mode is None:
        parent = root.parent
        if not parent.is_dir():
            raise ControlStoreError(f"parent directory does not exist for {label}: {parent}")
        if parent.is_symlink():
            raise ControlStoreError(f"parent directory for {label} must not be a symlink: {parent}")
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ControlStoreError(f"cannot create {label}: {exc}") from None
        _fsync_directory(parent)
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ControlStoreError(f"{label} must be a real directory, not a symlink or file")
    if not os.access(path, os.W_OK):
        raise ControlStoreError(f"{label} is not writable by this user")


def _write_replacement_json(path: Path, record: Mapping[str, Any], label: str) -> None:
    temporary = path.with_name(f".{path.name}.bind-{uuid4()}.tmp")
    try:
        _write_json(temporary, record)
        identity = temporary.lstat()
        os.replace(str(temporary), str(path))
        replaced = path.lstat()
    except OSError as exc:
        raise ControlStoreError(f"cannot atomically replace {label}: {exc}") from None
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    if not _same_filesystem_identity(replaced, identity):
        raise ControlStoreError(f"{label} changed filesystem identity during replacement")
    _fsync_directory(path.parent)


def bind_control_roots(
    root: Path,
    queue_root: str,
    planning_root: str,
    implementation_roots: Sequence[str],
    *,
    command: str = "cockpit-control bind-roots",
    timeout_seconds: Optional[float] = None,
    poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS,
    dry_run: bool = False,
) -> RootBindingResult:
    """Bind complete canonical work roots and refresh the derived ledger."""

    root = _require_absolute_root(str(root), "configured")
    roots = _declared_roots_from_values(root, queue_root, planning_root, implementation_roots)
    metadata = validate_control_authority(root)
    current = metadata["canonical_roots"]
    changed = current != roots
    if dry_run:
        return RootBindingResult(
            root=root,
            queue_root=roots["queue_root"],
            planning_root=roots["planning_root"],
            implementation_roots=tuple(roots["implementation_roots"]),
            changed=changed,
        )

    for label, value in (
        ("queue root", roots["queue_root"]),
        ("planning root", roots["planning_root"]),
    ):
        _ensure_declared_directory(value, label)
    for index, value in enumerate(roots["implementation_roots"]):
        _ensure_declared_directory(value, f"implementation root {index + 1}")

    with PortableControlLock(
        root,
        command,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    ) as lock:
        metadata = validate_control_authority(root)
        changed = metadata["canonical_roots"] != roots
        if changed:
            updated = _metadata_with_canonical_roots(
                metadata,
                roots,
                migrated_at=utc_timestamp(),
            )
            validate_root_metadata(updated, root)
            history = read_committed_events(root, updated["control_id"])
            ledger = build_ledger_projection(updated, history.events)
            _write_replacement_json(root / CONTROL_METADATA_NAME, updated, CONTROL_METADATA_NAME)
            _write_replacement_json(root / LEDGER_NAME, ledger, LEDGER_NAME)
            view_identity = _write_projection_temporary(
                root / EVENTS_VIEW_TEMPORARY_NAME,
                build_events_view(history.events),
                EVENTS_VIEW_TEMPORARY_NAME,
            )
            _replace_projection(
                root / EVENTS_VIEW_TEMPORARY_NAME,
                root / EVENTS_NAME,
                view_identity,
                EVENTS_NAME,
            )
        else:
            ControlLedgerProjection(root, command=command, held_lock=lock).run()
    validate_control_store(root)
    return RootBindingResult(
        root=root,
        queue_root=roots["queue_root"],
        planning_root=roots["planning_root"],
        implementation_roots=tuple(roots["implementation_roots"]),
        changed=changed,
    )


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


def _report_lifecycle_record(result: LifecycleRecordResult) -> int:
    """Print the publication outcome and, separately, the materialization one."""

    publication = result.publication
    location = f"{EVENTS_DIR_NAME}/{publication.path.name}"
    if publication.committed:
        print(f"cockpit-control: committed {location} at revision {publication.revision}")
    else:
        print(
            f"cockpit-control: would commit {location} at revision {publication.revision}; "
            "no state changed"
        )
    if publication.projection is not None:
        print(
            f"cockpit-control: projected {LEDGER_NAME} at revision "
            f"{publication.projection.revision}"
        )

    lifecycle = result.lifecycle
    summary = (
        f"worker-lifecycle {lifecycle['state']} sequence {lifecycle['sequence']} "
        f"for mission {lifecycle['mission_id']} worker {lifecycle['worker_id']} "
        f"trace {lifecycle['trace_id']}"
    )
    if result.applied:
        verb = "advanced" if publication.committed else "would advance"
        print(f"cockpit-control: {summary} {verb} the materialized mission state")
    else:
        materialized = result.materialized
        detail = "no materialized mission state exists"
        if materialized is not None:
            detail = (
                f"materialized state is {materialized['lifecycle']['state']} at sequence "
                f"{materialized['lifecycle']['sequence']}"
            )
        verb = "is retained" if publication.committed else "would be retained"
        print(
            f"cockpit-control: {summary} {verb} for audit only "
            f"({result.outcome}; {detail})"
        )
        if publication.committed:
            print(
                f"cockpit-control: {location} is durable audit evidence that did not change "
                f"the materialized mission state ({result.outcome})",
                file=os.sys.stderr,
            )
        if result.outcome == LIFECYCLE_RETAINED_INVALID_TRANSITION and materialized is not None:
            # An operator following a repair sentence deserves to be told which
            # states the section 9 table actually allows next, rather than only
            # that this one changed nothing.
            current = materialized["lifecycle"]["state"]
            allowed = ", ".join(WORKER_LIFECYCLE_TRANSITIONS.get(current, ())) or "no state"
            print(
                f"cockpit-control: {lifecycle['state']} is not reachable from {current}; "
                f"this mission can record {allowed} next",
                file=os.sys.stderr,
            )
    if publication.projection is not None:
        _report_projection_debris(publication.projection.interrupted_temporaries)
    _report_pending_debris(publication.pending_debris)
    return 0


def _report_lifecycle_status(
    root: Path,
    observations: Sequence[WorkerLifecycleObservation],
    as_of: str,
) -> int:
    """Print one freshness observation per materialized mission and succeed.

    A stale worker is an observation, not a verdict, so this command always
    exits 0 and says explicitly on stderr that expiry is recoverable and is not
    a mission failure.
    """

    stale = [observation for observation in observations if observation.stale]
    print(
        f"cockpit-control: {len(observations)} worker mission(s) in {root} as of {as_of}; "
        f"{len(stale)} stale observation(s)"
    )
    for observation in observations:
        print(
            f"{observation.mission_id} worker {observation.worker_id} "
            f"queue-item {observation.queue_item_id} trace {observation.trace_id} "
            f"state {observation.state} sequence {observation.sequence} "
            f"observation {observation.observation} "
            f"reason {observation.reason or '-'} "
            f"recovery {observation.recovery or '-'} "
            f"fresh-until {observation.fresh_until or '-'}"
        )
    for observation in stale:
        print(
            f"cockpit-control: mission {observation.mission_id} on {observation.worker_id} is a "
            f"stale {observation.state} observation ({observation.reason}); this is recoverable "
            "and is not a mission failure, and it awaits a bounded recovery action",
            file=os.sys.stderr,
        )
    return 0


def _parsed_sequence(value: str) -> int:
    """Parse an explicit lifecycle sequence without accepting a float or sign."""

    text = value.strip() if isinstance(value, str) else ""
    if not text.isascii() or not text.isdigit():
        raise ControlStoreError("worker lifecycle requires positive integer sequence")
    # CPython refuses to convert an absurdly long digit string at all, so the
    # refusal is stated as a diagnostic instead of escaping as a ValueError
    # traceback that would leak internal paths.
    try:
        return int(text)
    except ValueError:
        raise ControlStoreError(
            "worker lifecycle requires a sequence within the representable integer range"
        ) from None


def _parsed_freshness_seconds(value: str) -> float:
    """Parse an explicit freshness window in seconds."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ControlStoreError("--fresh-for must be a positive number of seconds") from None
    return _validated_seconds(parsed, "--fresh-for", allow_zero=False)


def _lifecycle_from_arguments(args: Any) -> Dict[str, Any]:
    """Build one complete lifecycle record from explicit command arguments.

    Nothing is guessed.  An active state must be given an explicit freshness
    window, because inventing a default heartbeat timeout would be a policy the
    control plane has not been told.
    """

    if args.fresh_until is not None and args.fresh_for is not None:
        raise ControlStoreError("--fresh-until and --fresh-for cannot be combined")
    heartbeat_at = args.heartbeat_at
    fresh_until = args.fresh_until
    if args.state in WORKER_LIFECYCLE_TERMINAL_STATES:
        for option, value in (
            ("--heartbeat-at", heartbeat_at),
            ("--fresh-until", fresh_until),
            ("--fresh-for", args.fresh_for),
        ):
            if value is not None:
                raise ControlStoreError(
                    f"{option} cannot be given for terminal state {args.state!r}; a terminal "
                    "mission has no freshness to refresh"
                )
    if args.state in WORKER_LIFECYCLE_ACTIVE_STATES:
        if heartbeat_at is None:
            heartbeat_at = utc_timestamp()
        if fresh_until is None:
            if args.fresh_for is None:
                raise ControlStoreError(
                    f"state {args.state!r} requires an explicit freshness deadline; "
                    "pass --fresh-until or --fresh-for"
                )
            fresh_until = _shifted_timestamp(
                heartbeat_at, _parsed_freshness_seconds(args.fresh_for)
            )
    blocker: Optional[Dict[str, Any]] = None
    if args.blocker_category is not None:
        blocker = {"category": args.blocker_category, "detail": args.blocker_detail}
    elif args.blocker_detail is not None:
        raise ControlStoreError("--blocker-detail requires --blocker-category")
    elif args.state == LIFECYCLE_BLOCKED:
        raise ControlStoreError(
            f"state {LIFECYCLE_BLOCKED!r} requires a categorized blocker; pass "
            "--blocker-category"
        )
    return build_worker_lifecycle(
        state=args.state,
        worker_id=args.worker,
        mission_id=args.mission,
        queue_item_id=args.queue_item,
        trace_id=args.trace,
        sequence=_parsed_sequence(args.sequence),
        parent_trace_id=args.parent_trace,
        reason=args.reason,
        blocker=blocker,
        heartbeat_at=heartbeat_at,
        fresh_until=fresh_until,
        evidence_refs=tuple(args.evidence or ()),
        superseded_by_mission_id=args.superseded_by,
    )


def _parsed_payload(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Parse an explicit payload argument as one JSON object of metadata."""

    if value is None:
        return None
    # Clause order is load-bearing: `json.JSONDecodeError` subclasses
    # `ValueError`, so the precise decode diagnostic must be matched before the
    # broad guard below.
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ControlStoreError(f"event payload is not valid JSON: {exc}") from None
    except RecursionError:
        raise ControlStoreError("event payload is nested too deeply to parse") from None
    except ValueError as exc:
        # CPython refuses to convert an integer literal longer than its 4300
        # digit `int` conversion limit and raises a bare `ValueError` rather
        # than a `JSONDecodeError`, which would otherwise escape as a traceback
        # naming internal filesystem paths instead of a fail-closed diagnostic.
        raise ControlStoreError(f"event payload is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ControlStoreError("event payload must be a JSON object of structured metadata")
    return parsed


def _command_summary_line(slot: Mapping[str, Any]) -> str:
    """Render one materialized command as one machine-readable record.

    Every value rendered here is a UUID, a digest, an integer, a UTC timestamp,
    or an identifier constrained to printable non-whitespace ASCII, so no
    caller- or worker-supplied string can forge a field or record boundary.
    Free-text `reason` values are deliberately absent from this payload.
    """

    envelope = slot["envelope"]
    return (
        f"command {envelope['command_id']} type {envelope['command_type']} "
        f"mission {envelope['mission_id']} queue-item {envelope['queue_item_id']} "
        f"target {envelope['target']['kind']}/{envelope['target']['id']} "
        f"trace {envelope['trace_id']} "
        f"parent-trace {envelope['parent_trace_id'] or '-'} "
        f"digest {envelope['payload_digest']} "
        f"schema-version {envelope['schema_version']} "
        f"status {slot['status']} "
        f"deliveries {len(slot['deliveries'])} "
        f"acknowledgements {len(slot['acknowledgements'])} "
        f"conflicts {len(slot['conflicts'])} "
        f"created {envelope['created_at']} "
        f"deadline {envelope['deadline_at'] or '-'}"
    )


def _command_boundary_line(envelope: Mapping[str, Any]) -> str:
    """Render the declared mission boundaries a command carries.

    Declared roots are reported as declared or unassigned rather than printed:
    a filesystem path may legitimately contain spaces, and this payload is
    field-delimited.  The exact roots stay in the JSON records, which no
    delimiter can confuse.
    """

    boundaries = envelope["boundaries"]
    return (
        f"boundaries {envelope['command_id']} control-root declared "
        f"queue-root {'declared' if boundaries['queue_root'] else '-'} "
        f"planning-root {'declared' if boundaries['planning_root'] else '-'} "
        f"implementation-roots {len(boundaries['implementation_roots'])} "
        f"runtime {','.join(boundaries['runtime_boundaries']) or '-'}"
    )


def _command_acknowledgement_line(command_id: str, entry: Mapping[str, Any]) -> str:
    """Render one durable acknowledgement of a command."""

    acknowledgement = entry["acknowledgement"]
    return (
        f"acknowledgement {command_id} outcome {acknowledgement['outcome']} "
        f"by {acknowledgement['acknowledged_by']} "
        f"digest {acknowledgement['payload_digest']} "
        f"revision {entry['revision']} at {acknowledgement['acknowledged_at']} "
        f"result-refs {','.join(acknowledgement['result_refs']) or '-'}"
    )


def _command_conflict_line(command_id: str, conflict: Mapping[str, Any]) -> str:
    """Render one durable conflict recorded against a command."""

    return (
        f"conflict {command_id} reason {conflict['reason']} "
        f"source {conflict['source']} digest {conflict['payload_digest']} "
        f"revision {conflict['revision']} at {conflict['recorded_at']}"
    )


def _command_slot_lines(slot: Mapping[str, Any]) -> List[str]:
    """Render one command, its boundaries, acknowledgements, and conflicts."""

    command_id = slot["envelope"]["command_id"]
    lines = [_command_summary_line(slot), _command_boundary_line(slot["envelope"])]
    for entry in slot["acknowledgements"]:
        lines.append(_command_acknowledgement_line(command_id, entry))
    for conflict in slot["conflicts"]:
        lines.append(_command_conflict_line(command_id, conflict))
    return lines


def _recorded_conflict(
    slot: Optional[Mapping[str, Any]],
    event_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the conflict one published event recorded, if it recorded one."""

    if slot is None:
        return None
    for conflict in slot["conflicts"]:
        if conflict["event_id"] == event_id:
            return dict(conflict)
    return None


def _report_command_record(result: CommandRecordResult) -> int:
    """Print the publication outcome and, separately, the protocol one.

    Publication and materialization are separate answers exactly as they are for
    lifecycle events: a redelivery, a conflicting reuse, and a losing concurrent
    delivery are all committed as durable evidence, and only the deterministic
    fold decides whether any of them moved the command state.
    """

    publication = result.publication
    location = f"{EVENTS_DIR_NAME}/{publication.path.name}"
    if publication.committed:
        print(f"cockpit-control: committed {location} at revision {publication.revision}")
    else:
        print(
            f"cockpit-control: would commit {location} at revision {publication.revision}; "
            "no state changed"
        )
    if publication.projection is not None:
        print(
            f"cockpit-control: projected {LEDGER_NAME} at revision "
            f"{publication.projection.revision}"
        )

    command_id = result.command_id
    materialized = result.materialized
    status = "-" if materialized is None else materialized["status"]
    verb = "is" if publication.committed else "would be"
    exit_code = 0
    if result.outcome == COMMAND_FOLD_REGISTERED:
        print(
            f"cockpit-control: command {command_id} {verb} registered with digest "
            f"{result.envelope['payload_digest']} (status {status})"
        )
    elif result.outcome == COMMAND_FOLD_ACKNOWLEDGED:
        acknowledgement = result.acknowledgement or {}
        if acknowledgement.get("outcome") == COMMAND_DUPLICATE:
            print(
                f"cockpit-control: command {command_id} {verb} acknowledged duplicate; "
                f"the stored result is returned without applying it again (status {status})"
            )
        else:
            print(
                f"cockpit-control: command {command_id} {verb} acknowledged "
                f"{acknowledgement.get('outcome')} by "
                f"{acknowledgement.get('acknowledged_by')} (status {status})"
            )
    elif result.conflicted:
        conflict = _recorded_conflict(materialized, publication.event_id) or {}
        reason = conflict.get("reason", COMMAND_CONFLICT_DIGEST)
        print(
            f"cockpit-control: command {command_id} {verb} refused as a {reason}; "
            f"the conflict {verb} recorded (status {status})"
        )
        stored = "-" if materialized is None else materialized["envelope"]["payload_digest"]
        print(
            f"cockpit-control: command {command_id} {verb} refused as a {reason}: "
            f"{COMMAND_CONFLICT_EXPLANATIONS.get(reason, reason)}; the stored command keeps "
            f"digest {stored} and the offered delivery declared "
            f"{conflict.get('payload_digest', '-')}",
            file=os.sys.stderr,
        )
        exit_code = 1
    elif result.outcome == COMMAND_FOLD_RETAINED_DUPLICATE:
        print(
            f"cockpit-control: command {command_id} {verb} a duplicate delivery retained "
            f"for audit only; the stored result is returned without applying it again "
            f"(status {status})"
        )
    else:
        print(
            f"cockpit-control: command {command_id} {verb} retained for audit only "
            f"({result.outcome}; status {status})"
        )
        if publication.committed:
            print(
                f"cockpit-control: {location} is durable audit evidence that did not change "
                f"the materialized command state ({result.outcome})",
                file=os.sys.stderr,
            )
        exit_code = 1

    if materialized is not None:
        for line in _command_slot_lines(materialized):
            print(line)
    if publication.projection is not None:
        _report_projection_debris(publication.projection.interrupted_temporaries)
    _report_pending_debris(publication.pending_debris)
    return exit_code


def _report_command_status(root: Path, observations: Sequence[Mapping[str, Any]]) -> int:
    """Print every materialized command and succeed.

    A recorded conflict is durable evidence about a rejected delivery, not a
    failure of the stored command, so this read-only query always exits 0 and
    names the conflicts on stderr.
    """

    conflicted = [slot for slot in observations if slot["conflicts"]]
    print(
        f"cockpit-control: {len(observations)} command(s) in {root}; "
        f"{len(conflicted)} with recorded conflict(s)"
    )
    for slot in observations:
        for line in _command_slot_lines(slot):
            print(line)
    for slot in conflicted:
        envelope = slot["envelope"]
        print(
            f"cockpit-control: command {envelope['command_id']} has "
            f"{len(slot['conflicts'])} recorded conflict(s); the stored command keeps "
            f"digest {envelope['payload_digest']} and status {slot['status']}",
            file=os.sys.stderr,
        )
    return 0


def _dialog_observation(state: MissionState, entry: Mapping[str, Any]) -> str:
    """Classify one dialog against the mission state folded in the same pass."""

    if entry["state"] != MISSION_DIALOG_PENDING:
        return MISSION_DIALOG_OBSERVATION_SETTLED
    mission = state.missions.get(entry["dialog"]["mission_id"])
    if mission is not None and mission["lifecycle"]["state"] in WORKER_LIFECYCLE_ACTIVE_STATES:
        return MISSION_DIALOG_OBSERVATION_ANSWERABLE
    return MISSION_DIALOG_OBSERVATION_ORPHANED


def _mission_dialog_line(
    entry: Mapping[str, Any],
    observation: str = MISSION_DIALOG_OBSERVATION_SETTLED,
) -> str:
    """Render one materialized mission dialog as one machine-readable record.

    Every value rendered here is a UUID, an integer, a UTC timestamp, a closed
    vocabulary word, an identifier constrained to printable non-whitespace
    ASCII, or a whitespace-free typed reference, so no worker- or
    operator-supplied string can forge a field or record boundary.  The question
    and answer bodies are not merely omitted from this payload: they are never
    persisted anywhere, so there is nothing here to leak.
    """

    dialog = entry["dialog"]
    return (
        f"dialog {dialog['command_id']} kind {dialog['kind']} state {entry['state']} "
        f"mission {dialog['mission_id']} worker {dialog['worker_id']} "
        f"queue-item {dialog['queue_item_id']} trace {dialog['trace_id']} "
        f"parent-trace {dialog['parent_trace_id'] or '-'} "
        f"category {dialog['category']} "
        f"answers {dialog['answers_command_id'] or '-'} "
        f"answered-by {entry['answered_by_command_id'] or '-'} "
        f"answered-at {entry['answered_at'] or '-'} "
        f"raised {dialog['raised_at']} "
        f"body-refs {','.join(dialog['body_refs']) or '-'} "
        f"schema-version {dialog['schema_version']} "
        f"revision {entry['revision']} "
        f"observation {observation}"
    )


def _mission_cancellation_request_line(entry: Mapping[str, Any]) -> str:
    """Render one committed cooperative cancellation request."""

    cancellation = entry["cancellation"]
    return (
        f"cancellation-request {cancellation['command_id']} "
        f"mission {cancellation['mission_id']} worker {cancellation['worker_id']} "
        f"queue-item {cancellation['queue_item_id']} trace {cancellation['trace_id']} "
        f"parent-trace {cancellation['parent_trace_id'] or '-'} "
        f"requested {cancellation['requested_at']} "
        f"deadline {cancellation['acknowledge_deadline_at']} "
        f"evidence-refs {','.join(cancellation['evidence_refs']) or '-'} "
        f"schema-version {cancellation['schema_version']} "
        f"revision {entry['revision']}"
    )


def _mission_cancellation_line(observation: CancellationObservation) -> str:
    """Render one cancellation observation evaluated at one explicit moment."""

    return (
        f"cancellation {observation.command_id} mission {observation.mission_id} "
        f"worker {observation.worker_id} queue-item {observation.queue_item_id} "
        f"trace {observation.trace_id} "
        f"parent-trace {observation.parent_trace_id or '-'} "
        f"requested {observation.requested_at} "
        f"deadline {observation.acknowledge_deadline_at} "
        f"observation {observation.observation} "
        f"reason {observation.reason or '-'} "
        f"recovery {observation.recovery or '-'} "
        f"acknowledgement {observation.acknowledgement or '-'} "
        f"by {observation.acknowledged_by or '-'} "
        f"at {observation.acknowledged_at or '-'} "
        f"command-status {observation.command_status} "
        f"mission-state {observation.mission_state or '-'} "
        f"revision {observation.revision} as-of {observation.as_of}"
    )


def _mission_slot_line(worker_id: str, slot: Mapping[str, Any]) -> str:
    """Render the one mission slot a worker holds."""

    return (
        f"slot {worker_id} mission {slot['mission_id'] or '-'} "
        f"queue-item {slot['queue_item_id'] or '-'} state {slot['state']} "
        f"command {slot['command_id'] or '-'} replaces {slot['replaces'] or '-'} "
        f"conflicts {len(slot['conflicts'])} revision {slot['revision']} "
        f"at {slot['recorded_at']}"
    )


def _mission_conflict_line(worker_id: str, conflict: Mapping[str, Any]) -> str:
    """Render one durable conflict recorded against a worker's mission slot."""

    return (
        f"mission-conflict {worker_id} mission {conflict['mission_id']} "
        f"reason {conflict['reason']} source {conflict['source']} "
        f"command {conflict['command_id'] or '-'} revision {conflict['revision']} "
        f"at {conflict['recorded_at']}"
    )


def _mission_record_summary(result: MissionControlResult) -> str:
    """Describe the managed mission operation one command carried."""

    record = result.record
    if result.record_field == MISSION_DIALOG_PAYLOAD_FIELD:
        return (
            f"mission dialog {record['kind']} {record['command_id']} for mission "
            f"{record['mission_id']} worker {record['worker_id']} "
            f"trace {record['trace_id']}"
        )
    if result.record_field == MISSION_CANCELLATION_PAYLOAD_FIELD:
        return (
            f"mission cancellation {record['command_id']} for mission "
            f"{record['mission_id']} worker {record['worker_id']} "
            f"deadline {record['acknowledge_deadline_at']}"
        )
    if result.record_field == CONTROLLER_DISPATCH_PAYLOAD_FIELD:
        return (
            f"controller dispatch {record['command_id']} of mission "
            f"{record['mission_id']} to worker {record['worker_id']} for queue item "
            f"{record['queue_item_id']}"
        )
    return (
        f"mission replacement {record['command_id']} replacing mission "
        f"{record['replaced_mission_id']} with {record['replacement_mission_id']} "
        f"on worker {record['worker_id']}"
    )


def _mission_control_lines(result: MissionControlResult) -> List[str]:
    """Render whatever the managed operation materialized, in a stable order."""

    lines: List[str] = []
    record = result.record
    if result.record_field == MISSION_DIALOG_PAYLOAD_FIELD:
        for key in (record["answers_command_id"], record["command_id"]):
            entry = result.state.dialogs.get(key) if key is not None else None
            if entry is not None:
                lines.append(
                    _mission_dialog_line(entry, _dialog_observation(result.state, entry))
                )
    elif result.record_field == MISSION_CANCELLATION_PAYLOAD_FIELD:
        entry = result.state.cancellations.get(record["command_id"])
        if entry is not None:
            lines.append(_mission_cancellation_request_line(entry))
    slot = result.worker_slot
    if slot is not None:
        lines.append(_mission_slot_line(record["worker_id"], slot))
        for conflict in slot["conflicts"]:
            if conflict["event_id"] == result.command.publication.event_id:
                lines.append(_mission_conflict_line(record["worker_id"], conflict))
    return lines


def _report_mission_control(result: MissionControlResult) -> int:
    """Print the delivery outcome and, separately, the mission-correlation one.

    Delivery and correlation are separate answers exactly as publication and
    materialization are for lifecycle and command events: an idempotent
    redelivery and a refused second active slot are both durably committed, and
    only the deterministic fold decides whether either moved mission state.
    """

    exit_code = _report_command_record(result.command)
    summary = _mission_record_summary(result)
    verb = "is" if result.committed else "would be"
    if result.applied:
        print(f"cockpit-control: {summary} {verb} recorded ({result.outcome})")
    elif result.command.conflicted:
        # A refused envelope commits no mission record at all, so this is not a
        # redelivery of anything: the authoritative refusal is the command
        # conflict reported immediately above and on stderr.
        print(
            f"cockpit-control: {summary} {verb} not applied because its command envelope "
            "was refused as a durable conflict; no mission record was stored"
        )
    elif result.redelivered:
        print(
            f"cockpit-control: {summary} {verb} a redelivery; the stored mission record "
            "is returned without applying it again"
        )
    elif result.conflicted:
        print(
            f"cockpit-control: {summary} {verb} refused; the mission slot conflict "
            f"{verb} recorded ({result.outcome})"
        )
    else:
        print(
            f"cockpit-control: {summary} {verb} retained for audit only ({result.outcome})"
        )
    for line in _mission_control_lines(result):
        print(line)

    if result.conflicted:
        conflict = result.recorded_conflict or {}
        reason = conflict.get("reason", MISSION_SLOT_CONFLICT_SECOND_ACTIVE)
        explanation = MISSION_SLOT_CONFLICT_EXPLANATIONS.get(
            reason, "worker {worker_id} cannot hold the mission slot it claims"
        ).format(
            worker_id=result.record["worker_id"],
            mission_id=conflict.get("mission_id", "-"),
        )
        print(
            f"cockpit-control: {summary} {verb} refused because {explanation}; "
            "the conflict is recorded and no mission state changed",
            file=os.sys.stderr,
        )
        exit_code = 1
    elif not (result.applied or result.redelivered):
        print(
            f"cockpit-control: {summary} {verb} durable audit evidence that did not change "
            f"the materialized mission state ({result.outcome})",
            file=os.sys.stderr,
        )
        exit_code = 1
    return exit_code


def _report_mission_status(report: MissionControlReport) -> int:
    """Print every managed dialog, cancellation, and mission slot and succeed.

    A pending question, a timed-out cancellation, and a recorded slot conflict
    are all durable observations rather than verdicts, so this read-only query
    always exits 0 and names each of them on stderr.
    """

    pending = report.pending_prompts
    orphaned = report.orphaned_prompts
    timed_out = report.timed_out
    conflicts = report.conflicts
    print(
        f"cockpit-control: {len(report.dialogs)} mission dialog(s), "
        f"{len(report.cancellations)} cancellation(s), "
        f"{len(report.worker_slots)} worker slot(s) in {report.root} "
        f"as of {report.as_of}; {len(pending)} pending prompt(s), "
        f"{len(timed_out)} timed-out cancellation(s), "
        f"{len(conflicts)} recorded conflict(s), "
        f"{len(orphaned)} orphaned prompt(s)"
    )
    for entry in report.dialogs:
        print(_mission_dialog_line(entry, report.dialog_observation(entry)))
    for observation in report.cancellations:
        print(_mission_cancellation_line(observation))
    for worker_id, slot in report.worker_slots:
        print(_mission_slot_line(worker_id, slot))
        for conflict in slot["conflicts"]:
            print(_mission_conflict_line(worker_id, conflict))
    for entry in pending:
        dialog = entry["dialog"]
        print(
            f"cockpit-control: mission {dialog['mission_id']} on {dialog['worker_id']} has a "
            f"pending {dialog['kind']} ({dialog['command_id']}); it is answerable only "
            f"through {DEFAULT_MISSION_ANSWER_COMMAND}",
            file=os.sys.stderr,
        )
    for entry in orphaned:
        dialog = entry["dialog"]
        print(
            f"cockpit-control: mission {dialog['mission_id']} on {dialog['worker_id']} has an "
            f"orphaned {dialog['kind']} ({dialog['command_id']}) "
            f"({MISSION_DIALOG_ORPHAN_REASON}); it is not answerable through "
            f"{DEFAULT_MISSION_ANSWER_COMMAND} and awaits a bounded recovery action",
            file=os.sys.stderr,
        )
    for observation in timed_out:
        print(
            f"cockpit-control: cancellation {observation.command_id} of mission "
            f"{observation.mission_id} on {observation.worker_id} is a timed-out "
            f"observation ({observation.reason}); this is recoverable and is not a "
            "mission failure, and it awaits a bounded recovery action",
            file=os.sys.stderr,
        )
    for worker_id, conflict in conflicts:
        print(
            f"cockpit-control: worker {worker_id} has a recorded {conflict['reason']} "
            f"from {conflict['source']} at revision {conflict['revision']}; its single "
            "mission slot was never given a second active mission",
            file=os.sys.stderr,
        )
    return 0


# --- managed queue/tmux/rendering adapter seams ------------------------------

control_queue_adapter.bind_facade(globals())
QueueItemObservation = control_queue_adapter.QueueItemObservation
QueueObservation = control_queue_adapter.QueueObservation
_queue_item_observation = control_queue_adapter._queue_item_observation
_queue_paused = control_queue_adapter._queue_paused
observe_queue = control_queue_adapter.observe_queue

control_tmux_adapter.bind_facade(globals())
_session_identity = control_tmux_adapter.session_identity

control_rendering.bind_facade(globals())
_print_error = control_rendering._print_error
_report_lock_repair = control_rendering._report_lock_repair
_report_preflight = control_rendering._report_preflight
_report_store_repair = control_rendering._report_store_repair
_report_pending_debris = control_rendering._report_pending_debris
_report_projection_debris = control_rendering._report_projection_debris
_report_ledger_projection = control_rendering._report_ledger_projection
_report_event_publication = control_rendering._report_event_publication
_report_event_history = control_rendering._report_event_history
_report_lifecycle_record = control_rendering._report_lifecycle_record
_report_lifecycle_status = control_rendering._report_lifecycle_status
_report_command_record = control_rendering._report_command_record
_report_command_status = control_rendering._report_command_status
_report_mission_control = control_rendering._report_mission_control
_report_mission_status = control_rendering._report_mission_status


# --- managed controller reconciliation seam ----------------------------------

control_controller.bind_facade(globals())
control_controller.ControlStoreError = ControlStoreError

ControllerDiagnostic = control_controller.ControllerDiagnostic
ControllerEvidence = control_controller.ControllerEvidence
ControllerAction = control_controller.ControllerAction
ControllerRecoveryStep = control_controller.ControllerRecoveryStep
ControllerTickResult = control_controller.ControllerTickResult
ControllerJournalObservation = control_controller.ControllerJournalObservation
ControllerTick = control_controller.ControllerTick
controller_state_key = control_controller.controller_state_key
controller_ledger_plan = control_controller.controller_ledger_plan
_controller_derived_uuid = control_controller._controller_derived_uuid
_controller_observation_action = control_controller._controller_observation_action
controller_recovery_ladder = control_controller.controller_recovery_ladder
select_recovery_step = control_controller.select_recovery_step
select_recovery_action = control_controller.select_recovery_action
_mission_boundaries = control_controller._mission_boundaries
_mission_slot_command_id = control_controller._mission_slot_command_id
select_controller_action = control_controller.select_controller_action
build_controller_dispatch = control_controller.build_controller_dispatch
build_controller_observation = control_controller.build_controller_observation
_controller_dispatched_pairs = control_controller._controller_dispatched_pairs
_correlation_counts = control_controller._correlation_counts
controller_tick = control_controller.controller_tick
_precedence_verdicts = control_controller._precedence_verdicts
controller_precedence_lines = control_controller.controller_precedence_lines
controller_tick_lines = control_controller.controller_tick_lines
_contested_duplicate = control_controller._contested_duplicate
worker_lifecycle_cancellation_route = control_controller.worker_lifecycle_cancellation_route
controller_duplicate_repair_route = control_controller.controller_duplicate_repair_route
controller_blocking_repair = control_controller.controller_blocking_repair
report_controller_tick = control_controller.report_controller_tick

# --- managed wake lease/scheduling seam --------------------------------------

control_wake.bind_facade(globals())
control_wake.ControlStoreError = ControlStoreError

WakeIntent = control_wake.WakeIntent
WAKE_LEASE_DIR = control_wake.WAKE_LEASE_DIR
WAKE_LEASE_RECOVERED_DIR = control_wake.WAKE_LEASE_RECOVERED_DIR
WAKE_LEASE_RELEASED_DIR = control_wake.WAKE_LEASE_RELEASED_DIR
WAKE_LEASE_FILE = control_wake.WAKE_LEASE_FILE
WAKE_LEASE_TTL_SECONDS = control_wake.WAKE_LEASE_TTL_SECONDS
WAKE_CRON_TAG = control_wake.CRON_TAG
build_wake_intent = control_wake.build_wake_intent
wake_intent_to_dict = control_wake.wake_intent_to_dict
guard_wake_fire = control_wake.guard_wake_fire
acquire_tick_lease = control_wake.acquire_tick_lease
release_tick_lease = control_wake.release_tick_lease
run_wake_controller_tick = control_wake.run_wake_controller_tick


def _parsed_command_payload(value: str) -> Dict[str, Any]:
    """Parse the structured payload a command digest is computed over."""

    # Clause order is load-bearing: `json.JSONDecodeError` subclasses
    # `ValueError`, so the precise decode diagnostic must be matched before the
    # broad guard below.
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ControlStoreError(f"command payload is not valid JSON: {exc}") from None
    except RecursionError:
        raise ControlStoreError("command payload is nested too deeply to parse") from None
    except ValueError as exc:
        # CPython refuses to convert an integer literal longer than its 4300
        # digit `int` conversion limit and raises a bare `ValueError` rather
        # than a `JSONDecodeError`, which would otherwise escape as a traceback
        # naming internal filesystem paths instead of a fail-closed diagnostic.
        # A caller that needs such a payload digests it itself and passes
        # `--digest`, which is a pure string and never converted to a number.
        raise ControlStoreError(f"command payload is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ControlStoreError("command payload must be a JSON object of structured metadata")
    return parsed


def _command_digest_from_arguments(args: Any) -> str:
    """Return the payload digest one invocation declares, refusing ambiguity.

    A caller either hands over the payload, which is digested here so every
    process agrees, or hands over a digest it already computed.  Doing both
    would be two sources of truth for one value, so it is refused.
    """

    if args.payload is not None and args.digest is not None:
        raise ControlStoreError("--payload and --digest cannot be combined")
    if args.payload is not None:
        return command_payload_digest(_parsed_command_payload(args.payload))
    if args.digest is not None:
        return _require_digest({"payload_digest": args.digest}, "payload_digest", "command")
    raise ControlStoreError(
        "a command payload digest is required; pass --payload or --digest"
    )


def _envelope_from_arguments(args: Any, control_root: Path) -> Dict[str, Any]:
    """Build one complete command envelope from explicit command arguments.

    Nothing is guessed.  The declared control root is always the resolved
    control root this command is being delivered into, so no *caller of this
    CLI* can mint an envelope declaring a boundary around a different store:
    there is deliberately no `--control-root` argument to override it.

    That is a minting guarantee, not a read-back one.  `boundaries.control_root`
    is validated as a normalized absolute path wherever a command envelope is
    read, but it is not cross-checked against the store the record was found
    in, so a hand-written committed event can still declare an unrelated root
    and be read back.  Deciding whether a declared boundary is legitimate for
    the store, the mission, and the actor is ADR-016 boundary enforcement, which
    this story does not own; asserting it here would also make relocating a
    control store turn its immutable committed history into permanently
    unreadable and unrepairable authority, because committed events are never
    rewritten.
    """

    if args.command_type in MANAGED_COMMAND_TYPES:
        raise ControlStoreError(
            f"command type {args.command_type!r} is a managed mission command and cannot be "
            f"delivered through {DEFAULT_COMMAND_REGISTER_COMMAND}; use "
            f"{DEFAULT_MISSION_QUESTION_COMMAND}, {DEFAULT_MISSION_ANSWER_COMMAND}, "
            f"{DEFAULT_MISSION_CANCEL_COMMAND}, or {DEFAULT_MISSION_REPLACE_COMMAND} so it "
            "stays correlated to one active mission"
        )
    return build_command_envelope(
        command_id=args.command_id,
        command_type=args.command_type,
        mission_id=args.mission,
        queue_item_id=args.queue_item,
        target_kind=args.target_kind,
        target_id=args.target,
        trace_id=args.trace,
        payload_digest=_command_digest_from_arguments(args),
        control_root=str(control_root),
        parent_trace_id=args.parent_trace,
        queue_root=args.queue_root,
        planning_root=args.planning_root,
        implementation_roots=tuple(args.implementation_root or ()),
        runtime_boundaries=tuple(args.runtime_boundary or ()),
        deadline_at=args.deadline,
    )


def _parsed_acknowledge_seconds(value: str) -> float:
    """Parse an explicit cancellation acknowledgement window in seconds."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ControlStoreError(
            "--acknowledge-within must be a positive number of seconds"
        ) from None
    return _validated_seconds(parsed, "--acknowledge-within", allow_zero=False)


def _cancellation_deadline(args: Any, requested_at: str) -> str:
    """Return the explicit moment an acknowledgement is expected by.

    Nothing is guessed.  A cooperative cancellation without a declared deadline
    could never be observed as timed out without inventing a policy the control
    plane has not been told, so one of the two explicit forms is required.
    """

    if args.acknowledge_by is not None and args.acknowledge_within is not None:
        raise ControlStoreError(
            "--acknowledge-by and --acknowledge-within cannot be combined"
        )
    if args.acknowledge_by is not None:
        return _require_timestamp(
            {"acknowledge_deadline_at": args.acknowledge_by},
            "acknowledge_deadline_at",
            "mission cancellation",
        )
    if args.acknowledge_within is None:
        raise ControlStoreError(
            "a cooperative cancellation requires an explicit acknowledgement deadline; "
            "pass --acknowledge-by or --acknowledge-within"
        )
    return _shifted_timestamp(
        requested_at,
        _parsed_acknowledge_seconds(args.acknowledge_within),
        field="requested_at",
        label="mission cancellation",
        option="--acknowledge-within",
        noun="cancellation acknowledgement deadline",
    )


def _managed_envelope(
    args: Any,
    control_root: Path,
    command_type: str,
    mission_id: str,
    queue_item_id: str,
    target_id: str,
    trace_id: str,
    parent_trace_id: Optional[str],
) -> Dict[str, Any]:
    """Build the US2 command envelope one managed mission operation delivers.

    The managed operations reuse the whole command contract rather than growing
    a second one: the same durable command ID, the same canonical payload
    digest, the same declared ADR-016 boundaries, and the same idempotent
    redelivery and conflict recording.  Only the command type and the correlated
    record are decided here, and the declared control root is always the store
    the command is delivered into.
    """

    return build_command_envelope(
        command_id=args.command_id,
        command_type=command_type,
        mission_id=mission_id,
        queue_item_id=queue_item_id,
        target_kind=COMMAND_TARGET_WORKER,
        target_id=target_id,
        trace_id=trace_id,
        payload_digest=_command_digest_from_arguments(args),
        control_root=str(control_root),
        parent_trace_id=parent_trace_id,
        queue_root=args.queue_root,
        planning_root=args.planning_root,
        implementation_roots=tuple(args.implementation_root or ()),
        runtime_boundaries=tuple(args.runtime_boundary or ()),
        deadline_at=args.deadline,
    )


def _run_raise_question(root: Path, args: Any) -> MissionControlResult:
    """Raise one mission question or access prompt against the active mission."""

    label = "mission prompt"
    if args.kind not in MISSION_DIALOG_PROMPT_KINDS:
        raise ControlStoreError(
            f"{label} declares unknown kind {args.kind!r}; expected one of "
            f"{', '.join(MISSION_DIALOG_PROMPT_KINDS)}"
        )
    worker_id = _require_identifier({"worker": args.worker}, "worker", label)
    mission_id = _require_uuid({"mission": args.mission}, "mission", label)
    queue_item_id = _require_identifier({"queue-item": args.queue_item}, "queue-item", label)
    state = read_mission_state(root)
    # Admission answers a *new* prompt.  A redelivery of a command ID this
    # control root already recorded is answered by the stored result instead, so
    # retrying an uncertain delivery can never be refused for having succeeded.
    if args.command_id not in state.dialogs:
        require_active_mission(state, mission_id, worker_id, queue_item_id, label)

    dialog = build_mission_dialog(
        command_id=args.command_id,
        kind=args.kind,
        mission_id=mission_id,
        worker_id=worker_id,
        queue_item_id=queue_item_id,
        trace_id=args.trace,
        category=args.category,
        body_refs=tuple(args.body_ref or ()),
        parent_trace_id=args.parent_trace,
        raised_at=args.raised_at,
    )
    envelope = _managed_envelope(
        args,
        root,
        MISSION_DIALOG_COMMAND_TYPES[dialog["kind"]],
        mission_id,
        queue_item_id,
        worker_id,
        dialog["trace_id"],
        dialog["parent_trace_id"],
    )
    return register_mission_command(
        root,
        envelope,
        MISSION_DIALOG_PAYLOAD_FIELD,
        dialog,
        actor=args.actor if args.actor is not None else worker_id,
        command=DEFAULT_MISSION_QUESTION_COMMAND,
        dry_run=args.dry_run,
    )


def _run_answer_question(root: Path, args: Any) -> MissionControlResult:
    """Answer exactly one pending prompt through the protocol and nowhere else."""

    label = "mission answer"
    answers = _require_uuid({"answers": args.answers}, "answers", label)
    state = read_mission_state(root)
    entry = state.dialogs.get(answers)
    if entry is None:
        raise ControlStoreError(
            f"{label} answers command {answers}, which this control root has never raised "
            "as a mission prompt"
        )
    kind = MISSION_DIALOG_RESPONSES.get(entry["dialog"]["kind"])
    if kind is None:
        raise ControlStoreError(
            f"{label} answers command {answers}, which is a "
            f"{entry['dialog']['kind']!r} record rather than a prompt"
        )
    prompt = dict(entry["dialog"])
    # Admission answers a *new* reply.  A redelivery of a command ID this control
    # root already recorded is answered by the stored result instead, so
    # retrying an uncertain delivery is never refused for having succeeded.
    if args.command_id not in state.dialogs:
        prompt = require_pending_prompt(state, answers, kind, label)
        # The mission a dialog belongs to must still be the worker's active
        # mission: an answer to a mission that ended is uncorrelated input,
        # which is exactly what this story removes from the interaction path.
        require_active_mission(
            state,
            prompt["mission_id"],
            prompt["worker_id"],
            prompt["queue_item_id"],
            label,
        )

    dialog = build_mission_dialog(
        command_id=args.command_id,
        kind=kind,
        mission_id=prompt["mission_id"],
        worker_id=prompt["worker_id"],
        queue_item_id=prompt["queue_item_id"],
        trace_id=args.trace,
        category=args.category,
        body_refs=tuple(args.body_ref or ()),
        # ADR-016 gives each focused dialog a child trace, so an answer whose
        # parent is not stated explicitly is correlated to the prompt it resolves.
        parent_trace_id=(
            args.parent_trace if args.parent_trace is not None else prompt["trace_id"]
        ),
        answers_command_id=answers,
        raised_at=args.raised_at,
    )
    envelope = _managed_envelope(
        args,
        root,
        MISSION_DIALOG_COMMAND_TYPES[kind],
        dialog["mission_id"],
        dialog["queue_item_id"],
        dialog["worker_id"],
        dialog["trace_id"],
        dialog["parent_trace_id"],
    )
    return register_mission_command(
        root,
        envelope,
        MISSION_DIALOG_PAYLOAD_FIELD,
        dialog,
        actor=args.answered_by,
        command=DEFAULT_MISSION_ANSWER_COMMAND,
        dry_run=args.dry_run,
    )


def _run_cancel_mission(root: Path, args: Any) -> MissionControlResult:
    """Request cooperative cancellation of one active mission."""

    label = "mission cancellation"
    worker_id = _require_identifier({"worker": args.worker}, "worker", label)
    mission_id = _require_uuid({"mission": args.mission}, "mission", label)
    queue_item_id = _require_identifier({"queue-item": args.queue_item}, "queue-item", label)
    state = read_mission_state(root)
    # Admission answers a *new* cancellation.  A redelivery of a command ID this
    # control root already recorded is answered by the stored result instead.
    if args.command_id not in state.cancellations:
        require_active_mission(state, mission_id, worker_id, queue_item_id, label)

    requested_at = _require_timestamp(
        {"requested_at": args.requested_at if args.requested_at is not None else utc_timestamp()},
        "requested_at",
        label,
    )
    cancellation = build_mission_cancellation(
        command_id=args.command_id,
        mission_id=mission_id,
        worker_id=worker_id,
        queue_item_id=queue_item_id,
        trace_id=args.trace,
        reason=args.reason,
        acknowledge_deadline_at=_cancellation_deadline(args, requested_at),
        parent_trace_id=args.parent_trace,
        evidence_refs=tuple(args.evidence or ()),
        requested_at=requested_at,
    )
    envelope = _managed_envelope(
        args,
        root,
        COMMAND_TYPE_MISSION_CANCEL,
        mission_id,
        queue_item_id,
        worker_id,
        cancellation["trace_id"],
        cancellation["parent_trace_id"],
    )
    return register_mission_command(
        root,
        envelope,
        MISSION_CANCELLATION_PAYLOAD_FIELD,
        cancellation,
        actor=args.actor,
        command=DEFAULT_MISSION_CANCEL_COMMAND,
        dry_run=args.dry_run,
    )


def _run_replace_mission(root: Path, args: Any) -> MissionControlResult:
    """Replace one mission with a new mission ID on the same single worker slot.

    Nothing semantic is refused before publication here, deliberately.  A
    replacement that would give a worker a second active mission slot is
    committed and recorded as a durable conflict by the same deterministic fold
    that resolves two concurrent replacements, so the refusal is evidence rather
    than a message that disappears with the process that printed it.
    """

    replacement = build_mission_replacement(
        command_id=args.command_id,
        worker_id=args.worker,
        queue_item_id=args.queue_item,
        replaced_mission_id=args.mission,
        replacement_mission_id=args.replacement_mission,
        trace_id=args.trace,
        reason=args.reason,
        parent_trace_id=args.parent_trace,
        evidence_refs=tuple(args.evidence or ()),
        requested_at=args.requested_at,
    )
    envelope = _managed_envelope(
        args,
        root,
        COMMAND_TYPE_MISSION_REPLACE,
        replacement["replaced_mission_id"],
        replacement["queue_item_id"],
        replacement["worker_id"],
        replacement["trace_id"],
        replacement["parent_trace_id"],
    )
    return register_mission_command(
        root,
        envelope,
        MISSION_REPLACEMENT_PAYLOAD_FIELD,
        replacement,
        actor=args.actor,
        command=DEFAULT_MISSION_REPLACE_COMMAND,
        dry_run=args.dry_run,
    )


def _add_command_payload_arguments(parser: Any) -> None:
    """Declare the payload digest arguments every managed command shares."""

    parser.add_argument(
        "--payload",
        default=None,
        metavar="JSON",
        help=(
            "the payload the durable digest is computed over; the body itself is "
            "never persisted"
        ),
    )
    parser.add_argument(
        "--digest",
        default=None,
        metavar="DIGEST",
        help="an already computed payload digest instead of the payload itself",
    )


def _add_command_boundary_arguments(parser: Any) -> None:
    """Declare the ADR-016 boundary arguments every managed command shares."""

    parser.add_argument(
        "--queue-root", default=None, metavar="PATH", help="declared queue boundary"
    )
    parser.add_argument(
        "--planning-root", default=None, metavar="PATH", help="declared planning boundary"
    )
    parser.add_argument(
        "--implementation-root",
        action="append",
        default=None,
        metavar="PATH",
        help="declared implementation boundary; may be repeated",
    )
    parser.add_argument(
        "--runtime-boundary",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="declared typed runtime, image, CI, IAM, or deployment boundary; may be repeated",
    )
    parser.add_argument(
        "--deadline", default=None, metavar="UTC", help="optional command deadline"
    )


def _facade_main_impl(argv: Optional[Sequence[str]] = None) -> int:
    """Run the small control-store CLI used by humans and controller commands."""

    import argparse

    parser = argparse.ArgumentParser(
        prog="cockpit-control",
        description=(
            "initialize, validate, publish immutable events into, record versioned worker "
            "lifecycle evidence in, deliver idempotent worker commands through, manage "
            "mission questions, cancellation, and replacement in, replay the derived "
            "ledger of, preflight, and guardedly repair the versioned cockpit control store"
        ),
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    init = subcommands.add_parser("init", help="atomically initialize an explicit control root")
    init.add_argument("--queue-root", default=None, metavar="PATH", help="declared queue root")
    init.add_argument("--planning-root", default=None, metavar="PATH", help="declared planning root")
    init.add_argument(
        "--implementation-root",
        action="append",
        default=None,
        metavar="PATH",
        help="declared implementation boundary; may be repeated",
    )
    bind_roots = subcommands.add_parser(
        "bind-roots",
        help="atomically bind queue, planning, and implementation roots into a control store",
    )
    bind_roots.add_argument("--queue-root", required=True, metavar="PATH", help="declared queue root")
    bind_roots.add_argument(
        "--planning-root", required=True, metavar="PATH", help="declared planning root"
    )
    bind_roots.add_argument(
        "--implementation-root",
        action="append",
        required=True,
        metavar="PATH",
        help="declared implementation boundary; may be repeated",
    )
    bind_roots.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report the binding without changing any state",
    )
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
    lifecycle = subcommands.add_parser(
        "record-lifecycle",
        help=(
            "commit one versioned worker lifecycle event and report whether it "
            "advanced the materialized mission state"
        ),
    )
    lifecycle.add_argument(
        "--state",
        required=True,
        metavar="STATE",
        help="lifecycle state: " + ", ".join(WORKER_LIFECYCLE_STATES),
    )
    lifecycle.add_argument("--worker", required=True, help="the worker that emitted the event")
    lifecycle.add_argument("--mission", required=True, metavar="UUID", help="mission identifier")
    lifecycle.add_argument(
        "--queue-item", required=True, metavar="QUEUE_ITEM_ID", help="queue item identifier"
    )
    lifecycle.add_argument("--trace", required=True, metavar="UUID", help="trace identifier")
    lifecycle.add_argument(
        "--parent-trace", default=None, metavar="UUID", help="parent trace identifier"
    )
    lifecycle.add_argument(
        "--sequence",
        required=True,
        metavar="N",
        help="monotonic per-mission lifecycle sequence number",
    )
    lifecycle.add_argument(
        "--reason",
        default=None,
        help="required explanation for blocked, failed, cancelled, and replaced",
    )
    lifecycle.add_argument(
        "--blocker-category", default=None, help="blocker category for the blocked state"
    )
    lifecycle.add_argument("--blocker-detail", default=None, help="blocker detail text")
    lifecycle.add_argument(
        "--heartbeat-at",
        default=None,
        metavar="UTC",
        help="heartbeat timestamp for an active state; defaults to now",
    )
    lifecycle.add_argument(
        "--fresh-until", default=None, metavar="UTC", help="explicit freshness deadline"
    )
    lifecycle.add_argument(
        "--fresh-for",
        default=None,
        metavar="SECONDS",
        help="freshness window measured from the heartbeat",
    )
    lifecycle.add_argument(
        "--evidence",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="typed evidence reference; may be repeated",
    )
    lifecycle.add_argument(
        "--superseded-by",
        default=None,
        metavar="UUID",
        help="the replacing mission identifier, required for the replaced state",
    )
    lifecycle.add_argument(
        "--actor", default=None, help="event actor; defaults to the emitting worker"
    )
    lifecycle.add_argument(
        "--dry-run",
        action="store_true",
        help="report the lifecycle decision without changing any state",
    )
    lifecycle_status = subcommands.add_parser(
        "lifecycle-status",
        help=(
            "report worker mission freshness from committed events without "
            "changing any state"
        ),
    )
    lifecycle_status.add_argument(
        "--mission", default=None, metavar="UUID", help="report only this mission"
    )
    lifecycle_status.add_argument(
        "--worker", default=None, metavar="WORKER", help="report only this worker"
    )
    lifecycle_status.add_argument(
        "--as-of",
        default=None,
        metavar="UTC",
        help="evaluate freshness at this explicit UTC moment instead of now",
    )
    register = subcommands.add_parser(
        "register-command",
        help=(
            "deliver one durable idempotent command envelope; a redelivery "
            "returns the stored result and a reused ID records a conflict"
        ),
    )
    register.add_argument(
        "--command-id",
        required=True,
        metavar="UUID",
        help="the durable command identifier every redelivery of this command reuses",
    )
    register.add_argument(
        "--type",
        dest="command_type",
        required=True,
        metavar="COMMAND_TYPE",
        help="the structured command type being delivered",
    )
    register.add_argument("--mission", required=True, metavar="UUID", help="mission identifier")
    register.add_argument(
        "--queue-item", required=True, metavar="QUEUE_ITEM_ID", help="queue item identifier"
    )
    register.add_argument(
        "--target-kind",
        default=COMMAND_TARGET_WORKER,
        metavar="KIND",
        help="delivery target kind: " + ", ".join(COMMAND_TARGET_KINDS),
    )
    register.add_argument(
        "--target", required=True, metavar="TARGET_ID", help="the worker or queue target"
    )
    register.add_argument("--trace", required=True, metavar="UUID", help="trace identifier")
    register.add_argument(
        "--parent-trace", default=None, metavar="UUID", help="parent trace identifier"
    )
    register.add_argument(
        "--payload",
        default=None,
        metavar="JSON",
        help="the command payload the durable digest is computed over",
    )
    register.add_argument(
        "--digest",
        default=None,
        metavar="DIGEST",
        help="an already computed payload digest instead of the payload itself",
    )
    register.add_argument(
        "--queue-root", default=None, metavar="PATH", help="declared queue boundary"
    )
    register.add_argument(
        "--planning-root", default=None, metavar="PATH", help="declared planning boundary"
    )
    register.add_argument(
        "--implementation-root",
        action="append",
        default=None,
        metavar="PATH",
        help="declared implementation boundary; may be repeated",
    )
    register.add_argument(
        "--runtime-boundary",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="declared typed runtime, image, CI, IAM, or deployment boundary; may be repeated",
    )
    register.add_argument(
        "--deadline", default=None, metavar="UTC", help="optional command deadline"
    )
    register.add_argument(
        "--actor",
        default=DEFAULT_EVENT_ACTOR,
        help="the actor recorded as the deliverer of the command",
    )
    register.add_argument(
        "--dry-run",
        action="store_true",
        help="report the delivery decision without changing any state",
    )
    acknowledge = subcommands.add_parser(
        "acknowledge-command",
        help=(
            "record one durable accepted, applied, rejected, or duplicate "
            "acknowledgement of a registered command"
        ),
    )
    acknowledge.add_argument(
        "--command-id", required=True, metavar="UUID", help="the command being acknowledged"
    )
    acknowledge.add_argument(
        "--outcome",
        required=True,
        metavar="OUTCOME",
        help="acknowledgement outcome: " + ", ".join(COMMAND_ACKNOWLEDGEMENT_OUTCOMES),
    )
    acknowledge.add_argument(
        "--by", dest="acknowledged_by", required=True, help="the acknowledging worker"
    )
    acknowledge.add_argument(
        "--payload",
        default=None,
        metavar="JSON",
        help="the payload the acknowledger applied, whose digest must match the command",
    )
    acknowledge.add_argument(
        "--digest",
        default=None,
        metavar="DIGEST",
        help="the payload digest the acknowledger observed",
    )
    acknowledge.add_argument(
        "--reason",
        default=None,
        help="required explanation for the rejected and duplicate outcomes",
    )
    acknowledge.add_argument(
        "--result",
        dest="result_refs",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="typed reference to the stored result; required for applied, may be repeated",
    )
    acknowledge.add_argument(
        "--actor", default=None, help="event actor; defaults to the acknowledging worker"
    )
    acknowledge.add_argument(
        "--dry-run",
        action="store_true",
        help="report the acknowledgement decision without changing any state",
    )
    command_status = subcommands.add_parser(
        "command-status",
        help=(
            "report command envelopes, acknowledgements, and conflicts from "
            "committed events without changing any state"
        ),
    )
    command_status.add_argument(
        "--command-id", default=None, metavar="UUID", help="report only this command"
    )
    command_status.add_argument(
        "--mission", default=None, metavar="UUID", help="report only this mission"
    )
    command_status.add_argument(
        "--target", default=None, metavar="TARGET_ID", help="report only this target"
    )
    raise_question = subcommands.add_parser(
        "raise-question",
        help=(
            "raise one mission question or access prompt as a durable command "
            "correlated to the worker's active mission"
        ),
    )
    raise_question.add_argument(
        "--command-id",
        required=True,
        metavar="UUID",
        help="the durable command identifier every redelivery of this prompt reuses",
    )
    raise_question.add_argument(
        "--kind",
        default=MISSION_DIALOG_QUESTION,
        metavar="KIND",
        help="prompt kind: " + ", ".join(MISSION_DIALOG_PROMPT_KINDS),
    )
    raise_question.add_argument("--worker", required=True, help="the worker raising the prompt")
    raise_question.add_argument(
        "--mission", required=True, metavar="UUID", help="the active mission identifier"
    )
    raise_question.add_argument(
        "--queue-item", required=True, metavar="QUEUE_ITEM_ID", help="queue item identifier"
    )
    raise_question.add_argument(
        "--trace", required=True, metavar="UUID", help="the focused dialog trace identifier"
    )
    raise_question.add_argument(
        "--parent-trace", default=None, metavar="UUID", help="parent trace identifier"
    )
    raise_question.add_argument(
        "--category", required=True, metavar="CATEGORY", help="structured prompt category"
    )
    raise_question.add_argument(
        "--body-ref",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help=(
            "typed reference to where the prompt body lives; required and repeatable, "
            "because the body itself is never persisted"
        ),
    )
    raise_question.add_argument(
        "--raised-at", default=None, metavar="UTC", help="explicit prompt timestamp"
    )
    _add_command_payload_arguments(raise_question)
    _add_command_boundary_arguments(raise_question)
    raise_question.add_argument(
        "--actor", default=None, help="event actor; defaults to the raising worker"
    )
    raise_question.add_argument(
        "--dry-run",
        action="store_true",
        help="report the prompt decision without changing any state",
    )
    answer_question = subcommands.add_parser(
        "answer-question",
        help=(
            "answer exactly one pending mission prompt as a durable command "
            "correlated to the same active mission"
        ),
    )
    answer_question.add_argument(
        "--command-id",
        required=True,
        metavar="UUID",
        help="the durable command identifier every redelivery of this answer reuses",
    )
    answer_question.add_argument(
        "--answers",
        required=True,
        metavar="UUID",
        help="the pending prompt command this answer resolves",
    )
    answer_question.add_argument(
        "--by",
        dest="answered_by",
        required=True,
        help="the overseer or operator answering the prompt",
    )
    answer_question.add_argument(
        "--trace", required=True, metavar="UUID", help="the focused dialog trace identifier"
    )
    answer_question.add_argument(
        "--parent-trace",
        default=None,
        metavar="UUID",
        help="parent trace identifier; defaults to the trace of the prompt answered",
    )
    answer_question.add_argument(
        "--category", required=True, metavar="CATEGORY", help="structured answer category"
    )
    answer_question.add_argument(
        "--body-ref",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help=(
            "typed reference to where the answer body lives; required and repeatable, "
            "because the body itself is never persisted"
        ),
    )
    answer_question.add_argument(
        "--raised-at", default=None, metavar="UTC", help="explicit answer timestamp"
    )
    _add_command_payload_arguments(answer_question)
    _add_command_boundary_arguments(answer_question)
    answer_question.add_argument(
        "--dry-run",
        action="store_true",
        help="report the answer decision without changing any state",
    )
    cancel_mission = subcommands.add_parser(
        "cancel-mission",
        help=(
            "request cooperative cancellation of one active mission with an "
            "explicit acknowledgement deadline"
        ),
    )
    cancel_mission.add_argument(
        "--command-id",
        required=True,
        metavar="UUID",
        help="the durable command identifier every redelivery of this request reuses",
    )
    cancel_mission.add_argument(
        "--worker", required=True, help="the worker owning the mission being cancelled"
    )
    cancel_mission.add_argument(
        "--mission", required=True, metavar="UUID", help="the active mission identifier"
    )
    cancel_mission.add_argument(
        "--queue-item", required=True, metavar="QUEUE_ITEM_ID", help="queue item identifier"
    )
    cancel_mission.add_argument("--trace", required=True, metavar="UUID", help="trace identifier")
    cancel_mission.add_argument(
        "--parent-trace", default=None, metavar="UUID", help="parent trace identifier"
    )
    cancel_mission.add_argument(
        "--reason", required=True, help="required explanation for the cancellation"
    )
    cancel_mission.add_argument(
        "--evidence",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="typed evidence reference; may be repeated",
    )
    cancel_mission.add_argument(
        "--requested-at", default=None, metavar="UTC", help="explicit request timestamp"
    )
    cancel_mission.add_argument(
        "--acknowledge-by",
        default=None,
        metavar="UTC",
        help="explicit moment an acknowledgement is expected by",
    )
    cancel_mission.add_argument(
        "--acknowledge-within",
        default=None,
        metavar="SECONDS",
        help="acknowledgement window measured from the request",
    )
    _add_command_payload_arguments(cancel_mission)
    _add_command_boundary_arguments(cancel_mission)
    cancel_mission.add_argument(
        "--actor",
        default=DEFAULT_EVENT_ACTOR,
        help="the actor recorded as the requester of the cancellation",
    )
    cancel_mission.add_argument(
        "--dry-run",
        action="store_true",
        help="report the cancellation decision without changing any state",
    )
    replace_mission = subcommands.add_parser(
        "replace-mission",
        help=(
            "terminate one mission as replaced and claim the worker's single "
            "slot for an explicit new mission identifier"
        ),
    )
    replace_mission.add_argument(
        "--command-id",
        required=True,
        metavar="UUID",
        help="the durable command identifier every redelivery of this request reuses",
    )
    replace_mission.add_argument(
        "--worker", required=True, help="the worker whose single mission slot is replaced"
    )
    replace_mission.add_argument(
        "--mission", required=True, metavar="UUID", help="the mission being replaced"
    )
    replace_mission.add_argument(
        "--replacement-mission",
        required=True,
        metavar="UUID",
        help="the explicit new mission identifier this replacement creates",
    )
    replace_mission.add_argument(
        "--queue-item", required=True, metavar="QUEUE_ITEM_ID", help="queue item identifier"
    )
    replace_mission.add_argument("--trace", required=True, metavar="UUID", help="trace identifier")
    replace_mission.add_argument(
        "--parent-trace", default=None, metavar="UUID", help="parent trace identifier"
    )
    replace_mission.add_argument(
        "--reason", required=True, help="required explanation for the replacement"
    )
    replace_mission.add_argument(
        "--evidence",
        action="append",
        default=None,
        metavar="TYPE:VALUE",
        help="typed evidence reference; may be repeated",
    )
    replace_mission.add_argument(
        "--requested-at", default=None, metavar="UTC", help="explicit request timestamp"
    )
    _add_command_payload_arguments(replace_mission)
    _add_command_boundary_arguments(replace_mission)
    replace_mission.add_argument(
        "--actor",
        default=DEFAULT_EVENT_ACTOR,
        help="the actor recorded as the requester of the replacement",
    )
    replace_mission.add_argument(
        "--dry-run",
        action="store_true",
        help="report the replacement decision without changing any state",
    )
    mission_status = subcommands.add_parser(
        "mission-status",
        help=(
            "report mission dialogs, cancellation observations, and the single "
            "mission slot each worker holds without changing any state"
        ),
    )
    mission_status.add_argument(
        "--mission", default=None, metavar="UUID", help="report only this mission"
    )
    mission_status.add_argument(
        "--worker", default=None, metavar="WORKER", help="report only this worker"
    )
    mission_status.add_argument(
        "--command-id", default=None, metavar="UUID", help="report only this command"
    )
    mission_status.add_argument(
        "--as-of",
        default=None,
        metavar="UTC",
        help=(
            "evaluate cancellation acknowledgement deadlines at this explicit UTC "
            "moment instead of now"
        ),
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
            result = initialize_control_store(
                resolved,
                queue_root=args.queue_root,
                planning_root=args.planning_root,
                implementation_roots=tuple(args.implementation_root or ()),
            )
            verb = "initialized" if result.created else "validated"
            print(f"cockpit-control: {verb} {result.root} (source: {result.source})")
            return 0
        if args.command == "bind-roots":
            result = bind_control_roots(
                resolved.path,
                args.queue_root,
                args.planning_root,
                tuple(args.implementation_root or ()),
                dry_run=args.dry_run,
            )
            verb = "would bind" if args.dry_run and result.changed else "bound"
            if not result.changed:
                verb = "already bound"
            print(
                f"cockpit-control: {verb} roots in {result.root}: "
                f"queue={result.queue_root} planning={result.planning_root} "
                f"implementation-roots={len(result.implementation_roots)}"
            )
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
        if args.command == "record-lifecycle":
            return _report_lifecycle_record(
                record_worker_lifecycle(
                    resolved.path,
                    _lifecycle_from_arguments(args),
                    actor=args.actor,
                    dry_run=args.dry_run,
                )
            )
        if args.command == "lifecycle-status":
            as_of = args.as_of if args.as_of is not None else utc_timestamp()
            return _report_lifecycle_status(
                resolved.path,
                observe_worker_lifecycle(
                    resolved.path,
                    as_of=as_of,
                    mission_id=args.mission,
                    worker_id=args.worker,
                ),
                as_of,
            )
        if args.command == "register-command":
            return _report_command_record(
                register_command(
                    resolved.path,
                    _envelope_from_arguments(args, resolved.path),
                    actor=args.actor,
                    dry_run=args.dry_run,
                )
            )
        if args.command == "acknowledge-command":
            return _report_command_record(
                acknowledge_command(
                    resolved.path,
                    build_command_acknowledgement(
                        args.command_id,
                        _command_digest_from_arguments(args),
                        args.outcome,
                        acknowledged_by=args.acknowledged_by,
                        reason=args.reason,
                        result_refs=tuple(args.result_refs or ()),
                    ),
                    actor=args.actor,
                    dry_run=args.dry_run,
                )
            )
        if args.command == "raise-question":
            return _report_mission_control(_run_raise_question(resolved.path, args))
        if args.command == "answer-question":
            return _report_mission_control(_run_answer_question(resolved.path, args))
        if args.command == "cancel-mission":
            return _report_mission_control(_run_cancel_mission(resolved.path, args))
        if args.command == "replace-mission":
            return _report_mission_control(_run_replace_mission(resolved.path, args))
        if args.command == "mission-status":
            return _report_mission_status(
                observe_mission_control(
                    resolved.path,
                    as_of=args.as_of,
                    mission_id=args.mission,
                    worker_id=args.worker,
                    command_id=args.command_id,
                )
            )
        if args.command == "command-status":
            return _report_command_status(
                resolved.path,
                observe_commands(
                    resolved.path,
                    command_id=args.command_id,
                    mission_id=args.mission,
                    target_id=args.target,
                ),
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


control_cli.bind_facade(globals())
main = control_cli.main


if __name__ == "__main__":
    raise SystemExit(main())
