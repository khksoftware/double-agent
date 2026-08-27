# SPDX-License-Identifier: Apache-2.0
"""The entitlement rule: who may command a node, and what it costs to acquire that.

**One sentence governs this whole module, and everything below is machinery for keeping it
true: no actor acquires authority over a node it does not already dominate in the
platform's own tree by writing a record.**

That sentence is not a slogan. It is the property that separates a control channel from a
suggestion box, and it has been lost once already in this design's own history -- by a rule
that made entitlement satisfiable by recording an adoption, which returned the cost of a
forged control signal from *"be that exact agent"* to *"write a row"*.

## Three legs, in strict order of authority

1. **DISPATCHER** -- the party the platform itself recorded as the node's parent. Structural,
   written by the platform at spawn, and **never declared**. This is the whole of the rule in
   the ordinary case.
2. **ADOPTION** -- entitlement *transfers*, and the transfer gate below is what decides when.
   Available only for an orphan, never for a live-parented node, and carrying lifecycle
   ownership and the successor-nomination obligation with it.
3. **TRANSITIVE REACH** -- entitlement is *extended*, for **one** named signal to **one**
   named node, by a party that is **already an ancestor** of that node. It transfers no
   ownership, creates no second owner, nominates no successor, and does not survive the act.

Legs 1 and 2 are authoritative without qualification. For leg 3 the authority attaches to the
**ancestry** -- which no record can create -- and to the transport's own sender identity, never
to the reach record itself. The record only selects *which* descendant of a subtree the issuer
already dominates it is signalling, and with what. **It can never reach outside that subtree.**

## The transfer gate, and why it is this expensive

Adoption moves ownership, so it needs proof that the prior owner **can no longer write** --
not proof that it stopped. Those are different claims and only the first is safe, because a
party that stopped can resume.

Three proofs are admitted, and this module implements the third because the first two are the
platform's to expose:

- a **direct reading** of the resource;
- the **platform's authoritative record** of write capability;
- a **durable, salvageable checkpoint** the prior owner wrote and the **taking** party
  validated and adopted.

**The third is a last resort, not a cheaper route.** Where either platform reading is
available it governs and this proof is refused, which is why :func:`evaluate_transfer_gate`
takes both availabilities and fails on their *presence*.

## What is NOT proof, and this list is the reason the module exists

**A record that the prior owner STOPPED is none of the three.** Neither is an elapsed
inactivity threshold. Every item in :data:`NOT_PROOF_OF_CEASED_WRITING` is a reading taken
*of the actor* or a statement of intention, rather than an artifact a second party could
check -- and each has been proposed here, in good faith, as sufficient.

The measurement that settles the notification case rather than arguing it: in one observed
session **48 of 253 notified agent task-ids were notified more than once, one of them eight
times.** A terminal notification withdraws no write access on such a substrate. It is *"a
statement that work stopped"*, which is precisely what a checkpoint is defined not to be.

## Detection and adoption are one population with a gate, never two predicates

**Detection is the union**: a node still running or stalled whose parent reached *any*
terminal disposition -- recorded **or** inferred. **Adoption is a strict subset**, gated on
the four conditions. Every adoptable node is detected first; every detected node that cannot
be adopted is reported and escalated.

Splitting these into two independent predicates has already been tried here and the two
selected **disjoint** populations, so the sweep emitted nothing for either while reading as
though it covered both. Detecting on an inference costs nothing if the inference is wrong.
Transferring ownership on one costs a second writer.

## The seam this module does not close

**A node the platform recorded no parent for cannot be adjudicated by leg 1 and cannot be an
orphan under any predicate**, because its dispatcher is the sessionless top-level actor: there
is no parent node to classify, notify, or find a checkpoint from. On the substrate this design
was measured against that was **287 of 296 nodes**. This module reports that case by name
rather than returning a quiet ``False``, because a coverage gap that renders as a negative is
a coverage gap nobody investigates.

## Command authority and lifecycle ownership are different questions

**`entitled_to_command`'s leg 2 answers "may this party command this node right now"**, and
correctly requires the node to presently be a **detected orphan** -- there is nothing live to
command once a node goes terminal. A separate consumer needs a different question answered:
*"whose lifecycle record is this, given a transfer that already happened"* -- and reading that
off the same leg made a validly adopted node silently hand itself back to its dead dispatcher
the instant it went terminal, because :func:`detect_orphans` stops detecting the moment the
node itself leaves ``RUNNING``/``STALLED``. That requirement is right for command authority and
wrong for ownership, and conflating the two is exactly the class of defect this module's own
history already paid for once, in a different shape.

:func:`entitled_to_own` is the sibling predicate for the ownership question. Leg 2's other two
structural halves -- the record's own well-formedness (``conditions_relied_on`` is non-empty)
and the checkpoint's continued resolution -- are re-derived identically, because a record
missing either is void for either question. Only the orphan-status half is replaced, with the
dispatcher's own terminal disposition alone, decoupled from the node's current state. Both
predicates fail closed on an absent resolver, for the same reason: "I could not check" must
never read the same as "I checked and it was fine", for a claim of ownership no less than for a
claim of command authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from .disposition import TERMINAL, Assessment, Disposition
from .lineage import Ledger
from .signals import SignalShape

__all__ = [
    "Adoption",
    "Entitlement",
    "EntitlementError",
    "Leg",
    "NOT_PROOF_OF_CEASED_WRITING",
    "OrphanDetection",
    "TransferGate",
    "TransitiveReach",
    "adopt",
    "detect_orphans",
    "entitled_to_command",
    "entitled_to_own",
    "evaluate_transfer_gate",
    "grant_transitive_reach",
    "spend_transitive_reach",
]


NOT_PROOF_OF_CEASED_WRITING: Tuple[str, ...] = (
    "the actor's own report that it stopped",
    "a completion or termination notification",
    "a removed registration",
    "a released lock",
    "the absence of recent output",
    "an elapsed inactivity threshold",
)
"""Every reading that looks like proof a prior owner has stopped writing, and is not.

