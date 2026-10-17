from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import typer
from typer.testing import CliRunner

from diorama.models.storybook import EbookMetadata, StoryBook
from scripts import process_ebook, profile_characters


@pytest.fixture(params=[process_ebook, profile_characters], ids=["ebook", "characters"])
def script(request):
    return request.param


@pytest.mark.parametrize(
    "effort", [None, "off", "minimal", "low", "medium", "high", "xhigh", "max"]
)
def test_cli_passes_thinking_effort(script, monkeypatch, effort):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    input_path = ".ebook-runs/example" if script is profile_characters else "input.json"
    args = [input_path, "--model", "test-model"]
    if effort is not None:
        args.extend(["--thinking-effort", effort])
    result = CliRunner().invoke(script.app, args)
    assert result.exit_code == 0, result.output
    main.assert_awaited_once_with(
        Path(input_path),
        "test-model",
        trace=script is process_ebook,
        thinking_effort=effort,
        **(
            {"resume": None, "max_nodes_group": 1}
            if script is profile_characters
            else {}
        ),
    )


@pytest.mark.parametrize(
    "flags", [["--thinking-effort", "invalid"], ["--thinking-effort"]]
)
def test_invalid_effort_is_a_cli_usage_error(script, monkeypatch, flags):
    main = AsyncMock()
    monkeypatch.setattr(script, "main", main)
    result = CliRunner().invoke(script.app, ["input.json", *flags])
    assert result.exit_code == 2
    assert "--thinking-effort" in result.output
    main.assert_not_awaited()


@pytest.mark.parametrize("effort", [None, "high", "max"])
async def test_main_validates_and_forwards_effort_before_login(
    script, monkeypatch, tmp_path, effort
):
    book = StoryBook(id="book", metadata=EbookMetadata(title="Demo"), structure=[])
    path = tmp_path / "storybook.json"
    path.write_text(book.model_dump_json())
    input_path = path.parent if script is profile_characters else path
    credentials = object()
    login = AsyncMock(return_value=credentials)
    config = object()
    provider = SimpleNamespace(aclose=AsyncMock())
    create_provider = Mock(return_value=provider)
    agent = SimpleNamespace(
        run=AsyncMock(return_value=book if script is process_ebook else [])
    )
    monkeypatch.setattr(script, "ensure_login", login)
    monkeypatch.setattr(script, "load_provider_settings", lambda: object())
    monkeypatch.setattr(
        script,
        "resolve_provider_selection",
        lambda *args, **kwargs: SimpleNamespace(provider=config, model="test-model"),
    )
    monkeypatch.setattr(
        script,
        "provider_thinking_levels",
        lambda *args, **kwargs: ("low", "medium", "high"),
    )
    monkeypatch.setattr(script, "create_model_provider", create_provider)

    monkeypatch.setattr(
        script,
        "EbookStructureAgent" if script is process_ebook else "CharacterProfilerAgent",
        Mock(return_value=agent),
    )

    if effort == "max":
        with pytest.raises(
            typer.BadParameter, match="test-model supports: low, medium, high"
        ):
            await script.main(
                input_path, "test-model", trace=False, thinking_effort=effort
            )
        login.assert_not_awaited()
        create_provider.assert_not_called()
        agent.run.assert_not_awaited()
    else:
        await script.main(input_path, "test-model", trace=False, thinking_effort=effort)
        create_provider.assert_called_once_with(
            config,
            credential_store=credentials,
            model="test-model",
            thinking_level=effort,
        )
        agent.run.assert_awaited_once()
        provider.aclose.assert_awaited_once()
