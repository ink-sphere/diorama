from __future__ import annotations

import asyncio
import json
import shlex
import sys
from hashlib import sha256
from html import escape
from pathlib import Path

import html2text
import pytest
from ebooklib import epub
from pydantic import ValidationError
from tau_agent import (
    AssistantMessage,
    SimpleCancellationToken,
    ToolCall,
    ToolResultMessage,
)
from tau_ai import AssistantDoneEvent, AssistantErrorEvent, FakeProvider

from diorama.agents.ebook_structure_agent import (
    EbookStructureAgent,
    EbookStructureError,
    StructurePlan,
    load_storybook,
    materialize_storybook,
    parse_ebook,
    validate_plan,
)
from diorama.agents.ebook_structure_agent.prompts import SYSTEM_PROMPT
from diorama.models.storybook import StoryBook, StructureNode, TextContent


def write_ebook(
    path: Path, contents: list[str], *, reverse_manifest: bool = False
) -> Path:
    book = epub.EpubBook()
    book.set_identifier("test-book")
    book.set_title("A Test Book")
    book.set_language("en")
    book.add_author("Test Author")
    book.add_metadata("DC", "publisher", "Test Publisher")
    book.add_metadata("DC", "subject", "Fiction")
    book.add_metadata(
        None, "meta", "", {"name": "calibre:series", "content": "Test Series"}
    )
    book.add_metadata(
        None, "meta", "", {"name": "calibre:series_index", "content": "2"}
    )
    chapters = [
        epub.EpubHtml(
            uid=f"c{index}",
            file_name=f"c{index}.xhtml",
            title=f"Document {index}",
            content=content,
        )
        for index, content in enumerate(contents)
    ]
    for chapter in reversed(chapters) if reverse_manifest else chapters:
        book.add_item(chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", *chapters]
    book.toc = [
        (
            epub.Section("Part One"),
            [
                epub.Link(
                    f"{chapter.file_name}#opening", f"Chapter {index}", f"link{index}"
                )
                for index, chapter in enumerate(chapters)
            ],
        )
    ]
    epub.write_epub(path, book)
    return path


@pytest.fixture
def ebook_path(tmp_path):
    return write_ebook(
        tmp_path / "book.epub",
        [
            '<section id="opening" epub:type="chapter"><h1>Chapter One</h1>'
            "<p>First <em>paragraph</em>.</p></section>",
            '<h1 id="opening">Chapter Two</h1><p>Second paragraph.</p>',
        ],
        reverse_manifest=True,
    )


def leaf(first: str, last: str, title: str = "", narrative: bool = True) -> dict:
    return {
        "structure_type": "chapter",
        "structure_title": title,
        "is_part_of_narrative": narrative,
        "start_block_id": first,
        "end_block_id": last,
    }


def complete_plan(source) -> dict:
    return {"nodes": [leaf(source.blocks[0].id, source.blocks[-1].id, "Chapter One")]}


def call_stream(name: str, arguments: dict, call_id: str = "call") -> list:
    message = AssistantMessage(
        content=[ToolCall(id=call_id, name=name, arguments=arguments)],
        stop_reason="toolUse",
    )
    return [AssistantDoneEvent(reason="toolUse", message=message)]


def flatten_content(nodes: list[StructureNode]) -> list[TextContent]:
    content = []
    for node in nodes:
        if node.content and isinstance(node.content[0], StructureNode):
            content.extend(flatten_content(node.content))
        else:
            content.extend(node.content)
    return content


def test_parser_preserves_spine_order_metadata_and_navigation_targets(ebook_path):
    source = parse_ebook(ebook_path)
    assert [block.text for block in source.blocks] == [
        "Chapter One",
        "First paragraph .",
        "Chapter Two",
        "Second paragraph.",
    ]
    assert [doc.href for doc in source.documents] == ["c0.xhtml", "c1.xhtml"]
    assert source.metadata.title == "A Test Book"
    assert source.metadata.authors == ["Test Author"]
    assert source.metadata.publisher == "Test Publisher"
    assert source.metadata.subjects == ["Fiction"]
    assert source.metadata.series == "Test Series"
    assert source.metadata.series_position == 2
    assert source.toc[1].block_id == "b000001"
    assert source.toc[2].block_id == "b000003"
    assert source.toc[1].depth == 1
    assert source.blocks[0].epub_types == ("chapter",)
    assert source.excluded_documents[0]["href"] == "nav.xhtml"
    assert parse_ebook(ebook_path).id == source.id


def test_parser_preserves_media_tables_and_inline_markup(tmp_path):
    path = write_ebook(
        tmp_path / "media.epub",
        [
            "<div>Opening <em>emphasis</em>.</div>"
            '<figure><img src="cover.jpg" alt="A house"/><figcaption>A house</figcaption></figure>'
            "<table><tr><td>Cell</td></tr></table><hr/>"
            "<p>Verse<br/>next line</p>",
        ],
    )
    source = parse_ebook(path)
    assert len(source.blocks) == 5
    assert "<em>emphasis</em>" in source.blocks[0].raw_text
    assert source.blocks[1].tag == "figure"
    assert '<img alt="A house" src="cover.jpg"/>' in source.blocks[1].raw_text
    assert source.blocks[2].tag == "table"
    assert source.blocks[3].tag == "hr"
    assert "<br/>" in source.blocks[4].raw_text
    book = materialize_storybook(
        StructurePlan.model_validate(complete_plan(source)), source
    )
    assert "_emphasis_" in book.structure[0].content[0].markdown_text
    assert "cover.jpg" in book.structure[0].content[1].markdown_text


def test_parser_indexes_plain_divisions_and_skips_nested_navigation(tmp_path):
    path = write_ebook(
        tmp_path / "divisions.epub",
        [
            '<section><nav epub:type="toc"><a href="#chapter">Chapter</a></nav>'
            '<div id="chapter">Opening <em>sentence</em>.</div>'
            "<div>Second paragraph.</div></section>",
        ],
    )
    source = parse_ebook(path)
    assert len(source.blocks) == 2
    assert "Opening" in source.blocks[0].text
    assert "<em>sentence</em>" in source.blocks[0].raw_text
    assert source.blocks[1].text == "Second paragraph."
    assert all("<nav" not in block.raw_text for block in source.blocks)


def test_parser_handles_non_spine_and_non_linear_documents(tmp_path):
    path = tmp_path / "notes.epub"
    book = epub.EpubBook()
    book.set_identifier("notes")
    book.set_title("Notes")
    book.set_language("en")
    main = epub.EpubHtml(uid="main", file_name="main.xhtml", content="<p>Story</p>")
    notes = epub.EpubHtml(
        uid="notes", file_name="notes.xhtml", content="<p>Footnote</p>"
    )
    extra = epub.EpubHtml(
        uid="extra", file_name="extra.xhtml", content="<p>Supplement</p>"
    )
    for item in (extra, notes, main):
        book.add_item(item)
    book.add_item(epub.EpubNav())
    book.spine = [main, (notes, "no")]
    epub.write_epub(path, book)
    source = parse_ebook(path)
    assert [block.text for block in source.blocks] == [
        "Story",
        "Footnote",
        "Supplement",
    ]
    assert source.documents[1].linear is False
    assert source.documents[1].in_spine is True
    assert source.documents[2].in_spine is False


def test_parser_rejects_invalid_or_empty_ebooks(tmp_path):
    path = tmp_path / "invalid.epub"
    path.write_text("not an EPUB")
    with pytest.raises(EbookStructureError, match="Unable to read EPUB"):
        parse_ebook(path)
    empty = write_ebook(tmp_path / "empty.epub", ["<p></p>"])
    source = parse_ebook(empty)
    assert source.blocks[0].text == ""


@pytest.mark.parametrize(
    "nodes, error",
    [
        ([leaf("missing", "b000004")], "Unknown source range"),
        ([leaf("b000002", "b000004")], "missing content"),
        ([leaf("b000001", "b000002")], "Missing content"),
        ([leaf("b000001", "b000003"), leaf("b000003", "b000004")], "overlap"),
        ([leaf("b000001", "b000004"), leaf("b000001", "b000004")], "overlap"),
        ([leaf("b000002", "b000001")], "reversed"),
    ],
)
def test_plan_requires_exact_ordered_coverage(ebook_path, nodes, error):
    with pytest.raises(ValueError, match=error):
        validate_plan(
            StructurePlan.model_validate({"nodes": nodes}), parse_ebook(ebook_path)
        )


def test_plan_rejects_mixed_content_and_coerced_arguments(ebook_path):
    node = leaf("b000001", "b000004")
    with pytest.raises(ValidationError, match="no source range"):
        StructurePlan.model_validate({"nodes": [dict(node, children=[node])]})
    with pytest.raises(ValidationError):
        StructurePlan.model_validate(
            {"nodes": [dict(node, is_part_of_narrative="true")]}
        )
    with pytest.raises(ValidationError):
        StructurePlan.model_validate({"nodes": [dict(node, invented_field=1)]})


def test_materializer_preserves_all_content_in_nested_cross_document_nodes(ebook_path):
    source = parse_ebook(ebook_path)
    plan = StructurePlan.model_validate(
        {
            "nodes": [
                {
                    "structure_type": "part",
                    "structure_title": "Part One",
                    "is_part_of_narrative": True,
                    "children": [
                        leaf("b000001", "b000001", narrative=False),
                        leaf("b000002", "b000004"),
                    ],
                }
            ]
        }
    )
    book = materialize_storybook(plan, source)
    assert isinstance(book, StoryBook)
    assert [text.raw_text for text in flatten_content(book.structure)] == [
        block.raw_text for block in source.blocks
    ]
    assert book.metadata == source.metadata
    assert book.metadata is not source.metadata
    assert len(book.structure[0].content[1].content) == 3


def done_stream() -> list:
    return [AssistantDoneEvent(reason="stop", message=AssistantMessage(content="Done"))]


def ebook_artifact(ebook_path) -> StoryBook:
    source = parse_ebook(ebook_path)
    return materialize_storybook(
        StructurePlan.model_validate(complete_plan(source)), source
    )


def text_artifact(data: bytes, raw: str | None = None) -> StoryBook:
    raw = raw if raw is not None else escape(data.decode("utf-8-sig"))
    converter = html2text.HTML2Text()
    converter.body_width = 0
    return StoryBook(
        id=sha256(data).hexdigest(),
        metadata={"title": "A Book"},
        structure=[
            StructureNode(
                structure_type="chapter",
                structure_title="",
                is_part_of_narrative=True,
                content=[
                    TextContent(
                        raw_text=raw, markdown_text=converter.handle(raw).strip()
                    )
                ],
            )
        ],
    )


def artifact_stream(book: StoryBook) -> list:
    return call_stream(
        "write", {"path": "output/storybook.json", "content": book.model_dump_json()}
    )


@pytest.mark.asyncio
async def test_coding_session_runs_scripts_with_standard_tools(ebook_path, tmp_path):
    source = parse_ebook(ebook_path)
    command = (
        f"{shlex.quote(sys.executable)} reference/ebook_tools.py "
        "build input/book.epub --plan work/structure.json --output output/storybook.json"
    )
    provider = FakeProvider(
        [
            call_stream("read", {"path": "reference/storybook_schema.json"}),
            call_stream(
                "write",
                {
                    "path": "work/structure.json",
                    "content": json.dumps(complete_plan(source)),
                },
            ),
            call_stream("bash", {"command": command}),
            call_stream(
                "bash",
                {
                    "command": f"{shlex.quote(sys.executable)} reference/ebook_tools.py validate input/book.epub --book output/storybook.json"
                },
            ),
            done_stream(),
        ]
    )
    events = []
    book = await EbookStructureAgent(
        provider=provider, model="fake", workspace_root=tmp_path / "runs"
    ).run(ebook_path, on_event=events.append)
    assert book == ebook_artifact(ebook_path)
    assert {tool.name for tool in provider.calls[0][3]} == {
        "read",
        "write",
        "edit",
        "bash",
    }
    assert SYSTEM_PROMPT.strip() in provider.calls[0][1]
    assert "<available_skills>" not in provider.calls[0][1]
    assert provider.calls[0][2][-1].text.startswith(
        "Extract this ebook into a StoryBook."
    )
    assert "Preserve source text" not in provider.calls[0][2][-1].text
    assert all(
        not message.is_error
        for call in provider.calls
        for message in call[2]
        if isinstance(message, ToolResultMessage)
    )
    workspace = next((tmp_path / "runs").iterdir())
    assert (workspace / "runtime/session.jsonl").is_file()
    assert not (workspace / "runtime/skills").exists()
    assert (workspace / "reference/storybook_schema.json").is_file()
    assert (workspace / "input/book.epub").read_bytes() == ebook_path.read_bytes()
    assert any(event.type == "agent_settled" for event in events)


@pytest.mark.asyncio
async def test_failed_validation_is_repaired_in_the_same_session(ebook_path):
    valid = ebook_artifact(ebook_path)
    invalid = valid.model_copy(deep=True)
    invalid.structure[0].content.pop()
    provider = FakeProvider(
        [artifact_stream(invalid), done_stream(), artifact_stream(valid), done_stream()]
    )
    result = await EbookStructureAgent(provider=provider, model="fake").run(ebook_path)
    assert result == valid
    assert len(set(provider.session_ids)) == 1
    assert all(SYSTEM_PROMPT.strip() in call[1] for call in provider.calls)
    messages = provider.calls[2][2]
    assert any(
        getattr(message, "text", "").startswith("Independent validation rejected")
        for message in messages
    )
    assert any(isinstance(message, ToolResultMessage) for message in messages)


@pytest.mark.asyncio
async def test_generic_text_needs_no_epub_tools(tmp_path):
    path = tmp_path / "book.txt"
    data = b"Chapter One\n\nA < B & C > D.\n\nChapter Two\nThe end."
    path.write_bytes(data)
    book = text_artifact(data)
    script = """import hashlib, html, sys
from pathlib import Path
import html2text
sys.path.insert(0, 'reference/library')
from diorama.models.storybook import StoryBook, StructureNode, TextContent
data = Path('input/book.txt').read_bytes()
raw = html.escape(data.decode())
converter = html2text.HTML2Text()
converter.body_width = 0
book = StoryBook(id=hashlib.sha256(data).hexdigest(), metadata={'title': 'A Book'},
    structure=[StructureNode(structure_type='chapter', structure_title='',
        is_part_of_narrative=True, content=[TextContent(raw_text=raw,
            markdown_text=converter.handle(raw).strip())])])
Path('output/storybook.json').write_text(book.model_dump_json())
"""
    provider = FakeProvider(
        [
            call_stream("write", {"path": "work/extract.py", "content": script}),
            call_stream(
                "bash", {"command": f"{shlex.quote(sys.executable)} work/extract.py"}
            ),
            done_stream(),
        ]
    )
    assert await EbookStructureAgent(provider=provider, model="fake").run(path) == book


@pytest.mark.asyncio
async def test_custom_source_validator_receives_original_bytes(tmp_path):
    path = tmp_path / "book.custom"
    data = b"custom-format-source"
    path.write_bytes(data)
    book = text_artifact(data)
    calls = []

    def validate(result, original, filename):
        calls.append((result, original, filename))
        assert original == data

    provider = FakeProvider([artifact_stream(book), done_stream()])
    result = await EbookStructureAgent(
        provider=provider, model="fake", source_validator=validate
    ).run(path)
    assert calls == [(result, data, path.name)]


@pytest.mark.asyncio
async def test_local_helper_edits_cannot_disable_host_validation(ebook_path):
    book = ebook_artifact(ebook_path)
    book.structure[0].content.pop()
    provider = FakeProvider(
        [
            call_stream(
                "write",
                {
                    "path": "reference/library/diorama/agents/ebook_structure_agent/validation.py",
                    "content": "def load_storybook(*args, **kwargs): return None",
                },
            ),
            artifact_stream(book),
            done_stream(),
        ]
    )
    with pytest.raises(EbookStructureError, match="Source content changed"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_repair_attempts=0
        ).run(ebook_path)


@pytest.mark.asyncio
async def test_staged_input_edits_cannot_change_source_identity(tmp_path):
    path = tmp_path / "book.txt"
    path.write_text("Original text")
    provider = FakeProvider(
        [
            call_stream(
                "write", {"path": "input/book.txt", "content": "Replacement text"}
            ),
            artifact_stream(text_artifact(b"Replacement text")),
            done_stream(),
        ]
    )
    with pytest.raises(EbookStructureError, match="SHA-256"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_repair_attempts=0
        ).run(path)


@pytest.mark.asyncio
async def test_output_is_validated_after_the_session_finishes(ebook_path):
    provider = FakeProvider(
        [
            artifact_stream(ebook_artifact(ebook_path)),
            call_stream("write", {"path": "output/storybook.json", "content": "{}"}),
            done_stream(),
        ]
    )
    with pytest.raises(EbookStructureError, match="validation failed"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_repair_attempts=0
        ).run(ebook_path)


@pytest.mark.asyncio
async def test_repairs_are_bounded(ebook_path):
    provider = FakeProvider([done_stream(), done_stream(), done_stream()])
    with pytest.raises(EbookStructureError, match="Missing output"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_repair_attempts=2
        ).run(ebook_path)
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_turn_limit_applies_across_repairs(ebook_path):
    provider = FakeProvider(
        [done_stream(), artifact_stream(ebook_artifact(ebook_path))]
    )
    with pytest.raises(EbookStructureError, match="max_turns=1"):
        await EbookStructureAgent(provider=provider, model="fake", max_turns=1).run(
            ebook_path
        )


@pytest.mark.asyncio
async def test_model_errors_are_reported(ebook_path):
    message = AssistantMessage(
        content="", stop_reason="error", error_message="Model failed"
    )
    provider = FakeProvider([[AssistantErrorEvent(reason="error", error=message)]])
    with pytest.raises(EbookStructureError, match="Model failed"):
        await EbookStructureAgent(provider=provider, model="fake").run(ebook_path)


@pytest.mark.asyncio
async def test_temporary_workspace_is_removed_and_provider_is_borrowed(ebook_path):
    class Provider(FakeProvider):
        closed = False

        async def aclose(self):
            self.closed = True

    provider = Provider([artifact_stream(ebook_artifact(ebook_path)), done_stream()])
    workspace = None
    events = []

    async def observe(event):
        events.append(event)

    class ObservedAgent(EbookStructureAgent):
        def _prepare_workspace(self, directory, data, filename):
            nonlocal workspace
            workspace = directory
            return super()._prepare_workspace(directory, data, filename)

    await ObservedAgent(provider=provider, model="fake").run(
        ebook_path, on_event=observe
    )
    assert workspace is not None and not workspace.exists()
    assert events
    assert not provider.closed


@pytest.mark.asyncio
async def test_cancellation_before_start(ebook_path):
    signal = SimpleCancellationToken()
    signal.cancel()
    provider = FakeProvider([])
    with pytest.raises(asyncio.CancelledError):
        await EbookStructureAgent(provider=provider, model="fake").run(
            ebook_path, signal=signal
        )
    assert not provider.calls


class WaitingProvider(FakeProvider):
    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()

    def stream_response(self, **kwargs):
        async def stream():
            self.started.set()
            await asyncio.Event().wait()
            yield

        return stream()


@pytest.mark.asyncio
async def test_cancellation_interrupts_a_waiting_model(ebook_path):
    signal = SimpleCancellationToken()
    provider = WaitingProvider()
    task = asyncio.create_task(
        EbookStructureAgent(provider=provider, model="fake").run(
            ebook_path, signal=signal
        )
    )
    await asyncio.wait_for(provider.started.wait(), 2)
    signal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_timeout_interrupts_a_waiting_model(ebook_path):
    with pytest.raises(EbookStructureError, match="timed out"):
        await EbookStructureAgent(
            provider=WaitingProvider(), model="fake", timeout_seconds=0.2
        ).run(ebook_path)


@pytest.mark.asyncio
async def test_context_limit_stops_before_a_model_request(ebook_path):
    provider = FakeProvider([])
    with pytest.raises(EbookStructureError, match="max_context_characters"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_context_characters=1
        ).run(ebook_path)
    assert not provider.calls


@pytest.mark.asyncio
async def test_output_symlink_outside_workspace_is_rejected(ebook_path, tmp_path):
    artifact = tmp_path / "outside.json"
    artifact.write_text(ebook_artifact(ebook_path).model_dump_json())
    provider = FakeProvider(
        [
            call_stream(
                "bash",
                {
                    "command": f"ln -s {shlex.quote(str(artifact))} output/storybook.json"
                },
            ),
            done_stream(),
        ]
    )
    with pytest.raises(EbookStructureError, match="inside the workspace"):
        await EbookStructureAgent(
            provider=provider, model="fake", max_repair_attempts=0
        ).run(ebook_path)


@pytest.mark.parametrize(
    "change,error",
    [
        ("omit", "Source content changed"),
        ("duplicate", "Source content changed"),
        ("reorder", "Source content changed"),
        ("markup", "Source content changed"),
        ("metadata", "Source metadata changed"),
        ("id", "SHA-256"),
        ("markdown", "Markdown content"),
        ("empty", "non-empty content"),
        ("extra", "Extra inputs"),
    ],
)
def test_trusted_validation_rejects_invalid_artifacts(
    ebook_path, tmp_path, change, error
):
    book = ebook_artifact(ebook_path)
    node = book.structure[0]
    if change == "omit":
        node.content.pop()
    elif change == "duplicate":
        node.content.append(node.content[-1])
    elif change == "reorder":
        node.content.reverse()
    elif change == "markup":
        node.content[1].raw_text = "<p>First paragraph.</p>"
        node.content[1].markdown_text = "First paragraph."
    elif change == "metadata":
        book.metadata.title = "Invented title"
    elif change == "id":
        book.id = "invented"
    elif change == "markdown":
        node.content[0].markdown_text = "Rewritten"
    elif change == "empty":
        node.content = []
    payload = book.model_dump()
    if change == "extra":
        payload["structure"][0]["content"][0]["invented"] = True
    output = tmp_path / "storybook.json"
    output.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match=error):
        load_storybook(output, ebook_path.read_bytes(), ebook_path.name)