Each is either authored by the actor whose stopping is in question, or records an intention
rather than a state, or is a subtraction against a clock. **A computed precondition cannot be
forged and can be wrong, and for an ownership transfer those are equally disqualifying.**

This tuple is exported so a caller can quote it when refusing, rather than paraphrasing it
into something weaker.
"""


class EntitlementError(ValueError):
    """An entitlement claim is malformed, or is being constructed in a shape that is unsound."""


class Leg(Enum):
    """Which leg of the rule an entitlement rests on."""

    DISPATCHER = "dispatcher"
    """The platform recorded this party as the node's parent. Structural, never declared."""

    ADOPTION = "adoption"
    """Ownership transferred through the gate. Carries the successor obligation."""

    TRANSITIVE_REACH = "transitive_reach"
    """Extended for one signal to one already-dominated node. Transfers nothing."""


@dataclass(frozen=True)
class Entitlement:
    """Whether one party may command one node, and on what.

    ``authoritative`` is deliberately separate from ``entitled``. Legs 1 and 2 carry the
    label without qualification; leg 3 carries it on the **ancestry**, not on the record that
    names the signal -- and a caller reporting an adjudication needs to be able to say which.
    """

    entitled: bool
    party_id: str
    node_id: str
    leg: Optional[Leg]
    authoritative: bool
    basis: str


@dataclass(frozen=True)
class OrphanDetection:
    """One node's orphan status under the union predicate.

    ``death_kind`` is ``"recorded"`` where the parent's terminal disposition rests on evidence
    the platform wrote, ``"inferred"`` where it rests on the clock, and ``None`` where the
    parent has not reached a terminal disposition at all.

    **``detected`` being false is not one fact.** It can mean the node is not running, the
    parent is alive, or the platform recorded no parent at all -- and the last of those is a
    structural coverage gap rather than a negative result. ``basis`` distinguishes them.
    """

    node_id: str
    detected: bool
    parent_id: Optional[str]
    death_kind: Optional[str]
    basis: str

    @property
    def unreachable_by_construction(self) -> bool:
        """Whether this node is outside the sweep's reach rather than failing its predicate."""
        return self.parent_id is None


@dataclass(frozen=True)
class TransferGate:
    """The four conditions, evaluated, with each one's own verdict retained.

    A party claiming an agent-written boundary must **state which of the four conditions it is
    relying on, or it is not making the claim.** Keeping the individual verdicts is what makes
    that answerable rather than rhetorical, so this type never collapses to a bare boolean.
    """

    node_id: str
    adopting_party_id: str
    governed_writers_enumerable: bool
    platform_readings_unavailable: bool
    checkpoint_resolves: bool
    validated_and_adopted: bool
    refusals: Tuple[str, ...]
    checkpoint_reference: Optional[str] = None

    @property
    def available(self) -> bool:
        """Whether adoption may proceed. **All four, or none.**"""
        return not self.refusals and all(
            (
                self.governed_writers_enumerable,
                self.platform_readings_unavailable,
                self.checkpoint_resolves,
                self.validated_and_adopted,
            )
        )

    @property
    def conditions_relied_on(self) -> Tuple[str, ...]:
        """The conditions this gate is resting its claim on, named for the record."""
        named = (
            ("governed_writers_enumerable", self.governed_writers_enumerable),
            ("platform_readings_unavailable", self.platform_readings_unavailable),
            ("checkpoint_resolves", self.checkpoint_resolves),
            ("validated_and_adopted", self.validated_and_adopted),
        )
        return tuple(name for name, held in named if held)


