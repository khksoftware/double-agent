# SPDX-License-Identifier: Apache-2.0
"""The conformance reader: what an agent emitted, checked against what it agreed to.

**This reports. It never blocks, and it must never be presented as prevention.** It runs
after the fact, over text that has already been emitted, so by construction it can only tell
you that something happened -- not stop it. A caller that wires this into a refusal has
built something this module does not support.

## Three readers, because one of them is measurably wrong

Checking that a message carries its agreed label sounds like a prefix comparison. It is not,
and the gap is not small. Measured over a real corpus of **4,121 emitted blocks across 255
transcripts**:

| reader | conforming |
| --- | --- |
| naive prefix match | 353 |
| normalised -- strip leading whitespace and markdown emphasis | 382 |
| bracket-tolerant -- accept a suffix inside the bracket | 518 |

**The 136 blocks between the second and third readers are ONE shape in fifteen variants**: a
label carrying a task identifier inside the bracket, `[Some Role -- some-task-id]`.

And the consequence is worse than a percentage. **Fifteen transcripts in that corpus score
zero under both of the first two readers while every counted message in them carries a
label.** A reader built to the naive or normalised specification reports each of those as a
total governance failure by a worker that did exactly what was asked.

So all three are implemented, and :class:`ConformanceReport` carries **all three counts side
by side**. A single number here is a number that hides which reader produced it.

## A finding requires a measured false-positive rate

**This module will not let you call its output a finding until you have measured how often
it is wrong**, against a hand-labelled sample from your own corpus. That is not caution for
its own sake:

- A detector that correctly fires must not be weakened to make a number look better.
- A detector whose false-positive rate is unknown must not be armed.

Both hold at once, and the only thing that satisfies both is measuring. **This package
supplies the measurement, never the number** -- the rate is a property of one corpus and one
labelling style, and a rate imported from somebody else's corpus is a guess wearing a
decimal point.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ConformanceError",
    "ConformanceReport",
    "ReaderMode",
    "conforms",
    "measure_false_positive_rate",
    "read_conformance",
]

_LEADING_EMPHASIS = "*_`~ \t"


class ConformanceError(ValueError):
    """A conformance result is being used in a way its evidence does not support."""


class ReaderMode(Enum):
    """How strictly a block's opening is compared against the agreed label."""

    NAIVE = "naive"
    """Raw prefix comparison. Scores a correctly-labelled message non-conforming whenever
    the label is wrapped in emphasis."""

    NORMALISED = "normalised"
    """Strips leading whitespace and markdown emphasis before comparing. **This is the
    reader a specification would naturally call sufficient, and on real corpora it is
    not.**"""

    BRACKET_TOLERANT = "bracket_tolerant"
    """Accepts a suffix inside the bracket, so ``[Some Role -- some-task]`` conforms to
    ``[Some Role]``. This is the reader that stops fifteen fully-compliant transcripts from
    scoring zero."""


def _normalise(text: str) -> str:
    return text.lstrip(_LEADING_EMPHASIS)


def conforms(block: str, label: str, mode: ReaderMode) -> bool:
    """Whether one emitted block carries ``label`` under ``mode``."""
    if not label:
        raise ConformanceError("an empty label matches everything and checks nothing")

    if mode is ReaderMode.NAIVE:
        return block.startswith(label)

    normalised = _normalise(block)
    if mode is ReaderMode.NORMALISED:
        return normalised.startswith(label)

    if mode is ReaderMode.BRACKET_TOLERANT:
        if normalised.startswith(label):
            return True
        if not (label.startswith("[") and label.endswith("]")):
            return False
        inner = re.escape(label[1:-1])
        return re.match(rf"\[\s*{inner}\s*(--|—|-|:|/|\|)\s*[^\]]*\]", normalised) is not None

    raise ConformanceError(f"unknown reader mode {mode!r}")


