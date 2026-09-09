import JSZip from "jszip";
import type { Book, Fragment, Section } from "../src/lib/book";

export async function fixture() {
  const head = '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Nested book</title><link rel="stylesheet" href="style.css"/></head><body>';
  const part1 = '<div class="act"><h1 id="act">Act I</h1><p>Before the scenes.</p>';
  const part2 = '<section><h2 id="scene">The orchard</h2><p>Scene introduction.</p>';
  const part3 = '<div><h3 id="adh">Adhyāya</h3><p>Opening verse.</p>';
  const part4 = '<p id="verse">Śloka १<br/><em>Original verse.</em></p>';
  const part5 = '<p>Parent interlude.</p></div><p>Scene ending.</p></section><p>Act ending.</p><a href="notes.xhtml#note">Read note</a><img src="art/pixel.png" alt="Illustration"/></div>';
  const body = part1 + part2 + part3 + part4 + part5;
  const raw = head + body + '</body></html>';
  const chunks = [part1, part2, part3, part4, part5];
  let offset = Buffer.byteLength(head);
  const fragments: Fragment[] = chunks.map(xhtml => {
    const start = offset; offset += Buffer.byteLength(xhtml);
    return { xhtml, source: { member: "OPS/main.xhtml", spine_index: 0, start_byte: start, end_byte: offset, encoding: "utf-8" } };
  });
  const node = (title: string, type: string, content: Fragment[], children: Section[] = []): Section => ({ title, type, index: "I", content, "sub-sections": children });
  const verse = node("Śloka", "Śloka", [fragments[3]]);
  const adh = node("Adhyāya", "Adhyāya", [fragments[2]], [verse]);
  const scene = node("The orchard", "Scene", [fragments[1]], [adh]);
  const act = node("Act I", "Act", [fragments[0], fragments[4]], [scene]);
  const noteBody = '<h1>Notes</h1><p id="note">A note with an <a href="main.xhtml#scene">internal return link</a>.</p>';
  const noteRaw = head + noteBody + '</body></html>';
  const note = node("Notes", "Notes", [{ xhtml: noteBody, source: { member: "OPS/notes.xhtml", spine_index: 1, start_byte: Buffer.byteLength(head), end_byte: Buffer.byteLength(head + noteBody), encoding: "utf-8" } }]);
  const book: Book = { schema_version: 1, title: "A Book of Many Parts", author: "Diorama Test", metadata: {}, source_sha256: "fixture-hash", source_archive: "source.epub", front_matter: [], content: [act], back_matter: [note] };
  const zip = new JSZip();
  zip.file("OPS/main.xhtml", raw); zip.file("OPS/notes.xhtml", noteRaw);
  zip.file("OPS/style.css", '.act {color: black} p {margin: 1em 0}');
  zip.file("OPS/art/pixel.png", Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6S1sAAAAASUVORK5CYII=", "base64"));
  return { book, zip, raw };
}