@dataclass(frozen=True)
class Adoption:
    """A recorded transfer of lifecycle ownership.

    Constructed only by :func:`adopt`, which refuses unless the gate is available. It is a
    frozen record of a transfer that already passed, never an instrument for requesting one.
    """

    node_id: str
    adopting_party_id: str
    checkpoint_reference: str
    conditions_relied_on: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.checkpoint_reference.strip():
            raise EntitlementError(
                "an adoption must name the checkpoint it rests on. Without one there is "
                "nothing a second party validated, and the transfer is the prior owner's own "
                "say-so wearing a record's shape."
            )


@dataclass(frozen=True)
class TransitiveReach:
    """Entitlement extended for exactly one signal to exactly one node.

    Constructed only by :func:`grant_transitive_reach`, which refuses an issuer that does not
    already dominate the target. **Spent when the signal it names is adjudicated**, and a spent
    reach entitles nothing -- it does not survive the act it was granted for.

    **``spent`` is read through a shared cell, not a frozen value.** Nothing in this package
    holds a reach registry, so single-use was previously a caller CONVENTION rather than a
    property of the mechanism: :func:`spend_transitive_reach` returned a *new* object with
    ``spent=True``, and a caller holding the original in a list who forgot to write the
    returned value back kept re-entitling on it indefinitely, with no error. The cell closes
    that: every reference sharing one grant -- the object :func:`grant_transitive_reach`
    returned, and any copy of it a caller stored -- observes the SAME spent state, because
    :func:`spend_transitive_reach` mutates the cell in place rather than handing back a
    sibling the original is now stale beside. What stays frozen is the reach's *identity*
    (``issuer_id``, ``node_id``, ``signal_shape``); only its single-use state is shared.
    """

    issuer_id: str
    node_id: str
    signal_shape: SignalShape
    _spent_cell: List[bool] = field(default_factory=lambda: [False], repr=False, compare=False)

    @property
    def spent(self) -> bool:
        return self._spent_cell[0]


