export type ReaderView = 'read' | 'characters'

export function ReaderViews({
  view,
  onChange,
}: {
  view: ReaderView
  onChange: (view: ReaderView) => void
}) {
  return (
    <nav className="reader-views" aria-label="Book views">
      <button aria-pressed={view === 'read'} onClick={() => onChange('read')}>
        Read
      </button>
      <button aria-pressed={view === 'characters'} onClick={() => onChange('characters')}>
        Characters
      </button>
    </nav>
  )
}
