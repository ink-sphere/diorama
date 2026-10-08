from __future__ import annotations

import json

import pytest

from diorama.models.storybook import (
    EbookMetadata,
    StoryBook,
    StructureNode,
    TextContent,
)


def leaf(
    raw: str, markdown: str = "", *, title: str = "", narrative: bool = False
) -> StructureNode:
    return StructureNode(
        structure_type="section",
        structure_title=title,
        is_part_of_narrative=narrative,
        content=[TextContent(raw_text=raw, markdown_text=markdown)],
    )


def group(
    children: list[StructureNode], *, title: str = "", narrative: bool = False
) -> StructureNode:
    return StructureNode(
        structure_type="part",
        structure_title=title,
        is_part_of_narrative=narrative,
        content=children,
    )


def book(nodes: list[StructureNode]) -> StoryBook:
    return StoryBook(
        id="source-id", metadata=EbookMetadata(title="A Book"), structure=nodes
    )


def text_at(node: StructureNode, index: int = 0) -> TextContent:
    content = node.content[index]
    assert isinstance(content, TextContent)
    return content


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " \n\t",
        '<div class="calibre3">\n\n</div><div class="calibre3">\n</div>',
        "<section><div><span>&nbsp;\u200b\ufeff</span></div></section>",
        "<!-- a comment -->",
        "<head><title>Metadata</title></head><script>ignored()</script><style>body{}</style>",
    ],
)
def test_post_init_removes_nodes_without_content(raw):
    result = book([leaf(raw, "\n", title="A title alone is not content")])
    assert result.structure == []
    assert result.metadata.title == "A Book"
    assert result.id == "source-id"


def test_cleanup_prunes_empty_groups_recursively_and_preserves_order():
    first = leaf("<p>First.</p>", "First.", title="First")
    last = leaf("<p>Last.</p>", "Last.", title="Last")
    nodes = [first, group([group([leaf("<div></div>")])], title="Empty branch"), last]
    result = book(nodes)
    assert [node.structure_title for node in result.structure] == ["First", "Last"]
    assert text_at(result.structure[0]).raw_text == "<p>First.</p>"
    assert text_at(result.structure[1]).raw_text == "<p>Last.</p>"


def test_group_flags_are_recomputed_after_pruning_without_mutating_caller_nodes():
    empty = leaf("<div></div>", narrative=True)
    supplement = leaf("<p>Notes.</p>", "Notes.", title="Notes")
    inner = group([empty, supplement], title="Inner", narrative=True)
    outer = group([inner], title="Outer", narrative=True)
    result = book([outer])
    cleaned_outer = result.structure[0]
    cleaned_inner = cleaned_outer.content[0]
    assert isinstance(cleaned_inner, StructureNode)
    assert not cleaned_inner.is_part_of_narrative
    assert not cleaned_outer.is_part_of_narrative
    assert [
        child.structure_title
        for child in cleaned_inner.content
        if isinstance(child, StructureNode)
    ] == ["Notes"]
    assert outer.is_part_of_narrative
    assert inner.is_part_of_narrative
    assert len(inner.content) == 2


def test_retained_narrative_child_sets_group_flag():
    result = book([group([leaf("<p>A story.</p>", "A story.", narrative=True)])])
    assert result.structure[0].is_part_of_narrative


@pytest.mark.parametrize(
    "raw",
    [
        '<img src="cover.jpg"/>',
        '<svg><image href="cover.jpg"/></svg>',
        '<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:image href="cover.jpg"/></s:svg>',
        "<table><tr><td></td></tr></table>",
        '<video src="scene.mp4"></video>',
        '<audio src="scene.mp3"></audio>',
        "<math><mi>x</mi></math>",
        "<hr/>",
        '<a id="footnote-1"></a>',
        '<a name="legacy-anchor"></a>',
        '<a href="#chapter-1"></a>',
        '<div id="chapter-1"></div>',
        '<div xml:id="chapter-1"></div>',
        '<div style="background-image: url(cover.jpg)"></div>',
    ],
)
def test_cleanup_preserves_headings_media_and_anchors(raw):
    result = book([leaf(raw)])
    assert len(result.structure) == 1
    assert text_at(result.structure[0]).raw_text == raw


def test_cleanup_preserves_markdown_content():
    result = book([leaf("", "A readable passage.")])
    assert text_at(result.structure[0]).markdown_text == "A readable passage."


def test_fragments_in_a_readable_node_are_preserved_verbatim():
    node = leaf("<div></div>")
    text = TextContent(raw_text="<p>Passage.</p>", markdown_text="Passage.")
    node.content = [text_at(node), text]
    result = book([node])
    assert result.structure[0].content == node.content
    assert len(result.structure[0].content) == 2


