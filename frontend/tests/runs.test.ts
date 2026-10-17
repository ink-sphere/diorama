import { mkdtemp, mkdir, readFile, rm, symlink, truncate, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { zipSync, strToU8 } from 'fflate'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  listRuns,
  readAsset,
  readBook,
  readCharacterProfiles,
  runMiddleware,
} from '../server/runs.mjs'

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
          is_part_of_narrative: true,
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
  it('loads only the character profiles artifact from the selected run', async () => {
    await writeFile(join(root, 'run-one/character-profiles.json'), '[]')
    expect(await readCharacterProfiles(root, 'run-one')).toBe('[]')
    await expect(readCharacterProfiles(root, '../outside')).rejects.toThrow('Invalid run')
    await rm(join(root, 'run-one/character-profiles.json'))
    await expect(readCharacterProfiles(root, 'run-one')).rejects.toMatchObject({ code: 'ENOENT' })
    expect((await listRuns(root)).runs).toHaveLength(1)
  })
  it('confines character profile symlinks and limits file size', async () => {
    const path = join(root, 'run-one/character-profiles.json')
    await writeFile(join(root, 'outside.json'), '[]')
    await symlink(join(root, 'outside.json'), path)
    await expect(readCharacterProfiles(root, 'run-one')).rejects.toMatchObject({ status: 403 })
    await rm(path)
    await writeFile(path, '')
    await truncate(path, 16 * 1024 * 1024 + 1)
    await expect(readCharacterProfiles(root, 'run-one')).rejects.toThrow('too large')
  })
  it('serves profiles through a read-only JSON endpoint and reports missing artifacts', async () => {
    const path = join(root, 'run-one/character-profiles.json')
    await writeFile(path, '[]')
    const headers = new Map<string, string>()
    let body = ''
    const response = {
      statusCode: 200,
      setHeader(name: string, value: string) {
        headers.set(name, value)
      },
      end(value = '') {
        body = value
      },
    }
    const middleware = runMiddleware(root)
    const request = { method: 'GET', url: '/api/runs/run-one/characters' }
    await middleware(request, response, () => {})
    expect(body).toBe('[]')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.get('Cache-Control')).toBe('no-store')
    await rm(path)
    await middleware(request, response, () => {})
    expect(response.statusCode).toBe(404)
    await middleware({ ...request, method: 'POST' }, response, () => {})
    expect(response.statusCode).toBe(405)
  })
  it('opens artifact-only runs alongside the legacy layout', async () => {
    const data = await readFile(join(root, 'run-one/output/storybook.json'), 'utf8')
    await mkdir(join(root, 'run-two'))
    await writeFile(join(root, 'run-two/storybook.json'), data)
    expect((await readBook(root, 'run-two')).data).toBe(data)
    const result = await listRuns(root)
    expect(result.runs.map((run) => run.runId).sort()).toEqual(['run-one', 'run-two'])
    expect(result.problems).toEqual([])
    await expect(readAsset(root, 'run-two', 'cover.png')).rejects.toMatchObject({ status: 404 })
  })
  it('does not fall back to a legacy file when the new artifact escapes a run', async () => {
    await writeFile(join(root, 'outside.json'), '{}')
    await symlink(join(root, 'outside.json'), join(root, 'run-one/storybook.json'))
    await expect(readBook(root, 'run-one')).rejects.toThrow('outside')
  })
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
  it('counts only narrative sections in the library', async () => {
    const narrative = {
      structure_type: 'chapter',
      is_part_of_narrative: true,
      content: [{ raw_text: '<p>Text</p>', markdown_text: 'Text' }],
    }
    const supplementary = { ...narrative, is_part_of_narrative: false }
    await writeFile(
      join(root, 'run-one/output/storybook.json'),
      JSON.stringify({
        id: 'book',
        metadata: { title: 'A book' },
        structure: [
          supplementary,
          { ...supplementary, content: [narrative] },
          { ...narrative, content: [supplementary, narrative, narrative] },
          { ...narrative, content: [supplementary] },
        ],
      }),
    )
    expect((await listRuns(root)).runs[0].sections).toBe(2)
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
