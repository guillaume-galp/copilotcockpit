"""Thin queue adapter seam preserving queue-observation contracts."""

from __future__ import annotations

from typing import Any, Mapping


QueueItemObservation = None
QueueObservation = None
_queue_item_observation_impl = None
_queue_paused_impl = None
observe_queue_impl = None


def bind_facade(namespace: Mapping[str, Any]) -> None:
    """Bind queue observation symbols from the compatibility facade."""

    global QueueItemObservation
    global QueueObservation
    global _queue_item_observation_impl
    global _queue_paused_impl
    global observe_queue_impl
    QueueItemObservation = namespace.get("QueueItemObservation")
    QueueObservation = namespace.get("QueueObservation")
    _queue_item_observation_impl = namespace.get("_queue_item_observation")
    _queue_paused_impl = namespace.get("_queue_paused")
    observe_queue_impl = namespace.get("observe_queue")


def _require(bound: Any, name: str) -> Any:
    if bound is None:
        raise RuntimeError(f"cockpit_control_queue_adapter is not bound: {name}")
    return bound


def _queue_item_observation(path: Any, label: str) -> Any:
    return _require(_queue_item_observation_impl, "_queue_item_observation")(path, label)


def _queue_paused(root: Any) -> bool:
    return _require(_queue_paused_impl, "_queue_paused")(root)


def observe_queue(root: Any) -> Any:
    return _require(observe_queue_impl, "observe_queue")(root)

