"""Control-root and schema compatibility seam for the cockpit_control facade.

This runtime module owns fail-closed control-root resolution, control-store
schema constants, and compatibility diagnostics that refuse unsupported future
schema versions before mutation.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

CONTROL_SCHEMA_VERSION = 1
CONTROL_METADATA_NAME = "control.json"
LEDGER_NAME = "ledger.json"
EVENTS_NAME = "events.jsonl"

CONTROL_ROOT_VARIABLE = "COCKPIT_CONTROL_ROOT"
QUEUE_ROOT_VARIABLE = "COCKPIT_QUEUE_ROOT"


class ControlRootSchemaError(RuntimeError):
    """Raised when control-root or schema compatibility checks fail closed."""


@dataclass(frozen=True)
class ResolvedControlRoot:
    """The configured root and the sole source from which it was resolved."""

    path: Path
    source: str


def require_absolute_root(
    value: str,
    source: str,
    variable: str = CONTROL_ROOT_VARIABLE,
) -> Path:
    if not isinstance(value, str) or not value:
        raise ControlRootSchemaError(f"{source} {variable} is empty")
    if "\x00" in value:
        raise ControlRootSchemaError(f"{source} {variable} contains a NUL byte")

    candidate = Path(value)
    if not candidate.is_absolute():
        raise ControlRootSchemaError(
            f"{source} {variable} must be an absolute path; refusing to infer it from cwd"
        )

    # Lexical normalization makes the effective root explicit without resolving
    # symlinks to an unexpected location. It also keeps a tmux-exported root
    # stable when the shell is launched from another working directory.
    normalized = Path(os.path.normpath(value))
    if not normalized.is_absolute():
        raise ControlRootSchemaError(f"{source} {variable} is malformed")
    return normalized


def tmux_control_root() -> Optional[str]:
    """Read the exact root from the active tmux server, if there is one."""

    if not os.environ.get("TMUX"):
        return None
    try:
        result = subprocess.run(
            ["tmux", "show-environment", CONTROL_ROOT_VARIABLE],
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
    prefix = f"{CONTROL_ROOT_VARIABLE}="
    if not output.startswith(prefix):
        return None
    return output[len(prefix) :]


def resolve_control_root(environ: Optional[Mapping[str, str]] = None) -> ResolvedControlRoot:
    """Resolve root from shell first, then active tmux session as fallback."""

    environment = os.environ if environ is None else environ
    if CONTROL_ROOT_VARIABLE in environment:
        return ResolvedControlRoot(
            require_absolute_root(environment[CONTROL_ROOT_VARIABLE], "shell"),
            "shell",
        )

    tmux_value = tmux_control_root()
    if tmux_value is not None:
        return ResolvedControlRoot(require_absolute_root(tmux_value, "tmux session"), "tmux")

    raise ControlRootSchemaError(
        "COCKPIT_CONTROL_ROOT is required; set an absolute root in the shell or active tmux session"
    )


def require_schema_version(record: Mapping[str, Any], label: str) -> None:
    """Require one supported control schema version, refusing future versions."""

    version = record.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise ControlRootSchemaError(f"{label} requires integer schema_version")
    if version > CONTROL_SCHEMA_VERSION:
        raise ControlRootSchemaError(
            f"{label} uses unsupported future schema_version {version}; upgrade cockpit tools before mutation"
        )
    if version != CONTROL_SCHEMA_VERSION:
        raise ControlRootSchemaError(f"{label} uses unsupported schema_version {version}")


def metadata_future_schema_diagnostic(
    declared: int,
    metadata_name: str = CONTROL_METADATA_NAME,
    supported_version: int = CONTROL_SCHEMA_VERSION,
) -> str:
    """Render the preflight compatibility diagnostic for unsupported future roots."""

    return (
        f"{metadata_name} declares future schema_version {declared}; this "
        f"build supports schema_version {supported_version} and refuses mutation"
    )


def declared_future_schema_version(record: Any) -> Optional[int]:
    """Return declared future schema_version or None when not applicable."""

    if not isinstance(record, dict):
        return None
    declared = record.get("schema_version")
    if isinstance(declared, bool) or not isinstance(declared, int):
        return None
    if declared <= CONTROL_SCHEMA_VERSION:
        return None
    return declared
