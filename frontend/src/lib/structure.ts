/**
 * Flattens an `EbookStructure` into the two shapes the reader actually needs: a
 * linear list of readable `Section`s and a `TocNode` tree for the sidebar.
 *
 * Sections are the tree's leaves in depth-first reading order, and that order is
 * the contract behind `ReadingProgress.section_index` on the backend — reordering
 * this walk invalidates every saved position.
 */

import type { BookScenes, EbookStructure, StructureNode } from "./types";

export interface Section {
  /** Depth-first leaf index; matches `ReadingProgress.section_index`. */
  index: number;
  /** Short chrome label, e.g. "Chapter IV". */
  label: string;
  /** Full display heading, e.g. "Chapter IV: The Rabbit Sends in a Little Bill". */
  heading: string;
  levelType: string;
  /** Ancestor headings, outermost first — shown as the reader's breadcrumb. */
  ancestors: string[];
  /**
   * The section's paragraphs grouped into scenes — the reader's page unit, since
   * each scene begins a fresh page.
   *
   * Always at least one group: a section with no segmentation (a `preamble_text`
   * opening, a book processed before scenes existed, a failed pass) is one scene
   * spanning everything, which paginates exactly as it did before scenes.
   */
  scenes: string[][];
  /** Every paragraph, scene grouping flattened away. */
  paragraphs: string[];
  wordCount: number;
  startBlockId: number;
}

export interface TocNode {
  key: string;
  heading: string;
  label: string;
  levelType: string;
  depth: number;
  /** Leaves point at their own section; branches at their first descendant leaf. */
  sectionIndex: number | null;
  children: TocNode[];
}

export interface ReadableBook {
  title: string;
  author: string | null;
  sections: Section[];
  toc: TocNode[];
}

/**
 * "front_matter" → "Front matter".
 *
 * `level_type` is whatever the loader agent chose to call that level, and it often
 * arrives snake_cased. It's shown as a label everywhere, so it gets cleaned up once,
 * here, rather than in each place that renders it.
 */
