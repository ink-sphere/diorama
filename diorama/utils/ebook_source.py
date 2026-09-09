"""Read EPUB source bytes without HTML reserialization."""

from __future__ import annotations

import codecs
import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.parsers import expat

from ..models.ebook_models import (
    ContentFragment,
    EbookDocument,
    EbookSection,
    SectionPlan,
    SourceReference,
    StructurePlan,
)


class EbookLoadError(ValueError):
    """An ebook could not be read or its structure could not be validated."""


def local(name: str) -> str:
    return name.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def member_path(base: str, href: str) -> str:
    url = urlsplit(href)
    if url.scheme or url.netloc:
        raise EbookLoadError(f"External package reference: {href}")
    result = posixpath.normpath(
        posixpath.join(posixpath.dirname(base), unquote(url.path))
    )
    if result.startswith(("../", "/")) or result == "..":
        raise EbookLoadError(f"Unsafe package reference: {href}")
    return result


def xml(data: bytes) -> ET.Element:
    # No external entities or internal entity expansion from untrusted ebooks.
    if b"<!ENTITY" in data.replace(b"\x00", b"").upper():
        raise EbookLoadError("XML entity declarations are unsupported")
    return ET.fromstring(data)


def encoding_of(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"<\x00")):
        return "utf-16-le"
    if data.startswith((b"\xfe\xff", b"\x00<")):
        return "utf-16-be"
    match = re.search(rb'<\?xml[^>]*encoding=[\'"]([^\'"]+)', data[:256])
    encoding = match[1].decode("ascii") if match else "utf-8"
    name = codecs.lookup(encoding).name
    return "utf-8" if name == "utf-8-sig" else name


@dataclass
class SourceUnit:
    member: str
    spine_index: int
    start: int
    end: int
    encoding: str
    tag: str = ""
    anchor: str | None = None
    heading: str | None = None


