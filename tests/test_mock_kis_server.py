"""Unit tests for the in-memory Mock KIS server (tests/mock_kis_server.py).

Uses FastAPI's TestClient — no real network or threading needed.
"""
from __future__ import annotations

import os
import sys

import pytest

# Ensure the project root is on sys.path so `tests.mock_kis_server` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("fastapi", reason="fastapi not installed — pip install -r requirements.txt")

from fastapi.testclient import TestClient  # noqa: E402

from tests import mock_kis_server as mks  # noqa: E402


@pytest.fixture
def client():
    mks.STATE.reset()
    with TestClient(mks.app) as c:
        yield c
    mks.STATE.reset()


# ---------- token ----------


def test_oauth_token_issues_unique_tokens(client):
    r1 = client.post("/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": "k", "appsecret": "s"})
    r2 = client.post("/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": "k", "appsecret": "s"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    t1 = r1.json()["access_token"]
    t2 = r2.json()["access_token"]
    assert t1.startswith(mks.MOCK_TOKEN_PREFIX)
    assert t1 != t2  # counter increments per request


def test_oauth_token_missing_creds_fails(client):
    r = client.post("/oauth2/tokenP", json={"grant_type": "client_credentials"})
    assert r.status_code == 400


def test_ws_approval(client):
    r = client.post("/oauth2/Approval", json={"grant_type": "client_credentials", "appkey": "k", "secretkey": "s"})
    assert r.status_code == 200
    assert "approval_key" in r.json()


# ---------- inquire-price ----------


def test_inquire_price_deterministic_base(client):
    """Base price is seeded by stock code → same code yields the same baseline."""
    code = "005930"
    base = mks.base_price(code)
    r = client.get("/uapi/domestic-stock/v1/quotations/inquire-price", params={
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": code,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["rt_cd"] == "0"
    cur = int(body["output"]["stck_prpr"])
    # current price is within ±0.5% of base
    assert 0.99 * base <= cur <= 1.01 * base
    assert body["output"]["rprs_mrkt_kor_name"] == f"MOCK_{code}"


# ---------- order-cash ----------


def _order_payload(stock="005930", qty=10, price=0, dvsn="01"):
    return {
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": stock,
        "ORD_DVSN": dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": str(price),
    }


def test_buy_market_decreases_cash_and_creates_holding(client):
    payload = _order_payload(qty=10)
    r = client.post("/uapi/domestic-stock/v1/trading/order-cash", json=payload, headers={"tr_id": "VTTC0012U"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rt_cd"] == "0"
    assert body["output"]["odno"]

    state = client.get("/__mock__/state", params={"account_key": "50000000:01"}).json()
    assert state["cash"] < mks.INITIAL_CASH_KRW
    assert "005930" in state["holdings"]
    assert state["holdings"]["005930"]["qty"] == 10


def test_buy_insufficient_cash_returns_error(client):
    # Way more than the initial 50M KRW can buy.
    payload = _order_payload(qty=10_000_000)
    r = client.post("/uapi/domestic-stock/v1/trading/order-cash", json=payload, headers={"tr_id": "VTTC0012U"})
    assert r.status_code == 200  # KIS returns 200 with rt_cd=1 for business errors
    body = r.json()
    assert body["rt_cd"] == "1"
    assert "insufficient" in body["msg1"].lower()


def test_sell_without_holding_fails(client):
    payload = _order_payload(qty=5)
    r = client.post("/uapi/domestic-stock/v1/trading/order-cash", json=payload, headers={"tr_id": "VTTC0011U"})
    assert r.json()["rt_cd"] == "1"


def test_buy_then_sell_round_trip(client):
    code = "005930"
    # buy 10
    client.post("/uapi/domestic-stock/v1/trading/order-cash",
                json=_order_payload(stock=code, qty=10),
                headers={"tr_id": "VTTC0012U"})
    # sell 7
    r_sell = client.post("/uapi/domestic-stock/v1/trading/order-cash",
                         json=_order_payload(stock=code, qty=7),
                         headers={"tr_id": "VTTC0011U"})
    assert r_sell.json()["rt_cd"] == "0"

    state = client.get("/__mock__/state", params={"account_key": "50000000:01"}).json()
    assert state["holdings"][code]["qty"] == 3
    assert state["order_count"] == 2


def test_order_cash_rejects_unknown_tr_id(client):
    r = client.post("/uapi/domestic-stock/v1/trading/order-cash",
                    json=_order_payload(),
                    headers={"tr_id": "ZZZZ9999U"})
    assert r.json()["rt_cd"] == "1"
    assert "unsupported tr_id" in r.json()["msg1"]


# ---------- inquire-balance ----------


def test_inquire_balance_empty_account(client):
    r = client.get("/uapi/domestic-stock/v1/trading/inquire-balance", params={
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["rt_cd"] == "0"
    assert body["output1"] == []
    assert int(float(body["output2"][0]["dnca_tot_amt"])) == mks.INITIAL_CASH_KRW


def test_inquire_balance_reflects_buy(client):
    code = "005930"
    client.post("/uapi/domestic-stock/v1/trading/order-cash",
                json=_order_payload(stock=code, qty=10),
                headers={"tr_id": "VTTC0012U"})

    r = client.get("/uapi/domestic-stock/v1/trading/inquire-balance", params={
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
    })
    body = r.json()
    assert len(body["output1"]) == 1
    assert body["output1"][0]["pdno"] == code
    assert int(body["output1"][0]["hldg_qty"]) == 10


# ---------- reserved orders ----------


def test_reserved_order_fills_buy(client):
    payload = {
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": "005930",
        "ORD_QTY": "5",
        "ORD_UNPR": "1000",
        "SLL_BUY_DVSN_CD": "02",  # BUY
        "ORD_DVSN_CD": "00",      # limit
        "ORD_OBJT_CBLC_DVSN_CD": "10",
    }
    r = client.post("/uapi/domestic-stock/v1/trading/order-resv", json=payload, headers={"tr_id": "CTSC0008U"})
    body = r.json()
    assert body["rt_cd"] == "0"
    assert body["output"]["RSVN_ORD_SEQ"]


# ---------- inquire-psbl-order ----------


def test_inquire_psbl_order_uses_current_price_when_unpr_zero(client):
    code = "005930"
    base = mks.base_price(code)
    r = client.get("/uapi/domestic-stock/v1/trading/inquire-psbl-order", params={
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": code,
        "ORD_UNPR": "0",
    })
    body = r.json()
    assert body["rt_cd"] == "0"
    max_qty = int(body["output"]["max_buy_qty"])
    # Rough sanity: should be cash // ~base
    assert max_qty > 0
    assert max_qty * base * 0.99 <= mks.INITIAL_CASH_KRW * 1.01


# ---------- daily-ccld ----------


def test_inquire_daily_ccld_returns_today_orders(client):
    client.post("/uapi/domestic-stock/v1/trading/order-cash",
                json=_order_payload(qty=3),
                headers={"tr_id": "VTTC0012U"})
    r = client.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld", params={
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
    })
    body = r.json()
    assert body["rt_cd"] == "0"
    assert len(body["output1"]) == 1
    assert body["output1"][0]["pdno"] == "005930"


# ---------- admin endpoints ----------


def test_admin_seed_holding(client):
    code = "035720"
    client.post("/__mock__/seed_holding", json={
        "CANO": "50000000",
        "ACNT_PRDT_CD": "01",
        "PDNO": code,
        "qty": 42,
        "avg_price": 12345,
    })
    state = client.get("/__mock__/state", params={"account_key": "50000000:01"}).json()
    assert state["holdings"][code]["qty"] == 42
    assert state["holdings"][code]["avg_price"] == 12345


def test_admin_reset_clears_state(client):
    client.post("/uapi/domestic-stock/v1/trading/order-cash",
                json=_order_payload(qty=1),
                headers={"tr_id": "VTTC0012U"})
    client.post("/__mock__/reset")
    state = client.get("/__mock__/state").json()
    assert state["accounts"] == []
