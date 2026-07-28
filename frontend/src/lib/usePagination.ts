"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";

/**
 * Measured, reflow-based pagination.
 *
 * The section's text is laid out in a CSS multi-column box whose column width is
 * the page width and whose height is the page height; the browser breaks the text
 * into columns for us — mid-paragraph, hyphenated, exactly where a typesetter would.
 * One column is one page, and turning a page is a horizontal translate. Page *count*
 * is therefore measured (`scrollWidth`), never estimated, so it stays correct across
 * type-size, line-height and viewport changes.
 *
 * The column width is applied to the element imperatively inside the same frame it
 * is measured in. Doing it through React state instead would need a second render
 * before `scrollWidth` meant anything, and the first paint would report one
 * oversized column.
 */
interface PaginationResult {
  /** Number of columns the text currently breaks into (never below 1). */
  pageCount: number;
  /** Width of one column in px — the distance one page turn translates. */
  pageWidth: number;
  /**
   * The page each scene opens on, in order — `scenePages[2] === 5` means scene 2
   * starts on page 5. Measured like the page count is: a scene forces a column break,
   * so where it landed is a fact about the layout, not something to compute from
   * character counts.
   */
  scenePages: number[];
  /** False until the first successful measurement, to avoid a flash of page 1. */
  measured: boolean;
  /** Force a re-measure. */
  remeasure: () => void;
}

/** Stable-identity setter: a fresh array every measure would re-fire every consumer. */
function sameNumbers(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

export function usePagination(
  viewportRef: RefObject<HTMLElement | null>,
  contentRef: RefObject<HTMLElement | null>,
  /** Anything that changes the typesetting: section text, type size, measure. */
  signature: string,
): PaginationResult {
  const [pageCount, setPageCount] = useState(1);
  const [pageWidth, setPageWidth] = useState(0);
  const [scenePages, setScenePages] = useState<number[]>([]);
  const [measured, setMeasured] = useState(false);
  const [nonce, setNonce] = useState(0);

  const remeasure = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    const viewport = viewportRef.current;
    const content = contentRef.current;
    if (!viewport || !content) return;

    let frame = 0;
    let cancelled = false;

    const measure = () => {
      if (cancelled) return;
      const box = viewport.getBoundingClientRect();
      // Floor to whole pixels and pin the column box to that exact size. The
      // viewport's real width is usually fractional (percentage padding, a `ch`
      // max-width), and the browser lays columns out at that fractional width while
      // `clientWidth` reports a rounded integer — so translating by the integer
      // falls a fraction of a pixel short *per page*. By the seventh page the
      // shortfall is several pixels and the previous column bleeds in at the left
      // edge. Forcing integer geometry makes the stride and the translate the same
      // number by construction.
      const width = Math.floor(box.width);
      const height = Math.floor(box.height);
      if (width <= 0 || height <= 0) return;

      // One column exactly as wide as the page, with no gap: neighbouring columns
      // are never visible at the same time, so a gap would only shift the maths.
      content.style.width = `${width}px`;
      content.style.height = `${height}px`;
      content.style.columnWidth = `${width}px`;
      content.style.columnGap = "0px";
      content.style.columnFill = "auto";

      const count = Math.max(1, Math.round(content.scrollWidth / width));

      // Which column each scene fell into. `offsetLeft` is measured against the
      // content box (it is the positioned ancestor) and reports the element's *first*
      // fragment, so a scene spanning three columns still answers with the one it
      // opened in. Transforms don't affect it, so the page-turn translate is
      // invisible here.
      const starts = Array.from(
        content.querySelectorAll<HTMLElement>("[data-scene]"),
        (element) =>
          Math.min(Math.max(0, Math.round(element.offsetLeft / width)), count - 1),
      );

      setPageWidth(width);
      setPageCount(count);
      setScenePages((current) => (sameNumbers(current, starts) ? current : starts));
      setMeasured(true);
    };

    // Measure after layout settles; measuring synchronously inside the effect can
    // catch the column box mid-reflow.
    frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(measure);
    });

    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    });
    observer.observe(viewport);

    // Web fonts change every line box; without this the first paint paginates
    // against the fallback face and the count shifts once Newsreader arrives.
    void document.fonts?.ready.then(() => measure());

    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [viewportRef, contentRef, signature, nonce]);

  return { pageCount, pageWidth, scenePages, measured, remeasure };
}
