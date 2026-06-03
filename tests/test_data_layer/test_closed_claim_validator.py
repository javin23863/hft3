"""Closed-claim kg_annotations contract — every LLM claim must be source-typed and cited.

Companion to `packages/data_layer/packet/validate.py`. These tests prove the
validator catches the most common LLM failure modes:
- missing or unknown `source_type`
- missing or malformed `cite` for citation-bearing sources
- empty `source_id` / `field` / `value`
- page that is not a positive int
- bogus PDF name

The drop helper must strip these from the persisted response.
"""

from __future__ import annotations


def _ann(**kwargs):
    return kwargs


def test_validator_accepts_ontology_extension_with_cite():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="ONTOLOGY_EXTENSION",
        source_id="MarkedMicroEvent",
        field="kind",
        value="ADD",
        cite={"pdf": "chicago_cme_microstructure_mathematical_model.pdf", "section": "§4 MBO", "page": 2},
    )
    assert validate_kg_annotations_closed_claim([ann]) == []


def test_validator_accepts_latency_authority_field_without_cite():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="LATENCY_AUTHORITY_FIELD",
        source_id="latency_authority",
        field="breakeven_us",
        value=4.5,
    )
    assert validate_kg_annotations_closed_claim([ann]) == []


def test_validator_rejects_unknown_source_type():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="FREE_FORM_OPINION",
        source_id="x",
        field="y",
        value=1,
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any("source_type" in e for e in errs)


def test_validator_requires_cite_for_ontology_extension():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="ONTOLOGY_EXTENSION",
        source_id="MarkedMicroEvent",
        field="kind",
        value="ADD",
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any(".cite" in e and "required" in e for e in errs)


def test_validator_rejects_empty_source_id():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="LATENCY_AUTHORITY_FIELD",
        source_id="  ",
        field="breakeven_us",
        value=1.0,
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any("source_id" in e for e in errs)


def test_validator_rejects_empty_field():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="LATENCY_AUTHORITY_FIELD",
        source_id="latency_authority",
        field="",
        value=1.0,
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any(".field" in e for e in errs)


def test_validator_rejects_null_value():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="LATENCY_AUTHORITY_FIELD",
        source_id="latency_authority",
        field="breakeven_us",
        value=None,
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any(".value" in e for e in errs)


def test_validator_rejects_bad_cite_page():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="ONTOLOGY_EXTENSION",
        source_id="X",
        field="y",
        value=1,
        cite={"pdf": "p.pdf", "section": "s", "page": 0},
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any(".cite.page" in e for e in errs)


def test_validator_rejects_non_int_page():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    ann = _ann(
        source_type="PDF_CITATION",
        source_id="latency_authority",
        field="breakeven_us",
        value=1.0,
        cite={"pdf": "p.pdf", "section": "s", "page": "two"},
    )
    errs = validate_kg_annotations_closed_claim([ann])
    assert any(".cite.page" in e for e in errs)


def test_drop_uncited_strips_invalid_keeps_valid():
    from data_layer.packet.validate import drop_uncited_kg_annotations

    good = _ann(
        source_type="LATENCY_AUTHORITY_FIELD",
        source_id="latency_authority",
        field="breakeven_us",
        value=1.0,
    )
    bad = _ann(
        source_type="ONTOLOGY_EXTENSION",
        source_id="X",
        field="y",
        value=1,
    )
    out = drop_uncited_kg_annotations([good, bad, "not a dict", None])
    assert out == [good]


def test_drop_uncited_on_non_list_returns_empty():
    from data_layer.packet.validate import drop_uncited_kg_annotations

    assert drop_uncited_kg_annotations(None) == []
    assert drop_uncited_kg_annotations({}) == []


def test_annotations_with_malformed_kg_list_returns_error():
    from data_layer.packet.validate import validate_kg_annotations_closed_claim

    errs = validate_kg_annotations_closed_claim("not a list")
    assert errs and "must be a list" in errs[0]
