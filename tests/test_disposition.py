# SPDX-License-Identifier: Apache-2.0
"""The classifier, and above all the abandonment refusal."""
from __future__ import annotations

import pytest

from double_agent.activity import ClockState, Reset, ResetKind, assess
from double_agent.disposition import (
    SUCCESSOR_REQUIRING,
    TERMINAL,
    Disposition,
    classify,
)
from double_agent.envelope import FENCE_MARKER, parse_envelope
from double_agent.lineage import build_ledger
from double_agent.ports import TerminalNotification

from .conftest import FakePlatform, at, node


def envelope(control_record: str = "control", checkpoints: str = "step-one"):
    fields = {
        "assigned_outcome": "the deliverable",
        "durable_cursor": "state/cursor.json",
        "checkpoints": checkpoints,
        "heartbeat_seconds": "120",
        "role_label": "[Worker]",
        "external_wait": "",
        "control_record": control_record,
    }
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return parse_envelope(f"{FENCE_MARKER}\n{body}\n")


def one_node(node_id="n1", status=None, stopped_by_user=None, activity=(0,)):
    platform = FakePlatform(nodes=[node(node_id, stopped_by_user=stopped_by_user)])
    platform.activity[node_id] = [at(s) for s in activity]
    if status is not None:
        platform.notifications[node_id] = [
            TerminalNotification(instant=at(10), status=status, record_identity="r1")
        ]
    return build_ledger(platform).nodes[node_id]


PROGRESSING = assess(
    [Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(10)
)
STALLED = assess([Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(9999))
NO_EVIDENCE = assess([], now=at(10))


class TestTheAbandonmentRefusal:
    """A cancelled node reports itself completed. This is the whole reason for the module."""

    def test_a_bare_declaration_does_not_authorize_a_closure(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: False,
            abandonment_claimed=True,
        )

        assert result.outcome_abandoned is False
        assert result.closure_authorized is False
        assert any("no abandonment record resolves" in r for r in result.blocking_reasons)

    def test_a_resolving_record_does_authorize_it(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: "abandonment" in ref,
            abandonment_claimed=True,
        )

        assert result.outcome_abandoned is True
        assert result.closure_authorized is True

    def test_the_refusal_holds_on_the_plain_cancel_path_not_only_on_escalation(self):
        """The banked finding: the rule existed for escalation and not for plain cancel.

        This node raised no argument and is not escalated. It is the ordinary cancel, which
        is the commoner path, and the refusal must reach it identically.
        """
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: False,
            abandonment_claimed=True,
            argument_emitted=False,
        )

        assert result.annotations.escalated is False
        assert result.outcome_abandoned is False
        assert result.closure_authorized is False

    def test_no_control_record_location_means_nowhere_to_resolve(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=None,
            abandonment_claimed=True,
        )

        assert result.outcome_abandoned is False
        assert any("nowhere for the record to resolve" in r for r in result.blocking_reasons)

    def test_a_missing_resolver_fails_closed(self):
        """"I could not check" must never read the same as "I checked and it was fine"."""
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            abandonment_claimed=True,
        )

        assert result.outcome_abandoned is False
        assert result.closure_authorized is False

    def test_complete_and_abandoned_together_is_refused(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: True,
            outcome_complete=True,
            abandonment_claimed=True,
        )

        assert result.closure_authorized is False
        assert any("both complete and abandoned" in r for r in result.blocking_reasons)


class TestTheFiveStates:
    def test_the_set_is_five_and_the_partitions_are_right(self):
        assert len(Disposition) == 5
        assert {d.value for d in Disposition} == {
            "running",
            "stalled",
            "unreachable",
            "finished",
            "dead",
        }
        assert {d.value for d in TERMINAL} == {"finished", "dead"}
        assert {d.value for d in SUCCESSOR_REQUIRING} == {"stalled", "dead"}

    def test_finished_is_deliberately_not_successor_requiring(self):
        """An escalation is answered, never handed to somebody else."""
        assert Disposition.FINISHED not in SUCCESSOR_REQUIRING

    def test_completed_is_finished(self):
        assert classify(one_node(status="completed"), staleness=PROGRESSING).disposition is Disposition.FINISHED

    def test_failed_is_dead_with_evidence(self):
        result = classify(one_node(status="failed"), staleness=PROGRESSING)

        assert result.disposition is Disposition.DEAD
        assert "failed" in (result.terminal_evidence or "")

    def test_stopped_by_user_is_dead_and_outranks_the_clock(self):
        result = classify(one_node(stopped_by_user=True), staleness=PROGRESSING)

        assert result.disposition is Disposition.DEAD

    def test_stopped_by_user_none_is_not_false(self):
        assert classify(one_node(stopped_by_user=None), staleness=PROGRESSING).disposition is Disposition.RUNNING

    def test_a_stalled_clock_is_stalled(self):
        assert classify(one_node(), staleness=STALLED).disposition is Disposition.STALLED


class TestUnreachableIsNotCollapsed:
    def test_no_clock_evidence_is_unreachable_rather_than_running_or_dead(self):
        result = classify(one_node(), staleness=NO_EVIDENCE)

        assert result.disposition is Disposition.UNREACHABLE
        assert result.disposition is not Disposition.RUNNING
        assert result.disposition is not Disposition.DEAD

    def test_unreachable_blocks_a_closure(self):
        result = classify(one_node(), staleness=NO_EVIDENCE, outcome_complete=True)

        assert result.closure_authorized is False
        assert any("would be a guess" in r for r in result.blocking_reasons)


