"""Tests for the tools in diorama.core.common_tools.

No network: every request is served by an ``httpx.MockTransport`` handed to the
tool as a pre-built client, so both search providers' real request shapes and
response shapes — and every image-fetch failure mode — are exercised offline.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from diorama.core.common_tools import ViewImageTool, WebSearchTool
from diorama.core.results import ImageBlock

EXA_BODY = {
    "results": [
        {
            "title": "Lewis Carroll",
            "url": "https://example.org/carroll",
            "publishedDate": "2020-01-01",
            "text": "Charles Lutwidge Dodgson, better known as Lewis Carroll…",
        }
    ]
}
TAVILY_BODY = {
    "results": [
        {
            "title": "John Tenniel",
            "url": "https://example.org/tenniel",
            "published_date": "2019-05-05",
            "content": "Sir John Tenniel illustrated the first edition…",
        }
    ]
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    """Never let a developer's real key leak into a test's provider resolution."""
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Provider resolution
# --------------------------------------------------------------------------- #
def test_no_key_anywhere_resolves_to_nothing():
    assert WebSearchTool().resolved_provider() is None


def test_exa_wins_auto_detection_when_both_keys_are_present(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    assert WebSearchTool().resolved_provider() == ("exa", "exa-key")


def test_tavily_is_used_when_it_is_the_only_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    assert WebSearchTool().resolved_provider() == ("tavily", "tavily-key")


def test_explicit_provider_beats_the_other_providers_key(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    tool = WebSearchTool(provider="tavily")
    assert tool.resolved_provider() == ("tavily", "tavily-key")


def test_explicit_provider_without_its_key_resolves_to_nothing(monkeypatch):
    """A forced provider does not silently fall back to the other one."""
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    assert WebSearchTool(provider="exa").resolved_provider() is None


def test_a_bare_api_key_is_rejected_because_it_names_no_api():
    with pytest.raises(ValueError, match="requires provider"):
        WebSearchTool(api_key="some-key")


def test_key_is_resolved_per_call_not_at_construction(monkeypatch):
    """A key exported after the tool was built is still honoured."""
    tool = WebSearchTool()
    assert tool.resolved_provider() is None
    monkeypatch.setenv("EXA_API_KEY", "late-key")
    assert tool.resolved_provider() == ("exa", "late-key")


# --------------------------------------------------------------------------- #
# Missing credentials
# --------------------------------------------------------------------------- #
async def test_search_without_a_key_fails_gracefully_and_names_both_variables():
    result = await WebSearchTool().forward(query="anything")
    assert result.is_error is True
    assert "EXA_API_KEY" in result.text and "TAVILY_API_KEY" in result.text
    # The message must steer the agent to nulls rather than to invention.
    assert "null" in result.text


async def test_missing_key_message_names_only_the_forced_provider():
    result = await WebSearchTool(provider="tavily").forward(query="anything")
    assert "TAVILY_API_KEY" in result.text
    assert "EXA_API_KEY" not in result.text


# --------------------------------------------------------------------------- #
# Request shapes
# --------------------------------------------------------------------------- #
async def test_exa_request_shape_and_normalised_results(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["json"] = _json.loads(request.content)
        return httpx.Response(200, json=EXA_BODY)

    tool = WebSearchTool(client=_client(handler))
    result = await tool.forward(query="Lewis Carroll biography", num_results=3)

    assert seen["url"] == "https://api.exa.ai/search"
    assert seen["headers"]["x-api-key"] == "exa-key"
    assert seen["json"]["numResults"] == 3
    assert (
        seen["json"]["contents"]["text"]["maxCharacters"] == tool.max_chars_per_result
    )

    assert result["provider"] == "exa"
    assert result["results"] == [
        {
            "title": "Lewis Carroll",
            "url": "https://example.org/carroll",
            "published_date": "2020-01-01",
            "text": "Charles Lutwidge Dodgson, better known as Lewis Carroll…",
        }
    ]


async def test_tavily_request_shape_and_normalised_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = _json.loads(request.content)
        return httpx.Response(200, json=TAVILY_BODY)

    result = await WebSearchTool(client=_client(handler)).forward(
        query="John Tenniel", num_results=2
    )

    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["auth"] == "Bearer tavily-key"
    assert seen["json"]["max_results"] == 2

    assert result["provider"] == "tavily"
    # Tavily's 'content' and Exa's 'text' both land in the same 'text' field.
    assert result["results"][0]["text"].startswith("Sir John Tenniel")
    assert result["results"][0]["published_date"] == "2019-05-05"


async def test_result_count_is_clamped_into_range(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["json"] = _json.loads(request.content)
        return httpx.Response(200, json=EXA_BODY)

    tool = WebSearchTool(client=_client(handler))
    await tool.forward(query="q", num_results=999)
    assert seen["json"]["numResults"] == 10


async def test_long_result_text_is_truncated_to_the_budget(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"title": "t", "url": "u", "text": "x" * 10_000}]}
        )

    tool = WebSearchTool(client=_client(handler), max_chars_per_result=50)
    result = await tool.forward(query="q")
    assert len(result["results"][0]["text"]) == 50


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #
async def test_http_error_status_becomes_a_tool_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    result = await WebSearchTool(client=_client(handler)).forward(query="q")
    assert result.is_error is True
    assert "401" in result.text


async def test_transport_failure_becomes_a_tool_error(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tavily-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = await WebSearchTool(client=_client(handler)).forward(query="q")
    assert result.is_error is True
    assert "tavily search request failed" in result.text


async def test_non_json_body_becomes_a_tool_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    result = await WebSearchTool(client=_client(handler)).forward(query="q")
    assert result.is_error is True
    assert "non-JSON" in result.text


async def test_empty_results_are_reported_as_text_not_an_error(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    result = await WebSearchTool(client=_client(handler)).forward(query="q")
    assert result.is_error is False
    assert "No results" in result.text


def test_tool_schema_exposes_query_and_num_results():
    schema = WebSearchTool().to_json_schema()
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"query", "num_results"}
    assert params["required"] == ["query"]


# --------------------------------------------------------------------------- #
# ViewImageTool
# --------------------------------------------------------------------------- #
PNG = b"\x89PNG\r\n\x1a\n" + b"fake pixels"


def _image_handler(
    body: bytes = PNG,
    *,
    content_type: str = "image/png",
    status: int = 200,
    headers: dict | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=body,
            headers={"content-type": content_type, **(headers or {})},
        )

    return handler


async def test_a_fetched_image_reaches_the_model_as_an_image_block():
    tool = ViewImageTool(client=_client(_image_handler()))
    result = await tool.forward(url="https://example.org/tenniel.png")

    assert result.is_error is False
    images = result.images
    assert len(images) == 1
    assert isinstance(images[0], ImageBlock)
    assert base64.b64decode(images[0].data) == PNG
    assert images[0].mime_type == "image/png"
    # The text block names the source, so a later citation can point at it.
    assert "tenniel.png" in result.text
    assert result.details["bytes"] == len(PNG)


async def test_the_served_content_type_wins_over_the_url_extension():
    """A url ending .png that serves jpeg must be tagged as what it really is."""
    tool = ViewImageTool(client=_client(_image_handler(content_type="image/jpeg")))
    result = await tool.forward(url="https://example.org/plate.png")
    assert result.images[0].mime_type == "image/jpeg"


async def test_a_charset_suffix_on_the_content_type_is_tolerated():
    handler = _image_handler(content_type="image/png; charset=binary")
    result = await ViewImageTool(client=_client(handler)).forward(
        url="https://example.org/a.png"
    )
    assert result.is_error is False
    assert result.images[0].mime_type == "image/png"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "data:image/png;base64,AAAA"])
async def test_non_http_urls_are_refused_without_a_request(url):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not be reached")

    result = await ViewImageTool(client=_client(handler)).forward(url=url)
    assert result.is_error is True
    assert "http(s)" in result.text


async def test_an_html_page_is_refused_with_advice_to_find_the_image_itself():
    handler = _image_handler(b"<html>", content_type="text/html")
    result = await ViewImageTool(client=_client(handler)).forward(
        url="https://example.org/gallery"
    )
    assert result.is_error is True
    assert "text/html" in result.text
    assert "web page" in result.text


async def test_svg_is_refused_because_a_vision_model_cannot_see_it():
    handler = _image_handler(b"<svg/>", content_type="image/svg+xml")
    result = await ViewImageTool(client=_client(handler)).forward(
        url="https://example.org/a.svg"
    )
    assert result.is_error is True
    assert not result.images


async def test_a_declared_content_length_over_the_cap_is_refused():
    handler = _image_handler(headers={"content-length": "99999999"})
    result = await ViewImageTool(client=_client(handler), max_bytes=1000).forward(
        url="https://example.org/huge.png"
    )
    assert result.is_error is True
    assert "larger than" in result.text


async def test_an_undeclared_oversize_body_is_still_caught_while_streaming():
    """Content-Length is advisory; the cap must hold without it."""
    handler = _image_handler(b"x" * 5000, headers={"content-length": "10"})
    result = await ViewImageTool(client=_client(handler), max_bytes=1000).forward(
        url="https://example.org/liar.png"
    )
    assert result.is_error is True
    assert "larger than" in result.text
    assert not result.images


async def test_http_error_status_becomes_a_tool_error_not_an_image():
    result = await ViewImageTool(client=_client(_image_handler(status=404))).forward(
        url="https://example.org/missing.png"
    )
    assert result.is_error is True
    assert "404" in result.text


async def test_transport_failure_becomes_a_tool_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    result = await ViewImageTool(client=_client(handler)).forward(
        url="https://example.org/a.png"
    )
    assert result.is_error is True
    assert "Could not fetch" in result.text


async def test_an_empty_body_is_an_error_rather_than_a_blank_image():
    result = await ViewImageTool(client=_client(_image_handler(b""))).forward(
        url="https://example.org/empty.png"
    )
    assert result.is_error is True
    assert not result.images


async def test_the_fetch_identifies_itself_since_archives_reject_anonymous_requests():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    await ViewImageTool(client=_client(handler)).forward(
        url="https://example.org/a.png"
    )
    assert "Diorama" in seen["ua"]


def test_view_image_schema_exposes_a_single_required_url():
    schema = ViewImageTool().to_json_schema()
    params = schema["function"]["parameters"]
    assert set(params["properties"]) == {"url"}
    assert params["required"] == ["url"]
