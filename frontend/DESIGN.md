---
name: Diorama reader
description: A calm local library and reader for structured ebooks.
colors:
  paper: '#f7f5ef'
  surface: '#eeece4'
  ink: '#272c25'
  muted: '#64695f'
  rule: '#d9ddd2'
  accent: '#456044'
  accent-hover: '#354d34'
  selection: '#d5dfbf'
  row-hover: '#eeeee5'
  cover-fallback: '#e5e7dc'
  error-surface: '#f5e8df'
  error-ink: '#7d3820'
  night-paper: '#1e2420'
  night-surface: '#2a332c'
  night-ink: '#e4e8dc'
  night-muted: '#a6b09f'
  night-rule: '#3a443a'
  night-accent: '#b4cba0'
typography:
  display:
    fontFamily: 'Literata, serif'
    fontSize: 'clamp(32px, 4.1vw, 51px)'
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: '-0.035em'
  title:
    fontFamily: 'Literata, serif'
    fontSize: '23px'
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: '-0.018em'
  body:
    fontFamily: 'Literata, serif'
    fontSize: '19px'
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: 'Work Sans, sans-serif'
    fontSize: '12px'
  button:
    fontFamily: 'Work Sans, sans-serif'
    fontSize: '13px'
    fontWeight: 500
rounded:
  segmented-option: '4px'
  contents-item: '5px'
  control: '6px'
  panel: '12px'
spacing:
  compact: '8px'
  small: '12px'
  medium: '16px'
  panel: '24px'
  section: '32px'
  reading-navigation: '64px'
components:
  button-primary:
    backgroundColor: '{colors.accent}'
    textColor: '{colors.paper}'
    typography: '{typography.button}'
    rounded: '{rounded.control}'
    padding: '10px 17px'
  button-primary-hover:
    backgroundColor: '{colors.accent-hover}'
  button-quiet:
    backgroundColor: 'transparent'
    textColor: '{colors.ink}'
    typography: '{typography.button}'
    rounded: '{rounded.control}'
    padding: '10px 17px'
  button-quiet-hover:
    backgroundColor: '{colors.surface}'
  button-icon:
    backgroundColor: 'transparent'
    textColor: '{colors.ink}'
    rounded: '{rounded.control}'
    width: '36px'
    height: '36px'
  search:
    backgroundColor: 'transparent'
    textColor: '{colors.ink}'
    typography: '{typography.label}'
    padding: '9px 2px'
    width: '205px'
  contents-item:
    backgroundColor: 'transparent'
    textColor: '{colors.ink}'
    typography: '{typography.label}'
    rounded: '{rounded.contents-item}'
    padding: '11px 12px'
  contents-item-current:
    backgroundColor: '{colors.surface}'
    textColor: '{colors.accent}'
  settings-panel:
    backgroundColor: '{colors.paper}'
    textColor: '{colors.ink}'
    rounded: '{rounded.panel}'
    padding: '24px'
    width: '285px'
---

# Design System: Diorama reader

## Overview

**Creative North Star: "The independent literary review"**

An independent literary review frames local ebook runs as a calm reading library. Warm paper, Literata, restrained green, fine rules, and real ebook covers give the interface a quiet editorial character.

The reader preserves source hierarchy in a conventional nested contents rail and leaves a generous column for book content. Work Sans keeps navigation and settings distinct from the literary text; mobile disclosures and saved reading preferences support the same reading experience at smaller widths.

**Key Characteristics:**

- Warm paper and restrained green.
- Literata reading and headings; Work Sans interface.
- Ruled library rows with actual ebook covers.
- Nested contents and a generous reading column.

## Colors

The palette combines warm neutral surfaces with a restrained botanical accent. Frontmatter records the implemented values; CSS custom properties remain the implementation source.

### Primary

Green marks reading actions, active contents, links, progress, and keyboard focus. The darker green is the primary button hover state; the pale selection color highlights selected text.

### Neutral

Paper is the main canvas, surface distinguishes selected controls, ink carries text, muted ink carries metadata, and rules divide regions. Library row hover and cover fallback have their own quiet neutral fills. Error surface and error ink identify recoverable file/library failures.

The night palette overrides all six reader custom properties together. It belongs to the reader and retains the same hierarchy and component structure.

