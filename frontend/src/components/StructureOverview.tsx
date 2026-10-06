import { ArrowRight, BookOpen, ChevronRight } from 'lucide-react'
import type { BookNavigation, StructureEntry } from '../lib/storybook'

export function StructureOverview({
  entry,
  navigation,
  onSelect,
}: {
  entry: StructureEntry
  navigation: BookNavigation
  onSelect: (path: string) => void
}) {
  const sectionCount = entry.lastSection - entry.firstSection + 1
  return (
    <div className="structure-overview">
      <h1 className="section-title">{entry.title}</h1>
      <p className="overview-description">
        {sectionCount} {sectionCount === 1 ? 'section' : 'sections'} in this{' '}
        {entry.node.structure_type.replaceAll('_', ' ') || 'group'}. Choose where to begin.
      </p>
      <button
        className="button overview-read"
        onClick={() => onSelect(navigation.sections[entry.firstSection].path)}
      >
        <BookOpen size={16} /> Read from beginning <ArrowRight size={16} />
      </button>
      <ol className="overview-children">
        {entry.children.map((path) => {
          const child = navigation.entries.get(path)!
          const count = child.lastSection - child.firstSection + 1
          return (
            <li key={path}>
              <button aria-label={`Open ${child.title}`} onClick={() => onSelect(path)}>
                <span>
                  <strong>{child.title}</strong>
                  <span className="overview-child-meta">
                    {child.node.structure_type.replaceAll('_', ' ')}
                    {child.children.length
                      ? ` · ${count} ${count === 1 ? 'section' : 'sections'}`
                      : ''}
                    {!child.node.is_part_of_narrative ? ' · supplementary' : ''}
                  </span>
                </span>
                <ChevronRight size={18} />
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