class EbookSource:
    def __init__(self, path: Path):
        try:
            self.archive = path.read_bytes()
            self.sha256 = hashlib.sha256(self.archive).hexdigest()
            self.members: dict[str, bytes] = {}
            self.units: list[SourceUnit] = []
            self.navigation: list[dict] = []
            self.spine: list[dict] = []
            with zipfile.ZipFile(io.BytesIO(self.archive)) as archive:
                if len(set(archive.namelist())) != len(archive.namelist()):
                    raise EbookLoadError("Duplicate ZIP member names")
                if sum(i.file_size for i in archive.infolist()) > 1_000_000_000:
                    raise EbookLoadError("EPUB expanded size exceeds 1 GB")

                def read(name):
                    if archive.getinfo(name).file_size > 256_000_000:
                        raise EbookLoadError(f"EPUB member exceeds 256 MB: {name}")
                    return archive.read(name)

                container = xml(read("META-INF/container.xml"))
                rootfile = next(
                    e for e in container.iter() if local(e.tag) == "rootfile"
                )
                package_path = member_path("", rootfile.attrib["full-path"])
                package = xml(read(package_path))
                metadata = next(e for e in package if local(e.tag) == "metadata")

                def element(e):
                    return {
                        "tag": e.tag,
                        "attributes": dict(e.attrib),
                        "text": e.text,
                        "tail": e.tail,
                        "children": [element(c) for c in e],
                    }

                self.metadata = {
                    "package_attributes": dict(package.attrib),
                    "metadata_attributes": dict(metadata.attrib),
                    "elements": [element(e) for e in metadata],
                }
                self.title = next(
                    (
                        "".join(e.itertext())
                        for e in metadata
                        if local(e.tag) == "title"
                    ),
                    path.stem,
                )
                creators = [e for e in metadata if local(e.tag) == "creator"]
                author_ids = {
                    e.attrib.get("refines", "").lstrip("#")
                    for e in metadata
                    if e.attrib.get("property") == "role" and e.text == "aut"
                }
                author = next(
                    (
                        e
                        for e in creators
                        if e.attrib.get("id") in author_ids
                        or any(
                            local(k) == "role" and v == "aut"
                            for k, v in e.attrib.items()
                        )
                    ),
                    None,
                )
                if author is None and creators:
                    author = creators[0]
                self.author = "".join(author.itertext()) if author is not None else None
                manifest = next(e for e in package if local(e.tag) == "manifest")
                items = {e.attrib["id"]: e.attrib for e in manifest}
                spine = next(e for e in package if local(e.tag) == "spine")
                encrypted = set()
                if "META-INF/encryption.xml" in archive.namelist():
                    encrypted = {
                        member_path("", e.attrib["URI"])
                        for e in xml(read("META-INF/encryption.xml")).iter()
                        if local(e.tag) == "CipherReference"
                    }
                for i, ref in enumerate(spine):
                    item = items[ref.attrib["idref"]]
                    name = member_path(package_path, item["href"])
                    if name in encrypted:
                        raise EbookLoadError(f"Encrypted spine content: {name}")
                    if item["media-type"] not in ("application/xhtml+xml", "text/html"):
                        raise EbookLoadError(
                            f"Unsupported spine media type: {item['media-type']}"
                        )
                    raw = read(name)
                    self.members[name] = raw
                    start = len(self.units)
                    self._index(name, i, raw)
                    self.spine.append(
                        {
                            "member": name,
                            "start": start,
                            "end": len(self.units),
                            "linear": ref.attrib.get("linear", "yes"),
                        }
                    )
                for item in items.values():
                    if (
                        "nav" in item.get("properties", "").split()
                        or item.get("media-type") == "application/x-dtbncx+xml"
                    ):
                        name = member_path(package_path, item["href"])
                        self.navigation.append(
                            {"member": name, "tree": element(xml(read(name)))}
                        )
            if not self.units:
                raise EbookLoadError("EPUB has no spine body content")
        except EbookLoadError:
            raise
        except (
            OSError,
            KeyError,
            StopIteration,
            ValueError,
            LookupError,
            zipfile.BadZipFile,
            ET.ParseError,
            expat.ExpatError,
            RuntimeError,
        ) as exc:
            raise EbookLoadError(f"Cannot read EPUB {path}: {exc}") from exc

    def _index(self, name: str, spine_index: int, raw: bytes):
        xml(raw)
        encoding = encoding_of(raw)
        parser = expat.ParserCreate(namespace_separator="}")
        cuts: dict[int, dict] = {}
        body_depth = 0
        depth = 0
        body_end = None
        headings: list[tuple[int, list[str]]] = []

        def start(tag, attrs):
            nonlocal depth, body_depth
            depth += 1
            tag = local(tag)
            offset = parser.CurrentByteIndex
            if tag == "body":
                if body_depth or cuts:
                    raise EbookLoadError(f"Multiple body elements: {name}")
                body_depth = depth
                # The first > outside attribute quotes ends the body start tag.
                text = raw[offset:].decode(encoding)
                match = re.match(r"""<[^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""", text)
                if not match:
                    raise EbookLoadError(f"Malformed body start: {name}")
                cuts[offset + len(match[0].encode(encoding))] = {}
            elif body_depth:
                cuts[offset] = {
                    "tag": tag,
                    "anchor": attrs.get("id")
                    or attrs.get("http://www.w3.org/XML/1998/namespace}id"),
                }
                if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    headings.append((offset, []))

        def chars(text):
            for _, parts in headings:
                parts.append(text)

        def end(tag):
            nonlocal depth, body_depth, body_end
            tag = local(tag)
            if headings and tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                offset, parts = headings.pop()
                cuts[offset]["heading"] = "".join(parts)
            if tag == "body" and body_depth == depth:
                body_end = parser.CurrentByteIndex
                body_depth = 0
            elif body_depth:
                # Tail text after a child can belong to its parent. Expat points
                # at </tag> for normal elements and past /> for empty elements.
                offset = parser.CurrentByteIndex
                closing = "</".encode(encoding)
                if raw[offset : offset + len(closing)] == closing:
                    delimiter = ">".encode(encoding)
                    stop = offset
                    while raw[stop : stop + len(delimiter)] != delimiter:
                        stop += len(delimiter)
                    offset = stop + len(delimiter)
                cuts.setdefault(offset, {"tag": "#tail"})
            depth -= 1

        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = chars
        parser.Parse(raw, True)
        if body_end is None:
            raise EbookLoadError(f"Missing XHTML body: {name}")
        offsets = sorted(o for o in cuts if o < body_end) + [body_end]
        for a, b in zip(offsets, offsets[1:]):
            self.units.append(SourceUnit(name, spine_index, a, b, encoding, **cuts[a]))

    def describe(self, start: int, limit: int) -> list[dict]:
        return [
            {
                "id": i,
                "member": u.member,
                "tag": u.tag,
                "anchor": u.anchor,
                "heading": u.heading[:1000] if u.heading else None,
                "preview": self.members[u.member][u.start : u.end].decode(u.encoding)[
                    :400
                ],
            }
            for i in range(start, min(start + limit, len(self.units)))
            for u in [self.units[i]]
        ]

    def fragments(self, start: int, end: int) -> list[ContentFragment]:
        result = []
        for u in self.units[start:end]:
            if (
                result
                and result[-1].source.spine_index == u.spine_index
                and result[-1].source.end_byte == u.start
            ):
                result[-1].source.end_byte = u.end
                result[-1].xhtml += self.members[u.member][u.start : u.end].decode(
                    u.encoding
                )
            else:
                result.append(
                    ContentFragment(
                        xhtml=self.members[u.member][u.start : u.end].decode(
                            u.encoding
                        ),
                        source=SourceReference(
                            member=u.member,
                            spine_index=u.spine_index,
                            start_byte=u.start,
                            end_byte=u.end,
                            encoding=u.encoding,
                        ),
                    )
                )
        return result

    def materialize(self, plan: StructurePlan) -> EbookDocument:
        def section(node: SectionPlan) -> EbookSection:
            if node.start >= node.end or node.end > len(self.units):
                raise EbookLoadError(f"Invalid range [{node.start}, {node.end})")
            cursor = node.start
            content = []
            children = []
            for child in node.sub_sections:
                if child.start < cursor or child.end > node.end:
                    raise EbookLoadError(
                        "Subsections overlap, are unordered, or escape their parent"
                    )
                content.extend(self.fragments(cursor, child.start))
                children.append(section(child))
                cursor = child.end
            content.extend(self.fragments(cursor, node.end))
            return EbookSection(
                title=node.title,
                index=node.index,
                type=node.type,
                content=content,
                sub_sections=children,
            )

        cursor = 0
        groups = {}
        for group in ("front_matter", "content", "back_matter"):
            groups[group] = []
            for node in getattr(plan, group):
                if node.start != cursor:
                    raise EbookLoadError(
                        f"Expected next section at unit {cursor}, got {node.start}; gap or overlap"
                    )
                groups[group].append(section(node))
                cursor = node.end
        if cursor != len(self.units):
            raise EbookLoadError(
                f"Unassigned source units [{cursor}, {len(self.units)})"
            )
        if not plan.content:
            raise EbookLoadError("No main literary content was identified")
        return EbookDocument(
            title=self.title,
            author=self.author,
            metadata=self.metadata,
            source_sha256=self.sha256,
            **groups,
        )
