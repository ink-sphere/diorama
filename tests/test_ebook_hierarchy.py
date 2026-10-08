from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import html2text
import pytest
from ebooklib import epub
from tau_agent import AssistantMessage, ToolCall
from tau_agent.messages import TextContent as AssistantTextContent
from tau_ai import AssistantDoneEvent, FakeProvider

from diorama.agents.ebook_structure_agent import (
    EbookStructureAgent,
    EbookStructureError,
    load_storybook,
    parse_ebook,
)
from diorama.agents.ebook_structure_agent.prompts import build_system_prompt
from diorama.models.storybook import (
    EbookMetadata,
    StoryBook,
    StructureNode,
    TextContent,
)


def fragment(raw: str) -> TextContent:
    converter = html2text.HTML2Text()
    converter.body_width = 0
    return TextContent(raw_text=raw, markdown_text=converter.handle(raw).strip())


def node(
    kind: str, title: str, content: list[TextContent] | list[StructureNode]
) -> StructureNode:
    return StructureNode(
        structure_type=kind,
        structure_title=title,
        is_part_of_narrative=True,
        content=content,
    )


def write_epub(path: Path, documents: list[str], toc) -> Path:
    book = epub.EpubBook()
    book.set_identifier("hierarchy-fixture")
    book.set_title("A Play")
    book.set_language("en")
    items = []
    for index, content in enumerate(documents):
        item = epub.EpubHtml(
            uid=f"doc{index}",
            file_name=f"doc{index}.xhtml",
            content=f'<main><div class="chapter"><section>{content}</section></div></main>',
        )
        book.add_item(item)
        items.append(item)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    book.spine = ["nav", *items]
    book.toc = toc
    epub.write_epub(path, book)
    return path


@dataclass
class Play:
    path: Path
    valid: StoryBook
    flat: StoryBook


@pytest.fixture
def play(tmp_path) -> Play:
    path = write_epub(
        tmp_path / "play.epub",
        [
            '<h2 id="act1">ACT I</h2><h3 id="scene1">SCENE I. An orchard</h3><p>Enter Adam.</p>'
            '<h3 id="scene2">SCENE II. The palace</h3><p>Enter Rosalind.</p>',
            '<h2 id="act2">ACT II</h2><h3 id="scene3">SCENE I. The forest</h3><p>Enter the Duke.</p>',
        ],
        [
            (
                epub.Link("doc0.xhtml#act1", "ACT I", "act1"),
                [
                    epub.Link("doc0.xhtml#scene1", "SCENE I. An orchard", "scene1"),
                    epub.Link("doc0.xhtml#scene2", "SCENE II. The palace", "scene2"),
                ],
            ),
            (
                epub.Link("doc1.xhtml#act2", "ACT II", "act2"),
                [
                    epub.Link("doc1.xhtml#scene3", "SCENE I. The forest", "scene3"),
                ],
            ),
        ],
    )
    source = parse_ebook(path)
    blocks = [block.raw_text for block in source.blocks]
    valid = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node(
                "act",
                "ACT I",
                [
                    node("section", "", [fragment(blocks[0])]),
                    node(
                        "scene", "SCENE I. An orchard", [fragment("".join(blocks[1:3]))]
                    ),
                    node(
                        "scene",
                        "SCENE II. The palace",
                        [fragment("".join(blocks[3:5]))],
                    ),
                ],
            ),
            node(
                "act",
                "ACT II",
                [
                    node("section", "", [fragment(blocks[5])]),
                    node(
                        "scene", "SCENE I. The forest", [fragment("".join(blocks[6:8]))]
                    ),
                ],
            ),
        ],
    )
    flat = valid.model_copy(deep=True)
    flat.structure = [
        node("act", "ACT I", [fragment("".join(blocks[:5]))]),
        node("act", "ACT II", [fragment("".join(blocks[5:]))]),
    ]
    return Play(path, valid, flat)


def validate(book: StoryBook, path: Path, tmp_path: Path) -> StoryBook:
    output = tmp_path / "storybook.json"
    output.write_text(book.model_dump_json())
    return load_storybook(output, path.read_bytes(), path.name)


def test_accepts_act_scene_tree_with_source_content_preserved(play, tmp_path):
    assert validate(play.valid, play.path, tmp_path) == play.valid


def test_rejects_complete_text_flattened_into_act_leaves(play, tmp_path):
    with pytest.raises(ValueError, match="Source hierarchy.*SCENE I.*ACT I"):
        validate(play.flat, play.path, tmp_path)


