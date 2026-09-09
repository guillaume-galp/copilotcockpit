"""Versioned, repository-exclusive scheduling claims (not a filesystem sandbox)."""

from __future__ import annotations

import copy
import fcntl
import os
import re
import subprocess
from urllib.parse import unquote, urlsplit
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("footprint requires nonempty, trimmed strings")
    return value


def _path(value: Any, live: bool = False) -> str:
    value = _text(value)
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("footprint paths must be absolute without '..'")
    if not live and str(path) != value:
        raise ValueError("footprint paths must be canonical")
    try:
        return str(path.resolve()) if live else str(path)
    except RuntimeError as exc:
        raise ValueError(f"footprint path cannot be resolved: {value}") from exc


def _git(root: str, *args: str) -> str:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    result = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=False, env=env,
    )
    if result.returncode:
        raise ValueError(f"footprint repository cannot be inspected: {root}: {result.stderr.strip()}")
    return result.stdout.strip()


def _upstream(value: Any, root: Optional[str] = None) -> str:
    value = _text(value)
    local = False
    if "://" in value:
        parsed = urlsplit(value)
        if parsed.scheme == "file" and parsed.hostname in (None, "localhost"):
            value = _path(unquote(parsed.path), root is not None)
            local = True
        elif parsed.hostname:
            value = parsed.hostname.lower() + "/" + parsed.path.lstrip("/")
    elif re.match(r"[^/@]+@[^/:]+:", value):
        host, path = value.split("@", 1)[1].split(":", 1)
        value = host.lower() + "/" + path
    elif value.startswith("/") or (root is not None and value.startswith(".")):
        value = str((Path(root or "/") / value).resolve()) if root is not None else _path(value)
        local = True
    if local:
        return _text(value)
    value = value.rstrip("/")
    return _text(value[:-4] if value.endswith(".git") else value)


def validate_footprint(value: Any, *, live: bool = False, normalize: bool = False) -> Dict[str, Any]:
    """Validate snapshots without filesystem lookups; optionally verify live scope."""
    if not isinstance(value, dict):
        raise ValueError("footprint must be an object")
    fields = {"version", "status", "rationale", "repositories", "write_paths", "resources", "workers"}
    if set(value) != fields or type(value.get("version")) is not int or value["version"] != 1:
        raise ValueError("unsupported footprint version or fields (expected version 1)")
    result = copy.deepcopy(value)
    if result["status"] not in ("draft", "reviewed"):
        raise ValueError("footprint status must be draft or reviewed")
    _text(result["rationale"])
    for field in ("repositories", "write_paths", "resources"):
        if not isinstance(result[field], list):
            raise ValueError(f"footprint {field} must be an array")
    if result["status"] == "reviewed" and not result["repositories"]:
        raise ValueError("reviewed footprint requires at least one repository")
    for repo in result["repositories"]:
        required = {"path", "git_common_dir", "upstream", "branch"}
        if not isinstance(repo, dict) or (set(repo) | ({"git_common_dir"} if normalize else set())) != required:
            raise ValueError("repository requires path, git_common_dir, upstream and branch")
        repo["path"] = _path(repo["path"], live)
        repo["upstream"] = _upstream(repo["upstream"], repo["path"] if live else None)
        _text(repo["branch"])
        if live:
            top = _path(_git(repo["path"], "rev-parse", "--show-toplevel"), True)
            if top != repo["path"]:
                raise ValueError("repository path must be its worktree root")
            common = _git(top, "rev-parse", "--git-common-dir")
            common = str((Path(top) / common).resolve())
            if "git_common_dir" in repo and _path(repo["git_common_dir"], True) != common:
                raise ValueError("repository git_common_dir changed")
            repo["git_common_dir"] = common
            remotes = _git(top, "remote").splitlines()
            if "origin" in remotes:
                origin = _upstream(_git(top, "remote", "get-url", "origin"), top)
                if origin != repo["upstream"]:
                    raise ValueError("repository upstream must match canonical origin identity")
        else:
            repo["git_common_dir"] = _path(repo["git_common_dir"])
    result["write_paths"] = [_path(path, live) for path in result["write_paths"]]
    result["resources"] = [_text(key) for key in result["resources"]]
    workers = result["workers"]
    if not isinstance(workers, dict):
        raise ValueError("footprint workers must be a role-to-worker object")
    for role, worker in workers.items():
        if role not in ("worker-dev", "worker-test", "worker-fix") or not isinstance(worker, str):
            raise ValueError("unsupported footprint worker role")
        if re.fullmatch(re.escape(role) + r"(?:-[1-9][0-9]*)?", worker) is None:
            raise ValueError("worker identity must be its role or role-<positive integer>")
    if not normalize and result != value:
        raise ValueError("footprint canonical paths changed; review required")
    return result


def footprints_conflict(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
    """Unknown/draft claims are exclusive; compare immutable reviewed snapshots."""
    if left is None or right is None:
        return True
    for value in (left, right):
        validate_footprint(value)
        if value["status"] != "reviewed":
            return True
    if set(left["resources"]) & set(right["resources"]):
        return True
    if {r["upstream"] for r in left["repositories"]} & {r["upstream"] for r in right["repositories"]}:
        return True
    def paths(scope: Dict[str, Any]) -> List[str]:
        return scope["write_paths"] + [
            repo[key] for repo in scope["repositories"] for key in ("path", "git_common_dir")
        ]
    for a in paths(left):
        for b in paths(right):
            if a == b or Path(a) in Path(b).parents or Path(b) in Path(a).parents:
                return True
    return False


def require_footprint_authority(
    footprint: Optional[Dict[str, Any]], roots: Mapping[str, Any],
) -> None:
    """Reviewed claims may narrow cockpit authority, never expand it."""
    if footprint is None:
        return
    validate_footprint(footprint)
    if footprint["status"] != "reviewed":
        return

    def canonical(value: Any) -> Path:
        resolved = _path(value, True)
        if resolved != value:
            raise ValueError(f"footprint authority path changed: {value}")
        return Path(resolved)

    def within(path: Path, parent: Path) -> bool:
        return path == parent or parent in path.parents

    implementation = roots.get("implementation_roots")
    if not isinstance(implementation, list) or not implementation:
        raise ValueError("reviewed footprint requires declared implementation_roots")
    implementation = [canonical(root) for root in implementation]
    writable = implementation + (
        [canonical(roots["planning_root"])] if roots.get("planning_root") else []
    )
    protected = [canonical(roots.get(key)) for key in ("control_root", "queue_root")]
    claims = [
        (f"repository {key}", canonical(repo[key]), implementation)
        for repo in footprint["repositories"] for key in ("path", "git_common_dir")
    ] + [("write_path", canonical(path), writable) for path in footprint["write_paths"]]
    for kind, path, allowed in claims:
        if not any(within(path, parent) for parent in allowed):
            raise ValueError(f"reviewed footprint {kind} outside cockpit declared roots: {path}")
        if any(within(path, reserved) or within(reserved, path) for reserved in protected):
            raise ValueError(f"reviewed footprint {kind} overlaps protected queue/control store: {path}")


@contextmanager
def queue_lock(root: Any) -> Iterator[None]:
    """Serialize queue writers with dispatch/acceptance's final queue read."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".queue.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
