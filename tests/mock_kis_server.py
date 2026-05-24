"""
Mock KIS Open API server (FastAPI)

Implements the subset of KIS REST endpoints needed by prism-insight to run
end-to-end without a real KIS account. The state is in-memory and
deterministic (price seeded from stock code), so tests are reproducible.

Routing: the production client picks this server up when KIS_ENV=mock is set
(see trading/kis_auth.py::_resolve_svr_url).

Run standalone:
    uvicorn tests.mock_kis_server:app --port 8000
or
    python -m tests.mock_kis_server
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("mock_kis")

# -----------------------------------------------------------------------------
# Constants & response helpers
# -----------------------------------------------------------------------------

INITIAL_CASH_KRW = 50_000_000
TOKEN_TTL_SECONDS = 24 * 60 * 60
MOCK_TOKEN_PREFIX = "mock-access-"

OK_BODY = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리되었습니다."}
ERR_BODY = {"rt_cd": "1", "msg_cd": "MCA99999", "msg1": "오류가 발생했습니다."}


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {**OK_BODY, **payload}


def _err(msg_cd: str, msg1: str) -> Dict[str, Any]:
    return {"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1, "output": {}}


# -----------------------------------------------------------------------------
# Pricing model — deterministic per stock_code
# -----------------------------------------------------------------------------

_BASE_PRICE_CACHE: Dict[str, int] = {}


def base_price(stock_code: str) -> int:
    """Deterministic baseline price for a stock code (seeded by hash)."""
    if stock_code in _BASE_PRICE_CACHE:
        return _BASE_PRICE_CACHE[stock_code]
    digest = hashlib.md5(stock_code.encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)
    # 1,000 ~ 200,000 KRW range
    price = 1000 + (seed % 199_000)
    # snap to 10 KRW grid for realism
    price = (price // 10) * 10
    _BASE_PRICE_CACHE[stock_code] = price
    return price


def jittered_price(stock_code: str) -> int:
    """Current price = base ± up to 0.5%, deterministic per call within a session."""
    base = base_price(stock_code)
    # Use a fresh random per call so the mock looks "live", but capped at ±0.5%.
    delta_pct = random.uniform(-0.005, 0.005)
    price = int(base * (1 + delta_pct))
    # snap to 10 KRW grid
    return max(10, (price // 10) * 10)


# -----------------------------------------------------------------------------
# In-memory state machine
# -----------------------------------------------------------------------------


@dataclass
class Holding:
    quantity: int
    avg_price: float

    def buy(self, qty: int, price: float) -> None:
        new_qty = self.quantity + qty
        if new_qty <= 0:
            self.quantity = 0
            self.avg_price = 0.0
            return
        self.avg_price = ((self.avg_price * self.quantity) + (price * qty)) / new_qty
        self.quantity = new_qty

    def sell(self, qty: int) -> int:
        sold = min(qty, self.quantity)
        self.quantity -= sold
        if self.quantity == 0:
            self.avg_price = 0.0
        return sold


@dataclass
class OrderRecord:
    order_no: str
    account: str
    product: str
    stock_code: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: int
    tr_id: str
    timestamp: datetime
    status: str = "FILLED"


@dataclass
class AccountBook:
    cash: int = INITIAL_CASH_KRW
    holdings: Dict[str, Holding] = field(default_factory=dict)
    orders: List[OrderRecord] = field(default_factory=list)
    order_counter: int = 0

    def next_order_no(self) -> str:
        self.order_counter += 1
        return f"{datetime.now():%Y%m%d}{self.order_counter:06d}"


class MockKISState:
    """In-memory state for all mock accounts. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: Dict[str, AccountBook] = defaultdict(AccountBook)
        self._tokens: Dict[str, datetime] = {}
        self._token_counter = 0

    # ----- accounts -----
    def book(self, account_key: str) -> AccountBook:
        with self._lock:
            return self._accounts[account_key]

    def reset(self) -> None:
        with self._lock:
            self._accounts.clear()
            self._tokens.clear()
            self._token_counter = 0
            _BASE_PRICE_CACHE.clear()

    # ----- tokens -----
    def issue_token(self) -> Dict[str, str]:
        with self._lock:
            self._token_counter += 1
            token = f"{MOCK_TOKEN_PREFIX}{self._token_counter:08d}"
            expiry = datetime.now() + timedelta(seconds=TOKEN_TTL_SECONDS)
            self._tokens[token] = expiry
        return {
            "access_token": token,
            "access_token_token_expired": expiry.strftime("%Y-%m-%d %H:%M:%S"),
            "token_type": "Bearer",
            "expires_in": TOKEN_TTL_SECONDS,
        }

    def issue_approval_key(self) -> Dict[str, str]:
        with self._lock:
            self._token_counter += 1
            key = f"mock-ws-approval-{self._token_counter:08d}"
        return {"approval_key": key}


