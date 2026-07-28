"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Media queries and mount state as *external stores* rather than effects.
 *
 * Both are browser facts that don't exist during SSR, and reading them with
 * `useState` + `useEffect` means a synchronous setState on mount — a cascading
 * render React (and the compiler lint) rightly objects to. `useSyncExternalStore`
 * expresses the same thing with a server snapshot instead.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

const noopSubscribe = () => () => {};

/** False on the server and during hydration, true once mounted in a browser. */
export function useMounted(): boolean {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
}
