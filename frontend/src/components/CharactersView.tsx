import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Search, UsersRound, X } from 'lucide-react'
import { parseCharacterProfiles, searchCharacters, type CharacterProfile } from '../lib/characters'

type LoadState =
  | { status: 'loading' }
  | { status: 'missing' }
  | { status: 'error'; message: string }
  | { status: 'ready'; profiles: CharacterProfile[] }

export function CharactersView({
  bookId,
  runId,
  active,
  onBack,
}: {
  bookId: string
  runId: string | null
  active: boolean
  onBack: () => void
}) {
  const [opened, setOpened] = useState(active)
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const profilePane = useRef<HTMLDivElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const lastSelection = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (active) setOpened(true)
  }, [active])

  useEffect(() => {
    if (!opened || !runId) return
    const controller = new AbortController()
    setState({ status: 'loading' })
    async function load() {
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId!)}/characters`, {
          signal: controller.signal,
        })
        if (response.status === 404) {
          if (!controller.signal.aborted) setState({ status: 'missing' })
          return
        }
        if (!response.ok) throw new Error('The character profiles could not be loaded.')
        const profiles = parseCharacterProfiles(await response.json(), bookId)
        const order = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true })
        profiles.sort((a, b) => order.compare(a.character_name, b.character_name))
        if (!controller.signal.aborted) setState({ status: 'ready', profiles })
      } catch (issue) {
        if (!controller.signal.aborted)
          setState({
            status: 'error',
            message:
              issue instanceof SyntaxError
                ? 'The character profiles file is not valid JSON.'
                : issue instanceof Error
                  ? issue.message
                  : 'The character profiles could not be loaded.',
          })
      }
    }
    void load()
    return () => controller.abort()
  }, [opened, runId, bookId, attempt])

  const profiles = state.status === 'ready' ? state.profiles : []
  const results = useMemo(() => searchCharacters(profiles, query), [profiles, query])
  const selected = profiles.find((profile) => profile.id === selectedId)

  useEffect(() => {
    if (selectedId && active) {
      if (profilePane.current) profilePane.current.scrollTop = 0
      heading.current?.focus({ preventScroll: true })
    }
  }, [selectedId, active])

  function retry() {
    setSelectedId(null)
    setAttempt((value) => value + 1)
  }

  function backToList() {
    setSelectedId(null)
    requestAnimationFrame(() => lastSelection.current?.focus({ preventScroll: true }))
  }

  let message: { title: string; description: string } | null = null
  if (!runId)
    message = {
      title: 'Open this book from your library',
      description:
        'Character profiles are loaded alongside books in the local library. A standalone StoryBook file does not include them.',
    }
  else if (state.status === 'missing')
    message = {
      title: 'No character profiles yet',
      description:
        'This book hasn’t been profiled yet. Once its profiles are ready, reload them here.',
    }
  else if (state.status === 'error')
    message = {
      title: 'Unable to open character profiles',
      description: state.message,
    }
  else if (state.status === 'ready' && !profiles.length)
    message = {
      title: 'No characters found',
      description: 'The profiles file for this book contains no characters.',
    }

  return (
    <section className="characters-view" hidden={!active} aria-label="Characters">
      {message ? (
        <div className="characters-state" role={state.status === 'error' ? 'alert' : undefined}>
          <UsersRound size={30} strokeWidth={1.4} aria-hidden="true" />
          <h1>{message.title}</h1>
          <p>{message.description}</p>
          <button className="button button-quiet" onClick={runId ? retry : onBack}>
            {runId ? 'Reload profiles' : 'Back to library'}
          </button>
        </div>
      ) : state.status === 'loading' ? (
        <div className="characters-state" role="status">
          <span className="loading-mark" aria-hidden="true" />
          Loading character profiles…
        </div>
      ) : (
        <div className={`characters-layout ${selected ? 'has-selection' : ''}`}>
          <aside className="characters-sidebar" aria-label="Character list">
            <div className="characters-list-heading">
              <h2>Characters</h2>
              <span>{profiles.length}</span>
            </div>
            <label className="character-search">
              <Search size={16} aria-hidden="true" />
              <input
                aria-label="Search characters"
                placeholder="Find a name or alias"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              {query ? (
                <button
                  className="icon-button"
                  aria-label="Clear character search"
                  onClick={() => setQuery('')}
                >
                  <X size={15} />
                </button>
              ) : null}
            </label>
            <p className="characters-caption">Profiles may reveal events from the whole book.</p>
            <p className="visually-hidden" role="status">
              {results.length} matching characters
            </p>
            {results.length ? (
              <ul className="character-list">
                {results.map((profile) => (
                  <li key={profile.id}>
                    <button
                      aria-current={selectedId === profile.id ? 'true' : undefined}
                      aria-label={`View ${profile.character_name}`}
                      onClick={(event) => {
                        lastSelection.current = event.currentTarget
                        setSelectedId(profile.id)
                      }}
                    >
                      <span>
                        <strong>{profile.character_name}</strong>
                        {profile.character_alias.length ? (
                          <span>{profile.character_alias.join(' · ')}</span>
                        ) : null}
                      </span>
                      <ArrowRight size={15} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="character-no-results">
                <p>No characters match “{query}”.</p>
                <button className="button button-quiet" onClick={() => setQuery('')}>
                  Clear search
                </button>
              </div>
            )}
          </aside>
          <div className="character-pane" ref={profilePane}>
            {selected ? (
              <article className="character-profile" key={selected.id}>
                <button className="character-back" onClick={backToList}>
                  <ArrowLeft size={16} /> All characters
                </button>
                <h1 ref={heading} tabIndex={-1}>
                  {selected.character_name}
                </h1>
                {selected.character_alias.length ? (
                  <div className="character-aliases">
                    <span>Also known as</span>
                    <p>{selected.character_alias.join(', ')}</p>
                  </div>
                ) : null}
                <section className="character-description" aria-label="In the story">
                  <h2>In the story</h2>
                  {selected.character_profile_description
                    .split(/\n\s*\n/)
                    .map((paragraph, index) => (
                      <p key={index}>{paragraph}</p>
                    ))}
                </section>
                <details className="character-appearance">
                  <summary>Appearance</summary>
                  <p>{selected.headshot_visual_prompt}</p>
                </details>
              </article>
            ) : (
              <div className="characters-state character-introduction">
                <UsersRound size={36} strokeWidth={1.3} aria-hidden="true" />
                <h1>A story’s familiar faces.</h1>
                <p>Select a character to explore their story, other names, and appearance.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
