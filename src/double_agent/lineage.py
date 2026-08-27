# SPDX-License-Identifier: Apache-2.0
"""The lineage tree: a pure reader over records the platform already wrote.

This module writes nothing and infers nothing. It reads what
:mod:`double_agent.ports` hands it, indexes it, and answers questions about shape --
who dispatched whom, how deep, when, and what terminal dispositions have been observed.

**Three things here are deliberately awkward, and each one is awkward because the
comfortable version is wrong.**

*Terminal dispositions are a sequence, not a value.* A node's terminal status is not
stable over time: the same node can be notified ``failed`` and later notified
``completed``. A reader that records "the status" produces a different answer depending
on when it read. This module records every observation and derives the current
disposition from the LAST by instant, so the derivation is stated rather than
accidental.

*An empty sequence is not the claim that the node did not stop.* It is the claim that
no terminal disposition has been observed. Those are different, and a real stopped
agent with no notification recorded anywhere is the case that makes the difference
matter.

*A notification with no status is recorded and is not evidence.* It is factual -- the
platform really did emit it -- which is exactly why it is dangerous: it reads like
terminal evidence and carries none.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from .ports import NodeRecord, Platform, TerminalNotification, UnsupportedCapability

__all__ = ["Ledger", "LedgerNode", "build_ledger"]


@dataclass(frozen=True)
class LedgerNode:
    """One node, as read. Never as inferred."""

    node_id: str
    parent_id: Optional[str]
    depth: Optional[int]
    dispatched_at: Optional[datetime]
    node_type: Optional[str]
    description: Optional[str]
    model: Optional[str]
    stopped_by_user: Optional[bool]
    activity_instants: Tuple[datetime, ...]
    terminal_notifications: Tuple[TerminalNotification, ...]

    @property
    def last_activity_at(self) -> Optional[datetime]:
        """The most recent evidenced activity, or ``None`` where none is evidenced.

        ``None`` here means *no activity has been evidenced*, which is not the same as
        *the node has been inactive since it was dispatched*. Nothing in this package
        substitutes the dispatch instant for a missing activity instant.
        """
        return self.activity_instants[-1] if self.activity_instants else None

    @property
    def terminal_status(self) -> Optional[str]:
        """The status of the LAST observed notification, or ``None``.

        ``None`` arises two ways and the caller usually needs to tell them apart:
        nothing has been observed at all, or the last observation carried no status.
        :meth:`has_terminal_observation` separates them.
        """
        if not self.terminal_notifications:
            return None
        return self.terminal_notifications[-1].status

    @property
    def has_terminal_observation(self) -> bool:
        """Whether ANY terminal notification has been observed for this node.

        **This is not "the node stopped".** A node that stopped without the platform
        recording a notification returns ``False`` here, and that case has been observed
        in the wild. Treat ``False`` as *not observed*, never as *still running*.
        """
        return bool(self.terminal_notifications)

    @property
    def has_terminal_evidence(self) -> bool:
        """Whether a terminal disposition has been observed that may be used as evidence.

        A notification carrying no status is an observation and is **not** evidence, so a
        node whose only notifications are status-less returns ``False`` here while
        :attr:`has_terminal_observation` returns ``True``.
        """
        return any(n.status is not None for n in self.terminal_notifications)

    @property
    def last_evidenced_status(self) -> Optional[str]:
        """The status of the last notification that actually carried one, or ``None``.

        **Not the same ``None`` as** :attr:`terminal_status`. That property answers "what did
        the LAST notification say", which is ``None`` even where an EARLIER notification
        carried a real status and a status-less one merely arrived after it -- a monitor
        event, per this package's own reconciliation module, "not a terminal disposition,
        and it may not become terminal evidence whatever it says about itself." A status-less
        arrival is real and observed, but it is not a status *change*, and classification must
        not read it as one: doing so once let a node the platform said ``completed`` read as
        ``running``, deleting real terminal evidence rather than merely failing to add any.
        This property is what a caller reads to avoid that -- it skips trailing status-less
        notifications and returns the last one that is genuine evidence, or ``None`` where
        none ever was.
        """
        for notification in reversed(self.terminal_notifications):
            if notification.status is not None:
                return notification.status
        return None

    def first_activity_at_or_after(self, instant: datetime) -> Optional[datetime]:
        """The first evidenced activity at or after ``instant``, or ``None``.

        This exists because a maximum cannot yield a first-at-or-after, and every bound
        measured from "when did this node next do anything" needs a start point rather
        than an end point.
        """
        for candidate in self.activity_instants:
            if candidate >= instant:
                return candidate
        return None


@dataclass(frozen=True)
class Ledger:
    """An immutable index over the platform's own lineage records."""

    nodes: Mapping[str, LedgerNode]
    _children: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    _roots: Tuple[str, ...] = ()

    def __iter__(self) -> Iterator[LedgerNode]:
        return iter(self.nodes.values())

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def get(self, node_id: str) -> Optional[LedgerNode]:
        return self.nodes.get(node_id)

    def children_of(self, node_id: str) -> Tuple[LedgerNode, ...]:
        return tuple(self.nodes[c] for c in self._children.get(node_id, ()))

    @property
    def roots(self) -> Tuple[LedgerNode, ...]:
        """Nodes with no parent, or whose parent is not in this ledger.

        A node whose recorded parent is absent from the ledger is a root **here** and is
        not thereby parentless in the platform: the ledger's scope is what was read, and
        this property says so rather than asserting a tree shape the records do not
        support.
        """
        return tuple(self.nodes[r] for r in self._roots)

    def ancestors_of(self, node_id: str) -> Tuple[LedgerNode, ...]:
        """Every ancestor of ``node_id``, nearest first, within this ledger.

        Cycle-safe: a record set that describes a cycle stops rather than looping. A
        cycle is a defect in the platform's records and this package will not pretend to
        resolve it.
        """
        seen = {node_id}
        out: List[LedgerNode] = []
        current = self.nodes.get(node_id)
        while current is not None and current.parent_id is not None:
            if current.parent_id in seen:
                break
            seen.add(current.parent_id)
            parent = self.nodes.get(current.parent_id)
            if parent is None:
                break
            out.append(parent)
            current = parent
        return tuple(out)

    def dominates(self, ancestor_id: str, node_id: str) -> bool:
        """Whether ``ancestor_id`` is a strict ancestor of ``node_id`` in the read tree.

        This is the structural relation the entitlement rule is stated against: no actor
        acquires authority over a node it does not already dominate **in the platform's
        own tree** by writing a record.
        """
        if ancestor_id == node_id:
            return False
        return any(a.node_id == ancestor_id for a in self.ancestors_of(node_id))

    def subtree_of(self, node_id: str) -> Tuple[LedgerNode, ...]:
        """``node_id``'s descendants, breadth-first, excluding itself. Cycle-safe."""
        out: List[LedgerNode] = []
        seen = {node_id}
        queue = list(self._children.get(node_id, ()))
        while queue:
            current_id = queue.pop(0)
            if current_id in seen:
                continue
            seen.add(current_id)
            node = self.nodes.get(current_id)
            if node is None:
                continue
            out.append(node)
            queue.extend(self._children.get(current_id, ()))
        return tuple(out)


