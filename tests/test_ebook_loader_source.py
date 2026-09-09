"""Source-fidelity and Tau integration tests; no API credentials required."""

import copy
import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from tau_agent import AssistantMessage, ToolCall
from tau_agent.provider_events import AssistantDoneEvent
from tau_ai.fake import FakeProvider

from diorama.agents import EbookDocument, EbookLoaderAgent, EbookLoadError
from diorama.models.ebook_models import StructurePlan
from diorama.utils.ebook_source import EbookSource


def epub_file(
    tmp_path, bodies, *, name="book.epub", encoding="utf-8", nav=True, encrypted=False
):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
        )
        items = "".join(
            f'<item id="s{i}" href="s{i}.xhtml" media-type="application/xhtml+xml"/>'
            for i in range(len(bodies))
        )
        refs = "".join(f'<itemref idref="s{i}"/>' for i in range(len(bodies)))
        nav_item = (
            '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
            if nav
            else ""
        )
        z.writestr(
            "OPS/book.opf",
            f"""<package xmlns:dc="http://purl.org/dc/elements/1.1/">
        <metadata><dc:title>Test &amp; Verse</dc:title><dc:creator id="editor">Editor</dc:creator>
        <dc:creator id="writer">Author</dc:creator><meta property="role" refines="#writer">aut</meta>
        <dc:language>sa</dc:language><meta property="custom">preserved</meta></metadata>
        <manifest>{items}{nav_item}</manifest><spine>{refs}</spine></package>""",
        )
        for i, body in enumerate(bodies):
            z.writestr(
                f"OPS/s{i}.xhtml",
                (
                    f'<?xml version="1.0" encoding="{encoding}"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Book</title></head><body data-test="a &gt; b">{body}</body></html>'
                ).encode(encoding),
            )
        z.writestr("OPS/image.png", b"original-image-bytes")
        if nav:
            z.writestr(
                "OPS/nav.xhtml",
                '<html><body><nav><ol><li><a href="s0.xhtml#chapter">Chapter</a></li></ol></nav></body></html>',
            )
        if encrypted:
            z.writestr(
                "META-INF/encryption.xml",
                '<encryption><EncryptedData><CipherData><CipherReference URI="OPS/s0.xhtml"/></CipherData></EncryptedData></encryption>',
            )
    return path


def node(start, end, title="Chapter", type="Chapter", index=None, children=None):
    return {
        "title": title,
        "type": type,
        "index": index,
        "start": start,
        "end": end,
        "sub-sections": children or [],
    }


def plan_for(source):
    return {
        "front_matter": [],
        "content": [node(0, len(source.units))],
        "back_matter": [],
    }


def provider_for(*plans):
    return FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id=f"call-{i}", name="submit_structure", arguments=plan
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            ]
            for i, plan in enumerate(plans)
        ]
    )


def fragments(document):
    def visit(section):
        yield from section.content
        for child in section.sub_sections:
            yield from visit(child)

    return sorted(
        (
            f
            for group in (document.front_matter, document.content, document.back_matter)
            for section in group
            for f in visit(section)
        ),
        key=lambda f: (f.source.spine_index, f.source.start_byte),
    )


def assert_fidelity(source, document):
    result = fragments(document)
    for f in result:
        ref = f.source
        assert (
            f.xhtml.encode(ref.encoding)
            == source.members[ref.member][ref.start_byte : ref.end_byte]
        )
    for spine in source.spine:
        units = source.units[spine["start"] : spine["end"]]
        if not units:
            continue
        matching = [f for f in result if f.source.spine_index == units[0].spine_index]
        assert matching[0].source.start_byte == units[0].start
        assert matching[-1].source.end_byte == units[-1].end
        for a, b in zip(matching, matching[1:]):
            assert a.source.end_byte == b.source.start_byte
        assert (
            "".join(f.xhtml for f in matching).encode(units[0].encoding)
            == source.members[units[0].member][units[0].start : units[-1].end]
        )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "iso-8859-1"])
