import { JSDOM } from "jsdom";
import createDOMPurify from "dompurify";
import iconv from "iconv-lite";
import postcss from "postcss";
import valueParser from "postcss-value-parser";
import type JSZip from "jszip";
import { fragmentsOf, navigationOf, nodesOf, rootSection, type Book } from "./book";
import { assetUrl, LibraryError, readMember, resolveMember } from "./library";

const XHTML = "http://www.w3.org/1999/xhtml";
export const bookCsp = "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'";

export function cleanCss(css: string, id: string, member: string): string {
  try {
    const root = postcss.parse(css);
    root.walkAtRules(rule => {
      if (rule.name.toLowerCase() !== "import") return;
      const parsed = valueParser(rule.params);
      const first = parsed.nodes.find(n => n.type !== "space" && n.type !== "comment");
      const href = first?.type === "string" ? first.value : first?.type === "function" && first.value.toLowerCase() === "url" ? valueParser.stringify(first.nodes).replace(/^["']|["']$/g, "") : "";
      const target = resolveMember(member, href);
      if (!target || !href) { rule.remove(); return; }
      rule.params = `url("${assetUrl(id, target.member)}")`;
    });
    root.walkDecls(decl => {
      if (/expression\s*\(|javascript\s*:|-moz-binding|behavior\s*:/i.test(`${decl.prop}:${decl.value}`)) { decl.remove(); return; }
      const parsed = valueParser(decl.value);
      parsed.walk(n => {
        if (n.type !== "function" || n.value.toLowerCase() !== "url") return;
        const href = valueParser.stringify(n.nodes).replace(/^["']|["']$/g, "");
        const target = resolveMember(member, href);
        const url = href.startsWith("#") ? href : target ? assetUrl(id, target.member) : "";
        n.nodes = [{ type: "string", quote: '"', value: url, sourceIndex: 0, sourceEndIndex: 0 }];
      });
      decl.value = parsed.toString();
    });
    return root.toString();
  } catch { return ""; }
}

export function sanitizeDocument(dom: JSDOM, id: string, member: string): void {
  const doc = dom.window.document;
  for (const element of [...doc.querySelectorAll("*")]) {
    // A source base element must never change the meaning of our rewritten URLs.
    if (["script", "iframe", "object", "embed", "form", "input", "button", "base", "meta", "audio", "video"].includes(element.localName.toLowerCase())) {
      element.remove(); continue;
    }
    for (const attr of [...element.attributes]) {
      const name = attr.localName.toLowerCase();
      if (name.startsWith("on") || ["srcdoc", "srcset", "ping", "action", "formaction", "target"].includes(name)) element.removeAttributeNode(attr);
    }
    if (element.localName === "style") element.textContent = cleanCss(element.textContent || "", id, member);
    if (element.hasAttribute("style")) {
      const cleaned = cleanCss(`x{${element.getAttribute("style")}}`, id, member);
      element.setAttribute("style", cleaned.slice(cleaned.indexOf("{") + 1, cleaned.lastIndexOf("}")));
    }
    for (const attr of [...element.attributes]) {
      if (!["href", "src", "poster"].includes(attr.localName)) continue;
      const href = attr.value;
      const resolved = resolveMember(member, href);
      const anchor = element.localName === "a";
      if (anchor && /^https?:\/\//i.test(href)) {
        element.setAttribute("data-external-link", href);
        element.setAttribute("href", "#");
      } else if (anchor && resolved) {
        element.setAttribute("data-book-member", resolved.member);
        element.setAttribute("data-book-anchor", resolved.hash);
        element.setAttribute("href", `#${encodeURIComponent(resolved.hash)}`);
      } else if (resolved) {
        attr.value = href.startsWith("#") ? href : assetUrl(id, resolved.member) + (resolved.hash ? `#${encodeURIComponent(resolved.hash)}` : "");
      } else element.removeAttributeNode(attr);
    }
    if (element.localName === "link" && element.getAttribute("rel") !== "stylesheet") element.remove();
  }
  const purifier = createDOMPurify(dom.window);
  purifier.sanitize(doc.documentElement, {
    IN_PLACE: true, WHOLE_DOCUMENT: true, ADD_TAGS: ["link"],
    ADD_ATTR: ["data-section", "data-book-member", "data-book-anchor", "data-external-link"],
    FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "base", "meta"],
  });
}

export function sectionMembers(book: Book, sectionId: string): { member: string; spine: number }[] {
  const seen = new Set<number>();
  return fragmentsOf(rootSection(book, sectionId)).flatMap(f => {
    if (seen.has(f.source.spine_index)) return [];
    seen.add(f.source.spine_index);
    return [{ member: f.source.member, spine: f.source.spine_index }];
  });
}

function xmlSource(bytes: Buffer, encoding: string): string {
  if (!iconv.encodingExists(encoding)) throw new LibraryError("Unsupported source encoding");
  const text = iconv.decode(bytes, encoding);
  if (/<!ENTITY/i.test(text)) throw new LibraryError("Custom XML entities are unsupported");
  return text.replace(/<\?xml[^?]*\?>/, "");
}

export async function renderSection(book: Book, zip: JSZip, id: string, sectionId: string, spine: number): Promise<string> {
  const fragments = fragmentsOf(rootSection(book, sectionId)).filter(f => f.source.spine_index === spine);
  if (!fragments.length) throw new LibraryError("Section document not found", 404);
  const { member, encoding } = fragments[0].source;
  const bytes = await readMember(zip, member);
  let end = fragments[0].source.start_byte;
  for (const f of fragments) {
    if (f.source.member !== member || f.source.start_byte !== end || f.source.end_byte <= end || f.source.end_byte > bytes.length) throw new LibraryError("Overlapping or incomplete section source ranges");
    if (iconv.decode(bytes.subarray(f.source.start_byte, f.source.end_byte), encoding) !== f.xhtml) throw new LibraryError("Saved XHTML does not match the source EPUB");
    end = f.source.end_byte;
  }
  const start = fragments[0].source.start_byte;
  const nodes = nodesOf(navigationOf(book)).filter(n => n.rootId === sectionId && n.start?.spine_index === spine);
  const insertions = new Map<number, string[]>();
  const add = (offset: number, text: string) => insertions.set(offset, [...(insertions.get(offset) || []), text]);
  const nonce = crypto.randomUUID();
  add(start, `<!--${nonce}-start-->`);
  nodes.forEach((n, i) => { if (n.start) add(n.start.start_byte, `<!--${nonce}-node-${i}-->`); });
  add(end, `<!--${nonce}-end-->`);
  const pieces: Buffer[] = [];
  let cursor = 0;
  for (const [offset, markers] of [...insertions].sort(([a], [b]) => a - b)) {
    pieces.push(bytes.subarray(cursor, offset), iconv.encode(markers.join(""), encoding)); cursor = offset;
  }
  pieces.push(bytes.subarray(cursor));
  let dom: JSDOM;
  try { dom = new JSDOM(xmlSource(Buffer.concat(pieces), encoding), { contentType: "application/xhtml+xml" }); }
  catch { throw new LibraryError("Source XHTML is not well-formed"); }
  try {
    const doc = dom.window.document;
    const walker = doc.createTreeWalker(doc, dom.window.NodeFilter.SHOW_COMMENT);
    const markers = new Map<string, Node>();
    while (walker.nextNode()) if (walker.currentNode.nodeValue?.startsWith(nonce)) markers.set(walker.currentNode.nodeValue, walker.currentNode);
    const a = markers.get(`${nonce}-start`), b = markers.get(`${nonce}-end`);
    if (!a || !b) throw new LibraryError("Cannot locate section source boundaries");
    const range = doc.createRange();
    range.setStartAfter(a); range.setEndBefore(b);
    let content: Node = range.cloneContents();
    let ancestor = range.commonAncestorContainer;
    if (ancestor.nodeType !== 1) ancestor = ancestor.parentNode!;
    while (ancestor && (ancestor as Element).localName !== "body") {
      const wrapper = ancestor.cloneNode(false);
      wrapper.appendChild(content); content = wrapper; ancestor = ancestor.parentNode!;
    }
    const body = doc.getElementsByTagNameNS(XHTML, "body")[0];
    if (!body) throw new LibraryError("Missing XHTML body");
    body.replaceChildren(content);
    const remaining = doc.createTreeWalker(body, dom.window.NodeFilter.SHOW_COMMENT);
    const replacements: Node[] = [];
    while (remaining.nextNode()) replacements.push(remaining.currentNode);
    for (const comment of replacements) {
      const index = nodes.findIndex((_, i) => comment.nodeValue === `${nonce}-node-${i}`);
      if (index < 0) continue;
      const marker = doc.createElementNS(XHTML, "span");
      marker.setAttribute("data-section", nodes[index].id);
      marker.setAttribute("class", "diorama-position");
      comment.parentNode!.replaceChild(marker, comment);
    }
    sanitizeDocument(dom, id, member);
    const style = doc.createElementNS(XHTML, "style");
    style.textContent = READER_CSS;
    doc.getElementsByTagNameNS(XHTML, "head")[0]?.appendChild(style);
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + new dom.window.XMLSerializer().serializeToString(doc.documentElement);
  } finally { dom.window.close(); }
}

export async function linkTarget(book: Book, zip: JSZip, member: string, anchor: string): Promise<{ section: string; anchor: string }> {
  const all = nodesOf(navigationOf(book));
  const candidates = all.filter(n => n.start?.member === member);
  const fallbackRoot = candidates[0]?.rootId || nodesOf(navigationOf(book)).find(n => {
    return n.id === n.rootId && fragmentsOf(rootSection(book, n.id)).some(f => f.source.member === member);
  })?.rootId;
  if (!fallbackRoot) throw new LibraryError("Linked page is not in this book's contents", 404);
  if (!anchor) return { section: fallbackRoot, anchor: "" };
  const fragments = [...book.front_matter, ...book.content, ...book.back_matter].flatMap(fragmentsOf).filter(f => f.source.member === member);
  const encoding = fragments[0]?.source.encoding || "utf-8";
  const raw = await readMember(zip, member);
  const dom = new JSDOM(xmlSource(raw, encoding), { contentType: "application/xhtml+xml" });
  try {
    const element = dom.window.document.getElementById(anchor);
    if (!element) throw new LibraryError("Linked anchor was not found", 404);
    // Locate the anchor's opening tag in original decoded source, then convert
    // its character position to the byte coordinate used by book.json.
    const decoded = iconv.decode(raw, encoding);
    const escaped = anchor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const match = new RegExp(`<[^>]+\\b(?:id|xml:id)=["']${escaped}["'][^>]*>`).exec(decoded);
    const byte = match ? iconv.encode(decoded.slice(0, match.index), encoding).length + (raw[0] === 0xff || raw[0] === 0xfe ? 2 : raw.subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])) ? 3 : 0) : -1;
    const root = [...book.front_matter.map((s, i) => ({ s, id: `front_matter/${i}` })), ...book.content.map((s, i) => ({ s, id: `content/${i}` })), ...book.back_matter.map((s, i) => ({ s, id: `back_matter/${i}` }))].find(({ s }) => fragmentsOf(s).some(f => f.source.member === member && f.source.start_byte <= byte && f.source.end_byte > byte));
    return { section: root?.id || fallbackRoot, anchor };
  } finally { dom.window.close(); }
}

export const READER_CSS = `
:root { color-scheme: light dark; --reader-bg: oklch(1 0 0); --reader-ink: oklch(.25 .008 110); --reader-link: oklch(.42 .075 110); }
:root[data-theme="dark"] { --reader-bg: oklch(.18 0 0); --reader-ink: oklch(.89 .006 110); --reader-link: oklch(.8 .085 110); }
html, body { margin:0!important; padding:0!important; width:auto!important; max-width:none!important; min-width:0!important; background:var(--reader-bg)!important; color:var(--reader-ink)!important; }
body { font-family:Georgia,"Times New Roman",serif!important; font-size:var(--reader-size,20px)!important; line-height:1.8!important; overflow-wrap:break-word; text-align:start!important; }
body * { color:inherit!important; background-color:transparent!important; }
body > * { max-width:100%!important; }
h1,h2,h3,h4 { line-height:1.35!important; text-wrap:balance; font-weight:normal; }
h1,h2 { margin:1.2em 0!important; } p { line-height:inherit!important; }
a { color:var(--reader-link)!important; text-decoration-thickness:1px; text-underline-offset:3px; }
img,svg { max-width:100%!important; height:auto; max-height:80vh; object-fit:contain; }
table { max-width:100%; } pre { white-space:pre-wrap; } .chapter { margin:0!important; }
.diorama-position { display:inline; width:0; height:0; scroll-margin-top:100px; }
:focus-visible { outline:2px solid var(--reader-link); outline-offset:3px; }
@media(prefers-reduced-motion:reduce) { * { scroll-behavior:auto!important; animation:none!important; transition:none!important; } }
`;
