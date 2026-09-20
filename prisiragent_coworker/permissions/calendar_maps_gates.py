# SPDX-License-Identifier: MIT
#
# Copyright (c) 2026 Prisiragent Project Contributors
#
# Derived from OpenWorker: no upstream counterpart (new file).
#   Original file:    (none)
#   Original author:  Prisiragent Project Contributors
#   Original license: MIT (see ../../LICENSE)
#
# This file is dual-licensed under the MIT License (see ../../LICENSE).
#
# Permission gate for calendar.read / calendar.write / maps.query (task #8).
#
# Design notes (per spec, 2026-09-19):
#   - Three permissions share ONE first-time grant flow. The user authorizes
#     once -> all three become silent forever. They are NOT three separate
#     cards fired in sequence.
#   - Risk tier is per-permission (calendar.write is "high" because writes
#     mutate the user's calendar); calendar.read / maps.query are "medium".
#   - Grant state persists to ~/.prisIr/perm_grants.json (existing
#     convention, also used by maps_keys.json). Atomic write via tmp + rename.
#   - "calendar.write" destructive operations (delete / dismiss) ALWAYS land
#     in the calendar ledger regardless of grant state -- the ledger is the
#     user's audit trail ("why did I have that buffer on Tuesday?"). See
#     prisIr_calendar.ledger for the storage layer; this module only emits
#     the records through the injected sink.
#   - Graceful degradation on grant denial:
#       * calendar.read denied   -> travel-buffer workflow disabled; agent
#         surfaces "无法安排交通缓冲" verbatim to user.
#       * calendar.write denied  -> agent reads only (calendar.read unaffected
#         if that grant is independent). Writes silently no-op; equivalent to
#         "agent silence on buffer insertion".
#       * maps.query denied      -> agent falls back to local heuristics
#         (e.g. rough taxi estimate) or surfaces a "无法估算通勤时间" message.
#
# Anti-flattery boundary:
#   - No `import openworker`.
#   - No vendor LLM SDK imports.
#   - No HTTP calls -- this is purely a local permission gate. The maps /
#     calendar services themselves live in prisIr_calendar.tools and are wired
#     into the agent separately; this module only brokers the user-facing
#     consent card.

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "CalendarMapsPermGate",
    "CalendarMapsPermission",
    "LedgerSink",
    "PermGrantStore",
    "PermRequestResult",
    "RiskTier",
    "UserPromptSink",
    "perm_grants_path",
]


# ----------------------------------------------------------------------
# Public constants
# ----------------------------------------------------------------------

# Default grant store location. ~/.prisIr/perm_grants.json follows the
# existing pattern (~/.prisIr/maps_keys.json for vendor credentials).
_DEFAULT_GRANTS_DIR = Path.home() / ".prisIr"
_DEFAULT_GRANTS_FILE = "perm_grants.json"


def perm_grants_path() -> Path:
    """Return the canonical grant store path.

    Honors ``PRISIR_PERM_GRANTS`` env var for test/dev overrides; otherwise
    returns ``~/.prisIr/perm_grants.json``. Creates the parent dir on first
    access via PermGrantStore (not here -- keeps the helper pure).
    """
    override = os.environ.get("PRISIR_PERM_GRANTS")
    if override:
        return Path(override)
    return _DEFAULT_GRANTS_DIR / _DEFAULT_GRANTS_FILE


RiskTier = Literal["medium", "high"]


class CalendarMapsPermission(str, Enum):
    """The three permission names under the shared first-time grant flow."""

    CALENDAR_READ = "calendar.read"
    CALENDAR_WRITE = "calendar.write"
    MAPS_QUERY = "maps.query"


