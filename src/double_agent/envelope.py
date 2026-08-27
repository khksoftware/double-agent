# SPDX-License-Identifier: Apache-2.0
"""The dispatch envelope: the contract, carried inside the brief.

**The one thing a platform reliably does not record is what a dispatch was FOR, in a
machine-readable shape.** It records the brief verbatim; it has no idea which part of that
text is the contract and which part is context.

So the envelope is **a fenced block inside the brief itself, not a companion file.** That is
the load-bearing decision here, and it follows from one property: a platform that records a
brief verbatim carries the envelope for free, with no second artifact to keep in step, no
write path, and no way for the two to disagree. A companion file would need all three.

## There is no `supervisor` field, and its absence is a security property

An earlier form of this contract declared who was entitled to send control signals. **That
field is deleted and must not come back.**

> A declared identity is the one leg an attacker can dress up. Nothing in the brief declares
> who the supervisor is, so nothing in the brief can lie about it.

**Entitlement is structural** -- derived from the platform's own lineage tree, which the
dispatching party does not write -- and never declared. See :mod:`double_agent.entitlement`.
Anything that reintroduces a declared authority into this block reintroduces the hole.

## `external_wait` declares, and a declaration is not a transition

The field exists because the inactivity regime's extension reads *"only where the envelope
declares a recorded operation-specific external wait"*, and without the field that condition
was unsatisfiable in either direction -- which is a trap rather than a gap, since evaluating
it as never-satisfied shortens the bound and as always-satisfied lengthens it.

**It cannot be minted to dodge a deadline**, and that is what keeps it out of supervisor
discretion: the envelope is written at dispatch, before any signal exists, so the extension
is computed from a contract fixed before there was anything to extend.

**And it does NOT supply the inactivity clock's external-wait reset.** A *declaration* is not
a *transition*: nothing observable marks the node entering the declared wait, and the only
observable that would is an emission by the node -- which is precisely the
purchasable-exemption shape the acknowledgement bound was corrected to remove. The residual
is narrowed, not closed, and it fails safe: a genuine long external wait reaches `stalled`,
which **blocks** a closure rather than authorizing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

__all__ = [
    "ENVELOPE_FIELDS",
    "Envelope",
    "EnvelopeError",
    "ExternalWait",
    "FENCE_MARKER",
    "control_record_reference",
    "parse_envelope",
    "render_envelope",
]

FENCE_MARKER = "double-agent-envelope"
"""The first line of the block. Deliberately the framework's own name and nothing else."""

ENVELOPE_FIELDS: Tuple[str, ...] = (
    "assigned_outcome",
    "durable_cursor",
    "checkpoints",
    "heartbeat_seconds",
    "role_label",
    "external_wait",
    "control_record",
)
"""Seven. There is no eighth, and the one that was removed is described in this module's
own documentation rather than left as an absence a reader has to notice."""

CONTROL_RECORD_KINDS: Tuple[str, ...] = ("relay", "adoption", "reach", "abandonment")
"""The four records that resolve under ``control_record``.

Each exists for the same reason: **the platform writes nothing that records them.** A
cooperatively cancelled node reports itself completed, an adoption is a decision rather than
an event, a spent transitive reach is an assertion, and an abandonment is a supervisory act.
A party writes each one durably or the corresponding field is a bare declaration.
"""

_EXTERNAL_WAIT_RE = re.compile(
    r"^(?P<operation>.+?)\s+for\s+up\s+to\s+(?P<seconds>\d+)\s+seconds?$", re.IGNORECASE
)


class EnvelopeError(ValueError):
    """A brief's envelope is absent, malformed, or declares something it may not."""


@dataclass(frozen=True)
class ExternalWait:
    """A declared, operation-specific external wait.

    **Operation-specific by shape rather than by judgement**: it names the operation and a
    bound in seconds, or it does not exist. A bare affirmative is not a declaration, and
    :func:`parse_envelope` refuses one rather than interpreting it generously.
    """

    operation: str
    bound_seconds: int

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise EnvelopeError("an external wait must name the operation")
        if self.bound_seconds <= 0:
            raise EnvelopeError(
                f"an external wait must declare a positive bound, not {self.bound_seconds}"
            )

    def render(self) -> str:
        return f"{self.operation} for up to {self.bound_seconds} seconds"


@dataclass(frozen=True)
class Envelope:
    """One dispatch's contract."""

    assigned_outcome: str
    durable_cursor: str
    checkpoints: Tuple[str, ...]
    heartbeat_seconds: int
    role_label: str
    control_record: str
    external_wait: Optional[ExternalWait] = None

    @property
    def declares_external_wait(self) -> bool:
        """Whether the inactivity regime's extension is available for this dispatch.

        Read by the clock. **Not** a claim that the node is currently waiting.
        """
        return self.external_wait is not None