@dataclass(frozen=True)
class ConformanceReport:
    """Counts under every reader, never one number.

    ``false_positive_rate`` is ``None`` until somebody measures it, and
    :meth:`as_finding` refuses while it is.
    """

    label: str
    total_blocks: int
    naive: int
    normalised: int
    bracket_tolerant: int
    false_positive_rate: Optional[float] = None
    sample_size: int = 0

    @property
    def spread(self) -> int:
        """Blocks the tolerant reader accepts and the normalised one rejects.

        **A large spread is a signal about the READER, not about the agents.** In the
        measured corpus this was 136 blocks, all of one shape.
        """
        return self.bracket_tolerant - self.normalised

    def as_finding(self) -> str:
        """Render this as a reportable finding, or refuse.

        Refuses while ``false_positive_rate`` is unmeasured, **and refuses identically where
        ``sample_size`` is less than one even though a rate is present.** ``sample_size=0``
        with a non-``None`` rate is exactly the state :func:`measure_false_positive_rate`
        itself refuses to produce -- *"a rate of 0.0 over zero blocks is not a measurement"*
        -- so a report built some other way and handed a rate anyway must be refused here on
        the identical ground, not accepted because the ``None`` check alone was silent about
        it. **A detector whose error rate nobody knows produces accusations rather than
        findings**, and this is the one place that distinction can still be enforced.
        """
        if self.false_positive_rate is None or self.sample_size < 1:
            raise ConformanceError(
                "this report has no measured false-positive rate, so its output is not a "
                "finding. A rate of 0.0 over zero blocks is not a measurement, and it is "
                "exactly the shape that makes an unmeasured detector look measured. Measure "
                "the rate against a hand-labelled sample of your own corpus first -- a rate "
                "taken from another corpus is a guess with a decimal point on it. See "
                "measure_false_positive_rate()."
            )
        return (
            f"{self.bracket_tolerant} of {self.total_blocks} emitted blocks carry {self.label!r} "
            f"(naive {self.naive}, normalised {self.normalised}, tolerant "
            f"{self.bracket_tolerant}); measured false-positive rate "
            f"{self.false_positive_rate:.1%} over {self.sample_size} hand-labelled blocks. "
            f"This is a report and not a refusal."
        )


def read_conformance(
    blocks: Iterable[str],
    label: str,
    *,
    false_positive_rate: Optional[float] = None,
    sample_size: int = 0,
) -> ConformanceReport:
    """Score every emitted block under all three readers.

    Refuses the inconsistent pair -- a ``false_positive_rate`` that is not ``None`` while
    ``sample_size`` is less than one -- at construction time, so that state cannot be built
    at all rather than merely being unreportable later at :meth:`ConformanceReport.as_finding`.
    A rate over zero hand-labelled blocks is not a measurement, whichever constructor a
    caller used to arrive at it.
    """
    if false_positive_rate is not None and sample_size < 1:
        raise ConformanceError(
            "a measured false_positive_rate requires sample_size >= 1. sample_size=0 with "
            "a rate present is exactly the state measure_false_positive_rate() itself "
            "refuses to produce, and this constructor refuses to build it rather than only "
            "leaving it unreportable at as_finding()."
        )
    materialised: Tuple[str, ...] = tuple(blocks)
    return ConformanceReport(
        label=label,
        total_blocks=len(materialised),
        naive=sum(1 for b in materialised if conforms(b, label, ReaderMode.NAIVE)),
        normalised=sum(1 for b in materialised if conforms(b, label, ReaderMode.NORMALISED)),
        bracket_tolerant=sum(
            1 for b in materialised if conforms(b, label, ReaderMode.BRACKET_TOLERANT)
        ),
        false_positive_rate=false_positive_rate,
        sample_size=sample_size,
    )


def measure_false_positive_rate(
    labelled_sample: Sequence[Tuple[str, bool]],
    label: str,
    *,
    mode: ReaderMode = ReaderMode.BRACKET_TOLERANT,
) -> Tuple[float, int]:
    """Measure how often ``mode`` calls a compliant block non-conforming.

    ``labelled_sample`` is ``(block, is_actually_compliant)`` pairs, hand-labelled by a
    person. **This function cannot manufacture that judgement and does not try**; supplying
    it is the work, and it is the reason the rate is honest.

    Returns ``(rate, sample_size)``. The rate is the proportion of genuinely compliant
    blocks the reader rejects -- a false accusation, which is the error that matters here,
    because it is the one that discredits the detector.
    """
    if not labelled_sample:
        raise ConformanceError(
            "an empty sample measures nothing. A rate of 0.0 over zero blocks is not a "
            "measurement, and it is exactly the shape that makes an unmeasured detector "
            "look measured."
        )
    compliant = [block for block, is_compliant in labelled_sample if is_compliant]
    if not compliant:
        raise ConformanceError(
            "this sample contains no genuinely compliant blocks, so it cannot measure a "
            "false-positive rate. It could measure a false-negative rate, which is a "
            "different question this function does not answer."
        )
    rejected = sum(1 for block in compliant if not conforms(block, label, mode))
    return rejected / len(compliant), len(labelled_sample)
