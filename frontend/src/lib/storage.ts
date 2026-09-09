export interface Position { section: string; anchor?: string; offset: number; updated: number }
export interface Preferences { theme: "light" | "dark"; size: number }
export function readStored<T>(key: string): T | null {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
}
export function writeStored(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Private browsing can disable storage. */ }
}
export function positionKey(hash: string) { return `diorama:position:${hash}`; }
export function preferences(): Preferences {
  const stored = readStored<Preferences>("diorama:preferences");
  return { theme: stored?.theme === "dark" || stored?.theme === "light" ? stored.theme : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
    size: Math.max(16, Math.min(28, Number(stored?.size) || 20)) };
}
export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal, cache: "no-store" });
  const json = await response.json();
  if (!response.ok) throw new Error(json.error || "Something went wrong. Please try again.");
  return json;
}
