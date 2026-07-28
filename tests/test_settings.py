"""Tests for per-agent model settings and provider credentials.

Fully offline: the settings file is redirected into a tmp_path, both model
catalogues and both key-checks are stubbed, and the environment variables that feed
the resolution chain are cleared so a developer's real ``.env`` (which
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
from diorama.models.catalogue import ModelInfo, list_google_models, list_models
from diorama.models.google_pricing import get_pricing as gemini_pricing
from diorama.models.providers import (
    GOOGLE,
    OPENROUTER,
    provider_id_for_model,
    strip_prefix,
    to_litellm_id,
)

KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
GOOGLE_KEY = "AIzaSyD-0123456789abcdefghijklmnopqrs"
#: What an install with an OpenRouter key (or no key at all) falls back to.
DEFAULT_MODEL = "openrouter/google/gemini-3.6-flash"
#: ...and what the same agent falls back to on a Google-only install.
GOOGLE_DEFAULT_MODEL = "gemini/gemini-3.6-flash"
GEMINI_MODEL = "gemini/gemini-2.5-flash"


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the settings store and clear every env var it falls back to."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DIORAMA_LOADER_MODEL_ID", raising=False)
    monkeypatch.delenv("DIORAMA_SCENE_MODEL_ID", raising=False)
    return path


@pytest.fixture
def client(settings_file: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Neither catalogue may touch the network by accident in a route test.
    monkeypatch.setattr(settings_routes, "list_models", lambda **_: [])
    monkeypatch.setattr(settings_routes, "list_google_models", lambda *_a, **_k: [])
    return TestClient(app)


# --------------------------------------------------------------------------- #
# A model id names its provider
# --------------------------------------------------------------------------- #
def test_provider_is_inferred_from_the_model_prefix() -> None:
    assert provider_id_for_model(DEFAULT_MODEL) == OPENROUTER
    assert provider_id_for_model(GEMINI_MODEL) == GOOGLE
    # An id Diorama doesn't recognise resolves to no provider — and therefore to no
    # Diorama-held key, leaving litellm's own environment lookup in charge.
    assert provider_id_for_model("anthropic/claude-sonnet-4") is None
    assert provider_id_for_model("gpt-4o-mini") is None


def test_to_litellm_id_is_idempotent_per_provider() -> None:
    assert to_litellm_id("openai/gpt-4o") == "openrouter/openai/gpt-4o"
    assert to_litellm_id("openrouter/openai/gpt-4o") == "openrouter/openai/gpt-4o"
    assert to_litellm_id("gemini-2.5-flash", GOOGLE) == GEMINI_MODEL
    assert to_litellm_id(GEMINI_MODEL, GOOGLE) == GEMINI_MODEL


def test_strip_prefix_uses_the_registry() -> None:
    assert strip_prefix(DEFAULT_MODEL) == "google/gemini-3.6-flash"
    assert strip_prefix(GEMINI_MODEL) == "gemini-2.5-flash"
    assert strip_prefix("gpt-4o-mini") == "gpt-4o-mini"


# --------------------------------------------------------------------------- #
# Resolution: settings → env → default
# --------------------------------------------------------------------------- #
def test_model_falls_back_to_default(settings_file: Path) -> None:
    model_id, source = resolve_model_id(DioramaSettings(), "ebook_loader")
    assert (model_id, source) == (DEFAULT_MODEL, "default")


def test_the_default_follows_the_provider_the_install_can_authenticate(
    settings_file: Path,
) -> None:
    """Defaults are per provider, so an unconfigured agent runs on the key you have."""
    google_only = DioramaSettings(api_keys={GOOGLE: GOOGLE_KEY})
    openrouter_only = DioramaSettings(api_keys={OPENROUTER: KEY})

    for agent_id in ("ebook_loader", "ebook_scene_segmentation"):
        assert resolve_model_id(google_only, agent_id) == (
            GOOGLE_DEFAULT_MODEL,
            "default",
        )
        assert resolve_model_id(openrouter_only, agent_id) == (DEFAULT_MODEL, "default")


def test_with_several_providers_connected_registry_order_decides(
    settings_file: Path,
) -> None:
    both = DioramaSettings(api_keys={OPENROUTER: KEY, GOOGLE: GOOGLE_KEY})
    assert resolve_model_id(both, "ebook_loader") == (DEFAULT_MODEL, "default")


def test_with_no_provider_connected_the_default_is_still_defined(
    settings_file: Path,
) -> None:
    """Every default is equally unusable here; show the first rather than nothing."""
    assert resolve_model_id(DioramaSettings(), "ebook_loader") == (
        DEFAULT_MODEL,
        "default",
    )


def test_a_key_in_the_environment_also_steers_the_default(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default follows a resolvable key, not specifically a saved one."""
    monkeypatch.setenv("GEMINI_API_KEY", "google-from-env")
    assert resolve_model_id(DioramaSettings(), "ebook_loader") == (
        GOOGLE_DEFAULT_MODEL,
        "default",
    )


