export interface TextContent {
  raw_text: string
  markdown_text: string
}
export interface StructureNode {
  structure_type: string
  structure_title: string
  is_part_of_narrative: boolean
  content: TextContent[] | StructureNode[]
}
export interface EbookMetadata {
  title: string
  authors: string[]
  language?: string | null
  identifiers?: string[]
  publisher?: string | null
  publication_date?: string | null
  description?: string | null
  subjects?: string[]
  contributors?: string[]
  rights?: string | null
  series?: string | null
  series_position?: number | null
  cover_href?: string | null
}
export interface StoryBook {
  id: string
  metadata: EbookMetadata
  structure: StructureNode[]
}
export interface StructureEntry {
  path: string
  node: StructureNode
  title: string
  parentPath: string | null
  children: string[]
  firstSection: number
  lastSection: number
  words: number
}
export interface BookNavigation {
  entries: Map<string, StructureEntry>
  roots: string[]
  sections: Section[]
}
export interface Section {
  path: string
  title: string
  type: string
  narrative: boolean
  content: TextContent[]
  words: number
  parents: string[]
}
export interface RunSummary {
  runId: string
  bookId: string
  title: string
  authors: string[]
  language?: string | null
  coverHref?: string | null
  sections: number
  modified: string
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function parseStoryBook(value: unknown): StoryBook {
  if (!record(value) || typeof value.id !== 'string' || !value.id.trim())
    throw new Error('This file needs a StoryBook id.')
  if (
    !record(value.metadata) ||
    typeof value.metadata.title !== 'string' ||
    !value.metadata.title.trim()
  )
    throw new Error('This file needs a book title in metadata.title.')
  const authors = value.metadata.authors ?? []
  if (!Array.isArray(authors) || authors.some((author) => typeof author !== 'string'))
    throw new Error('Book authors must be a list of names.')
  if (!Array.isArray(value.structure) || !value.structure.length)
    throw new Error('This file has no book structure to read.')
  let count = 0
  function nodes(items: unknown[], path: string, depth: number): StructureNode[] {
    if (depth > 64) throw new Error('The book structure is nested too deeply.')
    return items.map((item, index) => {
      count += 1
      const location = `${path}[${index}]`
      if (count > 20000 || !record(item))
        throw new Error('The book structure is invalid or too large.')
      if (
        typeof item.structure_type !== 'string' ||
        typeof item.structure_title !== 'string' ||
        typeof item.is_part_of_narrative !== 'boolean'
      )
        throw new Error(`${location} needs a type, title, and narrative flag.`)
      if (!Array.isArray(item.content)) throw new Error(`${location}.content must be a list.`)
      const first = item.content[0]
      const children = record(first) && 'structure_type' in first
      const content = children
        ? nodes(item.content, `${location}.content`, depth + 1)
        : item.content.map((fragment) => {
            if (
              !record(fragment) ||
              typeof fragment.raw_text !== 'string' ||
              typeof fragment.markdown_text !== 'string' ||
              'structure_type' in fragment
            )
              throw new Error(`${location} must contain either text fragments or child sections.`)
            return { raw_text: fragment.raw_text, markdown_text: fragment.markdown_text }
          })
      return {
        structure_type: item.structure_type,
        structure_title: item.structure_title,
        is_part_of_narrative: item.is_part_of_narrative,
        content,
      }
    })
  }
  return {
    id: value.id,
    metadata: { ...value.metadata, title: value.metadata.title, authors },
    structure: nodes(value.structure, 'structure', 0),
  }
}

export function isGroup(node: StructureNode): node is StructureNode & { content: StructureNode[] } {
  return node.content.length > 0 && 'structure_type' in node.content[0]
}
export function nodeTitle(node: StructureNode): string {
  return (
    node.structure_title.trim() ||
    node.structure_type.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase()) ||
    'Untitled section'
  )
}

export function buildNavigation(nodes: StructureNode[]): BookNavigation {
  const entries = new Map<string, StructureEntry>()
  const sections: Section[] = []
  function visit(items: StructureNode[], parentPath: string | null, parents: string[]): string[] {
    const paths: string[] = []
    items.forEach((node, index) => {
      if (!node.is_part_of_narrative) return
      const path = parentPath === null ? `${index}` : `${parentPath}.${index}`
      const entry: StructureEntry = {
        path,
        node,
        title: nodeTitle(node),
        parentPath,
        children: [],
        firstSection: sections.length,
        lastSection: sections.length,
        words: 0,
      }
      entries.set(path, entry)
      if (isGroup(node)) {
        entry.children = visit(node.content, path, [...parents, entry.title])
        if (!entry.children.length) {
          entries.delete(path)
          return
        }
        entry.words = entry.children.reduce((sum, child) => sum + entries.get(child)!.words, 0)
      } else {
        const content = node.content as TextContent[]
        entry.words =
          content
            .map((fragment) => fragment.markdown_text)
            .join(' ')
            .match(/\S+/g)?.length ?? 0
        sections.push({
          path,
          title: entry.title,
          type: node.structure_type.replaceAll('_', ' '),
          narrative: node.is_part_of_narrative,
          content,
          words: entry.words,
          parents,
        })
      }
      entry.lastSection = sections.length - 1
      paths.push(path)
    })
    return paths
  }
  const roots = visit(nodes, null, [])
  return { entries, roots, sections }
}

export function ancestors(navigation: BookNavigation, path: string): StructureEntry[] {
  const result: StructureEntry[] = []
  let parent = navigation.entries.get(path)?.parentPath
  while (parent !== null && parent !== undefined) {
    const entry = navigation.entries.get(parent)!
    result.unshift(entry)
    parent = entry.parentPath
  }
  return result
}

export function flattenSections(nodes: StructureNode[]): Section[] {
  return buildNavigation(nodes).sections
}
