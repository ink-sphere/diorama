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

**There is no "current provider".** Credentials are held one per provider and shared
across agents, while the model id stays per agent — and the model id is what names
its provider, via the litellm prefix it carries (see
:mod:`diorama.models.providers`). So the loader can think with a Gemini model while
some later agent goes through OpenRouter, with no mode to switch between: choosing
the model *is* choosing the provider, and the key that authenticates the run follows
from it. A key is still asked for once per provider rather than once per agent, since
one account funds every agent that routes through it.

The file lives at ``.diorama_data/settings.json`` (gitignored, alongside
``library.json``) and holds the API keys in plaintext — the same trust model as the
rest of this single-user localhost tool, where the process already reads a key from a
plaintext ``.env``. Nothing here ever sends a key back to the browser; see
:func:`mask_key` and :class:`SettingsView`.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from diorama.backend.store import DATA_DIR
from diorama.models.providers import (
    GOOGLE,
    OPENROUTER,
    PROVIDERS,
    PROVIDERS_BY_ID,
    provider_id_for_model,
)

SETTINGS_FILE = DATA_DIR / "settings.json"

#: Mirrors the ids in :data:`diorama.models.providers.PROVIDERS`, spelled out because
#: pydantic needs a static annotation. Adding a provider means an entry in both.
Provider = Literal["openrouter", "google"]
ValueSource = Literal["settings", "env", "default", "none"]

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
        default_models (dict[str, str]): provider id → the litellm model id this agent
            falls back to **when that provider is the one the install can
            authenticate**. One default per provider rather than one overall, because
            an unconfigured agent whose single default named a provider you hold no key
            for doesn't run at all — it 401s on its first call, which is a silent
            failure if that agent's failures are non-fatal.
        model_env_var (str): Environment variable consulted before the defaults.
    """

    id: str
    name: str
    description: str
    default_models: dict[str, str]
    model_env_var: str

    @property
    def fallback_model_id(self) -> str:
        """The default when *no* provider is connected — first in registry order.

        Something has to be shown on a fresh install with no keys at all, where every
        default is equally unusable. There is no cleverer answer than "the first one".
        """
        for provider in PROVIDERS:
            model = self.default_models.get(provider.id)
            if model:
                return model
        raise ValueError(f"agent {self.id!r} declares no default model")


AGENTS: list[AgentDefinition] = [
    AgentDefinition(
        id="ebook_loader",
        name="Ebook loader",
        description=(
            "Reads an uploaded EPUB end to end and works out its real hierarchy — "
            "acts, chapters, scenes — before the book lands on the shelf."
        ),
        default_models={
            OPENROUTER: "openrouter/google/gemini-3.6-flash",
            GOOGLE: "gemini/gemini-3.6-flash",
        },
        model_env_var="DIORAMA_LOADER_MODEL_ID",
    ),
    AgentDefinition(
        id="ebook_scene_segmentation",
        name="Scene segmentation",
        description=(
            "Takes each section the loader found and marks where the picture would "
            "change — the scene boundaries a later illustration hangs on."
        ),
        default_models={
            OPENROUTER: "openrouter/google/gemini-3.6-flash",
            GOOGLE: "gemini/gemini-3.6-flash",
        },
        model_env_var="DIORAMA_SCENE_MODEL_ID",
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

    #: provider id → API key. Absent means "fall back to that provider's env var".
    api_keys: dict[str, str] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_single_provider(cls, data: Any) -> Any:
        """Fold the pre-multi-provider shape into ``api_keys``.

        Before Google AI Studio was added, a file held one ``provider`` and one
        ``api_key``. Read it as a key *for that provider*, in place, so an install
        that already had OpenRouter configured keeps working without the user
        re-entering anything and without a migration step that could fail.
        """
        if not isinstance(data, dict) or "api_key" not in data:
            return data
        migrated = dict(data)
        legacy_key = migrated.pop("api_key", None)
        legacy_provider = migrated.pop("provider", None) or OPENROUTER
        if legacy_key and legacy_provider in PROVIDERS_BY_ID:
            keys = dict(migrated.get("api_keys") or {})
            keys.setdefault(legacy_provider, legacy_key)
            migrated["api_keys"] = keys
        return migrated


# --------------------------------------------------------------------------- #
# API-facing shapes (a key never crosses this boundary)
# --------------------------------------------------------------------------- #
class AgentView(BaseModel):
    """One agent row on the settings page: what it is, and what it will run.

    ``provider`` is derived from ``model_id``, not stored — it is how the page knows
    to warn that an agent is pointed at Gemini while only an OpenRouter key is set.
    None means the id carries no prefix Diorama recognises, in which case litellm is
    left to find a credential in the environment itself.

    ``default_model_id`` is **this install's** default (see
    :func:`resolve_default_model_id`), not a constant: it is what clearing the field
    would actually resolve to, which is the only version of "the default" worth
    labelling a Reset control with.
    """

    id: str
    name: str
    description: str
    model_id: str
    model_source: ValueSource
    default_model_id: str
    model_env_var: str
    configured_model_id: str | None = None
    provider: str | None = None


class ProviderView(BaseModel):
    """One provider, with the state of its credential.

    ``api_key_masked`` is a display string like ``sk-or-v1…a4f2`` — the real key is
    never serialised here, so a saved key cannot be read back out of the API.
    """

    id: str
    name: str
    api_key_env: str
    console_url: str
    key_prefix_hint: str
    blurb: str
    model_prefix: str
    api_key_configured: bool
    api_key_masked: str | None
    api_key_source: ValueSource


class SettingsView(BaseModel):
    """Everything the settings page renders."""

    providers: list[ProviderView]
    agents: list[AgentView]


class SettingsUpdate(BaseModel):
    """A partial write. Omitted fields are left exactly as they were.

    Both dicts are sparse in the same way and for the same reason: an entry that
    isn't there means "unchanged", because the form only ever received a mask of a
    key and a resolved (possibly inherited) model id, so sending everything it
    rendered would bake inherited values into the file. An **empty string** is the
    explicit erase — clearing a key, or resetting an agent to inherit.
    """

    #: provider id → API key. "" clears that provider's saved key.
    api_keys: dict[str, str] | None = None
    #: agent id → litellm model id. "" resets that agent to inherit.
    agents: dict[str, str] | None = None


class ConnectionTest(BaseModel):
    """The result of a "Test connection" click, for one provider.

    ``usage_usd`` / ``limit_usd`` / ``is_free_tier`` come from OpenRouter's key
    endpoint; Google's has no equivalent, so they stay None there rather than being
    faked into zeros.
    """

    ok: bool
    message: str
    provider: str = OPENROUTER
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

        for provider_id, api_key in (update.api_keys or {}).items():
            if provider_id not in PROVIDERS_BY_ID:
                continue  # ignore unknown providers rather than persisting junk
            key = (api_key or "").strip()
            if key:
                settings.api_keys[provider_id] = key
            else:
                settings.api_keys.pop(provider_id, None)

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


def resolve_api_key(
    settings: DioramaSettings, provider_id: str | None
) -> tuple[str | None, ValueSource]:
    """One provider's API key, and where it came from.

    Args:
        settings (DioramaSettings): The stored settings.
        provider_id (str | None): Which provider's credential to resolve. None — the
            answer :func:`~diorama.models.providers.provider_for_model` gives for an
            id Diorama doesn't recognise — yields no key, leaving litellm to its own
            environment lookup.

    Returns:
        tuple[str | None, ValueSource]: The key and its origin.
    """
    provider = PROVIDERS_BY_ID.get(provider_id or "")
    if provider is None:
        return None, "none"
    saved = settings.api_keys.get(provider.id)
    if saved:
        return saved, "settings"
    from_env = os.environ.get(provider.api_key_env)
    if from_env:
        return from_env, "env"
    return None, "none"


def resolve_default_model_id(
    settings: DioramaSettings, definition: AgentDefinition
) -> str:
    """The default ``definition`` falls back to **on this install**.

    Picks the default belonging to the first provider (in registry order) this install
    holds a usable key for, so an agent nobody has configured runs on the credential
    that actually exists rather than 401-ing against the one that doesn't. With several
    providers connected the registry order decides; with none, so does
    :attr:`AgentDefinition.fallback_model_id`.

    This is the fix for a real failure: the scene segmenter shipped with a single
    OpenRouter default onto an install that only had a Google key, and — because its
    failures are deliberately non-fatal — every section quietly fell back to one
    whole-section scene instead of the run stopping to say the key was missing.
    """
    for provider in PROVIDERS:
        model = definition.default_models.get(provider.id)
        if model and resolve_api_key(settings, provider.id)[0]:
            return model
    return definition.fallback_model_id


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
    return resolve_default_model_id(settings, definition), "default"


async def resolve_agent_runtime(agent_id: str) -> tuple[str, str | None]:
    """``(model_id, api_key)`` for one agent, reading settings fresh.

    The key is the one belonging to the provider the resolved *model* names, which is
    what lets two agents run against two different providers in the same install.

    Read per run rather than cached at import, so editing the settings page (or a
    ``.env``) takes effect on the next book without restarting the backend.
    """
    settings = await load_settings()
    model_id, _ = resolve_model_id(settings, agent_id)
    api_key, _ = resolve_api_key(settings, provider_id_for_model(model_id))
    return model_id, api_key


def _provider_view(settings: DioramaSettings, provider_id: str) -> ProviderView:
    definition = PROVIDERS_BY_ID[provider_id]
    api_key, source = resolve_api_key(settings, provider_id)
    return ProviderView(
        id=definition.id,
        name=definition.name,
        api_key_env=definition.api_key_env,
        console_url=definition.console_url,
        key_prefix_hint=definition.key_prefix_hint,
        blurb=definition.blurb,
        model_prefix=definition.litellm_prefix,
        api_key_configured=api_key is not None,
        api_key_masked=mask_key(api_key) if api_key else None,
        api_key_source=source,
    )


def build_view(settings: DioramaSettings) -> SettingsView:
    """Project the stored settings into the browser-facing shape (keys masked)."""
    return SettingsView(
        providers=[_provider_view(settings, p.id) for p in PROVIDERS],
        agents=[
            AgentView(
                id=definition.id,
                name=definition.name,
                description=definition.description,
                model_id=(resolved := resolve_model_id(settings, definition.id))[0],
                model_source=resolved[1],
                default_model_id=resolve_default_model_id(settings, definition),
                model_env_var=definition.model_env_var,
                configured_model_id=(
                    settings.agents.get(definition.id) or AgentConfig()
                ).model_id,
                provider=provider_id_for_model(resolved[0]),
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
    "Provider",
    "ProviderView",
    "SettingsUpdate",
    "SettingsView",
    "build_view",
    "load_settings",
    "mask_key",
    "resolve_agent_runtime",
    "resolve_api_key",
    "resolve_default_model_id",
    "resolve_model_id",
    "save_settings",
]