async def test_an_unconfigured_agent_on_a_google_only_install_gets_a_usable_runtime(
    settings_file: Path,
) -> None:
    """The regression this exists for.

    Scene segmentation shipped with a single OpenRouter default onto an install that
    held only a Google key. It resolved to an OpenRouter model, was handed no key, and
    401-ed on every section — silently, because its failures are non-fatal by design.
    An unconfigured agent must come out of resolution with a model *and* the key that
    authenticates it.
    """
    await save_settings(
        SettingsUpdate(
            api_keys={GOOGLE: GOOGLE_KEY}, agents={"ebook_loader": GEMINI_MODEL}
        )
    )

    model_id, api_key = await resolve_agent_runtime("ebook_scene_segmentation")

    assert model_id == GOOGLE_DEFAULT_MODEL
    assert api_key == GOOGLE_KEY


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
    assert resolve_api_key(DioramaSettings(), OPENROUTER) == (None, "none")

    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert resolve_api_key(DioramaSettings(), OPENROUTER) == ("from-env", "env")

    stored = DioramaSettings(api_keys={OPENROUTER: "from-settings"})
    assert resolve_api_key(stored, OPENROUTER) == ("from-settings", "settings")


def test_each_provider_resolves_its_own_key(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One key per provider, and one provider's key never stands in for another's."""
    monkeypatch.setenv("GEMINI_API_KEY", "google-from-env")
    settings = DioramaSettings(api_keys={OPENROUTER: KEY})
    assert resolve_api_key(settings, OPENROUTER) == (KEY, "settings")
    assert resolve_api_key(settings, GOOGLE) == ("google-from-env", "env")
    # An unrecognised model's provider is None, which must not fall back to a key.
    assert resolve_api_key(settings, None) == (None, "none")


async def test_resolve_agent_runtime_reads_the_saved_file(settings_file: Path) -> None:
    """What processing.py actually calls before building the loader agent."""
    await save_settings(
        SettingsUpdate(
            api_keys={OPENROUTER: KEY},
            agents={"ebook_loader": "openrouter/openai/gpt-4o"},
        )
    )
    assert await resolve_agent_runtime("ebook_loader") == (
        "openrouter/openai/gpt-4o",
        KEY,
    )


async def test_agent_runtime_picks_the_key_its_model_names(settings_file: Path) -> None:
    """The whole point of per-provider keys: the model chooses the credential."""
    await save_settings(
        SettingsUpdate(
            api_keys={OPENROUTER: KEY, GOOGLE: GOOGLE_KEY},
            agents={"ebook_loader": GEMINI_MODEL},
        )
    )
    assert await resolve_agent_runtime("ebook_loader") == (GEMINI_MODEL, GOOGLE_KEY)


async def test_agent_on_an_unkeyed_provider_gets_no_key(settings_file: Path) -> None:
    """A Gemini model with only an OpenRouter key must not be handed that key."""
    await save_settings(
        SettingsUpdate(
            api_keys={OPENROUTER: KEY}, agents={"ebook_loader": GEMINI_MODEL}
        )
    )
    assert await resolve_agent_runtime("ebook_loader") == (GEMINI_MODEL, None)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
async def test_partial_update_keeps_the_stored_key(settings_file: Path) -> None:
    """The form only ever holds a mask, so an omitted key must mean "unchanged"."""
    await save_settings(SettingsUpdate(api_keys={OPENROUTER: KEY}))
    after = await save_settings(SettingsUpdate(agents={"ebook_loader": "openrouter/x"}))
    assert after.api_keys[OPENROUTER] == KEY


async def test_saving_one_providers_key_leaves_the_others_alone(
    settings_file: Path,
) -> None:
    await save_settings(SettingsUpdate(api_keys={OPENROUTER: KEY}))
    after = await save_settings(SettingsUpdate(api_keys={GOOGLE: GOOGLE_KEY}))
    assert after.api_keys == {OPENROUTER: KEY, GOOGLE: GOOGLE_KEY}


async def test_an_empty_key_clears_that_provider(settings_file: Path) -> None:
    await save_settings(SettingsUpdate(api_keys={OPENROUTER: KEY, GOOGLE: GOOGLE_KEY}))
    after = await save_settings(SettingsUpdate(api_keys={GOOGLE: ""}))
    assert after.api_keys == {OPENROUTER: KEY}


async def test_empty_model_id_resets_an_agent_to_inherit(settings_file: Path) -> None:
    await save_settings(SettingsUpdate(agents={"ebook_loader": "openrouter/openai/x"}))
    after = await save_settings(SettingsUpdate(agents={"ebook_loader": ""}))
    assert after.agents["ebook_loader"].model_id is None
    assert resolve_model_id(after, "ebook_loader") == (DEFAULT_MODEL, "default")


async def test_unknown_agents_are_never_persisted(settings_file: Path) -> None:
    after = await save_settings(SettingsUpdate(agents={"not_an_agent": "openrouter/x"}))
    assert "not_an_agent" not in after.agents


async def test_unknown_providers_are_never_persisted(settings_file: Path) -> None:
    after = await save_settings(SettingsUpdate(api_keys={"not_a_provider": "x"}))
    assert after.api_keys == {}


async def test_settings_file_is_owner_only(settings_file: Path) -> None:
    """It holds plaintext API keys; the umask shouldn't decide who can read them."""
    await save_settings(SettingsUpdate(api_keys={OPENROUTER: KEY}))
    assert settings_file.stat().st_mode & 0o077 == 0


def test_a_mangled_file_falls_back_instead_of_crashing(settings_file: Path) -> None:
    settings_file.write_text("{ not json")
    assert settings_module._read() == DioramaSettings()


def test_the_single_provider_file_shape_still_loads(settings_file: Path) -> None:
    """An install configured before Google existed must not lose its key.

    The old file held one ``provider`` and one ``api_key``; it reads as a key *for
    that provider*, in place, with no migration step to fail.
    """
    settings_file.write_text(
        json.dumps(
            {
                "provider": "openrouter",
                "api_key": KEY,
                "agents": {"ebook_loader": {"model_id": "openrouter/openai/gpt-4o"}},
            }
        )
    )
    settings = settings_module._read()
    assert settings.api_keys == {OPENROUTER: KEY}
    assert resolve_api_key(settings, OPENROUTER) == (KEY, "settings")
    assert settings.agents["ebook_loader"].model_id == "openrouter/openai/gpt-4o"


# --------------------------------------------------------------------------- #
# The API-facing view never carries a key
# --------------------------------------------------------------------------- #
def test_mask_key_keeps_only_the_recognisable_ends() -> None:
    masked = mask_key(KEY)
    assert masked.startswith("sk-or-v1") and masked.endswith(KEY[-4:])
    assert KEY not in masked
    # Short enough to be unrecognisable anyway → fully hidden.
    assert set(mask_key("short")) == {"•"}


def test_view_masks_every_key(settings_file: Path) -> None:
    view = build_view(DioramaSettings(api_keys={OPENROUTER: KEY, GOOGLE: GOOGLE_KEY}))
    dumped = view.model_dump_json()
    assert KEY not in dumped and GOOGLE_KEY not in dumped
    by_id = {p.id: p for p in view.providers}
    assert by_id[OPENROUTER].api_key_masked == mask_key(KEY)
    assert by_id[GOOGLE].api_key_configured is True
    assert by_id[GOOGLE].api_key_source == "settings"


def test_view_reports_which_provider_each_agent_will_bill(settings_file: Path) -> None:
    view = build_view(
        DioramaSettings.model_validate(
            {"agents": {"ebook_loader": {"model_id": GEMINI_MODEL}}}
        )
    )
    assert view.agents[0].provider == GOOGLE


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
    assert [p["id"] for p in body["providers"]] == [OPENROUTER, GOOGLE]
    assert all(p["api_key_configured"] is False for p in body["providers"])
    assert all(p["api_key_source"] == "none" for p in body["providers"])
    assert [a["id"] for a in body["agents"]] == [
        "ebook_loader",
        "ebook_scene_segmentation",
    ]
    assert all(a["model_id"] == DEFAULT_MODEL for a in body["agents"])
    assert all(a["provider"] == OPENROUTER for a in body["agents"])
    # Each agent resolves independently, so a fresh install has nothing saved for
    # either — the shown model is inherited, not chosen.
    assert all(a["configured_model_id"] is None for a in body["agents"])


def test_the_view_reports_the_default_this_install_would_actually_use(
    client: TestClient,
) -> None:
    """`default_model_id` labels the Reset control, so it has to be the real one."""
    assert all(
        a["default_model_id"] == DEFAULT_MODEL
        for a in client.get("/api/settings").json()["agents"]
    )

    client.put("/api/settings", json={"api_keys": {GOOGLE: GOOGLE_KEY}})

    body = client.get("/api/settings").json()
    assert all(a["default_model_id"] == GOOGLE_DEFAULT_MODEL for a in body["agents"])
    assert all(a["model_id"] == GOOGLE_DEFAULT_MODEL for a in body["agents"])
    assert all(a["provider"] == GOOGLE for a in body["agents"])


def test_put_saves_and_returns_the_masked_view(
    client: TestClient, settings_file: Path
) -> None:
    response = client.put(
        "/api/settings",
        json={
            "api_keys": {OPENROUTER: KEY, GOOGLE: GOOGLE_KEY},
            "agents": {"ebook_loader": GEMINI_MODEL},
        },
    )
    assert response.status_code == 200
    assert KEY not in response.text and GOOGLE_KEY not in response.text
    assert response.json()["agents"][0]["model_id"] == GEMINI_MODEL
    assert response.json()["agents"][0]["provider"] == GOOGLE
    # ...but they really are on disk, or the agent couldn't authenticate with them.
    assert json.loads(settings_file.read_text())["api_keys"] == {
        OPENROUTER: KEY,
        GOOGLE: GOOGLE_KEY,
    }


def test_put_rejects_an_unknown_agent(client: TestClient) -> None:
    response = client.put("/api/settings", json={"agents": {"ghost": "openrouter/x"}})
    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]


