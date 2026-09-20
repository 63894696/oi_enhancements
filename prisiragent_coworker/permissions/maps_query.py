# SPDX-License-Identifier: MIT
#
# Copyright (c) 2026 Prisiragent Project Contributors
#
# This file is dual-licensed under the MIT License (see ../../LICENSE).
#
# Permission wrapper: maps.query (task #8).
#
# Thin shim over CalendarMapsPermGate for maps vendor calls (amap /
# Google). The vendor routing itself lives in
# ``prisIr_calendar.tools.maps_vendor``; this module only brokers the
# user-facing consent.
#
# Trigger verbs:
#     eta()        -- ETA between two coordinates / addresses
#     geocode()    -- address -> coordinates
#     reverse_geocode()  -- coordinates -> address (used by calendar to
#                           anchor a buffer at the right venue)
#
# Graceful degradation on deny:
#     Agent falls back to a rough local heuristic (e.g. a fixed 30-min
#     taxi estimate) and surfaces "无法估算通勤时间" only if the heuristic
#     is below a confidence threshold. The actual fallback policy is the
#     agent's, not the gate's.

from __future__ import annotations

import logging
from typing import Any

from prisiragent_coworker.permissions.calendar_maps_gates import (
    CalendarMapsPermission,
    CalendarMapsPermGate,
    PermRequestResult,
)

__all__ = ["is_granted", "request", "request_maps_query", "with_default"]

_LOGGER = logging.getLogger(__name__)

_DEFAULT_GATE: CalendarMapsPermGate | None = None


def with_default(gate: CalendarMapsPermGate) -> None:
    """Install the module-level default gate. Idempotent."""
    global _DEFAULT_GATE
    _DEFAULT_GATE = gate
    _LOGGER.info("maps_query: default gate installed")


def _gate() -> CalendarMapsPermGate:
    if _DEFAULT_GATE is None:
        raise RuntimeError(
            "maps_query: no default gate installed; "
            "call with_default(gate) at app startup"
        )
    return _DEFAULT_GATE


def is_granted() -> bool:
    if _DEFAULT_GATE is None:
        return False
    return _DEFAULT_GATE.grant_store.is_granted(CalendarMapsPermission.MAPS_QUERY)


def request(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    """Request maps.query permission via the default gate."""
    return _gate().request(
        CalendarMapsPermission.MAPS_QUERY,
        operation=operation,
        context=context,
    )


def request_maps_query(
    operation: str | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> PermRequestResult:
    return request(operation, context=context)