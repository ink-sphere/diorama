"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";

import type { TocNode } from "@/lib/structure";

/**
 * The book's own structure as navigation.
 *
 * Every entry is reachable — Diorama has no notion of locked or gated chapters, so
 * the tree is a map of the book, not a progress gate. Branches (an act, a part) are
 * headings that jump to their first readable section rather than separate targets.
 */
export function TocSidebar({
  open,
  toc,
  currentSection,
  onSelect,
}: {
  open: boolean;
  toc: TocNode[];
  currentSection: number;
  onSelect: (sectionIndex: number) => void;
}) {
  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.nav
          key="toc"
          aria-label="Table of contents"
          initial={{ width: 0, opacity: 0 }}
          animate={{ width: 288, opacity: 1 }}
          exit={{ width: 0, opacity: 0 }}
          transition={{ duration: 0.34, ease: [0.16, 1, 0.3, 1] }}
          className="relative z-10 shrink-0 overflow-hidden border-r border-rule bg-shell"
        >
          <div className="h-full w-72 overflow-y-auto px-3 py-5">
            <p className="label px-3 pb-3 text-ink-faint">Contents</p>
            <ul className="space-y-px">
              {toc.map((node) => (
                <TocRow
                  key={node.key}
                  node={node}
                  currentSection={currentSection}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          </div>
        </motion.nav>
      ) : null}
    </AnimatePresence>
  );
}

function TocRow({
  node,
  currentSection,
  onSelect,
}: {
  node: TocNode;
  currentSection: number;
  onSelect: (sectionIndex: number) => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const isCurrent = node.children.length === 0 && node.sectionIndex === currentSection;
  const containsCurrent = node.children.length > 0 && contains(node, currentSection);

  // Keep the reader's place visible when the sidebar opens on a long book.
  useEffect(() => {
    if (isCurrent) {
      ref.current?.scrollIntoView({ block: "nearest" });
    }
  }, [isCurrent]);

  return (
    <li>
      <button
        ref={ref}
        type="button"
        onClick={() => node.sectionIndex !== null && onSelect(node.sectionIndex)}
        aria-current={isCurrent ? "true" : undefined}
        style={{ paddingLeft: `${0.75 + node.depth * 0.85}rem` }}
        className={`relative w-full rounded-[3px] py-2 pr-3 text-left text-[0.86rem] leading-snug transition-colors ${
          isCurrent
            ? "bg-shell-raised text-ink"
            : containsCurrent
              ? "text-ink"
              : "text-ink-soft hover:bg-shell-raised/70 hover:text-ink"
        }`}
      >
        {isCurrent ? (
          <motion.span
            layoutId="toc-marker"
            className="absolute top-2 bottom-2 left-0 w-[2px] rounded-full bg-accent"
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          />
        ) : null}
        <span className={node.depth === 0 ? "font-medium" : ""}>{node.heading}</span>
      </button>
      {node.children.length > 0 ? (
        <ul className="space-y-px">
          {node.children.map((child) => (
            <TocRow
              key={child.key}
              node={child}
              currentSection={currentSection}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function contains(node: TocNode, sectionIndex: number): boolean {
  return node.children.some(
    (child) => child.sectionIndex === sectionIndex || contains(child, sectionIndex),
  );
}
