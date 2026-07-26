"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";

import { AlertIcon, CheckIcon, SparkIcon } from "@/components/Icons";
import type { TraceLine } from "@/lib/types";

/**
 * The live agent trace shown inside a processing card.
 *
 * Lines arrive over SSE and are merged by id upstream, so a tool call's "pending"
 * row becomes its "done" row in place rather than appearing twice — the log reads
 * as a checklist filling in, not a console scrolling by.
 */
export function TraceLog({ lines }: { lines: TraceLine[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, [lines]);

  const visible = lines.slice(-40);

  return (
    <div
      ref={scrollerRef}
      className="no-scrollbar relative max-h-44 overflow-y-auto pr-1"
      aria-live="polite"
    >
      <ol className="space-y-1.5">
        <AnimatePresence initial={false}>
          {visible.map((line) => (
            <motion.li
              key={line.id}
              layout="position"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
              className="flex items-start gap-2.5 text-[0.78rem] leading-snug"
            >
              <Marker line={line} />
              <span
                className={
                  line.status === "error"
                    ? "text-danger"
                    : line.kind === "thinking"
                      ? "text-ink-faint italic"
                      : line.status === "pending"
                        ? "text-ink-soft"
                        : "text-ink-soft/90"
                }
              >
                {line.text}
              </span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ol>
      <div ref={endRef} />
    </div>
  );
}

function Marker({ line }: { line: TraceLine }) {
  const base = "mt-[3px] size-3 shrink-0";
  if (line.status === "error") {
    return <AlertIcon className={`${base} text-danger`} />;
  }
  if (line.kind === "thinking") {
    return <SparkIcon className={`${base} text-ink-faint`} />;
  }
  if (line.status === "pending") {
    return (
      <span className={`${base} grid place-items-center`}>
        <motion.span
          className="block size-[6px] rounded-full bg-accent"
          animate={{ opacity: [1, 0.25, 1], scale: [1, 0.82, 1] }}
          transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut" }}
        />
      </span>
    );
  }
  return <CheckIcon className={`${base} text-ink-faint`} />;
}
