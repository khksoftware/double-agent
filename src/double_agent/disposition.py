# SPDX-License-Identifier: Apache-2.0
"""The disposition classifier: five states, two annotations, and one refusal.

**The refusal is the point of this module**, so it goes first.

A node that was cancelled cooperatively reports itself **completed**. The platform writes
nothing anywhere that says a cancel happened. So if "this outcome was deliberately
abandoned" is allowed to be a *declaration*, then abandoning an outcome and completing it
produce the identical governance record, and the honest exit becomes indistinguishable from
success. **This module refuses to accept an abandonment that is not backed by a record that
resolves** -- not on the escalation path, not on the plain cancel path, not anywhere.

That is not a general suspicion of supervisors. It is the same rule this design already
applies to ownership: a field whose only evidence is somebody saying so is a bare
declaration, and a bare declaration is worth exactly nothing once the thing it asserts is
the thing under dispute.

## Five states, and they are not widened

``running``, ``stalled``, ``unreachable``, ``finished``, ``dead``. Two are terminal;
two require a successor when the outcome is incomplete. **Nothing here adds a sixth**, and
that is a deliberate constraint rather than an oversight: a second termination vocabulary
is how two components come to disagree about whether work is done.

## Two annotations, because they are not states

``held`` and ``escalated`` are things that are true *about* a node in a state, and both are
invisible in the platform's own terminal record:

- **``held``** -- the node reached a declared checkpoint that resolves. Note what this does
  and does not prove: a checkpoint proves the node **reached** it. It does not prove the
  node is **holding now**. Only a resume tests that, and a resume ends the hold.
- **``escalated``** -- the node stopped after raising an argument, and a relay record for it
  resolves. **To the platform this is indistinguishable from finishing**, because that is
  all the substrate shows: a terminal notification with the outcome incomplete.

Both require evidence that resolves. A dispatch that declared no control-record location
**cannot reach ``escalated``** -- such a node is plain ``finished`` with an incomplete
outcome, which blocks its closure and names it. That is the safe direction, and it is the
correct one.

## What blocks a closure

Blocking is not failure. It is the system declining to call something done on evidence that
does not support it, and every blocking reason here names what would resolve it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, FrozenSet, Optional, Tuple

from .activity import ClockState, Staleness
from .envelope import Envelope, control_record_reference
from .lineage import LedgerNode

__all__ = [
    "Annotations",
    "Assessment",
    "Disposition",
    "SUCCESSOR_REQUIRING",
    "TERMINAL",
    "classify",
]


class Disposition(Enum):
    """The closed set. Five, and a sixth is a design change rather than an addition."""

    RUNNING = "running"
    STALLED = "stalled"
    UNREACHABLE = "unreachable"
    """**Not "dead" and not "running".** The honest answer where the clock has no evidence
    to read. Collapsing it into either direction is the failure this package exists to
    prevent: into ``running`` and a dead node blocks forever; into ``dead`` and a working
    node has its outcome closed over."""
    FINISHED = "finished"
    DEAD = "dead"


TERMINAL: FrozenSet[Disposition] = frozenset({Disposition.FINISHED, Disposition.DEAD})
SUCCESSOR_REQUIRING: FrozenSet[Disposition] = frozenset(
    {Disposition.STALLED, Disposition.DEAD}
)
"""``finished`` is deliberately absent.

An escalated node projects as ``finished`` with an incomplete outcome, and **no successor
nomination can discharge it.** That is right: an escalation is answered by the party it was
raised to, never by handing the work to somebody else.
"""


@dataclass(frozen=True)
class Annotations:
    """True *about* a node, rather than states it is in."""

    held: bool = False
    escalated: bool = False

    def __bool__(self) -> bool:
        return self.held or self.escalated


@dataclass(frozen=True)
class Assessment:
    """One node's disposition, with everything that produced it."""

    node_id: str
    disposition: Disposition
    annotations: Annotations = field(default_factory=Annotations)
    outcome_complete: bool = False
    outcome_abandoned: bool = False
    terminal_evidence: Optional[str] = None
    blocking_reasons: Tuple[str, ...] = ()
    basis: str = ""

    @property
    def closure_authorized(self) -> bool:
        """Whether this node's outcome may be recorded as settled.

        **Authorization is the absence of every blocking reason**, not the presence of a
        terminal state. A node can be terminal and still block, which is the common case
        and the useful one.
        """
        return not self.blocking_reasons


def _terminal_status(node: LedgerNode) -> Optional[str]:
    """The status classification reads: the last notification that actually carried one.

    **Not** ``node.terminal_status``, which is the raw LAST notification's status and is
    ``None`` even where an EARLIER notification carried real evidence and a status-less one
    merely arrived after it. Reading the raw value here is what let a node the platform said
    ``completed`` reclassify as ``running`` the moment a status-less monitor event followed
    it -- erasing real terminal evidence rather than merely failing to add any. See
    :attr:`LedgerNode.last_evidenced_status`.
    """
    return node.last_evidenced_status


