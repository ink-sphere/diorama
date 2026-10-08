from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from lxml import etree

from diorama.agents.ebook_structure_agent import parse_ebook
from diorama.agents.ebook_structure_agent.markup import markup_tokens
from diorama.agents.ebook_structure_agent.prompts import build_repair_prompt
from diorama.agents.ebook_structure_agent.validation import _validate_markup

COVER = '<svg xmlns="http://www.w3.org/2000/svg"><image href="cover.jpg"/></svg>'
HEADING = '<h2 id="pg-header-heading" title="">The Project Gutenberg eBook</h2>'


def test_xml_serialization_does_not_cause_mismatch_after_cover():
    root = etree.fromstring(
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body>{HEADING}</body></html>'.encode()
    )
    heading = root.find(".//{http://www.w3.org/1999/xhtml}h2")
    assert heading is not None
    xml_fragment = etree.tostring(heading, encoding="unicode", with_tail=False)
    assert "xmlns=" in xml_fragment
    assert len(list(markup_tokens([COVER]))) == 4
    _validate_markup([COVER, HEADING], [COVER, xml_fragment])


@pytest.mark.parametrize(
    "raw",
    [
        '<h2 xmlns="http://www.w3.org/1999/xhtml" id="pg-header-heading" title="">The Project Gutenberg eBook</h2>',
        '<x:h2 xmlns:x="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" id="pg-header-heading" title="">The Project Gutenberg eBook</x:h2>',
    ],
)
def test_standard_xhtml_namespace_placement_is_ignored(raw):
    assert list(markup_tokens([HEADING])) == list(markup_tokens([raw]))


@pytest.mark.parametrize(
    "raw",
    [
        HEADING.replace('id="pg-header-heading"', 'id="changed"'),
        HEADING.replace('title=""', 'title="changed"'),
        HEADING.replace("Gutenberg", "Other"),
        HEADING.replace("<h2 ", '<h2 xmlns="urn:other" '),
        HEADING.replace("<h2 ", '<h2 xmlns="http://www.idpf.org/2007/ops" '),
    ],
)
def test_real_changes_still_fail_and_report_both_sides(raw):
    with pytest.raises(ValueError) as failure:
        _validate_markup(
            [HEADING], [raw], source_locations=["book.xhtml (block b000002)"]
        )
    diagnostic = str(failure.value)
    assert "Expected book.xhtml (block b000002)" in diagnostic
    assert "actual text fragment 1" in diagnostic
    assert any(
        value in diagnostic
        for value in ("changed", "Other", "urn:other", "http://www.idpf.org/2007/ops")
    )


@pytest.mark.parametrize(
    "change", ["omit", "duplicate", "reorder", "href", "svg_namespace"]
)
def test_media_and_order_changes_are_still_rejected(change):
    actual = [COVER, HEADING]
    if change == "omit":
        actual = [HEADING]
    elif change == "duplicate":
        actual = [COVER, COVER, HEADING]
    elif change == "reorder":
        actual.reverse()
    elif change == "href":
        actual[0] = COVER.replace('href="cover.jpg"', 'href="other.jpg"')
    else:
        actual[0] = COVER.replace("http://www.w3.org/2000/svg", "urn:other")
    with pytest.raises(ValueError, match="Source content changed"):
        _validate_markup([COVER, HEADING], actual)


def test_mismatch_after_cover_points_to_header_and_survives_repair_prompt():
    actual = '<h2 id="pg-header-heading" title="" class="rewritten">The Project Gutenberg eBook</h2>'
    with pytest.raises(ValueError) as failure:
        _validate_markup(
            [COVER, HEADING],
            [COVER, actual],
            source_locations=[
                "wrap.xhtml (block b000001)",
                "text.xhtml (block b000002)",
            ],
        )
    diagnostic = str(failure.value)
    assert "at token 4" in diagnostic
    assert "text.xhtml (block b000002)" in diagnostic
    assert "actual text fragment 2" in diagnostic
    assert '"rewritten"' in diagnostic
    prompt = build_repair_prompt(diagnostic)
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["validation_error"] == diagnostic


def test_truncated_output_reports_end_of_content():
    with pytest.raises(ValueError, match="actual end of content"):
        _validate_markup([COVER, HEADING], [COVER])


def test_extra_output_reports_expected_end_of_content():
    with pytest.raises(ValueError, match="Expected end of content"):
        _validate_markup([COVER], [COVER, HEADING])


def test_long_mismatch_diagnostics_are_bounded():
    with pytest.raises(ValueError) as failure:
        _validate_markup(["x" * 20_000], ["y" * 20_000])
    assert len(str(failure.value)) < 4000


def test_real_shakespeare_header_xml_serialization_matches_source():
    path = Path(__file__).resolve().parents[1] / "books" / "as_you_like_it.epub"
    if not path.exists():
        pytest.skip("Local Shakespeare ebook is optional")
    source = parse_ebook(path)
    document = source.documents[1]
    root = etree.fromstring(document.raw_content)
    element = root.find(".//{http://www.w3.org/1999/xhtml}h2")
    assert element is not None
    raw = etree.tostring(element, encoding="unicode", with_tail=False)
    expected = next(
        block
        for block in source.blocks
        if BeautifulSoup(block.raw_text, "html.parser").find(id="pg-header-heading")
    )
    _validate_markup(
        [source.blocks[0].raw_text, expected.raw_text], [source.blocks[0].raw_text, raw]
    )