STATE = MockKISState()


def _account_key(cano: str, prdt: str) -> str:
    return f"{cano}:{prdt}"


# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------

app = FastAPI(
    title="Mock KIS Open API",
    description="In-memory KIS API for prism-insight development & tests.",
    version="0.1.0",
)


@app.on_event("startup")
def _on_startup() -> None:
    logger.info("Mock KIS server ready — initial cash %d KRW per account", INITIAL_CASH_KRW)


# ---------- OAuth ----------


class TokenRequest(BaseModel):
    grant_type: str = "client_credentials"
    appkey: Optional[str] = None
    appsecret: Optional[str] = None
    secretkey: Optional[str] = None  # WebSocket approval uses this name


@app.post("/oauth2/tokenP")
def oauth_token(req: TokenRequest) -> Dict[str, Any]:
    if not req.appkey or not (req.appsecret or req.secretkey):
        raise HTTPException(status_code=400, detail="missing appkey/appsecret")
    return STATE.issue_token()


@app.post("/oauth2/Approval")
def ws_approval(req: TokenRequest) -> Dict[str, Any]:
    if not req.appkey or not (req.appsecret or req.secretkey):
        raise HTTPException(status_code=400, detail="missing appkey/secretkey")
    return STATE.issue_approval_key()


# ---------- Quotations ----------


@app.get("/uapi/domestic-stock/v1/quotations/inquire-price")
def inquire_price(
    fid_cond_mrkt_div_code: str = Query("J", alias="fid_cond_mrkt_div_code"),
    fid_input_iscd: str = Query(..., alias="fid_input_iscd"),
) -> Dict[str, Any]:
    code = fid_input_iscd
    cur = jittered_price(code)
    base = base_price(code)
    ctrt = round((cur - base) / base * 100, 2) if base else 0.0
    output = {
        "rprs_mrkt_kor_name": f"MOCK_{code}",
        "stck_prpr": str(cur),
        "prdy_vrss": str(cur - base),
        "prdy_ctrt": f"{ctrt:.2f}",
        "acml_vol": str(random.randint(10_000, 5_000_000)),
        "stck_oprc": str(base),
        "stck_hgpr": str(int(base * 1.02)),
        "stck_lwpr": str(int(base * 0.98)),
    }
    return _ok({"output": output})


@app.get("/uapi/domestic-stock/v1/quotations/inquire-daily-price")
def inquire_daily_price(
    fid_cond_mrkt_div_code: str = Query("J", alias="fid_cond_mrkt_div_code"),
    fid_input_iscd: str = Query(..., alias="fid_input_iscd"),
    fid_period_div_code: str = Query("D", alias="fid_period_div_code"),
    fid_org_adj_prc: str = Query("0", alias="fid_org_adj_prc"),
) -> Dict[str, Any]:
    code = fid_input_iscd
    base = base_price(code)
    # 60-day random walk from base (deterministic per code).
    rng = random.Random(int(hashlib.md5(code.encode()).hexdigest()[:8], 16))
    rows = []
    px = float(base)
    today = datetime.now()
    for i in range(60):
        date = today - timedelta(days=i)
        change = rng.uniform(-0.03, 0.03)
        px = max(10.0, px * (1 + change))
        rows.append({
            "stck_bsop_date": date.strftime("%Y%m%d"),
            "stck_oprc": str(int(px * 0.995)),
            "stck_hgpr": str(int(px * 1.01)),
            "stck_lwpr": str(int(px * 0.99)),
            "stck_clpr": str(int(px)),
            "acml_vol": str(rng.randint(10_000, 1_000_000)),
        })
    return _ok({"output": rows})


# ---------- Trading: order-cash ----------


class OrderCashRequest(BaseModel):
    CANO: str
    ACNT_PRDT_CD: str
    PDNO: str
    ORD_DVSN: str
    ORD_QTY: str
    ORD_UNPR: str = "0"
    EXCG_ID_DVSN_CD: Optional[str] = None
    SLL_TYPE: Optional[str] = None
    CNDT_PRIC: Optional[str] = None


BUY_TR_IDS = {"TTTC0012U", "VTTC0012U"}
SELL_TR_IDS = {"TTTC0011U", "VTTC0011U"}


