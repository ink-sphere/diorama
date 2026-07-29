"use client";

import { motion } from "framer-motion";
import type { RefObject } from "react";

import { ImageIcon } from "@/components/Icons";
import type { Section } from "@/lib/structure";
import type { ReaderPrefs } from "@/lib/useReaderPrefs";

/**
 * The double-page spread: one sheet of paper, text verso, plate recto.
 *
 * The text page is a viewport over a multi-column box (see `usePagination`); a page
 * turn translates that box by exactly one column width, which is why the animation
 * is on `x` and never on opacity — the paper doesn't move, the text does.
 *
 * Scenes are the page unit: each is its own block with a forced column break before
 * it (`.scene-break`), so a scene always opens a page, overflows onto as many as it
 * needs, and the next one starts on the page after it ends. Nothing here computes
 * that — the browser fits scenes to columns exactly as it fits words to lines.
 */
export function Spread({
  section,
  bookTitle,
  page,
  pageCount,
  pageWidth,
  sceneIndex,
  measured,
  viewportRef,
  contentRef,
  prefs,
  showPlate,
}: {
  section: Section;
  bookTitle: string;
  page: number;
  pageCount: number;
  pageWidth: number;
  /** Which scene the current page is inside; -1 before the first measurement. */
  sceneIndex: number;
  measured: boolean;
  viewportRef: RefObject<HTMLDivElement | null>;
  contentRef: RefObject<HTMLDivElement | null>;
  prefs: ReaderPrefs;
  showPlate: boolean;
}) {
  const fontSize = `${1.0625 * prefs.fontScale}rem`;

  return (
    <div className="relative flex h-full w-full overflow-hidden rounded-[4px] bg-paper shadow-page ring-1 ring-rule page-surface">
      <div className="relative flex min-w-0 flex-1 flex-col px-[6%] py-[4.5%] lg:px-[8%]">
        <div
          className="relative mx-auto flex w-full min-h-0 flex-1 flex-col"
          style={{ fontSize, maxWidth: `${prefs.measure}ch` }}
        >
          <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-hidden">
            <motion.div
              ref={contentRef}
              className={`typeset absolute inset-0 ${
                prefs.family === "sans" ? "font-sans" : "font-serif"
              }`}
              data-opening={page === 0 ? "true" : "false"}
              style={{ lineHeight: prefs.lineHeight, fontSize }}
              animate={{ x: -page * pageWidth }}
              initial={false}
              transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
            >
              {/* The heading flows with the text instead of sitting above the
                  viewport, so it appears on the section's first page only. */}
              <h2
                className="mb-[1.1em] font-medium tracking-[-0.01em] text-ink"
                style={{ fontSize: "1.55em", lineHeight: 1.18 }}
              >
                {section.heading}
              </h2>
              {section.scenes.map((paragraphs, index) => (
                <div
                  key={index}
                  data-scene={index}
                  className={index > 0 ? "scene-break" : undefined}
                >
                  {paragraphs.map((paragraph, position) => (
                    <p key={position}>{paragraph}</p>
                  ))}
                  {index < section.scenes.length - 1 ? (
                    <p className="scene-end" aria-hidden>
                      ❦
                    </p>
                  ) : null}
                </div>
              ))}
            </motion.div>
          </div>

          <div className="mt-[2.2%] flex shrink-0 items-baseline justify-between pt-3">
            <span className="label text-ink-faint">{section.label}</span>
            <span className="label tabular-nums text-ink-faint">
              {measured ? `${page + 1} / ${pageCount}` : "—"}
            </span>
          </div>
        </div>
      </div>

      {showPlate ? (
        <>
          {/* The gutter: two facing pages, not one wide one. */}
          <div className="relative w-px shrink-0 bg-rule">
            <div
              className="pointer-events-none absolute inset-y-0 -left-8 w-8 bg-gradient-to-r from-transparent to-black/[0.045] dark:to-black/25"
              aria-hidden
            />
            <div
              className="pointer-events-none absolute inset-y-0 -right-8 w-8 bg-gradient-to-l from-transparent to-black/[0.045] dark:to-black/25"
              aria-hidden
            />
          </div>
          <PlatePage
            section={section}
            bookTitle={bookTitle}
            sceneIndex={sceneIndex}
            sceneCount={section.scenes.length}
          />
        </>
      ) : null}
    </div>
  );
}

/**
 * The recto plate.
 *
 * Diorama doesn't generate illustrations yet, so this is a reserved, designed slot
 * rather than a picture: an empty plate on laid paper, captioned with what it is
 * waiting for. When images do exist, only the inside of the frame changes.
 *
 * It tracks the *scene*, not the section — a scene is what an illustration will
 * depict, so the plate changes as you turn into the next one. A section with a single
 * scene (or none to segment) names the section instead: "scene 1 of 1" is a fact
 * about the data, not something worth telling the reader.
 */
function PlatePage({
  section,
  bookTitle,
  sceneIndex,
  sceneCount,
}: {
  section: Section;
  bookTitle: string;
  sceneIndex: number;
  sceneCount: number;
}) {
  const subject =
    sceneCount > 1 && sceneIndex >= 0
      ? `scene ${sceneIndex + 1} of ${sceneCount}`
      : section.heading;

  return (
    <div className="relative hidden min-w-0 flex-1 flex-col items-center justify-center px-[8%] py-[4.5%] lg:flex">
      <motion.figure
        key={`${section.index}-${Math.max(sceneIndex, 0)}`}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        // The percentage keeps the plate at the proportion it has always had on a
        // normal window; the rem cap stops it ballooning on a very wide one.
        className="flex w-full max-w-[min(32rem,72%)] flex-col items-center"
      >
        <div className="relative aspect-[4/5] w-full border border-rule p-2">
          <div className="flex h-full w-full flex-col items-center justify-center gap-4 border border-rule bg-shell/40">
            <ImageIcon className="size-7 text-ink-faint/70" />
            <p className="label text-ink-faint/80">Plate reserved</p>
          </div>
        </div>
        <figcaption className="mt-5 text-center font-serif text-[0.88rem] leading-relaxed text-ink-soft italic">
          An illustration for {subject} will sit here.
          <span className="label mt-2 block text-ink-faint not-italic">
            {sceneCount > 1 && sceneIndex >= 0 ? `${section.heading} · ` : ""}
            {bookTitle}
          </span>
        </figcaption>
      </motion.figure>
    </div>
  );
}
