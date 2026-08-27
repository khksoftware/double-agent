# SPDX-License-Identifier: Apache-2.0
"""The registry reconciliation: what was dispatched against what was registered.

A closure gate that reconciles a **caller-supplied** inventory has a hole in it that no
amount of care at the gate can close: an empty inventory and a session with no delegation
are byte-identical. This module is the missing call site -- it *populates* the inventory
from records the platform wrote, so the gate has something truthful to refuse.

## The one thing a self-populated inventory can never prove

**It cannot detect a dispatch nobody registered.** Two views of one source cannot disagree,
so diffing the inventory against the tree it was built from is empty by construction.

**What closes it is a second population, and this module is the only place both exist.** The
platform writes a record for every dispatch, nested ones included, whether or not any party
declared it. Only this module knows which of those nodes carries an **envelope**. So:

- the tree's node set is the complete population, complete *by construction* rather than by
  count;
- the inventory's rows are the subset carrying a resolvable envelope;
- **the difference is :attr:`Reconciliation.unregistered_dispatches`, reported BESIDE the
  inventory and never inside it.**

It cannot be an inventory row. A node with no envelope has no assigned outcome, and an
inventory row without one is not a row -- it is a shape the consuming gate refuses outright.
An implementer who instead suppresses such a node to keep the build alive deletes the record
the difference existed to surface, which is the failure this arrangement is built to prevent.

**And the inverse direction is the informative one.** *In the tree and not in the registry* is
the **normal** state of a finished agent -- one measured session held a single registry entry
against 255 dispatch records, because the registry is pruned on stop. Reporting that as an
unregistered dispatch would flood the finding with the ordinary case and bury the real one.
Only the envelope answers this question. *In the registry and not in the tree* is the
direction worth reading, and it is reported as **unresolvable**, never invented into a node.

## Terminal evidence, and the two ways to fake it

A terminal state with no evidence blocks. That refusal is easy to satisfy dishonestly, and
both routes have been executed rather than imagined:

*A readable placeholder.* Evidence is validated as a non-empty string and nothing more, so
``"(no notification observed)"`` **reads as a disclosure and functions as an authorization.**
This module refuses a string that describes the absence of evidence.

*A status-less notification.* Entirely factual -- the platform really did emit it -- and
therefore not caught by any placeholder rule. It is a monitor event, not a terminal
disposition, and it may not become terminal evidence whatever it says about itself.

## The pressure repair this module must never take

A stalled node blocks unconditionally, and no field here changes that. The tempting move on a
deadline is to **reclassify it as dead** so the terminal branch applies -- which fabricates a
terminal disposition into a closure gate for a node nobody established was dead. **A stalled
node is registered stalled and the closure stays refused** until it reaches a disposition
backed by a record, or a successor completes the outcome.

## Ownership is read from the transfer, not from the spawn record

The platform's parent link is written at spawn and **nothing rewrites it**, so after an
adoption an unconditional owner field names the *dead dispatcher* while the node is genuinely
owned by its adopter. The adoption record is what moves it -- and that record must resolve,
or ``owner`` becomes a declaration and reintroduces, one field along, exactly the hole the
entitlement rule spends itself removing.

**Ownership is a different question from command authority, and the two come apart at exactly
the moment a closure is taken over.** :func:`entitlement.entitled_to_command` answers *"may
this party command this node right now"*, and its leg 2 requires the node to presently be a
**detected orphan** -- correctly, since there is nothing live to command once a node goes
terminal. Reading *lifecycle ownership* off that same leg means a validly adopted node hands
itself back to its dead dispatcher the instant it finishes, silently -- the identical hole
this section opens with, one field later. **This module never re-derives either question
itself.** :func:`entitlement.entitled_to_own` is the canonical predicate for whether a recorded
adoption still holds *as an ownership fact* -- re-checking the record's own well-formedness,
the dispatcher's terminal disposition (decoupled from the node's own current state), and
checkpoint resolution afresh, rather than trusting the record's own say-so -- and
:func:`reconcile` consults it, reusing the same ``resolves`` callable and the same
reference-computation :func:`envelope.control_record_reference` uses for
``outcome_abandoned``, rather than re-implementing either. **Omitting ``resolves`` fails both
closed** -- an adoption is not honoured and a claimed abandonment is not settled -- the same
direction every other unchecked-resolver path in this package already fails, and **a discarded
adoption is never silent**: :func:`reconcile` appends a blocking reason naming the node, the
party, and exactly which check failed, because "I could not check" must never read the same as
"I checked and it was fine" -- for a claim of ownership no less than for a claim of
abandonment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Optional, Sequence, Tuple

from .activity import Staleness
from .disposition import SUCCESSOR_REQUIRING, TERMINAL, Assessment, Disposition
from .entitlement import Adoption, Leg, entitled_to_own
from .envelope import Envelope, control_record_reference
from .lineage import Ledger
from .ports import RegistryEntry

__all__ = [
    "ABSENCE_DESCRIBING",
    "Reconciliation",
    "ReconciliationRefused",
    "RegistryResolution",
    "WorkerRecord",
    "reconcile",
]


ABSENCE_DESCRIBING = re.compile(
    r"(?<![A-Za-z0-9])(?:no|none|nothing|not|never|absent|missing|unknown|unavailable|n/?a|"
    r"pending|tbd|nil|empty|void|silence|zero|unrecorded)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
"""Matches evidence text that describes the absence of evidence.

