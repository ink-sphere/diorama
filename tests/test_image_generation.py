from __future__ import annotations

import asyncio
import base64
from contextlib import aclosing
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fal_client
import httpx
import pytest
from PIL import Image

from diorama.image_generation.text_to_image import BaseFalT2IHandler, Flux3T2Ihandler


@pytest.mark.parametrize("inline", [True, False])
async def test_flux3_submits_all_inputs_and_downloads_loaded_image(monkeypatch, inline):
    with BytesIO() as buffer:
        Image.new("RGB", (3, 4), "red").save(buffer, format="PNG")
        content = buffer.getvalue()
    url = (
        "data:image/png;base64," + base64.b64encode(content).decode()
        if inline
        else "https://example.com/image.png"
    )
    submit = AsyncMock(return_value=SimpleNamespace(request_id="request-1"))
    result = AsyncMock(return_value={"images": [{"url": url}]})
    monkeypatch.setattr(fal_client, "submit_async", submit)
    monkeypatch.setattr(fal_client, "result_async", result)
    download = AsyncMock(
        return_value=httpx.Response(
            200,
            content=content,
            request=httpx.Request("GET", "https://example.com/image.png"),
        )
    )
    monkeypatch.setattr(httpx.AsyncClient, "get", download)
    options = {
        "aspect_ratio": "3:4",
        "resolution": "2k",
        "enable_prompt_expansion": False,
        "safety_tolerance": 0,
        "output_format": "png",
        "sync_mode": inline,
        "version": "latest",
    }
    handler = Flux3T2Ihandler(**options)

    request_id = await handler.submit("A red rectangle")
    result.assert_not_awaited()
    assert handler.request_ids == (request_id,)
    # Retrieval by ID works even on a new handler, without its original handles.
    image = await Flux3T2Ihandler().get_result(request_id)

    submit.assert_awaited_once_with(
        handler.endpoint,
        arguments={"prompt": "A red rectangle", **options},
        webhook_url=None,
    )
    result.assert_awaited_once_with(handler.endpoint, request_id)
    with image:
        assert image.size == (3, 4)
        assert image.getpixel((0, 0)) == (255, 0, 0)
        with BytesIO() as buffer:
            image.save(buffer, format="PNG")
    if inline:
        download.assert_not_awaited()
    else:
        download.assert_awaited_once_with(url)


async def test_flux3_preserves_unspecified_boolean_defaults(monkeypatch):
    submit = AsyncMock(return_value=SimpleNamespace(request_id="request"))
    monkeypatch.setattr(fal_client, "submit_async", submit)
    await Flux3T2Ihandler().submit("A red rectangle")
    arguments = submit.call_args.kwargs["arguments"]
    assert "enable_prompt_expansion" not in arguments
    assert "sync_mode" not in arguments
    assert arguments["safety_tolerance"] == 2
    assert arguments["version"] == "latest"


@pytest.mark.parametrize("safety_tolerance", [-1, 5])
def test_flux3_rejects_invalid_safety_tolerance(safety_tolerance):
    with pytest.raises(ValueError, match="safety_tolerance"):
        Flux3T2Ihandler(safety_tolerance=safety_tolerance)


async def test_batch_submits_every_prompt_without_waiting_for_generation(monkeypatch):
    submit = AsyncMock(
        side_effect=[SimpleNamespace(request_id=f"r{i}") for i in range(3)]
    )
    result = AsyncMock()
    monkeypatch.setattr(fal_client, "submit_async", submit)
    monkeypatch.setattr(fal_client, "result_async", result)
    handler = Flux3T2Ihandler()

    ids = await handler.submit_batch(["one", "two", "three"])

    assert ids == ["r0", "r1", "r2"]
    assert handler.request_ids == tuple(ids)
    assert [call.kwargs["arguments"]["prompt"] for call in submit.await_args_list] == [
        "one",
        "two",
        "three",
    ]
    result.assert_not_awaited()
    assert await handler.submit_batch([]) == []
    assert submit.await_count == 3


async def test_batch_validates_all_prompts_before_submitting(monkeypatch):
    submit = AsyncMock()
    monkeypatch.setattr(fal_client, "submit_async", submit)
    with pytest.raises(ValueError, match="prompt must not be empty"):
        await Flux3T2Ihandler().submit_batch(["valid prompt", " "])
    submit.assert_not_awaited()