def test_trusted_validation_limits_artifact_size(ebook_path, tmp_path):
    output = tmp_path / "storybook.json"
    output.write_text(ebook_artifact(ebook_path).model_dump_json())
    with pytest.raises(ValueError, match="max_output_bytes"):
        load_storybook(
            output, ebook_path.read_bytes(), ebook_path.name, max_output_bytes=10
        )


@pytest.mark.parametrize("suffix", [".txt", ".md", ".html"])
def test_trusted_validation_checks_supported_non_epub_formats(tmp_path, suffix):
    data = b"<p>First paragraph.</p>" if suffix == ".html" else b"First paragraph."
    book = text_artifact(data, data.decode() if suffix == ".html" else None)
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    assert load_storybook(output, data, "book" + suffix) == book
    changed = data.replace(b"First", b"Other")
    book.id = sha256(changed).hexdigest()
    output.write_text(book.model_dump_json())
    with pytest.raises(ValueError, match="Source (text|markup) changed"):
        load_storybook(output, changed, "book" + suffix)


def test_html_validation_ignores_document_boilerplate(tmp_path):
    data = b"<!DOCTYPE html><html><head><title>A Book</title></head><body><p>Text.</p></body></html>"
    book = text_artifact(data, "<p>Text.</p>")
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    assert load_storybook(output, data, "book.html") == book


