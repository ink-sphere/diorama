import { JSDOM } from "jsdom";
import { readBook, readArchive, readMember, LibraryError } from "@/lib/library";
import { bookCsp, cleanCss, linkTarget, renderSection, sanitizeDocument, sectionMembers } from "@/lib/xhtml";
export const dynamic = "force-dynamic";
const imageTypes: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", avif: "image/avif",
  woff: "font/woff", woff2: "font/woff2", ttf: "font/ttf", otf: "font/otf",
};
export async function GET(request: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  const { id, action } = await params;
  const query = new URL(request.url).searchParams;
  try {
    const book = await readBook(id);
    const zip = await readArchive(id, book);
    const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": bookCsp };
    if (action === "section") return Response.json(sectionMembers(book, query.get("section") || "content/0"), { headers });
    if (action === "resolve") return Response.json(await linkTarget(book, zip, query.get("member") || "", query.get("anchor") || ""), { headers });
    if (action === "document") {
      const html = await renderSection(book, zip, id, query.get("section") || "content/0", Number(query.get("spine")));
      return new Response(html, { headers: { ...headers, "Content-Type": "application/xhtml+xml; charset=utf-8" } });
    }
    if (action === "asset") {
      const member = query.get("member") || "";
      const ext = member.split(".").pop()?.toLowerCase() || "";
      if (!["css", "svg", ...Object.keys(imageTypes)].includes(ext)) throw new LibraryError("Unsupported asset type", 403);
      const data = await readMember(zip, member);
      if (ext === "css") return new Response(cleanCss(data.toString("utf8"), id, member), { headers: { ...headers, "Content-Type": "text/css; charset=utf-8" } });
      if (ext === "svg") {
        const dom = new JSDOM(data, { contentType: "image/svg+xml" });
        try {
          sanitizeDocument(dom, id, member);
          return new Response(new dom.window.XMLSerializer().serializeToString(dom.window.document.documentElement), { headers: { ...headers, "Content-Type": "image/svg+xml" } });
        } finally { dom.window.close(); }
      }
      return new Response(new Uint8Array(data), { headers: { ...headers, "Content-Type": imageTypes[ext] } });
    }
    throw new LibraryError("Unknown book endpoint", 404);
  } catch (error) {
    return Response.json({ error: error instanceof LibraryError ? error.message : "This book content could not be rendered" }, { status: error instanceof LibraryError ? error.status : 422 });
  }
}