async def test_exact_bytes_and_persistence(tmp_path, encoding):
    body = '\n<h2 id="chapter">CHAPTER I</h2>text outside<p>Café &amp; <em>verse</em><br/>line 2</p><!-- comment --><img src="image.png"/>\n'
    path = epub_file(tmp_path, [body], encoding=encoding)
    source = EbookSource(path)
    provider = provider_for(plan_for(source))
    agent = EbookLoaderAgent(provider, "scripted", output_dir=tmp_path / "out")
    document = await agent.load(path)
    assert_fidelity(source, document)
    assert document.author == "Author"
    destination = next((tmp_path / "out").iterdir())
    assert destination.name.endswith(hashlib.sha256(path.read_bytes()).hexdigest())
    assert (destination / "source.epub").read_bytes() == path.read_bytes()
    assert (
        EbookDocument.model_validate_json((destination / "book.json").read_text())
        == document
    )
    assert (
        "sub-sections"
        in json.loads((destination / "book.json").read_text())["content"][0]
    )
    assert len(provider.calls) == 1


async def test_nested_interleaving_cross_file_and_unicode(tmp_path):
    path = epub_file(
        tmp_path,
        [
            "<h1>Front</h1>",
            '<h1 id="parva">Parva</h1><p>before</p><h2 id="upa">Upaparva</h2><h3 id="adh">Adhyāya</h3><p id="verse">Śloka १</p><p id="between">between</p>',
            '<p id="verse2">Śloka २</p><p id="after">after</p>',
            "<p>License</p>",
        ],
        nav=False,
    )
    source = EbookSource(path)
    ids = {u.anchor: i for i, u in enumerate(source.units) if u.anchor}
    a, b = source.spine[1]["start"], source.spine[2]["end"]
    verse = node(ids["verse"], ids["between"], "Śloka", "Śloka", "१")
    verse2 = node(ids["verse2"], ids["after"], "Śloka", "Śloka", "२")
    adh = node(ids["adh"], b, "Adhyāya", "Adhyāya", children=[verse, verse2])
    upa = node(ids["upa"], b, "Upaparva", "Upaparva", children=[adh])
    plan = {
        "front_matter": [node(0, a, "Front", "Cover")],
        "content": [node(a, b, "Parva", "Parva", children=[upa])],
        "back_matter": [node(b, len(source.units), "License", "License")],
    }
    doc = await EbookLoaderAgent(
        provider_for(plan), "scripted", output_dir=tmp_path / "out"
    ).load(path)
    assert_fidelity(source, doc)
    section = doc.content[0].sub_sections[0].sub_sections[0]
    assert [s.index for s in section.sub_sections] == ["१", "२"]
    assert "between" in "".join(f.xhtml for f in section.content)
    assert "after" in "".join(f.xhtml for f in section.content)


@pytest.mark.parametrize("kind", ["gap", "overlap", "escape", "reversed", "empty"])
def test_reject_invalid_structure(tmp_path, kind):
    source = EbookSource(epub_file(tmp_path, ["<h2>A</h2><p>B</p><p>C</p>"]))
    plan = plan_for(source)
    n = len(source.units)
    if kind == "gap":
        plan["content"][0]["end"] = n - 1
    elif kind == "overlap":
        plan["content"].append(node(0, n))
    elif kind == "escape":
        plan["content"][0]["sub-sections"] = [node(0, n + 1)]
    elif kind == "reversed":
        plan["content"][0]["sub-sections"] = [node(2, 1)]
    else:
        plan["content"] = []
    with pytest.raises(EbookLoadError):
        source.materialize(StructurePlan.model_validate(plan))


async def test_validation_feedback_then_correction(tmp_path):
    path = epub_file(tmp_path, ["<h2>A</h2><p>B</p>"])
    source = EbookSource(path)
    good = plan_for(source)
    bad = copy.deepcopy(good)
    bad["content"][0]["start"] = 1
    provider = provider_for(bad, good)
    doc = await EbookLoaderAgent(
        provider, "scripted", output_dir=tmp_path / "out"
    ).load(path)
    assert_fidelity(source, doc)
    assert len(provider.calls) == 2
    assert any(getattr(m, "is_error", False) for m in provider.calls[1][2])


