# Double Agent

<p>
  <img src="assets/double-agent.png" alt="Double Agent mascot: a single figure split down the middle, wearing a black top hat and overcoat on one side and a yellow hard hat and work jacket on the other, in dark sunglasses" width="180" align="left">
  Double Agent is a Python library for supervising delegated AI agents. When one agent dispatches others, somebody has to answer plain questions about them: which are still running, what each was asked to do, who is allowed to cancel whom, and what became of the ones that stopped. Double Agent answers those questions from the records the platform itself already keeps, rather than from a second registry maintained by hand alongside it.
</p>

<br clear="left">

**Why that distinction is the whole point.** Most systems for supervising delegated agents
keep exactly that kind of registry by hand, updated alongside the platform. The trouble is
that the platform underneath is already recording the same facts — and more — for the same
agents, at the same instants.

The hand-kept copy is lossy in ways that are invisible from inside it:

- **Pruned** when an agent stops — a live-only roster that cannot answer a single question
  about what already happened.
- **Unlocked**, often, in its writes — so parallel dispatches drop each other's entries.
- **First-available, not first-correct** — a field ends up holding whatever value showed up
  first, not what its name promises.

The consequence is the one that matters: **an absent entry in such a registry proves
nothing**, and a registry that cannot be absent-checked cannot answer "is this agent still
running?" — which is the only question anyone asks it.

So Double Agent is **not a new source of truth** — it is a reader, a projection, and a small
set of actions at the only boundaries where action is possible.

## What it gives you

| | |
| --- | --- |
| **A lineage tree** | every dispatched agent as a node, each with an id, a parent, a depth, when it started, when it last did anything, and a final outcome drawn from a closed set |
| **An activity clock** | how long since an agent's last *evidenced* activity, measured against one inactivity threshold — never two competing ones |
| **A disposition classifier** | a small, closed set of terminal states, including two that most systems blur into one |
| **A dispatch envelope** | everything tracked about one dispatch: its outcome so far, a durable cursor and checkpoints for resuming it safely, a heartbeat, a role label, whether it has declared itself waiting on something external, and where its control records actually live |
| **A conformance predicate** | a check over what an agent actually emitted, carrying its own measured false-positive rate |
| **A signal protocol** | five named signals — `cancel`, `suspend`, `override`, `hazard`, `status` — with a stated authority order |
| **An entitlement rule** | who may signal whom, and the one condition under which that authority transfers |
| **A registry reconciliation** | a check against the platform's own registry, on any platform that keeps one |

## The two ideas worth reading before the API

### "Not observed" is never "not there"

The package refuses, everywhere, to collapse *nobody looked* into *nothing is there*. A
roster you probed and found empty and a roster nobody could read are different facts, and
only one of them is evidence. Where a platform supplies nothing, the steps that need it
**block** — they do not fall back to a weaker answer.

That is a smaller guarantee than a framework that always returns something, and a truer one.
A gap gets investigated; a confident wrong answer does not.

### Authority is structural, not declared

No actor gains authority over a node by writing a record — only by already sitting above it
in the platform's own tree. You may signal a node you dispatched yourself. You may signal a
node you have adopted, and adoption is the only act that actually transfers ownership. An
ancestor further up the tree may still reach down and send a single signal, but that reach is
spent on the signal itself; it transfers nothing.

And the transfer condition is deliberately hard. **Entitlement transfers only when the party
taking it over can prove the prior owner can no longer write** — and that proof must take one
of three forms:

- reading the prior owner's write capability directly,
- consulting the platform's own authoritative record of that capability, or
- where the platform exposes neither, and every writer inside the named boundary is a
  governed role, taking over a durable, salvageable checkpoint that the prior owner wrote
  and that the taking party has validated and adopted.

A record that the prior owner *stopped* is none of those three. An elapsed inactivity
threshold is not either — no platform reads it and every platform computes it. A platform
supplying none of the three supplies no transfer, and the step blocks.

## Signals, and the honest half of the signal protocol

A signal is only as strong as what the transport records about who sent it. (`status` is the
one exception: it is a read, not a message, so there is nothing sent and nothing to spoof.)

> The transport must record, on the recipient's side, a sender identity the sender does
> not control. Where it does not, the signal is **advisory**.

And the consequence, which matters more than the condition:

> An advisory signal's non-compliance produces an obstacle report. Only a signal that meets
> that condition — call it **adjudicable** — can have its non-compliance attributed to the
> recipient as **defiance**.

On some platforms every signal to a running actor is advisory. Such a platform gets the whole
protocol and no hazard producer at all — which is the correct outcome, stated rather than
worked around.

## Porting it

Everything platform-specific lives behind `double_agent.ports`. Implement `Platform`, declare
your `Capabilities` honestly, and nothing else in the package needs to know where a record
lives, what it is called, or what shape it has.

Declaring a capability you do not have raises at the call site rather than producing a wrong
answer. That is the intended failure direction.

The port surface is `double_agent.ports.Platform` and `double_agent.ports.Capabilities`; the
reading side is `double_agent.reconciliation.reconcile`. Neither has a separate usage guide
yet — read the module docstrings, which are load-bearing rather than illustrative.

## Status

**Early.** The interfaces are settled and the implementation is landing module by module.
Version is `0.1.0` and the package is not yet published to an index.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
