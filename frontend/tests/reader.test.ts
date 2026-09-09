import { afterEach, describe, expect, it } from "vitest";
import { JSDOM } from "jsdom";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fixture } from "./fixtures";
import { bookSchema, fragmentsOf, navigationOf, nodesOf, rootSection } from "../src/lib/book";
import { bookDirectory, listBooks, readBook, resolveMember, safeMember } from "../src/lib/library";
import { cleanCss, linkTarget, renderSection, sanitizeDocument } from "../src/lib/xhtml";

const tempDirs: string[] = [];
afterEach(async () => { delete process.env.DIORAMA_LIBRARY_DIR; await Promise.all(tempDirs.splice(0).map(dir => fs.rm(dir, { recursive: true, force: true }))); });

describe("book navigation", () => {
  it("preserves four levels and distinguishes repeated titles", async () => {
    const { book } = await fixture(); book.content.push(structuredClone(book.content[0]));
    const nodes = nodesOf(navigationOf(book));
    expect(new Set(nodes.map(n => n.id)).size).toBe(nodes.length);
    expect(nodes[3].id).toBe("content/0/sub-sections/0/sub-sections/0/sub-sections/0");
    expect(nodes[3].rootId).toBe("content/0");
    expect(() => rootSection(book, "content/0/sub-sections/0")).toThrow();
  });
  it("orders direct fragments and children without duplicates", async () => {
    const { book } = await fixture();
    const parts = fragmentsOf(book.content[0]);
    expect(parts).toHaveLength(5);
    expect(parts[3].xhtml).toContain("Śloka");
    expect(parts[4].xhtml).toContain("Parent interlude");
  });
  it("rejects unsupported schemas", async () => { const { book } = await fixture(); expect(bookSchema.safeParse({ ...book, schema_version: 2 }).success).toBe(false); });
});

describe("XHTML reconstruction", () => {
  it("reconstructs partial wrappers, all nested content, XHTML namespaces and assets", async () => {
    const { book, zip } = await fixture();
    const html = await renderSection(book, zip, "nested", "content/0", 0);
    const dom = new JSDOM(html, { contentType: "application/xhtml+xml" });
    const doc = dom.window.document;
    expect(doc.documentElement.namespaceURI).toBe("http://www.w3.org/1999/xhtml");
    expect(doc.querySelectorAll("[data-section]")).toHaveLength(4);
    expect(doc.querySelectorAll("#verse")).toHaveLength(1);
    expect(doc.querySelector("#verse em")?.textContent).toBe("Original verse.");
    expect(doc.querySelector("img")?.getAttribute("src")).toContain("OPS%2Fart%2Fpixel.png");
    expect(doc.querySelector("link")?.getAttribute("href")).toContain("OPS%2Fstyle.css");
    expect(doc.querySelector("a")?.getAttribute("data-book-member")).toBe("OPS/notes.xhtml");
    dom.window.close();
  });
  it("keeps ancestor wrappers when the top-level section starts within a div", async () => {
    const { book, zip } = await fixture();
    const first = book.content[0].content.shift()!;
    book.front_matter = [{ title: "Opening", type: "Opening", index: null, content: [first], "sub-sections": [] }];
    const html = await renderSection(book, zip, "nested", "content/0", 0);
    const dom = new JSDOM(html, { contentType: "application/xhtml+xml" });
    expect(dom.window.document.querySelector("div.act section #scene")).not.toBeNull();
    expect(dom.window.document.getElementById("act")).toBeNull();
    dom.window.close();
  });
  it("rejects gaps, overlaps and modified source text", async () => {
    const { book, zip } = await fixture();
    book.content[0].content[0].xhtml += "changed";
    await expect(renderSection(book, zip, "nested", "content/0", 0)).rejects.toThrow("does not match");
  });
  it("resolves cross-document footnotes", async () => {
    const { book, zip } = await fixture();
    expect(await linkTarget(book, zip, "OPS/notes.xhtml", "note")).toEqual({ section: "back_matter/0", anchor: "note" });
    await expect(linkTarget(book, zip, "OPS/notes.xhtml", "missing")).rejects.toThrow();
  });
  it("removes active XHTML, unsafe SVG and external asset URLs", () => {
    const dom = new JSDOM('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>T</title></head><body onload="alert(1)"><script>alert(1)</script><iframe src="/"/><a href="javascript:alert(1)">bad</a><img src="https://external.invalid/x"/><svg xmlns="http://www.w3.org/2000/svg"><script>bad</script><image href="https://external.invalid/x"/></svg></body></html>', { contentType: "application/xhtml+xml" });
    sanitizeDocument(dom, "nested", "OPS/main.xhtml");
    const html = dom.serialize();
    expect(html).not.toMatch(/<script|<iframe|onload|javascript:|external\.invalid/);
    dom.window.close();
  });
  it("rewrites CSS resources and strips remote imports", () => {
    const css = cleanCss('@import "other.css"; @import "https://external.invalid/style.css"; .a{background:url("art/pixel.png");color:red} .b{width:expression(alert(1))}', "nested", "OPS/style.css");
    expect(css).toContain("OPS%2Fother.css"); expect(css).toContain("OPS%2Fart%2Fpixel.png");
    expect(css).not.toContain("external.invalid"); expect(css).not.toContain("expression");
  });
});

describe("local library boundaries", () => {
  it("handles empty libraries, invalid books, missing archives, and symlinks", async () => {
    const dir = await fs.mkdtemp(path.join(os.tmpdir(), "diorama-reader-")); tempDirs.push(dir); process.env.DIORAMA_LIBRARY_DIR = dir;
    expect(await listBooks()).toEqual([]);
    const { book, zip } = await fixture();
    await fs.mkdir(path.join(dir, "good"));
    await fs.writeFile(path.join(dir, "good/book.json"), JSON.stringify(book));
    await fs.writeFile(path.join(dir, "good/source.epub"), await zip.generateAsync({ type: "nodebuffer" }));
    await fs.mkdir(path.join(dir, "bad")); await fs.writeFile(path.join(dir, "bad/book.json"), "invalid");
    const listing = await listBooks(); expect(listing.find(b => b.id === "good")?.error).toBeUndefined(); expect(listing.find(b => b.id === "bad")?.error).toBeTruthy();
    await fs.symlink(path.join(dir, "good"), path.join(dir, "linked"));
    await expect(bookDirectory("linked")).rejects.toThrow("Linked");
    await expect(readBook("../good")).rejects.toThrow("Invalid");
    await fs.unlink(path.join(dir, "good/source.epub"));
    expect((await listBooks()).find(b => b.id === "good")?.error).toContain("Missing");
  });
  it("confines resource paths", () => {
    expect(() => safeMember("../secret")).toThrow(); expect(() => safeMember("/etc/passwd")).toThrow();
    expect(resolveMember("OPS/main.xhtml", "art/pixel.png")).toEqual({ member: "OPS/art/pixel.png", hash: "" });
    expect(resolveMember("OPS/main.xhtml", "javascript:alert(1)")).toBeNull();
    expect(resolveMember("OPS/main.xhtml", "https://external.invalid/a")).toBeNull();
  });
});