def entitled_to_command(
    ledger: Ledger,
    *,
    party_id: str,
    node_id: str,
    adoptions: Sequence[Adoption] = (),
    reaches: Sequence[TransitiveReach] = (),
    signal_shape: Optional[SignalShape] = None,
    assessments: Mapping[str, Assessment] = {},
    resolves: Optional[Callable[[str], bool]] = None,
) -> Entitlement:
    """Decide whether ``party_id`` may command ``node_id``, and say on which leg.

    ``signal_shape`` is required to reach leg 3 and ignored by legs 1 and 2: a transitive
    reach is granted for one *named* signal, so asking whether it entitles a party without
    naming the signal is a question the record cannot answer. Omitting it does not silently
    widen the reach -- it excludes leg 3 from consideration.

    ``assessments`` and ``resolves`` are what leg 2 re-derives against. **An ``Adoption`` is
    a record, and a record naming this party and this node is not by itself proof of
    anything** -- exactly like a :class:`TransitiveReach` naming a party and a node is not
    proof of ancestry until leg 3 re-checks :meth:`Ledger.dominates`. Leg 2 has two structural
    halves no adoption record can create: whether the node is *actually* a detected orphan
    right now, and whether the checkpoint it names *actually resolves* right now.
    :func:`detect_orphans` is the one canonical answer to the first, so leg 2 calls it rather
    than trusting the record's own say-so; ``resolves`` is the one canonical answer to the
    second, so leg 2 calls it against ``adoption.checkpoint_reference`` rather than trusting
    ``conditions_relied_on``'s bare claim that ``checkpoint_resolves`` once held. **Omitting
    either does not widen leg 2 -- both fail it closed**, the same direction
    :func:`evaluate_transfer_gate` already fails an unchecked resolver: "I could not check"
    must never read the same as "I checked and it was fine".

    The legs are tried in authority order and the first that holds is returned, so an adopted
    node's own dispatcher is still reported on leg 1 where the platform's record still says so.
    """
    node = ledger.get(node_id)
    if node is None:
        return Entitlement(
            entitled=False,
            party_id=party_id,
            node_id=node_id,
            leg=None,
            authoritative=False,
            basis=(
                f"no node {node_id!r} in the read lineage, so there is nothing to be entitled "
                f"to. This is the ledger's scope speaking, not a claim that the node does not "
                f"exist."
            ),
        )

    # ---- leg 1: the dispatcher, as the platform recorded it -------------------
    if node.parent_id is None:
        structural_note = (
            "the platform recorded no parent for this node, so leg 1 cannot adjudicate it at "
            "all -- its dispatcher is the sessionless top-level actor, which has no identity "
            "to compare against"
        )
    else:
        structural_note = f"the platform recorded {node.parent_id!r} as this node's dispatcher"
        if node.parent_id == party_id:
            return Entitlement(
                entitled=True,
                party_id=party_id,
                node_id=node_id,
                leg=Leg.DISPATCHER,
                authoritative=True,
                basis=(
                    "this party is the node's own dispatcher in the platform's own tree. "
                    "Structural, written at spawn, and not declarable by anyone."
                ),
            )

    # ---- leg 2: a recorded adoption, re-validated at use time ------------------
    # A record can be constructed directly, bypassing `adopt()` and the transfer gate
    # entirely -- Python cannot forbid that. What it CAN do is exactly what leg 3 does
    # below: re-derive the structural half no record can create, from the ledger and the
    # assessments the caller supplies, rather than trusting the record's own two string
    # fields. Whether this node is presently a detected orphan is that structural half --
    # `detect_orphans` is the one canonical predicate for it, so it is called fresh here
    # instead of a second, narrower check that could drift from it.
    orphan_detections = (
        {d.node_id: d for d in detect_orphans(ledger, assessments)} if adoptions else {}
    )
    for adoption in adoptions:
        if adoption.node_id == node_id and adoption.adopting_party_id == party_id:
            if not adoption.conditions_relied_on:
                return Entitlement(
                    entitled=False,
                    party_id=party_id,
                    node_id=node_id,
                    leg=None,
                    authoritative=False,
                    basis=(
                        "an adoption record names this party and node but rests on no named "
                        "conditions. `adopt()` never produces one of these: `TransferGate."
                        "available` requires all four conditions, and `conditions_relied_on` "
                        "then names all four, so an empty tuple is the record's own tell that "
                        "no gate produced it. The record is void."
                    ),
                )
            detection = orphan_detections.get(node_id)
            if detection is None or not detection.detected:
                return Entitlement(
                    entitled=False,
                    party_id=party_id,
                    node_id=node_id,
                    leg=None,
                    authoritative=False,
                    basis=(
                        "an adoption record names this party and node, but the node is not "
                        "currently a detected orphan of the platform's own tree -- adoption is "
                        "available only for an orphan, never for a live-parented node. The "
                        "record is void: whether a node is orphaned is the half no record can "
                        "create, and without it this is authority acquired by writing a row."
                    ),
                )
            # The second structural half: whether the checkpoint the record names actually
            # resolves, re-checked now rather than trusted from `conditions_relied_on`'s bare
            # claim. `evaluate_transfer_gate` already fails closed on an unchecked resolver at
            # adoption time; leg 2 owes the same failure direction at USE time, or the gate's
            # own discipline is undone by the very record it produced.
            if resolves is None:
                return Entitlement(
                    entitled=False,
                    party_id=party_id,
                    node_id=node_id,
                    leg=None,
                    authoritative=False,
                    basis=(
                        "an adoption record names this party and node and the node is "
                        "presently a detected orphan, but no reference resolver was supplied, "
                        f"so whether the checkpoint at {adoption.checkpoint_reference!r} "
                        "resolves could not be checked. This fails closed, exactly as an "
                        "unchecked resolver already fails the transfer gate itself: 'I could "
                        "not check' must never read the same as 'I checked and it was fine'. "
                        "The record is void."
                    ),
                )
            if not resolves(adoption.checkpoint_reference):
                return Entitlement(
                    entitled=False,
                    party_id=party_id,
                    node_id=node_id,
                    leg=None,
                    authoritative=False,
                    basis=(
                        "an adoption record names this party and node and the node is "
                        "presently a detected orphan, but the checkpoint at "
                        f"{adoption.checkpoint_reference!r} does not resolve when checked "
                        "again now. The record is void: whether a checkpoint resolves is the "
                        "other half no record can create -- exactly as ancestry is for leg 3 "
                        "-- and `conditions_relied_on` naming `checkpoint_resolves` is the "
                        "record's own say-so, not evidence."
                    ),
                )
            return Entitlement(
                entitled=True,
                party_id=party_id,
                node_id=node_id,
                leg=Leg.ADOPTION,
                authoritative=True,
                basis=(
                    f"this party adopted the node through the transfer gate, resting on "
                    f"{', '.join(adoption.conditions_relied_on)}, with the checkpoint at "
                    f"{adoption.checkpoint_reference!r}. Lifecycle ownership and the "
                    f"successor-nomination obligation moved with it, the node is still a "
                    f"detected orphan of the platform's own tree as of this read, and the "
                    f"checkpoint still resolves as of this read."
                ),
            )

    # ---- leg 3: transitive reach, bounded by ancestry --------------------------
    if signal_shape is not None:
        for reach in reaches:
            if (
                reach.node_id == node_id
                and reach.issuer_id == party_id
                and reach.signal_shape is signal_shape
                and not reach.spent
            ):
                if not ledger.dominates(party_id, node_id):
                    return Entitlement(
                        entitled=False,
                        party_id=party_id,
                        node_id=node_id,
                        leg=None,
                        authoritative=False,
                        basis=(
                            "a transitive-reach record names this party and node, and the "
                            "party is not an ancestor of the node in the platform's own tree. "
                            "The record is void: ancestry is the half no record can create, "
                            "and without it this is authority acquired by writing a row."
                        ),
                    )
                return Entitlement(
                    entitled=True,
                    party_id=party_id,
                    node_id=node_id,
                    leg=Leg.TRANSITIVE_REACH,
                    authoritative=False,
                    basis=(
                        f"this party already dominates the node in the platform's own tree and "
                        f"recorded a reach for {signal_shape.value!r} before sending. The "
                        f"authority is the ancestry, not the record; the record only selects "
                        f"which dominated descendant is signalled, and is spent on adjudication."
                    ),
                )

    return Entitlement(
        entitled=False,
        party_id=party_id,
        node_id=node_id,
        leg=None,
        authoritative=False,
        basis=(
            f"{structural_note}; no adoption by this party is recorded, and no unspent "
            f"transitive reach names this party, this node and this signal. Entitlement is "
            f"structural: nothing this party can write would change the answer."
        ),
    )


