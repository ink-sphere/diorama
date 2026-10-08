import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  List,
  Moon,
  SlidersHorizontal,
  Sun,
  X,
} from 'lucide-react'
import {
  ancestors,
  buildNavigation,
  isGroup,
  type BookNavigation,
  type StoryBook,
} from '../lib/storybook'
import { renderSection } from '../lib/content'
import { position, preferences, saveStored } from '../lib/storage'
import { Contents } from './Contents'
import { StructureOverview } from './StructureOverview'

interface Props {
  book: StoryBook
  runId: string | null
  onBack: () => void
}

function scrollRatio(container: HTMLDivElement): number {
  const extent = container.scrollHeight - container.clientHeight
  return extent > 1 ? Math.min(1, Math.max(0, container.scrollTop / extent)) : 1
}

export function Reader(props: Props) {
  const { book, onBack } = props
  const navigation = useMemo(() => buildNavigation(book.structure), [book])
  if (!navigation.sections.length) {
    return (
      <div className="reader theme-paper">
        <header className="reader-header">
          <button className="icon-button" onClick={onBack} aria-label="Back to library">
            <ArrowLeft size={19} />
          </button>
          <div className="running-title">
            <strong>{book.metadata.title}</strong>
            <span>{book.metadata.authors.join(', ') || 'Unknown author'}</span>
          </div>
        </header>
        <main className="reading-pane">
          <article className="book-article">
            <p className="empty-section">This book has no narrative sections to read.</p>
          </article>
        </main>
      </div>
    )
  }
  return <NarrativeReader {...props} navigation={navigation} />
}

