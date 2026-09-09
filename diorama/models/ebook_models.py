"""Validated, source-backed ebook structures."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EbookModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SourceReference(EbookModel):
    member: str
    spine_index: int
    start_byte: int
    end_byte: int
    encoding: str


class ContentFragment(EbookModel):
    xhtml: str
    source: SourceReference


class EbookSection(EbookModel):
    title: str
    index: str | None = None
    type: str
    content: list[ContentFragment] = Field(default_factory=list)
    sub_sections: list[EbookSection] = Field(default_factory=list, alias="sub-sections")


class EbookDocument(EbookModel):
    schema_version: int = 1
    title: str
    author: str | None
    metadata: dict[str, Any]
    source_sha256: str
    source_archive: str = "source.epub"
    content: list[EbookSection]
    front_matter: list[EbookSection]
    back_matter: list[EbookSection]


class SectionPlan(EbookModel):
    title: str
    index: str | None = None
    type: str
    start: int = Field(ge=0, description="Inclusive source unit ID")
    end: int = Field(gt=0, description="Exclusive source unit ID")
    sub_sections: list[SectionPlan] = Field(default_factory=list, alias="sub-sections")


class StructurePlan(EbookModel):
    front_matter: list[SectionPlan]
    content: list[SectionPlan]
    back_matter: list[SectionPlan]
