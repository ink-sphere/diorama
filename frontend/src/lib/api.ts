/**
 * Thin client over the FastAPI library API (`diorama/backend/routes/books.py`).
 *
 * Everything is called from the browser — the backend is a personal, single-user
 * process on localhost, so there is no server-side fetching or caching layer to
 * keep in sync.
 */

import type {
  BookRecord,
  BookScenes,
  BookUsage,
  ConnectionTest,
  EbookStructure,
  ModelCatalogue,
  Provider,
  ReadingProgress,
  SettingsUpdate,
  SettingsView,
  UploadResponse,
  UsageSummary,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    throw new ApiError(await errorText(response), response.status);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/** FastAPI puts the human-readable reason in `detail`; fall back to the status. */
async function errorText(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  } catch {
    /* not json — fall through */
  }
  return `Request failed (${response.status})`;
}

export function listBooks(): Promise<BookRecord[]> {
  return request<BookRecord[]>("/api/books", { cache: "no-store" });
}

export function getBook(bookId: string): Promise<BookRecord> {
  return request<BookRecord>(`/api/books/${bookId}`, { cache: "no-store" });
}

export function getStructure(bookId: string): Promise<EbookStructure> {
  return request<EbookStructure>(`/api/books/${bookId}/structure`, {
    cache: "no-store",
  });
}

/**
 * A book's scene boundaries, or null when it simply has none.
 *
 * 404 is the normal answer for a book shelved before scene segmentation existed, or
 * one whose segmentation pass failed — neither is a reason to refuse to open the
 * book, so this resolves to null and the reader falls back to continuous pagination.
 * Any other failure still throws.
 */
export async function getScenes(bookId: string): Promise<BookScenes | null> {
  try {
    return await request<BookScenes>(`/api/books/${bookId}/scenes`, {
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
      return null;
    }
    throw error;
  }
}

export function uploadBook(file: File): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file);
  return request<UploadResponse>("/api/books", { method: "POST", body });
}

export function retryBook(bookId: string): Promise<BookRecord> {
  return request<BookRecord>(`/api/books/${bookId}/retry`, { method: "POST" });
}

export function deleteBook(bookId: string): Promise<void> {
  return request<void>(`/api/books/${bookId}`, { method: "DELETE" });
}

export function saveProgress(
  bookId: string,
  progress: ReadingProgress,
): Promise<BookRecord> {
  return request<BookRecord>(`/api/books/${bookId}/progress`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(progress),
  });
}

export function getSettings(): Promise<SettingsView> {
  return request<SettingsView>("/api/settings", { cache: "no-store" });
}

export function saveSettings(update: SettingsUpdate): Promise<SettingsView> {
  return request<SettingsView>("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
}

/** Every provider's models, merged, for the picker. `refresh` bypasses the caches. */
export function listModels(refresh = false): Promise<ModelCatalogue> {
  return request<ModelCatalogue>(
    `/api/settings/models${refresh ? "?refresh=true" : ""}`,
    { cache: "no-store" },
  );
}

/** Validate one provider's key — the one the user typed, or the stored one. */
export function testConnection(
  provider: Provider,
  apiKey?: string,
): Promise<ConnectionTest> {
  return request<ConnectionTest>("/api/settings/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey ?? null }),
  });
}

/** Cost totals across every book that has a call ledger. */
export function getUsageSummary(): Promise<UsageSummary> {
  return request<UsageSummary>("/api/usage", { cache: "no-store" });
}

/**
 * One book's cost trace, down to each LLM call.
 *
 * Throws a 404 `ApiError` when the book has no ledger — which is the normal answer
 * for a book processed before cost tracking existed, not a broken request.
 */
export function getBookUsage(bookId: string): Promise<BookUsage> {
  return request<BookUsage>(`/api/usage/books/${bookId}`, { cache: "no-store" });
}

export function coverUrl(bookId: string): string {
  return `${API_BASE}/api/books/${bookId}/cover`;
}

export function streamUrl(bookId: string): string {
  return `${API_BASE}/api/books/${bookId}/stream`;
}

export { ApiError };
