"""Prompt-rule regression tests for company_status_agent firecrawl resilience.

Locks the 4-rule resilience block introduced for issue #286 (firecrawl tool
result missing in 2.1 기업현황 section under batch load) so future prompt
edits can't silently drop the retry / fallback / fail-loud / status-marker
instructions.

Reads `cores/agents/company_info_agents.py` as source text rather than
importing it, so the test runs in CI without mcp_agent installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMPANY_INFO_AGENTS_SRC = (
    PROJECT_ROOT / "cores" / "agents" / "company_info_agents.py"
).read_text(encoding="utf-8")


def _slice_ko_block() -> str:
    """Return the Korean instruction body of create_company_status_agent."""
    src = COMPANY_INFO_AGENTS_SRC
    # The Korean block lives in the `else:` branch right after the English `if language == "en":`
    ko_marker = '당신은 기업 현황 분석 전문가입니다'
    en_marker = '기업: {company_name} ({company_code})'  # closes the Korean block
    start = src.index(ko_marker)
    end = src.index(en_marker, start)
    return src[start:end]


def _slice_en_block() -> str:
    src = COMPANY_INFO_AGENTS_SRC
    start = src.index('You are a company status analysis expert.')
    end = src.index('Company: {company_name} ({company_code})', start)
    return src[start:end]


@pytest.fixture(scope="module")
def ko_block() -> str:
    return _slice_ko_block()


@pytest.fixture(scope="module")
def en_block() -> str:
    return _slice_en_block()


def test_ko_resilience_block_lists_all_four_rules(ko_block):
    assert "도구 복원력 규칙" in ko_block
    assert "이슈 #286" in ko_block
    # Rule 1: retry on empty/error/short
    assert "재시도" in ko_block
    assert "500자 미만" in ko_block
    # Rule 2: firecrawl_search fallback
    assert "firecrawl_search" in ko_block
    # Rule 3: never silently omit
    assert "데이터 미수집 (도구 호출 실패)" in ko_block
    # Rule 4: status marker for grep
    assert "<!-- firecrawl_status:" in ko_block
    assert "ok | partial | failed" in ko_block


def test_en_resilience_block_lists_all_four_rules(en_block):
    assert "Tool Resilience Rules" in en_block
    assert "issue #286" in en_block
    assert "RETRY ONCE" in en_block
    assert "500 characters" in en_block
    assert "firecrawl_search" in en_block
    assert "NEVER silently omit" in en_block
    assert "데이터 미수집 (도구 호출 실패)" in en_block
    assert "<!-- firecrawl_status:" in en_block


def test_status_marker_uses_html_comment_form(ko_block, en_block):
    """The marker must be an HTML comment so it doesn't render in PDFs but is
    grep-able by ops scripts. If the form changes, grep/alerts break."""
    expected = "<!-- firecrawl_status: ok | partial | failed -->"
    assert expected in ko_block
    assert expected in en_block


def test_company_status_agent_still_declares_firecrawl_server():
    """Resilience rules are useless if the agent isn't actually wired to
    firecrawl. Guard against accidental server_names removal."""
    assert 'server_names=["firecrawl"]' in COMPANY_INFO_AGENTS_SRC


def test_example_config_extends_firecrawl_read_timeout():
    """The example config must show `read_timeout_seconds` on firecrawl so
    operators copy-pasting kis_devlp.yaml.example don't regress to the
    short default that surfaces issue #286 under batch load."""
    config_text = (PROJECT_ROOT / "mcp_agent.config.yaml.example").read_text(encoding="utf-8")
    # Find the firecrawl block (next 8 lines) and assert read_timeout_seconds appears in it.
    fc_idx = config_text.index("firecrawl:")
    block = config_text[fc_idx:fc_idx + 600]
    assert "read_timeout_seconds" in block, (
        "firecrawl entry in mcp_agent.config.yaml.example should declare "
        "read_timeout_seconds to match the resilience guidance"
    )
