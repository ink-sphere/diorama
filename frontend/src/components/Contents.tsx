import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { type BookNavigation, type StructureEntry } from '../lib/storybook'

interface Props {
  navigation: BookNavigation
  selected: string
  onSelect: (path: string) => void
  paths?: string[]
}

function ContentsNode({
  entry,
  navigation,
  selected,
  onSelect,
}: {
  entry: StructureEntry
  navigation: BookNavigation
  selected: string
  onSelect: (path: string) => void
}) {
  const { node, path, title } = entry
  const group = entry.children.length > 0
  const within = selected === path || selected.startsWith(`${path}.`)
  const [expanded, setExpanded] = useState(within)
  useEffect(() => {
    if (within) setExpanded(true)
  }, [selected, within])
  return (
    <li>
      <div className={`contents-row ${group && within ? 'contains-current' : ''}`}>
        {group ? (
          <button
            className="contents-disclosure"
            aria-label={`${expanded ? 'Collapse' : 'Expand'} ${title}`}
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <ChevronRight size={14} />
          </button>
        ) : (
          <span className="contents-spacer" />
        )}
        <button
          type="button"
          className="contents-node"
          aria-label={title}
          aria-current={selected === path ? 'location' : undefined}
          onClick={() => {
            if (group) setExpanded(true)
            onSelect(path)
          }}
        >
          <span>{title}</span>
          <span className="contents-type">
            {node.structure_type.replaceAll('_', ' ')}
            {!node.is_part_of_narrative ? ' · supplementary' : ''}
          </span>
        </button>
      </div>
      {group && expanded ? (
        <Contents
          navigation={navigation}
          paths={entry.children}
          selected={selected}
          onSelect={onSelect}
        />
      ) : null}
    </li>
  )
}

export function Contents({ navigation, selected, onSelect, paths = navigation.roots }: Props) {
  return (
    <ol className="contents-list">
      {paths.map((path) => {
        return (
          <ContentsNode
            key={path}
            entry={navigation.entries.get(path)!}
            navigation={navigation}
            selected={selected}
            onSelect={onSelect}
          />
        )
      })}
    </ol>
  )
}
