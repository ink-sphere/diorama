"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef } from "react";

import { TypeIcon } from "@/components/Icons";
import {
  FONT_SCALES,
  LINE_HEIGHTS,
  MEASURES,
  type ReaderPrefs,
} from "@/lib/useReaderPrefs";

/**
 * The "Aa" popover: type size, leading, measure and face.
 *
 * Every control here changes the typesetting, which re-triggers measurement in
 * `usePagination` — page counts and the current page shift underneath the reader,
 * which is expected and why the reader keeps its position proportionally.
 */
export function TypeMenu({
  open,
  onOpenChange,
  prefs,
  onChange,
  onReset,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  prefs: ReaderPrefs;
  onChange: (patch: Partial<ReaderPrefs>) => void;
  onReset: () => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapperRef.current?.contains(event.target as Node)) onOpenChange(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  return (
    <div ref={wrapperRef} className="relative">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-label="Typography settings"
        aria-expanded={open}
        className={`grid size-9 place-items-center rounded-full transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
          open ? "bg-shell-raised text-ink" : "text-ink-soft"
        }`}
      >
        <TypeIcon className="size-[19px]" />
      </button>

      <AnimatePresence>
        {open ? (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 z-40 mt-2 w-72 origin-top-right rounded-[4px] border border-rule bg-shell-raised p-4 shadow-page"
          >
            <Row label="Size">
              <Stepper
                values={FONT_SCALES}
                value={prefs.fontScale}
                onChange={(fontScale) => onChange({ fontScale })}
                format={(value) => `${Math.round(value * 100)}%`}
              />
            </Row>
            <Row label="Leading">
              <Stepper
                values={LINE_HEIGHTS}
                value={prefs.lineHeight}
                onChange={(lineHeight) => onChange({ lineHeight })}
                format={(value) => value.toFixed(2)}
              />
            </Row>
            <Row label="Measure">
              <Stepper
                values={MEASURES}
                value={prefs.measure}
                onChange={(measure) => onChange({ measure })}
                format={(value) => `${value}ch`}
              />
            </Row>
            <Row label="Face">
              <div className="flex gap-1">
                {(["serif", "sans"] as const).map((family) => (
                  <button
                    key={family}
                    type="button"
                    onClick={() => onChange({ family })}
                    className={`relative rounded-full px-3 py-1 text-[0.8rem] transition-colors ${
                      family === "serif" ? "font-serif" : "font-sans"
                    } ${prefs.family === family ? "text-ink" : "text-ink-faint hover:text-ink-soft"}`}
                  >
                    {prefs.family === family ? (
                      <motion.span
                        layoutId="type-face-pill"
                        className="absolute inset-0 rounded-full bg-shell ring-1 ring-rule"
                        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                      />
                    ) : null}
                    <span className="relative">
                      {family === "serif" ? "Newsreader" : "Inter"}
                    </span>
                  </button>
                ))}
              </div>
            </Row>

            <button
              type="button"
              onClick={onReset}
              className="label mt-3 w-full rounded-[3px] border border-rule py-1.5 text-ink-faint transition hover:border-rule-strong hover:text-ink-soft"
            >
              Reset
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <span className="label text-ink-faint">{label}</span>
      {children}
    </div>
  );
}

function Stepper<T extends number>({
  values,
  value,
  onChange,
  format,
}: {
  values: T[];
  value: T | number;
  onChange: (value: T) => void;
  format: (value: number) => string;
}) {
  const index = nearestIndex(values, value);
  return (
    <div className="flex items-center gap-1">
      <StepButton
        label="Decrease"
        disabled={index <= 0}
        onClick={() => onChange(values[index - 1])}
      >
        −
      </StepButton>
      <span className="w-14 text-center font-sans text-[0.8rem] tabular-nums text-ink">
        {format(values[index])}
      </span>
      <StepButton
        label="Increase"
        disabled={index >= values.length - 1}
        onClick={() => onChange(values[index + 1])}
      >
        +
      </StepButton>
    </div>
  );
}

/** Stored values can fall outside the current option list; snap to the closest. */
function nearestIndex(values: number[], value: number): number {
  let best = 0;
  for (let i = 1; i < values.length; i += 1) {
    if (Math.abs(values[i] - value) < Math.abs(values[best] - value)) best = i;
  }
  return best;
}

function StepButton({
  children,
  onClick,
  disabled,
  label,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="grid size-7 place-items-center rounded-full border border-rule text-ink-soft transition hover:border-rule-strong hover:text-ink disabled:opacity-30 disabled:hover:border-rule"
    >
      {children}
    </button>
  );
}
