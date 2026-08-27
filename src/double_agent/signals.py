# SPDX-License-Identifier: Apache-2.0
"""The signal protocol: one channel, five named shapes, and an honest limit.

## The limit, stated before the protocol, because it governs everything below

**A signal to a node that is still running cannot be adjudicated.** The reason is
structural rather than a gap in this implementation: a signal is only provable if the
platform recorded it **on the recipient's side**, with a sender identity the sender does
not control -- and on most platforms that record is written at a *resume boundary*, which by
definition only exists for a node that has already stopped.

So the operationally useful cancel -- the one you send to a worker that is *currently doing
the wrong thing* -- is **permanently advisory**. It works. It is not provable.

**The consequence this module enforces:** non-compliance with an advisory signal produces an
**obstacle report**. Only non-compliance with an adjudicable signal may be attributed to the
recipient as **defiance**. That distinction is not politeness; it is the difference between
"we asked and it did not happen" and "we can show it was told and refused", and collapsing
them manufactures an accusation out of an absence of evidence.

A platform where *every* signal is advisory gets the whole protocol and **no defiance
attribution at all**. That is the correct outcome and it is stated rather than worked around.

## Five shapes, and one of them is not a message

| shape | direction | carries, at minimum |
| --- | --- | --- |
| ``cancel`` | supervisor to node | the target handle and the reason |
| ``suspend`` | supervisor to node | the handle and the durable artifact to produce before stopping |
| ``override`` | supervisor to node | the handle and the answer that was owed |
| ``hazard`` | **node to supervisor** | the node's own handle and the specific risk the stop is claimed to increase |
| ``status`` | **never sent** | nothing -- it is a read on the supervisor's side, not a message |

``status`` being a read rather than a message is the point rather than an omission: **there
is nothing to adjudicate**, because nothing was asked of anybody. This module refuses to
construct one.

``hazard`` is the only shape travelling upward, and it is **relayed, never adjudicated** --
it is the node's argument, not the supervisor's report about the node.

``override`` **carries the answer that was owed or it is not sent.** An override that
re-issues an instruction without answering the argument raised against it is the instruction
again, louder.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from .ports import Platform, SignalOutcome, UnsupportedCapability

__all__ = [
    "Adjudication",
    "NonCompliance",
    "SIGNAL_TOKEN",
    "Signal",
    "SignalError",
    "SignalShape",
    "adjudicate",
    "parse_signal",
    "send",
]

SIGNAL_TOKEN = "double-agent-signal"
"""The wire token. Deliberately the framework's own name.

**A protocol token cannot be renamed after anyone implements against it**, so this is the
one string in the package where host bookkeeping would have been permanent.
"""

_SIGNAL_RE = re.compile(rf"^{re.escape(SIGNAL_TOKEN)}:\s*(?P<shape>[a-z]+)\s*$", re.MULTILINE)


class SignalError(ValueError):
    """A signal is malformed, or is being constructed in a shape that is not a message."""


class SignalShape(Enum):
    """The four shapes that are actually sent. ``status`` is deliberately absent."""

    CANCEL = "cancel"
    SUSPEND = "suspend"
    OVERRIDE = "override"
    HAZARD = "hazard"

    @property
    def upward(self) -> bool:
        """Whether this shape travels from the node to its supervisor."""
        return self is SignalShape.HAZARD

    @property
    def adjudicated(self) -> bool:
        """Whether non-compliance with this shape is ever attributable.

        ``hazard`` is **relayed, never adjudicated**: it is the node's argument, and an
        argument is answered rather than enforced.
        """
        return not self.upward


class NonCompliance(Enum):
    """What non-compliance with a signal may be recorded as."""

    OBSTACLE_REPORT = "obstacle_report"
    """We asked, and it did not happen. **Attributes nothing to the recipient.**"""

    DEFIANCE = "defiance"
    """We can show it was told and did not comply. Available only for an adjudicable
    signal."""

    NOT_APPLICABLE = "not_applicable"
    """Nothing was asked of anybody -- an upward shape, or a read."""


@dataclass(frozen=True)
class Signal:
    """One signal, in one shape, about one node."""

    shape: SignalShape
    handle: str
    reason: str = ""
    durable_artifact: str = ""
    answer: str = ""
    risk: str = ""

    def __post_init__(self) -> None:
        if not self.handle.strip():
            raise SignalError("a signal must name the node it is about")

        if self.shape is SignalShape.CANCEL and not self.reason.strip():
            raise SignalError(
                "a cancel must carry its reason. A stop with no stated reason cannot be "
                "argued against, and the argument is the only protection the recipient has."
            )
        if self.shape is SignalShape.SUSPEND and not self.durable_artifact.strip():
            raise SignalError(
                "a suspend must name the durable artifact the node produces before "
                "stopping. Without it, suspend and cancel are the same instruction and the "
                "work is lost rather than parked."
            )
        if self.shape is SignalShape.OVERRIDE and not self.answer.strip():
            raise SignalError(
                "an override must carry the answer that was owed. An override that re-issues "
                "the instruction without answering the argument raised against it is the "
                "instruction again, louder."
            )
        if self.shape is SignalShape.HAZARD and not self.risk.strip():
            raise SignalError(
                "a hazard must name the specific risk the stop instruction is claimed to "
                "increase. A general objection is not a hazard."
            )

    def render(self) -> str:
        lines = [f"{SIGNAL_TOKEN}: {self.shape.value}", f"handle: {self.handle}"]
        for name, value in (
            ("reason", self.reason),
            ("durable_artifact", self.durable_artifact),
            ("answer", self.answer),
            ("risk", self.risk),
        ):
            if value:
                lines.append(f"{name}: {value}")
        return "\n".join(lines)


def parse_signal(text: str) -> Signal:
    """Read a signal out of a message body.

    Refuses ``status`` explicitly rather than reporting an unknown shape, because a caller
    writing one has a real misunderstanding worth naming.
    """
    match = _SIGNAL_RE.search(text)
    if match is None:
        raise SignalError(
            f"no signal here: no line reads '{SIGNAL_TOKEN}: <shape>'. Ordinary prose is not "
            f"a control signal, and a recipient must be able to tell the difference."
        )
    raw = match.group("shape")
    if raw == "status":
        raise SignalError(
            "'status' is not a message and is never sent. It is a read on the supervisor's "
            "side, which is why there is nothing to adjudicate about it."
        )
    try:
        shape = SignalShape(raw)
    except ValueError:
        raise SignalError(
            f"unknown signal shape {raw!r}; expected one of "
            f"{[s.value for s in SignalShape]}"
        ) from None

    fields: dict = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in ("handle", "reason", "durable_artifact", "answer", "risk"):
            fields.setdefault(key, value.strip())

    if "handle" not in fields:
        raise SignalError("a signal must name the node it is about; no 'handle' line found")

    return Signal(shape=shape, **fields)


@dataclass(frozen=True)
class Adjudication:
    """What may be concluded from a signal and the recipient's response."""

    signal_shape: SignalShape
    adjudicable: bool
    non_compliance: NonCompliance
    basis: str

    @property
    def attributable(self) -> bool:
        return self.non_compliance is NonCompliance.DEFIANCE