## Typography

Literata supplies the wordmark, library heading, book titles, and default reading text. Work Sans supplies labels, metadata, navigation, and controls; readers can also choose it for book text.

The display and title roles in frontmatter describe the library. Book titles reduce to (21px) at the medium breakpoint and (19px) on mobile. Reading defaults to the body role, with a persisted size range of (15–28px). Generated section titles use (1.7em), while source content headings use (1.75em), (1.45em), and (1.2em), all with (1.4) line height. Interface metadata uses (10–12px); primary actions use the button role. Avoid making source content compete with interface labels.

## Layout

The library has a centered container (`min(1040px, 90%)`), a ruled masthead, heading and introduction, search/list toolbar, stacked cover rows, and a file import strip. Desktop covers measure (92 × 130px); mobile covers measure (72 × 103px).

The reader occupies (100dvh), with fixed-height header and footer framing independently scrolling contents and reading regions. The contents rail starts at (280px), narrows to (235px) at widths up to (900px), and grows to (310px) from (1500px). The article uses `max-width: calc(68ch + 100px)` with horizontal padding of (50px), preserving a reading measure of roughly (68ch). At the medium breakpoint its padding becomes (38px 30px).

At widths up to (680px), the library toolbar and import strip stack, the reader header becomes (65px), and the article uses (32px 24px) padding. Contents becomes a disclosed overlay (`min(320px, 88vw)`) opened from the header. Running title, settings, progress percentage, and section stepping remain available.

Spacing values in frontmatter are recurring distances, not a comprehensive mathematical scale. Preserve the larger article and section-navigation breathing room alongside compact interface spacing.

## Elevation & Depth

Fine rules and tonal changes supply most separation. Shadows are limited to the physical book cover, the floating settings panel, and the disclosed mobile contents rail. Exact shadow and motion values live in the sidecar. Library row hover changes background over (180ms ease-out); loading indicators rotate over (1s linear). Reduced-motion mode removes animation and transitions.

## Shapes

Controls have small rounded corners; contents and segmented options use their own tighter radii. The settings panel has the larger panel radius. Book covers have an asymmetric bound-book silhouette (`3px 6px 6px 3px`). Rows remain flat and span the list width; green reading marks are small circles.

## Components

- **Buttons:** solid green primary action; ruled transparent quiet action; borderless icon controls. Hover uses the existing darker green or neutral surface. Disabled buttons retain the same shape with opacity (0.45). Keyboard focus uses a (2px) accent outline offset by (4px).
- **Search:** a transparent text input and small search icon sit inside the library toolbar. Its placeholder uses muted ink; mobile input expands to the available width.
- **Library rows:** a whole-row button joins the real cover, author/title, section/date/run metadata, and start/continue action. A missing cover uses the existing book icon and short title, not replacement artwork.
- **Contents:** nested ordered lists preserve source grouping. Separate arrow buttons expand and collapse branches; group and text-node buttons open their corresponding views. The current node uses neutral fill, green text, and medium weight. Structural types and supplementary flags appear in smaller muted text.
- **Hierarchy:** parent overviews list immediate children as ruled rows and offer a reading action into the first text node. Clickable breadcrumbs return to ancestor nodes. Existing type, color, and control tokens carry these views.
- **Settings:** a header disclosure opens the floating panel. A range input controls size; segmented buttons expose selected typeface and reading theme through `aria-pressed`. Preferences persist locally.
- **Reading navigation:** section controls appear after the text and in the footer. The footer combines current section, book progress, and section count; its thin progress bar is hidden on mobile while the percentage remains.
- **Content:** source HTML/Markdown keeps its hierarchy, images fit the column, tables scroll when necessary, and missing images become readable text. The book content itself stays visually quiet.

## Do's and Don'ts

### Do:

- Do use real ebook covers when available and the existing typographic fallback when unavailable.
- Do distinguish book typography from interface labels and keep source structure visible in contents.
- Do preserve visible keyboard focus, current-section states, and reduced-motion behavior.
- Do carry the paper/night palette through reader controls and preserve saved reading preferences.

### Don't:

- Don't replace the ruled library list with a dashboard card grid.
- Don't add ornamental imagery, gradients, or shadows behind book text.
- Don't invent book metadata or introduce generated artwork into the local library.
