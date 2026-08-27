# SPDX-License-Identifier: Apache-2.0
"""The activity clock -- and the one thing it refuses to measure.

**Emission is not progress.** This is the whole content of this module and it is worth
stating before any code, because the comfortable implementation is wrong in a way that
cannot be seen from inside it.

The obvious inactivity clock is ``now - <most recent transcript entry>``. Every entry that
advances that maximum is output churn: a tool call, a tool result, a thought, a line of
text, an attachment. Not one of them is evidence that anything was accomplished. So the
obvious clock fails in both directions:

*False alive.* A node stuck in a retry loop emits a call-and-result pair every second. Its
age never exceeds a second, so it reads healthy forever. **A clock meant to catch
unproductive activity is blind to unproductive activity by construction** -- which is the
precise case it exists for.

*False stalled.* A node executing one genuine long operation emits nothing and reads
stalled, so a correctly-working node blocks a closure and gets a successor demanded of it.

Neither is repaired by choosing different numbers.

**What resets inactivity is a closed set of four**, and the framing is deliberately narrow:

1. **A validated completion** -- a declared checkpoint reaching an artifact that resolves.
2. **An advancing durable cursor** -- the declared cursor's content actually changing.
3. **A declared external-wait transition** -- entering or leaving a *declared*,
   operation-specific wait that names a bound.
4. **An owned-child delta** -- a child node acquiring a dispatch record, or a child's own
   reset advancing. This is a **record read, not a process probe**; nothing here asks the
   operating system anything.

Where none of that is derivable, this module returns :attr:`ClockState.UNKNOWN` and the
caller blocks. It does **not** fall back to emission. Falling back is the substitution that
turns an inactivity clock into a liveness clock while keeping the name.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable, Optional, Sequence, Tuple

__all__ = [
    "ClockState",
    "Regime",
    "Reset",
    "ResetKind",
    "Staleness",
    "assess",
    "derive_resets",
]


class ResetKind(Enum):
    """The four things that reset inactivity. There is no fifth."""

    VALIDATED_COMPLETION = "validated_completion"
    CURSOR_ADVANCE = "cursor_advance"
    EXTERNAL_WAIT_TRANSITION = "external_wait_transition"
    OWNED_CHILD_DELTA = "owned_child_delta"


class ClockState(Enum):
    """What the clock says. ``UNKNOWN`` is a real answer and never a default."""

    ALIVE = "alive"
    """Within the probe threshold, on evidence."""

    PROBE_DUE = "probe_due"
    """Past the probe threshold, not yet past the terminate threshold."""

    STALLED = "stalled"
    """Past the terminate threshold, on evidence."""

    UNKNOWN = "unknown"
    """**No reset evidence is derivable.** Not a synonym for alive, and not for stalled.

    A caller meeting this blocks and says why. Treating it as either of the other two is
    the failure this module exists to prevent.
    """


@dataclass(frozen=True)
class Regime:
    """The inactivity regime. One regime, never two over the same population.

    The defaults are the proportionate ones: probe suspected idleness near two minutes,
    terminate near five, and allow a *recorded operation-specific* external wait to extend
    to ten without that extension counting as productivity.

    **The extension is the regime's own carve-out, not a supervisor's discretion.** It
    applies only where a declared external wait exists; it is not something a caller may
    grant because the node seems busy.
    """

    probe_after: timedelta = timedelta(minutes=2)
    terminate_after: timedelta = timedelta(minutes=5)
    external_wait_extension: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        if not (self.probe_after <= self.terminate_after <= self.external_wait_extension):
            raise ValueError(
                "an inactivity regime must be ordered probe <= terminate <= extension; "
                f"got {self.probe_after}, {self.terminate_after}, {self.external_wait_extension}"
            )


@dataclass(frozen=True)
class Reset:
    """One instant at which inactivity was reset, and by what."""

    instant: datetime
    kind: ResetKind
    detail: str = ""


@dataclass(frozen=True)
class Staleness:
    """The clock's answer, with its own basis attached.

    ``basis`` names what the answer rests on, so a caller reporting a stall can say what
    evidence it had rather than asserting a number.
    """

    state: ClockState
    last_reset_at: Optional[datetime]
    age: Optional[timedelta]
    basis: str
    extended: bool = False
    """Whether the terminate threshold was extended by a declared external wait."""


def assess(
    resets: Iterable[Reset],
    *,
    now: datetime,
    regime: Optional[Regime] = None,
    external_wait_declared: bool = False,
    dispatched_at: Optional[datetime] = None,
) -> Staleness:
    """Read the clock from reset evidence alone.

    ``dispatched_at`` is accepted and is **not** treated as a reset. A node that has never
    reset has produced no evidence of progress, and the dispatch instant is evidence that
    it started rather than that it advanced. It is used only to say so in ``basis``.
    """
    regime = regime or Regime()
    ordered: Tuple[Reset, ...] = tuple(sorted(resets, key=lambda r: r.instant))

    if not ordered:
        return Staleness(
            state=ClockState.UNKNOWN,
            last_reset_at=None,
            age=None,
            basis=(
                "no reset evidence is derivable for this node"
                + (
                    "; it has a dispatch instant, which is evidence that it started and not "
                    "that it advanced"
                    if dispatched_at is not None
                    else ""
                )
            ),
        )

    last = ordered[-1]
    age = now - last.instant

    if age < timedelta(0):
        # `now` earlier than the last reset is not a small measurement -- it means the
        # clock's own inputs are broken (clock skew, a stale `now`, resets read out of
        # order). Falling through to the ordinary branches below reads a negative age as
        # smaller than every threshold and returns ALIVE, which delays a stall verdict on a
        # reading that was already nonsensical rather than surfacing that it is nonsensical.
        return Staleness(
            state=ClockState.UNKNOWN,
            last_reset_at=last.instant,
            age=age,
            basis=(
                f"'now' is earlier than the last reset instant ({last.kind.value}) by "
                f"{-age}; this clock cannot read a negative age as evidence of anything, "
                f"and reporting ALIVE over it would be exactly the substitution this "
                f"module refuses everywhere else"
            ),
        )

    terminate_at = regime.external_wait_extension if external_wait_declared else regime.terminate_after

    if age >= terminate_at:
        state = ClockState.STALLED
    elif age >= regime.probe_after:
        state = ClockState.PROBE_DUE
    else:
        state = ClockState.ALIVE

    basis = f"last reset was {last.kind.value}"
    if last.detail:
        basis = f"{basis} ({last.detail})"
    if external_wait_declared:
        basis = f"{basis}; terminate threshold extended by a declared external wait"

    return Staleness(
        state=state,
        last_reset_at=last.instant,
        age=age,
        basis=basis,
        extended=external_wait_declared,
    )


def derive_resets(
    *,
    checkpoint_states: Sequence[Tuple[str, bool, Optional[datetime]]] = (),
    cursor_state: Optional[Tuple[str, Optional[str], Optional[datetime]]] = None,
    prior_cursor_digest: Optional[str] = None,
    external_wait_transitions: Sequence[Tuple[datetime, str]] = (),
    child_deltas: Sequence[Tuple[datetime, str]] = (),
) -> Tuple[Reset, ...]:
    """Build reset evidence from stated inputs. Nothing is inferred from emission.

    Each argument corresponds to exactly one of the four resetting kinds, and each is
    supplied by the caller from the platform rather than read here, so this function stays
    a pure derivation with no platform knowledge in it.

    ``checkpoint_states``
        ``(reference, exists, last_changed)`` per declared checkpoint. A checkpoint that
        does not exist is not a completion. A checkpoint that exists but whose change
        instant the platform cannot supply **produces no reset** -- existence alone is not
        an instant, and dating it ``now`` would make every read reset the clock.
    ``cursor_state``
        ``(reference, content_digest, last_changed)`` for the declared durable cursor.
    ``prior_cursor_digest``
        the digest from the previous reading. A cursor resets inactivity only where its
        content **changed**; an unchanged cursor that merely still exists is not progress.
    ``external_wait_transitions``
        ``(instant, detail)`` for each declared transition.
    ``child_deltas``
        ``(instant, detail)`` for each owned-child record delta.
    """
    out = []

    for reference, exists, last_changed in checkpoint_states:
        if exists and last_changed is not None:
            out.append(
                Reset(
                    instant=last_changed,
                    kind=ResetKind.VALIDATED_COMPLETION,
                    detail=f"checkpoint {reference!r} resolves",
                )
            )

    if cursor_state is not None:
        reference, digest, last_changed = cursor_state
        advanced = (
            digest is not None
            and prior_cursor_digest is not None
            and digest != prior_cursor_digest
        )
        if advanced and last_changed is not None:
            out.append(
                Reset(
                    instant=last_changed,
                    kind=ResetKind.CURSOR_ADVANCE,
                    detail=f"durable cursor {reference!r} advanced",
                )
            )

    for instant, detail in external_wait_transitions:
        out.append(
            Reset(instant=instant, kind=ResetKind.EXTERNAL_WAIT_TRANSITION, detail=detail)
        )

    for instant, detail in child_deltas:
        out.append(Reset(instant=instant, kind=ResetKind.OWNED_CHILD_DELTA, detail=detail))

    return tuple(sorted(out, key=lambda r: r.instant))