def send(platform: Platform, signal: Signal) -> SignalOutcome:
    """Deliver one signal, and report whether the platform made it adjudicable.

    An upward shape is not delivered through this path: a node's argument travels in its own
    emitted text, where a reader already looks for it.
    """
    if signal.shape.upward:
        raise SignalError(
            f"{signal.shape.value!r} travels from the node to its supervisor and is read from "
            f"the node's own emitted text, not delivered through this channel."
        )
    caps = platform.capabilities
    payload: Mapping[str, object] = {
        "handle": signal.handle,
        "reason": signal.reason,
        "durable_artifact": signal.durable_artifact,
        "answer": signal.answer,
    }
    outcome = platform.send_signal(signal.handle, signal.shape.value, payload)
    if outcome.adjudicable and not caps.adjudicable_signals:
        raise UnsupportedCapability(
            "adjudicable_signals",
            "the platform reported an adjudicable delivery while declaring it cannot record "
            "a sender identity on the recipient's side; the two cannot both be true",
        )
    return outcome


def adjudicate(
    signal: Signal,
    outcome: SignalOutcome,
    *,
    complied: bool,
    recipient_was_running: bool,
) -> Adjudication:
    """Decide what non-compliance with this signal may be recorded as.

    ``recipient_was_running`` is the decisive input and the reason this function exists.
    A signal delivered to a running node is mid-turn: no recipient-side record is written,
    so nothing about it is provable **however clearly it was sent and however plainly it was
    ignored.**
    """
    if signal.shape.upward:
        return Adjudication(
            signal_shape=signal.shape,
            adjudicable=False,
            non_compliance=NonCompliance.NOT_APPLICABLE,
            basis=(
                f"{signal.shape.value!r} is the node's own argument travelling upward. It is "
                f"relayed and answered, never adjudicated -- nothing was asked of the node."
            ),
        )

    if complied:
        return Adjudication(
            signal_shape=signal.shape,
            adjudicable=outcome.adjudicable,
            non_compliance=NonCompliance.NOT_APPLICABLE,
            basis="the recipient complied; there is no non-compliance to characterise",
        )

    if recipient_was_running or not outcome.adjudicable:
        why = (
            "the recipient was still running, so the signal was mid-turn and no "
            "recipient-side record exists"
            if recipient_was_running
            else "the transport recorded no sender identity on the recipient's side"
        )
        return Adjudication(
            signal_shape=signal.shape,
            adjudicable=False,
            non_compliance=NonCompliance.OBSTACLE_REPORT,
            basis=(
                f"{why}, so this is advisory. Non-compliance is an OBSTACLE REPORT and may "
                f"not be attributed to the recipient as defiance -- an absence of evidence "
                f"is not evidence of refusal."
            ),
        )

    return Adjudication(
        signal_shape=signal.shape,
        adjudicable=True,
        non_compliance=NonCompliance.DEFIANCE,
        basis=(
            "the recipient had already stopped, so the signal was recorded on its side with "
            "a sender identity it does not control; non-compliance is attributable"
        ),
    )
