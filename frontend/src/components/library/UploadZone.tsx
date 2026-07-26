"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import { PlusIcon, UploadIcon } from "@/components/Icons";

function epubsOnly(list: FileList | null): File[] {
  return Array.from(list ?? []).filter((file) =>
    file.name.toLowerCase().endsWith(".epub"),
  );
}

/**
 * Whole-window drop target for EPUBs.
 *
 * Dragging is tracked with a counter rather than a boolean because dragenter and
 * dragleave both fire as the pointer crosses nested children — a boolean flickers
 * the overlay every time the cursor passes over a card.
 */
export function UploadZone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  useEffect(() => {
    const isFileDrag = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types ?? []).includes("Files");

    const onEnter = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      depth.current += 1;
      setDragging(true);
    };
    const onLeave = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const onOver = (event: DragEvent) => {
      if (isFileDrag(event)) event.preventDefault();
    };
    const onDrop = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      depth.current = 0;
      setDragging(false);
      const files = epubsOnly(event.dataTransfer?.files ?? null);
      if (files.length > 0) onFiles(files);
    };

    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", onOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [onFiles]);

  return (
    <AnimatePresence>
      {dragging ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.18 }}
          className="pointer-events-none fixed inset-0 z-50 grid place-items-center bg-shell/85 backdrop-blur-sm"
        >
          <motion.div
            initial={{ scale: 0.97, y: 8 }}
            animate={{ scale: 1, y: 0 }}
            exit={{ scale: 0.98 }}
            transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
            className="flex flex-col items-center gap-4 rounded-[4px] border border-dashed border-rule-strong px-16 py-14"
          >
            <UploadIcon className="size-7 text-ink-soft" />
            <p className="font-serif text-xl text-ink">Drop it on the shelf</p>
            <p className="label text-ink-faint">EPUB files only</p>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

export function UploadButton({
  onFiles,
  busy,
}: {
  onFiles: (files: File[]) => void;
  busy: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="label inline-flex items-center gap-2 rounded-full border border-rule-strong px-4 py-2 text-ink-soft transition hover:border-ink hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {busy ? (
          <motion.span
            className="block size-3 rounded-full border border-ink-faint border-t-transparent"
            animate={{ rotate: 360 }}
            transition={{ duration: 0.9, repeat: Infinity, ease: "linear" }}
          />
        ) : (
          <PlusIcon className="size-3.5" />
        )}
        Add a book
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".epub,application/epub+zip"
        multiple
        hidden
        onChange={(event) => {
          const files = epubsOnly(event.target.files);
          if (files.length > 0) onFiles(files);
          event.target.value = "";
        }}
      />
    </>
  );
}
