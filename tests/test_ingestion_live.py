import pytest
from conftest import EDITIONS

from diorama.agent.tools import inspect_epub, read_passage, validate_hierarchy
from diorama.backend.ingestion import BookIngestion
from diorama.schemas.book import Section


@pytest.mark.geppetto_live
@pytest.mark.parametrize("edition", EDITIONS)
async def test_edition_hierarchy(tmp_path, epub_factory, edition):
    service = BookIngestion.local(tmp_path / "data")
    source = epub_factory(edition)
    result = await service.add_book("live", source)
    book = result.open_book()
    index = inspect_epub(source)
    validate_hierarchy(index, book)

    def sections(node, path=()):
        if isinstance(node, Section):
            path = (*path, node.label.casefold())
            yield path, node
            for child in node.children:
                yield from sections(child, path)

    def section_text(node):
        if isinstance(node, Section):
            return "".join(section_text(child) for child in node.children)
        return read_passage(index, node.source)

    nodes = list(sections(book.root))
    for label in set(EDITIONS[edition].labels):
        matching = [n for path, n in nodes if path[-1] == label]
        assert len(matching) == EDITIONS[edition].labels.count(label)
        if label == "story":
            assert all(n.is_self_contained for n in matching)
            assert tuple(map(section_text, matching)) == EDITIONS[edition].streams
        elif label == "chapter":
            assert section_text(matching[0]) == "".join(EDITIONS[edition].streams)
        elif label == "scene":
            assert all("act" in path[:-1] for path, _ in nodes if path[-1] == label)
            assert "Hello, friend!" in section_text(matching[0])
            assert "Exit." in section_text(matching[1])
        elif label == "śloka":
            assert all(
                path[-3:] == ("kāṇḍa", "sarga", "śloka")
                for path, _ in nodes
                if path[-1] == label
            )
            assert "e\u0301" in section_text(matching[0])
            assert "त" in section_text(matching[1])
    assert await service.add_book("live", source) == result
