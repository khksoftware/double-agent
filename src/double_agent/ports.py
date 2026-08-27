# SPDX-License-Identifier: Apache-2.0
"""The platform interface. Every other module in this package reads through this file.

This package does not observe agents. It observes *records a platform already wrote about
agents*, and every one of those records is private to whichever platform wrote it -- shapes,
key names, file locations, none of it standardised anywhere. That is the fact this file
exists to contain.

**A port is a property a platform either supplies or does not.** It is deliberately not a
lowest common denominator and not a set of optional extras: where a platform supplies
nothing for a capability, the steps that read it BLOCK rather than degrade. A framework that
silently substitutes a weaker answer for a missing one produces a confident wrong answer,
which is worse than a gap, because a gap gets investigated and a wrong answer does not.

**Why this is a file and not a convention.** Without it, "the framework is portable" is a
claim in a document; with it, the boundary is a type. Every capability below is declared,
and `Capabilities` is what a caller checks before relying on one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Optional, Protocol, Sequence, runtime_checkable

__all__ = [
    "Capabilities",
    "DispatchRecord",
    "NodeRecord",
    "Platform",
    "ReferenceState",
    "RegistryEntry",
    "SignalOutcome",
    "TerminalNotification",
    "UnsupportedCapability",
]


class UnsupportedCapability(RuntimeError):
    """Raised where a step needs a capability the platform does not supply.

    This is the blocking behaviour, made explicit. It is an error rather than a sentinel
    return precisely so that a caller cannot mistake "not supplied" for "supplied and
    empty" -- a distinction this package refuses to collapse anywhere.
    """

    def __init__(self, capability: str, detail: str = "") -> None:
        message = (
            f"the platform does not supply {capability!r}, so this step blocks rather than "
            f"deriving an answer from what remains"
        )
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)
        self.capability = capability


@dataclass(frozen=True)
class Capabilities:
    """What this platform actually supplies, declared rather than probed.

    Each flag is read before the corresponding port is called. A platform declaring a
    capability it does not have produces an exception at the call site rather than a wrong
    answer, which is the intended failure direction.
    """

    lineage: bool
    """A tree of dispatch records with parent links. Without it nothing here works."""

    activity_instants: bool
    """Per-node instants of evidenced activity. The activity clock reads these."""

    terminal_notifications: bool
    """Per-node terminal dispositions, ordered, possibly repeating for one node."""

    dispatch_text: bool
    """Whatever the platform recorded verbatim about a dispatch. Carries the envelope."""

    emitted_text: bool
    """What an agent emitted. The conformance predicate reads this."""

    registry: bool
    """The platform's own dispatch registry, where it keeps one."""

    reference_resolution: bool
    """Whether a named artifact reference resolves, and what state it is in.

    Two independent things read this. Entitlement transfer needs to know a record is more
    than a declaration. And the inactivity clock needs it because **emission does not reset
    inactivity** -- only validated completion, an advancing durable cursor, a declared
    external-wait transition, or an owned-child delta does -- and three of those four are
    questions about whether a named artifact exists and whether it has changed.
    """

    adjudicable_signals: bool
    """Whether the transport records, on the RECIPIENT's side, a sender identity the sender
    does not control. Where false, every signal is advisory: non-compliance produces an
    obstacle report and may never be attributed to the recipient as defiance."""


@dataclass(frozen=True)
class NodeRecord:
    """One node of the platform's own lineage tree, as the platform wrote it.

    Fields absent from the platform's record are ``None``, and ``None`` is never ``False``:
    "the platform did not record this" and "the platform recorded this as false" are
    different facts and several dispositions in this package turn on the difference.
    """

    node_id: str
    parent_id: Optional[str]
    depth: Optional[int]
    dispatched_at: Optional[datetime]
    node_type: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    stopped_by_user: Optional[bool] = None