async def test_partial_submission_failure_retains_accepted_ids(monkeypatch):
    submit = AsyncMock(
        side_effect=[SimpleNamespace(request_id="accepted"), RuntimeError("failed")]
    )
    monkeypatch.setattr(fal_client, "submit_async", submit)
    handler = Flux3T2Ihandler()
    with pytest.raises(RuntimeError, match="failed"):
        await handler.submit_batch(
            ["one", "two", "three"], webhook_url="https://example.com/hook"
        )
    assert handler.request_ids == ("accepted",)
    assert submit.await_count == 2
    assert submit.await_args_list[0].kwargs["webhook_url"] == "https://example.com/hook"


async def test_base_supports_another_endpoint_and_status(monkeypatch):
    class OtherHandler(BaseFalT2IHandler):
        endpoint = "test/other-endpoint"

        def _build_arguments(self, prompt):
            return {"prompt": prompt, "other_option": True}

    submit = AsyncMock(return_value=SimpleNamespace(request_id="other"))
    status = AsyncMock(return_value=fal_client.Queued(position=1))
    monkeypatch.setattr(fal_client, "submit_async", submit)
    monkeypatch.setattr(fal_client, "status_async", status)
    handler = OtherHandler()
    await handler.submit("Another prompt")
    assert submit.call_args.args == (handler.endpoint,)
    assert submit.call_args.kwargs["arguments"]["other_option"] is True
    assert isinstance(await handler.status("other", with_logs=True), fal_client.Queued)
    status.assert_awaited_once_with(handler.endpoint, "other", with_logs=True)


async def test_results_arrive_in_completion_order_and_isolate_failures(monkeypatch):
    handler = Flux3T2Ihandler()
    release = asyncio.Event()
    image = Image.new("RGB", (3, 4))

    async def get_result(request_id):
        if request_id == "slow":
            await release.wait()
            return image
        raise RuntimeError("generation failed")

    monkeypatch.setattr(handler, "get_result", get_result)
    async with aclosing(handler.iter_results(["slow", "failed"])) as results:
        failed = await anext(results)
        assert failed.request_id == "failed"
        assert isinstance(failed.error, RuntimeError)
        assert failed.image is None
        release.set()
        success = await anext(results)
        assert success.request_id == "slow"
        assert success.error is None
        assert success.image is image
        with pytest.raises(StopAsyncIteration):
            await anext(results)
    image.close()


async def test_closing_iterator_cancels_waiters_and_closes_unconsumed_images(
    monkeypatch,
):
    handler = Flux3T2Ihandler()
    images = {name: Image.new("RGB", (3, 4)) for name in ("one", "two")}
    cancelled = asyncio.Event()

    async def get_result(request_id):
        if request_id in images:
            return images[request_id]
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(handler, "get_result", get_result)
    async with aclosing(handler.iter_results(["one", "two", "waiting"])) as results:
        delivered = await anext(results)
    assert cancelled.is_set()
    for request_id, image in images.items():
        if request_id == delivered.request_id:
            assert image.getpixel((0, 0)) == (0, 0, 0)
            image.close()
        else:
            with pytest.raises(ValueError, match="closed image"):
                image.getpixel((0, 0))


async def test_empty_and_duplicate_result_batches(monkeypatch):
    handler = Flux3T2Ihandler()
    get_result = AsyncMock()
    monkeypatch.setattr(handler, "get_result", get_result)
    assert [result async for result in handler.iter_results([])] == []
    with pytest.raises(ValueError, match="unique"):
        await anext(handler.iter_results(["same", "same"]))
    get_result.assert_not_awaited()


@pytest.mark.parametrize(
    "response,error",
    [
        ({"images": []}, ValueError),
        ({"images": [{"url": "data:image/png;base64,aW52YWxpZA=="}]}, OSError),
    ],
)
async def test_get_result_propagates_invalid_outputs(monkeypatch, response, error):
    monkeypatch.setattr(fal_client, "result_async", AsyncMock(return_value=response))
    with pytest.raises(error):
        await Flux3T2Ihandler().get_result("request")


async def test_download_failure_is_reported_for_its_request(monkeypatch):
    monkeypatch.setattr(
        fal_client,
        "result_async",
        AsyncMock(return_value={"images": [{"url": "https://example.com/missing"}]}),
    )
    monkeypatch.setattr(
        httpx.AsyncClient,
        "get",
        AsyncMock(
            return_value=httpx.Response(
                404, request=httpx.Request("GET", "https://example.com/missing")
            )
        ),
    )
    results = [result async for result in Flux3T2Ihandler().iter_results(["request"])]
    assert len(results) == 1
    assert isinstance(results[0].error, httpx.HTTPStatusError)
    assert results[0].image is None
