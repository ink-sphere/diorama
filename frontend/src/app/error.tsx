"use client";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return <main className="empty"><h1>Something interrupted the reading room.</h1><button onClick={reset}>Try again</button></main>;
}
