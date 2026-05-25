"""Multi-LLM-provider resolver (v2.14.0).

Reads the `llm:` section of `mcp_agent.config.yaml` and returns the right
`AugmentedLLM` subclass + default model for each *role*. Env vars override
file config for ops convenience.

Roles in use (case-insensitive):

    default       — fallback when a role isn't recognized
    analysis      — the 6 KR analysis agents (price, flow, financial,
                    industry, news, market)
    strategist    — Investment Strategist (cross-section integrator)
    insight       — `/insight` archive Q&A agent
    translator    — Telegram broadcast translator agents
    summary       — Telegram 400-char summary agent
    trading       — Buy/Sell Specialist (uses OpenAIResponsesLLM only when
                    provider == openai, falls back to standard chat for
                    other providers)

Precedence (highest first):
    1. `PRISM_LLM_PROVIDER_<ROLE>` / `PRISM_LLM_MODEL_<ROLE>` env vars
    2. `PRISM_LLM_PROVIDER` / `PRISM_LLM_MODEL` env vars (apply to every role)
    3. `mcp_agent.config.yaml` `llm.roles.<role>` block
    4. `mcp_agent.config.yaml` `llm.default` block
    5. Hardcoded fallback (openai / gpt-5.4-mini) — preserves pre-v2.14
       behavior so existing deployments that don't ship a `llm:` block
       keep working

Imports of provider-specific LLM classes are lazy so a deployment that
only uses OpenAI doesn't need the Google/Anthropic packages installed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple, Type

logger = logging.getLogger(__name__)


# Hardcoded fallback — preserve pre-v2.14 behavior when nothing is configured.
_DEFAULT_PROVIDER = "openai"
_DEFAULT_MODEL = "gpt-5.4-mini"

# Role-specific provider/model defaults. Used when neither env vars nor
# `llm:` config supply a value for the role. These mirror what was hardcoded
# in source pre-v2.14 so existing deployments keep their behavior unchanged
# even without adding a `llm:` block to mcp_agent.config.yaml.
_ROLE_DEFAULTS: dict[str, tuple[str, str]] = {
    "analysis":   ("openai",    "gpt-5.4-mini"),      # cores/report_generation.py
    "summary":    ("openai",    "gpt-5.4-mini"),      # executive summary agent
    "strategist": ("anthropic", "claude-sonnet-4-6"), # Investment Strategist (v2.5.1)
    "insight":    ("anthropic", "claude-sonnet-4-6"), # /insight archive agent
    "trading":    ("openai",    "gpt-5.5"),           # Buy/Sell Specialist (v2.11.0)
    "translator": ("openai",    "gpt-5-nano"),        # Telegram translator (cost-optimized)
}

# Per-provider default model — used when the operator switches *provider*
# without explicitly setting a *model*. Model names aren't portable across
# providers (gpt-5.4-mini ≠ gemini-2.5-flash), so we need a sensible
# stand-in.
_PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "openai":    "gpt-5.4-mini",
    "anthropic": "claude-sonnet-4-6",
    "google":    "gemini-2.5-flash",
    "grok":      "grok-4",
}

_KNOWN_PROVIDERS = set(_PROVIDER_DEFAULT_MODELS.keys())


@dataclass(frozen=True)
class RoleConfig:
    """Resolved (provider, model) for a single role."""
    provider: str
    model: str


class LLMProvider:
    """Singleton-like resolver. `reset_cache()` exposed for tests."""

    _config_cache: Optional[dict] = None

    # ------------------------------------------------------------------ config

    @classmethod
    def _project_root(cls) -> Path:
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _load_config(cls) -> dict:
        if cls._config_cache is not None:
            return cls._config_cache

        cfg_path = cls._project_root() / "mcp_agent.config.yaml"
        loaded: dict = {}
        if cfg_path.exists():
            try:
                import yaml
                with cfg_path.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                loaded = data.get("llm", {}) or {}
            except Exception as e:  # pragma: no cover — config file is operator-supplied
                logger.warning("Failed to read llm: section from %s: %s", cfg_path, e)
                loaded = {}
        cls._config_cache = loaded
        return loaded

    @classmethod
    def reset_cache(cls) -> None:
        """Reset config cache (for tests or live config reload)."""
        cls._config_cache = None

    # ------------------------------------------------------------------ resolve

    @classmethod
    def resolve_role(cls, role: str = "default") -> RoleConfig:
        """Return the (provider, model) effective for `role`.

        Resolution precedence (highest first):
          1. `PRISM_LLM_PROVIDER_<ROLE>` / `PRISM_LLM_MODEL_<ROLE>` env var
          2. `PRISM_LLM_PROVIDER` / `PRISM_LLM_MODEL` env var (global)
          3. `llm.roles.<role>.{provider,model}` in mcp_agent.config.yaml
          4. `llm.default.{provider,model}` in mcp_agent.config.yaml
          5. Built-in role default (e.g. trading → openai/gpt-5.5)
          6. Hardcoded global fallback (openai / gpt-5.4-mini)

        Model resolution has one extra rule: if the operator *changed the
        provider* (via env or YAML) but did NOT specify a *model*, we use
        the provider's built-in default model (e.g. google → gemini-2.5-flash)
        instead of carrying over the role's pre-v2.14 OpenAI model name.
        """
        role_upper = role.upper()
        env_provider = (
            os.environ.get(f"PRISM_LLM_PROVIDER_{role_upper}")
            or os.environ.get("PRISM_LLM_PROVIDER")
        )
        env_model = (
            os.environ.get(f"PRISM_LLM_MODEL_{role_upper}")
            or os.environ.get("PRISM_LLM_MODEL")
        )

        cfg = cls._load_config()
        default_cfg = cfg.get("default") or {}
        role_cfg = (cfg.get("roles") or {}).get(role) or {}

        role_default_provider, role_default_model = _ROLE_DEFAULTS.get(
            role, (None, None)
        )

        provider = (
            env_provider
            or role_cfg.get("provider")
            or default_cfg.get("provider")
            or role_default_provider
            or _DEFAULT_PROVIDER
        )
        normalized = provider.strip().lower()
        if normalized not in _KNOWN_PROVIDERS:
            logger.warning(
                "Unknown LLM provider %r for role %r — falling back to %s. "
                "Known: %s",
                provider, role, _DEFAULT_PROVIDER, sorted(_KNOWN_PROVIDERS),
            )
            normalized = _DEFAULT_PROVIDER

        user_overrode_provider = bool(
            env_provider
            or role_cfg.get("provider")
            or default_cfg.get("provider")
        )

        if env_model or role_cfg.get("model") or default_cfg.get("model"):
            # User explicitly set a model — use it as-is.
            model = (
                env_model
                or role_cfg.get("model")
                or default_cfg.get("model")
            )
        elif user_overrode_provider and normalized != (role_default_provider or _DEFAULT_PROVIDER):
            # Operator switched provider but didn't pick a model — use the
            # provider's built-in default so we don't send an openai model
            # name to gemini etc.
            model = _PROVIDER_DEFAULT_MODELS.get(normalized, _DEFAULT_MODEL)
        else:
            # No override — use role default or global hardcoded fallback.
            model = role_default_model or _DEFAULT_MODEL

        return RoleConfig(provider=normalized, model=model)

    # ------------------------------------------------------------------ class lookup

    @classmethod
    def get_llm_class(
        cls,
        role: str = "default",
        *,
        prefer_responses_api: bool = False,
    ) -> Type[Any]:
        """Return the `AugmentedLLM` subclass for `role`.

        `prefer_responses_api=True` + provider=openai → returns
        `OpenAIResponsesLLM` (Buy/Sell Specialist flow). For any other
        provider the flag is silently ignored (responses-style API isn't
        portable yet) and the standard chat-style AugmentedLLM is used.
        """
        rc = cls.resolve_role(role)

        if rc.provider == "openai":
            if prefer_responses_api:
                # Lazy import to avoid pulling cores.llm.openai_responses_llm
                # for callers that don't need it.
                from cores.llm.openai_responses_llm import OpenAIResponsesLLM
                return OpenAIResponsesLLM
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
            return OpenAIAugmentedLLM

        if rc.provider == "anthropic":
            from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
            return AnthropicAugmentedLLM

        if rc.provider == "google":
            # Available in dragon1086/mcp-agent fork (see requirements.txt).
            from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM
            return GoogleAugmentedLLM

        if rc.provider == "grok":
            # xAI's API is OpenAI-compatible. mcp-agent doesn't ship a dedicated
            # Grok LLM class yet, so we route through OpenAIAugmentedLLM with
            # an OPENAI_BASE_URL override set by the operator. The trading
            # `prefer_responses_api` flag is forced off because Grok doesn't
            # implement the Responses API.
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
            return OpenAIAugmentedLLM

        # Should be unreachable — resolve_role normalizes unknown providers.
        raise ValueError(f"Unsupported LLM provider: {rc.provider!r}")

    @classmethod
    def get_model(cls, role: str = "default") -> str:
        return cls.resolve_role(role).model

    @classmethod
    def get_provider(cls, role: str = "default") -> str:
        return cls.resolve_role(role).provider

    # ------------------------------------------------------------------ params

    @classmethod
    def clean_request_params(cls, request_params: Any, role: str = "default") -> Any:
        """Strip provider-incompatible kwargs from `RequestParams` in-place.

        Currently strips:
          - `reasoning_effort` for non-openai providers (GPT-5 specific)
        Returns the (possibly mutated) params object so callers can chain.
        """
        if request_params is None:
            return request_params
        provider = cls.resolve_role(role).provider
        if provider == "openai":
            return request_params  # all params supported

        # Anthropic / Google / Grok don't implement reasoning_effort —
        # strip it unconditionally for non-openai providers so the request
        # doesn't get rejected as "unknown parameter" by the provider's API.
        try:
            current = getattr(request_params, "reasoning_effort", None)
            if current is not None:
                setattr(request_params, "reasoning_effort", None)
                logger.debug(
                    "stripped reasoning_effort=%r for non-openai provider %s",
                    current, provider,
                )
        except (AttributeError, TypeError):
            pass
        return request_params


# ---------------------------------------------------------------- convenience


def resolve_llm(
    role: str = "default",
    *,
    prefer_responses_api: bool = False,
) -> Tuple[Type[Any], str]:
    """Shorthand for `(llm_class, model)` used by call sites:

        cls, model = resolve_llm("analysis")
        llm = await agent.attach_llm(cls)
        report = await llm.generate_str(
            message=msg,
            request_params=clean(RequestParams(model=model, ...)),
        )
    """
    return (
        LLMProvider.get_llm_class(role, prefer_responses_api=prefer_responses_api),
        LLMProvider.get_model(role),
    )


def clean(request_params: Any, role: str = "default") -> Any:
    """Module-level alias for `LLMProvider.clean_request_params`."""
    return LLMProvider.clean_request_params(request_params, role)
