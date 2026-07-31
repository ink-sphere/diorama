"""Common, book-independent tools shared across diorama agents.

Two residents so far, both serving an agent's need to look outside the book it
was handed:

* :class:`WebSearchTool` fronts two providers — `Exa <https://docs.exa.ai>`_ and
  `Tavily <https://docs.tavily.com>`_ — behind one normalised result shape. Both
  APIs are a single POST endpoint, so the tool speaks raw HTTP via ``httpx``
  rather than pulling in two provider SDKs; the provider abstraction stays in
  this file and tests fake the transport.
* :class:`ViewImageTool` pulls an image url into the transcript, so an agent
  researching a book's illustration tradition can *look at* a Tenniel engraving
  instead of reading a sentence about one. It needs no credential, which is why
  it sits beside the search tool rather than inside it — the url it fetches
  usually came from a search result, but it works on any url the agent has.

Provider choice follows the same philosophy as the model layer's
``providers.py``: there is no global "current search provider" setting. An
explicit ``provider`` wins; otherwise the tool uses whichever provider it can
find a key for (``EXA_API_KEY``, then ``TAVILY_API_KEY``). No key at all is a
*graceful* failure — the tool returns an error result telling the model that
web research is unavailable, because every consumer of this tool treats the
web as enrichment over the book text, never as a required source.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import weave
from pydantic import model_validator

from diorama.core.results import (
    MAX_INLINE_IMAGE_BYTES,
    ImageBlock,
    TextBlock,
    ToolResult,
)
from diorama.core.tool import Tool, ToolParameter

SearchProviderId = Literal["exa", "tavily"]

_ENDPOINTS: dict[str, str] = {
    "exa": "https://api.exa.ai/search",
    "tavily": "https://api.tavily.com/search",
}
_ENV_KEYS: dict[str, str] = {
    "exa": "EXA_API_KEY",
    "tavily": "TAVILY_API_KEY",
}
#: Auto-detection order when no provider is named. Exa first: its semantic
#: search suits the open-ended literary queries this tool exists for.
_PROVIDER_ORDER: tuple[SearchProviderId, ...] = ("exa", "tavily")

_DEFAULT_NUM_RESULTS = 5
_MAX_NUM_RESULTS = 10
_ERROR_BODY_CHARS = 300

#: Sent with every image fetch. Several of the archives worth looking at for
#: illustration history (Wikimedia above all) reject requests that do not
#: identify themselves.
_IMAGE_USER_AGENT = "Diorama/0.0.1 (ebook illustration research)"
#: Media types a vision-capable model can actually see. SVG and PDF are
#: deliberately absent: neither is a raster image, and an SVG would arrive as
#: markup the model reads as an unreadable blob rather than a picture.
_VIEWABLE_IMAGE_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)
_MB = 1_000_000


class WebSearchTool(Tool):
    """Search the web via Exa or Tavily, normalising both into one result shape.

    Each result is ``{"title", "url", "published_date", "text"}`` with ``text``
    truncated to :attr:`max_chars_per_result`, so the model sees comparable
    output whichever provider answered. The provider that served the query is
    included in the payload — a research agent citing its sources should know
    where a snippet came from.

    Attributes:
        provider (SearchProviderId | None): Force a specific provider. None
            auto-detects from the environment at call time.
        api_key (str | None): Credential for ``provider``. Only meaningful when
            ``provider`` is set (a bare key names no API); None falls back to
            the provider's environment variable.
        max_chars_per_result (int): Per-result text budget. Both providers can
            return page content far beyond what a search round-trip should put
            in an agent transcript.
        timeout_seconds (float): HTTP timeout per request.
        client (Any): Optional pre-built ``httpx.AsyncClient`` (tests inject one
            backed by ``httpx.MockTransport``). When None, a client is created
            and closed per call.
    """

    tool_name: str = "web_search"
    description: str = (
        "Search the web and get back results with title, url, published date, "
        "and a text excerpt. Use focused queries (an author's name plus what "
        "you want to know) rather than whole questions."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="query",
            tool_type="string",
            description="The search query.",
        ),
        ToolParameter(
            param_name="num_results",
            tool_type="number",
            description=(
                f"How many results to return (default {_DEFAULT_NUM_RESULTS}, "
                f"max {_MAX_NUM_RESULTS})."
            ),
            required=False,
        ),
    ]

    provider: SearchProviderId | None = None
    api_key: str | None = None
    max_chars_per_result: int = 2000
    timeout_seconds: float = 30.0
    client: Any = None

    @model_validator(mode="after")
    def _key_needs_provider(self) -> WebSearchTool:
        if self.api_key is not None and self.provider is None:
            raise ValueError(
                "WebSearchTool(api_key=...) requires provider= too — a bare key "
                "does not say which API it belongs to."
            )
        return self

    def resolved_provider(self) -> tuple[SearchProviderId, str] | None:
        """Resolve which provider this tool would call, and with what key.

        Resolved at call time rather than at construction so a key exported
        after the tool was built (or cleared since) is honoured.

        Returns:
            tuple[SearchProviderId, str] | None: ``(provider, api_key)``, or
                None when no provider has a usable key.
        """
        if self.provider is not None:
            key = self.api_key or os.environ.get(_ENV_KEYS[self.provider], "")
            return (self.provider, key) if key else None
        for provider in _PROVIDER_ORDER:
            key = os.environ.get(_ENV_KEYS[provider], "")
            if key:
                return (provider, key)
        return None

    def _request(
        self, provider: SearchProviderId, key: str, query: str, num_results: int
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Build the (payload, headers) pair for one provider's search call."""
        if provider == "exa":
            return (
                {
                    "query": query,
                    "numResults": num_results,
                    "contents": {"text": {"maxCharacters": self.max_chars_per_result}},
                },
                {"x-api-key": key},
            )
        return (
            {
                "query": query,
                "max_results": num_results,
                "include_answer": False,
                "include_raw_content": False,
            },
            {"Authorization": f"Bearer {key}"},
        )

    def _normalise(
        self, provider: SearchProviderId, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Map a provider response onto the common result shape."""
        results: list[dict[str, Any]] = []
        for row in payload.get("results") or []:
            if provider == "exa":
                text = row.get("text") or ""
            else:
                text = row.get("content") or ""
            results.append(
                {
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "published_date": row.get("published_date")
                    or row.get("publishedDate"),
                    "text": text[: self.max_chars_per_result],
                }
            )
        return results

    @weave.op
    async def forward(self, query: str, num_results: float | None = None) -> Any:
        resolved = self.resolved_provider()
        if resolved is None:
            wanted = (
                _ENV_KEYS[self.provider]
                if self.provider
                else " or ".join(_ENV_KEYS[p] for p in _PROVIDER_ORDER)
            )
            return ToolResult.error(
                f"Web search is unavailable: no API key configured (set {wanted}). "
                "Continue using only the book's own text, and leave web-derived "
                "fields null rather than guessing."
            )
        provider, key = resolved
        count = int(num_results) if num_results else _DEFAULT_NUM_RESULTS
        count = max(1, min(count, _MAX_NUM_RESULTS))
        payload, headers = self._request(provider, key, query, count)

        client = self.client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                _ENDPOINTS[provider], json=payload, headers=headers
            )
        except httpx.HTTPError as e:
            return ToolResult.error(f"{provider} search request failed: {e}")
        finally:
            if client is not self.client:
                await client.aclose()

        if response.status_code >= 400:
            return ToolResult.error(
                f"{provider} search failed: HTTP {response.status_code}: "
                f"{response.text[:_ERROR_BODY_CHARS]}"
            )
        try:
            body = response.json()
        except ValueError:
            return ToolResult.error(
                f"{provider} search returned a non-JSON response: "
                f"{response.text[:_ERROR_BODY_CHARS]}"
            )
        results = self._normalise(provider, body)
        if not results:
            return ToolResult.from_text(f"No results (searched via {provider}).")
        return {"provider": provider, "results": results}


def _too_large(url: str, cap: int) -> str:
    """The over-budget message, phrased so the model knows what to do next."""
    return (
        f"{url} is larger than the {cap / _MB:.1f} MB limit for an image in the "
        "transcript. Look for a smaller version — a thumbnail or a preview is "
        "usually enough to judge how something was drawn."
    )


class ViewImageTool(Tool):
    """Fetch an image from a url so the model can actually look at it.

    The image reaches the model as a follow-up user message carrying an
    ``image_url`` part (see
    :func:`diorama.core.results.image_followup_message`), so this tool is only
    useful to a **vision-capable** model — binding it to a text-only one turns a
    successful fetch into a provider error rather than a graceful degradation.
    Every model Diorama defaults to can see.

    Attributes:
        max_bytes (int): Ceiling on a fetched image. Checked against the declared
            ``Content-Length`` first and again as the body streams in, since that
            header is advisory and frequently absent — without the second check a
            lying or silent server could put an arbitrarily large file in memory.
        timeout_seconds (float): HTTP timeout per request.
        client (Any): Optional pre-built ``httpx.AsyncClient`` (tests inject one
            backed by ``httpx.MockTransport``). When None, a client is created
            and closed per call.
    """

    tool_name: str = "view_image"
    description: str = (
        "Fetch an image from a url and look at it. Use this on image urls found "
        "through web_search to see an illustration, painting, or film still for "
        "yourself instead of relying on a written description of it."
    )
    parameters: list[ToolParameter] = [
        ToolParameter(
            param_name="url",
            tool_type="string",
            description=(
                "Direct http(s) url of the image file itself, not of the page "
                "that displays it."
            ),
        ),
    ]

    max_bytes: int = MAX_INLINE_IMAGE_BYTES
    timeout_seconds: float = 30.0
    client: Any = None

    @weave.op
    async def forward(self, url: str) -> Any:
        if urlparse(url).scheme.lower() not in ("http", "https"):
            return ToolResult.error(
                f"{url!r} is not an http(s) url — view_image fetches images over "
                "the web, and cannot read local paths or data urls."
            )

        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds, follow_redirects=True
        )
        data = bytearray()
        try:
            async with client.stream(
                "GET", url, headers={"User-Agent": _IMAGE_USER_AGENT}
            ) as response:
                if response.status_code >= 400:
                    return ToolResult.error(
                        f"Could not fetch {url}: HTTP {response.status_code}. Try "
                        "another source for this image."
                    )
                mime = (
                    (response.headers.get("content-type") or "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                if mime not in _VIEWABLE_IMAGE_TYPES:
                    return ToolResult.error(
                        f"{url} served '{mime or 'no content type'}', which is not "
                        "an image this model can look at (expected one of "
                        f"{', '.join(sorted(_VIEWABLE_IMAGE_TYPES))}). If that was "
                        "a web page, find the url of the image file on it."
                    )
                declared = response.headers.get("content-length") or ""
                if declared.isdigit() and int(declared) > self.max_bytes:
                    return ToolResult.error(_too_large(url, self.max_bytes))
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > self.max_bytes:
                        return ToolResult.error(_too_large(url, self.max_bytes))
        except httpx.HTTPError as e:
            return ToolResult.error(f"Could not fetch {url}: {e}")
        finally:
            if client is not self.client:
                await client.aclose()

        if not data:
            return ToolResult.error(f"{url} returned an empty body — nothing to see.")
        return ToolResult(
            content=[
                TextBlock(text=f"Fetched {url} ({mime}, {len(data)} bytes)."),
                ImageBlock(
                    data=base64.b64encode(bytes(data)).decode("ascii"), mime_type=mime
                ),
            ],
            details={"url": url, "mime_type": mime, "bytes": len(data)},
        )


__all__ = [
    "SearchProviderId",
    "ViewImageTool",
    "WebSearchTool",
]
