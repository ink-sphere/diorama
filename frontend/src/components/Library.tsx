"use client";
import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { ArrowUpRight, BookOpen, Moon, RefreshCw, Search, Sun } from "lucide-react";
import { readerUrl, type BookSummary } from "@/lib/book";
import { fetchJson, positionKey, preferences, readStored, writeStored, type Position, type Preferences } from "@/lib/storage";

export function Library() {
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [prefs, setPrefs] = useState<Preferences>({ theme: "light", size: 20 });
  const [positions, setPositions] = useState<Record<string, Position>>({});
  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const data = await fetchJson<BookSummary[]>("/api/books");
      setBooks(data);
      setPositions(Object.fromEntries(data.flatMap(b => {
        const p = b.hash && readStored<Position>(positionKey(b.hash));
        return p ? [[b.id, p]] : [];
      })));
    } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const p = preferences(); setPrefs(p); document.documentElement.dataset.theme = p.theme; void refresh(); }, [refresh]);
  function toggleTheme() {
    const next: Preferences = { ...prefs, theme: prefs.theme === "light" ? "dark" : "light" };
    setPrefs(next); document.documentElement.dataset.theme = next.theme; writeStored("diorama:preferences", next);
  }
  const filtered = books.filter(b => `${b.title} ${b.author || ""}`.toLowerCase().includes(query.toLowerCase()));
  return <>
    <header className="app-bar"><Link href="/" className="wordmark"><BookOpen size={21} strokeWidth={1.5} /> diorama<span className="bar-label">Your reading room</span></Link>
      <button className="icon-button" onClick={toggleTheme} aria-label={`Use ${prefs.theme === "light" ? "dark" : "light"} theme`}>{prefs.theme === "light" ? <Moon size={18} /> : <Sun size={18} />}</button>
    </header>
    <main className="library">
      <div className="library-heading"><div><p className="quiet-label">A little space for a good book</p><h1>Your library<span className="count">{books.filter(b => !b.error).length.toString().padStart(2, "0")}</span></h1></div>
        <button className="text-button" onClick={refresh} disabled={loading}><RefreshCw size={15} className={loading ? "refreshing" : ""} /> Refresh library</button>
      </div>
      <div className="shelf-toolbar"><span>{query ? `${filtered.length} results` : "All books"}</span><label className="search"><Search size={16} /><input aria-label="Search by title or author" placeholder="Find a title or author" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      {error && <div className="notice" role="alert">{error}<button onClick={refresh}>Try again</button></div>}
      {loading && !books.length ? <div className="shelf-skeleton" aria-label="Loading books"><div /><div /><div /></div> : null}
      {!loading && !error && books.length === 0 && <div className="empty"><BookOpen size={36} strokeWidth={1} /><h2>Your next story starts here.</h2><p>Run the ebook loader to add a book to <code>.diorama</code>, then refresh your library.</p></div>}
      {!loading && books.length > 0 && filtered.length === 0 && <p className="empty">No titles match “{query}”. Try the author’s name.</p>}
      <div className="book-shelf">{filtered.map((book, i) => book.error ? <article className="damaged-book" key={book.id}><BookOpen /><h2>{book.title}</h2><p>{book.error}</p></article> : <Link className="book-tile" href={readerUrl(book.id)} key={book.id}>
        <div className="cover-stage"><div className="cover-object">
          {book.cover ? <Image src={book.cover} alt={`${book.title} cover`} width={220} height={306} unoptimized priority={i === 0} /> : <div className="type-cover"><BookOpen size={23} strokeWidth={1} /><span>{book.title}</span><small>{book.author}</small></div>}
        </div><span className="open-book"><ArrowUpRight size={19} /></span></div>
        <div className="book-caption"><h2>{book.title}</h2><p>{book.author || "Unknown author"}</p><div className="book-meta"><span>{book.sections} sections</span><span className="read-action">{positions[book.id] ? "Continue reading" : "Start reading"} <span aria-hidden="true">↗</span></span></div></div>
      </Link>)}</div>
      <footer className="library-footer"><span>Books to get lost in.</span><span>Stored locally. Ready when you are.</span></footer>
    </main>
  </>;
}