# Human-readable copy for the FIRST-TIME card only. After the user clicks
# "同意", the card never reappears (silent forever).
# Tone: tell the user *why* the agent needs the data and where it lives.
_FIRST_TIME_COPY: dict[CalendarMapsPermission, dict[str, str]] = {
    CalendarMapsPermission.CALENDAR_READ: (
        "PrisirAI 需要读你的日程来安排交通缓冲,"
        "数据只在本地,不会上传。"
    ),
    CalendarMapsPermission.CALENDAR_WRITE: (
        "PrisirAI 会自动插交通缓冲,你删 = 告诉 agent 不要。"
        "所有写入会记到本地 ledger,便于回看。"
    ),
    CalendarMapsPermission.MAPS_QUERY: (
        "PrisirAI 调高德/Google 算通勤时间,key 存本地,不会外发其他用途。"
    ),
}

_RISK_TIER: dict[CalendarMapsPermission, RiskTier] = {
    CalendarMapsPermission.CALENDAR_READ: "medium",
    CalendarMapsPermission.CALENDAR_WRITE: "high",
    CalendarMapsPermission.MAPS_QUERY: "medium",
}


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class PermRequestResult:
    """Outcome of a single permission request.

    Attributes:
        permission: The permission that was requested.
        granted: True iff the request was allowed. ``False`` means either
            denied by user or timed out.
        reason: Free-text rationale for logs ("user approved", "user denied",
            "first-time consent", "no prompt sink; assumed grant").
        via_first_time_card: True iff this request was the first-time card
            (i.e. the user was actually prompted via the card UI). Useful
            for analytics ("how many users hit the first-time card?").
        ledger_record_id: For calendar.write destructive ops, the ledger
            record ID emitted (so callers can correlate). None otherwise.
    """

    permission: CalendarMapsPermission
    granted: bool
    reason: str
    via_first_time_card: bool = False
    ledger_record_id: str | None = None


# ----------------------------------------------------------------------
# Sinks (injected; tests stub these out)
# ----------------------------------------------------------------------


@runtime_checkable
class UserPromptSink(Protocol):
    """Renders the first-time consent card and blocks for user response.

    Contract:
      * Receives ``(permissions: list[str], copy_by_perm: dict[str, str],
        risk_tiers: dict[str, str], request_id: str)``.
      * Returns ``True`` for "user approved all" or ``False`` for denied /
        timed-out (the underlying UI decides its own timeout; we never
        hang indefinitely).
      * MUST be safe to call from the chat thread -- the existing perm
        gate UI in ``prisIragent_web._perm_on_confirm`` already meets this.

    The sink is invoked AT MOST ONCE per user (the first time ANY of the
    three permissions is touched). After that, persisted grants are reused
    without ever calling the sink again.
    """

    def __call__(
        self,
        permissions: list[str],
        copy_by_perm: dict[str, str],
        risk_tiers: dict[str, str],
        request_id: str,
    ) -> bool: ...


@runtime_checkable
class LedgerSink(Protocol):
    """Append-only ledger writer for calendar.write destructive operations.

    Receives ``(record: dict)``; MUST be best-effort (never raises; the
    permission decision must not fail just because the ledger is down).

    Implementation note: the real sink in production points at
    ``prisIr_calendar.ledger.append``. Tests pass an in-memory list.
    """

    def __call__(self, record: dict[str, Any]) -> None: ...


# ----------------------------------------------------------------------
# Grant store
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class GrantRecord:
    """Persisted grant metadata for one permission.

    Attributes:
        granted_at: UTC timestamp of the user's first-time approval.
        request_id: The first-time card request_id that produced this
            grant (for forensic correlation in logs).
        risk_tier_at_freeze: The risk tier shown on the card. Frozen so
            future spec changes don't retroactively re-classify.
    """

    granted_at: str  # ISO-8601 UTC
    request_id: str
    risk_tier_at_freeze: RiskTier


