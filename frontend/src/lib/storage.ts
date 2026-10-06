export interface Position {
  path: string
  ratio: number
  updated: number
}
export interface Preferences {
  theme: 'paper' | 'night'
  fontSize: number
  font: 'serif' | 'sans'
}
export const defaultPreferences: Preferences = { theme: 'paper', fontSize: 19, font: 'serif' }

export function getStored<T>(key: string, fallback: T): T {
  try {
    const text = localStorage.getItem(`diorama:v1:${key}`)
    return text ? (JSON.parse(text) as T) : fallback
  } catch {
    return fallback
  }
}
export function saveStored(key: string, value: unknown): void {
  try {
    localStorage.setItem(`diorama:v1:${key}`, JSON.stringify(value))
  } catch {
    /* Reading still works when browser storage is unavailable. */
  }
}
export function preferences(): Preferences {
  const value = getStored<Partial<Preferences>>('preferences', {})
  return {
    theme: value.theme === 'night' ? 'night' : 'paper',
    fontSize: typeof value.fontSize === 'number' ? Math.min(28, Math.max(15, value.fontSize)) : 19,
    font: value.font === 'sans' ? 'sans' : 'serif',
  }
}
export function position(key: string): Position | null {
  const value = getStored<Partial<Position> | null>(`position:${key}`, null)
  if (!value || typeof value.path !== 'string' || typeof value.ratio !== 'number') return null
  return {
    path: value.path,
    ratio: Math.max(0, Math.min(1, value.ratio)),
    updated: typeof value.updated === 'number' ? value.updated : 0,
  }
}
