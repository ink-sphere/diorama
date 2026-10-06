import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, BookOpen, FolderOpen, RefreshCw, Search, Upload, X } from 'lucide-react'
import { parseStoryBook, type RunSummary, type StoryBook } from './lib/storybook'
import { imageUrl } from './lib/content'
import { position } from './lib/storage'
import { Reader } from './components/Reader'

interface OpenBook {
  book: StoryBook
  runId: string | null
}

function Cover({ run }: { run: RunSummary }) {
  const [failed, setFailed] = useState(false)
  const source = imageUrl(run.runId, run.coverHref ?? 'cover.jpg', true)
  return (
    <div className="book-cover">
      {source && !failed ? (
        <img
          src={source}
          alt={`Cover of ${run.title}`}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="cover-fallback">
          <BookOpen size={24} />
          <span>{run.title}</span>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [problems, setProblems] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [opening, setOpening] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [current, setCurrent] = useState<OpenBook | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const requestId = useRef(0)
  const importId = useRef(0)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/api/runs')
      if (!response.ok)
        throw new Error(
          'The local library is unavailable. Start the app with npm run dev, or open a JSON file.',
        )
      const result = await response.json()
      if (!Array.isArray(result.runs))
        throw new Error('The local library could not be read. You can still open a JSON file.')
      setRuns(result.runs)
      setProblems(result.problems ?? [])
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : 'The library could not be opened.')
    } finally {
      setLoading(false)
    }
  }, [])

  const openRun = useCallback(async (id: string) => {
    const request = ++requestId.current
    setOpening(id)
    setError('')
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(id)}/storybook`)
      if (!response.ok)
        throw new Error((await response.json()).error || 'This run could not be opened.')
      const book = parseStoryBook(await response.json())
      if (request !== requestId.current) return
      const url = new URL(location.href)
      url.search = ''
      url.searchParams.set('run', id)
      const previousSection = new URLSearchParams(location.search).get('section')
      if (new URLSearchParams(location.search).get('run') === id && previousSection)
        url.searchParams.set('section', previousSection)
      history.replaceState(null, '', url)
      setCurrent({ book, runId: id })
    } catch (issue) {
      if (request === requestId.current)
        setError(issue instanceof Error ? issue.message : 'This run could not be opened.')
    } finally {
      if (request === requestId.current) setOpening(null)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const run = new URLSearchParams(location.search).get('run')
    if (run) void openRun(run)
  }, [refresh, openRun])

  async function openFile(file: File) {
    const request = ++requestId.current
    setError('')
    setOpening(`file:${++importId.current}`)
    try {
      if (file.size > 128 * 1024 * 1024)
        throw new Error('This JSON file is larger than 128 MB. Open a smaller StoryBook.')
      const book = parseStoryBook(JSON.parse(await file.text()))
      if (request !== requestId.current) return
      const url = new URL(location.href)
      url.search = ''
      history.replaceState(null, '', url)
      setCurrent({ book, runId: null })
    } catch (issue) {
      if (request === requestId.current)
        setError(
          issue instanceof SyntaxError
            ? 'This file is not valid JSON. Choose a storybook.json from an ebook run.'
            : issue instanceof Error
              ? issue.message
              : 'This file could not be opened.',
        )
    } finally {
      if (request === requestId.current) setOpening(null)
      if (picker.current) picker.current.value = ''
    }
  }

  function back() {
    requestId.current += 1
    setCurrent(null)
    const url = new URL(location.href)
    url.search = ''
    history.replaceState(null, '', url)
    setOpening(null)
  }
  if (current)
    return (
      <Reader
        key={`${current.runId ?? 'file'}:${current.book.id}`}
        book={current.book}
        runId={current.runId}
        onBack={back}
      />
    )
  const filtered = runs.filter((run) =>
    `${run.title} ${run.authors.join(' ')} ${run.runId}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  )
  return (
    <div
      className="library-page"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => {
        event.preventDefault()
        const file = event.dataTransfer.files[0]
        if (file) void openFile(file)
      }}
    >
      <header className="masthead">
        <a href="/" className="wordmark">
          <span className="brand-mark">
            <BookOpen size={21} />
          </span>
          Diorama
          <span className="wordmark-rule" />
          <span className="wordmark-description">a place for stories</span>
        </a>
        <button
          className="button button-quiet"
          onClick={() => picker.current?.click()}
          disabled={!!opening}
        >
          <Upload size={16} /> Open JSON
        </button>
      </header>
      <main className="library-main">
        <div className="library-intro">
          <div>
            <h1>Your reading room.</h1>
            <p>
              The books you’ve brought into focus.
              <br />
              Pick up a story, and find your place.
            </p>
          </div>
          <div className="library-note">
            <span className="reading-dot" />
            <span>
              Your library lives here.
              <br />
              Your reading stays yours.
            </span>
          </div>
        </div>
        <div className="library-toolbar">
          <div className="library-label">
            <h2>Ebook runs</h2>
            <span>
              {runs.length} {runs.length === 1 ? 'book' : 'books'}
            </span>
          </div>
          <div className="library-tools">
            <label className="search-field">
              <Search size={17} />
              <input
                aria-label="Search books"
                placeholder="Find a book or author"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query ? (
                <button
                  className="icon-button"
                  onClick={() => setQuery('')}
                  aria-label="Clear search"
                >
                  <X size={14} />
                </button>
              ) : null}
            </label>
            <button
              className="icon-button"
              aria-label="Refresh library"
              disabled={loading || !!opening}
              onClick={() => {
                setError('')
                void refresh()
              }}
            >
              <RefreshCw size={18} className={loading ? 'spinning' : ''} />
            </button>
          </div>
        </div>
        {error ? (
          <div role="alert" className="error-message">
            <span>{error}</span>
            <button className="icon-button" onClick={() => setError('')} aria-label="Dismiss error">
              <X size={17} />
            </button>
          </div>
        ) : null}
        {loading ? (
          <div className="empty-state" role="status">
            <span className="loading-mark" />
            Opening your library…
          </div>
        ) : filtered.length ? (
          <div className="book-list">
            {filtered.map((run) => {
              const saved = position(`${run.bookId}:${run.runId}`)
              return (
                <button
                  className="book-row"
                  key={run.runId}
                  disabled={!!opening}
                  onClick={() => void openRun(run.runId)}
                >
                  <Cover run={run} />
                  <div className="book-information">
                    <span className="book-author">
                      {run.authors.join(', ') || 'Unknown author'}
                    </span>
                    <h3>{run.title}</h3>
                    <div className="book-details">
                      <span>{run.sections} sections</span>
                      <span className="detail-dot" />
                      <span>
                        {new Intl.DateTimeFormat(undefined, {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        }).format(new Date(run.modified))}
                      </span>
                    </div>
                    <span className="run-name">{run.runId}</span>
                  </div>
                  <span className="read-action">
                    {opening === run.runId
                      ? 'Opening…'
                      : saved
                        ? 'Continue reading'
                        : 'Start reading'}
                    <ArrowRight size={19} />
                  </span>
                </button>
              )
            })}
          </div>
        ) : (
          <div className="empty-state">
            <BookOpen size={30} />
            <h3>{query ? 'No books match your search.' : 'Your next story starts here.'}</h3>
            <p>
              {query
                ? 'Try another title, author, or run name.'
                : 'Completed ebook runs will appear here. You can also open a storybook.json file.'}
            </p>
            <button
              className="button"
              onClick={() => (query ? setQuery('') : picker.current?.click())}
            >
              {query ? 'Clear search' : 'Open a book'}
            </button>
          </div>
        )}
        <div className="import-strip">
          <div>
            <FolderOpen size={20} />
            <span>
              <strong>Bring another story.</strong> Open a storybook.json, or drop it anywhere on
              this page.
            </span>
          </div>
          <button
            className="button button-quiet"
            onClick={() => picker.current?.click()}
            disabled={!!opening}
          >
            Choose a file
            <ArrowRight size={16} />
          </button>
        </div>
        {problems.length ? (
          <details className="library-problems">
            <summary>
              {problems.length} {problems.length === 1 ? 'run needs' : 'runs need'} attention
            </summary>
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </main>
      <footer className="library-footer">
        <span>Diorama</span>
        <span>Good stories deserve a little room.</span>
        <span>Local reading, at your pace.</span>
      </footer>
      <input
        className="visually-hidden"
        ref={picker}
        type="file"
        accept=".json,application/json"
        aria-label="Choose a StoryBook JSON file"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void openFile(file)
        }}
      />
    </div>
  )
}
