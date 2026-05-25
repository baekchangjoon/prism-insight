"""Regression tests for issue #280 — trading-journal feedback-loop transparency.

`JournalManager.get_provenance_for_ticker` returns counts + IDs of journal
artifacts that feed into a buy decision. `format_provenance_one_liner` turns
that into a Telegram-friendly summary so the user sees the feedback loop
instead of treating it as a black box.

Tests cover:
- enabled vs disabled JournalManager (empty provenance when disabled)
- principle filter mirrors get_universal_principles (is_active=1, scope=universal, supporting_trades>=2, top 5)
- same-stock journal filter mirrors get_context_for_ticker (top 3 by trade_date)
- intuition filter (is_active=1, top 10 by confidence)
- one-liner format includes all non-zero counts and omits zero counts
- one-liner empty when disabled / all-zero
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_journal_module():
    """Load tracking/journal.py directly to bypass tracking/__init__'s heavy imports."""
    # Pre-create logger stub so module-load doesn't require full stdlib chain
    spec = importlib.util.spec_from_file_location(
        "_journal_module", PROJECT_ROOT / "tracking" / "journal.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def journal_mod():
    return _load_journal_module()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE trading_principles (
            id INTEGER PRIMARY KEY,
            scope TEXT, condition TEXT, action TEXT, reason TEXT,
            priority TEXT, confidence REAL, supporting_trades INTEGER,
            is_active INTEGER, source_journal_id INTEGER
        );
        CREATE TABLE trading_journal (
            id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT,
            profit_rate REAL, holding_days INTEGER, one_line_summary TEXT,
            lessons TEXT, pattern_tags TEXT, trade_date TEXT,
            sell_reason TEXT, situation_analysis TEXT, judgment_evaluation TEXT
        );
        CREATE TABLE trading_intuitions (
            id INTEGER PRIMARY KEY, category TEXT, condition TEXT,
            insight TEXT, confidence REAL, is_active INTEGER
        );
        """
    )
    yield c
    c.close()


def _make_manager(journal_mod, conn, enabled: bool = True):
    """Construct a JournalManager that directly binds to our in-memory connection."""
    m = journal_mod.JournalManager.__new__(journal_mod.JournalManager)
    m.conn = conn
    m.cursor = conn.cursor()
    m.enable_journal = enabled
    m.language = "ko"
    return m


def test_provenance_returns_empty_when_journal_disabled(journal_mod, conn):
    m = _make_manager(journal_mod, conn, enabled=False)
    p = m.get_provenance_for_ticker("005930")
    assert p["enabled"] is False
    assert p["principle_count"] == 0
    assert p["same_stock_journal_count"] == 0
    assert p["intuition_count"] == 0
    assert p["principle_ids"] == []


def test_provenance_returns_zero_counts_on_empty_db(journal_mod, conn):
    m = _make_manager(journal_mod, conn, enabled=True)
    p = m.get_provenance_for_ticker("005930")
    assert p["enabled"] is True
    assert p == {
        "enabled": True,
        "principle_count": 0, "principle_ids": [],
        "same_stock_journal_count": 0, "same_stock_journal_ids": [],
        "intuition_count": 0, "intuition_ids": [],
    }


def test_provenance_principle_filter_matches_universal_principles_query(journal_mod, conn):
    """Active universal principles with supporting_trades >= 2 only, top 5."""
    rows = [
        # (scope, supporting_trades, is_active, priority, confidence) — expected to be picked or not
        ("universal", 5, 1, "high", 0.9),     # pick
        ("universal", 3, 1, "medium", 0.8),   # pick
        ("universal", 1, 1, "high", 0.9),     # drop: supporting_trades < 2
        ("sector", 5, 1, "high", 0.9),        # drop: scope != universal
        ("universal", 5, 0, "high", 0.9),     # drop: is_active=0
    ]
    for scope, st, active, pr, conf in rows:
        conn.execute(
            "INSERT INTO trading_principles "
            "(scope, condition, action, reason, priority, confidence, supporting_trades, is_active) "
            "VALUES (?, 'cond', 'act', 'rea', ?, ?, ?, ?)",
            (scope, pr, conf, st, active)
        )
    m = _make_manager(journal_mod, conn, enabled=True)
    p = m.get_provenance_for_ticker("005930")
    assert p["principle_count"] == 2


def test_provenance_same_stock_journal_filter_only_matching_ticker(journal_mod, conn):
    """Same-stock journal IDs come from trading_journal WHERE ticker = ?, LIMIT 3."""
    now = datetime.now()
    for i, (ticker, days_ago) in enumerate([
        ("005930", 1), ("005930", 2), ("005930", 3), ("005930", 4),  # one over the limit
        ("000660", 1),  # different ticker
    ]):
        conn.execute(
            "INSERT INTO trading_journal "
            "(ticker, company_name, profit_rate, holding_days, one_line_summary, "
            "lessons, pattern_tags, trade_date) VALUES (?,?,?,?,?,?,?,?)",
            (ticker, "name", 1.0, 5, "summary", "[]", "tags",
             (now - timedelta(days=days_ago)).isoformat())
        )
    m = _make_manager(journal_mod, conn, enabled=True)
    p = m.get_provenance_for_ticker("005930")
    assert p["same_stock_journal_count"] == 3  # capped at 3, all 005930
    p2 = m.get_provenance_for_ticker("000660")
    assert p2["same_stock_journal_count"] == 1


def test_provenance_intuitions_filter_active_only(journal_mod, conn):
    for active, conf in [(1, 0.9), (1, 0.7), (0, 0.95), (1, 0.5)]:
        conn.execute(
            "INSERT INTO trading_intuitions (category, condition, insight, confidence, is_active) "
            "VALUES ('cat', 'cond', 'ins', ?, ?)",
            (conf, active)
        )
    m = _make_manager(journal_mod, conn, enabled=True)
    p = m.get_provenance_for_ticker("005930")
    assert p["intuition_count"] == 3  # only is_active=1


def test_format_provenance_one_liner_includes_only_nonzero(journal_mod):
    fmt = journal_mod.JournalManager.format_provenance_one_liner
    assert fmt({"enabled": False}) == ""
    assert fmt({"enabled": True, "principle_count": 0,
                "same_stock_journal_count": 0, "intuition_count": 0}) == ""
    # only principles
    out = fmt({"enabled": True, "principle_count": 5,
               "same_stock_journal_count": 0, "intuition_count": 0})
    assert "누적 원칙 5개" in out
    # The header contains '매매일지' so we check the entry form ('같은 종목 일지')
    assert "같은 종목 일지" not in out
    assert "직관" not in out
    # all three
    out = fmt({"enabled": True, "principle_count": 5,
               "same_stock_journal_count": 2, "intuition_count": 7})
    assert "누적 원칙 5개" in out
    assert "같은 종목 일지 2개" in out
    assert "직관 7개" in out
    assert "📚 매매일지 참조" in out
