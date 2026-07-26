"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useTheme } from "next-themes";

import { useMounted } from "@/lib/useMediaQuery";

import { MoonIcon, SunIcon } from "./Icons";

export function ThemeToggle({ className = "" }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  // The resolved theme isn't knowable on the server, so the icon can only be
  // chosen after mount; the frame renders immediately so the header doesn't jump.
  const mounted = useMounted();

  const isDark = resolvedTheme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className={`grid size-9 place-items-center rounded-full text-ink-soft transition-colors hover:bg-shell-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${className}`}
      aria-label={
        mounted
          ? isDark
            ? "Switch to light theme"
            : "Switch to dark theme"
          : "Switch theme"
      }
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={mounted && isDark ? "sun" : "moon"}
          initial={{ opacity: 0, rotate: -35, scale: 0.7 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 35, scale: 0.7 }}
          transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          className="grid place-items-center"
        >
          {mounted && isDark ? (
            <SunIcon className="size-[18px]" />
          ) : (
            <MoonIcon className="size-[18px]" />
          )}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
