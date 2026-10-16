from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterProfile(BaseModel):
    id: str = Field(description="Unique identifier of the character.")
    book_id: str = Field(
        description="Unique identifier of the book the character belongs to."
    )
    character_name: str = Field(
        description="The name of the character, this is the name through which the character is most commonly known as in the book."
    )
    character_alias: list[str] = Field(
        description="The aliases of the character, other names, monickers, titles, etc which the character is referred to in the story."
    )
    character_profile_description: str = Field(
        description="A description of the character in the context of the st0ry."
    )
    headshot_visual_prompt: str = Field(
        description=(
            "An exhaustive, self-contained image-generation prompt for a head-and-shoulders "
            "portrait, written as natural descriptive prose without headings, section labels, "
            "or lists. Do not include any character names, aliases, IDs, or name placeholders "
            "anywhere in the prompt. Identify the subject through appearance alone. "
            "Describe all story-supported facial anatomy, proportions, complexion, "
            "skin texture, eyes, brows, nose, mouth, jaw, hair, facial hair, distinctive "
            "marks, expression, headwear, and visible clothing in precise visual language. "
            "Preserve colors, textures, placement, asymmetry, and changes over time when "
            "specified. Separate the depicted appearance from contextual variants and "
            "rendering instructions; explicitly identify unknowns without inventing traits. "
            "Keep identity and the depicted story state consistent with full_body_visual_prompt."
        )
    )
    full_body_visual_prompt: str = Field(
        description=(
            "An exhaustive, self-contained image-generation prompt showing the entire "
            "character from head to feet, or all extremities for non-human anatomy, written "
            "as natural descriptive prose without headings, section labels, or lists. "
            "Do not include any character names, aliases, IDs, or name placeholders anywhere "
            "in the prompt. Identify the subject through appearance alone. "
            "Repeat identifying facial features and describe all story-supported stature, "
            "build, proportions, anatomy, posture, clothing layers, fit, fabrics, colors, "
            "patterns, wear, footwear, accessories, and carried objects. Preserve precise "
            "placement and changes over time. Separate the depicted appearance from "
            "contextual variants and rendering instructions; explicitly identify unknowns "
            "without inventing traits. Keep identity and the depicted story state "
            "consistent with headshot_visual_prompt."
        )
    )