@pytest.mark.asyncio
async def test_cancellation_during_staging_waits_for_cleanup(ebook_path):
    import threading

    started = threading.Event()
    release = threading.Event()
    workspace = None

    class DelayedAgent(EbookStructureAgent):
        def _prepare_workspace(self, directory, data, filename):
            nonlocal workspace
            workspace = directory
            started.set()
            release.wait(timeout=2)
            return super()._prepare_workspace(directory, data, filename)

    task = asyncio.create_task(
        DelayedAgent(provider=FakeProvider([]), model="fake").run(ebook_path)
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert workspace is not None and not workspace.exists()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.fixture
def json_export(tmp_path):
    from zipfile import ZipFile

    path = tmp_path / "export.epub"
    fragments = [
        "<h1>Chapter One</h1><p>First <em>paragraph</em>.</p>",
        '<h1>Chapter Two</h1><p>Second paragraph.</p><img src="cover.jpg"/>',
    ]
    payload = {
        "title": "Fallback Book Title",
        "metadata": {
            "http://purl.org/dc/elements/1.1/": {
                "title": [["A Test Book", {}]],
                "creator": [["Test Author", {}]],
                "language": [["en", {}]],
            },
        },
        "chapters": [
            {"chapter_index": 9 - index, "content": fragment, "segments": []}
            for index, fragment in enumerate(fragments)
        ],
    }
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {"version": 1, "title": "Manifest Title", "author": "Manifest Author"}
            ),
        )
        archive.writestr("book.json", json.dumps(payload))
        archive.writestr("profiles.json", "{}")
        archive.writestr("cover.jpg", b"image")
    book = text_artifact(path.read_bytes(), fragments[0])
    book.metadata.title = "A Test Book"
    book.metadata.authors = ["Test Author"]
    book.metadata.language = "en"
    book.structure[0].content = [
        text_artifact(b"", fragment).structure[0].content[0] for fragment in fragments
    ]
    return path, book


