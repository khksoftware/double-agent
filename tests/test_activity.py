# SPDX-License-Identifier: Apache-2.0
"""The clock, and specifically the two directions it must not fail in.

Both named cases below are the ones the obvious implementation gets wrong, so both are
written as behaviour tests rather than as unit checks of a helper.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from double_agent.activity import (
    ClockState,
    Regime,
    Reset,
    ResetKind,
    assess,
    derive_resets,
)

from .conftest import at


class TestEmissionIsNotProgress:
    def test_a_retry_loop_emitting_constantly_does_not_read_alive(self):
        """False `alive`: the case a transcript-maximum clock is blind to by construction.

        A node emitting a call-and-result pair every second for ten minutes has produced no
        validated completion, no cursor advance, no external-wait transition and no child
        delta. Its emission is dense and its progress is nil.
        """
        # Emission is not even an input to `assess`, which is the point: there is no
        # argument through which dense output could make this node read healthy.
        result = assess([], now=at(600), dispatched_at=at(0))

        assert result.state is ClockState.UNKNOWN
        assert result.state is not ClockState.ALIVE
        assert result.last_reset_at is None
        assert "no reset evidence" in result.basis

    def test_the_dispatch_instant_is_not_treated_as_a_reset(self):
        result = assess([], now=at(10), dispatched_at=at(0))

        assert result.state is ClockState.UNKNOWN
        assert "started and not" in result.basis


class TestUnknownIsNeverADefault:
    def test_no_evidence_is_unknown_rather_than_stalled(self):
        """The other direction. A node with no derivable evidence is not condemned either.

        `UNKNOWN` blocks a caller and makes it say why. Reading it as `STALLED` would demand
        a successor for a node that may be working perfectly.
        """
        result = assess([], now=at(100000), dispatched_at=at(0))

        assert result.state is ClockState.UNKNOWN
        assert result.state is not ClockState.STALLED
        assert result.age is None

    def test_now_earlier_than_the_last_reset_is_unknown_not_alive(self):
        """`now` before the last reset produced a negative age and read ALIVE, with no
        guard -- failing toward alive on a clock reading that is already nonsensical
        (clock skew, a stale `now`, resets read out of order), which delays a stall verdict
        rather than surfacing that the reading itself cannot be trusted."""
        result = assess(
            [Reset(instant=at(1000), kind=ResetKind.CURSOR_ADVANCE)], now=at(1)
        )

        assert result.state is ClockState.UNKNOWN
        assert result.state is not ClockState.ALIVE
        assert result.age == timedelta(seconds=1) - timedelta(seconds=1000)


class TestThresholds:
    @pytest.mark.parametrize(
        "elapsed, expected",
        [
            (0, ClockState.ALIVE),
            (119, ClockState.ALIVE),
            (120, ClockState.PROBE_DUE),
            (299, ClockState.PROBE_DUE),
            (300, ClockState.STALLED),
            (10_000, ClockState.STALLED),
        ],
    )
    def test_default_regime_boundaries(self, elapsed, expected):
        reset = Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE, detail="cursor advanced")

        assert assess([reset], now=at(elapsed)).state is expected

    def test_a_declared_external_wait_extends_the_terminate_threshold(self):
        reset = Reset(instant=at(0), kind=ResetKind.VALIDATED_COMPLETION)

        without = assess([reset], now=at(400))
        with_wait = assess([reset], now=at(400), external_wait_declared=True)

        assert without.state is ClockState.STALLED
        assert with_wait.state is ClockState.PROBE_DUE
        assert with_wait.extended is True
        assert "extended by a declared external wait" in with_wait.basis

    def test_the_extension_still_ends(self):
        """The carve-out is a longer bound, not the absence of one."""
        reset = Reset(instant=at(0), kind=ResetKind.VALIDATED_COMPLETION)

        assert assess([reset], now=at(601), external_wait_declared=True).state is ClockState.STALLED

    def test_the_latest_reset_wins_regardless_of_input_order(self):
        early = Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)
        late = Reset(instant=at(280), kind=ResetKind.OWNED_CHILD_DELTA, detail="child appeared")

        result = assess([late, early], now=at(300))

        assert result.last_reset_at == at(280)
        assert result.state is ClockState.ALIVE
        assert "owned_child_delta" in result.basis

    def test_an_unordered_regime_is_refused(self):
        with pytest.raises(ValueError, match="ordered probe <= terminate <= extension"):
            Regime(probe_after=timedelta(minutes=9), terminate_after=timedelta(minutes=5))


class TestDeriveResets:
    def test_a_checkpoint_that_does_not_exist_is_not_a_completion(self):
        resets = derive_resets(checkpoint_states=[("out/step-1.json", False, at(50))])

        assert resets == ()

    def test_a_checkpoint_that_exists_without_a_change_instant_produces_no_reset(self):
        """Existence alone is not an instant.

        Dating it `now` would make every reading reset the clock, which is the same
        inversion as counting emission: the act of looking would become evidence of
        progress.
        """
        resets = derive_resets(checkpoint_states=[("out/step-1.json", True, None)])

        assert resets == ()

    def test_a_resolving_checkpoint_with_an_instant_is_a_completion(self):
        resets = derive_resets(checkpoint_states=[("out/step-1.json", True, at(50))])

        assert len(resets) == 1
        assert resets[0].kind is ResetKind.VALIDATED_COMPLETION
        assert resets[0].instant == at(50)

    def test_an_unchanged_cursor_is_not_progress(self):
        resets = derive_resets(
            cursor_state=("state/cursor", "digest-a", at(90)),
            prior_cursor_digest="digest-a",
        )

        assert resets == ()

    def test_a_changed_cursor_is_progress(self):
        resets = derive_resets(
            cursor_state=("state/cursor", "digest-b", at(90)),
            prior_cursor_digest="digest-a",
        )

        assert len(resets) == 1
        assert resets[0].kind is ResetKind.CURSOR_ADVANCE

    def test_a_cursor_with_no_prior_reading_is_not_progress(self):
        """A first reading establishes a baseline; it does not establish an advance."""
        resets = derive_resets(cursor_state=("state/cursor", "digest-b", at(90)))

        assert resets == ()

    def test_all_four_kinds_are_derivable_and_ordered(self):
        resets = derive_resets(
            checkpoint_states=[("out/step-1.json", True, at(10))],
            cursor_state=("state/cursor", "b", at(20)),
            prior_cursor_digest="a",
            external_wait_transitions=[(at(30), "waiting on remote index")],
            child_deltas=[(at(40), "child c1 acquired a record")],
        )

        assert [r.kind for r in resets] == [
            ResetKind.VALIDATED_COMPLETION,
            ResetKind.CURSOR_ADVANCE,
            ResetKind.EXTERNAL_WAIT_TRANSITION,
            ResetKind.OWNED_CHILD_DELTA,
        ]
        assert [r.instant for r in resets] == [at(10), at(20), at(30), at(40)]
