# SPDX-License-Identifier: MIT
#
# Copyright (c) 2026 Prisiragent Project Contributors
#
# This file is dual-licensed under the MIT License (see ../../LICENSE).
#
# Permission wrapper: calendar.read (task #8).
#
# Thin shim over CalendarMapsPermGate for callers that want a per-permission
# import surface. The real logic (first-time card merging, grant store,
# risk tier, ledger rules) lives in calendar_maps_gates.py and is shared
# across the three wrappers -- never duplicate it here.
#
# Trigger verbs (for the agent's tool dispatcher to call):
#     list_events()       -- enumerate events in a window
#     get_event(id)       -- fetch one event
#     free_busy(window)   -- check availability for travel-buffer planning
#
# Graceful degradation on deny:
#     The agent's travel-buffer workflow must detect a denied result and
#     surface "无法安排交通缓冲" to the user verbatim. See
#     tests/test_perm_calendar.py::test_deny_blocks_travel_buffer.

from __future__ import annotations

import logging
from typing import Any

from prisiragent_coworker.permissions.calendar_maps_gates import (
    CalendarMapsPermission,
    CalendarMapsPermGate,
    PermRequestResult,
)

__all__ = ["DEFAULT_GATE", "is_granted", "request", "request_calendar_read"]

_LOGGER = logging.getLogger(__name__)

# Module-level default gate; production callers (the agent's tool
# dispatcher) wire this once at startup. Tests build their own gate via
# ``with_default(Gate())`` if they need isolation.
_DEFAULT_GATE: CalendarMapsPermGate | None = None


def with_default(gate: CalendarMapsPermGate) -> None:
    """Install the module-level default gate. Idempotent."""
    global _DEFAULT_GATE
    _DEFAULT_GATE = gate
    _LOGGER.info("calendar_read: default gate installed")


def _gate() -> CalendarMapsPermGate:
    if _DEFAULT_GATE is None:
        raise RuntimeError(
            "calendar_read: no default gate installed; "
            "call with_default(gate) at app startup"
        )
    return _DEFAULT_GATE


def is_granted() -> bool:
    """Return True iff calendar.read has been granted (silent fast-path probe)."""
    if _DEFAULT_GATE is None:
        return False
    return _DEFAULT_GATE.grant_store.is_granted(CalendarMapsPermission.CALENDAR_READ)


def request(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    """Request calendar.read permission via the default gate."""
    return _gate().request(
        CalendarMapsPermission.CALENDAR_READ,
        operation=operation,
        context=context,
    )


# Convenience alias: agents commonly call this as ``request_calendar_read()``
# without caring about the operation verb.
def request_calendar_read(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    return request(operation, context=context)


# Sentinel gate used by tools/__init__.py style re-exports.
DEFAULT_GATE = _DEFAULT_GATE  # may be None until with_default() runs.