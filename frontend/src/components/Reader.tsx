"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, ArrowRight, BookOpen, Check, List, Minus, Moon, Plus, Sun, X } from "lucide-react";
import { Contents } from "./Contents";
import { nodesOf, readerUrl, rootsOf, type BookInfo } from "@/lib/book";
import { fetchJson, positionKey, preferences, readStored, writeStored, type Position, type Preferences } from "@/lib/storage";

interface FrameInfo { member: string; spine: number }
interface FrameHandle { element: HTMLIFrameElement; document: Document; member: string }

function BookFrame({ url, info, prefs, onReady, onLink, onFailure }: {
  url: string; info: FrameInfo; prefs: Preferences;
  onReady: (spine: number, handle: FrameHandle | null) => void;
  onLink: (member: string, anchor: string) => void;
  onFailure: (message: string) => void;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const cleanup = useRef<(() => void) | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => () => { cleanup.current?.(); onReady(info.spine, null); }, [info.spine, onReady]);
  useEffect(() => {
    const doc = ref.current?.contentDocument;
    if (loaded && doc) {
      doc.documentElement.dataset.theme = prefs.theme;
      doc.documentElement.style.setProperty("--reader-size", `${prefs.size}px`);
    }
  }, [prefs, loaded]);
  return <div className={`frame-wrap ${loaded ? "is-loaded" : ""}`}>
    {!loaded && <div className="reading-skeleton" aria-label="Loading reading section"><i /><i /><i /><i /><i /></div>}
    <iframe ref={ref} src={url} title={`Book text: ${info.member}`} sandbox="allow-same-origin" className="book-frame" scrolling="no" onLoad={() => {
      cleanup.current?.();
      const frame = ref.current!;
      const doc = frame.contentDocument;
      if (!doc?.body || doc.querySelector("parsererror") || doc.documentElement.namespaceURI !== "http://www.w3.org/1999/xhtml") {
        onFailure("This section could not be rendered. The source EPUB may be missing or inconsistent with book.json."); return;
      }
      doc.documentElement.dataset.theme = prefs.theme;
      doc.documentElement.style.setProperty("--reader-size", `${prefs.size}px`);
      const resize = () => { frame.style.height = `${Math.ceil(doc.body.getBoundingClientRect().height) + 2}px`; };
      const observer = new ResizeObserver(resize); observer.observe(doc.body);
      const click = (event: MouseEvent) => {
        const element = event.target as Element;
        const a = element.closest?.("a"); if (!a) return;
        event.preventDefault();
        if (a.hasAttribute("data-external-link")) {
          const href = a.getAttribute("data-external-link")!;
          if (/^https?:\/\//i.test(href)) window.open(href, "_blank", "noopener,noreferrer");
        } else if (a.hasAttribute("data-book-member")) onLink(a.getAttribute("data-book-member")!, a.getAttribute("data-book-anchor") || "");
      };
      doc.addEventListener("click", click);
      resize(); setLoaded(true); onReady(info.spine, { element: frame, document: doc, member: info.member });
      cleanup.current = () => { observer.disconnect(); doc.removeEventListener("click", click); };
    }} />
  </div>;
}

export function Reader({ bookId }: { bookId: string }) {
  const router = useRouter();
  const query = useSearchParams();
  const requested = query.get("section");
  const anchor = query.get("anchor");
  const memberQuery = query.get("member");
  const [book, setBook] = useState<BookInfo | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [frames, setFrames] = useState<FrameInfo[]>([]);
  const [loadedRoot, setLoadedRoot] = useState("");
  const [active, setActive] = useState("");
  const [prefs, setPrefs] = useState<Preferences>({ theme: "light", size: 20 });
  const [sidebar, setSidebar] = useState(true);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [readyVersion, setReadyVersion] = useState(0);
  const handles = useRef(new Map<number, FrameHandle>());
  const restoreOffset = useRef<number | null>(null);
  const restored = useRef("");
  const drawer = useRef<HTMLDialogElement>(null);
  const contentsButton = useRef<HTMLButtonElement>(null);
  const nodes = useMemo(() => book ? nodesOf(book.navigation) : [], [book]);
  const roots = useMemo(() => book ? rootsOf(book.navigation) : [], [book]);
  const selected = nodes.find(n => n.id === requested);
  const rootId = selected?.rootId || "";
  const root = roots.find(n => n.id === rootId);
  const rootIndex = roots.findIndex(n => n.id === rootId);
  const percent = roots.length ? Math.round((rootIndex + 1) / roots.length * 100) : 0;

  useEffect(() => {
    const p = preferences(); setPrefs(p); document.documentElement.dataset.theme = p.theme;
    const controller = new AbortController();
    fetchJson<BookInfo>(`/api/books/${encodeURIComponent(bookId)}`, controller.signal).then(setBook).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [bookId]);
  useEffect(() => {
    if (!book) return;
    document.title = `${book.title} — Diorama`;
    if (!requested) {
      const saved = readStored<Position>(positionKey(book.hash));
      const exists = nodes.some(n => n.id === saved?.section);
      restoreOffset.current = exists && Number.isFinite(saved?.offset) ? saved!.offset : 0;
      router.replace(readerUrl(bookId, exists ? saved!.section : book.navigation.content[0]?.id || roots[0]?.id), { scroll: false });
    }
  }, [book, bookId, nodes, requested, roots, router]);
  useEffect(() => {
    if (!rootId) return;
    const controller = new AbortController();
    setError(""); setFrames([]); handles.current.clear(); setLoadedRoot(""); restored.current = "";
    fetchJson<FrameInfo[]>(`/api/books/${encodeURIComponent(bookId)}/section?section=${encodeURIComponent(rootId)}`, controller.signal).then(data => {
      setFrames(data); setLoadedRoot(rootId);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [bookId, rootId]);
  const onReady = useCallback((spine: number, handle: FrameHandle | null) => {
    if (handle) handles.current.set(spine, handle); else handles.current.delete(spine);
    setReadyVersion(v => v + 1);
  }, []);
  const markerTop = useCallback((id: string): number | null => {
    for (const frame of handles.current.values()) {
      const element = [...frame.document.querySelectorAll("[data-section]")].find(e => e.getAttribute("data-section") === id);
      if (element) return element.getBoundingClientRect().top + frame.element.getBoundingClientRect().top + window.scrollY - 100;
    }
    return null;
  }, []);
  useEffect(() => {
    if (!requested || loadedRoot !== rootId || !frames.length || handles.current.size !== frames.length) return;
    const key = `${requested}:${anchor || ""}:${memberQuery || ""}`;
    if (restored.current === key) return;
    const token = requestAnimationFrame(() => {
      let top = markerTop(requested) ?? 0;
      if (anchor) {
        for (const frame of handles.current.values()) {
          if (memberQuery && frame.member !== memberQuery) continue;
          const target = frame.document.getElementById(anchor);
          if (target) { top = target.getBoundingClientRect().top + frame.element.getBoundingClientRect().top + window.scrollY - 100; break; }
        }
      }
      window.scrollTo({ top: Math.max(0, top + (restoreOffset.current || 0)), behavior: "instant" });
      restoreOffset.current = null; restored.current = key; setActive(requested);
    });
    return () => cancelAnimationFrame(token);
  }, [requested, rootId, frames, readyVersion, loadedRoot, anchor, memberQuery, markerTop]);
  useEffect(() => {
    if (!book || !rootId || !frames.length) return;
    let timer: ReturnType<typeof setTimeout>;
    const save = () => {
      if (!restored.current) return;
      const candidates = nodes.filter(n => n.rootId === rootId).map(n => ({ n, top: markerTop(n.id) })).filter(p => p.top !== null);
      const current = candidates.filter(p => p.top! <= window.scrollY + 40).at(-1)?.n || selected;
      if (!current) return;
      setActive(current.id);
      writeStored(positionKey(book.hash), { section: current.id, offset: window.scrollY - (markerTop(current.id) || 0), updated: Date.now() } satisfies Position);
    };
    const scroll = () => { clearTimeout(timer); timer = setTimeout(save, 180); };
    window.addEventListener("scroll", scroll); window.addEventListener("pagehide", save);
    return () => { clearTimeout(timer); save(); window.removeEventListener("scroll", scroll); window.removeEventListener("pagehide", save); };
  }, [book, rootId, frames, nodes, selected, markerTop]);
  useEffect(() => {
    if (mobileOpen) drawer.current?.showModal(); else if (drawer.current?.open) drawer.current.close();
  }, [mobileOpen]);
  function changePrefs(next: Preferences) {
    setPrefs(next); document.documentElement.dataset.theme = next.theme; writeStored("diorama:preferences", next);
  }
  function go(id: string) {
    setMobileOpen(false); setNotice(""); restoreOffset.current = 0; restored.current = "";
    router.push(readerUrl(bookId, id), { scroll: false });
    if (id === requested) setReadyVersion(v => v + 1);
  }
  const onLink = useCallback(async (member: string, targetAnchor: string) => {
    try {
      const result = await fetchJson<{ section: string; anchor: string }>(`/api/books/${encodeURIComponent(bookId)}/resolve?${new URLSearchParams({ member, anchor: targetAnchor })}`);
      restoreOffset.current = 0; restored.current = "";
      router.push(readerUrl(bookId, result.section, result.anchor) + `&member=${encodeURIComponent(member)}`, { scroll: false });
      setReadyVersion(v => v + 1);
    } catch (e) { setNotice((e as Error).message); }
  }, [bookId, router]);
  if (!book) return <main className="empty"><Link href="/" className="text-button"><ArrowLeft size={16} /> Library</Link><h1>{error ? "Unable to open this book" : "Opening your book…"}</h1>{error && <p role="alert">{error}</p>}</main>;
  const contents = <Contents navigation={book.navigation} active={active || requested || ""} onSelect={go} />;
  return <div className={`reader ${sidebar ? "with-sidebar" : ""}`}>
    <header className="app-bar reader-bar"><div className="bar-start"><Link href="/" className="back-library" aria-label="Back to library"><ArrowLeft size={17} /><span>Library</span></Link><span className="bar-divider" /><span className="bar-book-title">{book.title}</span></div>
      <div className="reader-actions"><button ref={contentsButton} className="icon-button" aria-label="Toggle contents" aria-expanded={mobileOpen || sidebar} onClick={() => matchMedia("(max-width: 800px)").matches ? setMobileOpen(!mobileOpen) : setSidebar(!sidebar)}><List size={19} /></button><span className="bar-divider" />
        <button className="icon-button" aria-label="Decrease text size" disabled={prefs.size <= 16} onClick={() => changePrefs({ ...prefs, size: prefs.size - 2 })}><Minus size={15} /></button><span className="text-size" aria-live="polite">Aa</span><button className="icon-button" aria-label="Increase text size" disabled={prefs.size >= 28} onClick={() => changePrefs({ ...prefs, size: prefs.size + 2 })}><Plus size={15} /></button><span className="bar-divider" />
        <button className="icon-button" aria-label={`Use ${prefs.theme === "light" ? "dark" : "light"} theme`} onClick={() => changePrefs({ ...prefs, theme: prefs.theme === "light" ? "dark" : "light" })}>{prefs.theme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button>
      </div>
    </header>
    <aside className="reader-sidebar"><div className="sidebar-book"><BookOpen size={22} strokeWidth={1.3} /><h2>{book.title}</h2><p>{book.author}</p></div>{contents}<div className="sidebar-bottom"><Check size={13} /> Reading position saved on this device</div></aside>
    <dialog ref={drawer} className="contents-dialog" onCancel={() => setMobileOpen(false)} onClose={() => { setMobileOpen(false); contentsButton.current?.focus(); }}><div className="dialog-top"><h2>Contents</h2><button className="icon-button" aria-label="Close contents" onClick={() => setMobileOpen(false)}><X size={20} /></button></div>{contents}</dialog>
    <main className="reader-main" id="reading-content"><div className="reading-topline"><span>{root?.type}{root?.index ? ` ${root.index}` : ""}</span><span>{book.author}</span></div>
      {book && requested && !selected && <div className="notice" role="alert">This section is no longer in the book. <button onClick={() => go(book.navigation.content[0]?.id || roots[0].id)}>Open the beginning</button></div>}
      {error && <div className="notice" role="alert">{error}<button onClick={() => location.reload()}>Reload</button></div>}
      {notice && <div className="notice" role="alert">{notice}<button onClick={() => setNotice("")}>Dismiss</button></div>}
      {!error && selected && loadedRoot !== rootId && <div className="reading-skeleton"><i /><i /><i /><i /></div>}
      <div className="reading-prose" style={{ maxWidth: `${prefs.size * 34}px` }}>{loadedRoot === rootId && frames.map(info => <BookFrame key={`${rootId}:${info.spine}`} info={info} prefs={prefs} onReady={onReady} onLink={onLink} onFailure={setError} url={`/api/books/${encodeURIComponent(bookId)}/document?${new URLSearchParams({ section: rootId, spine: String(info.spine) })}`} />)}</div>
      {root && <nav className="chapter-navigation" aria-label="Reading sections"><button disabled={rootIndex <= 0} onClick={() => go(roots[rootIndex - 1].id)}><ArrowLeft size={17} /><span><small>Previous section</small>{roots[rootIndex - 1]?.title || "Beginning of book"}</span></button><button disabled={rootIndex >= roots.length - 1} onClick={() => go(roots[rootIndex + 1].id)}><span><small>Next section</small>{roots[rootIndex + 1]?.title || "End of book"}</span><ArrowRight size={17} /></button></nav>}
    </main>
    <footer className="reading-footer"><span>{root?.title || ""}</span><span>{rootIndex >= 0 ? `${rootIndex + 1} / ${roots.length} sections` : ""}</span><div className="progress-track" aria-label="Section progress"><div style={{ width: `${percent}%` }} /></div></footer>
  </div>;
}