def test_rejects_scene_siblings_outside_their_acts(play, tmp_path):
    book = play.valid.model_copy(deep=True)
    flattened = []
    for act in book.structure:
        flattened.append(node("act", act.structure_title, act.content[0].content))
        flattened.extend(act.content[1:])
    book.structure = flattened
    with pytest.raises(ValueError, match="Source hierarchy"):
        validate(book, play.path, tmp_path)


def test_rejects_scene_nested_in_its_previous_sibling(play, tmp_path):
    book = play.valid.model_copy(deep=True)
    act = book.structure[0]
    first, second = act.content
    first.content = [node("section", "", first.content), second]
    act.content.pop()
    with pytest.raises(ValueError, match="must be a child of 'ACT I'"):
        validate(book, play.path, tmp_path)


def test_rejects_correct_titles_with_wrong_semantic_types(play, tmp_path):
    book = play.valid.model_copy(deep=True)
    book.structure[0].content[1].structure_type = "chapter"
    with pytest.raises(ValueError, match="structure_type='scene'"):
        validate(book, play.path, tmp_path)


def test_nested_toc_can_override_equal_heading_levels_across_documents(tmp_path):
    path = write_epub(
        tmp_path / "nested.epub",
        [
            '<h1 id="volume">The Journey</h1><p>Opening.</p>',
            '<h1 id="chapter">A New Shore</h1><p>Arrival.</p>',
            '<h1 id="section">The Harbour</h1><p>A boat.</p>',
        ],
        [
            (
                epub.Link("doc0.xhtml#volume", "The Journey", "volume"),
                [
                    (
                        epub.Link("doc1.xhtml#chapter", "A New Shore", "chapter"),
                        [epub.Link("doc2.xhtml#section", "The Harbour", "section")],
                    ),
                ],
            )
        ],
    )
    source = parse_ebook(path)
    blocks = [block.raw_text for block in source.blocks]
    book = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node(
                "part",
                "The Journey",
                [
                    node("section", "", [fragment("".join(blocks[:2]))]),
                    node(
                        "chapter",
                        "A New Shore",
                        [
                            node("section", "", [fragment("".join(blocks[2:4]))]),
                            node(
                                "section",
                                "The Harbour",
                                [fragment("".join(blocks[4:]))],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )
    assert validate(book, path, tmp_path) == book
    book.structure = [
        node("part", "The Journey", [fragment("".join(blocks[:2]))]),
        node("chapter", "A New Shore", [fragment("".join(blocks[2:4]))]),
        node("section", "The Harbour", [fragment("".join(blocks[4:]))]),
    ]
    with pytest.raises(ValueError, match="Source hierarchy.*must be a child"):
        validate(book, path, tmp_path)


@pytest.mark.parametrize("kind", ["html", "json_zip"])
def test_internal_hierarchy_is_checked_in_other_markup_formats(play, tmp_path, kind):
    import json
    from zipfile import ZipFile

    source = parse_ebook(play.path)
    raw = "".join(block.raw_text for block in source.blocks)
    if kind == "html":
        path = tmp_path / "play.html"
        path.write_text(raw)
    else:
        path = tmp_path / "play.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", '{"title":"A Play"}')
            archive.writestr("book.json", json.dumps({"chapters": [{"content": raw}]}))
    for original, valid in [(play.flat, False), (play.valid, True)]:
        book = original.model_copy(deep=True)
        book.id = sha256(path.read_bytes()).hexdigest()
        if valid:
            assert validate(book, path, tmp_path) == book
        else:
            with pytest.raises(ValueError, match="Source hierarchy"):
                validate(book, path, tmp_path)


def test_decorative_and_quoted_headings_do_not_force_structure(tmp_path):
    raw = "<h1>A decorative title</h1><h3>A decorative subtitle</h3><blockquote><h2>ACT I</h2><p>A quotation.</p></blockquote><p>A story.</p>"
    path = tmp_path / "book.html"
    path.write_text(raw)
    book = StoryBook(
        id=sha256(path.read_bytes()).hexdigest(),
        metadata=EbookMetadata(title="A Book"),
        structure=[node("chapter", "", [fragment(raw)])],
    )
    assert validate(book, path, tmp_path) == book


def artifact_response(book: StoryBook):
    return [
        AssistantDoneEvent(
            reason="toolUse",
            message=AssistantMessage(
                content=[
                    ToolCall(
                        id="write",
                        name="write",
                        arguments={
                            "path": "output/storybook.json",
                            "content": book.model_dump_json(),
                        },
                    )
                ],
                stop_reason="toolUse",
            ),
        )
    ]


def done_response():
    return [
        AssistantDoneEvent(
            reason="stop",
            message=AssistantMessage(content=[AssistantTextContent(text="Done")]),
        )
    ]


async def test_agent_repairs_flat_hierarchy_before_saving(play, tmp_path):
    provider = FakeProvider(
        [
            artifact_response(play.flat),
            done_response(),
            artifact_response(play.valid),
            done_response(),
        ]
    )
    events = []
    root = tmp_path / "runs"
    agent = EbookStructureAgent(
        provider=provider, model="fake", workspace_root=root, callbacks=[events.append]
    )
    assert await agent.run(play.path) == play.valid
    assert len(set(provider.session_ids)) == 1
    diagnostics = [
        event.event.details["error"]
        for event in events
        if event.event.type == "validation_failed"
    ]
    assert len(diagnostics) == 1 and "SCENE I" in diagnostics[0]
    assert any(
        "Source hierarchy" in getattr(message, "text", "")
        for message in provider.calls[2][2]
    )
    saved = list(root.glob("*/storybook.json"))
    assert len(saved) == 1
    assert StoryBook.model_validate_json(saved[0].read_bytes()) == play.valid
    assert [item.name for item in saved[0].parent.iterdir()] == ["storybook.json"]


async def test_flattened_hierarchy_cannot_be_published(play, tmp_path):
    root = tmp_path / "runs"
    agent = EbookStructureAgent(
        provider=FakeProvider([artifact_response(play.flat), done_response()]),
        model="fake",
        workspace_root=root,
        max_repair_attempts=0,
    )
    with pytest.raises(EbookStructureError, match="Source hierarchy"):
        await agent.run(play.path)
    assert not root.exists()


def test_prompt_requires_outline_and_tree_audit():
    prompt = build_system_prompt()
    assert "work/outline.json" in prompt
    assert "act node with" in prompt
    assert "Audit every outlined boundary" in prompt
    assert "reference/ebook_tools.py" not in prompt


def test_identical_scene_titles_in_different_acts_are_distinct_nodes(tmp_path):
    path = write_epub(
        tmp_path / "repeated.epub",
        [
            '<h2 id="act">ACT I</h2><h3 id="scene">SCENE I. The forest</h3><p>First scene.</p>',
            '<h2 id="act">ACT II</h2><h3 id="scene">SCENE I. The forest</h3><p>Another scene.</p>',
        ],
        [
            (
                epub.Link("doc0.xhtml#act", "ACT I", "act1"),
                [epub.Link("doc0.xhtml#scene", "SCENE I. The forest", "scene1")],
            ),
            (
                epub.Link("doc1.xhtml#act", "ACT II", "act2"),
                [epub.Link("doc1.xhtml#scene", "SCENE I. The forest", "scene2")],
            ),
        ],
    )
    source = parse_ebook(path)
    structure = []
    for document in source.documents:
        blocks = source.blocks[document.start : document.stop]
        structure.append(
            node(
                "act",
                blocks[0].text,
                [
                    node("section", "", [fragment(blocks[0].raw_text)]),
                    node(
                        "scene",
                        blocks[1].text,
                        [fragment("".join(block.raw_text for block in blocks[1:]))],
                    ),
                ],
            )
        )
    book = StoryBook(id=source.id, metadata=source.metadata, structure=structure)
    assert validate(book, path, tmp_path) == book
    scene = book.structure[1].content[0]
    assert isinstance(scene, StructureNode)
    scene.structure_title = "SCENE II. The forest"
    with pytest.raises(ValueError, match="SCENE I.*ACT II"):
        validate(book, path, tmp_path)


def test_heading_levels_restart_in_separate_documents_without_nested_toc(tmp_path):
    path = write_epub(
        tmp_path / "chapters.epub",
        [
            "<h1>Chapter One</h1><p>The first chapter.</p>",
            "<h2>Chapter Two</h2><p>The second chapter.</p>",
        ],
        [],
    )
    source = parse_ebook(path)
    book = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node(
                "chapter",
                source.blocks[document.start].text,
                [
                    fragment(
                        "".join(
                            block.raw_text
                            for block in source.blocks[document.start : document.stop]
                        )
                    )
                ],
            )
            for document in source.documents
        ],
    )
    assert validate(book, path, tmp_path) == book


def test_flat_toc_does_not_erase_explicit_act_scene_nesting(tmp_path):
    path = write_epub(
        tmp_path / "flat-nav.epub",
        [
            '<h2 id="act">ACT I</h2><h3 id="scene">SCENE I. The forest</h3><p>A scene.</p>',
        ],
        [
            epub.Link("doc0.xhtml#act", "ACT I", "act"),
            epub.Link("doc0.xhtml#scene", "SCENE I. The forest", "scene"),
        ],
    )
    source = parse_ebook(path)
    book = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node("act", "ACT I", [fragment(source.blocks[0].raw_text)]),
            node(
                "scene",
                "SCENE I. The forest",
                [fragment("".join(block.raw_text for block in source.blocks[1:]))],
            ),
        ],
    )
    with pytest.raises(ValueError, match="ACT I.*own act node"):
        validate(book, path, tmp_path)


