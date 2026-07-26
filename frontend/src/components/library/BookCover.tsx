"use client";

import { useState } from "react";

import { coverUrl } from "@/lib/api";

/**
 * A book's cover: the real image extracted from the EPUB, or a typographic one.
 *
 * The backend answers 404 for books whose EPUB carries no cover image, which is
 * common enough (public-domain conversions especially) that the fallback is a
 * designed cover rather than a placeholder box — the shelf should never show a
 * hole where a book is.
 */
export function BookCover({
  bookId,
  title,
  author,
  className = "",
}: {
  bookId: string;
  title: string;
  author?: string | null;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);

  if (failed) {
    return (
      <div
        className={`relative flex h-full w-full flex-col justify-between overflow-hidden bg-paper p-4 ${className}`}
      >
        {/* Hue is derived from the id so a given book always gets the same cover. */}
        <div
          className="absolute inset-0 opacity-[0.55]"
          style={{ background: fallbackWash(bookId) }}
          aria-hidden
        />
        <div className="relative">
          <div className="h-px w-8 bg-ink/30" />
        </div>
        <div className="relative">
          <p className="font-serif text-[0.95rem] leading-tight font-medium text-ink [text-wrap:balance]">
            {title}
          </p>
          {author ? (
            <p className="label mt-2 text-[0.6rem] text-ink-soft">{author}</p>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Plain <img>: covers come from a user-configurable API origin at runtime,
          which next/image would need build-time remotePatterns for. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={coverUrl(bookId)}
        alt={`Cover of ${title}`}
        loading="lazy"
        onError={() => setFailed(true)}
        onLoad={() => setLoaded(true)}
        className={`h-full w-full object-cover transition-opacity duration-500 ${
          loaded ? "opacity-100" : "opacity-0"
        } ${className}`}
      />
    </>
  );
}

function fallbackWash(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) % 360;
  }
  const hue = hash;
  return `radial-gradient(120% 90% at 20% 0%, oklch(0.86 0.05 ${hue}) 0%, transparent 60%), linear-gradient(160deg, oklch(0.9 0.03 ${(hue + 40) % 360}) 0%, oklch(0.82 0.04 ${(hue + 90) % 360}) 100%)`;
}