def classify(
    node: LedgerNode,
    *,
    staleness: Staleness,
    envelope: Optional[Envelope] = None,
    resolves: Optional[Callable[[str], bool]] = None,
    outcome_complete: bool = False,
    abandonment_claimed: bool = False,
    successor_handle: Optional[str] = None,
    argument_emitted: bool = False,
) -> Assessment:
    """Classify one node.

    ``resolves`` answers whether a named artifact reference resolves. It is supplied by the
    caller from the platform. **Where it is absent, every check that depends on a record
    resolving fails closed** -- the annotation is not granted and the abandonment is not
    accepted -- because "I could not check" must never read the same as "I checked and it
    was fine".

    ``abandonment_claimed`` is exactly that: a claim. Whether it becomes
    :attr:`Assessment.outcome_abandoned` depends on whether its record resolves.
    """
    resolves = resolves or (lambda _reference: False)
    blocking: list = []
    evidence: Optional[str] = None
    annotations_held = False
    annotations_escalated = False

    # ---- state -------------------------------------------------------------
    status = _terminal_status(node)

    if node.stopped_by_user is True:
        disposition = Disposition.DEAD
        evidence = "the platform's own record marks this node stopped by the user"
        basis = "stopped_by_user"
    elif status == "completed":
        disposition = Disposition.FINISHED
        evidence = "a terminal notification carrying status 'completed'"
        basis = "terminal notification: completed"
    elif status == "failed":
        disposition = Disposition.DEAD
        evidence = "a terminal notification carrying status 'failed'"
        basis = "terminal notification: failed"
    elif status is not None:
        disposition = Disposition.DEAD
        evidence = f"a terminal notification carrying status {status!r}"
        basis = f"terminal notification: {status}"
    elif staleness.state is ClockState.STALLED:
        disposition = Disposition.STALLED
        basis = f"the clock reports stalled -- {staleness.basis}"
    elif staleness.state is ClockState.UNKNOWN:
        disposition = Disposition.UNREACHABLE
        basis = (
            "the clock has no reset evidence to read, so this node's activity is "
            f"unobservable rather than absent -- {staleness.basis}"
        )
    else:
        disposition = Disposition.RUNNING
        basis = f"the clock reports progress -- {staleness.basis}"

    # **The guard is on the RAW last notification, `node.terminal_status`, never on
    # `status`.** `status` now already skips a trailing status-less notification for
    # classification, so gating this on `status is None` would silence the one case that
    # actually happened: a real status followed by a status-less arrival, where `status` is
    # NOT None (the earlier evidence still governs) but the platform's most recent record
    # about this node is still worth flagging.
    if node.has_terminal_observation and node.terminal_status is None:
        if status is not None:
            blocking.append(
                f"the most recent terminal notification observed for this node carried no "
                f"status, so it is an observation and not terminal evidence on its own. An "
                f"earlier notification's real status ({status!r}) still governs this node's "
                f"disposition -- it is not erased by the status-less arrival -- but the "
                f"status-less notification is itself worth investigating"
            )
        else:
            blocking.append(
                "a terminal notification was observed for this node but carried no status, so it "
                "is an observation and not terminal evidence; nothing here establishes how this "
                "node ended"
            )

    # ---- annotations -------------------------------------------------------
    if envelope is not None:
        for checkpoint in envelope.checkpoints:
            if resolves(checkpoint):
                annotations_held = True
                break

        if disposition is Disposition.FINISHED and argument_emitted:
            relay = control_record_reference(envelope, "relay", node.node_id)
            if resolves(relay):
                annotations_escalated = True
            else:
                blocking.append(
                    "this node stopped having raised an argument, but no relay record "
                    f"resolves at {relay!r}. It is plain 'finished' with an incomplete "
                    "outcome until one does -- an escalation nobody recorded is a claim."
                )

    # ---- abandonment: the refusal this module exists for --------------------
    outcome_abandoned = False
    if abandonment_claimed:
        if envelope is None:
            blocking.append(
                "an abandoned outcome is claimed, but this dispatch declared no "
                "control-record location, so there is nowhere for the record to resolve. "
                "A cancelled node reports itself completed, so without a record that "
                "resolves, abandonment and success are the same governance record."
            )
        else:
            reference = control_record_reference(envelope, "abandonment", node.node_id)
            if resolves(reference):
                outcome_abandoned = True
                evidence = evidence or "an abandonment record"
            else:
                blocking.append(
                    "an abandoned outcome is claimed, but no abandonment record resolves at "
                    f"{reference!r}. This is refused on the plain cancel path exactly as it "
                    "is on the escalation path: a bare declaration authorizes nothing."
                )

    # ---- closure -----------------------------------------------------------
    settled = outcome_complete or outcome_abandoned

    if disposition is Disposition.FINISHED and not settled:
        blocking.append(
            "this node finished with its assigned outcome incomplete and no accepted "
            "abandonment, so the work it was dispatched for is not done"
        )
    if disposition in SUCCESSOR_REQUIRING and not settled and not successor_handle:
        blocking.append(
            f"this node is {disposition.value!r} with an unsettled outcome and no successor "
            "nominated, so the work has no owner"
        )
    if disposition is Disposition.UNREACHABLE:
        blocking.append(
            "this node's activity is unobservable, so nothing here can distinguish a node "
            "that is working from one that has stopped; closure would be a guess"
        )
    if outcome_complete and outcome_abandoned:
        blocking.append(
            "this outcome is recorded both complete and abandoned, which is not a state "
            "this vocabulary has; one of the two is wrong and neither is assumed"
        )

    return Assessment(
        node_id=node.node_id,
        disposition=disposition,
        annotations=Annotations(held=annotations_held, escalated=annotations_escalated),
        outcome_complete=outcome_complete,
        outcome_abandoned=outcome_abandoned,
        terminal_evidence=evidence,
        blocking_reasons=tuple(blocking),
        basis=basis,
    )