def test_put_rejects_an_unknown_provider(client: TestClient) -> None:
    response = client.put("/api/settings", json={"api_keys": {"ghost": "k"}})
    assert response.status_code == 400
    assert "ghost" in response.json()["detail"]


def _stub_catalogues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings_routes, "list_models", lambda **_: [_openrouter_model("openai/gpt-4o")]
    )
    monkeypatch.setattr(
        settings_routes,
        "list_google_models",
        lambda *_a, **_k: [_google_model("gemini-2.5-flash")],
    )


def test_models_endpoint_merges_every_connected_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_catalogues(monkeypatch)
    client.put(
        "/api/settings", json={"api_keys": {OPENROUTER: KEY, GOOGLE: GOOGLE_KEY}}
    )

    body = client.get("/api/settings/models").json()
    assert body["available"] is True
    assert [m["id"] for m in body["models"]] == [
        "openrouter/openai/gpt-4o",
        GEMINI_MODEL,
    ]
    assert {s["provider"]: s["count"] for s in body["providers"]} == {
        OPENROUTER: 1,
        GOOGLE: 1,
    }


def test_an_unconnected_provider_contributes_no_models(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenRouter's catalogue is public, but an unkeyed provider is still withheld.

    A model Diorama can't authenticate is one that fails halfway through the first
    book, so the picker lists what this install can run — not what exists.
    """
    _stub_catalogues(monkeypatch)
    client.put("/api/settings", json={"api_keys": {GOOGLE: GOOGLE_KEY}})

    body = client.get("/api/settings/models").json()
    assert [m["id"] for m in body["models"]] == [GEMINI_MODEL]
    status = {s["provider"]: s for s in body["providers"]}
    assert status[OPENROUTER]["needs_key"] is True
    assert status[OPENROUTER]["count"] == 0
    assert status[GOOGLE]["needs_key"] is False


def test_a_key_inherited_from_the_environment_counts_as_connected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connected means resolvable, not typed here — an existing .env still works."""
    _stub_catalogues(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)

    body = client.get("/api/settings/models").json()
    assert [m["id"] for m in body["models"]] == ["openrouter/openai/gpt-4o"]


def test_models_endpoint_distinguishes_unconnected_from_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one of the two empty lists is something the user can fix here."""
    # Connected, but the catalogue fetch came back with nothing.
    monkeypatch.setattr(settings_routes, "list_models", lambda **_: [])
    client.put("/api/settings", json={"api_keys": {OPENROUTER: KEY}})

    status = {
        s["provider"]: s for s in client.get("/api/settings/models").json()["providers"]
    }
    assert status[OPENROUTER] == {
        "provider": OPENROUTER,
        "name": "OpenRouter",
        "available": False,
        "needs_key": False,
        "count": 0,
    }
    assert status[GOOGLE]["needs_key"] is True


def test_models_endpoint_reports_an_empty_catalogue_rather_than_failing(
    client: TestClient,
) -> None:
    """Offline is a normal state — the picker degrades, the page still loads."""
    body = client.get("/api/settings/models").json()
    assert body["models"] == [] and body["available"] is False


def test_test_endpoint_without_a_key(client: TestClient) -> None:
    body = client.post("/api/settings/test", json={}).json()
    assert body["ok"] is False
    assert "No OpenRouter key" in body["message"]


def test_test_endpoint_names_the_provider_it_has_no_key_for(client: TestClient) -> None:
    body = client.post("/api/settings/test", json={"provider": GOOGLE}).json()
    assert body["ok"] is False
    assert "Google AI Studio" in body["message"]
    assert body["provider"] == GOOGLE


def test_test_endpoint_reports_a_rejected_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_key_check(monkeypatch, status_code=401)
    body = client.post("/api/settings/test", json={"api_key": "bad"}).json()
    assert body["ok"] is False
    assert "rejected" in body["message"]


def test_google_test_endpoint_accepts_a_key_that_can_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Google publishes no key-info endpoint, so listing models is the check."""
    _stub_key_check(monkeypatch, status_code=200, payload={"models": []})
    body = client.post(
        "/api/settings/test", json={"provider": GOOGLE, "api_key": GOOGLE_KEY}
    ).json()
    assert body["ok"] is True
    assert body["provider"] == GOOGLE
    # No balance is reported rather than a zero that would read as "nothing spent".
    assert body["usage_usd"] is None and body["limit_usd"] is None


def test_google_test_endpoint_reports_a_rejected_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Google answers a bad key with 400, not 401.
    _stub_key_check(monkeypatch, status_code=400)
    body = client.post(
        "/api/settings/test", json={"provider": GOOGLE, "api_key": "bad"}
    ).json()
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
        lambda **_: [_openrouter_model("meta/llama-guard", supports_tools=False)],
    )
    client.put(
        "/api/settings",
        json={"agents": {"ebook_loader": "openrouter/meta/llama-guard"}},
    )

    body = client.post("/api/settings/test", json={"api_key": "good"}).json()
    assert body["ok"] is True
    assert body["usage_usd"] == 1.5
    assert any("tool calling" in w for w in body["warnings"])


