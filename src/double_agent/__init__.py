# SPDX-License-Identifier: Apache-2.0
"""Double Agent -- read what the platform already recorded about your agents.

The premise, and everything here follows from it: **the platform already keeps the ledger.**
Most systems that manage delegated agents keep a second, hand-maintained registry beside it
and then reason from that copy. The copy is lossy in ways that are invisible from inside it
-- entries pruned when an agent stops, unlocked writes that drop concurrent dispatches,
fields that turn out to hold something other than what they are named for -- and an absent
entry in such a registry proves nothing at all.

So this package is **not a new source of truth. It is a reader, a projection, and a small
set of actions at the only boundaries where action is possible.**

Everything platform-specific lives behind :mod:`double_agent.ports`. Nothing else in this
package knows where a record lives, what it is called, or what shape it has.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
