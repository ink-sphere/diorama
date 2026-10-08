from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from bs4 import BeautifulSoup
from bs4.element import Comment, Doctype, NavigableString, ProcessingInstruction, Tag

_SERIALIZATION_NAMESPACES = {
    "http://www.w3.org/1999/xhtml",
    "http://www.idpf.org/2007/ops",
}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def markup_tokens(fragments: Iterable[str]) -> Iterator[tuple]:
    containers = {
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

    def visit(element) -> Iterator[tuple]:
        if isinstance(element, (Comment, Doctype, ProcessingInstruction)):
            return
        if isinstance(element, NavigableString):
            text = normalize(str(element))
            if text:
                yield ("text", text)
        elif isinstance(element, Tag):
            name = element.name.rsplit(":", 1)[-1].lower()
            if name in {"head", "script", "style"}:
                return
            if name not in containers:
                attrs = tuple(
                    sorted(
                        (key, tuple(value) if isinstance(value, list) else value)
                        for key, value in element.attrs.items()
                        if not (
                            isinstance(value, str)
                            and (
                                (
                                    key == "xmlns"
                                    and value == "http://www.w3.org/1999/xhtml"
                                )
                                or (
                                    key.startswith("xmlns:")
                                    and value in _SERIALIZATION_NAMESPACES
                                )
                            )
                        )
                    )
                )
                yield ("open", name, attrs)
            for child in element.children:
                yield from visit(child)
            if name not in containers:
                yield ("close", name)

    for fragment in fragments:
        for element in BeautifulSoup(fragment, "html.parser").contents:
            yield from visit(element)
