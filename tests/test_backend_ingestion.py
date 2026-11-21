import pytest

from diorama.agent.tools import InvalidEpub, inspect_epub, validate_hierarchy
from diorama.backend.ingestion import BookIngestion


async def test_reopen_repeat_and_changed_edition(harness_factory, epub_factory):
    harness, calls = harness_factory()
    service = BookIngestion(harness)
    source = epub_factory()
    old = await service.add_book("test", source)
    assert await BookIngestion(harness).add_book("test", source) == old
    assert len(calls) == 1
    source.write_bytes(epub_factory("play").read_bytes())
    new = await service.add_book("test", source)
    assert new.manifest_path != old.manifest_path
    source.write_bytes(b"broken")
    with pytest.raises(InvalidEpub):
        await service.add_book("test", source)
    for result in (old, new):
        validate_hierarchy(inspect_epub(result.source_epub), result.open_book())
    assert len(calls) == 2
