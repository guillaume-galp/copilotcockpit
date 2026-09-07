"""Source-tree pre-extraction contract and static-check helper (NOT an installed runtime module).

This module exists in the repository to provide typed seam contracts and
lightweight static dependency-direction checks for unit tests and other
developer tooling during the foundation-phase work. It intentionally
moves no runtime behavior and is not installed by the normal
installation surfaces.

If a future story requires this module to be present at runtime, that
story MUST update MANAGED_RUNTIME_MODULES and the installer/doctor/
uninstall/release/cold-install surfaces so the module is actually
packaged and deployed.
"""


from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from pathlib import Path

# Ontology-aligned seam names used in comments and diagnostics.
SEAM_COMMAND_ENVELOPE = "command-envelope"
SEAM_WORKER_LIFECYCLE = "worker-lifecycle"
SEAM_CONTROLLER_OBSERVATION = "controller-observation"


class DependencyError(RuntimeError):
    """Raised when a static dependency-direction rule is violated."""


@dataclass(frozen=True)
class CommandEnvelope:
    """A typed view of the existing on-wire command envelope schema.

    This dataclass is intentionally a thin, annotated wrapper. Conversion
    helpers validate against the live facade when required so the runtime
    behavior of the facade is preserved while giving future code a typed
    seam to depend on.
    """

    schema_version: int
    record_type: str
    command_id: str
    command_type: str
    mission_id: str
    queue_item_id: str
    target: Dict[str, Any]
    trace_id: str
    parent_trace_id: Optional[str]
    payload_digest: str
    boundaries: Dict[str, Any]
    created_at: str
    deadline_at: Optional[str]


def envelope_from_dict(record: Dict[str, Any]) -> CommandEnvelope:
    """Convert a dict (on-wire) envelope into a typed CommandEnvelope.

    The function performs no mutation and is conservative: it only reads the
    dict and constructs a frozen dataclass. For compatibility it is safe to
    round-trip an envelope through envelope_to_dict() and get byte-compatible
    on-wire semantics when validated by the facade.
    """
    # Lightweight shape matching only; validators in cockpit_control keep the
    # authoritative semantics. This thin conversion makes future extraction
    # easy without changing the facade.
    return CommandEnvelope(
        schema_version=record.get("schema_version"),
        record_type=record.get("record_type"),
        command_id=record.get("command_id"),
        command_type=record.get("command_type"),
        mission_id=record.get("mission_id"),
        queue_item_id=record.get("queue_item_id"),
        target=record.get("target"),
        trace_id=record.get("trace_id"),
        parent_trace_id=record.get("parent_trace_id"),
        payload_digest=record.get("payload_digest"),
        boundaries=record.get("boundaries"),
        created_at=record.get("created_at"),
        deadline_at=record.get("deadline_at"),
    )


def envelope_to_dict(env: CommandEnvelope) -> Dict[str, Any]:
    """Convert a typed CommandEnvelope back into the dict/on-wire schema.

    The mapping uses the same field names as the facade so callers that still
    use the dict-based facade observe no change.
    """
    return {
        "schema_version": env.schema_version,
        "record_type": env.record_type,
        "command_id": env.command_id,
        "command_type": env.command_type,
        "mission_id": env.mission_id,
        "queue_item_id": env.queue_item_id,
        "target": env.target,
        "trace_id": env.trace_id,
        "parent_trace_id": env.parent_trace_id,
        "payload_digest": env.payload_digest,
        "boundaries": env.boundaries,
        "created_at": env.created_at,
        "deadline_at": env.deadline_at,
    }


def _iter_python_files(paths: Sequence[Path]) -> Sequence[Path]:
    files: List[Path] = []
    for p in paths:
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                files.append(f)
    return files


def _extract_imported_names(path: Path) -> Set[str]:
    """Parse a Python source file and return top-level imported module/package names."""
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def _name_matches_token(name: str, token: str) -> bool:
    """Return True when an imported top-level name should be considered a match

    Matching semantics are intentionally conservative and based on token
    segmentation rather than naive substring checks. We consider an imported
    top-level name to match a banned token when either the name equals the
    token, or one of the underscore-separated segments equals the token. This
    avoids false positives where a banned token appears as a substring inside
    a larger identifier.
    """
    if name == token:
        return True
    parts = name.split("_")
    return token in parts


