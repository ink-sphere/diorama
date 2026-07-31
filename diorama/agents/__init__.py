"""Concrete agents built on top of diorama.core's ReAct agent framework."""

from diorama.agents.ebook_loader import EbookLoaderAgent, EbookLoaderError
from diorama.agents.ebook_scene_segmentation import (
    EbookSceneSegmentationAgent,
    SceneSegmentationError,
)
from diorama.agents.literary_research_agent import (
    LiteraryResearchAgent,
    LiteraryResearchError,
    LiteraryResearchReport,
)

__all__ = [
    "EbookLoaderAgent",
    "EbookLoaderError",
    "EbookSceneSegmentationAgent",
    "LiteraryResearchAgent",
    "LiteraryResearchError",
    "LiteraryResearchReport",
    "SceneSegmentationError",
]