function NarrativeReader({
  book,
  runId,
  onBack,
  navigation,
}: Props & { navigation: BookNavigation }) {
  const sections = navigation.sections
  const storageKey = `${book.id}:${runId ?? 'file'}`
  const saved = useMemo(() => position(storageKey), [storageKey])
  const requested = new URLSearchParams(location.search).get('section')
  const [selected, setSelected] = useState(
    () =>
      (requested && navigation.entries.has(requested) ? requested : undefined) ??
      (saved && navigation.entries.has(saved.path) ? saved.path : undefined) ??
      sections[0].path,
  )
  const [prefs, setPrefs] = useState(preferences)
  const [contentsOpen, setContentsOpen] = useState(false)
  const [ratio, setRatio] = useState(saved?.path === selected ? saved.ratio : 0)
  const pane = useRef<HTMLDivElement>(null)
  const resumeRatio = useRef(ratio)
  const latestPosition = useRef({ path: selected, ratio, updated: Date.now() })
  const pendingAnchor = useRef<string | null>(null)
  const entry = navigation.entries.get(selected)!
  const group = isGroup(entry.node)
  const index = entry.firstSection
  const section = sections[index]
  const html = useMemo(() => (group ? '' : renderSection(section, runId)), [group, section, runId])
  const breadcrumb = ancestors(navigation, selected)
  const siblings =
    entry.parentPath === null
      ? navigation.roots
      : navigation.entries.get(entry.parentPath)!.children
  const siblingIndex = siblings.indexOf(selected)
  const previous = group ? navigation.entries.get(siblings[siblingIndex - 1]) : sections[index - 1]
  const next = group ? navigation.entries.get(siblings[siblingIndex + 1]) : sections[index + 1]
  const totalWords = useMemo(() => sections.reduce((sum, item) => sum + item.words, 0), [sections])
  const precedingWords = sections.slice(0, index).reduce((sum, item) => sum + item.words, 0)
  const progress = Math.round(
    100 *
      (totalWords
        ? (precedingWords + (group ? 0 : section.words * ratio)) / totalWords
        : (index + (group ? 0 : ratio)) / sections.length),
  )
  const minutes = Math.max(1, Math.ceil(entry.words / 220))

  function select(path: string) {
    setContentsOpen(false)
    if (path === selected) return
    rememberPosition()
    resumeRatio.current = 0
    latestPosition.current = { path, ratio: 0, updated: Date.now() }
    setRatio(0)
    setSelected(path)
    saveStored(`position:${storageKey}`, latestPosition.current)
  }

  function rememberPosition() {
    if (pane.current) {
      latestPosition.current = {
        path: selected,
        ratio: scrollRatio(pane.current),
        updated: Date.now(),
      }
      saveStored(`position:${storageKey}`, latestPosition.current)
    }
  }

  function leave() {
    rememberPosition()
    onBack()
  }

  useEffect(() => {
    saveStored('preferences', prefs)
  }, [prefs])
  useEffect(() => {
    if (runId) {
      const url = new URL(location.href)
      url.searchParams.set('section', selected)
      history.replaceState(null, '', url)
    }
    const container = pane.current
    if (!container) return
    const frame = requestAnimationFrame(() => {
      container.scrollTop =
        resumeRatio.current * Math.max(0, container.scrollHeight - container.clientHeight)
      const anchor = pendingAnchor.current
      if (anchor) {
        container.querySelector(`[id="${CSS.escape(anchor)}"]`)?.scrollIntoView({ block: 'start' })
        pendingAnchor.current = null
      }
      container.focus({ preventScroll: true })
    })
    return () => cancelAnimationFrame(frame)
  }, [selected, runId])

  useEffect(() => {
    const container = pane.current
    if (!container) return
    let timer: ReturnType<typeof setTimeout> | undefined
    function update() {
      if (latestPosition.current.path !== selected) return
      setRatio(latestPosition.current.ratio)
      saveStored(`position:${storageKey}`, latestPosition.current)
    }
    function scroll() {
      if (!container || latestPosition.current.path !== selected) return
      latestPosition.current = {
        path: selected,
        ratio: scrollRatio(container),
        updated: Date.now(),
      }
      resumeRatio.current = latestPosition.current.ratio
      clearTimeout(timer)
      timer = setTimeout(update, 120)
    }
    function pagehide() {
      scroll()
      update()
    }
    container.addEventListener('scroll', scroll, { passive: true })
    window.addEventListener('pagehide', pagehide)
    const resize = new ResizeObserver(scroll)
    if (container.firstElementChild) resize.observe(container.firstElementChild)
    return () => {
      container.removeEventListener('scroll', scroll)
      window.removeEventListener('pagehide', pagehide)
      resize.disconnect()
      clearTimeout(timer)
      if (latestPosition.current.path === selected) {
        saveStored(`position:${storageKey}`, latestPosition.current)
      }
    }
  }, [selected, storageKey])

  function followAnchor(event: React.MouseEvent) {
    const target = event.target instanceof Element ? event.target.closest('a') : null
    const href = target?.getAttribute('href')
    if (!href?.startsWith('#')) return
    let anchor: string
    try {
      anchor = decodeURIComponent(href.slice(1))
    } catch {
      return
    }
    event.preventDefault()
    const local = pane.current?.querySelector(`[id="${CSS.escape(anchor)}"]`)
    if (local) {
      local.scrollIntoView({ block: 'start' })
      return
    }
    const destination = sections.find((item) =>
      item.content.some((fragment) => {
        const template = document.createElement('template')
        template.innerHTML = fragment.raw_text
        return !!template.content.querySelector(`[id="${CSS.escape(anchor)}"]`)
      }),
    )
    if (destination) {
      pendingAnchor.current = anchor
      select(destination.path)
    }
  }

  const titleInContent =
    !group && /<h[1-6][\s>]/i.test(section.content.map((item) => item.raw_text).join(''))
  return (
    <div
      className={`reader theme-${prefs.theme}`}
      style={{ '--reading-size': `${prefs.fontSize}px` } as CSSProperties}
    >
      <header className="reader-header">
        <button className="icon-button" onClick={leave} aria-label="Back to library">
          <ArrowLeft size={19} />
        </button>
        <div className="running-title">
          <strong>{book.metadata.title}</strong>
          <span>{book.metadata.authors.join(', ') || 'Unknown author'}</span>
        </div>
        <div className="reader-actions">
          <button
            className={`icon-button contents-toggle ${contentsOpen ? 'active' : ''}`}
            onClick={() => setContentsOpen(!contentsOpen)}
            aria-label="Toggle contents"
            aria-expanded={contentsOpen}
          >
            <List size={19} />
          </button>
          <details className="settings">
            <summary aria-label="Reading settings">
              <SlidersHorizontal size={19} />
            </summary>
            <div className="settings-panel">
              <h3>Make yourself comfortable</h3>
              <label>
                Text size <span>{prefs.fontSize}px</span>
              </label>
              <input
                aria-label="Text size"
                type="range"
                min="15"
                max="28"
                value={prefs.fontSize}
                onChange={(event) => setPrefs({ ...prefs, fontSize: Number(event.target.value) })}
              />
              <span className="setting-label">Typeface</span>
              <div className="segmented">
                <button
                  aria-pressed={prefs.font === 'serif'}
                  onClick={() => setPrefs({ ...prefs, font: 'serif' })}
                >
                  Literata
                </button>
                <button
                  aria-pressed={prefs.font === 'sans'}
                  onClick={() => setPrefs({ ...prefs, font: 'sans' })}
                >
                  Work Sans
                </button>
              </div>
              <span className="setting-label">Reading theme</span>
              <div className="segmented">
                <button
                  aria-pressed={prefs.theme === 'paper'}
                  onClick={() => setPrefs({ ...prefs, theme: 'paper' })}
                >
                  <Sun size={15} /> Paper
                </button>
                <button
                  aria-pressed={prefs.theme === 'night'}
                  onClick={() => setPrefs({ ...prefs, theme: 'night' })}
                >
                  <Moon size={15} /> Night
                </button>
              </div>
            </div>
          </details>
        </div>
      </header>
      <div className="reader-layout">
        <aside
          className={`reader-sidebar ${contentsOpen ? 'is-open' : ''}`}
          aria-label="Book contents"
        >
          <div className="sidebar-heading">
            <h2>Contents</h2>
            <button
              className="icon-button contents-close"
              onClick={() => setContentsOpen(false)}
              aria-label="Close contents"
            >
              <X size={18} />
            </button>
          </div>
          <p className="sidebar-caption">
            {sections.length} sections · {Math.ceil(totalWords / 220)} min read
          </p>
          <Contents navigation={navigation} selected={selected} onSelect={select} />
          <div className="sidebar-foot">
            <span className="reading-dot" /> Your place is saved on this device.
          </div>
        </aside>
        <div className="reading-pane" ref={pane} tabIndex={-1}>
          <article
            className={`book-article font-${prefs.font}`}
            lang={book.metadata.language || undefined}
          >
            <nav className="structure-breadcrumb" aria-label="Structure breadcrumb">
              {breadcrumb.map((parent) => (
                <span key={parent.path}>
                  <button onClick={() => select(parent.path)} aria-label={`Go to ${parent.title}`}>
                    {parent.title}
                  </button>
                  <ChevronRight size={12} />
                </span>
              ))}
              <span aria-current="location">{entry.title}</span>
            </nav>
            <div className="section-meta">
              <span>
                {entry.node.structure_type.replaceAll('_', ' ')}
                {!entry.node.is_part_of_narrative ? ' · supplementary' : ''}
              </span>
              <span>{minutes} min read</span>
            </div>
            {group ? (
              <StructureOverview entry={entry} navigation={navigation} onSelect={select} />
            ) : (
              <>
                {titleInContent ? null : <h1 className="section-title">{entry.title}</h1>}
                {section.content.length ? (
                  <div
                    className="book-content"
                    onClick={followAnchor}
                    onErrorCapture={(event) => {
                      const image = event.target
                      if (image instanceof HTMLImageElement) {
                        const text = document.createElement('span')
                        text.className = 'missing-image'
                        text.textContent = image.alt || 'Image unavailable'
                        image.replaceWith(text)
                      }
                    }}
                    dangerouslySetInnerHTML={{ __html: html }}
                  />
                ) : (
                  <p className="empty-section">This section contains no text.</p>
                )}
              </>
            )}
            <nav className="section-navigation" aria-label="Section navigation">
              <button disabled={!previous} onClick={() => previous && select(previous.path)}>
                <ArrowLeft size={17} />
                <span>Previous section{previous ? <strong>{previous.title}</strong> : null}</span>
              </button>
              <button disabled={!next} onClick={() => next && select(next.path)}>
                <span>
                  Next section
                  {next ? (
                    <strong>{next.title}</strong>
                  ) : !group ? (
                    <strong>
                      <Check size={15} /> End of book
                    </strong>
                  ) : null}
                </span>
                <ArrowRight size={17} />
              </button>
            </nav>
          </article>
        </div>
      </div>
      <footer className="reader-footer">
        <span className="footer-section">{entry.title}</span>
        <div className="reader-progress">
          <span>{progress}%</span>
          <progress aria-label="Book progress" max={100} value={progress} />
        </div>
        <div className="step-controls">
          <button
            className="icon-button"
            disabled={!previous}
            onClick={() => previous && select(previous.path)}
            aria-label="Previous section"
          >
            <ChevronLeft size={18} />
          </button>
          <span>
            {group ? `${index + 1}–${entry.lastSection + 1}` : index + 1} / {sections.length}
          </span>
          <button
            className="icon-button"
            disabled={!next}
            onClick={() => next && select(next.path)}
            aria-label="Next section"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </footer>
    </div>
  )
}
