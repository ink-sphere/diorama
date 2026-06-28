"""Validation + deterministic slicing of the agent's submitted tree.

The agent hands back a list of raw node dicts (matching ``SUBMIT_SCHEMA``). This
module:

1. :func:`validate_tree` — checks the raw tree against the real block stream and
   returns human-readable errors (fed back to the agent so it self-corrects).
2. :func:`build_structure` — expands any ``child_pattern`` levels, assigns each
   node a ``[start, end)`` block range, slices text into preambles/leaves, and runs
   a coverage check — producing the final :class:`EbookStructure`.

All deterministic; no LLM.
"""

from __future__ import annotations

import re
from typing import Any

from diorama.ebook.context import EbookContext
from diorama.ebook.models import (
    KINDS,
    Block,
    CoverageReport,
    EbookStructure,
    StructureNode,
)

# Roman-numeral parsing for ordinals like "ACT IV" / "SCENE II".
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        val = _ROMAN[ch]
        total += -val if val < prev else val
        prev = max(prev, val)
    return total or None


def _parse_number(token: str | None) -> int | None:
    """Parse an ordinal from a regex group: arabic digits or a roman numeral."""
    if token is None:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _roman_to_int(token)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_tree(nodes: list[dict[str, Any]], ctx: EbookContext) -> list[str]:
    """Return a list of validation errors for the raw submitted tree (empty = ok)."""
    errors: list[str] = []
    n_blocks = ctx.total_blocks
    if not isinstance(nodes, list):
        return ["`nodes` must be a list of node objects."]
    if not nodes:
        return ["`nodes` is empty — the book must have at least one top-level unit."]

    # Track depth-first start order to ensure global monotonicity.
    state = {"last_start": -1}

    def check(level: list[dict[str, Any]], parent_path: str, lo: int, hi: int) -> None:
        prev_sibling = -1
        for i, raw in enumerate(level):
            path = f"{parent_path}[{i}]"
            if not isinstance(raw, dict):
                errors.append(f"{path}: node must be an object.")
                continue
            start = raw.get("start_block_id")
            if not isinstance(start, int):
                errors.append(f"{path}: missing/invalid integer start_block_id.")
                continue
            if not (0 <= start < n_blocks):
                errors.append(
                    f"{path}: start_block_id {start} out of range (0..{n_blocks - 1})."
                )
            if not raw.get("level_type"):
                errors.append(f"{path}: missing level_type.")
            kind = raw.get("kind")
            if kind not in KINDS:
                errors.append(f"{path}: kind {kind!r} not one of {KINDS}.")
            if start <= prev_sibling:
                errors.append(
                    f"{path}: start_block_id {start} not after previous sibling "
                    f"({prev_sibling}); siblings must strictly increase."
                )
            if start <= state["last_start"]:
                errors.append(
                    f"{path}: start_block_id {start} breaks document order "
                    f"(<= a prior node at {state['last_start']})."
                )
            if not (lo <= start < hi):
                errors.append(
                    f"{path}: start_block_id {start} outside parent range [{lo},{hi})."
                )
            prev_sibling = start
            state["last_start"] = max(state["last_start"], start)

            children = raw.get("children") or []
            pattern = raw.get("child_pattern")
            if children and pattern:
                errors.append(
                    f"{path}: provide either children or child_pattern, not both."
                )
            if pattern is not None:
                rx = pattern.get("regex") if isinstance(pattern, dict) else None
                if not rx:
                    errors.append(f"{path}.child_pattern: missing regex.")
                else:
                    try:
                        re.compile(rx)
                    except re.error as e:
                        errors.append(f"{path}.child_pattern: invalid regex ({e}).")
                if isinstance(pattern, dict) and not pattern.get("level_type"):
                    errors.append(f"{path}.child_pattern: missing level_type.")

            # Children must live within this node's range; its end is the next
            # sibling's start, or the parent's hi.
            nxt = level[i + 1].get("start_block_id") if i + 1 < len(level) else hi
            child_hi = nxt if isinstance(nxt, int) else hi
            if children:
                check(children, f"{path}.children", start, child_hi)

    check(nodes, "nodes", 0, n_blocks)
    return errors


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def _slice_text(blocks: list[Block], start: int, end: int) -> str | None:
    text = "\n\n".join(b.text for b in blocks[start:end])
    return text or None