async def test_exhaustion_leaves_no_output(tmp_path):
    path = epub_file(tmp_path, ["<p>A</p>"])
    bad = plan_for(EbookSource(path))
    bad["content"][0]["start"] = 1
    with pytest.raises(EbookLoadError, match="max_turns=1"):
        await EbookLoaderAgent(
            provider_for(bad), "scripted", max_turns=1, output_dir=tmp_path / "out"
        ).load(path)
    assert not (tmp_path / "out").exists()


async def test_repeat_publication_and_failure_preserve_previous(tmp_path):
    path = epub_file(tmp_path, ["<p>A</p>"])
    plan = plan_for(EbookSource(path))
    output = tmp_path / "out"
    agent = EbookLoaderAgent(provider_for(plan, plan), "scripted", output_dir=output)
    await agent.load(path)
    await agent.load(path)
    destination = next(output.iterdir())
    old_json = (destination / "book.json").read_bytes()
    (destination / "source.epub").write_bytes(b"corrupted")
    with pytest.raises(EbookLoadError, match="hash mismatch"):
        await EbookLoaderAgent(provider_for(plan), "scripted", output_dir=output).load(
            path
        )
    assert (destination / "book.json").read_bytes() == old_json
    assert len(list(output.iterdir())) == 1


@pytest.mark.parametrize("kind", ["missing", "malformed", "encrypted"])
async def test_input_errors(tmp_path, kind):
    path = tmp_path / "missing.epub"
    if kind == "malformed":
        path.write_bytes(b"not a zip")
    elif kind == "encrypted":
        path = epub_file(tmp_path, ["<p>A</p>"], encrypted=True)
    with pytest.raises(EbookLoadError):
        await EbookLoaderAgent(
            FakeProvider([]), "scripted", output_dir=tmp_path / "out"
        ).load(path)
    assert not (tmp_path / "out").exists()


async def test_provider_failure(tmp_path):
    class BrokenProvider:
        async def stream_response(self, **kwargs):
            raise RuntimeError("provider unavailable")
            yield

    path = epub_file(tmp_path, ["<p>A</p>"])
    with pytest.raises(EbookLoadError, match="provider unavailable"):
        await EbookLoaderAgent(
            BrokenProvider(), "scripted", output_dir=tmp_path / "out"
        ).load(path)
    assert not (tmp_path / "out").exists()


async def test_tools_paginate_and_resolve(tmp_path):
    path = epub_file(tmp_path, ['<h2 id="chapter">I</h2><p>' + "word " * 2000 + "</p>"])
    source = EbookSource(path)
    calls = [
        ("list_outline", {"start": 0, "limit": 1}),
        ("list_spine", {"start": 0, "limit": 1}),
        ("list_units", {"start": 0, "limit": 1}),
        ("read_navigation", {"offset": 0, "limit": 20}),
        ("resolve_source", {"member": "OPS/s0.xhtml", "anchor": "chapter"}),
        ("inspect_units", {"unit": 1, "offset": 0, "limit": 10}),
        ("inspect_units", {"unit": 999999}),
    ]
    streams = [
        [
            AssistantDoneEvent(
                reason="toolUse",
                message=AssistantMessage(
                    content=[ToolCall(id=f"inspect-{i}", name=name, arguments=args)],
                    stop_reason="toolUse",
                ),
            )
        ]
        for i, (name, args) in enumerate(calls)
    ]
    streams.append(
        [
            AssistantDoneEvent(
                reason="toolUse",
                message=AssistantMessage(
                    content=[
                        ToolCall(
                            id="submit",
                            name="submit_structure",
                            arguments=plan_for(source),
                        )
                    ],
                    stop_reason="toolUse",
                ),
            )
        ]
    )
    provider = FakeProvider(streams)
    await EbookLoaderAgent(provider, "scripted", output_dir=tmp_path / "out").load(path)
    results = {m.tool_name: m for m in provider.calls[-1][2] if m.role == "toolResult"}
    assert json.loads(results["resolve_source"].text)["unit"] == 0
    assert len(json.loads(results["read_navigation"].text)["text"]) == 20
    assert len(json.loads(results["list_units"].text)["items"]) == 1
    assert results["inspect_units"].is_error