def _dispatcher_terminal_state(
    assessments: Mapping[str, Assessment], node
) -> Tuple[Optional[str], str]:
    """Whether ``node``'s own recorded dispatcher reached a terminal disposition, decoupled
    from whether ``node`` itself is presently ``RUNNING``/``STALLED``.

    This is one of :func:`detect_orphans`'s two conditions, not a second copy of it -- the
    other, whether the node itself is presently running or stalled, is that function's own
    escalation-specific half and does not belong here. :func:`entitled_to_own` needs the
    dispatcher's terminal state alone: a node that has itself gone terminal is not less owned
    by its adopter, it has only stopped needing to be detected.

    Returns ``(death_kind, basis)``. ``death_kind`` is ``"recorded"`` where the dispatcher's
    terminal disposition rests on evidence the platform wrote, ``"inferred"`` where it rests on
    the clock, and ``None`` where the dispatcher has not reached a terminal disposition, was
    never assessed, or does not exist.
    """
    if node.parent_id is None:
        return None, (
            "the platform recorded no parent for this node, so there is no dispatcher that "
            "could have reached a terminal disposition"
        )
    parent = assessments.get(node.parent_id)
    if parent is None:
        return None, (
            f"the dispatcher {node.parent_id!r} was not assessed, so nothing here says "
            f"whether it ended. Not assessed is not alive."
        )
    if parent.disposition in TERMINAL:
        return "recorded", (
            f"the dispatcher is {parent.disposition.value!r} on "
            f"{parent.terminal_evidence or 'recorded evidence'}"
        )
    if parent.disposition is Disposition.STALLED:
        return "inferred", (
            "the dispatcher is stalled on the clock, which is an inference -- enough to "
            "detect and enough to have transferred ownership through a real adoption, "
            "though never enough on its own to command"
        )
    return None, (
        f"the dispatcher is {parent.disposition.value!r}, which is not a terminal "
        f"disposition of any kind"
    )


