"""Per-agent model configuration and provider credentials.

Diorama runs one or more LLM-backed agents (today: just the ebook loader). Which
model each one uses, and the API key they authenticate with, were previously
environment variables only — this module puts them behind a settings file the UI
can write, without taking the environment variables away.

**Resolution order, for both the model id and the API key: saved settings → the
environment variable → the built-in default.** That ordering is what makes the
settings page additive rather than a replacement: an existing ``.env`` keeps working
untouched, and a value saved from the UI simply shadows it. The API-facing views
report which of the three a live value came from (``source``), so the settings page
can say "inherited from .env" instead of showing a mysteriously pre-filled field.

Credentials are **shared across agents** while the model id is **per agent**: one
OpenRouter account funds every agent, so asking for the same key once per agent
would be friction with no payoff. The stored shape still nests agents in a dict
keyed by agent id, so adding the second agent is a registry entry, not a migration.

The file lives at ``.diorama_data/settings.json`` (gitignored, alongside
``library.json``) and holds the API key in plaintext — the same trust model as the
rest of this single-user localhost tool, where the process already reads the key
from a plaintext ``.env``. Nothing here ever sends a key back to the browser; see
:func:`mask_key` and :class:`SettingsView`.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from diorama.backend.store import DATA_DIR

SETTINGS_FILE = DATA_DIR / "settings.json"

Provider = Literal["openrouter"]
ValueSource = Literal["settings", "env", "default", "none"]

#: The only provider wired up today. Kept as a list (and a Literal) so adding the
#: second one is a data change plus a credential lookup, not a UI rewrite.
PROVIDERS: list[dict[str, str]] = [
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "api_key_env": "OPENROUTER_API_KEY",
        "console_url": "https://openrouter.ai/keys",
    },
]

_lock = asyncio.Lock()


# --------------------------------------------------------------------------- #
# The agent registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgentDefinition:
    """A configurable agent, as the settings page lists it.

    Attributes:
        id (str): Stable key used in ``settings.json`` and API payloads.
        name (str): Human-readable name shown in the UI.
        description (str): One line explaining what the agent does, so a reader can
            tell which model they are choosing a model *for*.
        default_model_id (str): The litellm model id used when nothing is configured.
        model_env_var (str): Environment variable consulted before the default.
    """

    id: str
    name: str
    description: str
    default_model_id: str
    model_env_var: str


AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        id="ebook_loader",
        name="Ebook loader",
        description=(
            "Reads an uploaded EPUB end to end and works out its real hierarchy — "
            "acts, chapters, scenes — before the book lands on the shelf."
        ),
        default_model_id="openrouter/openai/gpt-4o-mini",
        model_env_var="DIORAMA_LOADER_MODEL_ID",
    ),
]

AGENTS_BY_ID: dict[str, AgentDefinition] = {a.id: a for a in AGENTS}


# --------------------------------------------------------------------------- #
# The persisted shape
# --------------------------------------------------------------------------- #
class AgentConfig(BaseModel):
    """What the user has explicitly chosen for one agent (None = inherit)."""

    model_id: str | None = None


class DioramaSettings(BaseModel):
    """The contents of ``.diorama_data/settings.json``.

    Every field is optional and every absent field falls back to the environment —
    an empty (or missing) file must behave exactly like the app did before settings
    existed.
    """

    provider: Provider = "openrouter"
    api_key: str | None = None
    agents: dict[str, AgentConfig] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# API-facing shapes (a key never crosses this boundary)
# --------------------------------------------------------------------------- #
class AgentView(BaseModel):
    """One agent row on the settings page: what it is, and what it will run."""

    id: str
    name: str
    description: str
    model_id: str
    model_source: ValueSource
    default_model_id: str
    model_env_var: str
    configured_model_id: str | None = None


class ProviderView(BaseModel):
    id: str
    name: str
    api_key_env: str
    console_url: str


class SettingsView(BaseModel):
    """Everything the settings page renders.

    ``api_key_masked`` is a display string like ``sk-or-v1…a4f2`` — the real key is
    never serialised here, so a saved key cannot be read back out of the API.
    """

    provider: Provider
    providers: list[ProviderView]
    api_key_configured: bool
    api_key_masked: str | None
    api_key_source: ValueSource
    agents: list[AgentView]


class SettingsUpdate(BaseModel):
    """A partial write. Omitted fields are left exactly as they were.

    ``api_key`` omitted means "keep the stored key" — which is what the form sends
    back when the user did not retype it, since it only ever received a mask.
    Clearing a key therefore needs its own explicit flag rather than an empty string.
    """

    provider: Provider | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    #: agent id → litellm model id. An empty string resets that agent to inherit.
    agents: dict[str, str] | None = None


class ConnectionTest(BaseModel):
    """The result of a "Test connection" click."""

    ok: bool
    message: str
    label: str | None = None
    usage_usd: float | None = None
    limit_usd: float | None = None
    is_free_tier: bool | None = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def _read() -> DioramaSettings:
    if not SETTINGS_FILE.exists():
        return DioramaSettings()
    try:
        return DioramaSettings.model_validate_json(SETTINGS_FILE.read_text() or "{}")
    except Exception:  # noqa: BLE001 — a hand-mangled file shouldn't brick the app
        return DioramaSettings()


def _write(settings: DioramaSettings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(settings.model_dump(mode="json"), indent=2, ensure_ascii=False)
    )
    # The file holds an API key in plaintext; keep it owner-readable rather than
    # inheriting whatever the process umask happens to be.
    try:
        SETTINGS_FILE.chmod(0o600)
    except OSError:  # noqa: BLE001 — best effort (e.g. a mounted volume)
        pass


async def load_settings() -> DioramaSettings:
    async with _lock:
        return _read()


async def save_settings(update: SettingsUpdate) -> DioramaSettings:
    """Apply a partial update to the stored settings and return the new state."""
    async with _lock:
        settings = _read()

        if update.provider is not None:
            settings.provider = update.provider

        if update.clear_api_key:
            settings.api_key = None
        elif update.api_key is not None:
            key = update.api_key.strip()
            settings.api_key = key or None

        for agent_id, model_id in (update.agents or {}).items():
            if agent_id not in AGENTS_BY_ID:
                continue  # ignore unknown agents rather than persisting junk
            chosen = (model_id or "").strip()
            settings.agents[agent_id] = AgentConfig(model_id=chosen or None)

        _write(settings)
        return settings


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def mask_key(key: str) -> str:
    """A key rendered for display: enough to recognise, not enough to use."""
    if len(key) <= 12:
        return "•" * len(key)
    return f"{key[:8]}…{key[-4:]}"


def resolve_api_key(settings: DioramaSettings) -> tuple[str | None, ValueSource]:
    """The API key to authenticate with, and where it came from."""
    if settings.api_key:
        return settings.api_key, "settings"
    env_var = next(
        (p["api_key_env"] for p in PROVIDERS if p["id"] == settings.provider),
        "OPENROUTER_API_KEY",
    )
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env, "env"
    return None, "none"


def resolve_model_id(
    settings: DioramaSettings, agent_id: str
) -> tuple[str, ValueSource]:
    """The litellm model id ``agent_id`` should run with, and where it came from.

    Raises:
        KeyError: If ``agent_id`` is not in :data:`AGENTS`.
    """
    definition = AGENTS_BY_ID[agent_id]
    configured = (settings.agents.get(agent_id) or AgentConfig()).model_id
    if configured:
        return configured, "settings"
    from_env = os.environ.get(definition.model_env_var)
    if from_env:
        return from_env, "env"
    return definition.default_model_id, "default"


async def resolve_agent_runtime(agent_id: str) -> tuple[str, str | None]:
    """``(model_id, api_key)`` for one agent, reading settings fresh.

    Read per run rather than cached at import, so editing the settings page (or a
    ``.env``) takes effect on the next book without restarting the backend.
    """
    settings = await load_settings()
    model_id, _ = resolve_model_id(settings, agent_id)
    api_key, _ = resolve_api_key(settings)
    return model_id, api_key


def build_view(settings: DioramaSettings) -> SettingsView:
    """Project the stored settings into the browser-facing shape (key masked)."""
    api_key, key_source = resolve_api_key(settings)
    return SettingsView(
        provider=settings.provider,
        providers=[ProviderView(**p) for p in PROVIDERS],
        api_key_configured=api_key is not None,
        api_key_masked=mask_key(api_key) if api_key else None,
        api_key_source=key_source,
        agents=[
            AgentView(
                id=definition.id,
                name=definition.name,
                description=definition.description,
                model_id=(resolved := resolve_model_id(settings, definition.id))[0],
                model_source=resolved[1],
                default_model_id=definition.default_model_id,
                model_env_var=definition.model_env_var,
                configured_model_id=(
                    settings.agents.get(definition.id) or AgentConfig()
                ).model_id,
            )
            for definition in AGENTS
        ],
    )


__all__ = [
    "AGENTS",
    "AGENTS_BY_ID",
    "PROVIDERS",
    "SETTINGS_FILE",
    "AgentConfig",
    "AgentDefinition",
    "AgentView",
    "ConnectionTest",
    "DioramaSettings",
    "SettingsUpdate",
    "SettingsView",
    "build_view",
    "load_settings",
    "mask_key",
    "resolve_agent_runtime",
    "resolve_api_key",
    "resolve_model_id",
    "save_settings",
]
