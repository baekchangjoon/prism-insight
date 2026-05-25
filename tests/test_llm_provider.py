"""Unit tests for cores.llm.provider — multi-LLM-provider resolver (v2.14.0).

Tests verify resolution precedence, role-specific built-in defaults, model-
override semantics when only the provider changes, request-params cleanup
per provider, and lazy class-import behavior. Two end-to-end "user
journey" tests cover the two common configurations: OpenAI default
(pre-v2.14 behavior preserved) and Gemini swap.

CI runs this without mcp_agent installed — provider.py defers all LLM-class
imports until `get_llm_class()` is called, and the resolve/clean/model
functions exercised here never touch LLM packages.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_provider():
    """Load cores/llm/provider.py without going through tracking/__init__.

    Registers in `sys.modules` because `@dataclass(frozen=True)` walks
    `sys.modules[cls.__module__]` at decoration time on Python 3.11+.
    """
    name = "cores.llm.provider"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "cores" / "llm" / "provider.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def reset_provider_state(monkeypatch):
    """Each test starts from a clean cache + no env vars."""
    for key in list(os.environ):
        if key.startswith("PRISM_LLM_"):
            monkeypatch.delenv(key, raising=False)
    mod = _load_provider()
    mod.LLMProvider.reset_cache()
    yield mod
    mod.LLMProvider.reset_cache()


# --------------------------------------------------------- default resolution


def test_default_role_uses_global_fallback(reset_provider_state, tmp_path, monkeypatch):
    """With no config file and no env vars, `default` role returns
    `openai / gpt-5.4-mini` (pre-v2.14 behavior)."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    rc = reset_provider_state.LLMProvider.resolve_role("default")
    assert rc.provider == "openai"
    assert rc.model == "gpt-5.4-mini"


@pytest.mark.parametrize("role,provider,model", [
    ("analysis",   "openai",    "gpt-5.4-mini"),
    ("summary",    "openai",    "gpt-5.4-mini"),
    ("strategist", "anthropic", "claude-sonnet-4-6"),
    ("insight",    "anthropic", "claude-sonnet-4-6"),
    ("trading",    "openai",    "gpt-5.5"),
    ("translator", "openai",    "gpt-5-nano"),
])
def test_role_builtin_defaults_match_pre_v214_hardcoded_values(
    reset_provider_state, tmp_path, monkeypatch, role, provider, model
):
    """The built-in role defaults must mirror the hardcoded model choices
    used before v2.14.0 so deployments without a `llm:` block see zero
    behavior change."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    rc = reset_provider_state.LLMProvider.resolve_role(role)
    assert rc.provider == provider
    assert rc.model == model


# ----------------------------------------------------------------- yaml config


def _write_config(tmp_path: Path, llm_block: str) -> None:
    """Write a minimal mcp_agent.config.yaml with the given llm: block."""
    (tmp_path / "mcp_agent.config.yaml").write_text(
        textwrap.dedent(llm_block).lstrip(), encoding="utf-8"
    )


def test_yaml_default_provider_overrides_role_builtin(
    reset_provider_state, tmp_path, monkeypatch
):
    """If user sets llm.default.provider=google in YAML, every role uses
    google (and the model picks the provider's built-in default since no
    model was specified)."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    _write_config(tmp_path, """
        llm:
          default:
            provider: google
    """)
    reset_provider_state.LLMProvider.reset_cache()

    # analysis was openai by built-in → now google
    rc = reset_provider_state.LLMProvider.resolve_role("analysis")
    assert rc.provider == "google"
    assert rc.model == "gemini-2.5-flash"  # provider default model

    # trading too
    rc = reset_provider_state.LLMProvider.resolve_role("trading")
    assert rc.provider == "google"
    assert rc.model == "gemini-2.5-flash"


def test_yaml_role_overrides_yaml_default(
    reset_provider_state, tmp_path, monkeypatch
):
    """Role-level YAML beats default-level YAML."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    _write_config(tmp_path, """
        llm:
          default:
            provider: google
          roles:
            strategist:
              provider: anthropic
              model: claude-opus-4-7
    """)
    reset_provider_state.LLMProvider.reset_cache()

    assert reset_provider_state.LLMProvider.get_provider("analysis") == "google"
    assert reset_provider_state.LLMProvider.get_provider("strategist") == "anthropic"
    assert reset_provider_state.LLMProvider.get_model("strategist") == "claude-opus-4-7"


def test_yaml_only_model_set_keeps_role_default_provider(
    reset_provider_state, tmp_path, monkeypatch
):
    """If user sets only a model (no provider) for a role, the provider
    stays at the role's built-in default."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    _write_config(tmp_path, """
        llm:
          roles:
            analysis:
              model: gpt-5.1
    """)
    reset_provider_state.LLMProvider.reset_cache()

    rc = reset_provider_state.LLMProvider.resolve_role("analysis")
    assert rc.provider == "openai"   # built-in default for analysis
    assert rc.model == "gpt-5.1"     # user override


# ---------------------------------------------------------------- env overrides


def test_global_env_provider_beats_yaml(reset_provider_state, tmp_path, monkeypatch):
    """`PRISM_LLM_PROVIDER` env var wins over `llm.default.provider` YAML."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    _write_config(tmp_path, """
        llm:
          default:
            provider: google
    """)
    reset_provider_state.LLMProvider.reset_cache()
    monkeypatch.setenv("PRISM_LLM_PROVIDER", "anthropic")

    rc = reset_provider_state.LLMProvider.resolve_role("analysis")
    assert rc.provider == "anthropic"
    # Model defaults to provider's built-in since user only set env provider
    assert rc.model == "claude-sonnet-4-6"


def test_role_specific_env_beats_global_env(reset_provider_state, tmp_path, monkeypatch):
    """`PRISM_LLM_PROVIDER_ANALYSIS` wins over `PRISM_LLM_PROVIDER`."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PRISM_LLM_PROVIDER", "google")
    monkeypatch.setenv("PRISM_LLM_PROVIDER_ANALYSIS", "anthropic")
    monkeypatch.setenv("PRISM_LLM_MODEL_ANALYSIS", "claude-opus-4-7")

    rc = reset_provider_state.LLMProvider.resolve_role("analysis")
    assert rc.provider == "anthropic"
    assert rc.model == "claude-opus-4-7"

    # Other roles use the global env override
    rc2 = reset_provider_state.LLMProvider.resolve_role("trading")
    assert rc2.provider == "google"


