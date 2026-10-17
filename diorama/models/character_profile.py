from __future__ import annotations

from pydantic import BaseModel, Field


class CharacterProfile(BaseModel):
    id: str = Field(description="Unique identifier of the character.")
    book_id: str = Field(
        description="Unique identifier of the book the character belongs to."
    )
    character_name: str = Field(
        description=(
            "The character's full name as explicitly established in the storybook, "
            "even when a shorter name or nickname is more common. Use the fullest "
            "supported name without inventing missing parts; for an unnamed character, "
            "use a concise distinguishing description grounded in the story. "
            "Keep other attested names and nicknames in character_alias."
        )
    )
    character_alias: list[str] = Field(
        description=(
            "All explicit, non-trivial aliases used for this character in the story: "
            "alternate names, meaningful short names, nicknames, pseudonyms, disguise "
            "names, and identifying titles or epithets, including those used only once. "
            "Exclude character_name itself, duplicates, pronouns, generic references, "
            "passing descriptions, and trivial case, whitespace, punctuation, or "
            "possessive variants. Include only attested forms, never invented name "
            "parts, expansions, or translations. Use an empty list if none qualify."
        )
    )
    character_profile_description: str = Field(
        description=(
            "A concise introduction in one or two sentences that tells the reader "
            "who the character is, using story-supported identity, role, key "
            "relationships, and distinguishing traits as needed. Do not recount "
            "their entire journey, list plot events, or summarize their character "
            "arc. Keep this limit during enrichment and merges; use one short "
            "sentence when evidence is sparse, without inventing details."
        )
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
