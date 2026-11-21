"""Read EPUB source positions and verify the hierarchy proposed by Geppetto."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from lxml import etree

from diorama.schemas.book import Book, BookNode, Passage, Section, SourceRange

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_DOCUMENT = 16 * 1024 * 1024
BLOCKS = frozenset(
    "address article aside blockquote dd div dl dt figcaption figure footer h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table tr ul".split()
)


class InvalidEpub(ValueError):
    """The supplied edition cannot be indexed faithfully."""


class InvalidCandidate(ValueError):
    """A hierarchy does not describe the supplied edition."""


@dataclass(frozen=True)
class TextPosition:
    """Map an interval in `Document.text` to an XPath in its XHTML resource.

    The slot is `text` for an element's text, `tail` for text following the
    element, or `break` for a newline emitted by the indexer.
    """

    start: int
    end: int
    xpath: str
    slot: str


@dataclass(frozen=True)
class Heading:
    """An XHTML heading's start offset, stripped title, and level (1 through 6)."""

    offset: int
    title: str
    level: int
    anchor: str | None = None


@dataclass(frozen=True)
class Document:
    """Indexed text and markup evidence for one entry in the EPUB spine.

    `linear` marks a main reading-order entry. `links` holds href values as
    written in the document, including relative references to notes or assets.
    """

    occurrence: int
    resource: str
    linear: bool
    text: str
    positions: list[TextPosition]
    headings: list[Heading]
    links: list[str]


@dataclass(frozen=True)
class Navigation:
    """A table-of-contents entry with an archive-relative target and nesting depth."""

    title: str
    target: str
    depth: int


@dataclass
class SourceIndex:
    """An in-memory index of an edition's metadata, spine occurrences, and navigation."""

    source_sha256: str
    title: str
    authors: list[str]
    spine: list[Document] = field(default_factory=list)
    navigation: list[Navigation] = field(default_factory=list)
    cover_resource: str | None = None


