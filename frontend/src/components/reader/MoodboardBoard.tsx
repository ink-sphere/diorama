"use client";

import { motion } from "framer-motion";
import { Fragment, useState } from "react";

import { AlertIcon, CheckIcon, RetryIcon } from "@/components/Icons";
import type {
  Evidence,
  LocationProfile,
  Milieu,
  ResearchRecord,
  StyleBible,
  StyleDirection,
  TimePeriod,
  VisualMarkers,
} from "@/lib/types";

/**
 * The moodboard proper: a book's research, rendered.
 *
 * No generated images anywhere — v1's board is typographic. The palette *names* its
 * colours in hex, so they are shown as real swatches rather than described; the mood
 * vocabulary gets set rather than listed; and the period timeline distinguishes when
 * a book is set from when it was written, because they are usually different and the
 * second is what readers' visual memory of a book comes from.
 *
 * A partial record renders the sections it has and marks the ones it doesn't, so two
 * good artifacts are never withheld because a third never arrived.
 */
export function MoodboardBoard({
  record,
  onChoose,
  onRetry,
}: {
  record: ResearchRecord;
  onChoose: (direction: StyleDirection) => void;
  onRetry: () => void;
}) {
  const { author_profile: author, world_dossier: world, style_bibles: styles } =
    record;

  return (
    <div className="space-y-10">
      {record.status === "partial" ? (
        <div className="flex items-start gap-3 rounded-[4px] bg-shell-raised px-4 py-3">
          <AlertIcon className="mt-0.5 size-4 shrink-0 text-danger" />
          <div className="min-w-0">
            <p className="text-[0.85rem] leading-relaxed text-ink-soft">
              {record.error ?? "The research pass stopped before it finished."}
            </p>
            <button
              type="button"
              onClick={onRetry}
              className="label mt-2 inline-flex items-center gap-1.5 text-ink-soft transition-colors hover:text-ink"
            >
              <RetryIcon className="size-3" />
              Research again
            </button>
          </div>
        </div>
      ) : null}

      {author ? (
        <Section title="About the author">
          <div className="space-y-4 font-serif text-[1.02rem] leading-relaxed text-ink">
            <p>{author.bio_prose}</p>
            <p>{author.work_context_prose}</p>
          </div>
          <p className="label mt-4 text-ink-faint">
            {[
              author.name,
              [author.birth_date, author.death_date].filter(Boolean).join("–") ||
                null,
              author.publication_year
                ? `Published ${author.publication_year}`
                : null,
              author.authorship_period,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {author.visual_tradition.length > 0 ? (
            <ul className="mt-5 space-y-2.5 border-t border-rule pt-4">
              {author.visual_tradition.map((entry) => (
                <li key={`${entry.kind}-${entry.name}`} className="text-[0.88rem]">
                  <span className="text-ink">{entry.name}</span>
                  <span className="label ml-2 text-ink-faint">
                    {entry.kind}
                    {entry.medium ? ` · ${entry.medium}` : ""}
                  </span>
                  <p className="mt-0.5 leading-relaxed text-ink-soft">
                    {entry.description}
                  </p>
                </li>
              ))}
            </ul>
          ) : null}
        </Section>
      ) : (
        <MissingSection title="About the author" />
      )}

      {world ? (
        <>
          {world.time_periods.length > 0 ? (
            <Section title="When it happens">
              <Timeline periods={world.time_periods} />
            </Section>
          ) : null}

          {world.locations.length > 0 ? (
            <Section title="Where it happens">
              <div className="grid gap-3 sm:grid-cols-2">
                {world.locations.map((location) => (
                  <LocationCard key={location.name} location={location} />
                ))}
              </div>
            </Section>
          ) : null}

          {world.milieus.length > 0 ? (
            <Section title="Who is in it, and what they wear">
              <ul className="space-y-4">
                {world.milieus.map((milieu) => (
                  <MilieuRow key={milieu.name} milieu={milieu} />
                ))}
              </ul>
            </Section>
          ) : null}
        </>
      ) : (
        <MissingSection title="The world of the book" />
      )}

      {styles ? (
        <Section title="Art direction">
          <p className="mb-4 text-[0.88rem] leading-relaxed text-ink-soft">
            {styles.traditional
              ? "Two directions — one drawn from how this book has been illustrated before, one Diorama's own. Pick the one the pictures should follow."
              : "This book has no illustration history to draw on, so there is one direction: Diorama's own."}
          </p>
          <div className="grid gap-4 lg:grid-cols-2">
            <StyleCard
              bible={styles.original}
              selected={record.chosen_direction === "original"}
              onSelect={() => onChoose("original")}
            />
            {styles.traditional ? (
              <StyleCard
                bible={styles.traditional}
                selected={record.chosen_direction === "traditional"}
                onSelect={() => onChoose("traditional")}
              />
            ) : null}
          </div>
        </Section>
      ) : (
        <MissingSection title="Art direction" />
      )}

      <footer className="space-y-3 border-t border-rule pt-5">
        {record.coverage_notes.length > 0 ? (
          <p className="text-[0.82rem] leading-relaxed text-ink-faint">
            Most of this dossier cites the book&apos;s opening chapters — later ones
            may hold more.{" "}
            <button
              type="button"
              onClick={onRetry}
              className="underline underline-offset-2 transition-colors hover:text-ink"
            >
              Research again
            </button>{" "}
            for a wider pass.
          </p>
        ) : null}
        <p className="label text-ink-faint">
          {/* Unmeasured and free are different claims: a book researched before cost
              tracking existed shows nothing rather than $0. */}
          {typeof record.cost_usd === "number"
            ? `Researched for ${formatCost(record.cost_usd)}`
            : "Researched"}
        </p>
      </footer>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="label mb-4 text-ink-faint">{title}</h3>
      {children}
    </section>
  );
}

function MissingSection({ title }: { title: string }) {
  return (
    <section>
      <h3 className="label mb-2 text-ink-faint">{title}</h3>
      <p className="text-[0.88rem] text-ink-faint italic">
        The run stopped before it wrote this.
      </p>
    </section>
  );
}

/**
 * The periods, with story time and authorship time on separate rails.
 *
 * A novel written in 1861 and set in 1810 has two time systems, and the pictures need
 * both: the story's for what is in the frame, the author's for the idiom readers
 * already associate with the book.
 */
function Timeline({ periods }: { periods: TimePeriod[] }) {
  const rails: { kind: TimePeriod["kind"]; label: string }[] = [
    { kind: "story", label: "When it's set" },
    { kind: "authorship", label: "When it was written" },
  ];
  return (
    <div className="space-y-5">
      {rails.map((rail) => {
        const entries = periods.filter((period) => period.kind === rail.kind);
        if (entries.length === 0) return null;
        return (
          <div key={rail.kind}>
            <p className="label mb-2 text-ink-faint">{rail.label}</p>
            <div className="space-y-3 border-l border-rule-strong pl-4">
              {entries.map((period) => (
                <div key={`${period.kind}-${period.label}`}>
                  <p className="font-serif text-[1rem] text-ink">
                    {period.label}
                    {period.span ? (
                      <span className="ml-2 text-[0.82rem] text-ink-faint">
                        {period.span}
                      </span>
                    ) : null}
                  </p>
                  {period.summary ? (
                    <p className="mt-1 text-[0.88rem] leading-relaxed text-ink-soft">
                      {period.summary}
                    </p>
                  ) : null}
                  <Markers markers={period.visual_markers} />
                  <EvidenceNote evidence={period.evidence} />
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Markers({ markers }: { markers: VisualMarkers }) {
  const entries: [string, string | null | undefined][] = [
    ["Dress", markers.clothing],
    ["Technology", markers.technology],
    ["Transport", markers.transport],
    // Its own row rather than folded into a general description: candle, gas and
    // electric light are three different pictures of the same room.
    ["Light", markers.light_sources],
    ["Architecture", markers.architecture],
  ];
  const present = entries.filter(([, value]) => value?.trim());
  if (present.length === 0) return null;
  // A two-column grid whose label column sizes itself to the longest label, rather
  // than a fixed width the labels have to fit inside. `.label` is uppercase with
  // 0.14em tracking, which makes "ARCHITECTURE" ~108px — wider than any hand-picked
  // column, and an overflowing label doesn't wrap or clip, it paints straight over
  // the value next to it.
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] items-baseline gap-x-4 gap-y-1 text-[0.82rem] leading-relaxed">
      {present.map(([label, value]) => (
        <Fragment key={label}>
          <dt className="label text-ink-faint">{label}</dt>
          <dd className="text-ink-soft">{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function LocationCard({ location }: { location: LocationProfile }) {
  const existence =
    location.existence === "real"
      ? "Real"
      : location.existence === "fictional"
        ? "Invented"
        : "Real, altered";
  return (
    <div className="rounded-[4px] bg-shell-raised px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-serif text-[1rem] text-ink">{location.name}</p>
        <span className="label shrink-0 text-ink-faint">{existence}</span>
      </div>
      <p className="mt-1.5 text-[0.85rem] leading-relaxed text-ink-soft">
        {location.description}
      </p>
      {location.visual_notes ? (
        <p className="mt-1.5 text-[0.85rem] leading-relaxed text-ink-soft/85">
          {location.visual_notes}
        </p>
      ) : null}
      {location.periods.length > 0 ? (
        <p className="label mt-2.5 text-ink-faint">
          {location.periods.join(" · ")}
        </p>
      ) : null}
      <EvidenceNote evidence={location.evidence} />
    </div>
  );
}

function MilieuRow({ milieu }: { milieu: Milieu }) {
  return (
    <li>
      <p className="font-serif text-[1rem] text-ink">{milieu.name}</p>
      {milieu.description ? (
        <p className="mt-0.5 text-[0.85rem] leading-relaxed text-ink-faint">
          {milieu.description}
        </p>
      ) : null}
      <p className="mt-1 text-[0.88rem] leading-relaxed text-ink-soft">
        {milieu.wardrobe}
      </p>
      <EvidenceNote evidence={milieu.evidence} />
    </li>
  );
}

/**
 * One candidate art direction, selectable.
 *
 * `style_prompt_block` and the negative constraints sit behind a disclosure: they are
 * the machine's contract — the paragraph appended verbatim to every future image call
 * — which is worth being able to read but is not what the choice is made on.
 */
function StyleCard({
  bible,
  selected,
  onSelect,
}: {
  bible: StyleBible;
  selected: boolean;
  onSelect: () => void;
}) {
  const [showContract, setShowContract] = useState(false);
  return (
    <div
      className={`relative rounded-[4px] p-5 transition-colors ${
        selected
          ? "bg-shell-raised ring-1 ring-accent"
          : "bg-shell-raised/60 ring-1 ring-rule"
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="flex w-full items-start justify-between gap-3 text-left focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
      >
        <span>
          <span className="label block text-ink-faint">
            {bible.direction === "traditional"
              ? "Its own tradition"
              : "Diorama's own"}
          </span>
          <span className="mt-1 block font-serif text-lg leading-tight text-ink">
            {bible.name}
          </span>
        </span>
        <span
          className={`mt-1 grid size-5 shrink-0 place-items-center rounded-full transition-colors ${
            selected ? "bg-accent text-paper" : "ring-1 ring-rule-strong"
          }`}
        >
          {selected ? <CheckIcon className="size-3" /> : null}
        </span>
      </button>

      <p className="mt-3 text-[0.88rem] leading-relaxed text-ink-soft">
        {bible.rationale}
      </p>

      {bible.mood_words.length > 0 ? (
        <p className="mt-4 font-serif text-[1.05rem] leading-relaxed text-ink-soft">
          {bible.mood_words.join(" · ")}
        </p>
      ) : null}

      {bible.palette.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {bible.palette.map((colour) => (
            <div key={`${colour.name}-${colour.hex}`} className="w-16">
              <div
                className="h-9 w-full rounded-[3px] ring-1 ring-ink/10"
                style={{ backgroundColor: colour.hex }}
              />
              <p className="mt-1 truncate text-[0.68rem] text-ink-faint" title={colour.name}>
                {colour.name}
              </p>
            </div>
          ))}
        </div>
      ) : null}

      <p className="mt-4 text-[0.85rem] leading-relaxed text-ink-soft">
        {bible.lighting}
      </p>

      {bible.influences.length > 0 ? (
        <p className="label mt-3 text-ink-faint">
          After {bible.influences.join(", ")}
        </p>
      ) : null}

      <button
        type="button"
        onClick={() => setShowContract((open) => !open)}
        className="label mt-4 text-ink-faint transition-colors hover:text-ink-soft"
      >
        {showContract ? "Hide" : "Show"} the prompt this becomes
      </button>
      {showContract ? (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="overflow-hidden"
        >
          <p className="mt-3 border-l-2 border-rule-strong pl-3 text-[0.8rem] leading-relaxed text-ink-soft">
            {bible.style_prompt_block}
          </p>
          {bible.negative_constraints.length > 0 ? (
            <ul className="mt-2 space-y-0.5 pl-3">
              {bible.negative_constraints.map((constraint) => (
                <li key={constraint} className="text-[0.78rem] text-ink-faint">
                  Never: {constraint}
                </li>
              ))}
            </ul>
          ) : null}
        </motion.div>
      ) : null}
    </div>
  );
}

/**
 * Where an entry came from, kept quiet.
 *
 * Behind a disclosure rather than inline: the board should earn trust without reading
 * like a bibliography. Block ids are shown as a count — jumping the reader to a block
 * would mean closing the modal mid-thought, which is a worse trade than it sounds.
 */
function EvidenceNote({ evidence }: { evidence: Evidence }) {
  const blocks = evidence.block_ids.length;
  const urls = evidence.urls;
  if (blocks === 0 && urls.length === 0) return null;
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem] text-ink-faint">
      {blocks > 0 ? (
        <span>
          {blocks} passage{blocks === 1 ? "" : "s"} in the book
        </span>
      ) : null}
      {urls.slice(0, 3).map((url) => (
        <a
          key={url}
          href={url}
          target="_blank"
          rel="noreferrer noopener"
          className="underline underline-offset-2 transition-colors hover:text-ink-soft"
        >
          {hostOf(url)}
        </a>
      ))}
    </p>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "source";
  }
}

/**
 * Money at the magnitude it actually is — a fixed two decimals renders most real
 * research runs as "$0.00", which reads as free rather than small.
 */
function formatCost(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  if (value < 1) return `$${value.toFixed(3)}`;
  return `$${value.toFixed(2)}`;
}
