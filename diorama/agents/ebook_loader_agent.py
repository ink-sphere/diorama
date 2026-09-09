"""Tau-assisted interpretation with deterministic, lossless EPUB extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

from pydantic import Field
from tau_agent import AgentHarness, AgentHarnessConfig, AgentTool, AgentToolResult
from tau_agent.messages import AssistantMessage
from tau_agent.provider import ModelProvider

from diorama.models.ebook_models import EbookDocument, EbookModel, StructurePlan
from diorama.prompts import EBOOK_LOADER_AGENT_SYSTEM_PROMPT
from diorama.utils.ebook_source import EbookLoadError, EbookSource
from diorama.utils.trace_events import LoaderEvent, TraceCallback, TraceEvent


class PageRequest(EbookModel):
    start: int = Field(default=0, ge=0)
    limit: int = Field(default=40, ge=1, le=100)


class InspectRequest(EbookModel):
    unit: int = Field(ge=0)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class ResolveRequest(EbookModel):
    member: str
    anchor: str | None = None


class NavigationRequest(EbookModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class EbookLoaderAgent:
    """Interpret and save an EPUB using an explicitly supplied Tau provider.

    Each load uses a fresh harness; provider lifecycle belongs to the caller.
    Successful calls publish book.json and source.epub under the configured
    output directory in a subdirectory named from the source stem and SHA-256.
    """

    def __init__(
        self,
        provider: ModelProvider,
        model: str,
        *,
        output_dir: str | Path = ".diorama",
        max_turns: int = 30,
        on_event: TraceCallback | None = None,
    ):
        if not model.strip():
            raise ValueError("model must be nonempty")
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        self.provider = provider
        self.model = model
        self.output_dir = Path(output_dir)
        self.max_turns = max_turns
        self.on_event = on_event
        self._trace_failed = False

    def _emit(self, event: TraceEvent) -> None:
        if self.on_event is not None and not self._trace_failed:
            try:
                self.on_event(event)
            except Exception:
                self._trace_failed = True
                warnings.warn(
                    "Trace callback failed; tracing disabled for this load",
                    RuntimeWarning,
                    stacklevel=2,
                )

    def _destination(self, path: Path, digest: str) -> Path:
        stem = (
            re.sub(r"[^\w.-]+", "-", path.stem, flags=re.UNICODE).strip(".-")[:80]
            or "book"
        )
        stem = stem.encode("utf-8")[:160].decode("utf-8", errors="ignore")
        return self.output_dir / f"{stem}-{digest}"

    async def load(self, epub_path: str | Path) -> EbookDocument:
        self._trace_failed = False
        self._emit(LoaderEvent("load_start", "Parsing EPUB", {"path": str(epub_path)}))
        try:
            return await self._load(epub_path)
        except asyncio.CancelledError:
            self._emit(LoaderEvent("load_cancelled", "Loading cancelled"))
            raise
        except Exception as exc:
            self._emit(LoaderEvent("load_error", str(exc)))
            raise

    async def _load(self, epub_path: str | Path) -> EbookDocument:
        path = Path(epub_path)
        source = await asyncio.to_thread(EbookSource, path)
        self._emit(
            LoaderEvent(
                "source_ready",
                source.title,
                {"units": len(source.units), "spine_documents": len(source.spine)},
            )
        )
        document = None
        last_validation = None
        outline = [
            {
                "id": i,
                "member": u.member,
                "tag": u.tag,
                "anchor": u.anchor,
                "heading": u.heading[:1000] if u.heading else None,
            }
            for i, u in enumerate(source.units)
            if u.anchor or u.heading
        ]
        # Paginate navigation as text to bound even unusually large nav trees.
        navigation = json.dumps(source.navigation, ensure_ascii=False)

        def tool(name, description, schema, callback):
            async def execute(call_id, arguments, signal=None, on_update=None):
                request = schema.model_validate(arguments)
                result = callback(request)
                return AgentToolResult(content=json.dumps(result, ensure_ascii=False))

            return AgentTool(
                name=name,
                label=name,
                description=description,
                parameters=schema.model_json_schema(by_alias=True),
                execute_fn=execute,
                execution_mode="sequential",
            )

        def page(items, request):
            stop = min(request.start + request.limit, len(items))
            return {
                "items": items[request.start : stop],
                "total": len(items),
                "next": stop if stop < len(items) else None,
            }

        def inspect(request):
            if request.unit >= len(source.units):
                raise EbookLoadError("Unknown source unit")
            u = source.units[request.unit]
            text = source.members[u.member][u.start : u.end].decode(u.encoding)
            stop = min(request.offset + request.limit, len(text))
            return {
                "unit": request.unit,
                "member": u.member,
                "xhtml": text[request.offset : stop],
                "characters": len(text),
                "next_offset": stop if stop < len(text) else None,
            }

        def resolve(request):
            matches = [
                i
                for i, u in enumerate(source.units)
                if u.member == request.member
                and (request.anchor is None or u.anchor == request.anchor)
            ]
            if not matches:
                raise EbookLoadError("Source member or anchor not found")
            return {"unit": matches[0]}

        def submit(request):
            nonlocal document, last_validation
            try:
                document = source.materialize(request)
            except EbookLoadError as exc:
                last_validation = str(exc)
                raise
            return {"accepted": True}

        tools = [
            tool(
                "list_outline",
                "Page through headings and anchors.",
                PageRequest,
                lambda r: page(outline, r),
            ),
            tool(
                "list_spine",
                "Page through spine documents and their unit ranges.",
                PageRequest,
                lambda r: page(source.spine, r),
            ),
            tool(
                "list_units",
                "Page through source units with short previews.",
                PageRequest,
                lambda r: {
                    "items": source.describe(r.start, r.limit),
                    "total": len(source.units),
                },
            ),
            tool(
                "inspect_units",
                "Read a source unit in character windows.",
                InspectRequest,
                inspect,
            ),
            tool(
                "read_navigation",
                "Read navigation JSON in character windows.",
                NavigationRequest,
                lambda r: {
                    "text": navigation[r.offset : r.offset + r.limit],
                    "characters": len(navigation),
                    "next_offset": r.offset + r.limit
                    if r.offset + r.limit < len(navigation)
                    else None,
                },
            ),
            tool(
                "resolve_source",
                "Resolve an archive member and optional anchor to a source unit.",
                ResolveRequest,
                resolve,
            ),
            tool(
                "submit_structure",
                "Validate the complete literary hierarchy; correct errors and resubmit.",
                StructurePlan,
                submit,
            ),
        ]
        harness = AgentHarness(
            AgentHarnessConfig(
                provider=self.provider,
                model=self.model,
                system=EBOOK_LOADER_AGENT_SYSTEM_PROMPT,
                tools=tools,
                max_turns=self.max_turns,
            )
        )
        prompt = json.dumps(
            {
                "title": source.title,
                "author": source.author,
                "unit_count": len(source.units),
                "spine": page(source.spine, PageRequest()),
                "outline": page(outline, PageRequest()),
                "metadata_preview": json.dumps(source.metadata, ensure_ascii=False)[
                    :8000
                ],
                "navigation_characters": len(navigation),
            },
            ensure_ascii=False,
        )
        stream = harness.prompt(prompt)
        try:
            async for event in stream:
                self._emit(event)
                if document is not None:
                    break
        except Exception as exc:
            raise EbookLoadError(f"Tau interpretation failed: {exc}") from exc
        finally:
            await stream.aclose()
        if document is None:
            errors = [
                m.error_message
                for m in harness.messages
                if isinstance(m, AssistantMessage) and m.error_message
            ]
            reason = (
                errors[-1]
                if errors
                else "Agent ended without submitting a valid structure"
            )
            if last_validation:
                reason += f"; last validation error: {last_validation}"
            raise EbookLoadError(reason)
        # Synchronous publication avoids background writes after task cancellation.
        destination = self._destination(path, source.sha256)
        self._emit(LoaderEvent("publish_start", "Saving validated book"))
        self._publish(destination, source, document)
        self._emit(
            LoaderEvent(
                "load_complete",
                document.title,
                {"destination": str(destination.resolve())},
            )
        )
        return document

    @staticmethod
    def _publish(destination: Path, source: EbookSource, document: EbookDocument):
        payload = document.model_dump_json(by_alias=True, indent=2)
        stage = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=".ebook-", dir=destination.parent))
            (stage / "book.json").write_text(payload, encoding="utf-8")
            if destination.exists():
                saved = destination / "source.epub"
                if hashlib.sha256(saved.read_bytes()).hexdigest() != source.sha256:
                    raise EbookLoadError(
                        f"Existing source archive hash mismatch: {saved}"
                    )
                os.replace(stage / "book.json", destination / "book.json")
            else:
                (stage / "source.epub").write_bytes(source.archive)
                os.rename(stage, destination)
        except OSError as exc:
            raise EbookLoadError(
                f"Cannot publish ebook to {destination}: {exc}"
            ) from exc
        finally:
            if stage is not None and stage.exists():
                shutil.rmtree(stage)
