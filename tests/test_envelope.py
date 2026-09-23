# SPDX-License-Identifier: Apache-2.0
"""The envelope, and the field that must never come back."""
from __future__ import annotations

import pytest

from double_agent.envelope import (
    CONTROL_RECORD_KINDS,
    ENVELOPE_FIELDS,
    Envelope,
    EnvelopeError,
    ExternalWait,
    FENCE_MARKER,
    OPTIONAL_ENVELOPE_FIELDS,
    control_record_reference,
    parse_envelope,
    render_envelope,
)


def brief(**overrides: str) -> str:
    fields = {
        "assigned_outcome": "a working parser",
        "durable_cursor": "state/cursor.json",
        "checkpoints": "parsed, validated, landed",
        "heartbeat_seconds": "120",
        "role_label": "[Worker]",
        "external_wait": "",
        "control_record": "control/",
    }
    fields.update(overrides)
    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    return f"Some preamble prose.\n\n{FENCE_MARKER}\n{body}\n\nSome trailing instructions.\n"


class TestThereIsNoSupervisorField:
    def test_seven_fields_and_none_of_them_declares_an_identity(self):
        assert len(ENVELOPE_FIELDS) == 7
        assert "supervisor" not in ENVELOPE_FIELDS
        assert not any("supervisor" in f for f in ENVELOPE_FIELDS)

    def test_a_brief_declaring_a_supervisor_is_REFUSED_not_ignored(self):
        """Ignoring it would let the field live on in briefs and quietly mean nothing.

        Refusing is what stops a declared authority being reintroduced by habit, and the
        refusal says why rather than just naming the field.
        """
        # The baseline must actually parse, or the refusal below proves nothing about the
        # supervisor line specifically.
        parse_envelope(brief())

        text = brief().replace(
            "control_record: control/", "control_record: control/\nsupervisor: some-agent-type"
        )
        with pytest.raises(EnvelopeError) as excinfo:
            parse_envelope(text)

        message = str(excinfo.value)
        assert "supervisor" in message
        assert "entitlement is" in message
        assert "attacker" in message

    def test_the_rendered_block_carries_no_supervisor_line(self):
        envelope = parse_envelope(brief())

        assert "supervisor" not in render_envelope(envelope)


class TestParsing:
    def test_round_trip(self):
        original = parse_envelope(brief())
        again = parse_envelope(f"{FENCE_MARKER}\n" + render_envelope(original).split("\n", 1)[1])

        assert again == original

    def test_fields_are_read_correctly(self):
        envelope = parse_envelope(brief())

        assert envelope.assigned_outcome == "a working parser"
        assert envelope.durable_cursor == "state/cursor.json"
        assert envelope.checkpoints == ("parsed", "validated", "landed")
        assert envelope.heartbeat_seconds == 120
        assert envelope.role_label == "[Worker]"
        assert envelope.control_record == "control/"
        assert envelope.external_wait is None
        assert envelope.declares_external_wait is False

    def test_no_envelope_is_refused_rather_than_read_as_an_empty_contract(self):
        with pytest.raises(EnvelopeError, match="no envelope in this brief"):
            parse_envelope("A brief with no contract in it at all.")

    def test_two_envelopes_are_refused_rather_than_disambiguated(self):
        with pytest.raises(EnvelopeError, match="2 envelopes in one brief"):
            parse_envelope(brief() + brief())

    def test_a_missing_field_is_named(self):
        text = brief().replace("role_label: [Worker]\n", "")

        with pytest.raises(EnvelopeError, match="missing required fields.*role_label"):
            parse_envelope(text)

    def test_a_present_but_empty_required_field_is_refused(self):
        with pytest.raises(EnvelopeError, match="present but empty"):
            parse_envelope(brief(assigned_outcome=""))

    def test_a_duplicated_field_is_refused(self):
        text = brief().replace(
            "role_label: [Worker]", "role_label: [Worker]\nrole_label: [Other]"
        )

        with pytest.raises(EnvelopeError, match="appears twice"):
            parse_envelope(text)

    @pytest.mark.parametrize("value", ["not-a-number", "0", "-5"])
    def test_a_bad_heartbeat_is_refused(self, value):
        with pytest.raises(EnvelopeError, match="heartbeat_seconds"):
            parse_envelope(brief(heartbeat_seconds=value))


class TestExternalWait:
    def test_a_declaration_names_an_operation_and_a_bound(self):
        envelope = parse_envelope(
            brief(external_wait="waiting on the remote index for up to 600 seconds")
        )

        assert envelope.external_wait == ExternalWait(
            operation="waiting on the remote index", bound_seconds=600
        )
        assert envelope.declares_external_wait is True

    @pytest.mark.parametrize("value", ["yes", "true", "waiting", "a long time", "600"])
    def test_a_bare_affirmative_is_not_a_declaration(self, value):
        """Read generously, any of these would extend a deadline nobody set."""
        with pytest.raises(EnvelopeError, match="not a declaration"):
            parse_envelope(brief(external_wait=value))

    def test_an_empty_external_wait_is_legitimate(self):
        assert parse_envelope(brief(external_wait="")).external_wait is None

    def test_a_zero_or_negative_bound_is_refused(self):
        with pytest.raises(EnvelopeError, match="positive bound"):
            ExternalWait(operation="x", bound_seconds=0)

    def test_an_unnamed_operation_is_refused(self):
        with pytest.raises(EnvelopeError, match="name the operation"):
            ExternalWait(operation="   ", bound_seconds=10)