class PermGrantStore:
    """JSON-backed persistence for first-time grants.

    File format (single JSON object):
        {
            "version": 1,
            "grants": {
                "calendar.read":  {"granted_at": "...", "request_id": "...",
                                   "risk_tier_at_freeze": "medium"},
                "calendar.write": {...},
                "maps.query":     {...}
            }
        }

    Concurrency:
        All writes go through ``_lock``. The store is the SINGLE writer;
        reader/writer races are resolved by the lock + atomic rename.

    Failure mode:
        A read that hits a malformed file (truncated, bad JSON) is treated
        as "no grants" -- the first-time card will re-appear on next call.
        The original file is preserved at ``perm_grants.json.corrupt.<ts>``
        so the user can recover if desired.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = (path or perm_grants_path()).resolve()
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def is_granted(self, perm: CalendarMapsPermission) -> bool:
        """Return True iff the persisted grants file contains this permission."""
        with self._lock:
            data = self._read_unlocked()
        return perm.value in data.get("grants", {})

    def grants(self) -> dict[str, GrantRecord]:
        """Return all currently persisted grants as a name -> record dict."""
        with self._lock:
            data = self._read_unlocked()
        out: dict[str, GrantRecord] = {}
        for name, raw in data.get("grants", {}).items():
            try:
                out[name] = GrantRecord(
                    granted_at=str(raw["granted_at"]),
                    request_id=str(raw["request_id"]),
                    risk_tier_at_freeze=raw["risk_tier_at_freeze"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning(
                    "perm_grants: dropping malformed record for %s (%s)",
                    name, exc,
                )
        return out

    def record_grant(
        self,
        perm: CalendarMapsPermission,
        *,
        request_id: str,
        risk_tier: RiskTier,
    ) -> GrantRecord:
        """Persist a first-time approval; idempotent.

        Returns the GrantRecord that is now on disk (whether we wrote it
        just now or it was already there).
        """
        now = datetime.now(UTC).isoformat()
        record = GrantRecord(
            granted_at=now,
            request_id=request_id,
            risk_tier_at_freeze=risk_tier,
        )
        with self._lock:
            data = self._read_unlocked()
            existing = data.get("grants", {}).get(perm.value)
            if existing and all(
                k in existing for k in ("granted_at", "request_id", "risk_tier_at_freeze")
            ):
                # Idempotent: keep the original record, return it.
                return GrantRecord(
                    granted_at=str(existing["granted_at"]),
                    request_id=str(existing["request_id"]),
                    risk_tier_at_freeze=existing["risk_tier_at_freeze"],
                )
            data.setdefault("grants", {})[perm.value] = {
                "granted_at": record.granted_at,
                "request_id": record.request_id,
                "risk_tier_at_freeze": record.risk_tier_at_freeze,
            }
            data["version"] = 1
            self._write_unlocked(data)
        return record

    # ---- internals ----

    def _read_unlocked(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "grants": {}}
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            _LOGGER.warning("perm_grants: cannot read %s (%s)", self._path, exc)
            return {"version": 1, "grants": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._quarantine_corrupt(raw, exc)
            return {"version": 1, "grants": {}}
        if not isinstance(data, dict):
            _LOGGER.warning(
                "perm_grants: top level is %s, not a dict; treating as empty",
                type(data).__name__,
            )
            return {"version": 1, "grants": {}}
        data.setdefault("grants", {})
        return data

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        # Atomic-ish: write to tmp + os.replace. On Windows, os.replace
        # works across the same volume; the parent dir was created in
        # __init__ so this is safe.
        tmp = self._path.with_suffix(self._path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    def _quarantine_corrupt(self, raw: str, exc: Exception) -> None:
        try:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            corrupt_path = self._path.with_name(
                f"{self._path.name}.corrupt.{stamp}"
            )
            corrupt_path.write_text(raw, encoding="utf-8")
            _LOGGER.warning(
                "perm_grants: file %s is malformed (%s); "
                "quarantined to %s; treating as empty",
                self._path, exc, corrupt_path,
            )
        except OSError as qe:
            _LOGGER.warning(
                "perm_grants: file %s is malformed (%s) AND quarantine "
                "failed (%s); treating as empty without preserving copy",
                self._path, exc, qe,
            )


# ----------------------------------------------------------------------
# Gate
# ----------------------------------------------------------------------


# Operations under calendar.write that are *destructive* and thus must
# land in the ledger even after the user has granted calendar.write.
# This is the spec requirement: "calendar.write 即使首次授权后,涉及
# '删除'类操作仍要日志到 ledger".
_CALENDAR_WRITE_DESTRUCTIVE_OPS: frozenset[str] = frozenset(
    {"dismiss_event", "delete_buffer", "delete_event", "remove_event"}
)


@dataclass
class CalendarMapsPermGate:
    """Shared first-time gate for calendar.read / calendar.write / maps.query.

    Constructor args:
        grant_store: Where first-time grants live. Tests inject a tmp path
            via ``PermGrantStore(tmp_path / "perm_grants.json")``.
        user_prompt: Called exactly once across the lifetime of the three
            permissions -- the very first time ANY of them is touched.
            If None, the gate assumes approval (suitable for non-UI tests
            and for headless/CI runs where no card exists).
        ledger: Sink for calendar.write destructive ops. If None, those
            records are logged but not persisted (suitable for tests).
        now_fn: Clock for ledger timestamps; injectable for tests.

    Thread safety:
        ``_lock`` serializes first-time card invocations. The store has
        its own internal lock so concurrent ``is_granted`` calls don't
        see torn writes.
    """

    grant_store: PermGrantStore
    user_prompt: UserPromptSink | None = None
    ledger: LedgerSink | None = None
    now_fn: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(UTC)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _first_time_consumed: bool = field(default=False, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request(
        self,
        perm: CalendarMapsPermission,
        *,
        operation: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> PermRequestResult:
        """Ask for permission to perform ``operation`` under ``perm``.

        Semantics:
          1. If perm is already granted -> silent allow. No card fires.
          2. If perm is NOT granted AND no other perm has triggered the
             first-time card yet -> fire the card ONCE covering all three
             permissions. If the user approves, persist grants for ALL
             THREE (so the user never sees this card again). If denied,
             persist nothing and return deny for this perm.
          3. If perm is NOT granted AND another perm already triggered
             the card and was rejected -> return deny silently. We do NOT
             re-prompt for the remaining perms (the user's "no" applies
             to the whole batch).

        Args:
            perm: The permission being requested.
            operation: Optional verb (e.g. "list_events", "insert_buffer",
                "dismiss_event") for log correlation. Required when the
                caller wants destructive-write ledger correlation.
            context: Free-form dict attached to the ledger record for
                destructive calendar.write ops. Ignored otherwise.

        Returns:
            PermRequestResult; see class docstring.
        """
        ctx = context or {}

        # Fast path: already granted -> silent allow.
        if self.grant_store.is_granted(perm):
            ledger_id = self._maybe_log_destructive(perm, operation, ctx)
            return PermRequestResult(
                permission=perm,
                granted=True,
                reason="grant present; silent allow",
                via_first_time_card=False,
                ledger_record_id=ledger_id,
            )

        # Slow path: needs first-time card. Serialize across perms.
        with self._lock:
            # Re-check inside the lock (another thread may have granted
            # while we were waiting).
            if self.grant_store.is_granted(perm):
                ledger_id = self._maybe_log_destructive(perm, operation, ctx)
                return PermRequestResult(
                    permission=perm,
                    granted=True,
                    reason="grant present (post-lock recheck); silent allow",
                    via_first_time_card=False,
                    ledger_record_id=ledger_id,
                )

            if self._first_time_consumed:
                # Another perm already prompted and was either approved
                # or denied. If approved, is_granted above would have
                # caught it; landing here means a prior perm was
                # rejected. Silent deny.
                return PermRequestResult(
                    permission=perm,
                    granted=False,
                    reason=(
                        "first-time card was already consumed for a prior "
                        "perm and was not approved; silent deny"
                    ),
                    via_first_time_card=False,
                )

            # Fire the FIRST-TIME card (covers all three perms).
            request_id = uuid.uuid4().hex[:12]
            approved = self._prompt_first_time(request_id)
            self._first_time_consumed = True

            if approved:
                self._persist_grants_for_all(request_id)
                ledger_id = self._maybe_log_destructive(perm, operation, ctx)
                return PermRequestResult(
                    permission=perm,
                    granted=True,
                    reason="first-time card approved; grants persisted",
                    via_first_time_card=True,
                    ledger_record_id=ledger_id,
                )
            # Denied / timed out -- nothing persisted, this perm denied.
            return PermRequestResult(
                permission=perm,
                granted=False,
                reason="first-time card not approved",
                via_first_time_card=True,
            )

    def first_time_card_payload(self) -> dict[str, Any]:
        """Return the payload the front-end should render for the card.

        Tests use this to assert the copy. Production UI code calls
        ``request()`` and lets the gate drive ``user_prompt`` internally.

        Returns:
            {
              "permissions": ["calendar.read", "calendar.write", "maps.query"],
              "copy":       {"calendar.read": "...", ...},
              "risk_tiers": {"calendar.read": "medium", ...}
            }
        """
        return {
            "permissions": [p.value for p in CalendarMapsPermission],
            "copy": {p.value: _FIRST_TIME_COPY[p] for p in CalendarMapsPermission},
            "risk_tiers": {p.value: _RISK_TIER[p] for p in CalendarMapsPermission},
        }

    def risk_tier(self, perm: CalendarMapsPermission) -> RiskTier:
        return _RISK_TIER[perm]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _prompt_first_time(self, request_id: str) -> bool:
        """Invoke the user_prompt sink ONCE; return True iff approved.

        If no sink is wired (headless / tests), default to APPROVE -- the
        alternative would deny a real user, which is worse than asking.
        Tests that want "no sink => deny" should pass an explicit stub.
        """
        if self.user_prompt is None:
            _LOGGER.info(
                "perm_gate: no user_prompt sink wired; assuming approval "
                "(request_id=%s)", request_id,
            )
            return True
        payload = self.first_time_card_payload()
        try:
            return bool(self.user_prompt(
                permissions=payload["permissions"],
                copy_by_perm=payload["copy"],
                risk_tiers=payload["risk_tiers"],
                request_id=request_id,
            ))
        except Exception as exc:  # noqa: BLE001 -- sink must not break verdict
            _LOGGER.warning(
                "perm_gate: user_prompt sink raised %s; treating as denial "
                "for safety (request_id=%s)", exc, request_id,
            )
            return False

    def _persist_grants_for_all(self, request_id: str) -> None:
        """Record grants for ALL THREE permissions in one shot.

        This is the "一次弹卡 = 三档全开" contract. Even though the user
        clicked the card while asking for, say, calendar.read, we persist
        grants for calendar.write and maps.query too -- they will never
        see a card for those.
        """
        for perm in CalendarMapsPermission:
            self.grant_store.record_grant(
                perm,
                request_id=request_id,
                risk_tier=_RISK_TIER[perm],
            )

    def _maybe_log_destructive(
        self,
        perm: CalendarMapsPermission,
        operation: str | None,
        context: dict[str, Any],
    ) -> str | None:
        """Emit a ledger entry iff this is a destructive calendar.write op.

        Returns the ledger record id if one was emitted, else None. The id
        is a synthetic UUID hex that the calendar ledger can use to
        dedupe if needed.
        """
        if perm is not CalendarMapsPermission.CALENDAR_WRITE:
            return None
        if not operation:
            return None
        if operation not in _CALENDAR_WRITE_DESTRUCTIVE_OPS:
            return None
        record_id = uuid.uuid4().hex
        record = {
            "id": record_id,
            "ts": self.now_fn().isoformat(),
            "permission": perm.value,
            "operation": operation,
            "context": dict(context),
            "note": (
                "destructive calendar.write operation; ledger entry "
                "emitted by perm_gate regardless of grant state"
            ),
        }
        if self.ledger is None:
            _LOGGER.warning(
                "perm_gate: no ledger sink wired; destructive op "
                "%s would have been logged: %s",
                operation, record,
            )
            return record_id
        try:
            self.ledger(record)
        except Exception as exc:  # noqa: BLE001 -- ledger is best-effort
            _LOGGER.warning(
                "perm_gate: ledger sink raised %s for op=%s; "
                "decision unaffected", exc, operation,
            )
        return record_id