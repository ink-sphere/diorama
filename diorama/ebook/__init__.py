"""Deterministic EPUB parsing and structure-tree slicing.

This package has no LLM/agent dependency: :mod:`~diorama.ebook.parser` flattens an
EPUB into numbered :class:`~diorama.ebook.models.Block` objects, and
:mod:`~diorama.ebook.slicer` deterministically turns a submitted tree of block-id
boundaries into an :class:`~diorama.ebook.models.EbookStructure`. The agent that
discovers those boundaries lives in
:mod:`diorama.agents.ebook_loader.EbookLoaderAgent`.
"""

from diorama.ebook.cover import extract_cover
from diorama.ebook.models import (
    Block,
    Coverage,
    EbookStructure,
    StructureNode,
    TocEntry,
)
from diorama.ebook.parser import EbookContext
from diorama.ebook.scenes import (
    BookScenes,
    Scene,
    SceneSegmentation,
    build_scenes,
    iter_leaves,
    split_paragraphs,
    validate_scenes,
)
from diorama.ebook.slicer import DEFAULT_SEGMENT_LENGTH, build_structure, validate_tree

__all__ = [
    "Block",
    "BookScenes",
    "Coverage",
    "EbookStructure",
    "Scene",
    "SceneSegmentation",
    "StructureNode",
    "TocEntry",
    "EbookContext",
    "DEFAULT_SEGMENT_LENGTH",
    "extract_cover",
    "build_scenes",
    "build_structure",
    "iter_leaves",
    "split_paragraphs",
    "validate_scenes",
    "validate_tree",
]
