"""AI providers package — NotebookLM provider (and registry)."""

from ai.providers.notebooklm_provider import NotebookLMProvider
from ai.providers.opencode_provider import OpenCodeProvider


# ─── Registry ───────────────────────────────────────────────────────
#
# v3 note: v2 also registered "opencode-acp" (OpenZEN / OpenCode sidecar).
# The OpenZen panel and its sidecar landed first (d382f55, aa2c925) and the
# provider is now registered under the exact v2 name, so the deferred
# sub-phase is closed.
_PROVIDERS: dict[str, type] = {
    "notebooklm": NotebookLMProvider,
    "opencode-acp": OpenCodeProvider,
}


def get_provider(name: str = "notebooklm", **kwargs) -> object:
    """Resolve an AI provider by name.

    Args:
        name: Provider key (``"notebooklm"`` or ``"opencode-acp"``).
        **kwargs: Additional keyword arguments passed to the provider
                  constructor.

    Returns:
        An instance of the provider (satisfies the ``AIProvider`` interface).

    Raises:
        ValueError: If the provider name is unknown.
    """
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{name}'. "
            f"Available: {', '.join(_PROVIDERS)}"
        )
    return cls(**kwargs)


def list_providers() -> list[dict]:
    """Return all available providers as list of dicts."""
    return [
        {
            "name": name,
            "description": cls().description,
        }
        for name, cls in _PROVIDERS.items()
    ]