"""Tests for per-agent model settings and provider credentials.

Fully offline: the settings file is redirected into a tmp_path, the OpenRouter
catalogue and key-check are stubbed, and the environment variables that feed the
resolution chain are cleared so a developer's real ``.env`` (which
``diorama.backend.main`` loads at import) can't change the outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diorama.backend import settings as settings_module
from diorama.backend.main import app
from diorama.backend.routes import settings as settings_routes
from diorama.backend.settings import (
    DioramaSettings,
    SettingsUpdate,
    build_view,
    mask_key,
    resolve_agent_runtime,
    resolve_api_key,
    resolve_model_id,
    save_settings,
)
from diorama.models.catalogue import ModelInfo, list_models, to_litellm_id

KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
DEFAULT_MODEL = "openrouter/openai/gpt-4o-mini"


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the settings store and clear every env var it falls back to."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DIORAMA_LOADER_MODEL_ID", raising=False)
    return path


@pytest.fixture
def client(settings_file: Path) -> TestClient:
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Resolution: settings → env → default
# --------------------------------------------------------------------------- #
def test_model_falls_back_to_default(settings_file: Path) -> None:
    model_id, source = resolve_model_id(DioramaSettings(), "ebook_loader")
    assert (model_id, source) == (DEFAULT_MODEL, "default")


def test_env_var_shadows_the_default(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "DIORAMA_LOADER_MODEL_ID", "openrouter/anthropic/claude-sonnet-4"
    )
    model_id, source = resolve_model_id(DioramaSettings(), "ebook_loader")
    assert (model_id, source) == ("openrouter/anthropic/claude-sonnet-4", "env")


def test_saved_setting_shadows_the_env_var(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DIORAMA_LOADER_MODEL_ID", "openrouter/from/env")
    settings = DioramaSettings.model_validate(
        {"agents": {"ebook_loader": {"model_id": "openrouter/from/settings"}}}
    )
    assert resolve_model_id(settings, "ebook_loader") == (
        "openrouter/from/settings",
        "settings",
    )


def test_api_key_resolution_order(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert resolve_api_key(DioramaSettings()) == (None, "none")

    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert resolve_api_key(DioramaSettings()) == ("from-env", "env")

    stored = DioramaSettings(api_key="from-settings")
    assert resolve_api_key(stored) == ("from-settings", "settings")


async def test_resolve_agent_runtime_reads_the_saved_file(settings_file: Path) -> None:
    """What processing.py actually calls before building the loader agent."""
    await save_settings(
        SettingsUpdate(api_key=KEY, agents={"ebook_loader": "openrouter/openai/gpt-4o"})
    )
    assert await resolve_agent_runtime("ebook_loader") == (
        "openrouter/openai/gpt-4o",
        KEY,
    )


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
async def test_partial_update_keeps_the_stored_key(settings_file: Path) -> None:
    """The form only ever holds a mask, so an omitted key must mean "unchanged"."""
    await save_settings(SettingsUpdate(api_key=KEY))
    after = await save_settings(SettingsUpdate(agents={"ebook_loader": "openrouter/x"}))
    assert after.api_key == KEY


async def test_clearing_takes_an_explicit_flag(settings_file: Path) -> None:
    await save_settings(SettingsUpdate(api_key=KEY))
    assert (await save_settings(SettingsUpdate(clear_api_key=True))).api_key is None


async def test_empty_model_id_resets_an_agent_to_inherit(settings_file: Path) -> None:
    await save_settings(SettingsUpdate(agents={"ebook_loader": "openrouter/openai/x"}))
    after = await save_settings(SettingsUpdate(agents={"ebook_loader": ""}))
    assert after.agents["ebook_loader"].model_id is None
    assert resolve_model_id(after, "ebook_loader") == (DEFAULT_MODEL, "default")


async def test_unknown_agents_are_never_persisted(settings_file: Path) -> None:
    after = await save_settings(SettingsUpdate(agents={"not_an_agent": "openrouter/x"}))
    assert "not_an_agent" not in after.agents


async def test_settings_file_is_owner_only(settings_file: Path) -> None:
    """It holds a plaintext API key; the umask shouldn't decide who can read it."""
    await save_settings(SettingsUpdate(api_key=KEY))
    assert settings_file.stat().st_mode & 0o077 == 0


