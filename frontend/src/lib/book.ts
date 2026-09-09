import { z } from "zod";

export const sourceSchema = z.object({
  member: z.string(), spine_index: z.number().int().nonnegative(),
  start_byte: z.number().int().nonnegative(), end_byte: z.number().int().nonnegative(),
  encoding: z.string(),
});
export const fragmentSchema = z.object({ xhtml: z.string(), source: sourceSchema });
export type Fragment = z.infer<typeof fragmentSchema>;
export interface Section {
  title: string; index: string | null; type: string;
  content: Fragment[]; "sub-sections": Section[];
}
export const sectionSchema: z.ZodType<Section> = z.lazy(() => z.object({
  title: z.string(), index: z.string().nullable().default(null), type: z.string(),
  content: z.array(fragmentSchema), "sub-sections": z.array(sectionSchema),
}));
export const bookSchema = z.object({
  schema_version: z.literal(1), title: z.string(), author: z.string().nullable(),
  metadata: z.record(z.string(), z.unknown()), source_sha256: z.string(),
  source_archive: z.string().default("source.epub"),
  content: z.array(sectionSchema), front_matter: z.array(sectionSchema),
  back_matter: z.array(sectionSchema),
});
export type Book = z.infer<typeof bookSchema>;
export const groups = ["front_matter", "content", "back_matter"] as const;
export type Group = typeof groups[number];
export const groupLabels: Record<Group, string> = {
  front_matter: "Opening pages", content: "Contents", back_matter: "Closing pages",
};
export interface NavNode {
  id: string; rootId: string; title: string; index: string | null; type: string;
  children: NavNode[]; start: Fragment["source"] | null;
}
export type Navigation = Record<Group, NavNode[]>;
export interface BookInfo {
  id: string; title: string; author: string | null; hash: string;
  navigation: Navigation; cover: string | null;
}
export interface BookSummary {
  id: string; title: string; author: string | null; hash?: string;
  sections?: number; cover?: string | null; error?: string;
}
export function fragmentsOf(section: Section): Fragment[] {
  return [...section.content, ...section["sub-sections"].flatMap(fragmentsOf)].sort(
    (a, b) => a.source.spine_index - b.source.spine_index || a.source.start_byte - b.source.start_byte,
  );
}
export function navigationOf(book: Book): Navigation {
  function walk(section: Section, id: string, rootId: string): NavNode {
    return { id, rootId, title: section.title, index: section.index, type: section.type,
      start: fragmentsOf(section)[0]?.source ?? null,
      children: section["sub-sections"].map((s, i) => walk(s, `${id}/sub-sections/${i}`, rootId)) };
  }
  return Object.fromEntries(groups.map(group => [group, book[group].map((s, i) => walk(s, `${group}/${i}`, `${group}/${i}`))])) as Navigation;
}
export function flatten(nodes: NavNode[]): NavNode[] {
  return nodes.flatMap(n => [n, ...flatten(n.children)]);
}
export function rootsOf(nav: Navigation): NavNode[] { return groups.flatMap(g => nav[g]); }
export function nodesOf(nav: Navigation): NavNode[] { return flatten(rootsOf(nav)); }
export function rootSection(book: Book, id: string): Section {
  const match = /^(front_matter|content|back_matter)\/(\d+)$/.exec(id);
  const section = match && book[match[1] as Group][Number(match[2])];
  if (!section) throw new Error("Section not found");
  return section;
}
export function readerUrl(bookId: string, section?: string, anchor?: string): string {
  const query = new URLSearchParams();
  if (section) query.set("section", section);
  if (anchor) query.set("anchor", anchor);
  return `/books/${encodeURIComponent(bookId)}${query.size ? `?${query}` : ""}`;
}
