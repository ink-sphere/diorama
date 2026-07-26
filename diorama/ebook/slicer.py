"""Deterministic tree -> :class:`EbookStructure` builder and validator.

The loader agent submits a tree of block-id boundaries (see
``diorama.agents.ebook_loader.SUBMIT_SCHEMA``); everything here is pure, agent-free
logic: validating full/non-overlapping block coverage, auto-expanding
``child_pattern`` repeating levels, joining block text for leaf nodes, and
paginating leaf text into segments. The agent never assembles text itself — it only
proposes boundaries, which keeps its output small and makes the assembled text
exactly reproducible from the block ids it chose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from diorama.ebook.models import Coverage, EbookStructure, StructureNode
from diorama.ebook.parser import EbookContext

DEFAULT_SEGMENT_LENGTH = 1500

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class _RawNode:
    """One node of the tree as submitted by the agent, before validation."""

    level_type: str
    number: str | None
    title: str | None
    start_block_id: int
    end_block_id: int
    child_pattern: str | None
    child_level_type: str | None
    children: list["_RawNode"]


def _parse_raw(payload: dict) -> _RawNode:
    """Parse one submitted node dict into a :class:`_RawNode`.

    Raises:
        ValueError: If required fields are missing or of the wrong type.
    """
    try:
        return _RawNode(
            level_type=str(payload["level_type"]),
            number=payload.get("number"),
            title=payload.get("title"),
            start_block_id=int(payload["start_block_id"]),
            end_block_id=int(payload["end_block_id"]),
            child_pattern=payload.get("child_pattern"),
            child_level_type=payload.get("child_level_type"),
            children=[_parse_raw(c) for c in payload.get("children") or []],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"malformed node ({e})") from e


def _ranges(ids: list[int]) -> list[tuple[int, int]]:
    """Collapse a sorted-or-not list of block ids into inclusive contiguous ranges."""
    ranges: list[tuple[int, int]] = []
    for i in sorted(ids):
        if ranges and ranges[-1][1] == i - 1:
            ranges[-1] = (ranges[-1][0], i)
        else:
            ranges.append((i, i))
    return ranges


def _pattern_matches(
    node: _RawNode, context: EbookContext, pattern: re.Pattern
) -> list[int]:
    """Block ids within ``node``'s range whose text matches ``pattern`` at the start."""
    return [
        b.id
        for b in context.blocks_in_range(node.start_block_id, node.end_block_id)
        if pattern.match(b.text)
    ]


def _expand_pattern(
    node: _RawNode, context: EbookContext, pattern: re.Pattern
) -> tuple[list[_RawNode], int | None]:
    """Expand a ``child_pattern`` node into explicit children.

    Returns ``(children, preamble_end_block_id)`` — ``preamble_end_block_id`` is the
    last block id before the first match (inclusive), or None if the first match is
    at the node's own start.
    """
    match_ids = _pattern_matches(node, context, pattern)
    if not match_ids:
        return [], None

    preamble_end = match_ids[0] - 1 if match_ids[0] > node.start_block_id else None
    boundaries = match_ids + [node.end_block_id + 1]
    children: list[_RawNode] = []
    for i, start in enumerate(match_ids):
        end = boundaries[i + 1] - 1
        match = pattern.match(context.block(start).text)
        assert match is not None
        number = match.group(1) if match.groups() else match.group(0)
        children.append(
            _RawNode(
                level_type=node.child_level_type or "section",
                number=number,
                title=None,
                start_block_id=start,
                end_block_id=end,
                child_pattern=None,
                child_level_type=None,
                children=[],
            )
        )
    return children, preamble_end


def _walk(tree: list[dict], context: EbookContext) -> tuple[list[str], list[int]]:
    """Validate ``tree`` against ``context``, returning ``(errors, assignment_counts)``.

    ``assignment_counts[i]`` is how many leaf nodes claimed block ``i`` — 0 is a gap,
    2+ is an overlap. Both a compiled ``validate_tree`` and coverage computation share
    this single walk so their notion of "assigned" can never drift apart.
    """
    errors: list[str] = []
    counts = [0] * context.total_blocks
    total = context.total_blocks

    try:
        nodes = [_parse_raw(n) for n in tree]
    except ValueError as e:
        return [str(e)], counts

    def walk(node: _RawNode, path: str) -> None:
        if node.start_block_id > node.end_block_id:
            errors.append(f"{path}: start_block_id > end_block_id")
            return
        if node.start_block_id < 0 or node.end_block_id >= total:
            errors.append(
                f"{path}: block range [{node.start_block_id}, {node.end_block_id}] "
                f"out of bounds (book has {total} blocks, ids 0..{total - 1})"
            )
            return
        if node.child_pattern and node.children:
            errors.append(f"{path}: node has both 'children' and 'child_pattern'")
            return

        if node.child_pattern:
            if not node.child_level_type:
                errors.append(
                    f"{path}: child_pattern requires child_level_type to be set"
                )
                return
            try:
                pattern = re.compile(node.child_pattern, re.MULTILINE)
            except re.error as e:
                errors.append(f"{path}: invalid child_pattern regex: {e}")
                return
            children, preamble_end = _expand_pattern(node, context, pattern)
            if not children:
                errors.append(
                    f"{path}: child_pattern matched no blocks in range "
                    f"[{node.start_block_id}, {node.end_block_id}]"
                )
                return
            if preamble_end is not None:
                for block_id in range(node.start_block_id, preamble_end + 1):
                    counts[block_id] += 1
            for i, child in enumerate(children):
                walk(child, f"{path}.child_pattern[{i}]")
            return

        if node.children:
            prev_end: int | None = None
            for i, child in enumerate(node.children):
                if prev_end is not None and child.start_block_id != prev_end + 1:
                    errors.append(
                        f"{path}.children[{i}]: gap/overlap — expected "
                        f"start_block_id {prev_end + 1}, got {child.start_block_id}"
                    )
                walk(child, f"{path}.children[{i}]")
                prev_end = child.end_block_id
            if node.children[0].start_block_id != node.start_block_id:
                errors.append(
                    f"{path}: first child does not start at the parent's "
                    f"start_block_id ({node.start_block_id})"
                )
            if node.children[-1].end_block_id != node.end_block_id:
                errors.append(
                    f"{path}: last child does not end at the parent's "
                    f"end_block_id ({node.end_block_id})"
                )
            return

        for block_id in range(node.start_block_id, node.end_block_id + 1):
            counts[block_id] += 1

    for i, node in enumerate(nodes):
        walk(node, f"root[{i}]")

    return errors, counts


