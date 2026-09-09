"use client";
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { groups, groupLabels, type Navigation, type NavNode } from "@/lib/book";

function Entry({ node, active, onSelect }: { node: NavNode; active: string; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(active.startsWith(node.id));
  useEffect(() => { if (active.startsWith(`${node.id}/`)) setOpen(true); }, [active, node.id]);
  return <li>
    <div className={`toc-row ${active === node.id ? "selected" : ""}`}>
      {node.children.length > 0 ? <button className="toc-toggle" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${node.title}`} onClick={() => setOpen(!open)}>{open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</button> : <span className="toc-spacer" />}
      <button className="toc-label" aria-current={active === node.id ? "location" : undefined} onClick={() => onSelect(node.id)}><span className="toc-index">{node.index || "·"}</span><span>{node.title || node.type}</span></button>
    </div>
    {open && node.children.length > 0 && <ol>{node.children.map(child => <Entry key={child.id} node={child} active={active} onSelect={onSelect} />)}</ol>}
  </li>;
}
export function Contents({ navigation, active, onSelect }: { navigation: Navigation; active: string; onSelect: (id: string) => void }) {
  return <nav aria-label="Book contents" className="contents">{groups.map(group => navigation[group].length > 0 && <section key={group}>
    <h3>{groupLabels[group]}</h3><ol>{navigation[group].map(node => <Entry key={node.id} node={node} active={active} onSelect={onSelect} />)}</ol>
  </section>)}</nav>;
}
