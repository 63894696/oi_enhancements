# SPDX-License-Identifier: MIT
#
# Copyright (c) 2026 Prisiragent Project Contributors
#
# This file is dual-licensed under the MIT License (see ../../LICENSE).
#
# Permission wrapper: calendar.write (task #8, HIGH RISK).
#
# Thin shim over CalendarMapsPermGate. The shared first-time card is
# defined in calendar_maps_gates.py -- when ANY of the three perms is
# requested for the first time, ONE card fires and (if approved) ALL
# THREE become silent.
#
# Operations under calendar.write:
#   - insert_buffer()       -- add a travel buffer before an event (non-destructive)
#   - update_buffer()       -- modify a buffer (non-destructive)
#   - dismiss_event()       -- mark an event as no-attend / remove its buffer
#   - delete_buffer()       -- explicitly drop a previously inserted buffer
#   - delete_event() / remove_event()
#
# Destructive ops (dismiss/delete family) ALWAYS land in the calendar
# ledger regardless of grant state. The gate's CalendarMapsPermGate
# handles this -- callers do not need to remember. Pass the operation
# verb so the gate can route to the ledger sink.
#
# Graceful degradation on deny:
#     Reads remain available if calendar.read is granted independently;
#     writes silently no-op (agent "silence" on buffer insertion).

from __future__ import annotations

import logging
from typing import Any

from prisiragent_coworker.permissions.calendar_maps_gates import (
    CalendarMapsPermission,
    CalendarMapsPermGate,
    PermRequestResult,
)

__all__ = [
    "DESTRUCTIVE_OPERATIONS",
    "is_destructive",
    "is_granted",
    "request",
    "request_calendar_write",
    "with_default",
]

_LOGGER = logging.getLogger(__name__)

# Verbs considered destructive for calendar.write. The same set is
# mirrored (and enforced) in calendar_maps_gates.py
# (_CALENDAR_WRITE_DESTRUCTIVE_OPS). This re-export lets callers
# introspect the policy without reaching into the gates module.
DESTRUCTIVE_OPERATIONS: frozenset[str] = frozenset(
    {"dismiss_event", "delete_buffer", "delete_event", "remove_event"}
)


def is_destructive(operation: str | None) -> bool:
    """True iff this operation, if executed, will produce a ledger entry."""
    return bool(operation) and operation in DESTRUCTIVE_OPERATIONS


_DEFAULT_GATE: CalendarMapsPermGate | None = None


def with_default(gate: CalendarMapsPermGate) -> None:
    """Install the module-level default gate. Idempotent."""
    global _DEFAULT_GATE
    _DEFAULT_GATE = gate
    _LOGGER.info("calendar_write: default gate installed")


def _gate() -> CalendarMapsPermGate:
    if _DEFAULT_GATE is None:
        raise RuntimeError(
            "calendar_write: no default gate installed; "
            "call with_default(gate) at app startup"
        )
    return _DEFAULT_GATE


def is_granted() -> bool:
    """Return True iff calendar.write has been granted."""
    if _DEFAULT_GATE is None:
        return False
    return _DEFAULT_GATE.grant_store.is_granted(CalendarMapsPermission.CALENDAR_WRITE)


def request(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    """Request calendar.write permission via the default gate.

    ``operation`` MUST be set for destructive verbs (dismiss_event,
    delete_buffer, delete_event, remove_event) so the gate can write
    the ledger record.
    """
    if is_destructive(operation):
        _LOGGER.debug(
            "calendar_write: destructive op=%s -> ledger will be written",
            operation,
        )
    return _gate().request(
        CalendarMapsPermission.CALENDAR_WRITE,
        operation=operation,
        context=context,
    )


def request_calendar_write(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    return request(operation, context=context)