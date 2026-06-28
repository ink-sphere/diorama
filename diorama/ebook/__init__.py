"""Ebook ingestion: extract an EPUB's hierarchical structure with a ReAct agent.

See ``docs/ebook-loader-agent.md`` for the design.
"""

from diorama.ebook.context import EbookContext
from diorama.ebook.ebook_loader_agent import (
    DEFAULT_MODEL_ID,
    EbookLoaderAgent,
    EbookLoadError,
)
from diorama.ebook.models import (
    Block,
    CoverageReport,
    EbookStructure,
    StructureNode,
    TocEntry,
)
from diorama.ebook.slicer import build_structure, validate_tree
from diorama.ebook.tools import build_ebook_tools

__all__ = [
    "EbookLoaderAgent",
    "EbookLoadError",
    "DEFAULT_MODEL_ID",
    "EbookContext",
    "EbookStructure",
    "StructureNode",
    "CoverageReport",
    "Block",
    "TocEntry",
    "build_ebook_tools",
    "build_structure",
    "validate_tree",
]
