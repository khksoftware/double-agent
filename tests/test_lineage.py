# SPDX-License-Identifier: Apache-2.0
"""The lineage reader, and the distinctions it refuses to collapse."""
from __future__ import annotations

import pytest

from double_agent.lineage import build_ledger
from double_agent.ports import TerminalNotification, UnsupportedCapability

from .conftest import FakePlatform, all_capabilities, at, node


def notified(seconds: float, status, identity: str) -> TerminalNotification:
    return TerminalNotification(instant=at(seconds), status=status, record_identity=identity)


class TestImportResolution:
    def test_the_package_is_not_an_empty_namespace_package(self):
        """The registered trap: a bare import can succeed against nothing at all.

        Asserting the import worked proves nothing here -- a namespace package imports
        cleanly and has no code in it. The resolved file is what distinguishes them.
        """
        import double_agent

        assert double_agent.__file__ is not None
        assert double_agent.__file__.replace("\\", "/").endswith(
            "src/double_agent/__init__.py"
        )
        assert double_agent.__version__


class TestTerminalDispositionsAreASequence:
    def test_a_node_notified_failed_then_completed_reports_the_last(self):
        """Measured in the wild: the same node can be notified twice with different statuses.

        A reader that records "the status" gives a different governance answer depending on
        when it read. This one records every observation and derives from the last.
        """
        platform = FakePlatform(nodes=[node("a")])
        platform.notifications["a"] = [
            notified(10, "failed", "r1"),
            notified(20, "completed", "r2"),
        ]

        ledger = build_ledger(platform)

        assert len(ledger.nodes["a"].terminal_notifications) == 2
        assert ledger.nodes["a"].terminal_status == "completed"

    def test_notifications_are_ordered_even_when_the_platform_is_not(self):
        platform = FakePlatform(nodes=[node("a")])
        platform.notifications["a"] = [
            notified(20, "completed", "r2"),
            notified(10, "failed", "r1"),
        ]

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].terminal_status == "completed"


class TestNotObservedIsNotNotThere:
    def test_no_notification_is_not_a_claim_the_node_kept_running(self):
        platform = FakePlatform(nodes=[node("a")])

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].has_terminal_observation is False
        assert ledger.nodes["a"].terminal_status is None

    def test_a_status_less_notification_is_observed_but_is_not_evidence(self):
        """The dangerous case: factual, emitted by the platform, and carrying nothing.

        It reads like terminal evidence at a glance, which is exactly why the two
        properties are separate and why one of them is false here.
        """
        platform = FakePlatform(nodes=[node("a")])
        platform.notifications["a"] = [notified(10, None, "r1")]

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].has_terminal_observation is True
        assert ledger.nodes["a"].has_terminal_evidence is False
        assert ledger.nodes["a"].terminal_status is None
        assert ledger.nodes["a"].last_evidenced_status is None

    def test_last_evidenced_status_skips_a_trailing_status_less_notification(self):
        """`terminal_status` (the raw last observation) and `last_evidenced_status` (the
        last one that is actually evidence) must disagree here -- that disagreement is
        exactly what a status-less arrival after a real one is supposed to produce."""
        platform = FakePlatform(nodes=[node("a")])
        platform.notifications["a"] = [
            notified(10, "completed", "r1"),
            notified(20, None, "r2"),
        ]

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].terminal_status is None
        assert ledger.nodes["a"].last_evidenced_status == "completed"

    def test_last_evidenced_status_agrees_with_terminal_status_when_the_last_one_is_real(self):
        platform = FakePlatform(nodes=[node("a")])
        platform.notifications["a"] = [notified(10, "completed", "r1")]

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].terminal_status == "completed"
        assert ledger.nodes["a"].last_evidenced_status == "completed"

    def test_absent_is_none_and_none_is_not_false(self):
        platform = FakePlatform(nodes=[node("a"), node("b", stopped_by_user=False)])

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].stopped_by_user is None
        assert ledger.nodes["b"].stopped_by_user is False
        assert ledger.nodes["a"].stopped_by_user is not False

    def test_no_evidenced_activity_does_not_borrow_the_dispatch_instant(self):
        platform = FakePlatform(nodes=[node("a", dispatched_at=at(0))])

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].last_activity_at is None
        assert ledger.nodes["a"].dispatched_at == at(0)


