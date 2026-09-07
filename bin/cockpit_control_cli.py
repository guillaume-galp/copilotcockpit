"""Thin CLI adapter seam preserving the facade `main()` contract."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


_facade_main_impl = None


def bind_facade(namespace: Mapping[str, Any]) -> None:
    """Bind the facade's authoritative CLI implementation into this adapter."""

    global _facade_main_impl
    _facade_main_impl = namespace.get("_facade_main_impl")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the control CLI through the bound facade implementation."""

    if _facade_main_impl is None:
        raise RuntimeError("cockpit_control_cli is not bound to the facade implementation")
    return _facade_main_impl(argv)

