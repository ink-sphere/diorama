import { useEffect, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { isGroup, nodeTitle, type StructureNode } from '../lib/storybook'

interface Props {
  nodes: StructureNode[]
  selected: string
  onSelect: (path: string) => void
  prefix?: string
}

function ContentsNode({
  node,
  path,
  selected,
  onSelect,
}: {
  node: StructureNode
  path: string
  selected: string
  onSelect: (path: string) => void
}) {
  const group = isGroup(node)
  const within = selected === path || selected.startsWith(`${path}.`)
  const [expanded, setExpanded] = useState(within)
  useEffect(() => {
    if (within) setExpanded(true)
  }, [selected, within])
  const title = nodeTitle(node)
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
        <Contents nodes={node.content} prefix={path} selected={selected} onSelect={onSelect} />
      ) : null}
    </li>
  )
}

export function Contents({ nodes, selected, onSelect, prefix = '' }: Props) {
  return (
    <ol className="contents-list">
      {nodes.map((node, index) => {
        const path = prefix ? `${prefix}.${index}` : `${index}`
        return (
          <ContentsNode
            key={path}
            node={node}
            path={path}
            selected={selected}
            onSelect={onSelect}
          />
        )
      })}
    </ol>
  )
}
