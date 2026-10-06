import { readFile, readdir, realpath, stat } from 'node:fs/promises'
import { resolve, relative, sep, extname } from 'node:path'
import { unzipSync } from 'fflate'

const maxBookBytes = 128 * 1024 * 1024
const imageTypes = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.avif': 'image/avif',
}

export class RunError extends Error {
  constructor(message, status = 400) {
    super(message)
    this.status = status
  }
}

async function confined(root, target) {
  const [base, file] = await Promise.all([realpath(root), realpath(target)])
  const rel = relative(base, file)
  if (rel === '..' || rel.startsWith(`..${sep}`) || resolve(base, rel) !== file)
    throw new RunError('This file is outside the ebook runs directory.', 403)
  return file
}

async function runDirectory(root, id) {
  if (!/^[a-zA-Z0-9_-]+$/.test(id)) throw new RunError('Invalid run identifier.')
  return confined(root, resolve(root, id))
}

export async function readBook(root, id) {
  const directory = await runDirectory(root, id)
  const path = await confined(directory, resolve(directory, 'output/storybook.json'))
  const info = await stat(path)
  if (!info.isFile() || info.size > maxBookBytes)
    throw new RunError('This StoryBook is too large to open.')
  return { data: await readFile(path, 'utf8'), modified: info.mtime.toISOString() }
}

function countSections(nodes) {
  return nodes.reduce((count, node) => {
    const first = node.content?.[0]
    const group = first !== null && typeof first === 'object' && 'structure_type' in first
    return count + (group ? countSections(node.content) : 1)
  }, 0)
}

export async function listRuns(root) {
  let entries
  try {
    entries = await readdir(root, { withFileTypes: true })
  } catch (error) {
    if (error.code === 'ENOENT') return { runs: [], problems: [] }
    throw error
  }
  const results = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory())
      .map(async (entry) => {
        try {
          const { data, modified } = await readBook(root, entry.name)
          const book = JSON.parse(data)
          if (
            typeof book.id !== 'string' ||
            typeof book.metadata?.title !== 'string' ||
            !Array.isArray(book.structure) ||
            (book.metadata.authors !== undefined &&
              (!Array.isArray(book.metadata.authors) ||
                book.metadata.authors.some((author) => typeof author !== 'string')))
          )
            throw new Error('Invalid StoryBook')
          return {
            run: {
              runId: entry.name,
              bookId: book.id,
              title: book.metadata.title,
              authors: book.metadata.authors ?? [],
              language: typeof book.metadata.language === 'string' ? book.metadata.language : null,
              coverHref:
                typeof book.metadata.cover_href === 'string' ? book.metadata.cover_href : null,
              sections: countSections(book.structure),
              modified,
            },
          }
        } catch (error) {
          if (error.code === 'ENOENT') return null
          return { problem: `${entry.name}: unable to read storybook.json` }
        }
      }),
  )
  return {
    runs: results
      .flatMap((result) => (result?.run ? [result.run] : []))
      .sort((a, b) => b.modified.localeCompare(a.modified)),
    problems: results.flatMap((result) => (result?.problem ? [result.problem] : [])),
  }
}

export async function readAsset(root, id, reference, cover = false) {
  const directory = await runDirectory(root, id)
  if (
    reference.includes('\\') ||
    reference.includes('\0') ||
    reference.startsWith('/') ||
    reference.split('/').includes('..')
  )
    throw new RunError('Invalid image path.')
  if (!cover && !imageTypes[extname(reference).toLowerCase()])
    throw new RunError('Only book images can be opened.')
  for (const folder of ['output/assets', 'output', 'input']) {
    if (!reference || !imageTypes[extname(reference).toLowerCase()]) continue
    try {
      const path = await confined(directory, resolve(directory, folder, reference))
      const info = await stat(path)
      if (info.isFile() && info.size <= 32 * 1024 * 1024)
        return { data: await readFile(path), type: imageTypes[extname(path).toLowerCase()] }
    } catch (error) {
      if (error instanceof RunError) throw error
      if (!['ENOENT', 'ENOTDIR'].includes(error.code)) throw error
    }
  }
  const input = await confined(directory, resolve(directory, 'input'))
  const files = await readdir(input, { withFileTypes: true })
  for (const file of files.filter((item) => item.isFile())) {
    const path = await confined(input, resolve(input, file.name))
    if ((await stat(path)).size > 256 * 1024 * 1024) continue
    const data = await readFile(path)
    if (data[0] !== 0x50 || data[1] !== 0x4b) continue
    let extracted
    try {
      extracted = unzipSync(data, {
        filter: (entry) => {
          if (
            !imageTypes[extname(entry.name).toLowerCase()] ||
            entry.originalSize > 32 * 1024 * 1024
          )
            return false
          return (
            entry.name === reference ||
            entry.name.endsWith(`/${reference}`) ||
            (cover && /(?:^|\/)cover[^/]*\.(?:jpe?g|png|webp|svg)$/i.test(entry.name))
          )
        },
      })
    } catch {
      continue
    }
    const names = Object.keys(extracted)
    const match =
      names.find((name) => name === reference) ??
      names.find((name) => name.endsWith(`/${reference}`)) ??
      (cover ? names[0] : undefined)
    if (match) return { data: extracted[match], type: imageTypes[extname(match).toLowerCase()] }
  }
  throw new RunError('This image is not included in the ebook run.', 404)
}

export function runMiddleware(root) {
  return async (request, response, next) => {
    const url = new URL(request.url ?? '/', 'http://localhost')
    if (!url.pathname.startsWith('/api/runs')) return next()
    response.setHeader('Cache-Control', 'no-store')
    response.setHeader('X-Content-Type-Options', 'nosniff')
    if (request.method !== 'GET') {
      response.statusCode = 405
      response.end()
      return
    }
    const json = (value) => {
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify(value))
    }
    try {
      if (url.pathname === '/api/runs') {
        json(await listRuns(root))
        return
      }
      const match = /^\/api\/runs\/([^/]+)\/(storybook|assets)$/.exec(url.pathname)
      if (!match) throw new RunError('This run endpoint does not exist.', 404)
      const id = decodeURIComponent(match[1])
      if (match[2] === 'storybook') {
        const { data } = await readBook(root, id)
        response.setHeader('Content-Type', 'application/json')
        response.end(data)
      } else {
        const asset = await readAsset(
          root,
          id,
          url.searchParams.get('path') ?? '',
          url.searchParams.get('cover') === '1',
        )
        response.setHeader('Content-Type', asset.type)
        response.setHeader(
          'Content-Security-Policy',
          "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        )
        response.end(asset.data)
      }
    } catch (error) {
      response.statusCode =
        error instanceof RunError ? error.status : error.code === 'ENOENT' ? 404 : 500
      json({
        error:
          error instanceof RunError
            ? error.message
            : response.statusCode === 404
              ? 'The run or file could not be found. Refresh the library.'
              : 'The ebook run could not be opened.',
      })
    }
  }
}
