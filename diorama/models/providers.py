"""The providers Diorama can route an agent's model through.

One registry, imported by everything that needs to know a provider exists: the
settings store (which credential to resolve), the model catalogue (which API to
list models from), and the pricing cascade (which rate table applies). Adding a
third provider is an entry here plus a catalogue fetcher, not a change to the
shape of settings, the API, or the UI.

**A model id names its own provider.** litellm addresses OpenRouter as
``openrouter/<vendor>/<model>`` and Google AI Studio as ``gemini/<model>``, so the
prefix on the id an agent is configured with is what decides which credential the
run authenticates with. That is what lets one agent think with a Gemini model while
another goes through OpenRouter — there is no global "current provider" to switch
between, because the choice is already carried by the thing the user actually picked.

An id with an unrecognised prefix (``anthropic/claude-sonnet-4``, say) resolves to
no provider and therefore no Diorama-held key, which leaves litellm to find one in
the environment exactly as it did before any of this existed. Typing an id Diorama
doesn't know about degrades; it doesn't fail.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The provider ids used as keys in ``settings.json`` and in API payloads. Kept in
#: sync by hand with the ``Provider`` Literal in :mod:`diorama.backend.settings`,
#: which pydantic needs as a static annotation.
OPENROUTER = "openrouter"
GOOGLE = "google"


@dataclass(frozen=True)
class ProviderDefinition:
    """One place an agent's model requests can be sent.

    Attributes:
        id (str): Stable key used in ``settings.json`` and API payloads.
        name (str): Display name, e.g. "Google AI Studio".
        litellm_prefix (str): The prefix litellm routes on (``openrouter/``,
            ``gemini/``). Also how :func:`provider_for_model` works backwards from a
            configured model id to the credential it needs.
        api_key_env (str): Environment variable consulted when no key is saved.
        console_url (str): Where a user creates a key.
        key_prefix_hint (str): A placeholder shaped like a real key for that provider.
        blurb (str): One line of orientation for the settings page.
    """

    id: str
    name: str
    litellm_prefix: str
    api_key_env: str
    console_url: str
    key_prefix_hint: str
    blurb: str


PROVIDERS: list[ProviderDefinition] = [
    ProviderDefinition(
        id=OPENROUTER,
        name="OpenRouter",
        litellm_prefix="openrouter/",
        api_key_env="OPENROUTER_API_KEY",
        console_url="https://openrouter.ai/keys",
        key_prefix_hint="sk-or-v1-…",
        blurb="One key, several hundred models from every major lab.",
    ),
    ProviderDefinition(
        id=GOOGLE,
        name="Google AI Studio",
        litellm_prefix="gemini/",
        api_key_env="GEMINI_API_KEY",
        console_url="https://aistudio.google.com/apikey",
        key_prefix_hint="AIza…",
        blurb="Gemini models billed direct, no middleman markup.",
    ),
]

PROVIDERS_BY_ID: dict[str, ProviderDefinition] = {p.id: p for p in PROVIDERS}

#: Longest prefix first, so a future ``gemini/foo/bar`` style id can't be matched by
#: a shorter registry entry that merely shares its opening characters.
_BY_PREFIX: list[ProviderDefinition] = sorted(
    PROVIDERS, key=lambda p: len(p.litellm_prefix), reverse=True
)


def provider_for_model(model_id: str | None) -> ProviderDefinition | None:
    """The provider a litellm model id routes through, or None if unrecognised.

    Args:
        model_id (str | None): A litellm model id, e.g. ``gemini/gemini-2.5-flash``.

    Returns:
        ProviderDefinition | None: The matching provider, or None when no registered
            prefix matches — including for a bare id like ``gpt-4o-mini``, which
            litellm resolves from its own environment lookup.
    """
    mid = (model_id or "").strip()
    for provider in _BY_PREFIX:
        if mid.startswith(provider.litellm_prefix):
            return provider
    return None


def provider_id_for_model(model_id: str | None) -> str | None:
    """:func:`provider_for_model`, as an id."""
    provider = provider_for_model(model_id)
    return provider.id if provider else None


def to_litellm_id(model_ref: str, provider_id: str = OPENROUTER) -> str:
    """Prefix a provider-native model id for litellm, leaving prefixed ids alone.

    Args:
        model_ref (str): A provider-native id (``openai/gpt-4o``, ``gemini-2.5-flash``)
            or an already-prefixed litellm id.
        provider_id (str): Which provider ``model_ref`` belongs to. Ignored when
            ``model_ref`` already carries a known prefix.

    Returns:
        str: A litellm model id.
    """
    ref = (model_ref or "").strip()
    if not ref:
        return ref
    if provider_for_model(ref) is not None:
        return ref
    provider = PROVIDERS_BY_ID.get(provider_id)
    return f"{provider.litellm_prefix}{ref}" if provider else ref


def strip_prefix(model_id: str) -> str:
    """A litellm id with its route prefix removed, for display.

    ``openrouter/openai/gpt-4o-mini`` reads better as ``openai/gpt-4o-mini`` in
    prose, and ``gemini/gemini-2.5-flash`` as ``gemini-2.5-flash``.
    """
    provider = provider_for_model(model_id)
    return model_id[len(provider.litellm_prefix) :] if provider else model_id


__all__ = [
    "GOOGLE",
    "OPENROUTER",
    "PROVIDERS",
    "PROVIDERS_BY_ID",
    "ProviderDefinition",
    "provider_for_model",
    "provider_id_for_model",
    "strip_prefix",
    "to_litellm_id",
]