@pytest.mark.parametrize("filename", ["export.epub", "export.zip", "export.bin"])
def test_json_zip_export_is_validated_by_contents(json_export, tmp_path, filename):
    path, book = json_export
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    assert load_storybook(output, path.read_bytes(), filename) == book


@pytest.mark.parametrize(
    "change", ["omit", "duplicate", "reorder", "rewrite", "markup"]
)
def test_json_zip_export_requires_exact_ordered_content(json_export, tmp_path, change):
    path, book = json_export
    content = book.structure[0].content
    if change == "omit":
        content.pop()
    elif change == "duplicate":
        content.append(content[-1])
    elif change == "reorder":
        content.reverse()
    else:
        raw = content[0].raw_text
        raw = (
            raw.replace("First", "Rewritten")
            if change == "rewrite"
            else raw.replace("<em>", "").replace("</em>", "")
        )
        content[0] = text_artifact(b"", raw).structure[0].content[0]
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    with pytest.raises(ValueError, match="Source content changed"):
        load_storybook(output, path.read_bytes(), path.name)


@pytest.mark.parametrize("field", ["title", "authors", "language"])
def test_json_zip_export_preserves_metadata(json_export, tmp_path, field):
    path, book = json_export
    setattr(book.metadata, field, ["Invented"] if field == "authors" else "Invented")
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    with pytest.raises(ValueError, match=f"Source metadata changed: {field}"):
        load_storybook(output, path.read_bytes(), path.name)