**A non-empty string is not evidence.** The consuming gate can only check that something was
written, so a citation reading *"(no notification observed)"* passes it while asserting the
exact opposite of what the field is for. This pattern is deliberately broad and deliberately
applied only to the evidence field, where a false negative costs an authorized closure over a
node nobody established had ended.

**Word boundaries are ``(?<![A-Za-z0-9])``/``(?![A-Za-z0-9])``, not ``\\b``, deliberately.**
``\\b`` treats ``_`` as a word character, so it never fires between ``no`` and the underscore
in ``no_status`` or ``not_observed`` -- a real gap this pattern was found to have, since an
evidence field is exactly the kind of text a caller writes as a status-shaped token rather
than as prose.

**That same widening has a measured cost, accepted deliberately rather than left implicit.**
Because ``-`` and ``_`` are not in the boundary classes above, an absence-word also fires
where it is only a hyphen- or underscore-separated SEGMENT of an ordinary identifier --
``'state/nil-cursor.json'``, ``'agent-zero/notify.json'``, ``'runs/void-migration/terminal.json'``.
Measured against 13 constructed evidence citations spanning the shapes this field actually
takes (a repository-relative path, a quoted notification identity, a digest-plus-prose
citation, a bare handle reference), **9 newly refuse a legitimate citation that the narrower,
pre-widening pattern accepted.** This is not silently re-narrowed back, because doing so would
reopen the twelve genuine misses the widening was built to close (``no_status``,
``not_observed`` and their siblings). **The trade is accepted in this direction on purpose**:
a false positive here blocks a closure that should have gone through; a false negative
authorizes one that should not have. Given the module's own stated priority -- *"a false
negative costs an authorized closure over a node nobody established had ended"* -- refusing
too much is the safer failure. **A caller whose legitimate evidence citations are built from
one of these seventeen words as a bare path or identifier segment should choose a different
token for that segment**, rather than expect this pattern to distinguish the two uses; nothing
in this field's shape lets it.
"""

_PUNCTUATION_ONLY = re.compile(r"^[\s\-–—?()\[\]{}:;,._*~]*$")
"""A string made ENTIRELY of punctuation/symbols, once stripped, describes nothing -- a bare
``-``, ``?`` or ``()`` reads as a placeholder shrug, not as an evidence citation. Checked
separately from :data:`ABSENCE_DESCRIBING` because none of these characters is a *word* an
alternation of words can name."""


class ReconciliationRefused(RuntimeError):
    """The reconciliation will not produce a partial inventory.

    Raised where the population itself cannot be read. **A closure disposition computed over a
    tree with a hole in it is a measurement of what could be read, presented as a measurement
    of what is there** -- so this refuses rather than emitting rows for the nodes that happened
    to be legible.
    """


@dataclass(frozen=True)
class WorkerRecord:
    """One node's registration, in this framework's own vocabulary.

    **The names here are the framework's, not any consuming registry's.** A host binds these
    to whatever its own gate requires; that binding is the host's, and putting it here would
    make every adopter inherit one registry's field names.
    """

    handle: str
    owner: str
    state: str
    assigned_outcome: str
    durable_cursor: str
    checkpoints: Tuple[str, ...]
    heartbeat_interval_seconds: int
    last_probe_age_seconds: Optional[float]
    outcome_complete: bool
    outcome_abandoned: bool
    terminal_evidence: Optional[str] = None
    successor_handle: Optional[str] = None


@dataclass(frozen=True)
class RegistryResolution:
    """One registry entry, resolved against the dispatch tree or explicitly not.

    An entry naming a node this session's tree does not contain is **unresolvable**. It is
    never invented into a node, and never quietly dropped: a registry is per-host and may
    legitimately carry another session's entries, so the honest product is a marked entry
    rather than either a fabrication or a silence.
    """

    entry_id: str
    node_id: Optional[str]
    resolved: bool
    basis: str


@dataclass(frozen=True)
class Reconciliation:
    """What was dispatched, what was registered, and what stops a closure."""

    workers: Tuple[WorkerRecord, ...]
    unregistered_dispatches: Tuple[str, ...]
    registry_resolutions: Tuple[RegistryResolution, ...]
    blocking_reasons: Tuple[str, ...]

    @property
    def closure_authorized(self) -> bool:
        """Whether a closure may be authorized over this session.

        Authorization is the **absence of every blocking reason**. A session can be entirely
        terminal and still block, which is the common case and the useful one.
        """
        return not self.blocking_reasons

    @property
    def unregistered_dispatch_detectable(self) -> bool:
        """Always ``False``, and it is a statement about the *inventory*, not about this object.

        A self-populated inventory cannot prove that nothing went unregistered. That claim is
        permanently unavailable and this property says so rather than being quietly widened by
        :attr:`unregistered_dispatches` -- which is evidence from **outside** the inventory,
        obtained by comparing two populations the inventory alone does not have. Both are true
        at once, and collapsing them would convert real evidence into a false guarantee.
        """
        return False


def _describes_absence(text: str) -> bool:
    if ABSENCE_DESCRIBING.search(text):
        return True
    stripped = text.strip()
    return bool(stripped) and bool(_PUNCTUATION_ONLY.match(stripped))


def _terminal_evidence_for(assessment: Assessment) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(evidence, refusal)`` for one node. Exactly one is ever non-``None``."""
    evidence = assessment.terminal_evidence
    if evidence is None:
        return None, (
            f"{assessment.node_id} is {assessment.disposition.value!r} with no terminal "
            f"evidence recorded. Its disposition rests on an inference, and an inference "
            f"cannot distinguish a stopped worker from a thinking one."
        )
    if not evidence.strip():
        return None, (
            f"{assessment.node_id} carries terminal evidence that is empty once stripped"
        )
    if _describes_absence(evidence):
        return None, (
            f"{assessment.node_id} carries terminal evidence that describes the absence of "
            f"evidence: {evidence!r}. It reads as a disclosure and would function as an "
            f"authorization, which is the more dangerous of the two directions."
        )
    return evidence, None