def _expand_pattern(
    pattern: dict[str, Any], start: int, end: int, blocks: list[Block]
) -> list[dict[str, Any]]:
    """Turn a ``child_pattern`` into explicit raw child nodes within ``[start, end)``.

    Searches blocks *after* the parent's own start so a level never matches its own
    heading. The first regex capture group, if present, becomes the unit number.
    """
    regex = re.compile(pattern["regex"])
    level_type = pattern.get("level_type") or "section"
    kind = pattern.get("kind") or "narrative"
    children: list[dict[str, Any]] = []
    for b in blocks[start + 1 : end]:
        m = regex.search(b.text)
        if not m:
            continue
        number = _parse_number(m.group(1)) if m.groups() else None
        children.append(
            {
                "level_type": level_type,
                "title": b.text,
                "kind": kind,
                "start_block_id": b.block_id,
                "number": number,
            }
        )
    return children


def _build_nodes(
    raw_nodes: list[dict[str, Any]],
    parent_end: int,
    blocks: list[Block],
    level_types: list[str],
) -> list[StructureNode]:
    """Recursively assign ranges, expand patterns, and slice text."""
    ordered = sorted(raw_nodes, key=lambda r: r["start_block_id"])
    result: list[StructureNode] = []
    for i, raw in enumerate(ordered):
        start = raw["start_block_id"]
        end = ordered[i + 1]["start_block_id"] if i + 1 < len(ordered) else parent_end

        level_type = raw.get("level_type") or "section"
        if level_type not in level_types:
            level_types.append(level_type)

        raw_children = raw.get("children") or []
        pattern = raw.get("child_pattern")
        if not raw_children and isinstance(pattern, dict):
            raw_children = _expand_pattern(pattern, start, end, blocks)

        children = (
            _build_nodes(raw_children, end, blocks, level_types) if raw_children else []
        )

        preamble = None
        text = None
        if children:
            first_child_start = children[0].start_block_id
            preamble = _slice_text(blocks, start, first_child_start)
        else:
            text = _slice_text(blocks, start, end)

        result.append(
            StructureNode(
                level_type=level_type,
                kind=raw.get("kind") or "narrative",
                title=raw.get("title") or "",
                clean_title=raw.get("clean_title"),
                number=raw.get("number"),
                start_block_id=start,
                end_block_id=end,
                preamble_text=preamble,
                text=text,
                children=children,
            )
        )
    return result


def _coverage(root: list[StructureNode], total: int) -> CoverageReport:
    """Walk the tree and account for every block exactly once."""
    seen = [0] * total

    def mark(nodes: list[StructureNode]) -> None:
        for node in nodes:
            if node.children:
                # Internal node owns only its preamble span [start, first child).
                for i in range(node.start_block_id, node.children[0].start_block_id):
                    if 0 <= i < total:
                        seen[i] += 1
                mark(node.children)
            else:
                for i in range(node.start_block_id, node.end_block_id):
                    if 0 <= i < total:
                        seen[i] += 1

    mark(root)
    gaps = _runs(i for i in range(total) if seen[i] == 0)
    overlaps = _runs(i for i in range(total) if seen[i] > 1)
    assigned = sum(1 for c in seen if c >= 1)
    return CoverageReport(
        covered=not gaps and not overlaps,
        total_blocks=total,
        assigned_blocks=assigned,
        gaps=gaps,
        overlaps=overlaps,
    )


def _runs(indices: Any) -> list[tuple[int, int]]:
    """Compress a sorted iterable of ints into half-open ``(start, end)`` runs."""
    runs: list[tuple[int, int]] = []
    start = prev = None
    for i in indices:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            runs.append((start, prev + 1))
            start = prev = i
    if start is not None:
        runs.append((start, prev + 1))
    return runs


def build_structure(
    ctx: EbookContext, raw_nodes: list[dict[str, Any]]
) -> EbookStructure:
    """Expand, slice, and verify the submitted tree into an :class:`EbookStructure`."""
    level_types: list[str] = []
    root = _build_nodes(raw_nodes, ctx.total_blocks, ctx.blocks, level_types)
    coverage = _coverage(root, ctx.total_blocks)
    return EbookStructure(
        source_path=ctx.source_path,
        title=ctx.title,
        level_types=level_types,
        root=root,
        coverage=coverage,
    )