def test_parent_owns_bare_tail_text(tmp_path):
    source = EbookSource(
        epub_file(tmp_path, ['<div>parent<p id="child">child</p>bare tail</div>'])
    )
    child_start = next(i for i, u in enumerate(source.units) if u.anchor == "child")
    tail_start = child_start + 1
    assert source.describe(tail_start, 1)[0]["preview"].startswith("bare tail")
    plan = plan_for(source)
    plan["content"][0]["sub-sections"] = [node(child_start, tail_start)]
    doc = source.materialize(StructurePlan.model_validate(plan))
    assert "bare tail" in "".join(f.xhtml for f in doc.content[0].content)
    assert "bare tail" not in "".join(
        f.xhtml for f in doc.content[0].sub_sections[0].content
    )
    assert_fidelity(source, doc)


async def test_failed_replace_keeps_valid_json(tmp_path, monkeypatch):
    path = epub_file(tmp_path, ["<p>A</p>"])
    plan = plan_for(EbookSource(path))
    agent = EbookLoaderAgent(
        provider_for(plan, plan), "scripted", output_dir=tmp_path / "out"
    )
    await agent.load(path)
    destination = next((tmp_path / "out").iterdir())
    old = (destination / "book.json").read_bytes()

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(EbookLoadError, match="simulated disk failure"):
        await agent.load(path)
    assert (destination / "book.json").read_bytes() == old
    assert len(list((tmp_path / "out").iterdir())) == 1


async def test_unwritable_output_reports_loader_error(tmp_path):
    path = epub_file(tmp_path, ["<p>A</p>"])
    output = tmp_path / "not-a-directory"
    output.write_text("existing file")
    with pytest.raises(EbookLoadError, match="Cannot publish ebook"):
        await EbookLoaderAgent(
            provider_for(plan_for(EbookSource(path))), "scripted", output_dir=output
        ).load(path)
    assert output.read_text() == "existing file"


@pytest.mark.parametrize(
    "filename, chapters, acts, scenes",
    [
        ("dracula.epub", 27, 0, 0),
        ("alice-in-wonderland.epub", 12, 0, 0),
        ("pg1523-images-3.epub", 0, 5, 22),
    ],
)
async def test_optional_supplied_books(tmp_path, filename, chapters, acts, scenes):
    import re

    directory = os.environ.get("DIORAMA_TEST_BOOKS")
    if not directory:
        pytest.skip("Set DIORAMA_TEST_BOOKS to run local EPUB integration tests")
    path = Path(directory) / filename
    source = EbookSource(path)
    headings = [(i, u.heading.strip()) for i, u in enumerate(source.units) if u.heading]
    starts = [
        (i, text)
        for i, text in headings
        if re.match(r"^(CHAPTER|ACT)\s+[IVXLCDM]+", text)
    ]
    scene_starts = [
        (i, text) for i, text in headings if re.match(r"^SCENE\s+[IVXLCDM]+", text)
    ]
    assert len(starts) == (chapters or acts)
    assert len(scene_starts) == scenes
    back_start = next(i for i, text in headings if "FULL PROJECT GUTENBERG" in text)
    nodes = []
    for j, (start, title) in enumerate(starts):
        end = starts[j + 1][0] if j + 1 < len(starts) else back_start
        children = []
        scene_ranges = [
            (i, t)
            for i, t in headings
            if start < i < end and (t.startswith("SCENE ") or t == "EPILOGUE")
        ]
        for k, (a, t) in enumerate(scene_ranges):
            b = scene_ranges[k + 1][0] if k + 1 < len(scene_ranges) else end
            children.append(
                node(a, b, t, "Scene" if t.startswith("SCENE ") else "Epilogue")
            )
        nodes.append(
            node(start, end, title, "Chapter" if chapters else "Act", children=children)
        )
    plan = {
        "front_matter": [node(0, starts[0][0], "Front matter", "FrontMatter")],
        "content": nodes,
        "back_matter": [node(back_start, len(source.units), "License", "License")],
    }
    document = await EbookLoaderAgent(
        provider_for(plan), "scripted", output_dir=tmp_path / "out"
    ).load(path)
    assert_fidelity(source, document)
    assert len(document.content) == (chapters or acts)
