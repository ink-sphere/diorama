from __future__ import annotations

import re
from unicodedata import category

from bs4 import BeautifulSoup

from diorama.models.storybook import StructureNode, TextContent

_BODY_MEDIA = {
    "img",
    "svg",
    "image",
    "picture",
    "video",
    "audio",
    "canvas",
    "table",
    "math",
    "iframe",
    "object",
    "embed",
}


def has_text(text: str) -> bool:
    return any(
        not character.isspace() and category(character) not in {"Cc", "Cf"}
        for character in text
    )


def markdown_body(markdown: str) -> str:
    markdown = re.sub(r"(?m)^[ \t]{0,3}#{1,6}(?:[ \t]+|$).*", "", markdown)
    return re.sub(r"(?m)^[^\n]+\n[ \t]*[=-]{2,}[ \t]*(?:\n|$)", "", markdown)


def has_content(content: TextContent) -> bool:
    soup = BeautifulSoup(content.raw_text, "html.parser")
    for element in soup.find_all(["head", "script", "style", "template"]):
        element.decompose()
    headings = soup.find_all(
        lambda tag: re.fullmatch(r"h[1-6]", tag.name.rsplit(":", 1)[-1]) is not None
    )
    elements = soup.find_all(True)
    if any(element.name.rsplit(":", 1)[-1] in _BODY_MEDIA for element in elements):
        return True
    if any(
        "background" in str(element.get("style", "")).lower()
        and "url(" in str(element.get("style", "")).lower()
        for element in elements
    ):
        return True
    if headings:
        for heading in headings:
            heading.decompose()
        return has_text(soup.get_text(" ", strip=True))
    if has_text(markdown_body(content.markdown_text)):
        return True
    text = soup.get_text(" ", strip=True)
    if has_text(text if elements else markdown_body(text)):
        return True
    return any(
        element.name.rsplit(":", 1)[-1] == "hr"
        or element.get("id")
        or element.get("xml:id")
        or (
            element.name.rsplit(":", 1)[-1] == "a"
            and (element.get("name") or element.get("href"))
        )
        for element in elements
    )


def _with_fragments(
    node: StructureNode, fragments: list[TextContent], *, first: bool
) -> StructureNode:
    children = [item for item in node.content if isinstance(item, StructureNode)]
    if children:
        index = 0 if first else len(children) - 1
        children[index] = _with_fragments(children[index], fragments, first=first)
        return node.model_copy(update={"content": children})
    text = [item for item in node.content if isinstance(item, TextContent)]
    return node.model_copy(
        update={"content": [*fragments, *text] if first else [*text, *fragments]}
    )


def _needs_preservation(content: TextContent) -> bool:
    soup = BeautifulSoup(content.raw_text, "html.parser")
    if soup.get_text(" ", strip=True).strip():
        return True
    layout = {
        "html",
        "body",
        "section",
        "div",
        "article",
        "aside",
        "main",
        "header",
        "footer",
    }
    return any(
        element.name.rsplit(":", 1)[-1] not in layout for element in soup.find_all(True)
    )


def normalize_nodes(
    nodes: list[StructureNode],
) -> tuple[list[StructureNode], list[TextContent]]:
    normalized: list[StructureNode] = []
    pending: list[TextContent] = []
    for node in nodes:
        children = [item for item in node.content if isinstance(item, StructureNode)]
        if children:
            children, trailing = normalize_nodes(children)
            if not children:
                pending.extend(trailing)
                continue
            if trailing:
                children[-1] = _with_fragments(children[-1], trailing, first=False)
            kept = node.model_copy(
                update={
                    "content": children,
                    "is_part_of_narrative": any(
                        child.is_part_of_narrative for child in children
                    ),
                }
            )
        else:
            text = [item for item in node.content if isinstance(item, TextContent)]
            combined = TextContent(
                raw_text="\n".join(item.raw_text for item in text),
                markdown_text="\n".join(item.markdown_text for item in text),
            )
            if not has_content(combined):
                pending.extend(
                    item
                    for item in text
                    if item.raw_text.strip() and _needs_preservation(item)
                )
                continue
            kept = node
        if pending:
            kept = _with_fragments(kept, pending, first=True)
            pending = []
        normalized.append(kept)
    return normalized, pending


def normalize_structure(nodes: list[StructureNode]) -> list[StructureNode]:
    normalized, trailing = normalize_nodes(nodes)
    if normalized and trailing:
        normalized[-1] = _with_fragments(normalized[-1], trailing, first=False)
    return normalized
