"""Regression tests for issue #282 — block same-day re-buy after sell.

`tracking.helpers.was_sold_today` and `prism-us/tracking/db_schema.was_us_ticker_sold_today`
must return True when a sell row exists in *_trading_history with today's
date (substr(sell_date,1,10)) for the given account, False otherwise.
Per-account scoping keeps cross-account holdings independent.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def kr_helpers():
    """Load tracking/helpers.py directly to skip side-effecting __init__."""
    return _load("_kr_helpers", REPO_ROOT / "tracking" / "helpers.py")


@pytest.fixture
def us_db():
    return _load("_us_db_schema", REPO_ROOT / "prism-us" / "tracking" / "db_schema.py")


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        """
        CREATE TABLE stock_holdings (ticker TEXT, account_key TEXT);
        CREATE TABLE trading_history (
            ticker TEXT, account_key TEXT, sell_date TEXT
        );
        CREATE TABLE us_trading_history (
            ticker TEXT, account_key TEXT, sell_date TEXT
        );
        """
    )
    yield c
    c.close()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _yesterday() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")


def test_kr_was_sold_today_returns_true_when_sold_today(conn, kr_helpers):
    conn.execute(
        "INSERT INTO trading_history VALUES (?,?,?)",
        ("005930", "vps:50000000:01", _today()),
    )
    cur = conn.cursor()
    assert kr_helpers.was_sold_today(cur, "005930", account_key="vps:50000000:01") is True


def test_kr_was_sold_today_returns_false_for_yesterday(conn, kr_helpers):
    conn.execute(
        "INSERT INTO trading_history VALUES (?,?,?)",
        ("000660", "vps:50000000:01", _yesterday()),
    )
    cur = conn.cursor()
    assert kr_helpers.was_sold_today(cur, "000660", account_key="vps:50000000:01") is False


def test_kr_was_sold_today_is_account_scoped(conn, kr_helpers):
    """A sell on account A must NOT block a buy on account B."""
    conn.execute(
        "INSERT INTO trading_history VALUES (?,?,?)",
        ("035720", "vps:50000001:01", _today()),
    )
    cur = conn.cursor()
    assert kr_helpers.was_sold_today(cur, "035720", account_key="vps:50000000:01") is False
    assert kr_helpers.was_sold_today(cur, "035720", account_key="vps:50000001:01") is True


def test_kr_was_sold_today_without_account_filter_is_global(conn, kr_helpers):
    conn.execute(
        "INSERT INTO trading_history VALUES (?,?,?)",
        ("005930", "vps:50000000:01", _today()),
    )
    cur = conn.cursor()
    assert kr_helpers.was_sold_today(cur, "005930") is True
    assert kr_helpers.was_sold_today(cur, "999999") is False


def test_us_was_sold_today_mirrors_kr_semantics(conn, us_db):
    conn.executemany(
        "INSERT INTO us_trading_history VALUES (?,?,?)",
        [
            ("AAPL", "prod:50000000:01", _today()),
            ("TSLA", "prod:50000000:01", _yesterday()),
            ("MSFT", "prod:50000001:01", _today()),
        ],
    )
    cur = conn.cursor()
    assert us_db.was_us_ticker_sold_today(cur, "AAPL", account_key="prod:50000000:01") is True
    assert us_db.was_us_ticker_sold_today(cur, "TSLA", account_key="prod:50000000:01") is False
    assert us_db.was_us_ticker_sold_today(cur, "MSFT", account_key="prod:50000000:01") is False
    assert us_db.was_us_ticker_sold_today(cur, "MSFT", account_key="prod:50000001:01") is True
    assert us_db.was_us_ticker_sold_today(cur, "NVDA", account_key="prod:50000000:01") is False


def test_helper_returns_false_when_table_missing(kr_helpers, us_db):
    """If the trading_history table doesn't exist, the guard fails open
    (logs an error but doesn't crash the buy path). Documenting this so
    future schema migrations don't accidentally turn the guard into a
    blocker for fresh installs."""
    c = sqlite3.connect(":memory:")
    cur = c.cursor()
    assert kr_helpers.was_sold_today(cur, "005930", account_key="vps:50000000:01") is False
    assert us_db.was_us_ticker_sold_today(cur, "AAPL", account_key="prod:50000000:01") is False
    c.close()
