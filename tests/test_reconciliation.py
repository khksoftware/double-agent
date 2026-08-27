# SPDX-License-Identifier: Apache-2.0
"""The reconciliation, and the two populations that make an unregistered dispatch visible."""
from __future__ import annotations

from dataclasses import replace

import pytest

from double_agent.activity import Reset, ResetKind, assess
from double_agent.disposition import classify
from double_agent.entitlement import Adoption, adopt, detect_orphans, evaluate_transfer_gate
from double_agent.envelope import FENCE_MARKER, parse_envelope
from double_agent.lineage import build_ledger
from double_agent.ports import RegistryEntry, TerminalNotification
from double_agent.reconciliation import (
    ReconciliationRefused,
    reconcile,
)

from .conftest import FakePlatform, at, node

PROGRESSING = assess([Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(10))
LONG_STALE = assess([Reset(instant=at(0), kind=ResetKind.CURSOR_ADVANCE)], now=at(9999))


def envelope(outcome="the deliverable", heartbeat="120", control_record="control"):
    fields = {
        "assigned_outcome": outcome,
        "durable_cursor": "state/cursor.json",
        "checkpoints": "step-one",
        "heartbeat_seconds": heartbeat,
        "role_label": "[Worker]",
        "external_wait": "",
        "control_record": control_record,
    }
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return parse_envelope(f"{FENCE_MARKER}\n{body}\n")


def session(nodes, statuses=None, activity=None):
    """Build a ledger plus per-node assessments from stated inputs."""
    platform = FakePlatform(nodes=[node(n, p) for n, p in nodes])
    for node_id, instants in (activity or {}).items():
        platform.activity[node_id] = [at(s) for s in instants]
    for node_id, status in (statuses or {}).items():
        platform.notifications[node_id] = [
            TerminalNotification(instant=at(10), status=status, record_identity="r1")
        ]
    return build_ledger(platform)


def assess_all(ledger, staleness_for=None, **kwargs):
    staleness_for = staleness_for or {}
    return {
        n.node_id: classify(
            n, staleness=staleness_for.get(n.node_id, PROGRESSING), **kwargs
        )
        for n in ledger
    }


def one_finished(node_id="w1", parent="sup"):
    ledger = session([(parent, None), (node_id, parent)], statuses={node_id: "completed"})
    assessments = {
        parent: classify(ledger.nodes[parent], staleness=PROGRESSING),
        node_id: classify(
            ledger.nodes[node_id], staleness=PROGRESSING, outcome_complete=True
        ),
    }
    return ledger, assessments


def orphaned_by_dead_dispatcher(node_id="w1", parent="sup"):
    """`parent` reaches a recorded TERMINAL disposition (`dead`, on a 'failed' notification)
    and `node_id` is left running beneath it -- a genuine orphan, unlike `one_finished`'s
    supervisor, which stays alive (`running`) and is not adjudicable by adoption at all."""
    ledger = session([(parent, None), (node_id, parent)], statuses={parent: "failed"})
    assessments = {
        parent: classify(ledger.nodes[parent], staleness=PROGRESSING),
        node_id: classify(ledger.nodes[node_id], staleness=PROGRESSING),
    }
    return ledger, assessments


def adopt_validly(ledger, assessments, node_id="w1", adopting_party_id="taker",
                   checkpoint_reference="state/cursor.json"):
    """Build a real `Adoption` through the transfer gate, never by hand -- an unvalidated
    record is not what `reconcile`'s own trust boundary should honour. `resolves` matches the
    one this helper's caller must also pass to `reconcile`, so the checkpoint re-validates
    identically at adoption time and at reconciliation time."""
    detection = next(
        d for d in detect_orphans(ledger, assessments) if d.node_id == node_id
    )
    gate = evaluate_transfer_gate(
        ledger,
        detection,
        adopting_party_id=adopting_party_id,
        checkpoint_reference=checkpoint_reference,
        resolves=lambda ref: ref == checkpoint_reference,
        validated_by_adopter=True,
        invalid_tail_rejected=True,
    )
    return adopt(gate)


class TestTheTwoPopulations:
    """An unregistered dispatch is visible only because two populations exist."""

    def test_a_node_with_no_envelope_is_reported_beside_the_inventory(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},  # the supervisor carries none
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert result.unregistered_dispatches == ("sup",)
        assert [w.handle for w in result.workers] == ["w1"]
        assert not result.closure_authorized

    def test_an_unregistered_dispatch_is_never_an_inventory_row(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert "sup" not in [w.handle for w in result.workers]

    def test_in_the_tree_and_not_in_the_registry_is_the_normal_state(self):
        """The registry is pruned on stop; reporting that would bury the real finding."""
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            registry_entries=[],
            session_handle="top",
        )
        assert result.unregistered_dispatches == ()
        assert not any("registry" in r for r in result.blocking_reasons)

    def test_in_the_registry_and_not_in_the_tree_is_marked_unresolvable(self):
        """Marked unresolvable, correctly -- and that marking must NOT also block closure.
        The registry is per-HOST, not per-session, so a concurrently-running second
        session's own entries are the module's own stated normal case, not a defect. Blocking
        on this made the closure gate unsatisfiable on any harness with a second session
        live, for a condition nothing here can distinguish from a real foreign reference."""
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            registry_entries=[
                RegistryEntry(entry_id="e1", node_id="from-another-session", fields={})
            ],
            session_handle="top",
        )
        resolution = result.registry_resolutions[0]
        assert not resolution.resolved
        assert resolution.node_id == "from-another-session"
        assert not any("registry entry" in r for r in result.blocking_reasons)

    def test_a_foreign_registry_entry_does_not_block_an_otherwise_closeable_session(self):
        """The full property: a session with nothing else wrong stays authorized even while
        carrying an unresolvable registry entry from a concurrently-running second session."""
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessments = {
            "w1": classify(ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True)
        }
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            registry_entries=[
                RegistryEntry(entry_id="e1", node_id="from-another-session", fields={})
            ],
            session_handle="top",
        )
        assert not result.registry_resolutions[0].resolved
        assert result.closure_authorized, result.blocking_reasons

    def test_the_detectability_claim_is_never_widened_by_the_evidence(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert result.unregistered_dispatches
        assert result.unregistered_dispatch_detectable is False


class TestTerminalEvidenceCannotBeFaked:
    def test_a_readable_placeholder_does_not_authorize_a_closure(self):
        """Measured: `(no notification observed)` reads as disclosure, functions as authority.

        **One node deliberately.** With any other node in the session still running, the
        closure blocks for that reason instead and this test would pass while proving nothing
        about the placeholder -- which is exactly the shape of masking it exists to catch.
        """
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessment = replace(
            classify(ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True),
            terminal_evidence="(no notification observed)",
        )
        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized
        assert any("describes the absence of evidence" in r for r in result.blocking_reasons)

    @pytest.mark.parametrize(
        "placeholder",
        [
            "(no notification observed)",
            "none recorded",
            "not observed",
            "unknown",
            "n/a",
            "pending",
            # False negatives an independent review found -- an underscore defeats a \b
            # boundary, and several ordinary absence words were simply absent.
            "nothing observed",
            "no_status",
            "not_observed",
            "unrecorded",
            "nil",
            "empty",
            "void",
            "silence",
            "zero notifications",
            "-",
            "?",
            "()",
        ],
    )
    def test_the_whole_family_of_absence_citations_is_refused(self, placeholder):
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessment = replace(
            classify(ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True),
            terminal_evidence=placeholder,
        )
        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized


class TestTheAbsenceWideningsMeasuredFalsePositiveCost:
    """Recorded by execution rather than left as an unverified docstring claim.

    The pattern's own widening (broader boundary classes, seven more words) also fires
    against a hyphen- or underscore-separated SEGMENT of an ordinary identifier, not only
    against the status-shaped tokens it was built to catch. **This is not narrowed back** --
    doing so would reopen the twelve genuine misses recorded just above (`no_status`,
    `not_observed` and siblings) -- so these citations are refused on purpose, in the
    direction the module's own docstring says is the safer failure."""

    @pytest.mark.parametrize(
        "legitimate_citation",
        [
            "state/nil-cursor.json",
            "runs/void-migration/terminal.json",
            "notification 'empty-set-probe' at 12:00Z",
            "agent-zero/notify.json",
            "transcripts/silence-room/terminal.json",
            "record nothing-burger-42.json",
            "unrecorded-run/notify.json",
            "notifications/zero.json",
            "task_empty_queue/terminal-record.json",
        ],
    )
    def test_a_legitimate_citation_built_from_a_widened_word_is_refused(
        self, legitimate_citation
    ):
        """9 of 13 constructed legitimate citations, refused after the pattern was widened --
        a measured cost. Each of these names a real artifact; none describes an absence.
        Accepted on purpose: the false positive blocks a closure that should
        have gone through, which the module's own docstring names as the safer direction."""
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessment = replace(
            classify(ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True),
            terminal_evidence=legitimate_citation,
        )
        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized

    @pytest.mark.parametrize(
        "legitimate_citation",
        [
            "transcripts/2026-08-26/notification-r1.json",
            "sha256:9e0f1c terminal record for w1",
        ],
    )
    def test_a_legitimate_citation_with_no_widened_word_still_authorizes(
        self, legitimate_citation
    ):
        """The two negative controls from the same reproduction, kept green: a citation with
        no absence-word segment at all is unaffected by the widening and still authorizes."""
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessment = replace(
            classify(ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True),
            terminal_evidence=legitimate_citation,
        )
        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert result.closure_authorized, result.blocking_reasons

    def test_whitespace_only_evidence_blocks(self):
        ledger, assessments = one_finished()
        assessments["w1"] = replace(assessments["w1"], terminal_evidence="   ")
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized

    def test_a_status_less_notification_yields_no_terminal_evidence(self):
        platform = FakePlatform(nodes=[node("w1")])
        platform.notifications["w1"] = [
            TerminalNotification(instant=at(10), status=None, record_identity="r1")
        ]
        ledger = build_ledger(platform)
        assessment = classify(ledger.nodes["w1"], staleness=PROGRESSING)
        assert assessment.terminal_evidence is None

        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized

    def test_genuine_evidence_with_a_complete_outcome_authorizes(self):
        """One node, finished, evidenced, outcome complete -- the only shape that authorizes."""
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessments = {
            "w1": classify(
                ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True
            )
        }
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert result.closure_authorized, result.blocking_reasons

    def test_a_running_supervisor_blocks_the_session_it_supervises(self):
        """Recorded because the fixture's own shape made this look like a defect once."""
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert not result.closure_authorized
        assert any("sup is registered nonterminal" in r for r in result.blocking_reasons)


class TestNonterminalBlocksUnconditionally:
    def test_a_stalled_node_blocks_even_with_a_successor(self):
        ledger = session([("w1", None)])
        assessments = {"w1": classify(ledger.nodes["w1"], staleness=LONG_STALE)}
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={"w1": LONG_STALE},
            now=at(9999),
            successors={"w1": "w2"},
            session_handle="top",
        )
        assert not result.closure_authorized
        assert any("registered nonterminal" in r for r in result.blocking_reasons)

    def test_a_successor_removes_only_the_successor_line(self):
        ledger = session([("w1", None)])
        assessments = {"w1": classify(ledger.nodes["w1"], staleness=LONG_STALE)}
        common = dict(
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={"w1": LONG_STALE},
            now=at(9999),
            session_handle="top",
        )
        without = reconcile(ledger, **common)
        with_successor = reconcile(ledger, successors={"w1": "w2"}, **common)

        assert any("no registered successor" in r for r in without.blocking_reasons)
        assert not any("no registered successor" in r for r in with_successor.blocking_reasons)
        assert not with_successor.closure_authorized

    def test_a_probe_age_beyond_the_heartbeat_adds_its_own_line(self):
        ledger = session([("w1", None)])
        assessments = {"w1": classify(ledger.nodes["w1"], staleness=LONG_STALE)}
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope(heartbeat="120")},
            staleness={"w1": LONG_STALE},
            now=at(9999),
            session_handle="top",
        )
        assert any("beyond its declared" in r for r in result.blocking_reasons)