def entitled_to_own(
    ledger: Ledger,
    *,
    party_id: str,
    node_id: str,
    adoptions: Sequence[Adoption] = (),
    assessments: Mapping[str, Assessment] = {},
    resolves: Optional[Callable[[str], bool]] = None,
) -> Entitlement:
    """Whether ``party_id`` is the LIFECYCLE OWNER of ``node_id``, right now.

    **A different question from :func:`entitled_to_command`.** That function answers "may
    this party command this node right now", and its leg 2 requires the node to presently be
    a *detected orphan* -- which itself requires the node still be ``RUNNING``/``STALLED``.
    That requirement is correct for command authority: there is nothing live to command once a
    node goes terminal. It is wrong for OWNERSHIP, which must survive the node's own
    transition to a terminal disposition -- a validly adopted node does not hand itself back
    to its dead dispatcher the moment it finishes; that is exactly the hole this module's own
    docstring names as the reason :func:`reconciliation.reconcile` reads ownership from the
    transfer rather than the spawn record.

    This function reuses every other structural check leg 2 already makes: an adoption record
    naming zero conditions is the gate's own tell that no real :class:`TransferGate` produced
    it and is void regardless of anything else; the checkpoint it names must still resolve
    now; and omitting ``resolves`` fails this closed, identically to leg 2 -- "I could not
    check" must never read the same as "I checked and it was fine". It substitutes
    :func:`_dispatcher_terminal_state` for :func:`detect_orphans` as the orphan-status half,
    because that half is the one condition ownership must *not* re-require of the node's own
    current state.

    Returns an entitlement on :attr:`Leg.ADOPTION` or a refused one (``leg=None``); this
    function never reaches legs 1 or 3, which are :func:`entitled_to_command`'s alone to
    adjudicate.
    """
    node = ledger.get(node_id)
    if node is None:
        return Entitlement(
            entitled=False,
            party_id=party_id,
            node_id=node_id,
            leg=None,
            authoritative=False,
            basis=(
                f"no node {node_id!r} in the read lineage, so there is nothing to own. This "
                f"is the ledger's scope speaking, not a claim that the node does not exist."
            ),
        )

    for adoption in adoptions:
        if adoption.node_id != node_id or adoption.adopting_party_id != party_id:
            continue
        if not adoption.conditions_relied_on:
            return Entitlement(
                entitled=False,
                party_id=party_id,
                node_id=node_id,
                leg=None,
                authoritative=False,
                basis=(
                    "an adoption record names this party and node but rests on no named "
                    "conditions. `adopt()` never produces one of these: `TransferGate."
                    "available` requires all four conditions, and `conditions_relied_on` "
                    "then names all four, so an empty tuple is the record's own tell that "
                    "no gate produced it. The record is void."
                ),
            )
        death_kind, why = _dispatcher_terminal_state(assessments, node)
        if death_kind is None:
            return Entitlement(
                entitled=False,
                party_id=party_id,
                node_id=node_id,
                leg=None,
                authoritative=False,
                basis=(
                    f"an adoption record names this party and node, but {why}. Ownership "
                    f"transfers from a dispatcher that has reached a terminal disposition, "
                    f"recorded or inferred, and this one has not. The record is void: "
                    f"whether the dispatcher is terminal is the half no record can create, "
                    f"and without it this is authority acquired by writing a row."
                ),
            )
        if resolves is None:
            return Entitlement(
                entitled=False,
                party_id=party_id,
                node_id=node_id,
                leg=None,
                authoritative=False,
                basis=(
                    f"an adoption record names this party and node and its dispatcher has "
                    f"reached a terminal disposition ({why}), but no reference resolver was "
                    f"supplied, so whether the checkpoint at "
                    f"{adoption.checkpoint_reference!r} resolves could not be checked. This "
                    f"fails closed, exactly as an unchecked resolver already fails the "
                    f"transfer gate itself: 'I could not check' must never read the same as "
                    f"'I checked and it was fine'. The record is void."
                ),
            )
        if not resolves(adoption.checkpoint_reference):
            return Entitlement(
                entitled=False,
                party_id=party_id,
                node_id=node_id,
                leg=None,
                authoritative=False,
                basis=(
                    f"an adoption record names this party and node and its dispatcher has "
                    f"reached a terminal disposition ({why}), but the checkpoint at "
                    f"{adoption.checkpoint_reference!r} does not resolve when checked again "
                    f"now. The record is void: whether a checkpoint resolves is the other "
                    f"half no record can create, and `conditions_relied_on` naming "
                    f"`checkpoint_resolves` is the record's own say-so, not evidence."
                ),
            )
        return Entitlement(
            entitled=True,
            party_id=party_id,
            node_id=node_id,
            leg=Leg.ADOPTION,
            authoritative=True,
            basis=(
                f"this party adopted the node through the transfer gate, resting on "
                f"{', '.join(adoption.conditions_relied_on)}, with the checkpoint at "
                f"{adoption.checkpoint_reference!r}. Lifecycle ownership and the "
                f"successor-nomination obligation moved with it, its dispatcher's terminal "
                f"disposition ({why}) still stands as of this read, and the checkpoint "
                f"still resolves as of this read."
            ),
        )

    return Entitlement(
        entitled=False,
        party_id=party_id,
        node_id=node_id,
        leg=None,
        authoritative=False,
        basis=(
            f"no adoption by {party_id!r} is recorded for {node_id!r} -- ownership rests on "
            f"the platform's own spawn record unless a real transfer moved it."
        ),
    )


