# Diorama reader

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React, as requested. Vite and TypeScript are implementation choices for this local application.

## Users

The user generates ebook structure runs and wants to read their resulting StoryBook objects.

## Product Purpose

Open local ebook runs and read the books stored in `output/storybook.json`, preserving their hierarchical structure and content.

## Operating Context

The user selected an automatically discovered local `.ebook-runs` library plus a standalone JSON file picker. The application runs locally alongside the repository.

## Capabilities and Constraints

Read existing outputs without changing ebook runs. Support nested structure, narrative and supplementary sections, original HTML and Markdown, and available book images. The StoryBook schema lives in `diorama/models/storybook.py`. Local file selection requires an explicit user action.

## Evidence on Hand

Real runs exist under `.ebook-runs`, including two outputs for the user's Harry Potter book. Use their actual metadata; do not invent library entries.

## Product Principles

Reading comes first. Preserve book content. Keep local data local. Make structure easy to navigate. Explain invalid or unavailable files with a recovery action.

## Open Decisions

The visual direction and convenience features are implementation assumptions, not permanent user preferences.