class TestProbeAgeComesFromTheClockNotFromOutput:
    def test_recent_emission_does_not_shorten_the_probe_age(self):
        """The whole point of the clock, carried across the seam into the gate."""
        ledger = session([("w1", None)], activity={"w1": [9998]})
        assessments = {"w1": classify(ledger.nodes["w1"], staleness=LONG_STALE)}
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={"w1": LONG_STALE},
            now=at(9999),
            session_handle="top",
        )
        record = result.workers[0]
        assert record.last_probe_age_seconds == pytest.approx(9999.0)

    def test_no_reset_evidence_yields_no_probe_age_rather_than_zero(self):
        ledger = session([("w1", None)])
        assessments = {"w1": classify(ledger.nodes["w1"], staleness=PROGRESSING)}
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert result.workers[0].last_probe_age_seconds is None


class TestOwnershipFollowsTheTransfer:
    def test_an_adopted_node_reports_its_adopter_not_its_dead_dispatcher(self):
        """`sup` is genuinely `dead` here (a recorded 'failed' notification) -- the situation
        the test's own name describes. `one_finished`'s `sup` stays alive and cannot be
        adopted at all: an earlier version of this test forged an `Adoption` directly over
        that live parent and `reconcile` trusted it with no adjudication whatsoever."""
        ledger, assessments = orphaned_by_dead_dispatcher()
        resolves = lambda ref: ref == "state/cursor.json"
        adoption = adopt_validly(ledger, assessments)
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            adoptions=[adoption],
            resolves=resolves,
            session_handle="top",
        )
        record = [w for w in result.workers if w.handle == "w1"][0]
        assert record.owner == "taker"

    def test_an_adopted_node_still_reports_its_adopter_once_it_goes_terminal(self):
        """The regression this pass exists to close: `entitled_to_command`'s leg 2 requires
        the node to presently be a detected orphan (`RUNNING`/`STALLED`), so reading
        ownership off that leg made a validly adopted node silently hand itself back to its
        dead dispatcher the instant it finished -- with a full resolver supplied and no
        blocking reason. `sup` is genuinely dead throughout; `w1` is adopted while running
        and then genuinely finishes (a real 'completed' notification). The same real
        adoption record must still be honoured."""
        ledger, assessments = orphaned_by_dead_dispatcher()
        resolves = lambda ref: ref == "state/cursor.json"
        adoption = adopt_validly(ledger, assessments)

        finished_ledger = session(
            [("sup", None), ("w1", "sup")], statuses={"sup": "failed", "w1": "completed"}
        )
        finished_assessments = {
            "sup": classify(finished_ledger.nodes["sup"], staleness=PROGRESSING),
            "w1": classify(
                finished_ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True
            ),
        }
        result = reconcile(
            finished_ledger,
            assessments=finished_assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            adoptions=[adoption],
            resolves=resolves,
            session_handle="top",
        )
        record = [w for w in result.workers if w.handle == "w1"][0]
        assert record.owner == "taker"

    def test_a_forged_adoption_over_a_live_dispatcher_is_not_honoured(self):
        """A regression for a forged-adoption defect this suite once missed: `sup` running,
        `w1` running beneath it, so `sup` has not reached a terminal disposition at all --
        the parent is alive, not merely terminal-but-already-settled. An `Adoption` built
        directly (bypassing `adopt()` and the transfer gate entirely -- Python cannot forbid
        that) must not move ownership regardless; `entitled_to_own` refuses it (leg=None,
        because the dispatcher has not reached a terminal disposition), and `reconcile` must
        fall back to the recorded parent rather than the forged record, disclosing the
        discarded claim rather than silently reverting it."""
        ledger = session([("sup", None), ("w1", "sup")])
        assessments = {
            "sup": classify(ledger.nodes["sup"], staleness=PROGRESSING),
            "w1": classify(ledger.nodes["w1"], staleness=PROGRESSING),
        }
        forged = Adoption(
            node_id="w1",
            adopting_party_id="x",
            checkpoint_reference="state/cursor.json",
            conditions_relied_on=("checkpoint_resolves",),
        )
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            adoptions=[forged],
            resolves=lambda ref: True,
            session_handle="top",
        )
        record = [w for w in result.workers if w.handle == "w1"][0]
        assert record.owner == "sup"
        assert any(
            "w1" in reason and "claimed adoption" in reason
            for reason in result.blocking_reasons
        ), "a forged, discarded adoption must be disclosed too, not just refused"

    def test_an_adoption_is_not_honoured_without_a_resolver_supplied_to_reconcile(self):
        """Fails closed exactly as leg 2 does when `resolves` is omitted: 'I could not
        check' must never read the same as 'I checked and it was fine' -- even for a
        validly-gated adoption, if the caller does not also give `reconcile` a resolver.

        A discarded adoption must also be DISCLOSED, never just silently reverted: the
        fail-closed default was fail-closed for entitlement and fail-OPEN for reporting --
        a definite wrong owner with nothing appended to `blocking_reasons`. This asserts
        the disclosure half explicitly, not only the ownership half."""
        ledger, assessments = orphaned_by_dead_dispatcher()
        adoption = adopt_validly(ledger, assessments)
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            adoptions=[adoption],
            session_handle="top",
            # no `resolves` supplied
        )
        record = [w for w in result.workers if w.handle == "w1"][0]
        assert record.owner == "sup"
        assert not result.closure_authorized
        assert any(
            "w1" in reason and "claimed adoption" in reason
            for reason in result.blocking_reasons
        ), "a discarded adoption must be disclosed, not just silently reverted"

    def test_without_an_adoption_the_spawn_record_stands(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        record = [w for w in result.workers if w.handle == "w1"][0]
        assert record.owner == "sup"

    def test_a_parentless_node_takes_the_session_handle(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        record = [w for w in result.workers if w.handle == "sup"][0]
        assert record.owner == "top"

    def test_an_unattributable_owner_is_emitted_empty_rather_than_invented(self):
        ledger, assessments = one_finished()
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="",
        )
        record = [w for w in result.workers if w.handle == "sup"][0]
        assert record.owner == ""
        assert any("unattributable" in r for r in result.blocking_reasons)


class TestTheRefusalOverAnUnreadableTree:
    def test_an_unreadable_node_refuses_the_whole_reconciliation(self):
        ledger, assessments = one_finished()
        with pytest.raises(ReconciliationRefused) as excinfo:
            reconcile(
                ledger,
                assessments=assessments,
                envelopes={"sup": envelope(), "w1": envelope()},
                staleness={},
                now=at(20),
                unreadable_node_ids=["w9"],
                session_handle="top",
            )
        assert "w9" in str(excinfo.value)

    def test_an_unassessed_node_blocks_rather_than_being_dropped(self):
        ledger, assessments = one_finished()
        del assessments["sup"]
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"sup": envelope(), "w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        assert any("Not assessed is not finished" in r for r in result.blocking_reasons)


class TestTheRecordCarriesTheOutcomeFields:
    def test_an_abandoned_outcome_is_carried_from_the_assessment(self):
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        platform_envelope = envelope()
        assessment = classify(
            ledger.nodes["w1"],
            staleness=PROGRESSING,
            envelope=platform_envelope,
            resolves=lambda ref: "abandonment" in ref,
            abandonment_claimed=True,
        )
        assert assessment.outcome_abandoned

        result = reconcile(
            ledger,
            assessments={"w1": assessment},
            envelopes={"w1": platform_envelope},
            staleness={},
            now=at(20),
            resolves=lambda ref: "abandonment" in ref,
            session_handle="top",
        )
        assert result.workers[0].outcome_abandoned
        assert result.closure_authorized, result.blocking_reasons

    def test_a_forged_outcome_abandoned_with_no_resolving_record_is_not_honoured(self):
        """A regression for a forged-declaration defect this suite once missed: an
        `Assessment` built directly with `outcome_abandoned=True` -- bypassing `classify`'s
        own refusal entirely, which Python cannot forbid -- and no abandonment record behind
        it at all (no `resolves` supplied to `reconcile`). Before this repair `reconcile`
        trusted the bare field and authorized the closure; it must not any more."""
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        forged = replace(
            classify(ledger.nodes["w1"], staleness=PROGRESSING),
            outcome_abandoned=True,
        )
        result = reconcile(
            ledger,
            assessments={"w1": forged},
            envelopes={"w1": envelope()},
            staleness={},
            now=at(20),
            session_handle="top",
            # no `resolves` supplied -- the forged claim has nothing to re-validate against
        )
        assert not result.workers[0].outcome_abandoned
        assert not result.closure_authorized
        assert any(
            "carries outcome_abandoned=True" in r and "no abandonment" in r
            for r in result.blocking_reasons
        )

    def test_the_envelope_supplies_the_contract_fields(self):
        ledger = session([("w1", None)], statuses={"w1": "completed"})
        assessments = {
            "w1": classify(
                ledger.nodes["w1"], staleness=PROGRESSING, outcome_complete=True
            )
        }
        result = reconcile(
            ledger,
            assessments=assessments,
            envelopes={"w1": envelope(outcome="ship the thing", heartbeat="300")},
            staleness={},
            now=at(20),
            session_handle="top",
        )
        record = result.workers[0]
        assert record.assigned_outcome == "ship the thing"
        assert record.heartbeat_interval_seconds == 300
        assert record.durable_cursor == "state/cursor.json"
        assert record.checkpoints == ("step-one",)