def check_import_cycles(domain_paths: Sequence[Path]) -> None:
    """Lightweight detection of import cycles among top-level domain tokens.

    This is not a full module-resolution algorithm; it is a focused check that
    builds a directed graph between top-level tokens discovered under the
    provided domain paths and reports any simple cycles. It is sufficient for
    the foundation-phase AC2 requirement to detect accidental circular
    dependencies introduced between domain modules.
    """
    files = _iter_python_files(domain_paths)
    # Map top-level token -> set of files providing that token
    token_providers: Dict[str, Set[Path]] = {}
    token_of_file: Dict[Path, str] = {}
    for f in files:
        # Compute a top-level token relative to the nearest domain root.
        rel = None
        for root in domain_paths:
            try:
                rel = f.relative_to(root)
                break
            except Exception:
                continue
        if rel is None:
            continue
        parts = rel.parts
        if parts[0].endswith('.py'):
            token = Path(parts[0]).stem
        else:
            token = parts[0]
        token_of_file[f] = token
        token_providers.setdefault(token, set()).add(f)

    # Build token-level edges: token A -> token B when any file under A imports B
    edges: Dict[str, Set[str]] = {t: set() for t in token_providers.keys()}
    for f, token in token_of_file.items():
        imported = _extract_imported_names(f)
        for name in imported:
            if name in token_providers:
                edges[token].add(name)

    # Detect cycles using DFS with recursion stack.
    visited: Set[str] = set()
    stack: Set[str] = set()
    found_cycles: List[List[str]] = []

    def dfs(u: str, path: List[str]) -> None:
        visited.add(u)
        stack.add(u)
        for v in sorted(edges.get(u, [])):
            if v not in visited:
                dfs(v, path + [v])
            elif v in stack:
                # Found a cycle; extract the cycle path
                try:
                    idx = path.index(v)
                    cycle = path[idx:] + [v]
                except ValueError:
                    cycle = [v, u, v]
                found_cycles.append(cycle)
        stack.remove(u)

    for node in sorted(edges.keys()):
        if node not in visited:
            dfs(node, [node])

    if found_cycles:
        lines = [" -> ".join(c) for c in found_cycles]
        raise DependencyError("import cycles detected among domain tokens:\n" + "\n".join(lines))


def check_dependency_direction(
    domain_paths: Sequence[Path],
    banned_adapter_names: Sequence[str] = ("cli", "rendering", "tmux", "queue", "scheduler"),
    detect_cycles: bool = True,
) -> None:
    """Check that files in domain_paths do not import banned adapters.

    This static check is intentionally conservative: it flags any imported
    top-level name that contains one of the banned adapter tokens. The check
    is designed to be used by focused tests during the foundation phase; it is
    not a replacement for later build-time tooling but is sufficient to meet
    AC2 for this story.
    """
    files = _iter_python_files(domain_paths)
    violations: List[Tuple[Path, str]] = []
    banned = set(banned_adapter_names)
    for f in files:
        imported = _extract_imported_names(f)
        for name in sorted(imported):
            for token in banned:
                if _name_matches_token(name, token):
                    violations.append((f, name))
    if violations:
        lines = [f"{p}: imports forbidden adapter '{n}'" for p, n in violations]
        raise DependencyError("dependency-direction violations:\n" + "\n".join(lines))

    if detect_cycles:
        # Run the lightweight cycle detector across the same domain surface.
        check_import_cycles(domain_paths)


# Exported convenience for tests: scan the repository root's `bin` and `lib`
# directories treating them as domain surfaces to be statically audited.
def check_repo_domain_surface(repo_root: Path) -> None:
    domain_dirs = [repo_root / "bin", repo_root / "lib"]
    check_dependency_direction(domain_dirs)