@app.post("/uapi/domestic-stock/v1/trading/order-cash")
def order_cash(
    body: OrderCashRequest,
    tr_id: str = Header(..., alias="tr_id"),
) -> Dict[str, Any]:
    if tr_id not in BUY_TR_IDS | SELL_TR_IDS:
        return _err("MCA00001", f"unsupported tr_id={tr_id}")

    side = "BUY" if tr_id in BUY_TR_IDS else "SELL"
    qty = int(body.ORD_QTY)
    if qty <= 0:
        return _err("APBK0918", "quantity must be > 0")

    # Resolve fill price: ORD_UNPR > 0 → limit, else current market price.
    unit_price = int(body.ORD_UNPR or 0)
    fill_price = unit_price if unit_price > 0 else jittered_price(body.PDNO)

    book = STATE.book(_account_key(body.CANO, body.ACNT_PRDT_CD))
    with STATE._lock:
        if side == "BUY":
            total = qty * fill_price
            if total > book.cash:
                return _err("APBK0552", "insufficient cash")
            book.cash -= total
            holding = book.holdings.get(body.PDNO) or Holding(0, 0.0)
            holding.buy(qty, float(fill_price))
            book.holdings[body.PDNO] = holding
        else:  # SELL
            holding = book.holdings.get(body.PDNO)
            if not holding or holding.quantity <= 0:
                return _err("APBK0550", "no holding to sell")
            if qty > holding.quantity:
                return _err("APBK0551", f"qty {qty} exceeds holding {holding.quantity}")
            holding.sell(qty)
            book.cash += qty * fill_price
            if holding.quantity == 0:
                # keep entry to allow zero-qty reads
                pass

        order_no = book.next_order_no()
        book.orders.append(OrderRecord(
            order_no=order_no,
            account=body.CANO,
            product=body.ACNT_PRDT_CD,
            stock_code=body.PDNO,
            side=side,
            quantity=qty,
            price=fill_price,
            tr_id=tr_id,
            timestamp=datetime.now(),
        ))

    return _ok({
        "output": {
            "KRX_FWDG_ORD_ORGNO": "00950",
            "odno": order_no,
            "ord_tmd": datetime.now().strftime("%H%M%S"),
        }
    })


# ---------- Trading: reserved order ----------


class ReservedOrderRequest(BaseModel):
    CANO: str
    ACNT_PRDT_CD: str
    PDNO: str
    ORD_QTY: str
    ORD_UNPR: str
    SLL_BUY_DVSN_CD: str  # "01" SELL, "02" BUY
    ORD_DVSN_CD: str
    ORD_OBJT_CBLC_DVSN_CD: Optional[str] = None
    LOAN_DT: Optional[str] = None
    LDNG_DT: Optional[str] = None
    RSVN_ORD_END_DT: Optional[str] = None


@app.post("/uapi/domestic-stock/v1/trading/order-resv")
def order_reserved(body: ReservedOrderRequest, tr_id: str = Header(..., alias="tr_id")) -> Dict[str, Any]:
    side = "BUY" if body.SLL_BUY_DVSN_CD == "02" else "SELL"
    qty = int(body.ORD_QTY)
    if qty <= 0:
        return _err("APBK0918", "quantity must be > 0")

    # Reserved orders are "queued" — for the mock we fill them right away so
    # downstream code can be exercised end-to-end. Real KIS would defer until
    # 7:30 AM next trading day.
    unit_price = int(body.ORD_UNPR or 0)
    fill_price = unit_price if unit_price > 0 else jittered_price(body.PDNO)

    book = STATE.book(_account_key(body.CANO, body.ACNT_PRDT_CD))
    with STATE._lock:
        if side == "BUY":
            total = qty * fill_price
            if total > book.cash:
                return _err("APBK0552", "insufficient cash")
            book.cash -= total
            holding = book.holdings.get(body.PDNO) or Holding(0, 0.0)
            holding.buy(qty, float(fill_price))
            book.holdings[body.PDNO] = holding
        else:
            holding = book.holdings.get(body.PDNO)
            if not holding or holding.quantity <= 0:
                return _err("APBK0550", "no holding to sell")
            sold = holding.sell(min(qty, holding.quantity))
            book.cash += sold * fill_price

        seq = book.next_order_no()
        book.orders.append(OrderRecord(
            order_no=seq,
            account=body.CANO,
            product=body.ACNT_PRDT_CD,
            stock_code=body.PDNO,
            side=side,
            quantity=qty,
            price=fill_price,
            tr_id=tr_id,
            timestamp=datetime.now(),
            status="RESERVED-FILLED",
        ))

    return _ok({"output": {"RSVN_ORD_SEQ": seq}})


# ---------- Inquiries ----------