class TestFirstActivityAtOrAfter:
    def test_it_is_not_derivable_from_a_maximum(self):
        platform = FakePlatform(nodes=[node("a")])
        platform.activity["a"] = [at(10), at(20), at(30)]

        ledger = build_ledger(platform)
        node_a = ledger.nodes["a"]

        assert node_a.last_activity_at == at(30)
        assert node_a.first_activity_at_or_after(at(15)) == at(20)
        assert node_a.first_activity_at_or_after(at(20)) == at(20)

    def test_none_where_the_node_emitted_nothing_after_the_instant(self):
        """A node inside one long operation has no delivery instant, so no bound starts."""
        platform = FakePlatform(nodes=[node("a")])
        platform.activity["a"] = [at(10)]

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].first_activity_at_or_after(at(50)) is None


class TestTreeShape:
    def _tree(self) -> FakePlatform:
        return FakePlatform(
            nodes=[
                node("root"),
                node("a", parent_id="root", depth=1),
                node("b", parent_id="root", depth=1),
                node("a1", parent_id="a", depth=2),
            ]
        )

    def test_children_roots_and_ancestors(self):
        ledger = build_ledger(self._tree())

        assert {n.node_id for n in ledger.roots} == {"root"}
        assert {n.node_id for n in ledger.children_of("root")} == {"a", "b"}
        assert [n.node_id for n in ledger.ancestors_of("a1")] == ["a", "root"]

    def test_dominates_is_strict(self):
        ledger = build_ledger(self._tree())

        assert ledger.dominates("root", "a1") is True
        assert ledger.dominates("a", "a1") is True
        assert ledger.dominates("b", "a1") is False
        assert ledger.dominates("a1", "a1") is False

    def test_subtree_excludes_itself(self):
        ledger = build_ledger(self._tree())

        assert {n.node_id for n in ledger.subtree_of("root")} == {"a", "b", "a1"}
        assert ledger.subtree_of("a1") == ()

    def test_a_node_whose_parent_is_outside_the_ledger_is_a_root_here(self):
        platform = FakePlatform(nodes=[node("orphan", parent_id="not-read")])

        ledger = build_ledger(platform)

        assert {n.node_id for n in ledger.roots} == {"orphan"}
        assert ledger.nodes["orphan"].parent_id == "not-read"

    def test_a_cycle_in_the_records_stops_rather_than_looping(self):
        platform = FakePlatform(nodes=[node("x", parent_id="y"), node("y", parent_id="x")])

        ledger = build_ledger(platform)

        assert len(ledger.ancestors_of("x")) <= 2
        assert ledger.subtree_of("x")


class TestCapabilities:
    def test_no_lineage_blocks_rather_than_returning_empty(self):
        platform = FakePlatform(caps=all_capabilities(lineage=False))

        with pytest.raises(UnsupportedCapability) as excinfo:
            build_ledger(platform)

        assert excinfo.value.capability == "lineage"
        assert "blocks rather than deriving" in str(excinfo.value)

    def test_a_platform_without_notifications_yields_nodes_with_none_observed(self):
        """Not an error -- but the node must not read as "observed and empty" either."""
        platform = FakePlatform(
            caps=all_capabilities(terminal_notifications=False), nodes=[node("a")]
        )

        ledger = build_ledger(platform)

        assert ledger.nodes["a"].has_terminal_observation is False

    def test_node_ids_narrows_the_read(self):
        platform = FakePlatform(nodes=[node("a"), node("b")])

        ledger = build_ledger(platform, node_ids=["a"])

        assert set(ledger.nodes) == {"a"}