@pytest.mark.parametrize("filename", ["book.zip", "book.bin", "book.html"])
def test_epub_is_detected_without_epub_extension(ebook_path, tmp_path, filename):
    book = ebook_artifact(ebook_path)
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    assert load_storybook(output, ebook_path.read_bytes(), filename) == book
    book.structure[0].content.pop()
    output.write_text(book.model_dump_json())
    with pytest.raises(ValueError, match="Source content changed"):
        load_storybook(output, ebook_path.read_bytes(), filename)


@pytest.mark.parametrize("filename", ["export.epub", "export.zip"])
@pytest.mark.parametrize(
    "contents", ["{", '{"chapters": []}', '{"chapters": [{"content": 42}]}']
)
def test_malformed_json_export_cannot_bypass_validation(tmp_path, filename, contents):
    from zipfile import ZipFile

    path = tmp_path / filename
    with ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("book.json", contents)
    book = text_artifact(path.read_bytes(), "<p>Text.</p>")
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    with pytest.raises(ValueError):
        load_storybook(output, path.read_bytes(), path.name)


@pytest.mark.parametrize(
    "archive_kind", ["plain_text", "unrecognized_zip", "broken_epub"]
)
def test_invalid_epub_has_a_validation_error(tmp_path, archive_kind):
    from zipfile import ZipFile

    path = tmp_path / "broken.epub"
    if archive_kind == "plain_text":
        path.write_text("not an EPUB")
    else:
        with ZipFile(path, "w") as archive:
            if archive_kind == "broken_epub":
                archive.writestr("mimetype", "application/epub+zip")
            else:
                archive.writestr("content.json", "{}")
    book = text_artifact(path.read_bytes(), "<p>Text.</p>")
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    with pytest.raises(
        ValueError, match="neither an EPUB container|Unable to read EPUB"
    ):
        load_storybook(output, path.read_bytes(), path.name)