def test_a_mangled_file_falls_back_instead_of_crashing(settings_file: Path) -> None:
    settings_file.write_text("{ not json")
    assert settings_module._read() == DioramaSettings()


# --------------------------------------------------------------------------- #
# The API-facing view never carries the key
# --------------------------------------------------------------------------- #
def test_mask_key_keeps_only_the_recognisable_ends() -> None:
    masked = mask_key(KEY)
    assert masked.startswith("sk-or-v1") and masked.endswith(KEY[-4:])
    assert KEY not in masked
    # Short enough to be unrecognisable anyway → fully hidden.
    assert set(mask_key("short")) == {"•"}


def test_view_masks_the_key(settings_file: Path) -> None:
    view = build_view(DioramaSettings(api_key=KEY))
    assert KEY not in view.model_dump_json()
    assert view.api_key_masked == mask_key(KEY)
    assert view.api_key_configured is True
    assert view.api_key_source == "settings"


def test_view_reports_what_is_saved_separately_from_what_is_effective(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inherited value fills `model_id` but leaves `configured_model_id` empty.

    That gap is what lets the settings page tell "you chose this" from "this came
    from your .env" — and what keeps Save from baking the env value into the file.
    """
    monkeypatch.setenv("DIORAMA_LOADER_MODEL_ID", "openrouter/from/env")
    agent = build_view(DioramaSettings()).agents[0]
    assert agent.model_id == "openrouter/from/env"
    assert agent.model_source == "env"
    assert agent.configured_model_id is None


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def test_get_settings_on_a_fresh_install(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body["api_key_configured"] is False
    assert body["api_key_source"] == "none"
    assert body["provider"] == "openrouter"
    assert [a["id"] for a in body["agents"]] == ["ebook_loader"]
    assert body["agents"][0]["model_id"] == DEFAULT_MODEL


def test_put_saves_and_returns_the_masked_view(
    client: TestClient, settings_file: Path
) -> None:
    response = client.put(
        "/api/settings",
        json={"api_key": KEY, "agents": {"ebook_loader": "openrouter/openai/gpt-4o"}},
    )
    assert response.status_code == 200
    assert KEY not in response.text
    assert response.json()["agents"][0]["model_id"] == "openrouter/openai/gpt-4o"
    # ...but it really is on disk, or the agent couldn't authenticate with it.
    assert json.loads(settings_file.read_text())["api_key"] == KEY


def test_put_rejects_an_unknown_agent(client: TestClient) -> None:
    response = client.put("/api/settings", json={"agents": {"ghost": "openrouter/x"}})
    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]


def test_models_endpoint_shapes_the_catalogue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings_routes, "list_models", lambda **_: [_model("openai/gpt-4o")]
    )
    body = client.get("/api/settings/models").json()
    assert body["available"] is True
    assert body["models"][0]["id"] == "openrouter/openai/gpt-4o"


def test_models_endpoint_reports_an_empty_catalogue_rather_than_failing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Offline is a normal state — the picker degrades, the page still loads."""
    monkeypatch.setattr(settings_routes, "list_models", lambda **_: [])
    body = client.get("/api/settings/models").json()
    assert body == {"models": [], "available": False}


def test_test_endpoint_without_a_key(client: TestClient) -> None:
    body = client.post("/api/settings/test", json={}).json()
    assert body["ok"] is False
    assert "No API key" in body["message"]


def test_test_endpoint_reports_a_rejected_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_key_check(monkeypatch, status_code=401)
    body = client.post("/api/settings/test", json={"api_key": "bad"}).json()
    assert body["ok"] is False
    assert "rejected" in body["message"]


def test_test_endpoint_warns_about_a_model_that_cannot_call_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader is nothing but tool calls; a tool-less model fails mid-book."""
    _stub_key_check(monkeypatch, status_code=200, payload={"data": {"usage": 1.5}})
    monkeypatch.setattr(
        settings_routes,
        "list_models",
        lambda **_: [_model("meta/llama-guard", supports_tools=False)],
    )
    client.put(
        "/api/settings",
        json={"agents": {"ebook_loader": "openrouter/meta/llama-guard"}},
    )

    body = client.post("/api/settings/test", json={"api_key": "good"}).json()
    assert body["ok"] is True
    assert body["usage_usd"] == 1.5
    assert any("tool calling" in w for w in body["warnings"])


def test_test_endpoint_warns_about_a_model_openrouter_does_not_serve(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_key_check(monkeypatch, status_code=200, payload={"data": {}})
    monkeypatch.setattr(
        settings_routes, "list_models", lambda **_: [_model("openai/gpt-4o")]
    )
    client.put("/api/settings", json={"agents": {"ebook_loader": "openrouter/made/up"}})

    body = client.post("/api/settings/test", json={"api_key": "good"}).json()
    assert any("made/up" in w for w in body["warnings"])


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
def test_to_litellm_id_is_idempotent() -> None:
    assert to_litellm_id("openai/gpt-4o") == "openrouter/openai/gpt-4o"
    assert to_litellm_id("openrouter/openai/gpt-4o") == "openrouter/openai/gpt-4o"


def test_catalogue_parses_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        "data": [
            {
                "id": "openai/gpt-4o",
                "name": "OpenAI: GPT-4o",
                "context_length": 128000,
                "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
                "supported_parameters": ["tools", "temperature"],
            },
            {"no_id": True},  # malformed entries are skipped, not fatal
        ]
    }
    calls = _stub_models_fetch(monkeypatch, raw)
    cache = tmp_path / "catalogue.json"

    models = list_models(cache_path=cache)
    assert len(models) == 1
    model = models[0]
    assert model.id == "openrouter/openai/gpt-4o"
    assert model.vendor == "openai"
    assert model.context_length == 128000
    assert model.prompt_price == pytest.approx(2.5e-06)
    assert model.supports_tools is True

    # Second call is served from disk — the picker shouldn't re-fetch per page load.
    assert list_models(cache_path=cache) == models
    assert calls["n"] == 1


def test_catalogue_serves_a_stale_cache_when_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale list of models beats an empty picker; the ids barely churn."""
    cache = tmp_path / "catalogue.json"
    _stub_models_fetch(monkeypatch, {"data": [{"id": "openai/gpt-4o", "pricing": {}}]})
    list_models(cache_path=cache)

    def explode(*_args, **_kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr("httpx.get", explode)
    assert [m.openrouter_id for m in list_models(cache_path=cache, force=True)] == [
        "openai/gpt-4o"
    ]


def test_catalogue_returns_empty_when_offline_with_no_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args, **_kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr("httpx.get", explode)
    assert list_models(cache_path=tmp_path / "missing.json") == []


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _model(openrouter_id: str, *, supports_tools: bool = True) -> ModelInfo:
    return ModelInfo(
        id=to_litellm_id(openrouter_id),
        openrouter_id=openrouter_id,
        name=openrouter_id,
        vendor=openrouter_id.split("/")[0],
        context_length=128000,
        prompt_price=1e-06,
        completion_price=2e-06,
        supports_tools=supports_tools,
    )


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


def _stub_key_check(
    monkeypatch: pytest.MonkeyPatch, *, status_code: int, payload: dict | None = None
) -> None:
    """Stand in for OpenRouter's /api/v1/key check."""

    class _Client:
        def __init__(self, **_kwargs) -> None: ...

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_exc) -> None: ...

        async def get(self, *_args, **_kwargs) -> _Response:
            return _Response(status_code, payload or {})

    monkeypatch.setattr("httpx.AsyncClient", _Client)


def _stub_models_fetch(monkeypatch: pytest.MonkeyPatch, payload: dict) -> dict:
    calls = {"n": 0}

    def fake_get(*_args, **_kwargs) -> _Response:
        calls["n"] += 1
        return _Response(200, payload)

    monkeypatch.setattr("httpx.get", fake_get)
    return calls
