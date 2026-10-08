import { describe, expect, it } from 'vitest'
import { ancestors, buildNavigation, flattenSections, parseStoryBook } from '../src/lib/storybook'
import { imageUrl, renderSection } from '../src/lib/content'

const fragment = { raw_text: '<p>First paragraph.</p>', markdown_text: 'First paragraph.' }
const leaf = {
  structure_type: 'chapter',
  structure_title: 'Chapter one',
  is_part_of_narrative: true,
  content: [fragment],
}
const book = {
  id: 'book',
  metadata: { title: 'A book', authors: ['An author'] },
  structure: [
    {
      structure_type: 'part',
      structure_title: 'Part one',
      is_part_of_narrative: true,
      content: [leaf],
    },
    { ...leaf, structure_title: '' },
  ],
}

describe('StoryBook structure', () => {
  it('keeps leaf order and nested paths while providing untitled section names', () => {
    const sections = flattenSections(parseStoryBook(book).structure)
    expect(sections.map((section) => section.path)).toEqual(['0.0', '1'])
    expect(sections[0].parents).toEqual(['Part one'])
    expect(sections[1].title).toBe('Chapter')
  })
  it.each([
    {},
    { ...book, structure: [] },
    { ...book, metadata: { title: 'A book', authors: 'author' } },
    { ...book, structure: [{ ...leaf, content: [fragment, leaf] }] },
  ])('rejects malformed or mixed source data', (value) => {
    expect(() => parseStoryBook(value)).toThrow()
  })
})

describe('book rendering', () => {
  it('removes executable source markup and rewrites local images', () => {
    const section = flattenSections(
      parseStoryBook({
        ...book,
        structure: [
          {
            ...leaf,
            content: [
              {
                raw_text:
                  '<script>window.bad=true</script><p onclick="alert(1)">Text</p><img src="../Images/page.png" onerror="alert(2)"><iframe src="https://example.com"></iframe>',
                markdown_text: '',
              },
            ],
          },
        ],
      }).structure,
    )[0]
    const html = renderSection(section, 'run-one')
    expect(html).not.toMatch(/<script|onclick|onerror|<iframe/)
    expect(html).toContain('/api/runs/run-one/assets?path=Images%2Fpage.png')
    expect(html).toContain('<p>Text</p>')
  })
  it('renders Markdown from plain text and explains missing images in standalone files', () => {
    const section = flattenSections(
      parseStoryBook({
        ...book,
        structure: [
          {
            ...leaf,
            content: [
              { raw_text: '# Heading', markdown_text: '# Heading\n\nText' },
              { raw_text: '<img src="page.png" alt="A map">', markdown_text: 'A map' },
            ],
          },
        ],
      }).structure,
    )[0]
    expect(renderSection(section, null)).toContain('<h1>Heading</h1>')
    expect(renderSection(section, null)).toContain('A map</span>')
  })
  it('does not automatically request remote images', () => {
    expect(imageUrl('run-one', 'https://tracker.example/image.png')).toBeNull()
    expect(imageUrl(null, 'page.png')).toBeNull()
  })
})

describe('hierarchical navigation', () => {
  it('indexes every level and preserves text order across nested groups', () => {
    const source = parseStoryBook({
      ...book,
      structure: [
        {
          ...book.structure[0],
          content: [
            {
              ...leaf,
              content: [
                { ...leaf, structure_type: 'scene', structure_title: 'Scene one' },
                { ...leaf, structure_type: 'scene', structure_title: 'Scene two' },
              ],
            },
            { ...leaf, structure_title: 'Chapter two' },
          ],
        },
        { ...leaf, structure_type: 'appendix', is_part_of_narrative: false },
      ],
    })
    const navigation = buildNavigation(source.structure)
    expect([...navigation.entries.keys()]).toEqual(['0', '0.0', '0.0.0', '0.0.1', '0.1'])
    expect(navigation.sections.map((section) => section.path)).toEqual(['0.0.0', '0.0.1', '0.1'])
    expect(navigation.entries.get('0')?.children).toEqual(['0.0', '0.1'])
    expect(navigation.entries.get('0.0')?.children).toEqual(['0.0.0', '0.0.1'])
    expect(navigation.entries.get('0')?.words).toBe(6)
    expect(navigation.entries.get('0')?.lastSection).toBe(2)
    expect(ancestors(navigation, '0.0.1').map((entry) => entry.path)).toEqual(['0', '0.0'])
    expect(navigation.entries.has('1')).toBe(false)
  })

  it('allows empty text nodes permitted by the Python model', () => {
    const source = parseStoryBook({
      ...book,
      structure: [{ ...leaf, structure_title: '', content: [] }],
    })
    const navigation = buildNavigation(source.structure)
    expect(navigation.sections).toHaveLength(1)
    expect(navigation.sections[0].words).toBe(0)
    expect(navigation.sections[0].title).toBe('Chapter')
    expect(renderSection(navigation.sections[0], null)).toBe('')
  })

  it('hides supplementary leaves and branches without renumbering source paths', () => {
    const supplementary = { ...leaf, is_part_of_narrative: false }
    const source = parseStoryBook({
      ...book,
      structure: [
        supplementary,
        { ...supplementary, content: [leaf] },
        {
          ...book.structure[0],
          content: [
            supplementary,
            leaf,
            { ...leaf, content: [supplementary] },
            { ...leaf, structure_title: 'Chapter two' },
          ],
        },
        supplementary,
      ],
    })
    const original = structuredClone(source)
    const navigation = buildNavigation(source.structure)
    expect(navigation.roots).toEqual(['2'])
    expect([...navigation.entries.keys()]).toEqual(['2', '2.1', '2.3'])
    expect(navigation.entries.get('2')?.children).toEqual(['2.1', '2.3'])
    expect(navigation.sections.map((section) => section.path)).toEqual(['2.1', '2.3'])
    expect(navigation.sections.every((section) => section.narrative)).toBe(true)
    expect(navigation.entries.get('2')).toMatchObject({ words: 4, firstSection: 0, lastSection: 1 })
    expect(navigation.sections[1].parents).toEqual(['Part one'])
    expect(source).toEqual(original)
  })

  it('returns empty navigation when no narrative leaves remain', () => {
    const supplementary = { ...leaf, is_part_of_narrative: false }
    const navigation = buildNavigation([
      supplementary,
      { ...leaf, content: [{ ...leaf, content: [supplementary] }] },
    ])
    expect(navigation.roots).toEqual([])
    expect(navigation.sections).toEqual([])
    expect(navigation.entries.size).toBe(0)
  })
})
