"""Best-effort cover-image extraction from an EPUB.

EPUBs declare their cover in at least four mutually incompatible ways depending on
which decade and which tool produced them, so this is a cascade of heuristics rather
than one lookup: the EPUB 3 ``properties="cover-image"`` manifest flag, the EPUB 2
``<meta name="cover" content="...">` pointer, ebooklib's own ``ITEM_COVER`` type, and
finally a filename guess. Nothing here is agent- or LLM-aware, and a missing cover is
an ordinary outcome (``None``), not an error.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import ebooklib
from ebooklib import epub

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}


def _as_image(item: Any) -> tuple[bytes, str] | None:
    """Return ``(bytes, media_type)`` when ``item`` is a usable image, else None."""
    if item is None:
        return None
    content = item.get_content()
    if not content:
        return None
    name = item.get_name() or ""
    media_type = (
        getattr(item, "media_type", None)
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )
    if not media_type.startswith("image/"):
        return None
    return content, media_type


def _from_manifest_properties(book: epub.EpubBook) -> Any:
    for item in book.get_items():
        properties = getattr(item, "properties", None) or []
        if "cover-image" in properties:
            return item
    return None


def _from_opf_meta(book: epub.EpubBook) -> Any:
    """Follow the EPUB 2 ``<meta name="cover" content="<manifest-id>">`` pointer."""
    for _value, attrs in book.get_metadata("OPF", "cover") or []:
        item_id = (attrs or {}).get("content")
        if item_id:
            item = book.get_item_with_id(item_id)
            if item is not None:
                return item
    return None


def _from_filename(book: epub.EpubBook) -> Any:
    candidates = [
        item
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE)
        if Path(item.get_name() or "").suffix.lower() in _IMAGE_SUFFIXES
    ]
    for item in candidates:
        if "cover" in (item.get_name() or "").lower():
            return item
    # An EPUB with exactly one image is almost always cover-only; more than one and
    # guessing does more harm than a clean typographic fallback in the UI.
    return candidates[0] if len(candidates) == 1 else None


def extract_cover(epub_path: str | Path) -> tuple[bytes, str] | None:
    """Return this EPUB's cover as ``(bytes, media_type)``, or None if it has none."""
    book = epub.read_epub(str(epub_path))
    finders = (
        _from_manifest_properties,
        _from_opf_meta,
        lambda b: next(iter(b.get_items_of_type(ebooklib.ITEM_COVER)), None),
        _from_filename,
    )
    for find in finders:
        image = _as_image(find(book))
        if image is not None:
            return image
    return None


__all__ = ["extract_cover"]
