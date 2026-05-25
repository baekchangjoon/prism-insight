"""Regression tests for issue #281 — AI-bullish-but-blocked-by-guard callout.

When the AI's `buy_score >= min_score` and `decision == "Enter"` (KR) or
`"entry"` (US) but a portfolio-side constraint (sector concentration / max
slots) blocks auto-execution, the skip message must now include an explicit
"AI는 매수를 추천했지만 포트폴리오 가드로 자동 매매가 보류되었습니다" call-out so the
user doesn't dismiss the message as "AI said no".

Reads tracking-agent source files as text (no mcp_agent import needed,
CI-friendly).
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KR_AGENT_SRC = (PROJECT_ROOT / "stock_tracking_enhanced_agent.py").read_text(encoding="utf-8")
US_AGENT_SRC = (
    PROJECT_ROOT / "prism-us" / "us_stock_tracking_agent.py"
).read_text(encoding="utf-8")


CALLOUT_HEAD = "AI는 매수를 추천했지만 포트폴리오 가드로 자동 매매가 보류되었습니다"
CALLOUT_GUIDE = "직접 검토 후 수동 매수를 고려"


def test_kr_skip_message_has_callout_for_ai_bullish_blocked_by_guard():
    assert CALLOUT_HEAD in KR_AGENT_SRC
    assert CALLOUT_GUIDE in KR_AGENT_SRC
    # Detection condition: AI Enter + sufficient score + sector_diverse False
    assert "ai_recommended_blocked_by_guard" in KR_AGENT_SRC
    assert 'decision == "Enter"' in KR_AGENT_SRC
    assert "buy_score >= min_score" in KR_AGENT_SRC
    assert "not sector_diverse" in KR_AGENT_SRC


def test_us_skip_message_has_callout_for_ai_bullish_blocked_by_guard():
    assert CALLOUT_HEAD in US_AGENT_SRC
    assert CALLOUT_GUIDE in US_AGENT_SRC
    # US derives from scenario.decision string match — guard against accidental
    # removal of the lower-case 'entry' check
    assert "ai_recommended_blocked_by_guard" in US_AGENT_SRC
    assert 'scenario_decision == "entry"' in US_AGENT_SRC
    # Reason-string keyword used for US detection
    assert '"sector concentration"' in US_AGENT_SRC


def test_kr_callout_only_emits_when_all_three_conditions_true():
    """The callout must be guarded so it doesn't fire when AI already said
    Skip or score was insufficient — both cases mean the AI itself was the
    blocker, not the portfolio guard."""
    # The conjunction must appear all together — verifies that future edits
    # don't drop one of the three predicates and weaken the gate.
    idx = KR_AGENT_SRC.index("ai_recommended_blocked_by_guard = (")
    block = KR_AGENT_SRC[idx:idx + 400]
    assert 'decision == "Enter"' in block
    assert "buy_score >= min_score" in block
    assert "not sector_diverse" in block


def test_kr_callout_appears_before_분석_의견_line():
    """The callout must be placed before '분석 의견' so it reads as a
    distinct guidance section, not buried at the bottom of the rationale."""
    # Find the callout text and verify the rationale field comes AFTER it.
    callout_idx = KR_AGENT_SRC.index(CALLOUT_HEAD)
    rationale_idx = KR_AGENT_SRC.index('"분석 의견: {scenario.get', callout_idx)
    assert rationale_idx > callout_idx, (
        "분석 의견 line must come after the AI-bullish callout in skip_message construction"
    )
