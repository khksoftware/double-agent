# SPDX-License-Identifier: Apache-2.0
"""The signal protocol, and the limit it refuses to paper over."""
from __future__ import annotations

import pytest

from double_agent.ports import SignalOutcome, UnsupportedCapability
from double_agent.signals import (
    SIGNAL_TOKEN,
    Adjudication,
    NonCompliance,
    Signal,
    SignalError,
    SignalShape,
    adjudicate,
    parse_signal,
    send,
)

from .conftest import FakePlatform, all_capabilities

CANCEL = Signal(shape=SignalShape.CANCEL, handle="n1", reason="the approach is wrong")
ADJUDICABLE = SignalOutcome(delivered=True, adjudicable=True)
ADVISORY = SignalOutcome(delivered=True, adjudicable=False)


class TestAdvisoryIsNotDefiance:
    """The heart of it: an absence of evidence is not evidence of refusal."""

    def test_a_running_recipient_makes_non_compliance_an_obstacle_report(self):
        result = adjudicate(CANCEL, ADJUDICABLE, complied=False, recipient_was_running=True)

        assert result.non_compliance is NonCompliance.OBSTACLE_REPORT
        assert result.attributable is False
        assert "mid-turn" in result.basis

    def test_the_running_case_wins_even_when_the_transport_claims_adjudicable(self):
        """A resume boundary only exists for a node that already stopped.

        If the transport says otherwise about a running node, the structural fact governs.
        """
        result = adjudicate(CANCEL, ADJUDICABLE, complied=False, recipient_was_running=True)

        assert result.adjudicable is False

    def test_a_stopped_recipient_with_a_recorded_sender_is_defiance(self):
        result = adjudicate(CANCEL, ADJUDICABLE, complied=False, recipient_was_running=False)

        assert result.non_compliance is NonCompliance.DEFIANCE
        assert result.attributable is True

    def test_no_recipient_side_record_is_an_obstacle_report_however_clear_the_send(self):
        result = adjudicate(CANCEL, ADVISORY, complied=False, recipient_was_running=False)

        assert result.non_compliance is NonCompliance.OBSTACLE_REPORT
        assert result.attributable is False
        assert "no sender identity" in result.basis

    def test_a_platform_with_no_adjudicable_signals_can_never_attribute_defiance(self):
        """Such a platform gets the whole protocol and no attribution. That is correct."""
        for running in (True, False):
            result = adjudicate(
                CANCEL, ADVISORY, complied=False, recipient_was_running=running
            )
            assert result.attributable is False

    def test_compliance_characterises_nothing(self):
        result = adjudicate(CANCEL, ADJUDICABLE, complied=True, recipient_was_running=True)

        assert result.non_compliance is NonCompliance.NOT_APPLICABLE


class TestHazardIsRelayedNotAdjudicated:
    HAZARD = Signal(
        shape=SignalShape.HAZARD, handle="n1", risk="stopping now corrupts the index"
    )

    def test_it_travels_upward(self):
        assert self.HAZARD.shape.upward is True
        assert SignalShape.CANCEL.upward is False

    def test_it_is_never_adjudicated(self):
        result = adjudicate(self.HAZARD, ADJUDICABLE, complied=False, recipient_was_running=False)

        assert result.non_compliance is NonCompliance.NOT_APPLICABLE
        assert result.attributable is False
        assert "relayed and answered" in result.basis

    def test_it_is_not_sent_through_the_delivery_channel(self):
        platform = FakePlatform()

        with pytest.raises(SignalError, match="not delivered through this channel"):
            send(platform, self.HAZARD)


class TestStatusIsNotAMessage:
    def test_the_shape_enum_does_not_contain_it(self):
        assert "status" not in {s.value for s in SignalShape}

    def test_parsing_one_is_refused_with_the_reason(self):
        with pytest.raises(SignalError) as excinfo:
            parse_signal(f"{SIGNAL_TOKEN}: status\nhandle: n1")

        assert "never sent" in str(excinfo.value)
        assert "nothing to adjudicate" in str(excinfo.value)


class TestEachShapeCarriesWhatMakesItActionable:
    def test_a_cancel_must_carry_its_reason(self):
        with pytest.raises(SignalError, match="cannot be argued against"):
            Signal(shape=SignalShape.CANCEL, handle="n1")

    def test_a_suspend_must_name_the_durable_artifact(self):
        with pytest.raises(SignalError, match="the work is lost rather than parked"):
            Signal(shape=SignalShape.SUSPEND, handle="n1")

    def test_an_override_must_carry_the_answer_that_was_owed(self):
        with pytest.raises(SignalError, match="the instruction again, louder"):
            Signal(shape=SignalShape.OVERRIDE, handle="n1")

    def test_a_hazard_must_name_the_specific_risk(self):
        with pytest.raises(SignalError, match="A general objection is not a hazard"):
            Signal(shape=SignalShape.HAZARD, handle="n1")

    def test_every_shape_must_name_a_node(self):
        with pytest.raises(SignalError, match="must name the node"):
            Signal(shape=SignalShape.CANCEL, handle="  ", reason="because")


class TestWireForm:
    def test_round_trip(self):
        for signal in (
            CANCEL,
            Signal(shape=SignalShape.SUSPEND, handle="n2", durable_artifact="state/cursor"),
            Signal(shape=SignalShape.OVERRIDE, handle="n3", answer="proceed; the risk is accepted"),
            Signal(shape=SignalShape.HAZARD, handle="n4", risk="the index would be left partial"),
        ):
            assert parse_signal(signal.render()) == signal

    def test_ordinary_prose_is_not_a_signal(self):
        with pytest.raises(SignalError, match="Ordinary prose is not"):
            parse_signal("Please stop what you are doing, it is the wrong approach.")

    def test_an_unknown_shape_is_named(self):
        with pytest.raises(SignalError, match="unknown signal shape"):
            parse_signal(f"{SIGNAL_TOKEN}: obliterate\nhandle: n1")

    def test_a_signal_without_a_handle_is_refused(self):
        with pytest.raises(SignalError, match="no 'handle' line"):
            parse_signal(f"{SIGNAL_TOKEN}: cancel\nreason: because")


class TestDelivery:
    def test_delivery_reports_what_the_platform_made_of_it(self):
        platform = FakePlatform(signal_adjudicable=False)

        outcome = send(platform, CANCEL)

        assert outcome.delivered is True
        assert outcome.adjudicable is False
        assert platform.signal_log[0][0] == "n1"
        assert platform.signal_log[0][1] == "cancel"

    def test_a_platform_claiming_adjudicable_while_declaring_it_cannot_is_refused(self):
        """Both cannot be true, and the contradiction is caught rather than believed."""
        platform = FakePlatform(caps=all_capabilities(adjudicable_signals=False))
        platform.signal_adjudicable = True

        # The fake honours its own declaration, so force the contradiction directly.
        platform.send_signal = lambda *_a, **_k: SignalOutcome(delivered=True, adjudicable=True)

        with pytest.raises(UnsupportedCapability, match="cannot both be true"):
            send(platform, CANCEL)


class TestClientNeutrality:
    def test_the_wire_token_carries_no_host_bookkeeping(self):
        """A protocol token cannot be renamed after anyone implements against it.

        The design's own draft used a work-item identifier here, which would have made one
        host's bookkeeping permanent in everybody's wire format.
        """
        assert SIGNAL_TOKEN == "double-agent-signal"
        assert not any(ch.isdigit() for ch in SIGNAL_TOKEN)
        assert "gov" not in SIGNAL_TOKEN.lower()