def validate_tree(tree: list[dict], context: EbookContext) -> list[str]:
    """Return human-readable errors, or ``[]`` when the tree is valid and coverable."""
    errors, counts = _walk(tree, context)
    if not errors:
        gaps = _ranges([i for i, c in enumerate(counts) if c == 0])
        overlaps = _ranges([i for i, c in enumerate(counts) if c > 1])
        if gaps:
            errors.append(f"blocks not assigned to any node: {gaps}")
        if overlaps:
            errors.append(f"blocks assigned to more than one node: {overlaps}")
    return errors


def _join_blocks(context: EbookContext, start: int, end: int) -> str:
    """Join a block range into continuous text, paragraphs separated by blank lines."""
    return "\n\n".join(b.text for b in context.blocks_in_range(start, end))


def _split_long_paragraph(paragraph: str, max_len: int) -> list[str]:
    """Split a single paragraph too long for one segment, at sentence/word bounds."""
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_RE.split(paragraph):
        if len(sentence) > max_len:
            if current:
                chunks.append(current)
                current = ""
            piece = ""
            for word in sentence.split(" "):
                candidate = f"{piece} {word}".strip()
                if len(candidate) > max_len and piece:
                    chunks.append(piece)
                    piece = word
                else:
                    piece = candidate
            if piece:
                chunks.append(piece)
            continue
        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _segment_text(text: str, max_len: int) -> list[str]:
    """Paginate ``text`` into segments of at most ``max_len`` characters.

    Packs whole paragraphs (split on blank lines) greedily; a paragraph exceeding
    ``max_len`` on its own is further split at sentence, then word, boundaries.
    """
    if not text:
        return []
    segments: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        if current:
            segments.append("\n\n".join(current))
            current.clear()

    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph) > max_len:
            flush()
            current_len = 0
            segments.extend(_split_long_paragraph(paragraph, max_len))
            continue
        added = len(paragraph) + (2 if current else 0)
        if current_len + added > max_len:
            flush()
            current = [paragraph]
            current_len = len(paragraph)
        else:
            current.append(paragraph)
            current_len += added
    flush()
    return segments


def _compute_coverage(tree: list[dict], context: EbookContext) -> Coverage:
    """Compute :class:`Coverage` for an already-valid tree."""
    errors, counts = _walk(tree, context)
    total = context.total_blocks
    assigned = sum(1 for c in counts if c > 0)
    return Coverage(
        covered=(not errors and assigned == total),
        total_blocks=total,
        assigned_blocks=assigned,
        gaps=_ranges([i for i, c in enumerate(counts) if c == 0]),
        overlaps=_ranges([i for i, c in enumerate(counts) if c > 1]),
    )


def build_structure(
    tree: list[dict],
    context: EbookContext,
    *,
    max_segment_length: int | None = None,
) -> EbookStructure:
    """Build the final :class:`EbookStructure` from a validated tree.

    Raises:
        ValueError: If the tree fails :func:`validate_tree`.
    """
    errors = validate_tree(tree, context)
    if errors:
        raise ValueError("invalid structure tree:\n" + "\n".join(errors))

    raw_nodes = [_parse_raw(n) for n in tree]
    level_types: set[str] = set()

    def build(node: _RawNode) -> StructureNode:
        level_types.add(node.level_type)
        children_raw = node.children
        preamble_text: str | None = None

        if node.child_pattern:
            pattern = re.compile(node.child_pattern, re.MULTILINE)
            children_raw, preamble_end = _expand_pattern(node, context, pattern)
            if preamble_end is not None:
                preamble_text = _join_blocks(context, node.start_block_id, preamble_end)

        if children_raw:
            return StructureNode(
                level_type=node.level_type,
                number=node.number,
                title=node.title,
                start_block_id=node.start_block_id,
                end_block_id=node.end_block_id,
                preamble_text=preamble_text,
                children=[build(c) for c in children_raw],
            )

        text = _join_blocks(context, node.start_block_id, node.end_block_id)
        segments = (
            _segment_text(text, max_segment_length) if max_segment_length else None
        )
        return StructureNode(
            level_type=node.level_type,
            number=node.number,
            title=node.title,
            start_block_id=node.start_block_id,
            end_block_id=node.end_block_id,
            text=text,
            segments=segments,
        )

    root = [build(n) for n in raw_nodes]
    coverage = _compute_coverage(tree, context)
    return EbookStructure(
        title=context.title,
        author=context.author,
        level_types=sorted(level_types),
        root=root,
        toc=context.toc,
        coverage=coverage,
        cost_usd=0.0,
    )


__all__ = ["DEFAULT_SEGMENT_LENGTH", "build_structure", "validate_tree"]
