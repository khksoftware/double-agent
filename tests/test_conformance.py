# SPDX-License-Identifier: Apache-2.0
"""The conformance reader, and the measured case that makes a third reader necessary."""
from __future__ import annotations

import pytest

from double_agent.conformance import (
    ConformanceError,
    ConformanceReport,
    ReaderMode,
    conforms,
    measure_false_positive_rate,
    read_conformance,
)

LABEL = "[Some Role]"


class TestTheFifteenTranscriptCase:
    """A worker that labelled every message, scored zero by the specified reader."""

    BLOCKS = [
        "[Some Role -- task-alpha] did the thing",
        "[Some Role -- task-beta] did another thing",
        "[Some Role -- eng-00299-review-1] and another",
    ]

    def test_naive_and_normalised_both_score_zero(self):
        report = read_conformance(self.BLOCKS, LABEL)

        assert report.naive == 0
        assert report.normalised == 0

    def test_the_tolerant_reader_scores_all_of_them(self):
        report = read_conformance(self.BLOCKS, LABEL)

        assert report.bracket_tolerant == 3
        assert report.spread == 3

    def test_this_is_the_difference_between_a_report_and_a_libel(self):
        """Built to the naive or normalised spec, the report reads as total failure.

        Every one of these messages carries the label. The reader is wrong, not the worker.
        """
        report = read_conformance(self.BLOCKS, LABEL)

        assert report.total_blocks == 3
        assert report.normalised == 0
        assert report.bracket_tolerant == report.total_blocks


class TestTheEmphasisCase:
    def test_naive_rejects_an_emphasised_label_that_is_plainly_there(self):
        block = "**[Some Role]** did the thing"

        assert conforms(block, LABEL, ReaderMode.NAIVE) is False
        assert conforms(block, LABEL, ReaderMode.NORMALISED) is True

    @pytest.mark.parametrize("prefix", ["", "  ", "*", "**", "_", "`", " \t*_"])
    def test_normalisation_strips_leading_whitespace_and_emphasis(self, prefix):
        assert conforms(f"{prefix}{LABEL} text", LABEL, ReaderMode.NORMALISED) is True


class TestDiscrimination:
    """A reader that always passes is not a reader, so the negatives matter most."""

    @pytest.mark.parametrize(
        "block",
        [
            "no label at all",
            "[Another Role] wrong label",
            "text before [Some Role] the label",
            "[Some Role extended without a separator] text",
        ],
    )
    def test_genuinely_non_conforming_blocks_are_rejected_by_every_reader(self, block):
        for mode in ReaderMode:
            assert conforms(block, LABEL, mode) is False, (block, mode)

    @pytest.mark.parametrize("separator", ["--", "—", "-", ":", "/", "|"])
    def test_common_separators_inside_the_bracket_are_tolerated(self, separator):
        block = f"[Some Role {separator} a-task-id] text"

        assert conforms(block, LABEL, ReaderMode.BRACKET_TOLERANT) is True

    def test_an_empty_label_is_refused_rather_than_matching_everything(self):
        with pytest.raises(ConformanceError, match="matches everything"):
            conforms("anything", "", ReaderMode.NAIVE)


class TestAFindingRequiresAMeasuredRate:
    def test_an_unmeasured_report_refuses_to_be_a_finding(self):
        report = read_conformance(["no label"], LABEL)

        with pytest.raises(ConformanceError) as excinfo:
            report.as_finding()

        assert "no measured false-positive rate" in str(excinfo.value)
        assert "your own" in str(excinfo.value)

    def test_a_measured_report_renders_and_says_it_is_not_a_refusal(self):
        report = read_conformance(
            ["[Some Role] ok", "nope"], LABEL, false_positive_rate=0.05, sample_size=40
        )

        rendered = report.as_finding()

        assert "naive" in rendered and "normalised" in rendered and "tolerant" in rendered
        assert "5.0%" in rendered
        assert "report and not a refusal" in rendered

    def test_all_three_counts_are_reported_side_by_side(self):
        report = read_conformance(
            ["[Some Role] a", "**[Some Role]** b", "[Some Role -- t] c", "nope"], LABEL
        )

        assert (report.naive, report.normalised, report.bracket_tolerant) == (1, 2, 3)

    def test_a_rate_over_zero_blocks_is_refused_even_when_the_field_is_not_none(self):
        """A regression: `as_finding` used to check `false_positive_rate is None` only, so a
        report built by some other route than `read_conformance` -- constructed directly, as
        a caller outside this module's own constructor is free to do -- with `sample_size=0`
        and a non-None rate rendered a sentence that stated its own emptiness (`"measured ...
        over 0 hand-labelled blocks"`) while calling itself a report and not a refusal. That
        must no longer render."""
        report = ConformanceReport(
            label=LABEL,
            total_blocks=1,
            naive=1,
            normalised=1,
            bracket_tolerant=1,
            false_positive_rate=0.0,
            sample_size=0,
        )
        with pytest.raises(ConformanceError, match="not a measurement"):
            report.as_finding()

    def test_read_conformance_refuses_to_build_the_inconsistent_pair(self):
        """The other half of the same fix: the invalid state cannot be built at all through
        this module's own constructor, not merely left unreportable at `as_finding`."""
        with pytest.raises(ConformanceError, match="sample_size"):
            read_conformance(["nope"], LABEL, false_positive_rate=0.0, sample_size=0)