@dataclass(frozen=True)
class TerminalNotification:
    """One observed terminal disposition for one node.

    ``status`` is ``None`` where the platform's notification carried no status at all. Such
    a notification is recorded and is NOT a terminal disposition: it may not be projected
    as terminal evidence. A factual-but-status-less record reads like evidence and is not,
    which is why it is representable here rather than dropped at the boundary.

    ``record_identity`` is the identity of the platform record this observation was read
    from. It exists so an index over notifications is idempotent. What that identity *is*
    is the platform's own business and never this package's.
    """

    instant: datetime
    status: Optional[str]
    record_identity: str


@dataclass(frozen=True)
class DispatchRecord:
    """What the platform recorded verbatim about one dispatch.

    ``text`` is the raw recorded text. This package parses an envelope out of it and does
    not assume any structure beyond "the platform records this verbatim".
    """

    node_id: str
    text: str


@dataclass(frozen=True)
class RegistryEntry:
    """One entry of the platform's own dispatch registry.

    An entry that does not resolve to a node of this session is marked unresolvable by the
    reconciliation. It is never invented into a node and never silently dropped.
    """

    entry_id: str
    node_id: Optional[str]
    fields: Mapping[str, object]


@dataclass(frozen=True)
class ReferenceState:
    """What the platform can say about a named artifact reference.

    ``last_changed`` and ``content_digest`` are both optional because a platform may be able
    to say an artifact exists without being able to say when it last moved. A caller that
    needs change detection and gets neither **blocks**; it does not fall back to "it exists,
    so call it progress", which is the substitution that turns an inactivity clock into a
    liveness clock and inverts the predicate it is supposed to implement.
    """

    exists: bool
    last_changed: Optional[datetime] = None
    content_digest: Optional[str] = None


@dataclass(frozen=True)
class SignalOutcome:
    """The result of asking the platform to deliver a signal.

    ``adjudicable`` is the whole point of this type. It reports whether the transport
    recorded, on the recipient's side, a sender identity the sender does not control. Where
    it is false the signal is advisory, and this package will not attribute non-compliance
    with it to the recipient.
    """

    delivered: bool
    adjudicable: bool
    detail: str = ""


@runtime_checkable
class Platform(Protocol):
    """What a host must implement for this package to read it.

    Every method may raise :class:`UnsupportedCapability`. A caller consults
    :attr:`capabilities` first; the raise is the backstop for a platform that declares a
    capability it cannot honour, not the normal control path.
    """

    @property
    def capabilities(self) -> Capabilities:
        """What this platform supplies. Read before any other member."""

    def lineage_records(self) -> Iterable[NodeRecord]:
        """Every node the platform has a dispatch record for, in any order."""

    def activity_instants(self, node_id: str) -> Sequence[datetime]:
        """Instants of evidenced activity for one node, ascending.

        The activity clock derives BOTH "most recent evidenced activity" and "first
        evidenced activity at or after t" from this one sequence. They are not two sources:
        a maximum cannot yield a first-at-or-after, and a design that returns only the
        maximum leaves the acknowledgement bound with no start instant.
        """

    def terminal_notifications(self, node_id: str) -> Sequence[TerminalNotification]:
        """Every terminal notification observed for one node, ascending by instant.

        A node's terminal status is not stable over time -- one node may be notified failed
        and later notified completed -- so this is a sequence and never a single value.
        """

    def dispatch_record(self, node_id: str) -> Optional[DispatchRecord]:
        """What the platform recorded verbatim about this dispatch, if anything."""

    def emitted_text(self, node_id: str) -> Optional[str]:
        """What this node emitted, if the platform retains it."""

    def registry_entries(self) -> Iterable[RegistryEntry]:
        """The platform's own dispatch registry. A platform with none supplies an empty
        iterable and declares ``registry=False``; the two are different and the
        reconciliation reports them differently."""

    def reference_state(self, reference: str) -> ReferenceState:
        """What this platform can say about a named artifact reference.

        Used wherever a record must be more than a declaration, and wherever progress must
        be distinguished from output. A platform that cannot answer declares
        ``reference_resolution=False`` and the steps needing it block.
        """

    def send_signal(self, node_id: str, shape: str, payload: Mapping[str, object]) -> SignalOutcome:
        """Ask the platform to deliver one signal to one node."""
