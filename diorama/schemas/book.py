"""Wire format for an edition's hierarchy and its ordered passage references."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=512, pattern=r"\S")
]


class SourceRange(BaseModel):
    """A nonempty interval in one occurrence of an XHTML spine resource.

    `occurrence` is the zero-based spine position and `resource` is the path
    inside the archive. `start` is inclusive and `end` exclusive. Both count
    Unicode code points in that occurrence's indexed text.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    occurrence: int = Field(ge=0)
    resource: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def check_interval(self) -> SourceRange:
        """Require a positive-length interval during model validation."""
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class BookNode(BaseModel):
    """Common ID for a section or passage. IDs must be unique within a book tree."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: Identifier


class Passage(BookNode):
    """Leaf whose `source` locates the passage text in the indexed edition."""

    kind: Literal["passage"] = "passage"
    source: SourceRange


class Section(BookNode):
    """Ordered group of passages and subsections with an edition-specific label.

    A section can contain text before its first subsection. For instance, an
    act may open with a stage direction. `is_self_contained` identifies units
    such as an independent story in an anthology.
    """

    kind: Literal["section"] = "section"
    label: Identifier
    title: str | None = None
    is_self_contained: bool = False
    children: list[Annotated[Section | Passage, Field(discriminator="kind")]] = Field(
        min_length=1
    )


class Book(BaseModel):
    """Manifest binding a library book ID and section tree to one EPUB digest.

    `title` and `authors` come from the source metadata. `uncertainties`
    records structural decisions that the edition leaves ambiguous.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    id: Identifier
    title: str
    authors: list[str]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    root: Section
    uncertainties: list[str] = Field(default_factory=list)