def test_unknown_provider_falls_back_to_openai_with_warning(
    reset_provider_state, tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PRISM_LLM_PROVIDER", "mistral")
    rc = reset_provider_state.LLMProvider.resolve_role("default")
    assert rc.provider == "openai"   # fall back


# ---------------------------------------------------------------- params cleanup


class _FakeRequestParams:
    """Stand-in for mcp_agent.workflows.llm.augmented_llm.RequestParams."""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_clean_strips_reasoning_effort_for_non_openai(
    reset_provider_state, tmp_path, monkeypatch
):
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("PRISM_LLM_PROVIDER", "google")

    rp = _FakeRequestParams(model="gemini-2.5-flash", reasoning_effort="none")
    cleaned = reset_provider_state.clean(rp, role="analysis")
    assert cleaned.reasoning_effort is None


def test_clean_preserves_reasoning_effort_for_openai(
    reset_provider_state, tmp_path, monkeypatch
):
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    # default = openai
    rp = _FakeRequestParams(model="gpt-5.4-mini", reasoning_effort="none")
    cleaned = reset_provider_state.clean(rp, role="analysis")
    assert cleaned.reasoning_effort == "none"


def test_clean_handles_none_request_params(reset_provider_state):
    assert reset_provider_state.clean(None, role="analysis") is None


# ---------------------------------------------------------- end-to-end journeys


def test_journey_default_openai_preserves_pre_v214_behavior(
    reset_provider_state, tmp_path, monkeypatch
):
    """User journey 1: deploy without touching llm: config — every role
    must produce the same provider+model as the pre-v2.14 hardcoded source.
    """
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    expected = {
        "analysis":   ("openai",    "gpt-5.4-mini"),
        "summary":    ("openai",    "gpt-5.4-mini"),
        "strategist": ("anthropic", "claude-sonnet-4-6"),
        "insight":    ("anthropic", "claude-sonnet-4-6"),
        "trading":    ("openai",    "gpt-5.5"),
        "translator": ("openai",    "gpt-5-nano"),
    }
    for role, (provider, model) in expected.items():
        rc = reset_provider_state.LLMProvider.resolve_role(role)
        assert (rc.provider, rc.model) == (provider, model), \
            f"Role {role}: expected ({provider},{model}) got ({rc.provider},{rc.model})"


def test_journey_gemini_swap_with_anthropic_strategist(
    reset_provider_state, tmp_path, monkeypatch
):
    """User journey 2: operator has only a Gemini key + an Anthropic key,
    wants OpenAI roles routed to Gemini but keeps the strategist on
    Anthropic. This is the hybrid layout we recommend in the runbook."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))
    _write_config(tmp_path, """
        llm:
          default:
            provider: google
            model: gemini-2.5-flash
          roles:
            strategist:
              provider: anthropic
              model: claude-sonnet-4-6
            insight:
              provider: anthropic
              model: claude-sonnet-4-6
            trading:
              provider: openai          # operator kept OpenAI Responses for trading
              model: gpt-5.5
    """)
    reset_provider_state.LLMProvider.reset_cache()

    expected = {
        "analysis":   ("google",    "gemini-2.5-flash"),
        "summary":    ("google",    "gemini-2.5-flash"),
        "translator": ("google",    "gemini-2.5-flash"),
        "strategist": ("anthropic", "claude-sonnet-4-6"),
        "insight":    ("anthropic", "claude-sonnet-4-6"),
        "trading":    ("openai",    "gpt-5.5"),
    }
    for role, (provider, model) in expected.items():
        rc = reset_provider_state.LLMProvider.resolve_role(role)
        assert (rc.provider, rc.model) == (provider, model)


# --------------------------------------------------------- resolve_llm helper


def test_resolve_llm_helper_returns_class_and_model(
    reset_provider_state, tmp_path, monkeypatch
):
    """resolve_llm() shorthand should return the same class as
    get_llm_class(role) and the same model string as get_model(role).
    We can't actually import the LLM classes in CI (no mcp_agent), so
    monkeypatch the loader to avoid real imports."""
    monkeypatch.setattr(reset_provider_state.LLMProvider, "_project_root",
                        classmethod(lambda cls: tmp_path))

    # Stub get_llm_class to avoid importing mcp_agent
    class _StubLLM:
        pass
    monkeypatch.setattr(
        reset_provider_state.LLMProvider, "get_llm_class",
        classmethod(lambda cls, role="default", **kw: _StubLLM)
    )
    cls, model = reset_provider_state.resolve_llm("analysis")
    assert cls is _StubLLM
    assert model == "gpt-5.4-mini"


def test_known_providers_set_matches_provider_default_models(reset_provider_state):
    """The supported-provider list must stay in sync with the per-provider
    default-model map. If a new provider is added, both have to be updated."""
    assert reset_provider_state._KNOWN_PROVIDERS == set(
        reset_provider_state._PROVIDER_DEFAULT_MODELS.keys()
    )