def _tag(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def _resource(base: str, href: str) -> str:
    url = urlsplit(href)
    path = unquote(url.path)
    if url.scheme or url.netloc or path.startswith("/") or "\\" in path:
        raise InvalidEpub(f"Expected an EPUB-local reference in {base}")
    name = (
        posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
        if path
        else base
    )
    if not name or name == ".." or name.startswith("../"):
        raise InvalidEpub("Resource falls outside the EPUB")
    return name


def _xml(archive: ZipFile, name: str) -> etree._Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_DOCUMENT:
        raise InvalidEpub(f"Oversized document: {name}")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    root = etree.fromstring(archive.read(info), parser)
    if any(isinstance(node, etree._Entity) for node in root.iter()):
        raise InvalidEpub(f"Unsupported entity in {name}")
    return root


def _document(root: etree._Element, name: str) -> Document:
    body = root.find("{*}body")
    if body is None:
        raise InvalidEpub(f"Missing XHTML body: {name}")
    tree = root.getroottree()
    chunks: list[str] = []
    positions: list[TextPosition] = []
    headings: list[Heading] = []
    links: list[str] = []
    offset = 0

    def emit(path: str, slot: str, text: str | None) -> None:
        nonlocal offset
        if text:
            end = offset + len(text)
            positions.append(TextPosition(offset, end, path, slot))
            chunks.append(text)
            offset = end

    def visit(node: etree._Element, path: str) -> None:
        tag = _tag(node)
        if not tag or tag in {"script", "style"} or "hidden" in node.attrib:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            headings.append(
                Heading(
                    offset,
                    "".join(node.itertext()).strip(),
                    int(tag[1]),
                    node.get("id"),
                )
            )
        if tag == "a" and node.get("href"):
            links.append(node.get("href"))
        if tag == "br":
            emit(path, "break", "\n")
        emit(path, "text", node.text)
        # XPath node() counts text and tails alongside elements, comments and PIs.
        child_index = 2 if node.text else 1
        for child in node:
            child_path = f"{path}/node()[{child_index}]"
            visit(child, child_path)
            emit(child_path, "tail", child.tail)
            child_index += 2 if child.tail else 1
        if tag in BLOCKS and chunks and not chunks[-1].endswith("\n"):
            emit(path, "break", "\n")

    visit(body, tree.getpath(body))
    return Document(0, name, True, "".join(chunks), positions, headings, links)


def _navigation(root: etree._Element, path: str, ncx: bool) -> list[Navigation]:
    entries = []
    if ncx:
        for point in root.iterfind(".//{*}navPoint"):
            target = point.find("{*}content")
            label = point.findtext("{*}navLabel/{*}text", "")
            if target is not None and target.get("src"):
                entries.append(
                    (
                        label,
                        target.get("src"),
                        sum(_tag(p) == "navPoint" for p in point.iterancestors()),
                    )
                )
    else:
        for nav in root.iterfind(".//{*}nav"):
            if "toc" not in nav.get("{http://www.idpf.org/2007/ops}type", "").split():
                continue
            for link in nav.iterfind(".//{*}a"):
                if link.get("href"):
                    entries.append(
                        (
                            "".join(link.itertext()).strip(),
                            link.get("href"),
                            max(
                                0,
                                sum(_tag(p) == "ol" for p in link.iterancestors()) - 1,
                            ),
                        )
                    )
    result = []
    for title, href, depth in entries:
        url = urlsplit(href)
        if not url.scheme and not url.netloc:
            target = _resource(path, href)
            if url.fragment:
                target += "#" + url.fragment
            result.append(Navigation(title, target, depth))
    return result


def inspect_epub(source_epub: Path | str) -> SourceIndex:
    """Read a local EPUB and index its XHTML documents in spine order.

    The index includes the archive's SHA-256 digest, title and creators,
    headings, text positions, and local EPUB 2 NCX or EPUB 3 navigation
    entries when present. Repeated references to one document get distinct
    occurrence numbers. Nonlinear entries remain available for inspection.

    Raises `InvalidEpub` for an unreadable or unsupported edition, including
    one without readable main text. This tool runs locally without a worker.
    """
    try:
        with Path(source_epub).open("rb") as stream:
            if stream.seek(0, 2) > MAX_ARCHIVE:
                raise InvalidEpub("Oversized EPUB")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            with ZipFile(stream) as archive:
                infos = archive.infolist()
                names = archive.namelist()
                if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                    raise InvalidEpub("EPUB exceeds expansion limits")
                if len(set(names)) != len(names) or any(
                    PurePosixPath(n).is_absolute()
                    or ".." in PurePosixPath(n).parts
                    or "\\" in n
                    for n in names
                ):
                    raise InvalidEpub("Ambiguous or unsafe archive path")
                container = _xml(archive, "META-INF/container.xml")
                rootfile = container.find(".//{*}rootfile")
                if rootfile is None:
                    raise InvalidEpub("Missing EPUB package")
                package_path = _resource("", rootfile.attrib["full-path"])
                package = _xml(archive, package_path)
                metadata = package.find("{*}metadata")
                spine = package.find("{*}spine")
                if metadata is None or spine is None:
                    raise InvalidEpub("Missing metadata or spine")
                index = SourceIndex(
                    digest,
                    metadata.findtext("{*}title", ""),
                    [a.text or "" for a in metadata.findall("{*}creator")],
                )
                items = {
                    i.attrib["id"]: i for i in package.iterfind("{*}manifest/{*}item")
                }
                cover_id = next(
                    (
                        m.get("content")
                        for m in metadata.findall("{*}meta")
                        if m.get("name") == "cover"
                    ),
                    None,
                )
                cover = next(
                    (
                        i
                        for i in items.values()
                        if "cover-image" in i.get("properties", "").split()
                    ),
                    items.get(cover_id),
                )
                if cover is not None and cover.get("href"):
                    try:
                        index.cover_resource = _resource(
                            package_path, cover.attrib["href"]
                        )
                    except InvalidEpub:
                        pass  # Optional artwork may point outside the edition.
                parsed: dict[str, Document] = {}
                for occurrence, ref in enumerate(spine.iterfind("{*}itemref")):
                    item = items[ref.attrib["idref"]]
                    name = _resource(package_path, item.attrib["href"])
                    if item.get("media-type") not in {
                        "application/xhtml+xml",
                        "text/html",
                    }:
                        raise InvalidEpub(f"Unsupported spine resource: {name}")
                    if name not in parsed:
                        parsed[name] = _document(_xml(archive, name), name)
                    index.spine.append(
                        replace(
                            parsed[name],
                            occurrence=occurrence,
                            linear=ref.get("linear") != "no",
                        )
                    )
                for item in items.values():
                    ncx = item.get("id") == spine.get("toc")
                    if ncx or "nav" in item.get("properties", "").split():
                        path = _resource(package_path, item.attrib["href"])
                        index.navigation.extend(
                            _navigation(_xml(archive, path), path, ncx)
                        )
                if not any(d.linear and d.text.strip() for d in index.spine):
                    raise InvalidEpub("No readable main text in this edition")
                return index
    except InvalidEpub:
        raise
    except (
        BadZipFile,
        KeyError,
        OSError,
        ValueError,
        RuntimeError,
        etree.XMLSyntaxError,
    ) as exc:
        raise InvalidEpub("Unreadable or unsupported EPUB") from exc


def read_passage(index: SourceIndex, source_range: SourceRange) -> str:
    """Return the indexed text between the inclusive start and exclusive end.

    Use the index for the edition that supplied the range. An unknown
    occurrence, mismatched resource path, or out-of-bounds end raises
    `InvalidCandidate`.
    """
    if source_range.occurrence >= len(index.spine):
        raise InvalidCandidate(f"Unknown occurrence {source_range.occurrence}")
    doc = index.spine[source_range.occurrence]
    if source_range.resource != doc.resource or source_range.end > len(doc.text):
        raise InvalidCandidate(
            f"Invalid resource or offset for occurrence {doc.occurrence}"
        )
    return doc.text[source_range.start : source_range.end]


def validate_hierarchy(index: SourceIndex, candidate: Book) -> None:
    """Verify a candidate against the indexed edition.

    Check the digest, title, creators, unique node IDs, and exact passage
    coverage of every nonempty linear spine entry in reading order. Return
    `None` on success or raise `InvalidCandidate` at the first mismatch.
    Section labels and literary boundaries are Geppetto's interpretation.
    """
    if candidate.source_sha256 != index.source_sha256:
        raise InvalidCandidate("Edition digest mismatch")
    if candidate.title != index.title or candidate.authors != index.authors:
        raise InvalidCandidate("Use the source title and creators")
    docs = iter(d for d in index.spine if d.linear and d.text)
    doc = next(docs, None)
    offset = 0
    ids = set()
    pending: list[BookNode] = [candidate.root]
    while pending:
        node = pending.pop()
        if node.id in ids:
            raise InvalidCandidate(f"Duplicate node ID {node.id}")
        ids.add(node.id)
        if isinstance(node, Section):
            pending.extend(reversed(node.children))
            continue
        assert isinstance(node, Passage)
        ref = node.source
        if doc is None:
            raise InvalidCandidate(f"Unexpected extra passage {node.id}")
        if (ref.occurrence, ref.resource, ref.start) != (
            doc.occurrence,
            doc.resource,
            offset,
        ) or ref.end > len(doc.text):
            raise InvalidCandidate(
                f"Expected {doc.resource} occurrence {doc.occurrence} offset {offset} at {node.id}"
            )
        offset = ref.end
        if offset == len(doc.text):
            doc = next(docs, None)
            offset = 0
    if doc is not None:
        raise InvalidCandidate(
            f"Missing {doc.resource} occurrence {doc.occurrence} from offset {offset}"
        )
