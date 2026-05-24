"""Integration tests for KIS_ENV=mock routing in trading/kis_auth.py.

Boots the FastAPI mock server on a free port via uvicorn in a background
thread, then exercises kis_auth.auth() to confirm requests are routed there
and credential validation is bypassed.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("yaml")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "trading"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_server():
    """Start the mock KIS server in a background thread for the module."""
    import uvicorn
    from tests import mock_kis_server as mks

    port = _free_port()
    os.environ["KIS_ENV"] = "mock"
    os.environ["KIS_MOCK_URL"] = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(mks.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="mock-kis-test", daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("mock KIS server failed to start within 10s")

    yield {"port": port, "url": f"http://127.0.0.1:{port}", "state": mks.STATE}

    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(autouse=True)
def reset_mock_state(mock_server):
    mock_server["state"].reset()
    yield
    mock_server["state"].reset()


@pytest.fixture
def fresh_kis_auth(mock_server, tmp_path, monkeypatch):
    """Reload kis_auth with a clean per-test config root so token files don't leak."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()

    # Force kis_auth to use the tmp config dir as its working root.
    # Patch config_root via env? Module reads it from its own file's dirname,
    # so we instead monkeypatch the attribute after import.
    if "kis_auth" in sys.modules:
        del sys.modules["kis_auth"]

    import kis_auth as ka  # noqa: WPS433 — re-import deliberate
    monkeypatch.setattr(ka, "config_root", str(cfg_dir))
    monkeypatch.setattr(ka, "token_tmp", str(cfg_dir / "KIS_test"))
    return ka


def test_auth_routes_to_mock_and_issues_token(fresh_kis_auth):
    ka = fresh_kis_auth
    ka.auth(svr="vps", product="01")
    env = ka.getTREnv()
    assert env.my_token.startswith("mock-access-")
    assert env.my_url.startswith("http://127.0.0.1:")


def test_resolve_svr_url_overridden_in_mock_mode(fresh_kis_auth):
    ka = fresh_kis_auth
    assert ka._is_mock_env()
    assert ka._resolve_svr_url("prod") == os.environ["KIS_MOCK_URL"]
    assert ka._resolve_svr_url("vps") == os.environ["KIS_MOCK_URL"]


def test_validate_credentials_bypassed_in_mock_mode(fresh_kis_auth):
    ka = fresh_kis_auth
    # A real-prefix key in vps mode would normally fail with CredentialMismatchError;
    # under mock it must succeed (no-op pass).
    ok, msg = ka.validate_credentials("PS_REAL_KEY_DEFINITELY_NOT_PSVT", "vps")
    assert ok is True
    assert msg == ""


def test_url_fetch_against_mock_inquire_price(fresh_kis_auth):
    ka = fresh_kis_auth
    ka.auth(svr="vps", product="01")
    res = ka._url_fetch(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        "",
        {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": "005930"},
    )
    assert res.isOK()
    body = res.getBody()
    assert body.output["rprs_mrkt_kor_name"] == "MOCK_005930"
    assert int(body.output["stck_prpr"]) > 0