@pytest.mark.asyncio
async def test_coding_agent_returns_json_export_storybook(json_export):
    path, book = json_export
    provider = FakeProvider([artifact_stream(book), done_stream()])
    assert await EbookStructureAgent(provider=provider, model="fake").run(path) == book


@pytest.mark.asyncio
async def test_coding_agent_repairs_json_export_content(json_export):
    path, valid = json_export
    invalid = valid.model_copy(deep=True)
    invalid.structure[0].content.pop()
    provider = FakeProvider(
        [artifact_stream(invalid), done_stream(), artifact_stream(valid), done_stream()]
    )
    assert await EbookStructureAgent(provider=provider, model="fake").run(path) == valid
    assert len(set(provider.session_ids)) == 1


@pytest.mark.parametrize("book_title", ["Fallback Book Title", None])
def test_json_export_uses_book_and_manifest_metadata_fallbacks(
    json_export, tmp_path, book_title
):
    from zipfile import ZipFile

    path, book = json_export
    with ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    payload = json.loads(entries["book.json"])
    payload.pop("metadata")
    payload["title"] = book_title
    entries["book.json"] = json.dumps(payload).encode()
    with ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    book.id = sha256(path.read_bytes()).hexdigest()
    book.metadata.title = book_title or "Manifest Title"
    book.metadata.authors = ["Manifest Author"]
    book.metadata.language = None
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    assert load_storybook(output, path.read_bytes(), path.name) == book
