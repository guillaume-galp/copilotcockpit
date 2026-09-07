"""Thin rendering adapter seam for human-readable diagnostics and output."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


_print_error_impl = None
_report_lock_repair_impl = None
_report_preflight_impl = None
_report_store_repair_impl = None
_report_pending_debris_impl = None
_report_projection_debris_impl = None
_report_ledger_projection_impl = None
_report_event_publication_impl = None
_report_event_history_impl = None
_report_lifecycle_record_impl = None
_report_lifecycle_status_impl = None
_report_command_record_impl = None
_report_command_status_impl = None
_report_mission_control_impl = None
_report_mission_status_impl = None


def bind_facade(namespace: Mapping[str, Any]) -> None:
    """Capture the facade's stable rendering contracts."""

    global _print_error_impl
    global _report_lock_repair_impl
    global _report_preflight_impl
    global _report_store_repair_impl
    global _report_pending_debris_impl
    global _report_projection_debris_impl
    global _report_ledger_projection_impl
    global _report_event_publication_impl
    global _report_event_history_impl
    global _report_lifecycle_record_impl
    global _report_lifecycle_status_impl
    global _report_command_record_impl
    global _report_command_status_impl
    global _report_mission_control_impl
    global _report_mission_status_impl
    _print_error_impl = namespace.get("_print_error")
    _report_lock_repair_impl = namespace.get("_report_lock_repair")
    _report_preflight_impl = namespace.get("_report_preflight")
    _report_store_repair_impl = namespace.get("_report_store_repair")
    _report_pending_debris_impl = namespace.get("_report_pending_debris")
    _report_projection_debris_impl = namespace.get("_report_projection_debris")
    _report_ledger_projection_impl = namespace.get("_report_ledger_projection")
    _report_event_publication_impl = namespace.get("_report_event_publication")
    _report_event_history_impl = namespace.get("_report_event_history")
    _report_lifecycle_record_impl = namespace.get("_report_lifecycle_record")
    _report_lifecycle_status_impl = namespace.get("_report_lifecycle_status")
    _report_command_record_impl = namespace.get("_report_command_record")
    _report_command_status_impl = namespace.get("_report_command_status")
    _report_mission_control_impl = namespace.get("_report_mission_control")
    _report_mission_status_impl = namespace.get("_report_mission_status")


def _require(bound: Any, name: str) -> Any:
    if bound is None:
        raise RuntimeError(f"cockpit_control_rendering is not bound: {name}")
    return bound


def _print_error(error: Exception) -> int:
    return _require(_print_error_impl, "_print_error")(error)


def _report_lock_repair(result: Any) -> int:
    return _require(_report_lock_repair_impl, "_report_lock_repair")(result)


def _report_preflight(report: Any) -> int:
    return _require(_report_preflight_impl, "_report_preflight")(report)


def _report_store_repair(result: Any) -> int:
    return _require(_report_store_repair_impl, "_report_store_repair")(result)


def _report_pending_debris(debris: Sequence[Any]) -> None:
    return _require(_report_pending_debris_impl, "_report_pending_debris")(debris)


def _report_projection_debris(temporaries: Sequence[Any]) -> None:
    return _require(_report_projection_debris_impl, "_report_projection_debris")(temporaries)


def _report_ledger_projection(result: Any) -> int:
    return _require(_report_ledger_projection_impl, "_report_ledger_projection")(result)


def _report_event_publication(result: Any) -> int:
    return _require(_report_event_publication_impl, "_report_event_publication")(result)


def _report_event_history(root: Any, history: Any) -> int:
    return _require(_report_event_history_impl, "_report_event_history")(root, history)


def _report_lifecycle_record(result: Any) -> int:
    return _require(_report_lifecycle_record_impl, "_report_lifecycle_record")(result)


def _report_lifecycle_status(root: Any, observations: Sequence[Any], as_of: str) -> int:
    return _require(_report_lifecycle_status_impl, "_report_lifecycle_status")(root, observations, as_of)


def _report_command_record(result: Any) -> int:
    return _require(_report_command_record_impl, "_report_command_record")(result)


def _report_command_status(root: Any, observations: Sequence[Mapping[str, Any]]) -> int:
    return _require(_report_command_status_impl, "_report_command_status")(root, observations)


def _report_mission_control(result: Any) -> int:
    return _require(_report_mission_control_impl, "_report_mission_control")(result)


def _report_mission_status(report: Any) -> int:
    return _require(_report_mission_status_impl, "_report_mission_status")(report)