function titleCase(value: string): string {
  const words = value.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "Chapter IV" — the level type carries the noun, the agent supplies the numeral. */
function labelFor(node: StructureNode): string {
  const level = titleCase((node.level_type || "section").trim());
  const number = node.number?.trim();
  return number ? `${level} ${number}` : level;
}

/**
 * Compose a heading without repeating what the title already says.
 *
 * EPUB headings are wildly inconsistent: some books title a chapter "The Pool of
 * Tears" (needs the "Chapter II" prefix), others title it "CHAPTER II. The Pool of
 * Tears" (already has it). Prefixing blindly yields "Chapter II: CHAPTER II. …".
 */
function headingFor(node: StructureNode): string {
  const label = labelFor(node);
  const title = node.title?.trim();
  if (!title) return label;

  const flatTitle = title.toLowerCase();
  const level = titleCase(node.level_type || "").toLowerCase();
  const number = node.number?.trim().toLowerCase();
  const alreadyLabelled =
    (level.length > 2 && flatTitle.startsWith(level)) ||
    (!!number && new RegExp(`\\b${escapeRegExp(number)}\\b`).test(flatTitle));

  return alreadyLabelled ? title : `${label}: ${title}`;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function toParagraphs(text: string): string[] {
  return text
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}

function normalize(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * Drop leading paragraphs that merely restate the heading the reader already sees.
 *
 * A section's block range starts *at* the chapter's own `<h1>`, so the first
 * paragraph is usually "CHAPTER I. Down the Rabbit-Hole" — printed again directly
 * under the rendered heading, and stealing the drop cap. Books split that heading
 * across one or two blocks ("CHAPTER I." then the title), so this walks the front of
 * the section while each short paragraph is still contained in the heading; real
 * prose never is.
 */
function stripRepeatedHeading(paragraphs: string[], heading: string): string[] {
  const target = normalize(heading);
  let start = 0;
  while (start < paragraphs.length && start < 2) {
    const candidate = normalize(paragraphs[start]);
    const repeats =
      candidate.length >= 3 &&
      paragraphs[start].length <= 90 &&
      (target.includes(candidate) || candidate.includes(target));
    if (!repeats) break;
    start += 1;
  }
  // Never strip everything: a heading-only section should still render its heading
  // as its text rather than becoming a blank page.
  return start >= paragraphs.length ? paragraphs : paragraphs.slice(start);
}

function countWords(paragraphs: string[]): number {
  return paragraphs.reduce(
    (total, paragraph) => total + paragraph.split(/\s+/).filter(Boolean).length,
    0,
  );
}

/**
 * Split a leaf's text into scene groups, using each scene's own text rather than its
 * paragraph indices.
 *
 * The indices are into the backend's `split_paragraphs` (a bare `"\n\n"` split);
 * `toParagraphs` here trims and drops empties, and `stripRepeatedHeading` removes
 * leading ones — so an index that meant paragraph 7 on the backend can mean a
 * different paragraph by the time it reaches the page. Each scene carries its verbatim
 * text, and the scenes partition the section exactly, so re-splitting each one
 * side-steps the drift entirely and cannot silently misplace a boundary.
 */
function toScenes(
  segmentation: { scenes: { text: string }[] } | undefined,
  fallback: string[],
  heading: string,
): string[][] {
  if (!segmentation || segmentation.scenes.length === 0) return [fallback];

  const groups = segmentation.scenes
    .map((scene) => toParagraphs(scene.text))
    .filter((group) => group.length > 0);
  if (groups.length === 0) return [fallback];

  // The heading repeat lives at the very front of the section, so it can only be in
  // the first scene — and stripping it can empty a scene that was nothing else.
  groups[0] = stripRepeatedHeading(groups[0], heading);
  const kept = groups.filter((group) => group.length > 0);
  return kept.length > 0 ? kept : [fallback];
}

/** Index a book's segmentations by the block their section starts at.
 *
 * Position would be the obvious key, and would be wrong: `readBook` emits an extra
 * "Opening" section for any branch node carrying `preamble_text`, which is not a leaf
 * and therefore has no segmentation of its own. One such node shifts every later
 * section off its scenes. The starting block id is carried by both sides and is unique
 * per section, so it survives that.
 */
function byStartBlock(scenes: BookScenes | null | undefined) {
  const map = new Map<number, BookScenes["segmentations"][number]>();
  for (const segmentation of scenes?.segmentations ?? []) {
    if (typeof segmentation.start_block_id === "number") {
      map.set(segmentation.start_block_id, segmentation);
    }
  }
  return map;
}

/**
 * Walk the tree once, emitting sections and TOC nodes together so the two can't
 * disagree about ordering or about which node a section index refers to.
 */
export function readBook(
  structure: EbookStructure,
  scenes?: BookScenes | null,
): ReadableBook {
  const sections: Section[] = [];
  const segmentations = byStartBlock(scenes);

  const walk = (
    nodes: StructureNode[],
    ancestors: string[],
    depth: number,
    keyPrefix: string,
  ): TocNode[] =>
    nodes.map((node, position) => {
      const key = `${keyPrefix}${position}`;
      const heading = headingFor(node);
      const label = labelFor(node);
      const children: TocNode[] = [];

      // Text a `child_pattern` expansion left stranded before the first match
      // (stage directions ahead of "SCENE I", a chapter's epigraph). It belongs to
      // the parent, but only leaves are readable, so it becomes one.
      const preamble = node.preamble_text?.trim();
      if (node.children.length > 0 && preamble) {
        const paragraphs = stripRepeatedHeading(toParagraphs(preamble), heading);
        const index = sections.length;
        sections.push({
          index,
          label,
          heading,
          levelType: node.level_type,
          ancestors,
          // A branch's preamble is not a leaf, so nothing segmented it: one scene.
          scenes: [paragraphs],
          paragraphs,
          wordCount: countWords(paragraphs),
          startBlockId: node.start_block_id,
        });
        children.push({
          key: `${key}-opening`,
          heading: "Opening",
          label: "Opening",
          levelType: node.level_type,
          depth: depth + 1,
          sectionIndex: index,
          children: [],
        });
      }

      if (node.children.length > 0) {
        children.push(
          ...walk(node.children, [...ancestors, heading], depth + 1, `${key}-`),
        );
        return {
          key,
          heading,
          label,
          levelType: node.level_type,
          depth,
          sectionIndex: firstSectionIndex(children),
          children,
        };
      }

      const paragraphs = stripRepeatedHeading(toParagraphs(node.text ?? ""), heading);
      const sceneGroups = toScenes(
        segmentations.get(node.start_block_id),
        paragraphs,
        heading,
      );
      const index = sections.length;
      sections.push({
        index,
        label,
        heading,
        levelType: node.level_type,
        ancestors,
        scenes: sceneGroups,
        paragraphs: sceneGroups.flat(),
        wordCount: countWords(paragraphs),
        startBlockId: node.start_block_id,
      });
      return {
        key,
        heading,
        label,
        levelType: node.level_type,
        depth,
        sectionIndex: index,
        children: [],
      };
    });

  const toc = walk(structure.root, [], 0, "");
  return {
    title: structure.title,
    author: structure.author ?? null,
    sections,
    toc,
  };
}

function firstSectionIndex(nodes: TocNode[]): number | null {
  for (const node of nodes) {
    if (node.sectionIndex !== null) return node.sectionIndex;
  }
  return null;
}

/** Depth-first flatten of the TOC, for keyboard nav and "current entry" lookup. */
export function flattenToc(nodes: TocNode[]): TocNode[] {
  return nodes.flatMap((node) => [node, ...flattenToc(node.children)]);
}