class TestMeasuringTheRate:
    def test_an_empty_sample_is_refused_rather_than_scoring_zero(self):
        """A rate of 0.0 over zero blocks is what makes an unmeasured detector look measured."""
        with pytest.raises(ConformanceError, match="empty sample measures nothing"):
            measure_false_positive_rate([], LABEL)

    def test_a_sample_with_no_compliant_blocks_cannot_measure_this_rate(self):
        with pytest.raises(ConformanceError, match="no genuinely compliant blocks"):
            measure_false_positive_rate([("nope", False)], LABEL)

    def test_the_rate_counts_compliant_blocks_the_reader_rejects(self):
        sample = [
            ("[Some Role] fine", True),
            ("[Some Role -- t] fine", True),
            ("text [Some Role] late", True),  # compliant by hand, rejected by the reader
            ("nope", False),
        ]

        rate, size = measure_false_positive_rate(sample, LABEL)

        assert size == 4
        assert rate == pytest.approx(1 / 3)

    def test_the_rate_depends_on_the_reader_and_the_module_says_so(self):
        sample = [("[Some Role -- t] fine", True)]

        tolerant, _ = measure_false_positive_rate(sample, LABEL, mode=ReaderMode.BRACKET_TOLERANT)
        normalised, _ = measure_false_positive_rate(sample, LABEL, mode=ReaderMode.NORMALISED)

        assert tolerant == 0.0
        assert normalised == 1.0


class TestClientNeutrality:
    def test_no_corpus_specific_number_survives_as_a_VALUE(self):
        """Measured counts may appear as evidence in prose. They may not appear as values.

        The distinction is the whole point and a string search cannot make it. Citing a
        corpus in a docstring is how a design choice is justified; a corpus's number
        surviving as a default, a threshold or a constant is how one corpus's answer gets
        silently imported into everybody else's. The second is the hazard.

        Checked over the module's own syntax tree, so docstrings are excluded by
        construction rather than by a fragile split.
        """
        import ast
        import inspect

        from double_agent import conformance

        tree = ast.parse(inspect.getsource(conformance))
        for docstring_node in [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        ]:
            if (
                docstring_node.body
                and isinstance(docstring_node.body[0], ast.Expr)
                and isinstance(docstring_node.body[0].value, ast.Constant)
                and isinstance(docstring_node.body[0].value.value, str)
            ):
                docstring_node.body[0].value.value = ""

        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
        }
        corpus_figures = {4121, 353, 382, 518, 136, 255}

        assert not (literals & corpus_figures), sorted(literals & corpus_figures)

    def test_no_default_false_positive_rate_exists_anywhere(self):
        """The sharpest form of the same hazard, and the one that would actually bite.

        A default rate makes an unmeasured detector render as measured, which is precisely
        what `as_finding` refuses to allow.
        """
        report = read_conformance(["nope"], LABEL)

        assert report.false_positive_rate is None

        with pytest.raises(ConformanceError):
            report.as_finding()

    def test_the_module_names_no_host_or_work_item(self):
        import inspect

        from double_agent import conformance

        source = inspect.getsource(conformance).lower()

        for token in ("eos", "gov-00", "eng-00", "dual-hat", "interlock", "§"):
            assert token not in source, token
