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
            "A visual description of the looks of the character's face. "
            "This is the prompt that an image generation model uses to generate a headshot of the character."
        )
    )