class TestStatusLessNotifications:
    def test_an_observation_with_no_status_blocks_rather_than_reading_as_evidence(self):
        platform = FakePlatform(nodes=[node("n1")])
        platform.notifications["n1"] = [
            TerminalNotification(instant=at(10), status=None, record_identity="r1")
        ]
        platform.activity["n1"] = [at(0)]
        ledger_node = build_ledger(platform).nodes["n1"]

        result = classify(ledger_node, staleness=PROGRESSING, outcome_complete=True)

        assert result.closure_authorized is False
        assert any("carried no status" in r for r in result.blocking_reasons)

    def test_a_status_less_arrival_after_a_real_one_does_not_erase_it(self):
        """A node notified 'completed', then notified again with no status. An independent
        review found this read as RUNNING with terminal_evidence None and zero blocking
        reasons -- the status-less arrival deleted real evidence rather than merely failing
        to add any. It must instead still read as FINISHED, on the real evidence, with the
        status-less arrival flagged rather than silently accepted."""
        platform = FakePlatform(nodes=[node("n1")])
        platform.notifications["n1"] = [
            TerminalNotification(instant=at(10), status="completed", record_identity="r1"),
            TerminalNotification(instant=at(70), status=None, record_identity="r2"),
        ]
        platform.activity["n1"] = [at(0)]
        ledger_node = build_ledger(platform).nodes["n1"]

        result = classify(ledger_node, staleness=PROGRESSING, outcome_complete=True)

        assert result.disposition is Disposition.FINISHED
        assert result.terminal_evidence == "a terminal notification carrying status 'completed'"
        assert any("carried no status" in r for r in result.blocking_reasons)
        assert any("still governs" in r for r in result.blocking_reasons)

    def test_a_status_less_arrival_with_no_prior_real_status_is_the_original_message(self):
        """The pre-existing case -- no real evidence ever, only a status-less notification --
        keeps its original wording; only the erasure case (a REAL status followed by a
        status-less one) is new."""
        platform = FakePlatform(nodes=[node("n1")])
        platform.notifications["n1"] = [
            TerminalNotification(instant=at(10), status=None, record_identity="r1"),
        ]
        platform.activity["n1"] = [at(0)]
        ledger_node = build_ledger(platform).nodes["n1"]

        result = classify(ledger_node, staleness=PROGRESSING, outcome_complete=True)

        assert not any("still governs" in r for r in result.blocking_reasons)
        assert any(
            "nothing here establishes how this node ended" in r
            for r in result.blocking_reasons
        )


class TestAnnotations:
    def test_a_resolving_checkpoint_marks_held(self):
        result = classify(
            one_node(),
            staleness=PROGRESSING,
            envelope=envelope(checkpoints="step-one, step-two"),
            resolves=lambda ref: ref == "step-two",
        )

        assert result.annotations.held is True

    def test_no_resolving_checkpoint_is_not_held(self):
        result = classify(
            one_node(), staleness=PROGRESSING, envelope=envelope(), resolves=lambda ref: False
        )

        assert result.annotations.held is False

    def test_escalation_requires_a_relay_record_that_resolves(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: "relay" in ref,
            argument_emitted=True,
            outcome_complete=False,
        )

        assert result.annotations.escalated is True

    def test_an_argument_with_no_relay_record_is_plain_finished_and_blocks(self):
        result = classify(
            one_node(status="completed"),
            staleness=PROGRESSING,
            envelope=envelope(),
            resolves=lambda ref: False,
            argument_emitted=True,
        )

        assert result.annotations.escalated is False
        assert result.disposition is Disposition.FINISHED
        assert result.closure_authorized is False
        assert any("an escalation nobody recorded is a claim" in r for r in result.blocking_reasons)

    def test_a_dispatch_with_no_envelope_cannot_reach_escalated(self):
        result = classify(
            one_node(status="completed"), staleness=PROGRESSING, argument_emitted=True
        )

        assert result.annotations.escalated is False


class TestClosureBlocking:
    def test_finished_with_an_incomplete_outcome_blocks(self):
        result = classify(one_node(status="completed"), staleness=PROGRESSING)

        assert result.disposition in TERMINAL
        assert result.closure_authorized is False

    def test_finished_and_complete_authorizes(self):
        result = classify(
            one_node(status="completed"), staleness=PROGRESSING, outcome_complete=True
        )

        assert result.closure_authorized is True

    def test_a_successor_requiring_state_needs_a_successor(self):
        without = classify(one_node(status="failed"), staleness=PROGRESSING)
        with_successor = classify(
            one_node(status="failed"), staleness=PROGRESSING, successor_handle="n2"
        )

        assert without.closure_authorized is False
        assert with_successor.closure_authorized is True

    def test_every_blocking_reason_says_what_it_is(self):
        result = classify(one_node(), staleness=NO_EVIDENCE)

        assert result.blocking_reasons
        for reason in result.blocking_reasons:
            assert len(reason) > 40, reason