def detect_orphans(
    ledger: Ledger,
    assessments: Mapping[str, Assessment],
) -> Tuple[OrphanDetection, ...]:
    """The **union** predicate: every orphan of every death, recorded or inferred.

    A node is detected where it is still ``running`` or ``stalled`` and its parent has reached
    a terminal disposition of any kind. Detection reports and escalates; it transfers nothing,
    so an inference that turns out wrong costs a report rather than a second writer.

    **This is deliberately wider than what adoption may act on, and the two must not be
    collapsed.** Narrowing detection to match adoption's evidence bar has been tried in this
    design and produced predicates over *disjoint* populations -- nodes alive minutes past
    their parent's recorded terminal notification were invisible to a sweep that only looked
    for parents gone quiet.

    ``UNREACHABLE`` is **not** read as a death inference. It means the clock had no evidence to
    read, which is a statement about observability and not about the parent -- collapsing it
    into death here would reintroduce, one level up, exactly the substitution the activity
    clock refuses to make.
    """
    out = []
    for node in ledger:
        assessment = assessments.get(node.node_id)
        if assessment is None:
            continue

        if assessment.disposition not in (Disposition.RUNNING, Disposition.STALLED):
            out.append(
                OrphanDetection(
                    node_id=node.node_id,
                    detected=False,
                    parent_id=node.parent_id,
                    death_kind=None,
                    basis=(
                        f"this node is {assessment.disposition.value!r}; the sweep is for nodes "
                        f"still running or stalled under a parent that has ended"
                    ),
                )
            )
            continue

        if node.parent_id is None:
            out.append(
                OrphanDetection(
                    node_id=node.node_id,
                    detected=False,
                    parent_id=None,
                    death_kind=None,
                    basis=(
                        "the platform recorded no parent for this node, so it cannot be an "
                        "orphan under any predicate: its dispatcher is the sessionless "
                        "top-level actor, which has no record to classify. **This is a "
                        "structural gap in the sweep's reach, not a finding that the node is "
                        "attended.**"
                    ),
                )
            )
            continue

        parent = assessments.get(node.parent_id)
        if parent is None:
            out.append(
                OrphanDetection(
                    node_id=node.node_id,
                    detected=False,
                    parent_id=node.parent_id,
                    death_kind=None,
                    basis=(
                        f"the parent {node.parent_id!r} was not assessed, so nothing here says "
                        f"whether it ended. Not assessed is not alive."
                    ),
                )
            )
            continue

        if parent.disposition in TERMINAL:
            kind = "recorded"
            why = f"the parent is {parent.disposition.value!r} on {parent.terminal_evidence or 'recorded evidence'}"
        elif parent.disposition is Disposition.STALLED:
            kind = "inferred"
            why = (
                "the parent is stalled on the clock, which is an inference and is enough to "
                "detect, report and escalate -- and never enough to transfer ownership"
            )
        else:
            out.append(
                OrphanDetection(
                    node_id=node.node_id,
                    detected=False,
                    parent_id=node.parent_id,
                    death_kind=None,
                    basis=(
                        f"the parent is {parent.disposition.value!r}, which is not a terminal "
                        f"disposition of any kind"
                    ),
                )
            )
            continue

        out.append(
            OrphanDetection(
                node_id=node.node_id,
                detected=True,
                parent_id=node.parent_id,
                death_kind=kind,
                basis=f"{why}; this node is {assessment.disposition.value!r} beneath it",
            )
        )

    return tuple(out)


def evaluate_transfer_gate(
    ledger: Ledger,
    detection: OrphanDetection,
    *,
    adopting_party_id: str,
    checkpoint_reference: Optional[str],
    resolves: Optional[Callable[[str], bool]] = None,
    validated_by_adopter: bool = False,
    invalid_tail_rejected: bool = False,
    direct_reading_available: bool = False,
    platform_write_capability_record_available: bool = False,
    non_role_writers_present: bool = False,
) -> TransferGate:
    """Evaluate the four conditions for transferring ownership of a detected orphan.

    ``resolves`` answers whether a named reference resolves to real content. **Its absence
    fails the gate closed** -- "I could not check" must never read the same as "I checked and
    it was fine", and this is the one call in the package where that confusion moves ownership.

    ``direct_reading_available`` and ``platform_write_capability_record_available`` fail the
    gate **on their presence**, which reads backwards until the reason is stated: where the
    platform can be read directly, the reading governs and the checkpoint proof is not an
    alternative route to the same claim. It is a last resort and behaves like one.

    ``validated_by_adopter`` must be the **adopting** party's own validation. The retiring
    role's assurance about its own checkpoint is the thing this gate exists to not accept.
    """
    refusals = []

    if not detection.detected:
        refusals.append(
            f"this node is not a detected orphan: {detection.basis}. Adoption is a strict "
            f"subset of detection and is never available for a live-parented node."
        )

    # ---- condition 1: governed-role writers, and an enumerable boundary -------
    node_present = detection.node_id in ledger
    governed_writers_enumerable = node_present and not non_role_writers_present
    if not node_present:
        refusals.append(
            f"node {detection.node_id!r} is not in the read lineage, so the boundary's "
            f"contents cannot be enumerated. Where the inside of a boundary cannot be "
            f"enumerated, that boundary is the wrong one."
        )
    if non_role_writers_present:
        refusals.append(
            "a writer inside this boundary is not a governed role, so the third proof is "
            "unavailable: it is admitted only where every writer is one, and a process that "
            "can write independently of a role is proved against the platform or not at all."
        )

    # ---- condition 2: neither platform reading available ----------------------
    platform_readings_unavailable = not (
        direct_reading_available or platform_write_capability_record_available
    )
    if direct_reading_available:
        refusals.append(
            "a direct reading of the resource is available, and where it is available it "
            "governs. The checkpoint proof is a last resort, never a cheaper route to the "
            "same claim."
        )
    if platform_write_capability_record_available:
        refusals.append(
            "the platform's authoritative record of write capability is available, and it "
            "governs for the same reason."
        )

    # ---- condition 3: a durable, salvageable checkpoint that resolves ---------
    checkpoint_resolves = False
    if not checkpoint_reference or not checkpoint_reference.strip():
        refusals.append(
            "no checkpoint is named. A statement that work stopped is not a checkpoint, and "
            "neither is a terminal notification: "
            + "; ".join(NOT_PROOF_OF_CEASED_WRITING)
            + ". Each is a reading taken of the actor, or an intention, rather than an "
            "artifact a second party could check."
        )
    elif resolves is None:
        refusals.append(
            f"no reference resolver was supplied, so whether {checkpoint_reference!r} resolves "
            f"could not be checked. This fails closed: an unchecked checkpoint and a valid one "
            f"must not produce the same transfer."
        )
    elif not resolves(checkpoint_reference):
        refusals.append(
            f"the checkpoint at {checkpoint_reference!r} does not resolve, so there is no "
            f"durable salvageable artifact carrying the recoverable state. A parent that "
            f"produced no checkpoint blocks exactly as a parent that went quiet does."
        )
    else:
        checkpoint_resolves = True

    # ---- condition 4: validated and adopted by the party taking ownership -----
    validated_and_adopted = bool(validated_by_adopter and invalid_tail_rejected)
    if not validated_by_adopter:
        refusals.append(
            "the adopting party has not validated the checkpoint against primary evidence. "
            "Validation by the retiring role is what makes this the actor's own say-so, which "
            "is what the gate exists to refuse."
        )
    if not invalid_tail_rejected:
        refusals.append(
            "the adopting party has not rejected an invalid or incomplete tail. Adopting a "
            "checkpoint wholesale binds the successor's authority to a point the prior owner "
            "may never have reached."
        )

    return TransferGate(
        node_id=detection.node_id,
        adopting_party_id=adopting_party_id,
        governed_writers_enumerable=governed_writers_enumerable,
        platform_readings_unavailable=platform_readings_unavailable,
        checkpoint_resolves=checkpoint_resolves,
        validated_and_adopted=validated_and_adopted,
        refusals=tuple(refusals),
        checkpoint_reference=checkpoint_reference,
    )


