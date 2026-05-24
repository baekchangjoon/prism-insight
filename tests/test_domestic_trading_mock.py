"""Integration tests for DomesticStockTrading against the in-process mock KIS server.

End-to-end coverage: price quote → buy → portfolio → sell, all hitting the
FastAPI mock instead of the real KIS endpoint.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("pandas")  # kis_auth imports pandas eagerly
pytest.importorskip("yaml")
pytest.importorskip("Crypto")  # pycryptodome
pytest.importorskip("cryptography")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "trading"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    import uvicorn
    from tests import mock_kis_server as mks

    port = _free_port()
    os.environ["KIS_ENV"] = "mock"
    os.environ["KIS_MOCK_URL"] = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(mks.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mock-kis-trading", daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock KIS server failed to start")

    yield {"url": f"http://127.0.0.1:{port}", "state": mks.STATE}

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_state(mock_server):
    mock_server["state"].reset()
    yield
    mock_server["state"].reset()


@pytest.fixture
def trader(mock_server, tmp_path, monkeypatch):
    """Build a DomesticStockTrading instance that points at the mock server."""
    # Clear any cached modules so fresh tmp config_root takes effect.
    for mod in ("kis_auth", "domestic_stock_trading"):
        sys.modules.pop(mod, None)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    import kis_auth as ka
    monkeypatch.setattr(ka, "config_root", str(cfg_dir))
    monkeypatch.setattr(ka, "token_tmp", str(cfg_dir / "KIS_test"))

    from domestic_stock_trading import DomesticStockTrading
    return DomesticStockTrading(mode="demo", buy_amount=10_000_000, auto_trading=True)


def test_get_current_price_returns_mock_payload(trader):
    info = trader.get_current_price("005930")
    assert info is not None
    assert info["stock_code"] == "005930"
    assert info["stock_name"] == "MOCK_005930"
    assert info["current_price"] > 0


def test_buy_then_portfolio_then_sell_full_round_trip(trader):
    code = "005930"

    buy_result = trader.buy_market_price(code, buy_amount=10_000_000)
    assert buy_result["success"], buy_result["message"]
    assert buy_result["quantity"] > 0
    bought_qty = buy_result["quantity"]

    portfolio = trader.get_portfolio()
    assert any(s["stock_code"] == code and s["quantity"] == bought_qty for s in portfolio)

    sell_result = trader.sell_all_market_price(code)
    assert sell_result["success"], sell_result["message"]
    assert sell_result["quantity"] == bought_qty

    after = trader.get_portfolio()
    assert all(s["stock_code"] != code for s in after if s["quantity"] > 0)


def test_account_summary_includes_initial_cash(trader, mock_server):
    summary = trader.get_account_summary()
    assert summary is not None
    # Initial cash is 50M KRW in the mock; total cash should match (no holdings yet).
    assert summary["total_cash"] >= 49_000_000
    assert summary["available_amount"] >= 49_000_000


def test_async_buy_via_context_manager(trader):
    code = "035720"

    async def run():
        return await trader.async_buy_stock(code, buy_amount=1_000_000, timeout=10.0)

    result = asyncio.run(run())
    assert result["success"], result["message"]
    assert result["quantity"] > 0
    assert result["order_no"]
