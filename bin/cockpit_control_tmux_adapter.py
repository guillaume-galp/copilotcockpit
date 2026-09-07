"""Thin tmux/subprocess adapter seam preserving session detection behavior."""

from __future__ import annotations

from typing import Any, Mapping


_session_identity_impl = None


def bind_facade(namespace: Mapping[str, Any]) -> None:
    """Bind tmux-backed session identity behavior from the facade."""

    global _session_identity_impl
    _session_identity_impl = namespace.get("_session_identity")


def session_identity() -> str:
    if _session_identity_impl is None:
        raise RuntimeError("cockpit_control_tmux_adapter is not bound")
    return _session_identity_impl()