def test_duplicate_navigation_alias_does_not_create_a_self_parent(tmp_path):
    path = write_epub(
        tmp_path / "aliases.epub",
        [
            '<h2 id="act">ACT I</h2><h3 id="scene">SCENE I. The forest</h3><p>A scene.</p>',
        ],
        [
            (
                epub.Link("doc0.xhtml#act", "ACT I", "outer"),
                [
                    (
                        epub.Link("doc0.xhtml#act", "ACT I", "alias"),
                        [epub.Link("doc0.xhtml#scene", "SCENE I. The forest", "scene")],
                    ),
                ],
            ),
        ],
    )
    source = parse_ebook(path)
    book = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node(
                "act",
                "ACT I",
                [
                    node("section", "", [fragment(source.blocks[0].raw_text)]),
                    node(
                        "scene",
                        "SCENE I. The forest",
                        [
                            fragment(
                                "".join(block.raw_text for block in source.blocks[1:])
                            )
                        ],
                    ),
                ],
            )
        ],
    )
    assert validate(book, path, tmp_path) == book


def test_scene_body_cannot_be_detached_from_its_correctly_nested_heading(tmp_path):
    path = write_epub(
        tmp_path / "detached.epub",
        [
            "<h2>ACT I</h2><h3>SCENE I. The forest</h3><p>Opening.</p><p>Continuation.</p>"
        ],
        [],
    )
    source = parse_ebook(path)
    blocks = source.blocks
    book = StoryBook(
        id=source.id,
        metadata=source.metadata,
        structure=[
            node(
                "act",
                "ACT I",
                [
                    node(
                        "scene",
                        "SCENE I. The forest",
                        [fragment("".join(block.raw_text for block in blocks[:3]))],
                    ),
                    node("section", "", [fragment(blocks[3].raw_text)]),
                ],
            )
        ],
    )
    with pytest.raises(
        ValueError, match="content following .*SCENE I.*inside its structure node"
    ):
        validate(book, path, tmp_path)


