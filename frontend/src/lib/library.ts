import fs from "node:fs/promises";
import path from "node:path";
import JSZip from "jszip";
import { JSDOM } from "jsdom";
import { bookSchema, navigationOf, type Book, type BookInfo, type BookSummary } from "./book";

export class LibraryError extends Error {
  constructor(message: string, public status = 422) { super(message); }
}
export function libraryRoot(): string {
  const override = process.env.DIORAMA_LIBRARY_DIR;
  if (override && !path.isAbsolute(override)) throw new LibraryError("DIORAMA_LIBRARY_DIR must be absolute");
  return override || process.env.DIORAMA_DEFAULT_LIBRARY || path.resolve(import.meta.dirname, "../../../.diorama");
}
export async function bookDirectory(id: string): Promise<string> {
  if (!id || id === "." || id === ".." || /[/\\\0]/.test(id)) throw new LibraryError("Invalid book ID", 400);
  const root = await fs.realpath(/* turbopackIgnore: true */ libraryRoot());
  const candidate = path.join(root, id);
  const actual = await fs.realpath(candidate).catch(() => { throw new LibraryError("Book not found", 404); });
  if (actual !== candidate) throw new LibraryError("Linked library directories are not supported", 403);
  return candidate;
}
async function localFile(directory: string, name: string): Promise<string> {
  if (!name || /[/\\\0]/.test(name) || name === "." || name === "..") throw new LibraryError("Invalid book filename");
  const file = path.join(directory, name);
  const actual = await fs.realpath(file).catch(() => { throw new LibraryError(`Missing ${name}`); });
  if (actual !== file) throw new LibraryError("Linked book files are not supported", 403);
  return file;
}
export async function readBook(id: string): Promise<Book> {
  const file = await localFile(await bookDirectory(id), "book.json");
  if ((await fs.stat(file)).size > 64 * 1024 * 1024) throw new LibraryError("Book JSON exceeds 64 MB");
  try { return bookSchema.parse(JSON.parse(await fs.readFile(file, "utf8"))); }
  catch { throw new LibraryError("Invalid or unsupported book.json"); }
}
const archiveCache = new Map<string, { stamp: string; archive: Promise<JSZip> }>();
export async function readArchive(id: string, book: Book): Promise<JSZip> {
  const file = await localFile(await bookDirectory(id), book.source_archive);
  const stat = await fs.stat(file);
  if (stat.size > 256 * 1024 * 1024) throw new LibraryError("Source archive exceeds 256 MB");
  const stamp = `${stat.mtimeMs}:${stat.size}`;
  const existing = archiveCache.get(file);
  if (existing?.stamp === stamp) return existing.archive;
  const archive = fs.readFile(file).then(bytes => JSZip.loadAsync(bytes));
  archiveCache.set(file, { stamp, archive });
  if (archiveCache.size > 4) archiveCache.delete(archiveCache.keys().next().value!);
  try { return await archive; } catch { archiveCache.delete(file); throw new LibraryError("Cannot open source EPUB"); }
}
export function safeMember(member: string): string {
  if (!member || member.startsWith("/") || /[\\\0]/.test(member) || member.split("/").includes("..")) {
    throw new LibraryError("Invalid archive member", 400);
  }
  return member;
}
export async function readMember(zip: JSZip, member: string): Promise<Buffer> {
  const entry = zip.file(safeMember(member));
  if (!entry) throw new LibraryError("Book resource not found", 404);
  // Check the advertised expanded size before allocation, then check actual bytes.
  const expanded = (entry as unknown as { _data?: { uncompressedSize?: number } })._data?.uncompressedSize;
  if (expanded && expanded > 32 * 1024 * 1024) throw new LibraryError("Book resource exceeds 32 MB");
  const bytes = await entry.async("nodebuffer");
  if (bytes.length > 32 * 1024 * 1024) throw new LibraryError("Book resource exceeds 32 MB");
  return bytes;
}
export function assetUrl(id: string, member: string): string {
  return `/api/books/${encodeURIComponent(id)}/asset?member=${encodeURIComponent(member)}`;
}
export function resolveMember(base: string, href: string): { member: string; hash: string } | null {
  if (/^[a-z][\w+.-]*:/i.test(href) || href.startsWith("//") || href.startsWith("/")) return null;
  try {
    const url = new URL(href, `https://epub.invalid/${base}`);
    if (url.origin !== "https://epub.invalid") return null;
    return { member: safeMember(decodeURIComponent(url.pathname.slice(1))), hash: decodeURIComponent(url.hash.slice(1)) };
  } catch { return null; }
}
async function coverOf(id: string, zip: JSZip): Promise<string | null> {
  try {
    const container = new JSDOM(await readMember(zip, "META-INF/container.xml"), { contentType: "text/xml" });
    const rootfile = container.window.document.getElementsByTagName("rootfile")[0]?.getAttribute("full-path");
    container.window.close();
    if (!rootfile) return null;
    const dom = new JSDOM(await readMember(zip, rootfile), { contentType: "text/xml" });
    const doc = dom.window.document;
    const coverId = [...doc.getElementsByTagName("meta")].find(e => e.getAttribute("name") === "cover")?.getAttribute("content");
    const item = [...doc.getElementsByTagName("item")].find(e => e.getAttribute("properties")?.split(" ").includes("cover-image") || (coverId && e.id === coverId));
    const resolved = item && resolveMember(rootfile, item.getAttribute("href") || "");
    dom.window.close();
    return resolved ? assetUrl(id, resolved.member) : null;
  } catch { return null; }
}
export async function bookInfo(id: string): Promise<BookInfo> {
  const book = await readBook(id);
  const archive = await readArchive(id, book);
  return { id, title: book.title, author: book.author, hash: book.source_sha256,
    navigation: navigationOf(book), cover: await coverOf(id, archive) };
}
export async function listBooks(): Promise<BookSummary[]> {
  const entries = await fs.readdir(/* turbopackIgnore: true */ libraryRoot(), { withFileTypes: true }).catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") return [];
    throw new LibraryError("Cannot read the library directory");
  });
  return (await Promise.all(entries.filter(e => e.isDirectory() && !e.name.startsWith(".")).map(async entry => {
    try {
      const info = await bookInfo(entry.name);
      return { id: info.id, title: info.title, author: info.author, hash: info.hash,
        cover: info.cover, sections: info.navigation.content.length };
    } catch (error) {
      return { id: entry.name, title: entry.name, author: null,
        error: error instanceof LibraryError ? error.message : "This book could not be opened" };
    }
  }))).sort((a, b) => a.title.localeCompare(b.title));
}
