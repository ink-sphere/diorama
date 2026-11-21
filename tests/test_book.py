import pytest
from conftest import EDITIONS, as_book
from pydantic import ValidationError

from diorama.agent.tools import inspect_epub
from diorama.schemas.book import Book, Section, SourceRange


@pytest.mark.parametrize("edition", EDITIONS)
def test_source_trees_round_trip(epub_factory, edition):
    book = as_book(inspect_epub(epub_factory(edition)))
    assert Book.model_validate_json(book.model_dump_json()) == book

    def labels(node):
        if isinstance(node, Section):
            yield node.label
            for child in node.children:
                yield from labels(child)

    assert tuple(labels(book.root))[1:] == EDITIONS[edition].labels


@pytest.mark.parametrize("start,end", [(0, 0), (2, 1), (-1, 2)])
def test_invalid_intervals(start, end):
    with pytest.raises(ValidationError):
        SourceRange(occurrence=0, resource="a", start=start, end=end)
