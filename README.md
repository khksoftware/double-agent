# Double Agent

**Read what your platform already recorded about your delegated agents, instead of keeping a
worse ledger beside it.**

Most systems that manage delegated agents keep a hand-maintained registry — a list of who is
running, what they were asked to do, when they started. The platform underneath is already
writing all of that, and more, for the same agents at the same instants.

The hand-kept copy is lossy in ways that are invisible from inside it:

- **It is pruned when an agent stops**, so it is a live-only roster and cannot answer a
  single question about what happened.
- **Its writes are often unlocked**, so parallel dispatches drop each other's entries.
- **Its fields hold what the first available value happened to be**, not what they are named
  for.

The consequence is the one that matters: **an absent entry in such a registry proves
nothing**, and a registry that cannot be absent-checked cannot answer "is this agent still
running?" — which is the only question anyone asks it.

So Double Agent is **not a new source of truth. It is a reader, a projection, and a small set
of actions at the only boundaries where action is possible.**

## What it gives you

| | |
| --- | --- |
| **A lineage tree** | nodes with an id, a parent, a depth, a dispatch instant, a last-activity instant, and a terminal disposition drawn from a closed set |
| **An activity clock** | age since last *evidenced* activity, compared against one inactivity regime and never a second one |
| **A disposition classifier** | a small closed set of states, with the two that are usually conflated kept apart |
| **A dispatch envelope** | the assigned outcome, a durable cursor, checkpoints, a heartbeat, a role label, a declared external wait, and where this dispatch's control records resolve |
| **A conformance predicate** | over what an agent emitted, carrying its own **measured** false-positive rate |
| **A signal protocol** | five named shapes — `cancel`, `suspend`, `override`, `hazard`, and `status`, which is a **read** rather than a message and cannot be constructed as one — with a stated authority order |
| **An entitlement rule** | who may signal whom, and the one condition under which that authority transfers |
| **A registry reconciliation** | against the platform's own registry, where it keeps one |

## The two ideas worth reading before the API

### "Not observed" is never "not there"

The package refuses, everywhere, to collapse *nobody looked* into *nothing is there*. A
roster you probed and found empty and a roster nobody could read are different facts, and
only one of them is evidence. Where a platform supplies nothing, the steps that need it
**block** — they do not fall back to a weaker answer.

That is a smaller guarantee than a framework that always returns something, and a truer one.
A gap gets investigated; a confident wrong answer does not.

### Authority is structural, not declared

**No actor acquires authority over a node it does not already dominate in the platform's own
tree by writing a record.** You may signal a node you dispatched. You may signal a node you
adopted, and adoption is the only thing that transfers ownership. An ancestor may spend a
recorded reach on a single signal, which transfers nothing.

And the transfer condition is deliberately hard: **entitlement transfers only where the party
taking it can prove the prior owner can no longer write** — by direct reading, by the
platform's own authoritative record of write capability, or, where the platform exposes
neither and every writer inside the named boundary is a governed role, by a durable
salvageable checkpoint the prior owner wrote and the taking party validated and adopted.

A record that the prior owner *stopped* is none of those three. An elapsed inactivity
threshold is not either — no platform reads it and every platform computes it. A platform
supplying none of the three supplies no transfer, and the step blocks.

## Signals, and the honest half of the signal protocol

A signal is only as strong as what the transport records about who sent it.

> The transport must record, on the **recipient's** side, a sender identity the sender does
> not control. Where it does not, the signal is **advisory**.

And the consequence, which matters more than the condition:

> An advisory signal's non-compliance produces an **obstacle report**. Only an adjudicable
> signal's non-compliance may be attributed to the recipient as **defiance**.

On some platforms every signal to a running actor is advisory. Such a platform gets the whole
protocol and **no hazard producer at all** — which is the correct outcome, stated rather than
worked around.

## Porting it

Everything platform-specific lives behind `double_agent.ports`. Implement `Platform`, declare
your `Capabilities` honestly, and nothing else in the package needs to know where a record
lives, what it is called, or what shape it has.

Declaring a capability you do not have raises at the call site rather than producing a wrong
answer. That is the intended failure direction.

The port surface is `double_agent.ports.Platform` and `double_agent.ports.Capabilities`; the
reading side is `double_agent.reconciliation.reconcile`. Neither has a separate usage guide
yet -- read the module docstrings, which are load-bearing rather than illustrative.

## Status

**Early.** The interfaces are settled and the implementation is landing module by module.
Version is `0.1.0` and the package is not yet published to an index.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