def control_record_reference(envelope: Envelope, kind: str, handle: str) -> str:
    """Where one control record for one node resolves.

    ``handle`` is the node's platform handle. The reference is repository-relative and is
    resolved through the platform, never by this package: whether it exists is
    :meth:`ports.Platform.reference_state`'s answer, and a reference that does not resolve
    means the record was declared and not written.
    """
    if kind not in CONTROL_RECORD_KINDS:
        raise EnvelopeError(
            f"unknown control record kind {kind!r}; expected one of {list(CONTROL_RECORD_KINDS)}"
        )
    root = envelope.control_record.rstrip("/")
    return f"{root}/{handle}.{kind}.json"


def render_envelope(envelope: Envelope) -> str:
    """Emit the block, for inclusion verbatim in a brief."""
    lines = [
        FENCE_MARKER,
        f"assigned_outcome:  {envelope.assigned_outcome}",
        f"durable_cursor:    {envelope.durable_cursor}",
        f"checkpoints:       {', '.join(envelope.checkpoints)}",
        f"heartbeat_seconds: {envelope.heartbeat_seconds}",
        f"role_label:        {envelope.role_label}",
        f"external_wait:     {envelope.external_wait.render() if envelope.external_wait else ''}",
        f"control_record:    {envelope.control_record}",
    ]
    return "\n".join(lines)


def _extract_block(text: str) -> Sequence[str]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == FENCE_MARKER]
    if not starts:
        raise EnvelopeError(
            f"no envelope in this brief: no line reads {FENCE_MARKER!r}. A dispatch without an "
            f"envelope has no contract, and this is not treated as an empty contract."
        )
    if len(starts) > 1:
        raise EnvelopeError(
            f"{len(starts)} envelopes in one brief. Which one is the contract is not a "
            f"judgement this package will make on a caller's behalf."
        )

    block = []
    for line in lines[starts[0] + 1 :]:
        if not line.strip():
            break
        if ":" not in line:
            break
        block.append(line)
    return block


def parse_envelope(text: str) -> Envelope:
    """Read the envelope out of a brief the platform recorded verbatim.

    Refuses rather than defaults. A missing field is named; an unknown field is named; a
    malformed external wait is named. **Nothing is inferred**, because every value here is
    part of a contract and a guessed contract term is worse than a refusal.
    """
    values: dict = {}
    for line in _extract_block(text):
        key, _, raw = line.partition(":")
        key = key.strip()
        if key not in ENVELOPE_FIELDS:
            raise EnvelopeError(
                f"unknown envelope field {key!r}. The contract has exactly "
                f"{len(ENVELOPE_FIELDS)} fields: {list(ENVELOPE_FIELDS)}. In particular there "
                f"is no field declaring who may send control signals -- entitlement is "
                f"structural, and a declared identity is the one leg an attacker can dress up."
            )
        if key in values:
            raise EnvelopeError(f"envelope field {key!r} appears twice")
        values[key] = raw.strip()

    missing = [f for f in ENVELOPE_FIELDS if f not in values]
    if missing:
        raise EnvelopeError(f"envelope is missing required fields: {missing}")

    try:
        heartbeat = int(values["heartbeat_seconds"])
    except ValueError:
        raise EnvelopeError(
            f"heartbeat_seconds must be an integer, not {values['heartbeat_seconds']!r}"
        ) from None
    if heartbeat <= 0:
        raise EnvelopeError(f"heartbeat_seconds must be positive, not {heartbeat}")

    raw_wait = values["external_wait"]
    wait: Optional[ExternalWait] = None
    if raw_wait:
        match = _EXTERNAL_WAIT_RE.match(raw_wait.replace("—", "--").strip("- "))
        if match is None:
            raise EnvelopeError(
                f"external_wait must name an operation and a bound -- "
                f"'<operation> for up to <integer> seconds' -- not {raw_wait!r}. A bare "
                f"affirmative is not a declaration, and this is refused rather than read "
                f"generously because the alternative is a bound nobody set."
            )
        wait = ExternalWait(
            operation=match.group("operation").strip(),
            bound_seconds=int(match.group("seconds")),
        )

    for required in ("assigned_outcome", "durable_cursor", "role_label", "control_record"):
        if not values[required]:
            raise EnvelopeError(f"envelope field {required!r} is present but empty")

    checkpoints = tuple(c.strip() for c in values["checkpoints"].split(",") if c.strip())

    return Envelope(
        assigned_outcome=values["assigned_outcome"],
        durable_cursor=values["durable_cursor"],
        checkpoints=checkpoints,
        heartbeat_seconds=heartbeat,
        role_label=values["role_label"],
        control_record=values["control_record"],
        external_wait=wait,
    )
