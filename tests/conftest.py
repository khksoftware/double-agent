# SPDX-License-Identifier: Apache-2.0
"""A fake platform, so the package's own behaviour is tested and not a host's.

The fake is deliberately literal: it returns exactly what it was constructed with and
raises :class:`UnsupportedCapability` for anything its declared capabilities exclude. That
second half is the useful one -- a fake that quietly returns empty for an unsupported
capability would let a test pass against the exact substitution this package refuses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

import pytest

from double_agent.ports import (
    Capabilities,
    DispatchRecord,
    NodeRecord,
    ReferenceState,
    RegistryEntry,
    SignalOutcome,
    TerminalNotification,
    UnsupportedCapability,
)

T0 = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    """An instant ``seconds`` after the fixture epoch."""
    return T0 + timedelta(seconds=seconds)


def all_capabilities(**overrides: bool) -> Capabilities:
    base = dict(
        lineage=True,
        activity_instants=True,
        terminal_notifications=True,
        dispatch_text=True,
        emitted_text=True,
        registry=True,
        reference_resolution=True,
        adjudicable_signals=True,
    )
    base.update(overrides)
    return Capabilities(**base)


@dataclass
class FakePlatform:
    """Returns what it was given; refuses what it does not declare."""

    caps: Capabilities = field(default_factory=all_capabilities)
    nodes: List[NodeRecord] = field(default_factory=list)
    activity: Dict[str, Sequence[datetime]] = field(default_factory=dict)
    notifications: Dict[str, Sequence[TerminalNotification]] = field(default_factory=dict)
    dispatch_texts: Dict[str, str] = field(default_factory=dict)
    emitted: Dict[str, str] = field(default_factory=dict)
    registry: List[RegistryEntry] = field(default_factory=list)
    references: Dict[str, ReferenceState] = field(default_factory=dict)
    signal_log: List[tuple] = field(default_factory=list)
    signal_adjudicable: bool = True
    signal_delivered: bool = True

    @property
    def capabilities(self) -> Capabilities:
        return self.caps

    def lineage_records(self) -> Iterable[NodeRecord]:
        if not self.caps.lineage:
            raise UnsupportedCapability("lineage")
        return list(self.nodes)

    def activity_instants(self, node_id: str) -> Sequence[datetime]:
        if not self.caps.activity_instants:
            raise UnsupportedCapability("activity_instants")
        return list(self.activity.get(node_id, ()))

    def terminal_notifications(self, node_id: str) -> Sequence[TerminalNotification]:
        if not self.caps.terminal_notifications:
            raise UnsupportedCapability("terminal_notifications")
        return list(self.notifications.get(node_id, ()))

    def dispatch_record(self, node_id: str) -> Optional[DispatchRecord]:
        if not self.caps.dispatch_text:
            raise UnsupportedCapability("dispatch_text")
        text = self.dispatch_texts.get(node_id)
        return None if text is None else DispatchRecord(node_id=node_id, text=text)

    def emitted_text(self, node_id: str) -> Optional[str]:
        if not self.caps.emitted_text:
            raise UnsupportedCapability("emitted_text")
        return self.emitted.get(node_id)

    def registry_entries(self) -> Iterable[RegistryEntry]:
        if not self.caps.registry:
            raise UnsupportedCapability("registry")
        return list(self.registry)

    def reference_state(self, reference: str) -> ReferenceState:
        if not self.caps.reference_resolution:
            raise UnsupportedCapability("reference_resolution")
        return self.references.get(reference, ReferenceState(exists=False))

    def send_signal(
        self, node_id: str, shape: str, payload: Mapping[str, object]
    ) -> SignalOutcome:
        self.signal_log.append((node_id, shape, dict(payload)))
        return SignalOutcome(
            delivered=self.signal_delivered,
            adjudicable=self.signal_adjudicable and self.caps.adjudicable_signals,
        )


def node(
    node_id: str,
    parent_id: Optional[str] = None,
    depth: Optional[int] = None,
    dispatched_at: Optional[datetime] = None,
    **kwargs: object,
) -> NodeRecord:
    return NodeRecord(
        node_id=node_id,
        parent_id=parent_id,
        depth=depth,
        dispatched_at=dispatched_at if dispatched_at is not None else T0,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()
