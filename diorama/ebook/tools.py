"""Tools the :class:`EbookLoaderAgent` uses to explore one book.

Every tool is bound to a shared :class:`EbookContext` (the book's block stream +
anchored ToC). The agent navigates the flat block space with these, then ends the
run by calling :class:`SubmitStructureTool`, which validates the proposed tree and
stashes it on the context for the deterministic slicer.

To keep responses inside the model's context window, the read-oriented tools cap
how much they return and report when they have truncated.
"""

from __future__ import annotations

import re
from typing import Any

from diorama.core.tool import Tool, ToolParameter
from diorama.ebook.context import EbookContext
from diorama.ebook.models import SUBMIT_SCHEMA, Block
from diorama.ebook.slicer import validate_tree

# Response caps (blocks). Generous enough to navigate, small enough to be safe.
_MAX_HEADINGS = 400
_MAX_READ_BLOCKS = 120
_MAX_SEARCH_MATCHES = 60


def _heading_view(b: Block) -> dict[str, Any]:
    return {"block_id": b.block_id, "tag": b.tag, "classes": b.classes, "text": b.text}


def _content_view(b: Block) -> dict[str, Any]:
    return {"block_id": b.block_id, "tag": b.tag, "text": b.text}


class _EbookTool(Tool):
    """Base for tools that share one book's :class:`EbookContext`."""

    context: EbookContext


class GetOverviewTool(_EbookTool):
    tool_name: str = "get_overview"
    description: str = (
        "Get a high-level summary of the book: title, number of spine documents, "
        "total block count, and how many blocks look like structural headings. "
        "Call this first to orient yourself."
    )
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:
        ctx = self.context
        spine_count = len({b.spine_index for b in ctx.blocks})
        return {
            "title": ctx.title,
            "spine_documents": spine_count,
            "total_blocks": ctx.total_blocks,
            "heading_candidate_count": len(ctx.heading_candidates()),
            "note": (
                "Blocks are numbered 0.."
                f"{max(ctx.total_blocks - 1, 0)} in reading order. A boundary is a "
                "block_id."
            ),
        }


class GetTocTool(_EbookTool):
    tool_name: str = "get_toc"
    description: str = (
        "Return the book's Table of Contents, each entry resolved to the block_id "
        "it points at. The ToC is often flat or incomplete — verify it against the "
        "in-content headings (list_headings/search_blocks) before trusting it."
    )
    parameters: list[ToolParameter] = []

    async def forward(self) -> Any:
        def conv(entries: list[Any]) -> list[dict[str, Any]]:
            return [
                {
                    "title": e.title,
                    "block_id": e.block_id,
                    "children": conv(e.children),
                }
                for e in entries
            ]

        return {"toc": conv(self.context.toc)}


class ListHeadingsTool(_EbookTool):
    tool_name: str = "list_headings"
    description: str = (
        "List heading-candidate blocks (likely structural markers) within a block "
        "range. This is your primary structural map. Narrow the range to drill into "
        "a section of a large book."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="start_block",
            tool_type="number",
            description="First block_id to include (default 0).",
            required=False,
            nullable=True,
        ),
        ToolParameter(
            param_name="end_block",
            tool_type="number",
            description="Exclusive upper block_id bound (default: end of book).",
            required=False,
            nullable=True,
        ),
        ToolParameter(
            param_name="tag_filter",
            tool_type="string",
            description="Optional comma-separated tag whitelist, e.g. 'h1,h2,h3'.",
            required=False,
            nullable=True,
        ),
    ]

    async def forward(
        self,
        start_block: int | None = None,
        end_block: int | None = None,
        tag_filter: str | None = None,
    ) -> Any:
        ctx = self.context
        lo = int(start_block) if start_block is not None else 0
        hi = int(end_block) if end_block is not None else ctx.total_blocks
        tags = (
            {t.strip().lower() for t in tag_filter.split(",")} if tag_filter else None
        )

        matches = [
            b
            for b in ctx.slice(lo, hi)
            if b.is_heading_candidate and (tags is None or b.tag in tags)
        ]
        truncated = len(matches) > _MAX_HEADINGS
        return {
            "range": [lo, hi],
            "count": len(matches),
            "truncated": truncated,
            "headings": [_heading_view(b) for b in matches[:_MAX_HEADINGS]],
        }


