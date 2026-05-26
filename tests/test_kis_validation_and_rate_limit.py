"""Regression tests for two issues found during the KIS demo smoke test
(2026-05-26):

  1. trading/kis_auth.py:validate_credentials() — PSVT prefix heuristic
     produced a false positive for a paper key that lacked the prefix.
     Fix adds PRISM_KIS_BYPASS_PREFIX_CHECK env-var to downgrade the
     rejection to a warning without changing the default strict behavior.

  2. trading/domestic_stock_trading.py:get_current_price() — KIS returns
     EGW00201 ("초당 거래건수 초과") under tight ticker-loop access. Fix
     adds one transparent retry with a short backoff.

Tests read the source as text (no mcp_agent / requests import needed) and
verify the env-var branch + retry constant + retry call exist in the code.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KIS_AUTH_SRC = (PROJECT_ROOT / "trading" / "kis_auth.py").read_text(encoding="utf-8")
DOMESTIC_SRC = (
    PROJECT_ROOT / "trading" / "domestic_stock_trading.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------- PSVT bypass env-var


def test_validate_credentials_recognizes_bypass_env_var():
    """The function must read PRISM_KIS_BYPASS_PREFIX_CHECK and respect
    truthy values. Guards against accidental removal of the env-var path."""
    assert "PRISM_KIS_BYPASS_PREFIX_CHECK" in KIS_AUTH_SRC
    # Truthy set parsing — accept 1/true/yes/on
    assert '"1", "true", "yes", "on"' in KIS_AUTH_SRC


def test_validate_credentials_keeps_strict_default():
    """Default (no env-var) must still reject mismatched prefixes — the
    bypass is opt-in, not the new default. Verify both rejection messages
    still exist."""
    assert "CREDENTIAL MISMATCH! Using DEMO app key (PSVT*) in REAL mode." in KIS_AUTH_SRC
    assert "CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode." in KIS_AUTH_SRC


def test_validate_credentials_bypass_logs_warning_not_silent_allow():
    """When bypassed, the function must log a WARNING so the operator sees
    that the safety net was disabled — silent allow would hide misconfig."""
    # Both branches should warn before returning (True, "")
    bypass_block_start = KIS_AUTH_SRC.index("PRISM_KIS_BYPASS_PREFIX_CHECK")
    bypass_block = KIS_AUTH_SRC[bypass_block_start:bypass_block_start + 2000]
    # Both bypass branches log a warning
    assert bypass_block.count("logging.warning(") >= 2


def test_bypass_hint_in_demo_mismatch_message():
    """The vps-mode rejection message must mention the bypass env-var so
    users with a non-PSVT paper key can discover the workaround."""
    # Find the vps mode rejection message body
    idx = KIS_AUTH_SRC.index("CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode.")
    block = KIS_AUTH_SRC[idx:idx + 600]
    assert "PRISM_KIS_BYPASS_PREFIX_CHECK" in block


# ---------------------------------------------------- EGW00201 retry


def test_get_current_price_retries_on_egw00201():
    """get_current_price() must explicitly handle KIS's per-second rate
    limit code with a retry, not just log-and-return-None."""
    assert "EGW00201" in DOMESTIC_SRC
    # The constant should appear in get_current_price specifically — check
    # by finding the function then slicing forward to the next def.
    fn_start = DOMESTIC_SRC.index("def get_current_price(self, stock_code: str)")
    # Look for next method definition to bound the function body
    fn_end = DOMESTIC_SRC.index("\n    def ", fn_start + 1)
    fn_body = DOMESTIC_SRC[fn_start:fn_end]
    assert "EGW00201" in fn_body
    assert "max_attempts" in fn_body
    assert "time.sleep" in fn_body


def test_retry_uses_increasing_backoff():
    """The retry loop must use a backoff multiplier (attempt-aware), not a
    single fixed sleep, so future tuning is obvious from the code."""
    fn_start = DOMESTIC_SRC.index("def get_current_price(self, stock_code: str)")
    fn_end = DOMESTIC_SRC.index("\n    def ", fn_start + 1)
    fn_body = DOMESTIC_SRC[fn_start:fn_end]
    assert "* attempt" in fn_body, (
        "Retry backoff should scale with attempt number so future tuning "
        "(e.g. 3 attempts) doesn't require code restructure"
    )


def test_retry_caps_attempts_to_avoid_blocking():
    """Retry must be bounded — unbounded loops on rate-limit hangs."""
    fn_start = DOMESTIC_SRC.index("def get_current_price(self, stock_code: str)")
    fn_end = DOMESTIC_SRC.index("\n    def ", fn_start + 1)
    fn_body = DOMESTIC_SRC[fn_start:fn_end]
    # Look for the explicit max_attempts = N definition (any small N)
    import re
    assert re.search(r"max_attempts\s*=\s*[1-5]", fn_body), (
        "max_attempts should be a small numeric literal (1..5) to bound retry latency"
    )
