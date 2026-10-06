import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { zipSync, strToU8 } from 'fflate'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { listRuns, readAsset, readBook } from '../server/runs.mjs'

let root: string
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'diorama-reader-test-'))
  await mkdir(join(root, 'run-one/output'), { recursive: true })
  await mkdir(join(root, 'run-one/input'))
  await writeFile(
    join(root, 'run-one/output/storybook.json'),
    JSON.stringify({
      id: 'book',
      metadata: { title: 'A book', authors: ['An author'] },
      structure: [
        {
          structure_type: 'chapter',
          content: [{ raw_text: '<p>Text</p>', markdown_text: 'Text' }],
        },
      ],
    }),
  )
})
afterEach(async () => {
  await rm(root, { recursive: true, force: true })
})

describe('local runs', () => {
  it('lists completed runs and ignores incomplete ones', async () => {
    await mkdir(join(root, 'unfinished'))
    const result = await listRuns(root)
    expect(result.runs).toHaveLength(1)
    expect(result.runs[0].runId).toBe('run-one')
    expect(result.runs[0].sections).toBe(1)
    expect(result.problems).toEqual([])
  })
  it('reports malformed files without preventing the library from loading', async () => {
    await writeFile(join(root, 'run-one/output/storybook.json'), '{')
    const result = await listRuns(root)
    expect(result.runs).toEqual([])
    expect(result.problems).toHaveLength(1)
  })
  it('does not follow a StoryBook symlink outside a run', async () => {
    await writeFile(join(root, 'outside.json'), '{}')
    await rm(join(root, 'run-one/output/storybook.json'))
    await symlink(join(root, 'outside.json'), join(root, 'run-one/output/storybook.json'))
    await expect(readBook(root, 'run-one')).rejects.toThrow('outside')
  })
  it('rejects traversal identifiers and image paths', async () => {
    await expect(readBook(root, '../outside')).rejects.toThrow('Invalid run')
    await expect(readAsset(root, 'run-one', '../../cover.png')).rejects.toThrow('Invalid image')
    await expect(readAsset(root, 'run-one', 'session.json')).rejects.toThrow('Only book images')
  })
  it('reads exact EPUB image entries and finds a cover in a JSON export', async () => {
    await writeFile(
      join(root, 'run-one/input/book.epub'),
      zipSync({
        'OEBPS/Images/map.png': strToU8('map'),
        'cover.jpg': strToU8('cover'),
        'book.json': strToU8('{}'),
      }),
    )
    expect(Buffer.from((await readAsset(root, 'run-one', 'Images/map.png')).data).toString()).toBe(
      'map',
    )
    const cover = await readAsset(root, 'run-one', 'cover1', true)
    expect(Buffer.from(cover.data).toString()).toBe('cover')
    expect(cover.type).toBe('image/jpeg')
    expect((await readFile(join(root, 'run-one/input/book.epub'))).length).toBeGreaterThan(0)
  })
})

it('counts nested text nodes even when a structure type is untitled', async () => {
  await writeFile(
    join(root, 'run-one/output/storybook.json'),
    JSON.stringify({
      id: 'book',
      metadata: { title: 'A book' },
      structure: [
        {
          structure_type: 'part',
          content: [
            {
              structure_type: '',
              content: [
                { structure_type: 'scene', content: [] },
                { structure_type: 'scene', content: [] },
              ],
            },
          ],
        },
      ],
    }),
  )
  expect((await listRuns(root)).runs[0].sections).toBe(2)
})