def test_namespaced_structural_headings_are_supported(tmp_path):
    raw = "<x:h2>ACT I</x:h2><x:h3>SCENE I. The forest</x:h3><p>A scene.</p>"
    path = tmp_path / "namespaced.html"
    path.write_text(raw)
    book = StoryBook(
        id=sha256(path.read_bytes()).hexdigest(),
        metadata=EbookMetadata(title="A Play"),
        structure=[
            node(
                "act",
                "ACT I",
                [
                    node("section", "", [fragment("<x:h2>ACT I</x:h2>")]),
                    node(
                        "scene",
                        "SCENE I. The forest",
                        [fragment("<x:h3>SCENE I. The forest</x:h3><p>A scene.</p>")],
                    ),
                ],
            ),
        ],
    )
    assert validate(book, path, tmp_path) == book


async def test_agent_accepts_xml_namespace_declarations_without_repair(play):
    from lxml import etree

    from diorama.agents.ebook_structure_agent.validation import iter_text_content

    book = play.valid.model_copy(deep=True)
    converter = html2text.HTML2Text()
    converter.body_width = 0
    for content in iter_text_content(book.structure):
        root = etree.fromstring(
            (
                '<root xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                + content.raw_text
                + "</root>"
            ).encode()
        )
        content.raw_text = "".join(
            etree.tostring(child, encoding="unicode", with_tail=False) for child in root
        )
        content.markdown_text = converter.handle(content.raw_text).strip()
    events = []
    provider = FakeProvider([artifact_response(book), done_response()])
    agent = EbookStructureAgent(
        provider=provider, model="fake", callbacks=[events.append]
    )
    assert await agent.run(play.path) == book
    assert not any(event.event.type == "validation_failed" for event in events)
    assert len(provider.calls) == 2