class ReadBlocksTool(_EbookTool):
    tool_name: str = "read_blocks"
    description: str = (
        "Read the actual text of a range of blocks, to verify a heading is real, "
        "inspect what falls between headings, or find a repeating marker. The range "
        f"is capped at {_MAX_READ_BLOCKS} blocks."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="start_block",
            tool_type="number",
            description="First block_id to read.",
        ),
        ToolParameter(
            param_name="end_block",
            tool_type="number",
            description="Exclusive upper block_id bound.",
            required=False,
            nullable=True,
        ),
    ]

    async def forward(self, start_block: int, end_block: int | None = None) -> Any:
        ctx = self.context
        lo = int(start_block)
        hi = int(end_block) if end_block is not None else lo + _MAX_READ_BLOCKS
        capped_hi = min(hi, lo + _MAX_READ_BLOCKS)
        blocks = ctx.slice(lo, capped_hi)
        return {
            "range": [lo, capped_hi],
            "truncated": capped_hi < hi,
            "blocks": [_content_view(b) for b in blocks],
        }


class SearchBlocksTool(_EbookTool):
    tool_name: str = "search_blocks"
    description: str = (
        "Find blocks whose text matches a Python regex (via re.search). Returns the "
        "total match count plus a sample. Use this to discover a repeating marker "
        "for a regular, deep level (e.g. every 'SCENE', every 'अध्याय N') — the "
        "count tells you whether to describe the level with a child_pattern."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="regex",
            tool_type="string",
            description="Python regular expression, e.g. '^SCENE\\s+[IVX]+'.",
        ),
        ToolParameter(
            param_name="start_block",
            tool_type="number",
            description="First block_id to include (default 0).",
            required=False,
            nullable=True,
        ),
        ToolParameter(
            param_name="end_block",
            tool_type="number",
            description="Exclusive upper block_id bound (default: end of book).",
            required=False,
            nullable=True,
        ),
    ]

    async def forward(
        self,
        regex: str,
        start_block: int | None = None,
        end_block: int | None = None,
    ) -> Any:
        ctx = self.context
        try:
            pattern = re.compile(regex)
        except re.error as e:
            return {"error": f"invalid regex: {e}"}
        lo = int(start_block) if start_block is not None else 0
        hi = int(end_block) if end_block is not None else ctx.total_blocks

        matches = [b for b in ctx.slice(lo, hi) if pattern.search(b.text)]
        return {
            "regex": regex,
            "range": [lo, hi],
            "count": len(matches),
            "truncated": len(matches) > _MAX_SEARCH_MATCHES,
            "matches": [_heading_view(b) for b in matches[:_MAX_SEARCH_MATCHES]],
        }


class SubmitStructureTool(_EbookTool):
    tool_name: str = "submit_structure"
    description: str = (
        "Submit the final book hierarchy. Each node anchors to a start_block_id and "
        "carries a semantic level_type and a kind. A node may have explicit children "
        "OR a child_pattern (never both). The tree is validated against the real "
        "blocks; if it returns errors, fix them and submit again. A successful "
        "submit completes the task."
    )
    parameters: list[ToolParameter] = []
    parameters_schema: dict[str, Any] = SUBMIT_SCHEMA

    async def forward(self, nodes: list[dict[str, Any]]) -> Any:
        errors = validate_tree(nodes, self.context)
        if errors:
            return {"ok": False, "errors": errors[:25]}
        self.context.submitted_structure = nodes

        def count(level: list[dict[str, Any]]) -> int:
            total = 0
            for n in level:
                total += 1
                total += count(n.get("children") or [])
            return total

        return {
            "ok": True,
            "stats": {
                "top_level_nodes": len(nodes),
                "explicit_nodes": count(nodes),
                "note": "Structure accepted. You are done — no further tool calls needed.",
            },
        }


def build_ebook_tools(ctx: EbookContext) -> list[Tool]:
    """Construct the full ebook tool set bound to one :class:`EbookContext`."""
    return [
        GetOverviewTool(context=ctx),
        GetTocTool(context=ctx),
        ListHeadingsTool(context=ctx),
        ReadBlocksTool(context=ctx),
        SearchBlocksTool(context=ctx),
        SubmitStructureTool(context=ctx),
    ]