def reconcile(
    ledger: Ledger,
    *,
    assessments: Mapping[str, Assessment],
    envelopes: Mapping[str, Envelope],
    staleness: Mapping[str, Staleness],
    now: datetime,
    adoptions: Sequence[Adoption] = (),
    resolves: Optional[Callable[[str], bool]] = None,
    registry_entries: Sequence[RegistryEntry] = (),
    unreadable_node_ids: Sequence[str] = (),
    session_handle: str = "",
    successors: Mapping[str, str] = {},
) -> Reconciliation:
    """Build the inventory from platform records, and report what blocks a closure.

    ``unreadable_node_ids`` names nodes whose records could not be read at all. **Any such
    node refuses the whole reconciliation**, by raising: there is no state to map it to that
    would not be a fabricated disposition for a node nobody could read.

    ``staleness`` supplies each node's clock reading. ``last_probe_age_seconds`` is derived
    from its **last reset**, never from the node's most recent output -- feeding emission into
    this field would put output churn into the consuming gate's own refusal, which is the
    substitution the activity clock exists to refuse one layer down.

    ``session_handle`` names the top-level actor, used as ``owner`` for a node the platform
    recorded no parent for. Where it is empty such a node is still emitted, with its ownership
    stated as unattributable rather than invented.

    ``resolves`` answers whether a named control-record reference resolves. It is supplied by
    the caller from the platform, exactly as :func:`disposition.classify` and
    :func:`entitlement.entitled_to_own` already require it. **Two things fail closed on its
    absence, and neither is a bare copy of the other's logic:**

    - a recorded ``adoption`` is honoured for OWNERSHIP only where
      :func:`entitlement.entitled_to_own` -- consulted here, not re-implemented -- returns it
      entitled on :attr:`entitlement.Leg.ADOPTION`. This is deliberately **not**
      :func:`entitlement.entitled_to_command`: that function's leg 2 additionally requires the
      node to presently be a detected orphan, which itself requires the node still be
      ``RUNNING``/``STALLED`` -- correct for "may this party command this node right now", and
      wrong for "whose lifecycle record is this", which must survive the node's own transition
      to a terminal disposition. ``entitled_to_own`` re-checks the record's own well-formedness
      and the dispatcher's terminal disposition instead, and still requires ``resolves`` to
      re-check the checkpoint now rather than trust the record's own claim. **Every discarded
      adoption is reported**: this function appends a blocking reason carrying the
      entitlement's own basis, so an unadjudicable or void adoption refuses the closure rather
      than silently reverting ownership to the dead dispatcher;
    - a node's claimed ``outcome_abandoned`` is honoured for settlement only where the
      abandonment record :func:`envelope.control_record_reference` computes still resolves now,
      the same check :func:`disposition.classify` performs when the claim is first made.

    A directly-constructed :class:`entitlement.Adoption` or a directly-constructed
    :class:`disposition.Assessment` with ``outcome_abandoned=True`` -- bypassing
    :func:`entitlement.adopt` or :func:`disposition.classify` entirely, which Python cannot
    forbid -- is therefore re-validated here rather than trusted, exactly as the module
    docstring's own invariant requires.
    """
    unreadable = tuple(unreadable_node_ids)
    if unreadable:
        raise ReconciliationRefused(
            "refusing to build an inventory over a tree with unreadable nodes: "
            + ", ".join(sorted(unreadable))
            + ". A disposition computed over what could be read is not a disposition over "
            "what is there, and emitting rows for the legible remainder would present the "
            "first as the second."
        )

    # ---- ownership: consult the OWNERSHIP predicate, never the command one ---
    # `entitled_to_command`'s leg 2 additionally requires the node to presently be a detected
    # orphan, which itself requires the node still be RUNNING/STALLED -- correct for "may
    # this party command this node right now", wrong for "whose lifecycle record is this".
    # Reading ownership off `entitled_to_command` is exactly how a validly adopted node lost
    # its adoption the moment it went terminal, silently, with a full resolver supplied.
    # `entitled_to_own` is the sibling predicate for the ownership question: it re-derives the
    # record's own well-formedness and the dispatcher's terminal disposition (never the
    # node's own current state) and still fails closed on an unresolved checkpoint, exactly
    # as `entitled_to_command` does -- so this stays a canonical predicate, not a second,
    # narrower copy of one.
    adopted_by = {}
    blocking: list = []
    for adoption in adoptions:
        ownership_entitlement = entitled_to_own(
            ledger,
            party_id=adoption.adopting_party_id,
            node_id=adoption.node_id,
            adoptions=adoptions,
            assessments=assessments,
            resolves=resolves,
        )
        if ownership_entitlement.leg is Leg.ADOPTION and ownership_entitlement.entitled:
            adopted_by[adoption.node_id] = adoption.adopting_party_id
        else:
            # A discarded adoption is never silent: "I could not check" and "I checked and
            # it was fine" must not read the same, for a claim of ownership any more than
            # for a claim of abandonment below.
            blocking.append(
                f"{adoption.node_id}'s claimed adoption by "
                f"{adoption.adopting_party_id!r} is not honoured: "
                f"{ownership_entitlement.basis}"
            )

    workers = []
    unregistered = []

    for node in ledger:
        assessment = assessments.get(node.node_id)
        if assessment is None:
            blocking.append(
                f"{node.node_id} was never assessed, so nothing here says what state it is "
                f"in. Not assessed is not finished."
            )
            continue

        envelope = envelopes.get(node.node_id)
        if envelope is None:
            unregistered.append(node.node_id)
            continue

        # ---- owner: the transfer moves it, the spawn record does not ----------
        if node.node_id in adopted_by:
            owner = adopted_by[node.node_id]
        elif node.parent_id is not None:
            owner = node.parent_id
        elif session_handle:
            owner = session_handle
        else:
            owner = ""
            blocking.append(
                f"{node.node_id} has no recorded parent and no session handle was supplied, "
                f"so its owner is unattributable. It is emitted with an empty owner rather "
                f"than an invented one."
            )

        # ---- probe age: from the clock's last reset, never from emission ------
        reading = staleness.get(node.node_id)
        last_reset = reading.last_reset_at if reading is not None else None
        probe_age = (now - last_reset).total_seconds() if last_reset is not None else None

        # ---- terminal evidence, and the two ways to fake it -------------------
        evidence: Optional[str] = None
        if assessment.disposition in TERMINAL:
            evidence, refusal = _terminal_evidence_for(assessment)
            if refusal is not None:
                blocking.append(refusal)
        else:
            blocking.append(
                f"{node.node_id} is registered nonterminal ({assessment.disposition.value!r}) "
                f"and was never discharged."
            )
            if probe_age is not None and probe_age > envelope.heartbeat_seconds:
                blocking.append(
                    f"{node.node_id} was last probed {probe_age:.1f}s ago, beyond its declared "
                    f"{envelope.heartbeat_seconds}s heartbeat interval."
                )

        # ---- outcome_abandoned: re-derived, never trusted as a bare declaration --------
        # `classify()` only sets this flag once an abandonment record actually resolves.
        # An `Assessment` can be constructed directly with the flag set and no such record
        # behind it -- Python cannot forbid that either -- so this re-checks the identical
        # condition `classify` already enforces, using the same reference computation and
        # the same `resolves` callable, rather than trusting the field's bare value.
        outcome_abandoned = assessment.outcome_abandoned
        if outcome_abandoned:
            abandonment_reference = control_record_reference(
                envelope, "abandonment", node.node_id
            )
            if resolves is None or not resolves(abandonment_reference):
                outcome_abandoned = False
                blocking.append(
                    f"{node.node_id} carries outcome_abandoned=True, but no abandonment "
                    f"record resolves at {abandonment_reference!r} now. `classify` refuses "
                    f"this identical claim absent a resolving record, and a bare declaration "
                    f"authorizes nothing here either: without a record that resolves, "
                    f"abandonment and success are the same governance record."
                )

        settled = assessment.outcome_complete or outcome_abandoned
        successor = successors.get(node.node_id)
        if (
            assessment.disposition in SUCCESSOR_REQUIRING
            and not settled
            and not successor
        ):
            blocking.append(
                f"{node.node_id} is {assessment.disposition.value!r} with an incomplete "
                f"outcome and no registered successor, so the work has no owner."
            )

        workers.append(
            WorkerRecord(
                handle=node.node_id,
                owner=owner,
                state=assessment.disposition.value,
                assigned_outcome=envelope.assigned_outcome,
                durable_cursor=envelope.durable_cursor,
                checkpoints=envelope.checkpoints,
                heartbeat_interval_seconds=envelope.heartbeat_seconds,
                last_probe_age_seconds=probe_age,
                outcome_complete=assessment.outcome_complete,
                outcome_abandoned=outcome_abandoned,
                terminal_evidence=evidence,
                successor_handle=successor,
            )
        )

    # ---- the registry, read in the one direction that is informative ---------
    resolutions = []
    for entry in registry_entries:
        if entry.node_id is not None and entry.node_id in ledger:
            resolutions.append(
                RegistryResolution(
                    entry_id=entry.entry_id,
                    node_id=entry.node_id,
                    resolved=True,
                    basis="this entry names a node of this session's dispatch tree",
                )
            )
            continue
        resolutions.append(
            RegistryResolution(
                entry_id=entry.entry_id,
                node_id=entry.node_id,
                resolved=False,
                basis=(
                    "this entry names no node of this session's dispatch tree. The registry "
                    "is per-host, so this is marked unresolvable rather than invented into a "
                    "node or dropped."
                ),
            )
        )
        # **No blocking reason here.** The registry is per-HOST, not per-session, and an
        # entry from a concurrently-running second session is the module's own stated
        # NORMAL case, not a defect -- appending a blocking reason for it made a legitimate,
        # unavoidable condition (another session's agents still registered) refuse every
        # closure on this harness while any other session had one outstanding. Marking the
        # entry unresolved, above, is the honest report `RegistryResolution` exists for;
        # it does not also need to stop the session it says nothing about.

    if unregistered:
        blocking.append(
            "this session contains "
            + ", ".join(sorted(unregistered))
            + " -- dispatches the platform recorded and no party registered. A closure over a "
            "session containing an unregistered dispatch is a closure over work nobody "
            "declared. These are reported beside the inventory because a row without an "
            "assigned outcome is not a row."
        )

    return Reconciliation(
        workers=tuple(workers),
        unregistered_dispatches=tuple(sorted(unregistered)),
        registry_resolutions=tuple(resolutions),
        blocking_reasons=tuple(blocking),
    )