def build_ledger(platform: Platform, *, node_ids: Optional[Iterable[str]] = None) -> Ledger:
    """Read the platform's lineage records into an immutable :class:`Ledger`.

    ``node_ids`` optionally narrows the read; omitted, every record the platform offers is
    admitted.

    **Only nodes the platform has a dispatch record for are admitted.** That predicate is
    the platform's to enforce at the port -- a notification stream keyed by task identity
    alone is routinely dominated by things that are not agents at all -- and this function
    additionally drops notifications for identities the lineage does not carry, so an
    index built here cannot acquire a node by having heard about it.
    """
    caps = platform.capabilities
    if not caps.lineage:
        raise UnsupportedCapability("lineage", "no lineage tree, so nothing here is derivable")

    wanted = set(node_ids) if node_ids is not None else None
    records: List[NodeRecord] = [
        r for r in platform.lineage_records() if wanted is None or r.node_id in wanted
    ]
    known = {r.node_id for r in records}

    nodes: Dict[str, LedgerNode] = {}
    for record in records:
        instants: Sequence[datetime] = ()
        if caps.activity_instants:
            instants = tuple(sorted(platform.activity_instants(record.node_id)))
        notifications: Sequence[TerminalNotification] = ()
        if caps.terminal_notifications:
            notifications = tuple(
                sorted(platform.terminal_notifications(record.node_id), key=lambda n: n.instant)
            )
        nodes[record.node_id] = LedgerNode(
            node_id=record.node_id,
            parent_id=record.parent_id,
            depth=record.depth,
            dispatched_at=record.dispatched_at,
            node_type=record.node_type,
            description=record.description,
            model=record.model,
            stopped_by_user=record.stopped_by_user,
            activity_instants=tuple(instants),
            terminal_notifications=tuple(notifications),
        )

    children: Dict[str, List[str]] = {}
    roots: List[str] = []
    for node in nodes.values():
        if node.parent_id is not None and node.parent_id in known:
            children.setdefault(node.parent_id, []).append(node.node_id)
        else:
            roots.append(node.node_id)

    return Ledger(
        nodes=dict(nodes),
        _children={k: tuple(v) for k, v in children.items()},
        _roots=tuple(roots),
    )