def adopt(gate: TransferGate) -> Adoption:
    """Record an adoption, or refuse and say which conditions failed.

    This is the only constructor of :class:`Adoption` a caller should use. Building one
    directly bypasses the gate, which is the single act this module exists to make expensive.
    """
    if not gate.available:
        raise EntitlementError(
            "adoption refused; ownership does not move.\n"
            + "\n".join(f"  - {reason}" for reason in gate.refusals)
            + (
                f"\n  conditions satisfied: {', '.join(gate.conditions_relied_on) or 'none'}"
            )
        )
    assert gate.checkpoint_reference is not None  # guaranteed by `available`
    return Adoption(
        node_id=gate.node_id,
        adopting_party_id=gate.adopting_party_id,
        checkpoint_reference=gate.checkpoint_reference,
        conditions_relied_on=gate.conditions_relied_on,
    )


def grant_transitive_reach(
    ledger: Ledger,
    *,
    issuer_id: str,
    node_id: str,
    signal_shape: SignalShape,
) -> TransitiveReach:
    """Extend entitlement for one signal to one already-dominated node.

    Refuses an issuer that is not already an ancestor of the target in the platform's own
    tree. **That refusal is the whole safety property**: the record's declared half selects
    which descendant of a subtree the issuer already dominates is being signalled, and its
    structural half -- ancestry -- is not declarable, so the reach can never leave that subtree.

    Refuses an upward shape too. A hazard travels from the node to its supervisor and is
    relayed rather than commanded, so there is no authority to extend.
    """
    if signal_shape.upward:
        raise EntitlementError(
            f"{signal_shape.value!r} travels upward from the node to its supervisor. It is "
            f"relayed and answered, never commanded, so there is no entitlement to extend."
        )
    if issuer_id == node_id:
        raise EntitlementError(
            "a party cannot grant itself reach to itself; entitlement over a node is held or "
            "it is not, and a self-directed record is not a leg of this rule."
        )
    if not ledger.dominates(issuer_id, node_id):
        raise EntitlementError(
            f"{issuer_id!r} is not an ancestor of {node_id!r} in the platform's own tree, so "
            f"there is nothing to extend. No actor acquires authority over a node it does not "
            f"already dominate by writing a record -- and a reach record is a record."
        )
    return TransitiveReach(issuer_id=issuer_id, node_id=node_id, signal_shape=signal_shape)


def spend_transitive_reach(reach: TransitiveReach) -> TransitiveReach:
    """Spend a reach on adjudication. A spent reach entitles nothing.

    Re-spending is refused rather than tolerated: a reach that could be spent twice is a
    standing entitlement wearing a single-use record's shape, which is the collapse leg 3 is
    written to avoid.

    **Mutates the reach's shared cell and returns the SAME object**, rather than a `replace()`
    copy. A copy left the ORIGINAL object -- and any other reference to it a caller stored --
    reporting `spent=False` forever, which is single-use enforced by caller convention rather
    than by the mechanism. Every reference to this exact grant now observes the spend.
    """
    if reach.spent:
        raise EntitlementError(
            f"this reach over {reach.node_id!r} for {reach.signal_shape.value!r} is already "
            f"spent. It is granted for one signal and does not survive the act."
        )
    reach._spent_cell[0] = True
    return reach
