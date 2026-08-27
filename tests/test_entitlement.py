# SPDX-License-Identifier: Apache-2.0
"""The entitlement rule, and above all the two things no record may buy.

Every test here is ultimately one property: **no actor acquires authority over a node it
does not already dominate in the platform's own tree by writing a record.** The transfer
gate and the reach refusal are the two places that property could be lost, so they carry the
most cases.
"""
from __future__ import annotations

import pytest

from double_agent.activity import Reset, ResetKind, assess
from double_agent.disposition import Disposition, classify
from double_agent.entitlement import (
    NOT_PROOF_OF_CEASED_WRITING,
    Adoption,
    EntitlementError,
    Leg,
    adopt,
    detect_orphans,
    entitled_to_command,
    entitled_to_own,
    evaluate_transfer_gate,
    grant_transitive_reach,
    spend_transitive_reach,
)
from double_agent.lineage import build_ledger
from double_agent.ports import TerminalNotification
from double_agent.signals import SignalShape

from .conftest import FakePlatform, at, node

PROGRESSING = assess([Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(10))
STALLED = assess([Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(9999))
NO_EVIDENCE = assess([], now=at(10))


def tree(*edges):
    """Build a ledger from ``(node_id, parent_id)`` pairs."""
    return build_ledger(FakePlatform(nodes=[node(n, p) for n, p in edges]))


def assessed(ledger, **dispositions):
    """Assess every node, forcing each named node's disposition through real inputs."""
    out = {}
    for ledger_node in ledger:
        want = dispositions.get(ledger_node.node_id, "running")
        staleness = {"running": PROGRESSING, "stalled": STALLED, "unreachable": NO_EVIDENCE}[
            want if want in ("running", "stalled", "unreachable") else "running"
        ]
        out[ledger_node.node_id] = classify(ledger_node, staleness=staleness)
    return out


def notified(ledger_platform, node_id, status="completed"):
    ledger_platform.notifications[node_id] = [
        TerminalNotification(instant=at(10), status=status, record_identity="r1")
    ]


def parent_child(parent_status=None, child="c", parent="p"):
    """A two-node tree whose parent may carry a terminal notification."""
    platform = FakePlatform(nodes=[node(parent), node(child, parent)])
    platform.activity[parent] = [at(0)]
    platform.activity[child] = [at(0)]
    if parent_status is not None:
        notified(platform, parent, parent_status)
    ledger = build_ledger(platform)
    assessments = {
        parent: classify(ledger.nodes[parent], staleness=PROGRESSING),
        child: classify(ledger.nodes[child], staleness=PROGRESSING),
    }
    return ledger, assessments


def detection_for(ledger, assessments, node_id):
    for found in detect_orphans(ledger, assessments):
        if found.node_id == node_id:
            return found
    raise AssertionError(f"no detection produced for {node_id!r}")


def passing_gate(ledger, detection, **overrides):
    kwargs = dict(
        adopting_party_id="taker",
        checkpoint_reference="state/cursor.json",
        resolves=lambda ref: ref == "state/cursor.json",
        validated_by_adopter=True,
        invalid_tail_rejected=True,
    )
    kwargs.update(overrides)
    return evaluate_transfer_gate(ledger, detection, **kwargs)


class TestLegOneIsStructural:
    """The ordinary case, and the one nothing can be written to change."""

    def test_the_recorded_dispatcher_is_entitled(self):
        ledger = tree(("p", None), ("c", "p"))
        result = entitled_to_command(ledger, party_id="p", node_id="c")
        assert result.entitled
        assert result.leg is Leg.DISPATCHER
        assert result.authoritative

    def test_a_stranger_is_not_entitled_and_cannot_become_so_by_writing(self):
        ledger = tree(("p", None), ("c", "p"), ("other", None))
        result = entitled_to_command(ledger, party_id="other", node_id="c")
        assert not result.entitled
        assert "structural" in result.basis

    def test_a_node_outside_the_read_lineage_reports_scope_rather_than_denial(self):
        ledger = tree(("p", None))
        result = entitled_to_command(ledger, party_id="p", node_id="absent")
        assert not result.entitled
        assert "ledger's scope" in result.basis

    def test_a_parentless_node_cannot_be_adjudicated_by_leg_one_at_all(self):
        """The measured majority case, and it must report the gap rather than a denial."""
        ledger = tree(("top", None))
        result = entitled_to_command(ledger, party_id="anyone", node_id="top")
        assert not result.entitled
        assert "recorded no parent" in result.basis


class TestTheTransferGateRefusesEveryRecordOfStopping:
    """The heart of the module. A parent that stopped is not a parent that cannot write."""

    def test_a_terminal_notification_alone_does_not_authorize_a_transfer(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        assert detection.detected and detection.death_kind == "recorded"

        gate = passing_gate(ledger, detection, checkpoint_reference=None)
        assert not gate.available
        assert any("not a checkpoint" in r for r in gate.refusals)

    def test_a_failed_notification_is_no_better_than_a_completed_one(self):
        ledger, assessments = parent_child(parent_status="failed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, checkpoint_reference="")
        assert not gate.available

    def test_an_elapsed_threshold_does_not_authorize_a_transfer(self):
        """`quietly dead` is a subtraction against a clock, and it detects but never moves."""
        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        ledger = build_ledger(platform)
        assessments = {
            "p": classify(ledger.nodes["p"], staleness=STALLED),
            "c": classify(ledger.nodes["c"], staleness=PROGRESSING),
        }
        detection = detection_for(ledger, assessments, "c")
        assert detection.detected and detection.death_kind == "inferred"

        gate = passing_gate(ledger, detection, checkpoint_reference=None)
        assert not gate.available

    def test_the_exclusion_list_is_quoted_rather_than_paraphrased(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, checkpoint_reference=None)
        joined = " ".join(gate.refusals)
        for excluded in NOT_PROOF_OF_CEASED_WRITING:
            assert excluded in joined

    def test_a_checkpoint_that_does_not_resolve_blocks_exactly_as_a_quiet_parent_does(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, resolves=lambda ref: False)
        assert not gate.available
        assert any("does not resolve" in r for r in gate.refusals)

    def test_an_unavailable_resolver_fails_closed_rather_than_open(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, resolves=None)
        assert not gate.available
        assert any("fails closed" in r for r in gate.refusals)

    def test_the_retiring_party_cannot_validate_its_own_checkpoint(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, validated_by_adopter=False)
        assert not gate.available
        assert any("adopting party has not validated" in r for r in gate.refusals)

    def test_an_unrejected_invalid_tail_blocks(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, invalid_tail_rejected=False)
        assert not gate.available


class TestTheThirdProofIsALastResort:
    """Where the platform can be read, the reading governs. Presence fails the gate."""

    def test_an_available_direct_reading_refuses_the_checkpoint_route(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, direct_reading_available=True)
        assert not gate.available
        assert any("last resort" in r for r in gate.refusals)

    def test_an_available_platform_write_capability_record_refuses_it_too(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(
            ledger, detection, platform_write_capability_record_available=True
        )
        assert not gate.available

    def test_a_non_role_writer_inside_the_boundary_refuses_it(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection, non_role_writers_present=True)
        assert not gate.available
        assert any("not a governed role" in r for r in gate.refusals)


class TestAdoptionWhenTheGateGenuinelyPasses:
    def test_all_four_conditions_produce_an_adoption_naming_them(self):
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        gate = passing_gate(ledger, detection)
        assert gate.available
        assert set(gate.conditions_relied_on) == {
            "governed_writers_enumerable",
            "platform_readings_unavailable",
            "checkpoint_resolves",
            "validated_and_adopted",
        }

        adoption = adopt(gate)
        assert adoption.node_id == "c"
        assert adoption.checkpoint_reference == "state/cursor.json"

    def test_adoption_then_entitles_on_leg_two(self):
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        result = entitled_to_command(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=assessments,
            resolves=lambda ref: ref == "state/cursor.json",
        )
        assert result.entitled
        assert result.leg is Leg.ADOPTION
        assert result.authoritative

    def test_a_genuine_adoption_is_refused_without_assessments_to_re_derive_against(self):
        """Omitting `assessments` does not widen leg 2 -- it fails it closed, the same
        direction an unchecked resolver already fails `evaluate_transfer_gate`."""
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        result = entitled_to_command(
            ledger, party_id="taker", node_id="c", adoptions=[adoption]
        )
        assert not result.entitled
        assert "not currently a detected orphan" in result.basis

    def test_a_genuine_adoption_is_refused_without_a_resolver_to_re_derive_against(self):
        """Same failure direction, for the OTHER structural half. `assessments` alone is
        not enough once a resolver is also required -- omitting it fails leg 2 closed
        rather than falling back to trusting `conditions_relied_on`'s own say-so."""
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        result = entitled_to_command(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=assessments,
        )
        assert not result.entitled
        assert "no reference resolver was supplied" in result.basis

    def test_a_legitimately_adopted_checkpoint_that_stops_resolving_no_longer_entitles(self):
        """The new refusal this repair introduces: a checkpoint is not a one-time proof
        good forever. If it stops resolving after adoption -- deleted, rotated, corrupted
        -- leg 2 now revokes rather than continuing to trust the record it once produced.
        Before this repair leg 2 never looked at the checkpoint again once adopted."""
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        result = entitled_to_command(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=assessments,
            resolves=lambda ref: False,  # the checkpoint no longer resolves
        )
        assert not result.entitled
        assert "does not resolve when checked again now" in result.basis

    def test_a_refused_gate_raises_and_names_what_failed(self):
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(
            ledger, detection_for(ledger, assessments, "c"), checkpoint_reference=None
        )
        with pytest.raises(EntitlementError) as excinfo:
            adopt(gate)
        assert "ownership does not move" in str(excinfo.value)

    def test_an_adoption_must_name_its_checkpoint(self):
        with pytest.raises(EntitlementError):
            Adoption(
                node_id="c",
                adopting_party_id="taker",
                checkpoint_reference="   ",
                conditions_relied_on=(),
            )

    def test_a_live_parented_node_is_never_adoptable(self):
        ledger, assessments = parent_child(parent_status=None)
        detection = detection_for(ledger, assessments, "c")
        assert not detection.detected
        gate = passing_gate(ledger, detection)
        assert not gate.available
        assert any("strict subset of detection" in r for r in gate.refusals)


class TestEntitledToOwnIsTheLifecycleOwnershipQuestion:
    """`entitled_to_own` is the sibling predicate :func:`reconciliation.reconcile` reads
    ownership from -- genuinely different from `entitled_to_command` at exactly one point:
    whether the node itself must still be live. These tests exercise that difference
    directly, at the unit level `entitled_to_command`'s own tests above already get."""

    def test_ownership_survives_the_nodes_own_transition_to_terminal(self):
        """The regression this predicate exists to close: a dispatcher that is genuinely
        dead stays dead regardless of what the child does next. Adopt while `c` is running,
        then let `c` itself finish (a genuine 'completed' notification) -- the same adoption
        record must still entitle its adopter to ownership."""
        ledger, assessments = parent_child(parent_status="failed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        notified(platform, "p", "failed")
        notified(platform, "c", "completed")
        finished_ledger = build_ledger(platform)
        finished_assessments = {
            "p": classify(finished_ledger.nodes["p"], staleness=PROGRESSING),
            "c": classify(
                finished_ledger.nodes["c"], staleness=PROGRESSING, outcome_complete=True
            ),
        }
        assert finished_assessments["c"].disposition is Disposition.FINISHED

        result = entitled_to_own(
            finished_ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=finished_assessments,
            resolves=lambda ref: ref == "state/cursor.json",
        )
        assert result.entitled
        assert result.leg is Leg.ADOPTION

    def test_command_authority_and_ownership_genuinely_disagree_at_that_same_state(self):
        """The two predicates are siblings, not synonyms: at the exact state the row above
        proves ownership survives, `entitled_to_command` correctly refuses -- there is
        nothing live left to command."""
        ledger, assessments = parent_child(parent_status="failed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        notified(platform, "p", "failed")
        notified(platform, "c", "completed")
        finished_ledger = build_ledger(platform)
        finished_assessments = {
            "p": classify(finished_ledger.nodes["p"], staleness=PROGRESSING),
            "c": classify(
                finished_ledger.nodes["c"], staleness=PROGRESSING, outcome_complete=True
            ),
        }

        result = entitled_to_command(
            finished_ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=finished_assessments,
            resolves=lambda ref: ref == "state/cursor.json",
        )
        assert not result.entitled
        assert "not currently a detected orphan" in result.basis

    def test_a_forged_adoption_over_a_live_parent_is_refused(self):
        """The forged-adoption regression this predicate must NOT reopen: a live parent
        never entitles, no matter how permissive the resolver."""
        ledger, assessments = parent_child(parent_status=None)
        forged = Adoption(
            node_id="c",
            adopting_party_id="x",
            checkpoint_reference="state/cursor.json",
            conditions_relied_on=("checkpoint_resolves",),
        )
        result = entitled_to_own(
            ledger,
            party_id="x",
            node_id="c",
            adoptions=[forged],
            assessments=assessments,
            resolves=lambda ref: True,
        )
        assert not result.entitled
        assert result.leg is None
        assert "is not a terminal disposition of any kind" in result.basis

    def test_fails_closed_without_a_resolver(self):
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)

        result = entitled_to_own(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=assessments,
        )
        assert not result.entitled
        assert "no reference resolver was supplied" in result.basis

    def test_refuses_a_record_naming_no_conditions_even_over_a_genuinely_dead_parent(self):
        ledger, assessments = parent_child(parent_status="completed")
        forged = Adoption(
            node_id="c",
            adopting_party_id="taker",
            checkpoint_reference="state/cursor.json",
            conditions_relied_on=(),
        )
        result = entitled_to_own(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[forged],
            assessments=assessments,
            resolves=lambda ref: True,
        )
        assert not result.entitled
        assert "rests on no named conditions" in result.basis


class TestAForgedAdoptionIsVoidWhenRead:
    """A directly constructed Adoption is a record like any other, and a record naming a
    party and a node is not by itself proof of anything -- exactly as a forged
    TransitiveReach is not proof of ancestry until leg 3 re-checks ``dominates``. Leg 2 must
    re-derive the equally re-derivable structural half: whether the node is genuinely a
    detected orphan right now. Every case here reproduces a shape an independent review of
    this package once found actually entitling, before this repair.
    """

    def test_a_forged_adoption_over_a_live_parented_sibling_subtree_is_refused(self):
        """root -> a -> b, plus root -> x. x is unrelated to b and b's parent a is alive."""
        ledger = tree(("root", None), ("a", "root"), ("b", "a"), ("x", "root"))
        assessments = assessed(ledger)
        assert assessments["a"].disposition is Disposition.RUNNING  # a is alive

        forged = Adoption(
            node_id="b",
            adopting_party_id="x",
            checkpoint_reference="anything-non-empty",
            conditions_relied_on=(),
        )
        result = entitled_to_command(
            ledger, party_id="x", node_id="b", adoptions=[forged], assessments=assessments
        )
        assert not result.entitled
        assert result.leg is None
        assert not result.authoritative

    def test_a_forged_adoption_over_ones_own_ancestor_is_refused(self):
        ledger = tree(("root", None), ("a", "root"), ("b", "a"))
        assessments = assessed(ledger)
        forged = Adoption(
            node_id="root",
            adopting_party_id="b",
            checkpoint_reference="anything-non-empty",
            conditions_relied_on=(),
        )
        result = entitled_to_command(
            ledger, party_id="b", node_id="root", adoptions=[forged], assessments=assessments
        )
        assert not result.entitled

    def test_a_forged_adoption_naming_zero_conditions_is_refused_even_over_a_real_orphan(self):
        """`adopt()` never produces an empty `conditions_relied_on` -- `available` requires
        all four. An empty tuple is the record's own tell that no gate produced it, and this
        holds even where the target genuinely is a detected orphan."""
        ledger, assessments = parent_child(parent_status="completed")
        detection = detection_for(ledger, assessments, "c")
        assert detection.detected

        forged = Adoption(
            node_id="c",
            adopting_party_id="stranger",
            checkpoint_reference="anything-non-empty",
            conditions_relied_on=(),
        )
        result = entitled_to_command(
            ledger,
            party_id="stranger",
            node_id="c",
            adoptions=[forged],
            assessments=assessments,
        )
        assert not result.entitled
        assert "no named conditions" in result.basis

    def test_the_real_gate_still_entitles_after_the_repair(self):
        """The behaviour the repair protects: a real gate pass still entitles on leg 2,
        given the same resolver used to pass the gate in the first place."""
        ledger, assessments = parent_child(parent_status="completed")
        gate = passing_gate(ledger, detection_for(ledger, assessments, "c"))
        adoption = adopt(gate)
        result = entitled_to_command(
            ledger,
            party_id="taker",
            node_id="c",
            adoptions=[adoption],
            assessments=assessments,
            resolves=lambda ref: ref == "state/cursor.json",
        )
        assert result.entitled
        assert result.leg is Leg.ADOPTION
        assert result.authoritative

    def test_a_forged_adoption_over_a_genuinely_orphaned_node_is_refused(self):
        """An earlier narrowing of this rule re-derived orphan status at use time, which
        correctly closed the live-parented case. It left a genuinely orphaned node --
        parent dead on a recorded terminal notification -- open to a stranger's
        forged-but-well-shaped `Adoption` naming four conditions it never actually
        satisfied: without also re-deriving whether the checkpoint resolves, this call
        used to return `entitled=True, leg=ADOPTION, authoritative=True`. It must now fail
        closed -- with no resolver at all, which is the exact call shape that used to
        entitle."""
        ledger, assessments = parent_child(parent_status="failed", child="b", parent="a")
        detection = detection_for(ledger, assessments, "b")
        assert detection.detected and detection.death_kind == "recorded"
        assert assessments["a"].disposition is Disposition.DEAD
        assert not ledger.dominates("x", "b")

        forged = Adoption(
            node_id="b",
            adopting_party_id="x",
            checkpoint_reference="anything-non-empty",
            conditions_relied_on=(
                "governed_writers_enumerable",
                "platform_readings_unavailable",
                "checkpoint_resolves",
                "validated_and_adopted",
            ),
        )
        result = entitled_to_command(
            ledger, party_id="x", node_id="b", adoptions=[forged], assessments=assessments
        )
        assert not result.entitled
        assert result.leg is None
        assert not result.authoritative
        assert "no reference resolver was supplied" in result.basis

    def test_a_forged_adoption_over_a_genuine_orphan_is_refused_even_with_an_honest_resolver(self):
        """The same residual, attacked harder: supplying a real resolver does not help the
        forger, because the checkpoint it forged was never real to begin with."""
        ledger, assessments = parent_child(parent_status="failed", child="b", parent="a")
        real_checkpoint_store = {"state/cursor.json"}

        forged = Adoption(
            node_id="b",
            adopting_party_id="x",
            checkpoint_reference="anything-non-empty",
            conditions_relied_on=(
                "governed_writers_enumerable",
                "platform_readings_unavailable",
                "checkpoint_resolves",
                "validated_and_adopted",
            ),
        )
        result = entitled_to_command(
            ledger,
            party_id="x",
            node_id="b",
            adoptions=[forged],
            assessments=assessments,
            resolves=lambda ref: ref in real_checkpoint_store,
        )
        assert not result.entitled
        assert result.leg is None
        assert "does not resolve when checked again now" in result.basis


class TestDetectionIsTheUnionAndAdoptionIsASubset:
    """Narrowing detection to adoption's bar produced disjoint populations once already."""

    def test_a_recorded_death_is_detected(self):
        ledger, assessments = parent_child(parent_status="completed")
        assert detection_for(ledger, assessments, "c").death_kind == "recorded"

    def test_an_inferred_death_is_detected_too(self):
        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        ledger = build_ledger(platform)
        assessments = {
            "p": classify(ledger.nodes["p"], staleness=STALLED),
            "c": classify(ledger.nodes["c"], staleness=PROGRESSING),
        }
        assert detection_for(ledger, assessments, "c").death_kind == "inferred"

    def test_an_unobservable_parent_is_not_read_as_a_death(self):
        """`unreachable` is a statement about observability, not about the parent."""
        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        ledger = build_ledger(platform)
        assessments = {
            "p": classify(ledger.nodes["p"], staleness=NO_EVIDENCE),
            "c": classify(ledger.nodes["c"], staleness=PROGRESSING),
        }
        detection = detection_for(ledger, assessments, "c")
        assert not detection.detected
        assert assessments["p"].disposition is Disposition.UNREACHABLE

    def test_a_parentless_node_is_outside_the_sweep_by_construction(self):
        ledger = tree(("top", None))
        detection = detection_for(ledger, assessed(ledger), "top")
        assert not detection.detected
        assert detection.unreachable_by_construction
        assert "structural gap" in detection.basis

    def test_an_unassessed_parent_is_not_treated_as_alive(self):
        ledger = tree(("p", None), ("c", "p"))
        only_child = {"c": classify(ledger.nodes["c"], staleness=PROGRESSING)}
        detection = detection_for(ledger, only_child, "c")
        assert not detection.detected
        assert "Not assessed is not alive" in detection.basis

    def test_a_node_that_is_not_running_or_stalled_is_not_swept(self):
        platform = FakePlatform(nodes=[node("p"), node("c", "p")])
        notified(platform, "p", "completed")
        notified(platform, "c", "completed")
        ledger = build_ledger(platform)
        assessments = {
            n.node_id: classify(n, staleness=PROGRESSING) for n in ledger
        }
        assert not detection_for(ledger, assessments, "c").detected


class TestTransitiveReachCannotLeaveTheSubtree:
    """Ancestry is the half no record can create, and it is what bounds leg 3."""

    def test_a_non_ancestor_cannot_be_granted_reach(self):
        ledger = tree(("a", None), ("b", "a"), ("stranger", None))
        with pytest.raises(EntitlementError) as excinfo:
            grant_transitive_reach(
                ledger, issuer_id="stranger", node_id="b", signal_shape=SignalShape.CANCEL
            )
        assert "already dominate" in str(excinfo.value)

    def test_a_grandparent_may_reach_a_grandchild_it_dominates(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = grant_transitive_reach(
            ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
        )
        result = entitled_to_command(
            ledger,
            party_id="a",
            node_id="c",
            reaches=[reach],
            signal_shape=SignalShape.CANCEL,
        )
        assert result.entitled
        assert result.leg is Leg.TRANSITIVE_REACH

    def test_the_reach_is_not_authoritative_on_its_own_record(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = grant_transitive_reach(
            ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
        )
        result = entitled_to_command(
            ledger,
            party_id="a",
            node_id="c",
            reaches=[reach],
            signal_shape=SignalShape.CANCEL,
        )
        assert not result.authoritative

    def test_a_reach_entitles_only_the_signal_it_names(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = grant_transitive_reach(
            ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
        )
        result = entitled_to_command(
            ledger,
            party_id="a",
            node_id="c",
            reaches=[reach],
            signal_shape=SignalShape.SUSPEND,
        )
        assert not result.entitled

    def test_omitting_the_signal_excludes_leg_three_rather_than_widening_it(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = grant_transitive_reach(
            ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
        )
        result = entitled_to_command(ledger, party_id="a", node_id="c", reaches=[reach])
        assert not result.entitled

    def test_a_spent_reach_entitles_nothing(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = spend_transitive_reach(
            grant_transitive_reach(
                ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
            )
        )
        result = entitled_to_command(
            ledger,
            party_id="a",
            node_id="c",
            reaches=[reach],
            signal_shape=SignalShape.CANCEL,
        )
        assert not result.entitled

    def test_a_reach_cannot_be_spent_twice(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = spend_transitive_reach(
            grant_transitive_reach(
                ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
            )
        )
        with pytest.raises(EntitlementError):
            spend_transitive_reach(reach)

    def test_a_callers_own_stored_copy_reads_spent_too(self):
        """Nothing in the package held a reach registry, so single-use was a CALLER
        convention -- a caller holding the object `grant_transitive_reach` returned in its
        own list, who then called `spend_transitive_reach` without writing the returned
        value back, kept re-entitling on its own stale copy indefinitely. `a` is `c`'s
        GRANDPARENT (never its direct dispatcher) so only a genuine leg-3 reach can entitle
        it, isolating the property from leg 1."""
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        reach = grant_transitive_reach(
            ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.CANCEL
        )
        stored = [reach]  # the caller's own store -- never overwritten below, on purpose

        spend_transitive_reach(reach)  # caller "forgets" to write the returned value back

        assert stored[0].spent is True
        result = entitled_to_command(
            ledger,
            party_id="a",
            node_id="c",
            reaches=stored,
            signal_shape=SignalShape.CANCEL,
        )
        assert not result.entitled

    def test_an_upward_shape_has_no_entitlement_to_extend(self):
        ledger = tree(("a", None), ("b", "a"), ("c", "b"))
        with pytest.raises(EntitlementError) as excinfo:
            grant_transitive_reach(
                ledger, issuer_id="a", node_id="c", signal_shape=SignalShape.HAZARD
            )
        assert "relayed" in str(excinfo.value)

    def test_a_party_cannot_grant_itself_reach_to_itself(self):
        ledger = tree(("a", None))
        with pytest.raises(EntitlementError):
            grant_transitive_reach(
                ledger, issuer_id="a", node_id="a", signal_shape=SignalShape.CANCEL
            )

    def test_a_forged_reach_over_an_undominated_node_is_void_when_read(self):
        """The record can be fabricated. Reading it still refuses, because ancestry cannot."""
        ledger = tree(("a", None), ("b", "a"), ("stranger", None))
        forged = grant_transitive_reach(
            ledger, issuer_id="a", node_id="b", signal_shape=SignalShape.CANCEL
        )
        forged = type(forged)(
            issuer_id="stranger", node_id="b", signal_shape=SignalShape.CANCEL
        )
        result = entitled_to_command(
            ledger,
            party_id="stranger",
            node_id="b",
            reaches=[forged],
            signal_shape=SignalShape.CANCEL,
        )
        assert not result.entitled
        assert "void" in result.basis