class TestControlRecords:
    def test_four_kinds_and_each_resolves_under_the_declared_directory(self):
        envelope = parse_envelope(brief(control_record="run/control"))

        assert set(CONTROL_RECORD_KINDS) == {"relay", "adoption", "reach", "abandonment"}
        for kind in CONTROL_RECORD_KINDS:
            reference = control_record_reference(envelope, kind, "node-7")
            assert reference.startswith("run/control/")
            assert "node-7" in reference
            assert kind in reference

    def test_a_trailing_slash_does_not_double(self):
        envelope = parse_envelope(brief(control_record="run/control/"))

        assert "//" not in control_record_reference(envelope, "relay", "n1")

    def test_an_unknown_kind_is_refused(self):
        envelope = parse_envelope(brief())

        with pytest.raises(EnvelopeError, match="unknown control record kind"):
            control_record_reference(envelope, "invented", "n1")


class TestClientNeutrality:
    def test_the_fence_marker_carries_no_client_bookkeeping(self):
        """The design's own template fenced this block with a work-item identifier.

        That is host bookkeeping and cannot survive into a package other people install,
        so the marker is the framework's own name and nothing else.
        """
        assert FENCE_MARKER == "double-agent-envelope"
        assert "gov" not in FENCE_MARKER.lower()
        assert not any(ch.isdigit() for ch in FENCE_MARKER)

class TestDeclaredWriteSetIsOptionalAndAdditive:
    """The one optional field, and every property that makes it safe to add.

    A host enforces write scope from this field. The field's value to that host is obvious;
    what these tests protect is the part that is easy to break later -- that a dispatch which
    declares NOTHING is treated exactly as it was before the field existed, in the renderer
    and in the parser alike.
    """

    def test_the_required_set_is_still_exactly_seven_and_excludes_this_field(self):
        assert len(ENVELOPE_FIELDS) == 7
        assert "declared_write_set" not in ENVELOPE_FIELDS
        assert OPTIONAL_ENVELOPE_FIELDS == ("declared_write_set",)

    def test_an_undeclared_envelope_renders_no_such_line(self):
        """The additive property, asserted on the bytes rather than on intent.

        An envelope that declares nothing must produce the block this renderer produced
        before the field existed -- same lines, same order, nothing appended -- or every
        brief already in flight is invalidated and a host comparing against the canonical
        render refuses legitimate dispatches.
        """
        rendered = render_envelope(
            Envelope(
                assigned_outcome="a working parser",
                durable_cursor="state/cursor.json",
                checkpoints=("parsed", "validated"),
                heartbeat_seconds=120,
                role_label="[Worker]",
                control_record="control/",
            )
        )
        assert "declared_write_set" not in rendered
        assert len(rendered.splitlines()) == 8  # the marker plus the seven required fields

    def test_envelope_text_without_the_field_still_parses(self):
        envelope = parse_envelope(brief())

        assert envelope.declared_write_set is None
        assert envelope.declares_write_set is False

    def test_a_declared_set_round_trips_through_render_and_parse(self):
        original = Envelope(
            assigned_outcome="a working parser",
            durable_cursor="state/cursor.json",
            checkpoints=("parsed", "validated"),
            heartbeat_seconds=120,
            role_label="[Worker]",
            control_record="control/",
            declared_write_set=("src/one.py", "src/two.py"),
        )

        recovered = parse_envelope(render_envelope(original))

        assert recovered.declared_write_set == ("src/one.py", "src/two.py")
        assert recovered.declares_write_set is True

    def test_a_present_but_empty_declaration_is_refused(self):
        """Refused rather than read as "writes nothing".

        An empty declaration is indistinguishable from one a caller meant to fill in, and it
        would refuse every write the dispatch makes. Omitting the field is how a dispatch
        declares nothing.
        """
        with pytest.raises(EnvelopeError, match="declares no paths"):
            parse_envelope(brief(declared_write_set=""))

    def test_a_repeated_path_is_refused(self):
        with pytest.raises(EnvelopeError, match="repeats a path"):
            parse_envelope(brief(declared_write_set="src/one.py, src/one.py"))

    def test_checkpoints_are_not_a_write_allowlist(self):
        """The reading this field exists to make unnecessary, asserted as independence.

        A real dispatch's checkpoints named files it was explicitly forbidden to write, so
        reading checkpoints as a write allowlist errs permissive on exactly the paths that
        matter most. An envelope with checkpoints and no declared set declares NOTHING.
        """
        envelope = parse_envelope(brief(checkpoints="hooks/a.py, hooks/b.py"))

        assert envelope.checkpoints == ("hooks/a.py", "hooks/b.py")
        assert envelope.declares_write_set is False
        assert envelope.declared_write_set is None
