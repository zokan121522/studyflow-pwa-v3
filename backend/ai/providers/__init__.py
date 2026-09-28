"""AI providers package — NotebookLM provider (and registry)."""

from ai.providers.notebooklm_provider import NotebookLMProvider


# ─── Registry ───────────────────────────────────────────────────────
#
# v3 note: v2 also registered "opencode-acp" (OpenZEN / OpenCode sidecar).
# OpenZen is deferred in v3 until the OpenCode server infra exists
# (nothing listens on :54321 locally) — it will be added to this registry
# in its own sub-phase, keeping the exact v2 name "opencode-acp".
_PROVIDERS: dict[str, type] = {
    "notebooklm": NotebookLMProvider,
}


def get_provider(name: str = "notebooklm", **kwargs) -> object:
    """Resolve an AI provider by name.

    Args:
        name: Provider key (``"notebooklm"`` for now; ``"opencode-acp"``
              arrives with OpenZen).
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