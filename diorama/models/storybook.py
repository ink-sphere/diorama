from __future__ import annotations

from pydantic import BaseModel, Field


class TextContent(BaseModel):
    raw_text: str = Field(description="The raw text content with HTML/XML tags")
    markdown_text: str = Field(description="The text converted to markdown")


class StructureNode(BaseModel):
    structure_type: str = Field(
        description="The type of the node in the hierarchical structue of the book"
    )
    structure_title: str = Field(description="The title of the strcture")
    is_part_of_narrative: bool = Field(
        description="Whether the structure node is part of the actual narrative from the book or not."
    )
    content: list[TextContent] | list[StructureNode] = Field(
        description="The content of the of node, can be either a list of `TextContent`s or a list of `StructureNode`s."
    )


class EbookMetadata(BaseModel):
    title: str = Field(description="Title of the book.")

    authors: list[str] = Field(
        default_factory=list,
        description="Authors or creators of the book.",
    )

    language: str | None = Field(
        default=None,
        description="Language of the book, usually a BCP 47 code such as 'en'.",
    )

    identifiers: list[str] = Field(
        default_factory=list,
        description="Book identifiers, such as an ISBN, UUID, or DOI.",
    )

    publisher: str | None = Field(
        default=None,
        description="Publisher of the book.",
    )

    publication_date: str | None = Field(
        default=None,
        description="Publication date as represented in the EPUB metadata.",
    )

    description: str | None = Field(
        default=None,
        description="Description or synopsis of the book.",
    )

    subjects: list[str] = Field(
        default_factory=list,
        description="Subjects, genres, categories, or keywords.",
    )

    contributors: list[str] = Field(
        default_factory=list,
        description="Other contributors, such as editors, translators, or illustrators.",
    )

    rights: str | None = Field(
        default=None,
        description="Copyright or rights statement.",
    )

    series: str | None = Field(
        default=None,
        description="Name of the series or collection containing the book.",
    )

    series_position: float | None = Field(
        default=None,
        description="Position of the book within its series.",
    )

    cover_href: str | None = Field(
        default=None,
        description="Path or href of the cover image inside the EPUB archive.",
    )


class StoryBook(BaseModel):
    id: str = Field(description="Unique identifier of the book.")
    metadata: EbookMetadata = Field(description="Ebook metadata.")
    structure: list[StructureNode] = Field(
        description="Structure and content of the book."
    )