def test_test_endpoint_warns_about_a_model_the_provider_does_not_serve(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_key_check(monkeypatch, status_code=200, payload={"data": {}})
    monkeypatch.setattr(
        settings_routes, "list_models", lambda **_: [_openrouter_model("openai/gpt-4o")]
    )
    client.put("/api/settings", json={"agents": {"ebook_loader": "openrouter/made/up"}})

    body = client.post("/api/settings/test", json={"api_key": "good"}).json()
    assert any("made/up" in w for w in body["warnings"])


def test_warnings_are_scoped_to_the_provider_under_test(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OpenRouter key says nothing about an agent pointed at Gemini."""
    _stub_key_check(monkeypatch, status_code=200, payload={"data": {}})
    monkeypatch.setattr(
        settings_routes, "list_models", lambda **_: [_openrouter_model("openai/gpt-4o")]
    )
    # Every agent, or the ones left on OpenRouter would warn about this stub
    # catalogue and drown out what's being asserted.
    client.put(
        "/api/settings",
        json={
            "agents": {
                "ebook_loader": GEMINI_MODEL,
                "ebook_scene_segmentation": GEMINI_MODEL,
            }
        },
    )

    body = client.post("/api/settings/test", json={"api_key": "good"}).json()
    assert body["warnings"] == []


# --------------------------------------------------------------------------- #
# Catalogue — OpenRouter
# --------------------------------------------------------------------------- #
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
    assert model.provider == OPENROUTER
    assert model.provider_model_id == "openai/gpt-4o"
    assert model.vendor == "openai"
    assert model.context_length == 128000
    assert model.prompt_price == pytest.approx(2.5e-06)
    assert model.pricing_known is True
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
    assert [m.provider_model_id for m in list_models(cache_path=cache, force=True)] == [
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
# Catalogue — Google AI Studio
# --------------------------------------------------------------------------- #
GOOGLE_RAW = {
    "models": [
        {
            "name": "models/gemini-2.5-flash",
            "displayName": "Gemini 2.5 Flash",
            "inputTokenLimit": 1048576,
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        },
        {
            "name": "models/gemini-4-imaginary",
            "displayName": "Gemini 4 Imaginary",
            "inputTokenLimit": 32768,
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            "name": "models/gemma-3-27b-it",
            "displayName": "Gemma 3 27B",
            "supportedGenerationMethods": ["generateContent"],
        },
        {
            # Can't hold a conversation at all — must not reach the picker.
            "name": "models/text-embedding-004",
            "displayName": "Embedding 004",
            "supportedGenerationMethods": ["embedContent"],
        },
    ]
}


def test_google_catalogue_needs_a_key(tmp_path: Path) -> None:
    """Without a key there is nothing callable, so nothing is offered."""
    assert list_google_models(None, cache_path=tmp_path / "google.json") == []


def test_google_catalogue_parses_prices_capability_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_google_fetch(monkeypatch, GOOGLE_RAW)
    cache = tmp_path / "google.json"

    models = {
        m.provider_model_id: m for m in list_google_models(GOOGLE_KEY, cache_path=cache)
    }
    assert "text-embedding-004" not in models  # no generateContent

    flash = models["gemini-2.5-flash"]
    assert flash.id == GEMINI_MODEL
    assert flash.provider == GOOGLE
    assert flash.context_length == 1048576
    assert flash.supports_tools is True
    assert flash.pricing_known is True
    assert flash.prompt_price == pytest.approx(0.30 / 1_000_000)

    # A model Diorama has no rate for shows as unpriced, not as free.
    unknown = models["gemini-4-imaginary"]
    assert unknown.pricing_known is False
    assert unknown.prompt_price == 0.0

    # Gemma runs on AI Studio but can't call tools, which the loader needs.
    assert models["gemma-3-27b-it"].supports_tools is False

    assert list_google_models(GOOGLE_KEY, cache_path=cache)  # served from disk
    assert calls["n"] == 1


def test_google_catalogue_follows_pagination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [
        {
            "models": [
                {
                    "name": "models/gemini-2.5-pro",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ],
            "nextPageToken": "more",
        },
        {
            "models": [
                {
                    "name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        },
    ]
    _stub_google_fetch(monkeypatch, pages)
    models = list_google_models(GOOGLE_KEY, cache_path=tmp_path / "google.json")
    assert {m.provider_model_id for m in models} == {
        "gemini-2.5-pro",
        "gemini-2.5-flash",
    }


def test_google_catalogue_survives_a_failed_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args, **_kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr("httpx.Client", explode)
    assert list_google_models(GOOGLE_KEY, cache_path=tmp_path / "missing.json") == []


# --------------------------------------------------------------------------- #
# Gemini pricing table
# --------------------------------------------------------------------------- #
def test_gemini_pricing_matches_the_longest_family(tmp_path: Path) -> None:
    """`-lite` is a different model from the family whose name it extends."""
    lite = gemini_pricing("gemini/gemini-2.5-flash-lite-preview-06-17")
    flash = gemini_pricing("gemini/gemini-2.5-flash-preview-09-2025")
    assert lite is not None and flash is not None
    assert lite.prompt == pytest.approx(0.10 / 1_000_000)
    assert flash.prompt == pytest.approx(0.30 / 1_000_000)


def test_gemini_pricing_accepts_every_spelling_of_an_id() -> None:
    bare = gemini_pricing("gemini-2.5-pro")
    assert bare == gemini_pricing("gemini/gemini-2.5-pro")
    assert bare == gemini_pricing("models/gemini-2.5-pro")


def test_gemini_pricing_never_bills_thinking_tokens_twice() -> None:
    """litellm folds `thoughtsTokenCount` into completion_tokens before we see it."""
    pricing = gemini_pricing(GEMINI_MODEL)
    assert pricing is not None and pricing.reasoning == 0.0


def test_gemini_pricing_falls_through_for_unknown_models() -> None:
    assert gemini_pricing("gemini/gemini-99-ultra") is None
    assert gemini_pricing("openrouter/google/gemini-2.5-flash") is None


def test_gemini_cached_input_is_cheaper_than_fresh_prompt() -> None:
    """The reason this table exists: litellm's static map has no cache rate at all."""
    pricing = gemini_pricing(GEMINI_MODEL)
    assert pricing is not None
    breakdown = pricing.cost_breakdown(
        prompt_tokens=1000, completion_tokens=0, cache_read_tokens=900
    )
    assert breakdown["prompt"] == pytest.approx(100 * pricing.prompt)
    assert breakdown["cache_read"] == pytest.approx(900 * pricing.cache_read)
    assert breakdown["cache_read"] < 900 * pricing.prompt


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _openrouter_model(model_ref: str, *, supports_tools: bool = True) -> ModelInfo:
    return ModelInfo(
        id=to_litellm_id(model_ref),
        provider=OPENROUTER,
        provider_model_id=model_ref,
        name=model_ref,
        vendor=model_ref.split("/")[0],
        context_length=128000,
        prompt_price=1e-06,
        completion_price=2e-06,
        pricing_known=True,
        supports_tools=supports_tools,
    )


def _google_model(model_ref: str, *, supports_tools: bool = True) -> ModelInfo:
    return ModelInfo(
        id=to_litellm_id(model_ref, GOOGLE),
        provider=GOOGLE,
        provider_model_id=model_ref,
        name=model_ref,
        vendor="google",
        context_length=1048576,
        prompt_price=3e-07,
        completion_price=2.5e-06,
        pricing_known=True,
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
    """Stand in for either provider's async credential check."""

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


def _stub_google_fetch(
    monkeypatch: pytest.MonkeyPatch, payload: dict | list[dict]
) -> dict:
    """Stand in for the paginated ``v1beta/models`` list, one entry per page."""
    pages = payload if isinstance(payload, list) else [payload]
    calls = {"n": 0}

    class _Client:
        def __init__(self, **_kwargs) -> None: ...

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *_exc) -> None: ...

        def get(self, *_args, **kwargs) -> _Response:
            page = 0
            token = (kwargs.get("params") or {}).get("pageToken")
            if token:
                page = (
                    next(
                        i
                        for i, p in enumerate(pages)
                        if p.get("nextPageToken") == token
                    )
                    + 1
                )
            else:
                calls["n"] += 1
            return _Response(200, pages[page])

    monkeypatch.setattr("httpx.Client", _Client)
    return calls
