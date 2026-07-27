/**
 * The app's icon set: one consistent 24-grid, 1.5px stroke, `currentColor`.
 *
 * Hand-rolled rather than pulled from a library so the weight matches the hairline
 * rules and small-caps labels the rest of the UI is built from — most icon packs
 * ship a heavier stroke that reads as louder than the type beside it.
 */

type IconProps = React.SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function ContentsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 6h1M4 12h1M4 18h1M9 6h11M9 12h11M9 18h11" />
    </Icon>
  );
}

export function TypeIcon(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...props}>
      <text
        x="12"
        y="17"
        textAnchor="middle"
        fontFamily="var(--font-serif)"
        fontSize="15"
        letterSpacing="-0.5"
      >
        Aa
      </text>
    </svg>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 13.2A8.2 8.2 0 1 1 10.8 4a6.6 6.6 0 0 0 9.2 9.2Z" />
    </Icon>
  );
}

export function SunIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" />
    </Icon>
  );
}

export function ChevronLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M14.5 5 8 12l6.5 7" />
    </Icon>
  );
}

export function ChevronRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M9.5 5 16 12l-6.5 7" />
    </Icon>
  );
}

export function ChevronDownIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M5 9l7 6 7-6" />
    </Icon>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 5v14M5 12h14" />
    </Icon>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4 4" />
    </Icon>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h16M10 4h4M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7M10 11v6M14 11v6" />
    </Icon>
  );
}

export function RetryIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M20 12a8 8 0 1 1-2.6-5.9M20 4v4h-4" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m5 12.5 4.5 4.5L19 7" />
    </Icon>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5v5M12 16h.01" />
    </Icon>
  );
}

export function BookIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H10a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5.5A1.5 1.5 0 0 1 4 15.5Z" />
      <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H14a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h4.5a1.5 1.5 0 0 0 1.5-1.5Z" />
    </Icon>
  );
}

export function UploadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 16V4M8 8l4-4 4 4" />
      <path d="M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15" />
    </Icon>
  );
}

export function SparkIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4.5 13.6 9 18 10.5 13.6 12 12 16.5 10.4 12 6 10.5 10.4 9Z" />
      <path d="M18.5 16.5 19 18l1.5.5-1.5.5-.5 1.5-.5-1.5L16.5 18l1.5-.5Z" />
    </Icon>
  );
}

/** Sliders rather than a cog — closer in weight to the hairline rules around it. */
export function SettingsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2.2" />
      <circle cx="9" cy="17" r="2.2" />
    </Icon>
  );
}

export function KeyIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="8" cy="12" r="4" />
      <path d="M12 12h8M17 12v3M20 12v2.5" />
    </Icon>
  );
}

/**
 * Ledger bars rather than a coin or a currency glyph: the dashboard is about how
 * spend is distributed, and a $ sign would also read as US-specific chrome in a UI
 * that is otherwise unlabelled.
 */
export function CostsIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 20h16" />
      <path d="M6.5 20v-6M11.5 20V8M16.5 20v-9" />
    </Icon>
  );
}

export function ImageIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3.5" y="5" width="17" height="14" rx="1.5" />
      <circle cx="9" cy="10" r="1.5" />
      <path d="m4 17 4.5-4.5L13 17M14 15l2.5-2.5L20 16" />
    </Icon>
  );
}
