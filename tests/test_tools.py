from zipfile import ZipFile

import pytest
from conftest import EDITIONS, as_book

from diorama.agent.tools import (
    InvalidCandidate,
    InvalidEpub,
    inspect_epub,
    read_passage,
    validate_hierarchy,
)
from diorama.schemas.book import Passage


@pytest.mark.parametrize("edition", EDITIONS)
def test_exact_reading_stream_and_ranges(epub_factory, edition):
    index = inspect_epub(epub_factory(edition))
    spec = EDITIONS[edition]
    assert tuple(doc.text for doc in index.spine if doc.linear) == spec.streams
    assert index.navigation[0].title == spec.toc
    assert index.spine[0].links == [f"p{len(spec.documents)}.xhtml#note"]
    assert not index.spine[-1].linear
    book = as_book(index)
    validate_hierarchy(index, book)

    def text(node):
        if isinstance(node, Passage):
            return read_passage(index, node.source)
        return "".join(text(child) for child in node.children)

    assert text(book.root) == "".join(spec.streams)
    first = index.spine[0]
    assert first.positions[0].start == 0
    assert first.positions[-1].end == len(first.text)
    assert first.headings[0].offset == 0


@pytest.mark.parametrize(
    "error",
    ["gap", "overlap", "reorder", "foreign", "digest", "duplicate", "extra", "missing"],
)
def test_rejects_invalid_candidate(epub_factory, error):
    index = inspect_epub(epub_factory())
    book = as_book(index)
    passages = book.root.children[0].children
    ref = passages[0].source
    if error == "gap":
        ref.end -= len("Echo.\n")
    elif error == "overlap":
        passages.insert(1, passages[0].model_copy(update={"id": "overlap"}))
    elif error == "reorder":
        passages.reverse()
    elif error == "foreign":
        ref.resource = "elsewhere.xhtml"
    elif error == "digest":
        book.source_sha256 = "0" * 64
    elif error == "duplicate":
        passages[0].id = book.root.id
    elif error == "extra":
        passages.append(passages[0].model_copy(update={"id": "extra"}))
    else:
        passages.pop()
    with pytest.raises(InvalidCandidate):
        validate_hierarchy(index, book)


def test_archive_paths_cannot_escape(epub_factory):
    path = epub_factory()
    with ZipFile(path, "a") as archive:
        archive.writestr("../outside", "unused")
    with pytest.raises(InvalidEpub, match="unsafe"):
        inspect_epub(path)


def test_position_map_resolves_xhtml_text_and_tails(epub_factory):
    from lxml import etree

    path = epub_factory("play")
    doc = inspect_epub(path).spine[0]
    with ZipFile(path) as archive:
        xhtml = etree.fromstring(archive.read(doc.resource))
    for position in doc.positions:
        (element,) = xhtml.xpath(position.xpath)
        expected = "\n" if position.slot == "break" else getattr(element, position.slot)
        assert doc.text[position.start : position.end] == expected


def test_epub2_navigation_repeated_spine_and_front_matter(tmp_path, epub_factory):
    source = epub_factory()
    path = tmp_path / "legacy.epub"
    ncx = '<ncx><navMap><navPoint><navLabel><text>Part</text></navLabel><content src="p0.xhtml"/><navPoint><navLabel><text>One</text></navLabel><content src="p0.xhtml#c"/></navPoint></navPoint></navMap></ncx>'
    with ZipFile(source) as original, ZipFile(path, "w") as changed:
        for name in original.namelist():
            data = original.read(name)
            if name == "OPS/book.opf":
                data = (
                    data.replace(b'properties="nav"', b"")
                    .replace(b"<spine>", b'<spine toc="ncx"><itemref idref="i0"/>')
                    .replace(
                        b"</manifest>",
                        b'<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest>',
                    )
                )
            if name == "OPS/p0.xhtml":
                data = data.replace(b"<body>", b"<body><p>Preface.</p>")
            changed.writestr(name, data)
        changed.writestr("OPS/toc.ncx", ncx)
    index = inspect_epub(path)
    assert [(n.title, n.depth, n.target) for n in index.navigation] == [
        ("Part", 0, "OPS/p0.xhtml"),
        ("One", 1, "OPS/p0.xhtml#c"),
    ]
    assert index.spine[1].text.startswith("Preface.\n")
    assert index.spine[0].resource == index.spine[1].resource
    assert index.spine[0].occurrence != index.spine[1].occurrence