@app.get("/uapi/domestic-stock/v1/trading/inquire-balance")
def inquire_balance(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    output1 = []
    eval_total = 0.0
    purchase_total = 0.0
    for code, h in book.holdings.items():
        if h.quantity <= 0:
            continue
        cur = jittered_price(code)
        eval_amount = cur * h.quantity
        purchase_amount = h.avg_price * h.quantity
        profit = eval_amount - purchase_amount
        rate = (profit / purchase_amount * 100) if purchase_amount else 0.0
        eval_total += eval_amount
        purchase_total += purchase_amount
        output1.append({
            "pdno": code,
            "prdt_name": f"MOCK_{code}",
            "hldg_qty": str(h.quantity),
            "pchs_avg_pric": f"{h.avg_price:.2f}",
            "prpr": str(cur),
            "evlu_amt": f"{eval_amount:.0f}",
            "evlu_pfls_amt": f"{profit:.0f}",
            "evlu_pfls_rt": f"{rate:.2f}",
        })

    output2 = {
        "dnca_tot_amt": f"{book.cash:.0f}",
        "ord_psbl_cash": f"{book.cash:.0f}",
        "tot_evlu_amt": f"{book.cash + eval_total:.0f}",
        "scts_evlu_amt": f"{eval_total:.0f}",
        "pchs_amt_smtl_amt": f"{purchase_total:.0f}",
        "evlu_pfls_smtl_amt": f"{eval_total - purchase_total:.0f}",
    }
    body = {**OK_BODY, "output1": output1, "output2": [output2]}
    return body


@app.get("/uapi/domestic-stock/v1/trading/inquire-psbl-order")
def inquire_psbl_order(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
    PDNO: str = Query("", alias="PDNO"),
    ORD_UNPR: str = Query("0", alias="ORD_UNPR"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    unit_price = int(ORD_UNPR or 0)
    if unit_price <= 0 and PDNO:
        unit_price = jittered_price(PDNO)
    max_qty = (book.cash // unit_price) if unit_price > 0 else 0
    return _ok({
        "output": {
            "ord_psbl_cash": str(book.cash),
            "max_buy_qty": str(max_qty),
            "max_buy_amt": str(max_qty * unit_price),
        }
    })


@app.get("/uapi/domestic-stock/v1/trading/inquire-daily-ccld")
def inquire_daily_ccld(
    CANO: str = Query(..., alias="CANO"),
    ACNT_PRDT_CD: str = Query(..., alias="ACNT_PRDT_CD"),
) -> Dict[str, Any]:
    book = STATE.book(_account_key(CANO, ACNT_PRDT_CD))
    today = datetime.now().strftime("%Y%m%d")
    rows = []
    for record in book.orders:
        if record.timestamp.strftime("%Y%m%d") != today:
            continue
        rows.append({
            "ord_dt": record.timestamp.strftime("%Y%m%d"),
            "ord_tmd": record.timestamp.strftime("%H%M%S"),
            "odno": record.order_no,
            "pdno": record.stock_code,
            "ord_qty": str(record.quantity),
            "tot_ccld_qty": str(record.quantity),
            "avg_prvs": str(record.price),
            "tot_ccld_amt": str(record.quantity * record.price),
            "sll_buy_dvsn_cd": "01" if record.side == "SELL" else "02",
        })
    body = {**OK_BODY, "output1": rows, "output2": [{"tot_ord_qty": str(sum(int(r["ord_qty"]) for r in rows))}]}
    return body


# ---------- Admin / introspection (test only) ----------


@app.post("/__mock__/reset")
def admin_reset() -> Dict[str, Any]:
    STATE.reset()
    return {"reset": True}


@app.get("/__mock__/state")
def admin_state(account_key: Optional[str] = None) -> Dict[str, Any]:
    if account_key:
        book = STATE.book(account_key)
        return {
            "cash": book.cash,
            "holdings": {k: {"qty": v.quantity, "avg_price": v.avg_price} for k, v in book.holdings.items()},
            "order_count": len(book.orders),
        }
    return {"accounts": list(STATE._accounts.keys()), "token_count": STATE._token_counter}


@app.post("/__mock__/seed_holding")
def admin_seed_holding(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-seed a holding for tests (bypasses cash/order paths)."""
    cano = payload["CANO"]
    prdt = payload["ACNT_PRDT_CD"]
    code = payload["PDNO"]
    qty = int(payload["qty"])
    avg = float(payload.get("avg_price") or base_price(code))
    book = STATE.book(_account_key(cano, prdt))
    with STATE._lock:
        book.holdings[code] = Holding(qty, avg)
    return {"seeded": {"account": _account_key(cano, prdt), "stock": code, "qty": qty, "avg_price": avg}}


# -----------------------------------------------------------------------------
# Standalone runner
# -----------------------------------------------------------------------------


def run_in_thread(host: str = "127.0.0.1", port: int = 8000, log_level: str = "warning"):
    """Start the mock server in a background daemon thread (for tests)."""
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mock-kis", daemon=True)
    thread.start()
    # wait until uvicorn signals started
    import time
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock KIS server failed to start within 10s")
    return server, thread


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