async def test_source_mismatch_repair_includes_expected_actual_and_location(play):
    invalid = play.valid.model_copy(deep=True)
    intro = invalid.structure[0].content[0]
    assert isinstance(intro, StructureNode)
    content = intro.content[0]
    assert isinstance(content, TextContent)
    content.raw_text = content.raw_text.replace('id="act1"', 'id="broken-act"')
    provider = FakeProvider(
        [
            artifact_response(invalid),
            done_response(),
            artifact_response(play.valid),
            done_response(),
        ]
    )
    agent = EbookStructureAgent(provider=provider, model="fake")
    assert await agent.run(play.path) == play.valid
    assert any(
        "Expected doc0.xhtml (block b000001)" in getattr(message, "text", "")
        and "actual text fragment 1" in getattr(message, "text", "")
        and "broken-act" in getattr(message, "text", "")
        for message in provider.calls[2][2]
    )


def test_nested_body_wrappers_are_traversed_by_source_inventory(play):
    from bs4 import BeautifulSoup

    source = parse_ebook(play.path)
    for document in source.documents:
        body = BeautifulSoup(document.raw_content, "html.parser").body
        assert body is not None
        assert not body.find_all(["h2", "h3"], recursive=False)
        assert body.find_all(["h2", "h3"])
    assert [block.text for block in source.blocks if block.heading_level] == [
        "ACT I",
        "SCENE I. An orchard",
        "SCENE II. The palace",
        "ACT II",
        "SCENE I. The forest",
    ]


async def test_coding_script_uses_installed_inventory_and_preflight_outside_checkout(
    play, tmp_path, monkeypatch
):
    import shlex
    import sys

    caller = tmp_path / "independent application"
    caller.mkdir()
    monkeypatch.chdir(caller)
    script = """import json
from dataclasses import asdict
from pathlib import Path
import html2text
from diorama.agents.ebook_structure_agent.source import parse_ebook
from diorama.agents.ebook_structure_agent.validation import load_storybook
from diorama.models.storybook import StoryBook, StructureNode, TextContent
source = parse_ebook('input/play.epub')
Path('work/inventory.json').write_text(json.dumps({'metadata': source.metadata.model_dump(mode='json'), 'blocks': [asdict(block) for block in source.blocks], 'toc': [asdict(entry) for entry in source.toc]}))
roots = []
for document in source.documents:
    stack = []
    for block in source.blocks[document.start:document.stop]:
        if block.heading_level:
            while stack and stack[-1][0] >= block.heading_level:
                stack.pop()
            kind = block.text.split()[0].lower()
            item = {'title': block.text, 'kind': kind, 'blocks': [], 'children': []}
            (stack[-1][1]['children'] if stack else roots).append(item)
            stack.append((block.heading_level, item))
        stack[-1][1]['blocks'].append(block)
converter = html2text.HTML2Text()
converter.body_width = 0
def build(item):
    text = [TextContent(raw_text=block.raw_text, markdown_text=converter.handle(block.raw_text).strip()) for block in item['blocks']]
    children = [build(child) for child in item['children']]
    if children and text:
        children.insert(0, StructureNode(structure_type='section', structure_title='', is_part_of_narrative=True, content=text))
    return StructureNode(structure_type=item['kind'], structure_title=item['title'], is_part_of_narrative=True, content=children or text)
book = StoryBook(id=source.id, metadata=source.metadata, structure=[build(item) for item in roots])
output = Path('output/storybook.json')
output.write_text(book.model_dump_json())
load_storybook(output, Path('input/play.epub').read_bytes(), 'play.epub')
print('Source fidelity and hierarchy preflight passed')
"""
    provider = FakeProvider(
        [
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="write-script",
                                name="write",
                                arguments={
                                    "path": "work/extract.py",
                                    "content": script,
                                },
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            ],
            [
                AssistantDoneEvent(
                    reason="toolUse",
                    message=AssistantMessage(
                        content=[
                            ToolCall(
                                id="execute",
                                name="bash",
                                arguments={
                                    "command": f"{shlex.quote(sys.executable)} work/extract.py"
                                },
                            )
                        ],
                        stop_reason="toolUse",
                    ),
                )
            ],
            done_response(),
        ]
    )
    root = caller / ".ebook-runs"
    book = await EbookStructureAgent(
        provider=provider, model="fake", workspace_root=root
    ).run(play.path)
    assert [act.structure_type for act in book.structure] == ["act", "act"]
    assert [scene.structure_type for scene in book.structure[0].content] == [
        "scene",
        "scene",
    ]
    assert Path.cwd() == caller
    assert len(list(root.glob("*/storybook.json"))) == 1
    prompt = provider.calls[0][1]
    assert (
        "from diorama.agents.ebook_structure_agent.source import parse_ebook" in prompt
    )
    assert "Run this preflight through bash after every regeneration" in prompt
