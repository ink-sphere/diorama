# Diorama reader

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React, as requested. Vite and TypeScript are implementation choices for this local application.

## Users

The user generates ebook structure runs and wants to read their resulting StoryBook objects.

## Product Purpose

Open local ebook runs and read the books stored in `storybook.json`, preserving their hierarchical structure and content. Consult each book's generated character profiles while keeping your reading place.

## Operating Context

The user selected an automatically discovered local `.ebook-runs` library plus a standalone JSON file picker. The application runs locally alongside the repository.

## Capabilities and Constraints

Read existing outputs without changing ebook runs. Support nested structure, narrative and supplementary sections, original HTML and Markdown, and available book images. The StoryBook schema lives in `diorama/models/storybook.py`. The dedicated Characters view reads `character-profiles.json` alongside the book, following `diorama/models/character_profile.py`, with name/alias search and selected profile details. Character descriptions can reveal whole-book events. Local file selection requires an explicit user action; standalone StoryBook imports cannot automatically load sibling profile files.

## Evidence on Hand

Real runs exist under `.ebook-runs`, including two outputs for the user's Harry Potter book. Use their actual metadata; do not invent library entries.

## Product Principles

Reading comes first. Preserve book content. Keep local data local. Make structure easy to navigate. Explain invalid or unavailable files with a recovery action.

## Open Decisions

The visual direction and convenience features are implementation assumptions, not permanent user preferences.