@pytest.mark.parametrize("method", ["constructor", "dict", "json"])
def test_normalization_applies_to_construction_and_deserialization(method):
    payload = {
        "id": "source-id",
        "metadata": {"title": "A Book"},
        "structure": [
            leaf("<div></div>").model_dump(),
            leaf("<p>Text.</p>", "Text.").model_dump(),
        ],
    }
    if method == "constructor":
        result = book([leaf("<div></div>"), leaf("<p>Text.</p>", "Text.")])
    elif method == "dict":
        result = StoryBook.model_validate(payload)
    else:
        result = StoryBook.model_validate_json(json.dumps(payload))
    assert len(result.structure) == 1
    assert text_at(result.structure[0]).raw_text == "<p>Text.</p>"
    reloaded = StoryBook.model_validate_json(result.model_dump_json())
    assert reloaded == result


def test_nodes_with_empty_arrays_are_removed():
    empty = StructureNode(
        structure_type="section",
        structure_title="Empty",
        is_part_of_narrative=False,
        content=[],
    )
    assert book([empty]).structure == []


@pytest.mark.parametrize(
    "raw,markdown",
    [
        ("<h2>ACT I</h2>", "## ACT I"),
        ('<h2><a id="act-i"></a><b>ACT I</b></h2>', "## **ACT I**"),
        (
            "<h1>Book title</h1><h2>By the author</h2><hr/>",
            "# Book title\n\n## By the author\n\n* * *",
        ),
        ('<x:h2 xmlns:x="http://www.w3.org/1999/xhtml">ACT I</x:h2>', "ACT I"),
        ("", "# Heading"),
        ("", "Heading\n=======\n"),
        ("# Heading", "# Heading"),
    ],
)
def test_pages_containing_only_headings_are_empty(raw, markdown):
    assert book([leaf(raw, markdown)]).structure == []


def test_parent_heading_page_is_folded_into_first_substantive_child():
    heading = leaf(
        '<h2><a id="act-i"></a>ACT I</h2>', "## ACT I", title="ACT I", narrative=True
    )
    scene = leaf(
        "<h3>SCENE I</h3><p>Dialogue.</p>",
        "### SCENE I\n\nDialogue.",
        title="SCENE I",
        narrative=True,
    )
    parent = group([heading, scene], title="ACT I", narrative=True)
    result = book([parent])
    assert len(result.structure) == 1
    children = result.structure[0].content
    assert len(children) == 1
    child = children[0]
    assert isinstance(child, StructureNode)
    assert child.structure_title == "SCENE I"
    assert [fragment.raw_text for fragment in child.content] == [
        text_at(heading).raw_text,
        text_at(scene).raw_text,
    ]
    assert result.structure[0].is_part_of_narrative
    assert len(parent.content) == 2
    assert len(scene.content) == 1
    assert StoryBook.model_validate_json(result.model_dump_json()) == result


def test_heading_only_groups_collapse_and_keep_markup_in_source_order():
    heading = leaf("<h1>Title</h1>", "# Title")
    removed = group([group([heading])], title="Title")
    body = leaf("<p>Body.</p>", "Body.", title="Body")
    result = book([removed, body])
    assert len(result.structure) == 1
    assert result.structure[0].structure_title == "Body"
    assert [fragment.raw_text for fragment in result.structure[0].content] == [
        "<h1>Title</h1>",
        "<p>Body.</p>",
    ]


def test_trailing_heading_page_is_folded_into_previous_content():
    body = leaf("<p>Body.</p>", "Body.")
    heading = leaf("<h3>Closing heading</h3>", "### Closing heading", narrative=True)
    result = book([group([body, heading], narrative=True)])
    assert len(result.structure[0].content) == 1
    assert not result.structure[0].is_part_of_narrative
    child = result.structure[0].content[0]
    assert isinstance(child, StructureNode)
    assert [fragment.raw_text for fragment in child.content] == [
        "<p>Body.</p>",
        "<h3>Closing heading</h3>",
    ]


def test_middle_heading_page_is_folded_into_next_content():
    result = book(
        [
            leaf("<p>First.</p>", "First."),
            leaf("<h2>Divider</h2>", "## Divider"),
            leaf("<p>Second.</p>", "Second."),
        ]
    )
    assert len(result.structure) == 2
    assert [fragment.raw_text for fragment in result.structure[1].content] == [
        "<h2>Divider</h2>",
        "<p>Second.</p>",
    ]


def test_heading_and_separate_body_fragments_are_not_empty():
    heading = leaf("<h2>Heading</h2>", "## Heading")
    heading.content = [
        text_at(heading),
        TextContent(raw_text="<p>Body.</p>", markdown_text="Body."),
    ]
    result = book([heading])
    assert len(result.structure) == 1
    assert result.structure[0].content == heading.content


@pytest.mark.parametrize(
    "raw",
    [
        '<h2>Heading</h2><img src="cover.jpg"/>',
        '<h2>Heading<img src="logo.jpg"/></h2>',
        "<p># This is literal paragraph text.</p>",
        "<pre># This is code.</pre>",
    ],
)
def test_headings_with_media_and_literal_heading_like_body_are_preserved(raw):
    result = book([leaf(raw)])
    assert len(result.structure) == 1
    assert text_at(result.structure[0]).raw_text == raw
